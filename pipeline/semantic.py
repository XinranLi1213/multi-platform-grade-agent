"""Typed business plans and actual-upload profiles. No model arithmetic or arbitrary code."""
import copy
import hashlib
import json
from decimal import Decimal

from .common import PipelineError, SPECIAL, clean, number, submission_time
from .ingest import layout


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def validate_business_draft(value, metrics):
    """Incomplete understanding is persisted separately and is never executable."""
    allowed = {"assessment_items", "weights", "missing", "missing_by_field", "precision", "cap", "output",
               "summary_field", "pass_threshold", "source_instruction", "reason", "excellent_threshold", "excellent_denominator"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise PipelineError("待补充业务草稿包含未知字段；不能包含计算结果或确认标记")
    items = value.get("assessment_items", [])
    if not isinstance(items, list) or len(items) > 16:
        raise PipelineError("待补充考核项目结构无效")
    ids, names = set(), set()
    for item in items:
        if not isinstance(item, dict) or set(item) - {"id", "name", "sources", "operation", "n", "reduce", "weights", "cap", "max_score", "basis"}:
            raise PipelineError("待补充项目包含未知字段")
        if not isinstance(item.get("id"), str) or not item["id"] or item["id"] in ids or not isinstance(item.get("name"), str) or not item["name"] or item["name"] in names:
            raise PipelineError("待补充项目须有唯一ID及名称")
        ids.add(item["id"]); names.add(item["name"])
    for item in items:
        sources = item.get("sources", [])
        if not isinstance(sources, list) or any(s not in set(metrics) | ids for s in sources):
            raise PipelineError("待补充项目引用了不存在的来源；请用问题询问缺少的来源")
        if item.get("operation") is not None and item["operation"] not in ("direct", "mean", "sum", "max", "best", "weighted"):
            raise PipelineError("待补充项目组合方式不支持")
        for key in ("max_score", "cap"):
            if item.get(key) is not None:
                number(item[key])
    weights = value.get("weights", {})
    if not isinstance(weights, dict) or any(k not in set(metrics) | ids | names or number(v) < 0 for k, v in weights.items()):
        raise PipelineError("待补充权重必须引用已知项目且非负")
    return copy.deepcopy(value)


def profiles(files, plan=None, samples=False):
    specs = {p["file_id"]: p for p in (plan or {}).get("platforms", [])}
    result = []
    for file in files:
        table = layout(file, specs.get(file["id"], {}).get("header_line"))
        fields = []
        for index, name in enumerate(table["headers"]):
            values = [r["values"][name] for r in table["rows"]]
            nonempty = [clean(v) for v in values if clean(v)]
            numeric, times = [], 0
            for value in nonempty:
                try:
                    numeric.append(number(value))
                except PipelineError:
                    pass
                try:
                    submission_time(value)
                    times += 1
                except PipelineError:
                    pass
            field = {"field_id": f"{file['id']}:{table['header_line']}:{index}", "column": name,
                     "empty": len(values) - len(nonempty), "distinct": len(set(nonempty)),
                     "repeated_nonempty": len(nonempty) - len(set(nonempty)), "numeric_count": len(numeric),
                     "numeric_min": str(min(numeric)) if numeric else None,
                     "numeric_max": str(max(numeric)) if numeric else None,
                     "parseable_submission_time_count": times,
                     "special_states": sorted(set(nonempty) & SPECIAL),
                     "leading_zero_count": sum(v.isdigit() and len(v) > 1 and v.startswith("0") for v in nonempty)}
            fields.append(field)
        item = {"file_id": file["id"], "name": file["name"], "header_line": table["header_line"],
                "headers": table["headers"], "row_count": len(table["rows"]), "fields": fields,
                "exact_duplicate_rows": len(table["rows"]) - len({tuple(r["values"].values()) for r in table["rows"]}),
                "reading_errors": table["errors"], "note": "统计值不是业务含义或满分证明；相同学生可以有不同活动记录"}
        spec = specs.get(file["id"], {})
        keys = [[spec.get("id_column")], [spec.get("name_column"), spec.get("class_column")],
                [spec.get("name_column"), spec.get("phone_column")]]
        item["candidate_key_checks"] = []
        for columns in keys:
            if not all(c in table["headers"] for c in columns):
                continue
            values = [tuple(clean(r["values"][c]) for c in columns) for r in table["rows"]]
            valid = [v for v in values if all(v)]
            item["candidate_key_checks"].append({"columns": columns, "missing": len(values) - len(valid),
                                                "duplicate": len(valid) - len(set(valid))})
        if samples:
            # Representative, real rows only; never synthesize examples for the model.
            rows = table["rows"]
            indexes = sorted({0, len(rows) // 2, len(rows) - 1}) if rows else []
            item["samples"] = [rows[i] for i in indexes]
        result.append(item)
    return result


def project_formulas(items, metrics):
    if not isinstance(items, list) or len(items) > 16:
        raise PipelineError("考核项目须为列表，第一版最多16项")
    by_id, names = {}, set(metrics)
    for item in items:
        allowed = {"id", "name", "sources", "operation", "n", "reduce", "weights", "cap", "max_score", "basis"}
        if not isinstance(item, dict) or set(item) - allowed:
            raise PipelineError("考核项目结构无效或包含未支持的字段")
        ident, name = item.get("id"), item.get("name")
        if not isinstance(ident, str) or not ident or ident in by_id or ident in metrics:
            raise PipelineError("考核项目ID须非空、唯一，不能覆盖来源字段")
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or name in names or name.startswith("__"):
            raise PipelineError("考核项目名称须唯一，不能覆盖原始测量项")
        if not isinstance(item.get("basis"), str) or not item["basis"].strip():
            raise PipelineError(f"{name}：请补充项目组成的指令依据")
        names.add(name)
        by_id[ident] = item
    expressions, visiting, done = [], set(), set()

    def visit(ident):
        if ident in visiting:
            raise PipelineError("考核项目存在循环引用：" + ident)
        if ident in done:
            return
        visiting.add(ident)
        item = by_id[ident]
        sources = item.get("sources")
        if not isinstance(sources, list) or not sources or len(sources) > 32 or any(not isinstance(s, str) for s in sources):
            raise PipelineError(item["name"] + "：请明确真实来源字段或前序项目")
        if len(set(sources)) != len(sources):
            raise PipelineError(item["name"] + "：来源重复，请明确是否重复计分")
        refs = []
        for source in sources:
            if source in by_id:
                visit(source)
                source = by_id[source]["name"]
            elif source not in metrics:
                raise PipelineError(item["name"] + "：来源不存在：" + source)
            refs.append(f"field({source!r})")
        op = item.get("operation")
        if op == "direct" and len(refs) == 1:
            expr = refs[0]
        elif op in ("mean", "sum", "max"):
            expr = f"{op}({','.join(refs)})"
        elif op == "best":
            n = item.get("n")
            if type(n) is not int or not 1 <= n <= len(refs) or item.get("reduce") not in ("mean", "sum"):
                raise PipelineError(item["name"] + "：请明确最佳N项的N及平均/累加方式")
            expr = f"{item['reduce']}(best({n},{','.join(refs)}))"
        elif op == "weighted":
            weights = item.get("weights")
            if not isinstance(weights, list) or len(weights) != len(refs):
                raise PipelineError(item["name"] + "：请补充每个来源的内部权重")
            weights = [number(w) for w in weights]
            if any(w < 0 for w in weights) or sum(weights) != 1:
                raise PipelineError(item["name"] + "：内部权重须非负且合计100%")
            expr = "+".join(f"{r}*{w}" for r, w in zip(refs, weights))
        else:
            raise PipelineError(item["name"] + "：请明确支持的项目组合方式")
        maximum = item.get("max_score")
        if maximum is None:
            if op == "sum" or op == "best" and item.get("reduce") == "sum":
                raise PipelineError(item["name"] + "：累加项目请明确合成满分")
            maximum = "100"  # Direct/mean/max/weighted use already-normalized percent inputs.
        maximum = number(maximum)
        if maximum <= 0:
            raise PipelineError(item["name"] + "：项目满分必须大于0")
        if "cap" in item:
            cap = number(item["cap"])
            if cap < 0 or cap > maximum:
                raise PipelineError(item["name"] + "：封顶须在项目满分范围内")
            expr = f"min({cap},({expr}))"
        if maximum != 100:
            expr = f"({expr})/{maximum}*100"
        expressions.append({"name": item["name"], "expr": expr})
        visiting.remove(ident)
        done.add(ident)

    for ident in by_id:
        visit(ident)
    return expressions, {key: item["name"] for key, item in by_id.items()}


def expand_projects(rules, metrics):
    rule = copy.deepcopy(rules)
    items = rule.pop("assessment_items", [])
    formulas, aliases = project_formulas(items, set(metrics))
    if not items:
        return rule
    if "weights" in rule:
        if not isinstance(rule["weights"], dict):
            raise PipelineError("总评权重须为项目与权重的对应关系")
        weights = {aliases.get(k, k): v for k, v in rule.pop("weights").items()}
        if len(weights) != len(rules["weights"]):
            raise PipelineError("项目权重重复引用同一项目")
        valid = set(metrics) | set(aliases.values())
        values = {key: number(value) for key, value in weights.items()}
        if not values or not set(values) <= valid or any(v < 0 for v in values.values()) or sum(values.values()) != 1:
            raise PipelineError("总评权重须引用实际项目、非负且合计100%")
        if "formulas" in rule:
            raise PipelineError("weights与formulas不能同时给出")
        expr = "+".join(f"field({k!r})*{v}" for k, v in values.items())
        if "cap" in rule:
            expr = f"min({number(rule['cap'])},({expr}))"
        formulas.append({"name": rule.get("output", "总评"), "expr": expr})
        rule.setdefault("summary_field", formulas[-1]["name"])
    elif "formulas" in rule:
        if not isinstance(rule["formulas"], list):
            raise PipelineError("总评公式须为列表")
        formulas.extend(rule["formulas"])
    else:
        raise PipelineError("已保存考核项目；尚缺总评组成或权重")
    rule["formulas"] = formulas
    return rule


def validate_adjustments(adjustments, targets, output_names):
    if not isinstance(adjustments, list) or len(adjustments) > 12:
        raise PipelineError("局部调整须为列表，第一版最多12条")
    seen = set()
    for adjustment in adjustments:
        allowed = {"id", "baseline", "scope", "target", "action", "value", "cap", "special", "basis", "order", "preserve"}
        if not isinstance(adjustment, dict) or set(adjustment) - allowed:
            raise PipelineError("局部调整结构无效")
        ident = adjustment.get("id")
        if not isinstance(ident, str) or not ident or ident in seen:
            raise PipelineError("局部调整ID须非空且唯一")
        seen.add(ident)
        if adjustment.get("baseline") not in ("before_adjustment",) and not isinstance(adjustment.get("baseline"), int):
            raise PipelineError("请明确调整前判断版本（before_adjustment或结果版本号）")
        if type(adjustment.get("baseline")) is bool:
            raise PipelineError("调整基准版本无效")
        if adjustment.get("target") not in targets or adjustment.get("target") in output_names:
            raise PipelineError("调整项目须为实际测量项或考核项目，不能直接覆盖总评公式")
        if adjustment.get("action") not in ("add", "multiply", "set", "floor"):
            raise PipelineError("调整动作仅支持加分、乘系数、设值、提高至最低值")
        number(adjustment.get("value"))
        if adjustment.get("special") not in ("preserve", "hold"):
            raise PipelineError("请明确调整遇到特殊状态时保留或待补充；不会自动转成数值")
        if "cap" in adjustment and number(adjustment["cap"]) < 0:
            raise PipelineError("调整封顶不能为负数")
        if not isinstance(adjustment.get("basis"), str) or not adjustment["basis"].strip():
            raise PipelineError("请填写调整依据或引用教师指令")
        if "order" in adjustment and (type(adjustment["order"]) is not int or adjustment["order"] < 0):
            raise PipelineError("调整顺序须为非负整数")
        scope = adjustment.get("scope")
        if not isinstance(scope, dict) or set(scope) not in ({"ids"}, {"field", "op", "value"}):
            raise PipelineError("范围须为明确编号列表或一个数值条件")
        if "ids" in scope:
            if not isinstance(scope["ids"], list) or not scope["ids"] or any(not isinstance(i, str) or not i for i in scope["ids"]):
                raise PipelineError("调整编号须为非空文本，保留前导零")
            if len(set(scope["ids"])) != len(scope["ids"]):
                raise PipelineError("调整编号重复")
        else:
            if scope["field"] not in targets | output_names or scope["op"] not in ("lt", "le", "eq", "ge", "gt"):
                raise PipelineError("调整筛选须引用实际数值项目及支持的比较条件")
            number(scope["value"])


def select_adjustments(adjustments, baseline_rows):
    rows = {r["student_id"]: r for r in baseline_rows}
    selected = []
    for rule in adjustments:
        scope, ids, unresolved = rule["scope"], [], []
        if "ids" in scope:
            unknown = set(scope["ids"]) - set(rows)
            if unknown:
                raise PipelineError("调整编号不在本次宽表中：" + "、".join(sorted(unknown)))
            ids = scope["ids"]
        else:
            threshold = number(scope["value"])
            for sid, row in rows.items():
                try:
                    value = number(row.get(scope["field"], ""))
                except PipelineError:
                    if rule["special"] == "hold":
                        unresolved.append(sid)
                    continue
                if {"lt": value < threshold, "le": value <= threshold, "eq": value == threshold,
                    "ge": value >= threshold, "gt": value > threshold}[scope["op"]]:
                    ids.append(sid)
        if unresolved:
            raise PipelineError("筛选基准含特殊状态，请明确保留政策；编号：" + "、".join(unresolved))
        selected.append({"rule": rule, "student_ids": sorted(ids)})
    for i, a in enumerate(selected):
        for b in selected[i + 1:]:
            overlap = sorted(set(a["student_ids"]) & set(b["student_ids"]))
            # Conservative: overlapping students can also have dependent project targets.
            if overlap and (a["rule"].get("order") is None or b["rule"].get("order") is None
                            or a["rule"]["order"] == b["rule"]["order"]):
                raise PipelineError("多条调整命中同一学生，请明确不同执行顺序：" + "、".join(overlap)
                                    + "；规则：" + a["rule"]["id"] + " / " + b["rule"]["id"])
    return sorted(selected, key=lambda s: s["rule"].get("order", 0))
