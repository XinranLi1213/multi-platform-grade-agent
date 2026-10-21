"""Stage 2 accepts a ready DWD manifest only. It cannot inspect platform files or deduplicate."""
import ast
import csv
import io
import json
import subprocess
import sys
from decimal import Decimal, DecimalException, ROUND_HALF_UP, localcontext
from pathlib import Path

from .common import SPECIAL, PipelineError, csv_bytes, dump, number, sha, InputProblem
from .rules import compile_rules, inspect_expression
from .semantic import select_adjustments


def read_dwd(manifest_path, expected_hash):
    manifest_path = Path(manifest_path)
    if sha(manifest_path) != expected_hash:
        raise PipelineError("阶段一manifest发生变化，拒绝使用失效输入")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("stage") != "DWD" or manifest.get("status") != "ready":
        raise PipelineError("阶段二唯一合法输入是ready状态的阶段一持久化产物")
    for name in ("data.json", "wide.csv", "audit.json"):
        if manifest.get("files", {}).get(name) != sha(manifest_path.parent / name):
            raise PipelineError("阶段一文件哈希不一致：" + name)
    data = json.loads((manifest_path.parent / "data.json").read_text(encoding="utf-8"))
    ids = [row.get("student_id") for row in data["rows"]]
    if not ids or any(not isinstance(sid, str) or not sid for sid in ids) or len(set(ids)) != len(ids):
        raise PipelineError("阶段一宽表不是每行一个明确编号")
    with (manifest_path.parent / "wide.csv").open(encoding="utf-8-sig", newline="") as handle:
        if list(csv.DictReader(handle)) != data["rows"]:
            raise PipelineError("阶段一CSV与持久化结构化数据不一致")
    return data


def policy_for(rule, metric, status):
    return rule.get("missing_by_field", {}).get(metric, {}).get(status, rule.get("missing", {}).get(status, {"action": "hold"}))


def precheck(data, rule):
    issues = []
    for row in data["rows"]:
        for metric in rule["referenced_metrics"]:
            value = row[metric]
            if value in SPECIAL and policy_for(rule, metric, value)["action"] == "hold":
                issues.append({"kind": "missing_policy", "student_id": row["student_id"], "metric": metric,
                    "state": value, "reason": "需要明确阶段二如何处理此状态，阶段一原值保持不变",
                    "sources": data["cell_sources"].get(row["student_id"], {}).get(metric, [])})
    return issues


def evaluate(expression, fields, states):
    tree, _ = inspect_expression(expression, set(fields))

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            if type(node.value) in (int, float):
                return number(ast.get_source_segment(expression, node))
            if type(node.value) in (str, bool):
                return node.value
            raise PipelineError("不支持的常量")
        if isinstance(node, ast.Call):
            fn = node.func.id
            if fn == "field":
                return fields[node.args[0].value]
            if fn == "state":
                return states[node.args[0].value]
            if fn == "ifelse":
                return ev(node.args[1]) if ev(node.args[0]) else ev(node.args[2])
            if fn == "best":
                return sorted([ev(arg) for arg in node.args[1:]], reverse=True)[:int(ev(node.args[0]))]
            values = [ev(arg) for arg in node.args]
            flat = [item for value in values for item in (value if isinstance(value, list) else [value])]
            if not flat or any(not isinstance(value, Decimal) for value in flat):
                raise PipelineError("数值函数缺少有效数值")
            if fn == "min":
                return min(flat)
            if fn == "max":
                return max(flat)
            if fn == "sum":
                return sum(flat, Decimal(0))
            if fn == "mean":
                return sum(flat, Decimal(0)) / len(flat)
            if fn == "abs" and len(flat) == 1:
                return abs(flat[0])
            raise PipelineError("函数参数数量无效")
        if isinstance(node, ast.BinOp):
            left, right = ev(node.left), ev(node.right)
            if not isinstance(left, Decimal) or not isinstance(right, Decimal):
                raise PipelineError("数学运算只接受明确的数值")
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
        if isinstance(node, ast.UnaryOp):
            value = ev(node.operand)
            if isinstance(node.op, ast.Not):
                return not value
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.Compare):
            left = ev(node.left)
            for operator, right_node in zip(node.ops, node.comparators):
                right = ev(right_node)
                operations = {ast.Gt: lambda: left > right, ast.GtE: lambda: left >= right,
                    ast.Lt: lambda: left < right, ast.LtE: lambda: left <= right,
                    ast.Eq: lambda: left == right, ast.NotEq: lambda: left != right}
                if not operations[type(operator)]():
                    return False
                left = right
            return True
        if isinstance(node, ast.BoolOp):
            return all(ev(v) for v in node.values) if isinstance(node.op, ast.And) else any(ev(v) for v in node.values)
        raise PipelineError("未允许的表达式节点")
    with localcontext() as context:
        context.prec, context.Emax, context.Emin = 50, 500, -500
        return ev(tree)


def calculate_business_row(row, rule, business_columns, overrides=None):
    """Evaluate typed project dependencies, retaining raw DWD cells and unrounded numeric nodes."""
    overrides = overrides or {}
    fields, prepared = {}, {}
    for metric in rule["referenced_metrics"]:
        raw = row[metric]
        if raw in SPECIAL:
            policy = policy_for(rule, metric, raw)
            if policy["action"] == "preserve":
                value = raw
            elif policy["action"] in ("zero", "value"):
                value = number("0" if policy["action"] == "zero" else policy["value"])
            else:
                raise PipelineError(f"{metric}的{raw or '空缺'}尚缺处理规则")
        else:
            value = number(raw)
        fields[metric] = overrides.get(metric, value)
        prepared[business_columns[metric]] = str(fields[metric])
    generated = {}
    for formula in rule["formulas"]:
        _, refs = inspect_expression(formula["expr"], set(fields))
        states = {**{k: str(v) for k, v in fields.items()}, **row}
        try:
            value = evaluate(formula["expr"], fields, states)
        except (PipelineError, TypeError):
            held = sorted({fields[r] for r in refs if isinstance(fields[r], str)})
            if not held:
                raise
            value = "/".join(held)
        value = overrides.get(formula["name"], value)
        if not isinstance(value, (Decimal, str)) or isinstance(value, Decimal) and not value.is_finite():
            raise PipelineError("项目输出须为有限数值或明确特殊状态")
        fields[formula["name"]] = value
        displayed = value
        if isinstance(value, Decimal) and "precision" in rule:
            with localcontext() as ctx:
                ctx.prec = 100
                displayed = value.quantize(Decimal(1).scaleb(-rule["precision"]), rounding=ROUND_HALF_UP)
        generated[formula["name"]] = str(displayed)
    last = generated.get(rule.get("summary_field"), next(reversed(generated.values())))
    try:
        number(last)
        status = "已计算"
    except PipelineError:
        status = "特殊状态保留"
    return {**row, **prepared, **generated, "__calculation_status": status, "__calculation_reason": ""}, fields


def execute_file(manifest_path, expected_hash, rules_path, output_dir, expected_formulas=None, expected_rules_hash=None,
                 baseline_path=None, baseline_hash=None):
    data = read_dwd(manifest_path, expected_hash)
    if expected_rules_hash is not None and sha(rules_path) != expected_rules_hash:
        raise PipelineError("业务规则文件与生成代码绑定的哈希不同")
    supplied = json.loads(Path(rules_path).read_text(encoding="utf-8"))
    rule = compile_rules(supplied, data["metrics"])
    if expected_formulas is not None and rule["formulas"] != expected_formulas:
        raise PipelineError("业务规则与生成代码中固定的公式不同")
    if precheck(data, rule):
        raise PipelineError("仍有未解决的特殊状态处理规则，拒绝计算")
    outputs = [formula["name"] for formula in rule["formulas"]]
    business_columns = {}
    used_columns = set(data["columns"] + outputs)
    for metric in data["metrics"]:
        if metric in rule["referenced_metrics"]:
            name = metric + "（计算输入）"
            while name in used_columns:
                name += "_"
            business_columns[metric] = name
            used_columns.add(name)
    rows, changes, failures = [], [], []
    for row in data["rows"]:
        if rule.get("assessment_items") or rule.get("adjustments"):
            try:
                calculated, _ = calculate_business_row(row, rule, business_columns)
                for metric in rule["referenced_metrics"]:
                    if row[metric] in SPECIAL:
                        policy = policy_for(rule, metric, row[metric])
                        if policy["action"] in ("zero", "value"):
                            changes.append({"student_id": row["student_id"], "metric": metric, "raw_state": row[metric],
                                            "numeric_value": calculated[business_columns[metric]], "basis": policy})
            except (PipelineError, DecimalException, TypeError, ValueError, KeyError) as exc:
                calculated = {**row, **{name: "" for name in list(business_columns.values()) + outputs},
                              "__calculation_status": "计算待处理", "__calculation_reason": str(exc)}
                failures.append({"student_id": row["student_id"], "reason": str(exc)})
            rows.append(calculated)
            continue
        fields, preserve, prepared = {}, set(), {}
        for metric in rule["referenced_metrics"]:
            value = row[metric]
            if value in SPECIAL:
                policy = policy_for(rule, metric, value)
                if policy["action"] == "preserve":
                    preserve.add(value)
                    prepared[business_columns[metric]] = value
                    continue
                actual = "0" if policy["action"] == "zero" else policy.get("value")
                fields[metric] = number(actual)
                changes.append({"student_id": row["student_id"], "metric": metric, "raw_state": value,
                                "numeric_value": str(fields[metric]), "basis": policy})
            else:
                fields[metric] = number(value)
            prepared[business_columns[metric]] = str(fields[metric])
        generated = {}
        failure_reason = ""
        status = "特殊状态保留" if preserve else "已计算"
        try:
            for formula in rule["formulas"]:
                if preserve:
                    value = "/".join(sorted(preserve))
                else:
                    value = evaluate(formula["expr"], fields, row)
                    if isinstance(value, Decimal):
                        if not value.is_finite():
                            raise PipelineError("计算结果不是有限数值")
                        # Downstream formulas use the unrounded value; rounding is only for final exported cells.
                        fields[formula["name"]] = value
                        if "precision" in rule:
                            with localcontext() as ctx:
                                ctx.prec = 100
                                value = value.quantize(Decimal(1).scaleb(-rule["precision"]), rounding=ROUND_HALF_UP)
                        value = str(value)
                    elif not isinstance(value, str):
                        raise PipelineError("公式输出必须是数值或明确的状态/等级文本")
                generated[formula["name"]] = value
        except (PipelineError, DecimalException, TypeError, ValueError, KeyError) as exc:
            generated = {name: "" for name in outputs}
            status = "计算待处理"
            failure_reason = str(exc)
            failures.append({"student_id": row["student_id"], "reason": str(exc)})
        rows.append({**row, **prepared, **generated, "__calculation_status": status, "__calculation_reason": failure_reason})
    adjustment_audit, adjustment_columns, scope_snapshot, adjustment_differences = [], [], [], []
    if rule.get("adjustments"):
        if failures:
            raise PipelineError("调整前计算存在未处理问题，不能确定调整范围")
        baseline_rows = rows
        if baseline_path:
            if sha(baseline_path) != baseline_hash:
                raise PipelineError("调整基准快照哈希变化")
            snapshot = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
            if snapshot["dwd_manifest_sha256"] != expected_hash:
                raise PipelineError("调整基准与当前阶段一版本不同，请重新指定范围依据")
            baseline_rows = snapshot["rows"]
        selected = select_adjustments(rule["adjustments"], baseline_rows)
        scope_snapshot = [{"rule_id": s["rule"]["id"], "student_ids": s["student_ids"]} for s in selected]
        before_rows = {r["student_id"]: dict(r) for r in rows}
        adjusted_rows = []
        targets = sorted({s["rule"]["target"] for s in selected})
        for target in targets:
            adjustment_columns.extend([target + "（调整前）", target + "（调整后）"])
        if set(adjustment_columns) & set(data["columns"] + outputs + list(business_columns.values())):
            raise PipelineError("调整输出名称与已有字段冲突，请修改项目名称")
        for raw in data["rows"]:
            sid, overrides = raw["student_id"], {}
            computed, fields = calculate_business_row(raw, rule, business_columns)
            initial = dict(fields)
            for selection in selected:
                adjustment = selection["rule"]
                if sid not in selection["student_ids"]:
                    continue
                target = adjustment["target"]
                value = fields.get(target)
                if not isinstance(value, Decimal):
                    if adjustment["special"] == "hold":
                        raise PipelineError(f"{sid} / {target}为特殊状态，请明确处理方式")
                    adjustment_audit.append({"student_id": sid, "rule_id": adjustment["id"], "target": target,
                                             "before": str(value), "after": str(value), "status": "特殊状态保留", "basis": adjustment["basis"]})
                    continue
                operand = number(adjustment["value"])
                action = adjustment["action"]
                with localcontext() as ctx:
                    ctx.prec = 50
                    after = value + operand if action == "add" else value * operand if action == "multiply" else operand if action == "set" else max(value, operand)
                    if "cap" in adjustment:
                        after = min(after, number(adjustment["cap"]))
                overrides[target] = after
                computed, fields = calculate_business_row(raw, rule, business_columns, overrides)
                adjustment_audit.append({"student_id": sid, "rule_id": adjustment["id"], "target": target,
                                         "before": str(value), "after": str(after), "status": "已调整", "basis": adjustment["basis"]})
            for target in targets:
                computed[target + "（调整前）"] = str(initial.get(target, ""))
                computed[target + "（调整后）"] = str(fields.get(target, ""))
            adjusted_rows.append(computed)
        rows = adjusted_rows
        for row in rows:
            original = before_rows[row["student_id"]]
            differences = {name: {"before": original.get(name), "after": row.get(name)}
                           for name in outputs if original.get(name) != row.get(name)}
            if differences:
                adjustment_differences.append({"student_id": row["student_id"], "fields": differences})
        dump(Path(output_dir) / "adjustment_scope.json", {"baseline_sha256": baseline_hash,
             "scope": scope_snapshot, "baseline_rows": baseline_rows})
        dump(Path(output_dir) / "adjustments.json", adjustment_audit)
    summary_field = rule.get("summary_field")
    numeric = []
    for row in rows:
        if summary_field and row["__calculation_status"] == "已计算":
            try:
                numeric.append(number(row[summary_field]))
            except PipelineError:
                pass
    excellent = None
    if "excellent_threshold" in rule:
        denominator = len(numeric) if rule["excellent_denominator"] == "numeric" else len(rows)
        excellent = str(sum(v >= number(rule["excellent_threshold"]) for v in numeric) * Decimal(100) / denominator) if denominator else None
    summary = {"rows": len(rows), "calculated": sum(r["__calculation_status"] == "已计算" for r in rows),
        "preserved": sum(r["__calculation_status"] == "特殊状态保留" for r in rows), "failed": len(failures),
        "summary_field": summary_field, "numeric_count": len(numeric),
        "average": str(sum(numeric, Decimal(0)) / len(numeric)) if numeric else None,
        "excellent_rate_percent": excellent, "excellent_threshold": rule.get("excellent_threshold"),
        "excellent_denominator": rule.get("excellent_denominator"),
        "excellent_note": "未提供优秀阈值及分母时不推断优秀率"}
    result = {"stage": "DWS", "dwd_manifest_sha256": expected_hash, "rules": supplied,
        "compiled_formulas": rule["formulas"], "business_input_columns": business_columns,
        "columns": data["columns"] + list(business_columns.values()) + outputs + adjustment_columns + ["__calculation_status", "__calculation_reason"],
        "rows": rows, "summary": summary, "state_changes": changes, "failures": failures,
        "adjustment_changes": adjustment_audit, "adjustment_scope": scope_snapshot,
        "adjustment_differences": adjustment_differences}
    output_dir = Path(output_dir)
    (output_dir / "output.csv").write_bytes(csv_bytes(result["columns"], rows))
    dump(output_dir / "result.json", result)
    return result


def generate_and_run(manifest_path, expected_hash, rules, output_dir, baseline=None):
    data = read_dwd(manifest_path, expected_hash)
    compiled = compile_rules(rules, data["metrics"])
    pending = precheck(data, compiled)
    if pending:
        return {"status": "checkpoint", "issues": pending}
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    dump(output_dir / "rules.json", rules)
    rules_hash = sha(output_dir / "rules.json")
    root = str(Path(__file__).resolve().parents[1])
    engine_hashes = {str(Path(root) / "pipeline" / name): sha(Path(root) / "pipeline" / name)
                     for name in ("common.py", "rules.py", "semantic.py", "dws.py")}
    baseline_path, baseline_hash = None, None
    if baseline is not None:
        baseline_path = str(output_dir / "baseline.json")
        dump(baseline_path, baseline)
        baseline_hash = sha(baseline_path)
    script = ("# Generated deterministic DWS runner. No upload access, deduplication or arbitrary model code.\n"
        "import sys, json\n" + f"sys.path.insert(0, {root!r})\n"
        "from pipeline.dws import execute_file\n"
        "from pipeline.common import sha, PipelineError\n"
        + f"SOURCE_HASHES = {engine_hashes!r}\n"
        "if any(sha(path) != expected for path, expected in SOURCE_HASHES.items()):\n"
        "    raise PipelineError('DWS engine version changed; create a new trial')\n"
        + f"FORMULAS = json.loads({json.dumps(compiled['formulas'], ensure_ascii=False)!r})\n"
        + f"RULES_SHA256 = {rules_hash!r}\n"
        + "try:\n"
        + f"    execute_file({str(Path(manifest_path).resolve())!r}, {expected_hash!r}, {str(output_dir / 'rules.json')!r}, {str(output_dir)!r}, FORMULAS, RULES_SHA256, {baseline_path!r}, {baseline_hash!r})\n"
        + "except PipelineError as exc:\n"
        + "    if any(word in str(exc) for word in ('哈希', '绑定', 'manifest', '完整性')): raise\n"
        + f"    from pipeline.common import dump\n    dump({str(output_dir / 'business_problem.json')!r}, {{'kind':'business_rule','reason':str(exc)}})\n    sys.exit(2)\n")
    code = output_dir / "calculation.py"
    code.write_text(script, encoding="utf-8", newline="\n")
    try:
        process = subprocess.run([sys.executable, "-I", "-X", "utf8", str(code)], cwd=output_dir,
                                 capture_output=True, text=True, encoding="utf-8", timeout=30)
    except subprocess.TimeoutExpired:
        dump(output_dir / "failure.json", {"reason": "隔离Python计算超过30秒，未交付成功结果"})
        from .common import ExecutionFault
        raise ExecutionFault("计算程序执行超时；阶段一未修改，可重试")
    if process.returncode:
        dump(output_dir / "failure.json", {"returncode": process.returncode, "error": process.stderr[-4000:]})
        if process.returncode == 2 and (output_dir / "business_problem.json").exists():
            problem = json.loads((output_dir / "business_problem.json").read_text(encoding="utf-8"))
            raise InputProblem(problem["reason"], problem["kind"])
        from .common import ExecutionFault
        raise ExecutionFault("计算程序执行失败；未交付当前结果，可重试或展开诊断信息")
    result = json.loads((output_dir / "result.json").read_text(encoding="utf-8"))
    result.update(status="checkpoint" if result["failures"] else "complete", directory=str(output_dir),
                  generated_code_sha256=sha(code), output_sha256=sha(output_dir / "output.csv"), rules_sha256=rules_hash,
                  engine_hashes=engine_hashes, python_version=sys.version,
                  command=[sys.executable, "-I", "-X", "utf8", str(code)])
    dump(output_dir / "execution.json", {key: result[key] for key in ("status", "generated_code_sha256", "output_sha256", "rules_sha256", "engine_hashes", "python_version", "command", "dwd_manifest_sha256")})
    return result


def compare(previous, current):
    old = {r["student_id"]: r for r in previous["rows"]}
    outputs = [f["name"] for f in current["compiled_formulas"]]
    changes = []
    for row in current["rows"]:
        before = old.get(row["student_id"], {})
        differences = {field: {"before": before.get(field), "after": row.get(field)}
                       for field in outputs if before.get(field) != row.get(field)}
        if differences:
            changes.append({"student_id": row["student_id"], "fields": differences})
    a, b = previous["summary"], current["summary"]
    compatible = (a.get("excellent_threshold") == b.get("excellent_threshold")
                  and a.get("excellent_denominator") == b.get("excellent_denominator")
                  and a.get("summary_field") == b.get("summary_field"))
    delta = None
    if compatible and a.get("excellent_rate_percent") is not None and b.get("excellent_rate_percent") is not None:
        delta = str(number(b["excellent_rate_percent"]) - number(a["excellent_rate_percent"]))
    return {"changed_students": len(changes), "changes": changes, "before": a, "after": b,
            "excellent_rate_change_percentage_points": delta, "same_excellent_definition": compatible}
