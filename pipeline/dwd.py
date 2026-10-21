"""Stage 1 only: identity, submission aggregation and unit conversion. No business weights."""
import json
from collections import defaultdict
from datetime import timezone
from decimal import Decimal, localcontext
from difflib import SequenceMatcher
from pathlib import Path

from .common import LEVELS, SPECIAL, PipelineError, clean, csv_bytes, dump, number, sha, submission_time
from .ingest import layout


def normalize_score(raw, scale):
    text = clean(raw)
    if text in SPECIAL:
        return text, {"raw": raw, "value": text, "basis": "保留原采集状态，阶段一未填0"}
    kind = scale.get("kind")
    with localcontext() as context:
        context.prec = 50
        if kind == "grade":
            levels = scale.get("levels", LEVELS)
            if text not in levels:
                raise PipelineError("等级不在明确的等级映射中")
            value = number(levels[text])
            basis = {"grade_map": levels}
        elif kind == "percent":
            value = number(text[:-1] if text.endswith("%") else text)
            basis = "明确百分制"
        elif kind == "points":
            maximum = number(scale.get("max_score"))
            if maximum <= 0:
                raise PipelineError("满分必须大于0")
            source = number(text)
            if not 0 <= source <= maximum:
                raise PipelineError("原始分数超出明确满分范围")
            value = source * Decimal(100) / maximum
            basis = {"formula": "raw * 100 / max_score", "max_score": str(maximum), "decimal_precision": 50}
        else:
            raise PipelineError("分制不明确；请给定百分制、满分或等级映射")
        if not 0 <= value <= 100:
            raise PipelineError("标准化后的单次分数不在0至100范围")
        return str(value), {"raw": raw, "value": str(value), "basis": basis}


def aggregate(observations, metric):
    if len(observations) == 1:
        return observations[0]["value"], "单条有效采集记录，无需去重排序"
    strategy = metric["strategy"]
    if strategy == "unique" and len({o["value"] for o in observations}) == 1:
        return observations[0]["value"], "仅接受一致值：全部原始记录与来源保留"
    if strategy == "latest":
        times = []
        for o in observations:
            raw = clean(o.get("timestamp", ""))
            try:
                parsed = submission_time(raw)
            except PipelineError:
                raise PipelineError("latest缺少完整可解析的提交时间（须含日期和时分），不能用文件行序或推断的零点代替时间")
            aware = parsed.tzinfo is not None
            times.append((parsed.astimezone(timezone.utc).isoformat() if aware else parsed.isoformat(), aware, o))
        if len({aware for _, aware, _ in times}) > 1:
            raise PipelineError("提交时间混合带时区和无时区值，无法可靠排序")
        latest = max(t for t, _, _ in times)
        values = {o["value"] for t, _, o in times if t == latest}
        if len(values) != 1:
            raise PipelineError("最后提交时间相同但成绩不同，不能选择任一条")
        return values.pop(), "latest：按完整提交时间排序，不按文件顺序"
    numeric, states = [], set()
    for o in observations:
        if o["value"] in SPECIAL:
            states.add(o["value"])
        else:
            numeric.append(number(o["value"]))
    if strategy in ("max", "sum", "mean"):
        if numeric and states - {"", "未交"}:
            raise PipelineError("同组数值与缺考/缓考/免修等状态并存，需要确认应保留的状态")
        if not numeric:
            if len(states) == 1:
                return states.pop(), strategy + "：没有有效数值，保留一致的原状态"
            raise PipelineError("同组存在多种特殊状态，需要确认")
        with localcontext() as context:
            context.prec = 50
            value = max(numeric) if strategy == "max" else sum(numeric, Decimal(0))
            if strategy == "mean":
                value /= len(numeric)
            if strategy in ("sum", "mean") and metric["scale"].get("kind") == "points":
                raw_values = [number(clean(o["source"]["values"][metric["column"]])) for o in observations if o["value"] not in SPECIAL]
                value = sum(raw_values, Decimal(0))
                if strategy == "mean":
                    value /= len(raw_values)
                value = value * Decimal(100) / number(metric["scale"]["max_score"])
        return str(value), strategy + "：仅对实际数值执行；原缺失尝试未填0，全部尝试均留审计"
    raise PipelineError("重复业务记录尚无明确聚合策略；考勤需明确sum或mean，其他项目需指定策略")


def materialize(files, plan, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    issues, layouts, sources, ledger, conversions, identity_actions, dedup = [], {}, [], {}, [], [], []

    def issue(kind, reason, records=None, **extra):
        item = {"id": f"Q{len(issues) + 1}", "kind": kind, "reason": reason, "records": records or [], **extra}
        issues.append(item)

    if not isinstance(plan, dict) or not isinstance(plan.get("platforms"), list):
        raise PipelineError("阶段一方案必须包含platforms数组")
    specs = plan["platforms"]
    if len(specs) != len(files) or {p.get("file_id") for p in specs if isinstance(p, dict)} != {f["id"] for f in files}:
        raise PipelineError("阶段一方案必须恰好覆盖每个上传文件，file_id不可重复")
    spec_by_id = {p["file_id"]: p for p in specs}
    identities = {}
    metrics = []
    for f in files:
        p = spec_by_id[f["id"]]
        table = layout(f, p.get("header_line"))
        layouts[f["id"]] = table
        if f["errors"] or table["errors"]:
            issue("reading", "；".join(f["errors"] + table["errors"]), file_id=f["id"])
        for key in ("id_column", "name_column", "class_column", "phone_column"):
            if p.get(key) is not None and p[key] not in table["headers"]:
                issue("mapping", f"{f['id']}的{key}引用未知字段")
        if not isinstance(p.get("metrics"), list):
            raise PipelineError("metrics必须为数组")
        seen_targets = set()
        for m in p["metrics"]:
            if (not isinstance(m, dict) or m.get("column") not in table["headers"]
                    or not isinstance(m.get("target"), str) or not m["target"].strip()
                    or m["target"].startswith("__") or m["target"] in ("student_id", "name", "class", "phone")
                    or m["target"] in seen_targets or not isinstance(m.get("scale"), dict)
                    or m.get("strategy") not in ("max", "latest", "sum", "mean", "unique", "confirm")):
                raise PipelineError("项目映射、策略或分制结构无效；同一文件不能覆盖同一目标列")
            if m.get("timestamp_column") is not None and m["timestamp_column"] not in table["headers"]:
                raise PipelineError("提交时间字段不在真实表头中")
            seen_targets.add(m["target"])
            if m["target"] not in metrics:
                metrics.append(m["target"])
        for row in table["rows"]:
            src = {"file_id": f["id"], "file_name": f["name"], **row}
            source_id = row["record_id"]
            sources.append(src)
            ident = {standard: clean(row["values"].get(p.get(column), "")) for standard, column in
                     (("student_id", "id_column"), ("name", "name_column"), ("class", "class_column"), ("phone", "phone_column"))}
            identities[source_id] = ident
            ledger[source_id] = {"source": src, "disposition": "pending"}
            for standard, column in (("student_id", "id_column"), ("name", "name_column"), ("class", "class_column"), ("phone", "phone_column")):
                raw_value = row["values"].get(p.get(column))
                if p.get(column) and raw_value != ident[standard]:
                    conversions.append({"record_id": source_id, "field": standard,
                        "raw": raw_value, "value": ident[standard], "basis": "清除Unicode不可见字符并去首尾空白，保留编号前导零"})
    if not metrics:
        issue("mapping", "未识别任何数值项目；请在阶段一方案中指定metrics")
    valid_roster_ids = [p["file_id"] for p in specs if p.get("id_column")]
    roster_id = plan.get("roster_file")
    if roster_id is None and valid_roster_ids:
        roster_id = max(valid_roster_ids, key=lambda fid: len({identities[r["record_id"]]["student_id"]
                         for r in layouts[fid]["rows"] if identities[r["record_id"]]["student_id"]}))
    if roster_id not in valid_roster_ids:
        issue("roster", "缺少有可靠编号的主表；请指定花名册或提供主键映射")
        roster_id = None
    entities, roster_sources = {}, defaultdict(list)
    by_record = {s["record_id"]: s for s in sources}
    exclusions = plan.get("excluded_records", {})
    resolutions = plan.get("identity_resolutions", {})
    cell_resolutions = plan.get("cell_resolutions", {})
    if any(not isinstance(v, dict) for v in (exclusions, resolutions, cell_resolutions)):
        raise PipelineError("人工补充记录必须为对象")

    def exclusion(record_id):
        reason = exclusions.get(record_id)
        if reason is not None:
            if not isinstance(reason, str) or not reason.strip():
                raise PipelineError("排除异常记录必须明确填写原因")
            ledger[record_id]["disposition"] = "excluded_by_user"
            ledger[record_id]["reason"] = reason
            return True
        return False

    if roster_id:
        for row in layouts[roster_id]["rows"]:
            rid = row["record_id"]
            if exclusion(rid):
                continue
            ident = identities[rid]
            sid = ident["student_id"]
            if not sid:
                issue("identity", "主表记录没有编号，不能生成每行一个编号的宽表", [by_record[rid]])
                continue
            roster_sources[sid].append(rid)
            if sid not in entities:
                entities[sid] = ident.copy()
            else:
                for key in ("name", "class", "phone"):
                    values = {ident[key], entities[sid][key]} - {""}
                    if len(values) > 1:
                        choice = cell_resolutions.get(f"{sid}|{key}")
                        if isinstance(choice, dict) and choice.get("reason") and choice.get("value") in values:
                            entities[sid][key] = choice["value"]
                        else:
                            issue("roster_conflict", f"主表编号{sid}的{key}冲突", [by_record[r] for r in roster_sources[sid]], student_id=sid, field=key)
            ledger[rid]["disposition"] = "roster_entity"
    appended = plan.get("append_unmatched_ids", [])
    if not isinstance(appended, list) or any(not isinstance(x, str) or not clean(x) for x in appended):
        raise PipelineError("append_unmatched_ids须为明确的编号数组")
    for src in sources:
        ident = identities[src["record_id"]]
        if ident["student_id"] in appended and not exclusion(src["record_id"]):
            entities.setdefault(ident["student_id"], ident.copy())
    observations = defaultdict(list)
    grouped_sources = defaultdict(list)
    for src in sources:
        rid, fid = src["record_id"], src["file_id"]
        p, ident = spec_by_id[fid], identities[rid]
        if exclusion(rid):
            continue
        sid = ident["student_id"]
        decision = resolutions.get(rid)
        if decision is not None:
            if not isinstance(decision, dict) or not isinstance(decision.get("reason"), str) or not decision["reason"].strip() or decision.get("student_id") not in entities:
                raise PipelineError("人工身份确认须引用主表实际编号并附原因")
            sid = decision["student_id"]
            identity_actions.append({"record_id": rid, "student_id": sid, "method": "teacher_confirmation", "reason": decision["reason"], "source": src})
        elif not sid:
            exact, candidates = [], []
            for candidate_id, master in entities.items():
                secondary_match = [k for k in ("class", "phone") if ident[k] and master[k] and ident[k] == master[k]]
                secondary_conflict = any(ident[k] and master[k] and ident[k] != master[k] for k in ("class", "phone"))
                ratio = SequenceMatcher(None, ident["name"], master["name"]).ratio() if ident["name"] else 0
                if ident["name"] and ident["name"] == master["name"] and secondary_match and not secondary_conflict:
                    exact.append(candidate_id)
                if ratio >= .6 and (secondary_match or ident["name"] == master["name"]):
                    candidates.append({"student_id": candidate_id, "name_similarity": ratio,
                                       "secondary_matches": secondary_match, "secondary_conflict": secondary_conflict, "master": master})
            if len(exact) == 1:
                sid = exact[0]
                identity_actions.append({"record_id": rid, "student_id": sid, "method": "unique_name_plus_secondary",
                    "reason": "清理格式后的姓名与班级/电话精确一致、无辅助字段冲突、主表候选唯一", "source": src})
            else:
                ledger[rid]["disposition"] = "pending_identity"
                issue("identity", "无编号记录不能可靠唯一对齐；模糊候选只提示，不自动补全", [src], candidates=candidates)
                continue
        if sid not in entities:
            ledger[rid]["disposition"] = "unmatched_left_join"
            issue("unmatched", f"编号{sid}不在主表；已进入异常清单，未静默丢失。可追加主表或明确排除原因", [src], student_id=sid)
            continue
        if decision is None and ident["name"] and entities[sid]["name"] and ident["name"] != entities[sid]["name"]:
            ledger[rid]["disposition"] = "pending_identity"
            issue("identity", f"编号{sid}一致但姓名不同，需要确认", [src], candidates=[entities[sid]])
            continue
        ledger[rid].update(disposition="retained", student_id=sid)
        grouped_sources[(fid, sid, tuple(src["values"].get(c, "") for c in p.get("record_grain", [])))].append(rid)
        for m in p["metrics"]:
            if any(src["values"].get(c) != v for c, v in m.get("where", {}).items()):
                continue
            try:
                value, conversion = normalize_score(src["values"][m["column"]], m["scale"])
                conversions.append({"record_id": rid, "field": m["target"], **conversion})
                time_field = m.get("timestamp_column")
                raw_time = src["values"].get(time_field, "")
                correction = plan.get("timestamp_resolutions", {}).get(rid, {}).get(time_field)
                timestamp = correction["value"] if correction else raw_time
                if correction:
                    conversions.append({"record_id": rid, "field": time_field, "raw": raw_time, "value": timestamp,
                                        "basis": "教师补充/修正提交时间：" + correction["reason"]})
                observations[(fid, sid, m["target"])].append({"value": value, "source": src,
                    "timestamp": timestamp, "timestamp_original": raw_time,
                    "record_grain": [src["values"].get(c, "") for c in p.get("record_grain", [])]})
            except PipelineError as exc:
                correction = cell_resolutions.get(f"{sid}|{m['target']}")
                if isinstance(correction, dict) and isinstance(correction.get("reason"), str) and correction["reason"].strip():
                    value = str(correction.get("value", ""))
                    if value not in SPECIAL:
                        number(value)
                    observations[(fid, sid, m["target"])].append({"value": value, "source": src,
                        "timestamp": plan.get("timestamp_resolutions", {}).get(rid, {}).get(m.get("timestamp_column"), {}).get("value", src["values"].get(m.get("timestamp_column"), ""))})
                    conversions.append({"record_id": rid, "field": m["target"], "raw": src["values"][m["column"]],
                        "value": value, "basis": "教师明确补充百分制值/状态：" + correction["reason"]})
                else:
                    ledger[rid]["disposition"] = "pending_numeric"
                    issue("scale", f"{m['target']}：{exc}", [src], metric=m["target"], student_id=sid)
    values_by_entity, cell_sources, aggregated = defaultdict(dict), defaultdict(dict), {}
    for p in specs:
        fid = p["file_id"]
        for sid in entities:
            for m in p["metrics"]:
                obs = observations.get((fid, sid, m["target"]), [])
                if not obs:
                    continue
                key = f"{sid}|{m['target']}"
                choice = cell_resolutions.get(key)
                cell_sources[sid].setdefault(m["target"], []).extend(obs)
                try:
                    grains = {tuple(o.get("record_grain", [])) for o in obs}
                    if p.get("record_grain") and any(any(not clean(v) for v in grain) for grain in grains) and not isinstance(choice, dict):
                        raise PipelineError("区分业务活动的字段存在空值，请核对原始记录，不能推断属于哪项活动")
                    if len(grains) > 1 and not isinstance(choice, dict):
                        raise PipelineError("同一编号包含不同活动记录，不能作为重复提交归并；请按活动分列或补充明确裁决")
                    if isinstance(choice, dict) and choice.get("reason"):
                        chosen = str(choice.get("value", ""))
                        if chosen not in SPECIAL:
                            number(chosen)
                        basis = "教师明确补充/裁决：" + str(choice["reason"])
                    else:
                        chosen, basis = aggregate(obs, m)
                    aggregated[(fid, sid, m["target"])] = chosen
                    previous = values_by_entity[sid].get(m["target"])
                    if previous is not None and previous != chosen:
                        if isinstance(choice, dict) and choice.get("reason"):
                            previous = chosen
                        else:
                            raise PipelineError("跨平台同一项目数值冲突；请分列或明确裁决")
                    values_by_entity[sid][m["target"]] = chosen
                    if len(obs) > 1:
                        dedup.append({"file_id": fid, "student_id": sid, "metric": m["target"], "strategy": m["strategy"],
                            "value": chosen, "basis": basis, "observations": obs})
                except PipelineError as exc:
                    if "跨平台" in str(exc):
                        values_by_entity[sid][m["target"]] = "冲突:" + json.dumps(sorted({o["value"] for o in cell_sources[sid][m["target"]]}), ensure_ascii=False)
                    affected = cell_sources[sid][m["target"]] if "跨平台" in str(exc) else obs
                    issue("deduplication", f"{sid} / {m['target']}：{exc}", [o["source"] for o in affected], metric=m["target"], student_id=sid)
    rows = [{**entity, **{metric: values_by_entity[sid].get(metric, "") for metric in metrics}}
            for sid, entity in sorted(entities.items())]
    if not rows:
        issue("roster", "主表没有可用编号记录，不能输出空表并声称阶段一完成")
    missing = [{"student_id": row["student_id"], "metric": metric, "state": row[metric]}
               for row in rows for metric in metrics if row[metric] in SPECIAL]
    collapsed = 0
    for (fid, sid, grain), ids in grouped_sources.items():
        p = spec_by_id[fid]
        if len(ids) > 1 and p["metrics"] and all((fid, sid, m["target"]) in aggregated for m in p["metrics"]):
            collapsed += len(ids) - 1
            for rid in ids:
                ledger[rid]["disposition"] = "folded_with_all_sources_retained"
    counts = {"platforms": len(files), "input_records": len(sources), "wide_rows": len(rows),
        "collapsed_submissions": collapsed, "auto_resolved_no_id": sum(a["method"] == "unique_name_plus_secondary" for a in identity_actions),
        "teacher_identity_resolutions": sum(a["method"] == "teacher_confirmation" for a in identity_actions),
        "missing_cells": len(missing), "missing_students": len({m["student_id"] for m in missing}),
        "unmatched_records": sum(v["disposition"] == "unmatched_left_join" for v in ledger.values()),
        "excluded_by_user": sum(v["disposition"] == "excluded_by_user" for v in ledger.values()),
        "source_records_audited": len(ledger)}
    audit = {"plan": plan, "roster_file": roster_id, "roster_sources": dict(roster_sources), "layouts": layouts,
        "inputs": files, "source_ledger": ledger, "identity_actions": identity_actions, "conversions": conversions,
        "deduplications": dedup, "cell_sources": dict(cell_sources), "missing": missing, "issues": issues, "counts": counts}
    columns = ["student_id", "name", "class", "phone"] + metrics
    dump(directory / "data.json", {"columns": columns, "metrics": metrics, "rows": rows, "cell_sources": dict(cell_sources)})
    (directory / "wide.csv").write_bytes(csv_bytes(columns, rows))
    dump(directory / "audit.json", audit)
    manifest = {"stage": "DWD", "status": "blocked" if issues else "ready", "roster_file": roster_id,
        "counts": counts, "issues": issues, "files": {name: sha(directory / name) for name in ("data.json", "wide.csv", "audit.json")}}
    dump(directory / "manifest.json", manifest)
    return {"status": manifest["status"], "manifest_sha256": sha(directory / "manifest.json"),
        "counts": counts, "issues": issues, "missing": missing, "columns": columns, "rows": rows,
        "directory": str(directory), "roster_file": roster_id}
