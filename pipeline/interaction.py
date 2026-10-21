"""Teacher form contracts; all choices come from the actual parsed upload."""
import copy
import hashlib
import json

from .common import InputProblem, PipelineError, SPECIAL, clean, number, submission_time
from .ingest import layout

STRATEGIES = {"max": "取最高分", "latest": "按提交时间取最后一次", "sum": "累加",
              "mean": "平均", "unique": "仅接受一致值", "confirm": "暂不处理"}


def tables(state, plan=None):
    specs = {p["file_id"]: p for p in (plan or state["plan"])["platforms"]}
    return [{"file_id": f["id"], "name": f["name"], "reading_actions": f.get("reading_actions", []),
             **layout(f, specs.get(f["id"], {}).get("header_line"))} for f in state["files"]]


def validate_plan(state, plan):
    if not isinstance(plan, dict) or not isinstance(plan.get("platforms"), list):
        raise InputProblem("数据整理方案必须包含文件配置")
    specs = plan["platforms"]
    if len(specs) != len(state["files"]) or {p.get("file_id") for p in specs if isinstance(p, dict)} != {f["id"] for f in state["files"]}:
        raise InputProblem("每个上传文件必须有且仅有一份整理配置")
    layouts = tables(state, plan)
    by_file = {t["file_id"]: t for t in layouts}
    records = {r["record_id"]: r for t in layouts for r in t["rows"]}
    metrics = set()
    for p in specs:
        t = by_file[p["file_id"]]
        context = {"file_id": p["file_id"], "file_name": t["name"]}
        grain = p.get("record_grain", [])
        if not isinstance(grain, list) or any(c not in t["headers"] for c in grain):
            raise InputProblem("记录粒度须选择实际活动字段", "mapping", field="record_grain", **context)
        for key in ("id_column", "name_column", "class_column", "phone_column"):
            if p.get(key) is not None and p[key] not in t["headers"]:
                raise InputProblem("请选择文件实际存在的字段", "mapping", field=key, **context)
        if not isinstance(p.get("metrics"), list):
            raise InputProblem("请配置要整理的项目", **context)
        seen = set()
        for m in p["metrics"]:
            if not isinstance(m, dict) or m.get("column") not in t["headers"]:
                raise InputProblem("请选择实际读取的原字段", "mapping", **context)
            target = m.get("target")
            if not isinstance(target, str) or not target.strip() or target.startswith("__") or target in {"student_id", "name", "class", "phone"} or target in seen:
                raise InputProblem("统一项目名称须非空、唯一，不能覆盖身份字段", "mapping", field=m["column"], **context)
            seen.add(target)
            metrics.add(target)
            where = m.get("where", {})
            if not isinstance(where, dict) or any(c not in t["headers"] or not isinstance(v, str) for c, v in where.items()):
                raise InputProblem("活动筛选须引用真实字段和文本原值", "mapping", field=target, **context)
            if any(not clean(v) for v in where.values()):
                raise InputProblem("请明确实际活动原值；空缺活动须先核对，不能自行归类", "mapping", field=target, **context)
            if where and not any(all(r["values"][c] == v for c, v in where.items()) for r in t["rows"]):
                raise InputProblem("活动筛选没有对应原始记录，请核对实际值", "mapping", field=target, **context)
            if m.get("strategy") not in STRATEGIES:
                raise InputProblem("请选择有效的归并方式", field=target, **context)
            if m.get("timestamp_column") is not None and m["timestamp_column"] not in t["headers"]:
                raise InputProblem("提交时间字段不在实际表头中", field=target, **context)
            scale = m.get("scale")
            if not isinstance(scale, dict):
                raise InputProblem("请提供分制设置", field=target, **context)
            if scale.get("kind") == "points" and number(scale.get("max_score")) <= 0:
                raise InputProblem("原始满分必须大于0", field=target, **context)
            if scale.get("kind") == "grade":
                levels = scale.get("levels", {})
                if not isinstance(levels, dict) or not levels:
                    raise InputProblem("请补充等级对应关系", field=target, **context)
                for value in levels.values():
                    if not 0 <= number(value) <= 100:
                        raise InputProblem("等级对应值须在0至100之间", field=target, **context)
    roster = next((p for p in specs if p["file_id"] == plan.get("roster_file")), None)
    # Legacy plans may use automatic roster selection, exactly as DWD does.
    candidates = [p for p in specs if p.get("id_column")]
    if roster is None and candidates:
        roster = max(candidates, key=lambda p: len({clean(r["values"].get(p["id_column"], "")) for r in by_file[p["file_id"]]["rows"]} - {""}))
    ids = {clean(r["values"].get(roster["id_column"], "")) for r in by_file[roster["file_id"]]["rows"] if r["record_id"] not in plan.get("excluded_records", {})} - {""} if roster and roster.get("id_column") else set()
    appended = plan.get("append_unmatched_ids", [])
    actual_ids = {clean(r["values"].get(p.get("id_column"), "")) for p in specs for r in by_file[p["file_id"]]["rows"]} - {""}
    if not isinstance(appended, list) or any(not isinstance(sid, str) or sid not in actual_ids for sid in appended):
        raise InputProblem("追加主表须选择原始文件中实际存在的编号", "unmatched", field="student_id")
    ids |= set(appended)
    for key in ("identity_resolutions", "excluded_records", "cell_resolutions", "timestamp_resolutions"):
        if not isinstance(plan.get(key, {}), dict):
            raise InputProblem("人工裁决配置格式错误", field=key)
    for rid, decision in plan.get("identity_resolutions", {}).items():
        context = {"record_id": rid, "records": [records[rid]] if rid in records else []}
        if rid not in records:
            raise InputProblem("待确认原始记录不存在，请使用当前流程中的记录", "identity", **context)
        if not isinstance(decision, dict) or not isinstance(decision.get("student_id"), str) or decision["student_id"] not in ids:
            raise InputProblem("请选择主花名册中的编号", "identity", field="student_id", **context)
        if not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
            raise InputProblem("请填写核对依据", "identity", field="reason", **context)
    for rid, reason in plan.get("excluded_records", {}).items():
        if rid not in records:
            raise InputProblem("要排除的原始记录不存在", record_id=rid)
        if not isinstance(reason, str) or not reason.strip():
            raise InputProblem("请填写排除依据", record_id=rid, field="reason")
    for key, decision in plan.get("cell_resolutions", {}).items():
        sid, sep, metric = key.partition("|")
        if not sep or sid not in ids or metric not in metrics | {"name", "class", "phone"}:
            raise InputProblem("裁决须引用实际编号和项目", "cell", field=key)
        if not isinstance(decision, dict) or not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
            raise InputProblem("请填写补充或裁决依据", "cell", field=key)
        value = decision.get("value")
        if not isinstance(value, str):
            raise InputProblem("请填写明确的裁决值", "cell", field=key)
        if metric in metrics and value not in SPECIAL:
            try:
                number(value)
            except PipelineError:
                affected = next((q.get("records", []) for q in state.get("issues", []) if q.get("student_id") == sid and q.get("metric") == metric), [])
                raise InputProblem("请填写具体成绩数值（例如99）或允许的特殊状态；暂不裁决请选择继续待确认", "cell", field=key, student_id=sid, metric=metric, records=affected)
    for rid, fields in plan.get("timestamp_resolutions", {}).items():
        if rid not in records or not isinstance(fields, dict) or not fields:
            raise InputProblem("补充时间须引用当前文件的实际记录", "submission_time", record_id=rid)
        fid = rid.split(":", 1)[0]
        spec = next(p for p in specs if p["file_id"] == fid)
        permitted = {m.get("timestamp_column") for m in spec["metrics"] if m.get("timestamp_column")}
        for field, decision in fields.items():
            context = {"record_id": rid, "field": field, "records": [records[rid]], "file_id": fid, "file_name": by_file[fid]["name"]}
            if field not in permitted or field not in records[rid]["values"]:
                raise InputProblem("请选择该文件实际配置的提交时间字段", "submission_time", **context)
            if not isinstance(decision, dict) or not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
                raise InputProblem("请填写补充或修正提交时间的核对依据", "submission_time", **context)
            value = decision.get("value")
            try:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError
                submission_time(value)
            except (ValueError, PipelineError):
                raise InputProblem("请填写包含日期和时分的有效的提交时间，例如2026-10-18T14:00:00；系统不会猜测缺失时间", "submission_time", **context)
    return layouts


def issue_key(issue):
    """Stable across question renumbering and workflow revisions."""
    return json.dumps([issue.get("kind"), issue.get("student_id"), issue.get("metric") or issue.get("field"),
                       sorted(r.get("record_id", "") for r in issue.get("records", []))], ensure_ascii=False)


def issue_guidance(state, issue):
    records = issue.get("records", [])
    spec = next((p for p in state["plan"].get("platforms", []) if records and p["file_id"] == records[0].get("file_id")), {})
    metric = next((m for m in spec.get("metrics", []) if m["target"] == issue.get("metric")), {})
    time_problem = issue.get("kind") == "deduplication" and metric.get("strategy") == "latest" and any(word in issue.get("reason", "") for word in ("提交时间", "时区"))
    if not time_problem:
        return {"issue_id": issue.get("id"), "issue_key": issue_key(issue)}
    field = metric.get("timestamp_column")
    times, details = [], []
    for r in records:
        original = r["values"].get(field, "")
        correction = state["plan"].get("timestamp_resolutions", {}).get(r["record_id"], {}).get(field, {})
        effective = correction.get("value", original)
        times.append({"record_id": r["record_id"], "file_name": r.get("file_name"), "line": r["line"],
                      "field": field, "original": original, "effective": effective, "reason": correction.get("reason", "")})
        details.append(f"第{r['line']}行原值{r['values'].get(metric.get('column'), '')}，提交时间{'未提供' if not effective else effective}")
    name = records[0]["values"].get(spec.get("name_column"), "") if records else ""
    return {"issue_id": issue.get("id"), "issue_key": issue_key(issue), "kind": "submission_time",
            "title": "哪一次才是最后有效提交？",
            "explanation": f"{name}（{issue.get('student_id', '')}）的{issue.get('metric', '')}有{len(records)}条提交：" + "；".join(details) + "。当前采用“按提交时间取最后一次”，现有时间信息不足或存在冲突，无法确定最后有效提交。",
            "question": "请核对平台提交记录或日志，补充准确时间并说明出处；也可以直接明确采用的成绩并填写依据，或先保留待确认。核对花名册尚未说明提交先后。",
            "timestamp_field": field, "timestamps": times}


def plan_diff(old, new):
    changes = []
    def walk(a, b, path):
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                walk(a.get(key), b.get(key), path + [str(key)])
        elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, path + [str(i)])
        elif a != b:
            changes.append({"path": ".".join(path), "before": a, "after": b})
    walk(old, new, [])
    return changes


def token(plan, revision):
    return hashlib.sha256(json.dumps([revision, plan], ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def presentation(state):
    from decimal import Decimal
    try:
        layouts = tables(state)
    except (ValueError, TypeError, KeyError):
        layouts = []
    rules = state.get("rules") or {}
    try:
        percentages = {k: format(number(v) * Decimal(100), 'f') for k, v in rules.get("weights", {}).items()}
    except (ValueError, TypeError, AttributeError):
        percentages = {}
    return {"tables": layouts, "issue_guidance": [issue_guidance(state, q) for q in state.get("issues", [])], "weight_percentages": percentages, "governance_summary": [
        {"file_id": p["file_id"], "items": [{"target": m["target"], "strategy": STRATEGIES.get(m.get("strategy"), "待确认"), "scale": m.get("scale")} for m in p.get("metrics", [])]}
        for p in state["plan"].get("platforms", [])], "effective_rules": copy.deepcopy(rules)}
