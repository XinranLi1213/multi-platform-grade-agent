"""Offline, evidence-based two-platform grade integration. Python 3.11+, stdlib only."""

import argparse
import csv
import hashlib
import json
import re
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path


FIELDS = ["学号", "实验A成绩", "实验B成绩", "总分", "处理状态", "原因",
          "实验A来源", "实验B来源", "规则依据"]


class InputError(ValueError):
    """A batch cannot be read safely; do not produce a misleading summary."""


def file_info(path):
    path = Path(path).resolve()
    return {"file": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def read_platform(path, score_column):
    """Read UTF-8/BOM CSV; retain raw strings and physical source line numbers."""
    path = Path(path).resolve()
    records = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, strict=True)
        headers = next(reader, None)
        if headers != ["学号", score_column]:
            raise InputError(f"{path}: 表头必须是 学号,{score_column}")
        while True:
            start_line = reader.line_num + 1
            try:
                values = next(reader)
            except StopIteration:
                break
            if not values:
                continue
            if len(values) != len(headers):
                raise InputError(f"{path}:{start_line}: 列数与表头不一致")
            row = dict(zip(headers, values))
            student_id = row["学号"]
            if not student_id.strip():
                raise InputError(f"{path}:{start_line}: 学号为空，无法关联")
            records.setdefault(student_id, []).append({
                "raw": row[score_column], "file": str(path),
                "line": start_line, "end_line": reader.line_num,
                "column": score_column,
            })
    return records


def associate_students(platform_a, platform_b):
    """Full outer join on exact string IDs; never zip rows or cast IDs to integers."""
    return [(student_id, platform_a.get(student_id, []), platform_b.get(student_id, []))
            for student_id in sorted(set(platform_a) | set(platform_b))]


def check_score(records, component):
    """Missing != zero. Duplicate/invalid values remain visible and block this total."""
    if not records:
        return "", None, [f"缺少实验 {component} 成绩（平台无该学号记录）"]
    if len(records) > 1:
        raw = json.dumps([record["raw"] for record in records], ensure_ascii=False)
        return raw, None, [f"实验 {component} 学号重复/成绩冲突，未选择任何记录"]
    raw = records[0]["raw"]
    normalized = raw.strip()
    if normalized == "":
        return raw, None, [f"缺少实验 {component} 成绩"]
    if not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", normalized):
        return raw, None, [f"实验 {component} 成绩不是有效有限数值：{raw}"]
    return raw, Decimal(normalized), []


def read_evidence(base, source):
    """Resolve explicit material references; this MVP does not infer rules from prose."""
    if not isinstance(source, dict):
        raise InputError("缺少规则出处")
    name, line = source.get("file"), source.get("line")
    if not isinstance(name, str) or type(line) is not int or line < 1:
        raise InputError("规则出处必须包含 file 和正整数 line")
    path = (base / name).resolve()
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    if line > len(lines) or not lines[line - 1].strip():
        raise InputError(f"规则出处无有效内容：{path}:{line}")
    return {**file_info(path), "line": line, "text": lines[line - 1]}


def describe_rule(rule):
    evidence = rule.get("evidence", {})
    location = f"{evidence.get('file', '无出处')}:{evidence.get('line', '?')}"
    return (f"{rule.get('id', '?')} A×{rule.get('weight_a', '?')}+"
            f"B×{rule.get('weight_b', '?')} [{location}]")


def resolve_rules(path):
    """Validate explicit applicability and require a recorded teacher conflict decision."""
    path = Path(path).resolve()
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or not isinstance(payload.get("rules"), list):
        raise InputError("规则文件必须包含 rules 数组")
    if not isinstance(payload.get("version"), str) or not payload["version"].strip():
        raise InputError("规则文件必须有非空 version")
    rules = payload["rules"]
    ids = [rule.get("id") for rule in rules if isinstance(rule, dict)]
    if (len(ids) != len(rules) or any(not isinstance(i, str) or not i for i in ids)
            or len(set(ids)) != len(ids)):
        raise InputError("每条规则必须有唯一非空 id")
    for rule in rules:
        try:
            rule["evidence"] = read_evidence(path.parent, rule.get("source"))
        except (InputError, OSError, UnicodeError) as exc:
            rule["evidence_error"] = str(exc)
    context = {**file_info(path), "version": payload["version"], "rules": rules,
               "selected_rule_id": None, "resolution": None}
    applicable = [rule for rule in rules if rule.get("applicable") is True]
    issues = []
    decision = payload.get("resolution")
    if decision is not None:
        if not isinstance(decision, dict):
            return None, ["规则选择缺少教师明确说明及出处"], context
        confirmation = decision.get("teacher_confirmation")
        if not isinstance(confirmation, str) or not confirmation.strip():
            return None, ["规则选择缺少教师明确说明及出处"], context
        try:
            decision_evidence = read_evidence(path.parent, decision.get("source"))
        except (InputError, OSError, UnicodeError) as exc:
            return None, [f"教师确认缺少有效出处：{exc}"], context
        context["resolution"] = {**decision, "evidence": decision_evidence}
        matches = [rule for rule in applicable if rule["id"] == decision.get("selected_rule_id")]
        if len(matches) != 1:
            return None, ["教师指定的规则不存在或未明确适用"], context
        selected = matches[0]
    else:
        unknown = [rule["id"] for rule in rules if type(rule.get("applicable")) is not bool]
        if unknown:
            issues.append("规则适用性不明确：" + ", ".join(unknown))
        if len(applicable) > 1:
            issues.append("适用规则冲突，需教师明确：" + "；".join(map(describe_rule, applicable)))
        if not applicable:
            issues.append("缺少适用计分规则")
        if issues:
            return None, issues, context
        selected = applicable[0]
    for field in ("weight_a", "weight_b", "decimal_places", "rounding", "method"):
        if field not in selected:
            issues.append(f"规则 {selected['id']} 缺少 {field} 依据")
    if selected.get("evidence_error"):
        issues.append(f"规则 {selected['id']} 缺少有效出处：{selected['evidence_error']}")
    if issues:
        return None, issues, context
    try:
        weight_a, weight_b = Decimal(str(selected["weight_a"])), Decimal(str(selected["weight_b"]))
        if (not weight_a.is_finite() or not weight_b.is_finite()
                or min(weight_a, weight_b) < 0 or weight_a + weight_b != Decimal("1")):
            issues.append("权重须为有限非负数且合计为 1")
    except InvalidOperation:
        issues.append("权重不是有效数值")
    if selected["method"] != "weighted_sum":
        issues.append("本轮仅支持明确的两项加权求和规则")
    if selected["rounding"] != "ROUND_HALF_UP":
        issues.append("本轮仅支持明确的 ROUND_HALF_UP 舍入规则")
    if type(selected["decimal_places"]) is not int or not 0 <= selected["decimal_places"] <= 10:
        issues.append("decimal_places 必须是 0 至 10 的整数")
    if issues:
        return None, issues, context
    context["selected_rule_id"] = selected["id"]
    return selected, [], context


def calculate_total(score_a, score_b, rule):
    """Decimal weighted sum; round only once at the final, explicitly configured step."""
    weight_a, weight_b = Decimal(str(rule["weight_a"])), Decimal(str(rule["weight_b"]))
    with localcontext() as ctx:
        ctx.prec = max(50, *(len(value.as_tuple().digits) + abs(value.as_tuple().exponent) + 10
                             for value in (score_a, score_b, weight_a, weight_b))) * 2
        exact = score_a * weight_a + score_b * weight_b
        rounded = exact.quantize(Decimal(1).scaleb(-rule["decimal_places"]), rounding=ROUND_HALF_UP)
    return str(exact), format(rounded, f".{rule['decimal_places']}f")


def integrate(platform_a_path, platform_b_path, rules_path, output_dir, previous_audit=None):
    input_paths = [Path(p).resolve() for p in (platform_a_path, platform_b_path, rules_path)]
    output_dir = Path(output_dir).resolve()
    destinations = [output_dir / "summary.csv", output_dir / "audit.json"]
    if any(destination in input_paths for destination in destinations):
        raise InputError("输出路径不得覆盖输入文件")
    if previous_audit and Path(previous_audit).resolve() in destinations:
        raise InputError("续算必须使用新输出目录，以保留上次审计记录")
    platform_a = read_platform(platform_a_path, "实验A成绩")
    platform_b = read_platform(platform_b_path, "实验B成绩")
    rule, rule_issues, rule_context = resolve_rules(rules_path)
    inputs = {"platform_a": file_info(platform_a_path), "platform_b": file_info(platform_b_path)}
    previous, previous_rows = None, {}
    if previous_audit:
        previous = json.loads(Path(previous_audit).read_text(encoding="utf-8"))
        if any(previous["inputs"][key]["sha256"] != inputs[key]["sha256"] for key in inputs):
            raise InputError("续算的成绩输入与上次不一致；请作为新批次运行")
        previous_rows = {row["student_id"]: row for row in previous["students"]}
    rows, students, changes = [], [], []
    rule_reference = (f"规则版本={rule_context['version']}；"
                      f"采用规则={rule_context['selected_rule_id'] or '未选择'}；"
                      + "；".join(describe_rule(candidate) for candidate in rule_context["rules"]))
    if rule_context["resolution"]:
        evidence = rule_context["resolution"]["evidence"]
        rule_reference += f"；教师说明={evidence['file']}:{evidence['line']}"
    for student_id, records_a, records_b in associate_students(platform_a, platform_b):
        raw_a, score_a, issues_a = check_score(records_a, "A")
        raw_b, score_b, issues_b = check_score(records_b, "B")
        issues = issues_a + issues_b + rule_issues
        exact, total = None, ""
        if not issues:
            exact, total = calculate_total(score_a, score_b, rule)
        status = "待处理" if issues else "已计算"
        rows.append({"学号": student_id, "实验A成绩": raw_a, "实验B成绩": raw_b,
                     "总分": total, "处理状态": status, "原因": "；".join(issues),
                     "实验A来源": "；".join(f"{r['file']}:{r['line']}" for r in records_a),
                     "实验B来源": "；".join(f"{r['file']}:{r['line']}" for r in records_b),
                     "规则依据": rule_reference})
        student = {"student_id": student_id, "platform_a": records_a, "platform_b": records_b,
                   "exact_total": exact, "total": total, "status": status, "reasons": issues,
                   "selected_rule_id": rule_context["selected_rule_id"]}
        students.append(student)
        if previous:
            before = previous_rows.get(student_id)
            keys = ("status", "total", "reasons", "selected_rule_id")
            old = {key: before[key] for key in keys} if before else None
            new = {key: student[key] for key in keys}
            if old != new:
                changes.append({"student_id": student_id, "before": old, "after": new})
    audit = {"schema_version": 1, "inputs": inputs, "rules": rule_context,
             "students": students, "counts": {"students": len(students),
             "calculated": sum(row["status"] == "已计算" for row in students),
             "pending": sum(row["status"] == "待处理" for row in students)},
             "previous_run": file_info(previous_audit) if previous_audit else None,
             "changes": changes}
    output_dir.mkdir(parents=True, exist_ok=True)
    with destinations[0].open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    destinations[1].write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return audit


def main():
    parser = argparse.ArgumentParser(description="两平台成绩整合：有依据才计算，异常保留待处理")
    parser.add_argument("--platform-a", required=True, type=Path)
    parser.add_argument("--platform-b", required=True, type=Path)
    parser.add_argument("--rules", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--previous-audit", type=Path)
    args = parser.parse_args()
    try:
        audit = integrate(args.platform_a, args.platform_b, args.rules, args.output_dir, args.previous_audit)
    except (InputError, OSError, UnicodeError, csv.Error, json.JSONDecodeError) as exc:
        print(f"输入错误：{exc}", file=sys.stderr)
        return 2
    print(json.dumps({**audit["counts"], "output_dir": str(args.output_dir.resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
