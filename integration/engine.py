"""Validate plans and execute explicit decisions. No semantic model lives in this module."""

import copy
import itertools
import json
from collections import defaultdict

from .csv_io import missing

TRANSFORMS = {"identity": lambda v: v, "strip": lambda v: v.strip(), "casefold": lambda v: v.casefold()}
META = ["__记录", "__状态", "__原因", "__来源"]
NAME_LABELS = {"姓名", "名字", "名称", "联系人", "联络人", "联系人姓名", "客户姓名", "客户名称", "name", "full_name", "fullname", "contact_name"}


class PlanError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("；".join(errors))


def default_plan(files):
    return {"row_meaning": "", "mode": "keep", "key_fields": [], "link_basis": "",
        "dedupe": "keep", "dedupe_basis": "", "mapping": {
            f["id"]: {h: {"target": h, "transform": "identity"} for h in f["headers"]} for f in files}}


def canonical_fields(plan):
    return list(dict.fromkeys(entry["target"] for fields in plan["mapping"].values() for entry in fields.values()))


def validate_plan(task, files, plan):
    errors = []
    if not isinstance(task, str) or not task.strip():
        errors.append("请填写自然语言任务")
    if not isinstance(plan, dict):
        return ["方案必须为结构化对象"]
    if any(f["status"] != "ok" for f in files):
        errors.append("存在读取失败或部分读取的文件；禁止把它当成空表继续，请修复源文件后重新上传")
    if not isinstance(plan.get("row_meaning"), str) or not plan["row_meaning"].strip():
        errors.append("任务需要明确：输出每一行代表什么对象或业务记录？请填写行含义")
    if plan.get("mode") not in ("keep", "join"):
        errors.append("请选择保留独立记录或跨平台关联")
    if plan.get("dedupe") not in ("keep", "exact"):
        errors.append("重复处理只支持全部保留或确认后的文件内整行完全一致去重")
    if plan.get("dedupe") == "exact" and (not isinstance(plan.get("dedupe_basis"), str) or not plan["dedupe_basis"].strip()):
        errors.append("请说明为什么整行一致代表冗余副本，而非多次有效业务记录")
    mapping = plan.get("mapping")
    if not isinstance(mapping, dict) or set(mapping) != {f["id"] for f in files}:
        return errors + ["字段映射必须完整覆盖两份文件"]
    structurally_valid = True
    for f in files:
        entries = mapping.get(f["id"])
        if not isinstance(entries, dict) or set(entries) != set(f["headers"]):
            errors.append(f"{f['name']}的映射缺列或含未知字段")
            structurally_valid = False
            continue
        targets = []
        for h, entry in entries.items():
            if (not isinstance(entry, dict) or not isinstance(entry.get("target"), str)
                    or not entry["target"].strip() or len(entry["target"]) > 80
                    or entry["target"].startswith("__") or entry.get("transform") not in TRANSFORMS):
                errors.append(f"{f['id']}.{h}的映射/格式转换无效（统一字段不能以__开头）")
                structurally_valid = False
                continue
            targets.append(entry["target"])
        if len(targets) != len(set(targets)):
            errors.append(f"{f['name']}内多个字段映射到同一统一字段；本版请保留为不同字段以免丢值")
            structurally_valid = False
    keys = plan.get("key_fields")
    if not isinstance(keys, list) or len(keys) > 3 or any(not isinstance(k, str) for k in keys) or len(keys) != len(set(keys)):
        errors.append("关联字段须为最多3个不同字段组成的数组")
        structurally_valid = False
    if plan.get("mode") == "join" and structurally_valid:
        common = set.intersection(*({e["target"] for e in mapping[f["id"]].values()} for f in files))
        if not keys or not set(keys) <= common:
            errors.append("请选择两侧实际存在的统一字段作为关联键；不能使用单侧字段或空关联键")
        if not isinstance(plan.get("link_basis"), str) or not plan["link_basis"].strip():
            errors.append("请说明关联键为何能识别同一对象/同一业务记录；仅唯一不等于身份可靠")
        if keys and set(keys) <= common:
            original_names = [h for entries in mapping.values() for h, e in entries.items() if e["target"] in keys]
            if (all(k.strip().casefold() in NAME_LABELS for k in keys)
                    or (original_names and all(h.strip().casefold() in NAME_LABELS for h in original_names))):
                errors.append("不能仅凭姓名/名称建立关联；请增加编号、电话或业务记录区分字段，或选择不关联")
    return errors


def required_confirmations(plan):
    required = ["mapping", "grain"]
    if plan["mode"] == "join":
        required.append("link")
    if plan["dedupe"] == "exact":
        required.append("dedupe")
    if any(e["transform"] != "identity" for entries in plan["mapping"].values() for e in entries.values()):
        required.append("transforms")
    return required


def value_candidates(files):
    """Statistical evidence only: equal names / intersecting raw values never prove semantics."""
    if len(files) != 2:
        return []
    a, b = files
    result = []
    sets_a = {h: {r["values"][h] for r in a["rows"] if not missing(r["values"][h])} for h in a["headers"]}
    sets_b = {h: {r["values"][h] for r in b["rows"] if not missing(r["values"][h])} for h in b["headers"]}
    for ha in a["headers"]:
        va = sets_a[ha]
        for hb in b["headers"]:
            vb = sets_b[hb]
            intersection = va & vb
            if ha == hb or intersection:
                result.append({"a": ha, "b": hb, "overlap": len(intersection),
                    "a_distinct": len(va), "b_distinct": len(vb), "same_header": ha == hb,
                    "evidence": f"原值交集{len(intersection)}个；A非空不同值{len(va)}个，B为{len(vb)}个。"
                    + ("列名完全相同。" if ha == hb else "列名不同。")
                    + "仅为数据证据，字段是否同义、是否可作身份依据均待确认。"})
    return sorted(result, key=lambda c: (c["overlap"], c["same_header"]), reverse=True)[:30]


def prepare_units(files, plan):
    units, deletions, conversions = [], [], []
    for f in files:
        groups = {}
        for row in f["rows"]:
            source = {"file_id": f["id"], "file_name": f["name"], "record_id": row["id"],
                      "line": row["line"], "end_line": row["end_line"], "values": copy.deepcopy(row["values"])}
            signature = tuple(row["values"].items())
            if plan["dedupe"] == "exact" and signature in groups:
                survivor = groups[signature]
                survivor["sources"].append(source)
                for h, entry in plan["mapping"][f["id"]].items():
                    survivor["field_sources"][entry["target"]].append({"record_id": row["id"], "raw": row["values"][h],
                        "value": TRANSFORMS[entry["transform"]](row["values"][h])})
                deletions.append({"removed_record": row["id"], "retained_record": survivor["sources"][0]["record_id"],
                                  "basis": "同一文件全部原字段原值逐项完全相同；" + plan["dedupe_basis"], "source": source})
                continue
            values, field_sources = {}, {}
            for h, entry in plan["mapping"][f["id"]].items():
                raw = row["values"][h]
                value = TRANSFORMS[entry["transform"]](raw)
                values[entry["target"]] = value
                field_sources[entry["target"]] = [{"record_id": row["id"], "raw": raw, "value": value}]
                if raw != value:
                    conversions.append({"record_id": row["id"], "column": h, "raw": raw, "value": value,
                                        "basis": entry["transform"] + "，用户明确确认的转换"})
            unit = {"file_id": f["id"], "values": values, "sources": [source], "field_sources": field_sources}
            groups[signature] = unit
            units.append(unit)
    return units, deletions, conversions


def key_statistics(units, keys, files):
    result = {}
    for f in files:
        groups = defaultdict(list)
        incomplete = []
        for u in units:
            if u["file_id"] != f["id"]:
                continue
            key = tuple(u["values"].get(k, "") for k in keys)
            if any(missing(v) for v in key):
                incomplete.append(u["sources"][0]["record_id"])
            else:
                groups[key].append(u)
        result[f["id"]] = {"missing_records": incomplete,
            "duplicate_keys": [{"key": list(key), "records": [u["sources"][0]["record_id"] for u in group]}
                               for key, group in groups.items() if len(group) > 1],
            "complete_unique_keys": sum(len(group) == 1 for group in groups.values())}
    return result


def build_result(task, files, plan):
    units, deletions, conversions = prepare_units(files, plan)
    fields = canonical_fields(plan)
    output, problems, merged = [], [], []

    def emit(members, status, reason=""):
        values, field_sources, sources = {}, defaultdict(list), []
        for u in members:
            sources.extend(u["sources"])
            for field, origins in u["field_sources"].items():
                field_sources[field].extend(origins)
        for field in fields:
            candidates = [u["values"][field] for u in members if field in u["values"]]
            nonempty = [v for v in candidates if not missing(v)]
            values[field] = nonempty[0] if nonempty else (candidates[0] if candidates else "")
        output.append({"id": f"R{len(output) + 1:05d}", "values": values, "status": status, "reason": reason,
                       "sources": sources, "field_sources": dict(field_sources)})

    if plan["mode"] == "keep":
        for u in units:
            emit([u], "独立保留", "用户选择不进行跨平台关联")
    else:
        buckets = defaultdict(list)
        for u in units:
            key = tuple(u["values"].get(k, "") for k in plan["key_fields"])
            if any(missing(v) for v in key):
                problem = {"kind": "missing_key", "reason": "关联键缺失；原记录独立保留", "key": list(key), "records": u["sources"]}
                problems.append(problem)
                emit([u], "待确认", problem["reason"])
            else:
                buckets[key].append(u)
        for key, members in buckets.items():
            by_file = defaultdict(list)
            for u in members:
                by_file[u["file_id"]].append(u)
            if any(len(group) > 1 for group in by_file.values()):
                problem = {"kind": "ambiguous_cardinality", "key": list(key),
                    "reason": "关联键存在一对多/多对多或平台内重复；没有展开笛卡尔积，也没有选取任一条",
                    "records": [s for u in members for s in u["sources"]]}
                problems.append(problem)
                for u in members:
                    emit([u], "待确认", problem["reason"])
                continue
            if len(members) == 1:
                emit(members, "独立保留", "另一平台无匹配记录；全外关联保留")
                continue
            conflicts = {}
            for field in fields:
                values = {u["values"].get(field, "") for u in members if not missing(u["values"].get(field, ""))}
                if len(values) > 1:
                    conflicts[field] = [{"value": u["values"].get(field), "sources": u["sources"]} for u in members if field in u["values"]]
            if conflicts:
                problem = {"kind": "value_conflict", "key": list(key), "conflicts": conflicts,
                    "reason": "同一统一字段存在不同非空值：" + "、".join(conflicts) + "；保留双方值和来源，未选择任何值",
                    "records": [s for u in members for s in u["sources"]]}
                problems.append(problem)
                for u in members:
                    emit([u], "待确认", problem["reason"])
            else:
                emit(members, "已关联")
                merged.append({"key": list(key), "sources": [s["record_id"] for u in members for s in u["sources"]]})
    n = sum(f["row_count"] for f in files)
    counts = {"input_records": n, "per_file": {f["id"]: f["row_count"] for f in files},
        "deleted_copies": len(deletions), "merged_pairs": len(merged), "output_rows": len(output),
        "pending_output_rows": sum(r["status"] == "待确认" for r in output),
        "source_records_covered": sum(len(r["sources"]) for r in output)}
    assert counts["output_rows"] == n - len(deletions) - len(merged)
    assert counts["source_records_covered"] == n
    return {"task": task, "row_meaning": plan["row_meaning"], "fields": fields, "records": output,
        "problems": problems, "counts": counts, "audit": {"plan": copy.deepcopy(plan), "deduplications": deletions,
            "conversions": conversions, "merged": merged, "read_reports": files}}


def analyze(task, files, plan):
    errors = validate_plan(task, files, plan)
    info = {"errors": errors, "value_candidates": value_candidates(files), "questions": [
        "输出每一行代表一个对象，还是一次业务记录？同一对象的不同记录需要哪些字段区分？",
        "哪些字段确实同义？值重合和文字相似均不能单独证明同义。",
        "关联键是否能识别同一个对象/业务记录？仅姓名相同不够；不确定时请选择保留独立记录。",
        "整行完全相同是否确实为冗余副本？若是多次有效事件，请选择不去重。"],
        "key_candidates": [], "key_statistics": {}, "problems": [], "projected_counts": None,
        "required_confirmations": [], "proposed_actions": None}
    # A provisional analysis is not an execution and never creates downloadable results.
    try:
        structurally_safe = not any("映射" in e or "关联字段须" in e or "单侧字段" in e or "读取" in e for e in errors)
        if structurally_safe:
            units, _, _ = prepare_units(files, plan)
            common = set.intersection(*({e["target"] for e in plan["mapping"][f["id"]].values()} for f in files))
            for size in (1, 2):
                for keys in itertools.islice(itertools.combinations(sorted(common), size), 20):
                    info["key_candidates"].append({"fields": list(keys), "statistics": key_statistics(units, keys, files),
                        "basis": "仅统计缺失与重复，不能自动证明身份或记录粒度；需要用户确认"})
            if plan["mode"] == "join" and plan["key_fields"]:
                info["key_statistics"] = key_statistics(units, plan["key_fields"], files)
            info["required_confirmations"] = required_confirmations(plan)
            if not errors:
                projected = build_result(task, files, plan)
                info["problems"] = projected["problems"]
                info["projected_counts"] = projected["counts"]
                info["proposed_actions"] = {"delete_redundant_copies": projected["audit"]["deduplications"],
                    "merge_pairs": projected["audit"]["merged"],
                    "retain_records": [{"sources": [s["record_id"] for s in r["sources"]],
                        "status": r["status"], "reason": r["reason"], "values": r["values"]}
                        for r in projected["records"] if r["status"] != "已关联"]}
    except (KeyError, TypeError, ValueError):
        if not errors:
            info["errors"].append("方案结构无效，请修正字段映射")
    return info


def execute(task, files, plan, confirmations):
    errors = validate_plan(task, files, plan)
    if not errors:
        for key in required_confirmations(plan):
            if not isinstance(confirmations, dict) or confirmations.get(key) is not True:
                errors.append(f"缺少独立确认：{key}")
    if errors:
        raise PlanError(errors)
    result = build_result(task, files, plan)
    result["audit"]["confirmations"] = {k: True for k in required_confirmations(plan)}
    result["export_columns"], result["export_rows"] = export_view(result)
    return result


def export_view(result, pending=False):
    rows = []
    for r in result["records"]:
        if pending and r["status"] != "待确认":
            continue
        rows.append({**r["values"], "__记录": r["id"], "__状态": r["status"], "__原因": r["reason"],
            "__来源": json.dumps(r["sources"], ensure_ascii=False, separators=(",", ":"))})
    return result["fields"] + META, rows
