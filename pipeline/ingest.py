"""Read raw CSV records, then detect layout; excluded notes/totals remain in the audit."""
import csv
import hashlib
import io
import re
from pathlib import PurePath

from .common import LEVELS, SPECIAL, PipelineError, clean, column_role


def parse_file(file_id, filename, content):
    report = {"id": file_id, "name": PurePath(filename.replace("\\", "/")).name,
              "sha256": hashlib.sha256(content).hexdigest(), "raw_rows": [], "errors": [], "reading_actions": []}
    if not report["name"].lower().endswith(".csv") or not content or len(content) > 2 * 1024 * 1024:
        report["errors"].append("只支持非空CSV，每份最多2 MiB；没有生成替代表")
        return report
    text = None
    for encoding in (["utf-16"] if content.startswith((b"\xff\xfe", b"\xfe\xff")) else ["utf-8-sig", "gb18030"]):
        try:
            text = content.decode(encoding, errors="strict")
            report["encoding"] = encoding
            report["reading_actions"].append(f"严格以{encoding}解码；未替换字符")
            break
        except UnicodeError:
            report["reading_actions"].append(f"{encoding}解码失败")
    if text is None or "\x00" in text or "\ufffd" in text:
        report["errors"].append("无法可靠解码，或含NUL/替换字符")
        return report
    choices = []
    for delimiter in (",", ";", "\t"):
        try:
            reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
            rows = []
            while True:
                line = reader.line_num + 1
                try:
                    cells = next(reader)
                except StopIteration:
                    break
                if len(rows) >= 5100:
                    raise PipelineError("超过本版5100条物理CSV记录限制；禁止部分读取后继续计算")
                rows.append({"line": line, "end_line": reader.line_num, "cells": cells})
            strength = max((sum(column_role(c) is not None for c in r["cells"]) for r in rows[:40]), default=0)
            choices.append((strength, delimiter, rows))
        except (csv.Error, PipelineError) as exc:
            if delimiter == ",":
                report["errors"].append(f"逗号CSV读取失败：{exc}")
    if not choices:
        report["errors"].append("未能完整解析CSV")
        return report
    strength, delimiter, rows = max(choices, key=lambda c: c[0])
    if report["errors"] and strength == 0:
        return report
    report["errors"] = []
    report.update(raw_rows=rows, delimiter=delimiter)
    report["reading_actions"].append(f"实际分隔符：{repr(delimiter)}")
    return report


def layout(report, header_line=None):
    rows = report["raw_rows"]
    candidates = []
    for row in rows[:40]:
        roles = [column_role(c) for c in row["cells"]]
        score = 5 * ("id" in roles) + 3 * ("name" in roles) + sum(r is not None for r in roles)
        if score >= 4 and len(row["cells"]) >= 2:
            candidates.append((score, row))
    if header_line is not None:
        selected = next((r for r in rows if r["line"] == header_line), None)
    elif candidates:
        selected = max(candidates, key=lambda c: c[0])[1]
    else:
        selected = None
    if selected is None:
        return {"headers": [], "rows": [], "excluded": [], "errors": ["真实表头尚不明确；请指定header_line和字段映射"]}
    headers = selected["cells"]
    errors = []
    if len(headers) != len(set(headers)) or any(not clean(h) for h in headers) or len(headers) > 128:
        errors.append("真实表头含空列名、重复列名或超过128列")
    if header_line is None and candidates:
        strongest = max(c[0] for c in candidates)
        alternatives = [r for score, r in candidates if score == strongest and r["cells"] != headers]
        if alternatives:
            errors.append("存在多个不同的高置信候选表头；请指定header_line")
    tail = rows[rows.index(selected) + 1:]
    footer_lines = set()
    for row in reversed(tail):
        nonempty = [clean(c) for c in row["cells"] if clean(c)]
        if not nonempty:
            footer_lines.add(row["line"])
        elif nonempty[0] in ("合计", "总计", "汇总", "平均", "平均分", "总人数"):
            footer_lines.add(row["line"])
        else:
            break
    records, excluded = [], []
    for row in rows:
        reason = None
        if row["line"] < selected["line"]:
            reason = "顶部说明行"
        elif row["line"] == selected["line"]:
            reason = "真实表头"
        elif row["line"] in footer_lines:
            reason = "底部汇总/空白行"
        elif row["cells"] == headers:
            reason = "重复表头"
        elif not row["cells"]:
            reason = "空白物理行"
        if reason:
            excluded.append({**row, "reason": reason})
        elif len(row["cells"]) != len(headers):
            errors.append(f"第{row['line']}行列数错误：期望{len(headers)}，实际{len(row['cells'])}；禁止执行")
            excluded.append({**row, "reason": "坏行，未忽略，阻断阶段一"})
        else:
            records.append({"record_id": f"{report['id']}:{row['line']}", "line": row["line"],
                            "end_line": row["end_line"], "values": dict(zip(headers, row["cells"]))})
    return {"header_line": selected["line"], "headers": headers, "rows": records, "excluded": excluded, "errors": errors}


def suggest_plan(files, task):
    platforms = []
    for f in files:
        table = layout(f)
        headers = table["headers"]
        roles = {role: next((h for h in headers if column_role(h) == role), None)
                 for role in ("id", "name", "class", "phone", "time")}
        metrics = []
        for h in headers:
            if column_role(h) or clean(h) in ("满分", "总分上限"):
                continue
            if not any(word in h for word in ("成绩", "得分", "分数", "实验", "练习", "测验", "作业", "考勤", "等级")):
                continue
            target = re.sub(r"[（(].*?[）)]", "", clean(h))
            target = re.sub(r"(成绩|得分|分数)$", "", target) or clean(h)
            category = "practice" if any(t in h for t in ("实验", "练习")) else "assignment" if any(t in h for t in ("作业", "测验")) else "attendance" if "考勤" in h else "other"
            strategy = {"practice": "max", "assignment": "latest", "attendance": "confirm", "other": "unique"}[category]
            values = [clean(r["values"][h]) for r in table["rows"] if clean(r["values"][h]) not in SPECIAL]
            explicit = re.search(r"满分\s*[:：]?\s*(\d+(?:\.\d+)?)", h)
            scale = {"kind": "points", "max_score": explicit[1]} if explicit else {"kind": "unknown"}
            if values and all(v in LEVELS for v in values):
                scale = {"kind": "grade", "levels": LEVELS.copy()}
            elif values and all(v.endswith("%") for v in values):
                scale = {"kind": "percent"}
            explicit_source_percent = re.search(r"(?:原始|输入|原分制)(?:成绩|数据|分数|均|都|是|为|按|采用|皆|各项){0,8}百分制", task)
            if explicit_source_percent or "百分制" in h:
                scale = {"kind": "percent"} if not explicit else scale
            instruction_max = re.search(re.escape(target) + r"\s*满分\s*(?:为|[:：=])?\s*(\d+(?:\.\d+)?)", task)
            if instruction_max:
                scale = ({"kind": "conflict", "header_max": explicit[1], "task_max": instruction_max[1]}
                         if explicit and explicit[1] != instruction_max[1]
                         else {"kind": "points", "max_score": instruction_max[1]})
            if scale.get("kind") == "grade":
                for grade in LEVELS:
                    prefix = r"(?<!不)" if grade == "及格" else ""
                    override = re.search(prefix + re.escape(grade) + r"\s*(?:=|＝|按|记|对应|折算为)\s*(\d+(?:\.\d+)?)", task)
                    if override:
                        scale["levels"][grade] = override[1]
            # A user instruction can override the category default, but only for the named metric.
            if re.search(re.escape(target) + r"[^，。；,;]{0,8}(?:取最高|max)", task, re.I):
                strategy = "max"
            if re.search(re.escape(target) + r"[^，。；,;]{0,8}(?:取最后|latest)", task, re.I):
                strategy = "latest"
            if category == "attendance":
                if re.search(r"考勤[^，。；,;]{0,8}(?:累加|求和)", task):
                    strategy = "sum"
                elif re.search(r"考勤[^，。；,;]{0,8}(?:均值|平均)", task):
                    strategy = "mean"
            metrics.append({"column": h, "target": target, "category": category, "strategy": strategy,
                            "timestamp_column": roles["time"], "scale": scale})
        platforms.append({"file_id": f["id"], "header_line": table.get("header_line"),
            "role": "scores" if metrics else "roster", "id_column": roles["id"], "name_column": roles["name"],
            "class_column": roles["class"], "phone_column": roles["phone"], "metrics": metrics})
        grain_candidates = [h for h in headers if clean(h) in ("活动", "活动编号", "项目编号", "作业编号", "实验编号", "科目")]
        if grain_candidates:
            platforms[-1]["record_grain"] = grain_candidates
    explicit_rosters = [p["file_id"] for p in platforms if p["role"] == "roster" and p["id_column"]]
    return {"platforms": platforms, "roster_file": explicit_rosters[0] if len(explicit_rosters) == 1 else None,
        "identity_resolutions": {}, "excluded_records": {}, "append_unmatched_ids": [], "cell_resolutions": {}}
