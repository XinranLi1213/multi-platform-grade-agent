"""Strict CSV reading and profiling; raw strings and physical locations are authoritative."""

import csv
import hashlib
import io
import re
from collections import Counter, defaultdict
from pathlib import PurePath

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_COLUMNS = 128
csv.field_size_limit(MAX_BYTES)


def missing(value):
    return value.strip() == ""


def read_upload(file_id, name, content):
    name = PurePath(name.replace("\\", "/")).name
    data = {"id": file_id, "name": name, "sha256": hashlib.sha256(content).hexdigest(),
            "status": "failed", "encoding": None, "delimiter": None, "repairs": [],
            "errors": [], "headers": [], "rows": [], "blank_lines": [], "profile": {},
            "row_count": 0, "bad_record_count": 0, "byte_count": len(content), "unread_remainder": False}
    if not name.lower().endswith(".csv"):
        data["errors"].append({"reason": "暂不支持此格式；仅支持CSV。未尝试读取。", "scope": "整个文件"})
        return data
    if not content or len(content) > MAX_BYTES:
        data["errors"].append({"reason": "空文件" if not content else "超过2 MiB限制；未读取为数据表", "scope": "整个文件"})
        return data
    encodings = ["utf-16"] if content.startswith((b"\xff\xfe", b"\xfe\xff")) else ["utf-8-sig", "gb18030"]
    text = None
    for encoding in encodings:
        try:
            text = content.decode(encoding, errors="strict")
            data["encoding"] = encoding
            if encoding != "utf-8-sig":
                data["repairs"].append(f"严格使用 {encoding} 解码成功；未替换任何字符。请核对预览文字。")
            break
        except UnicodeDecodeError as exc:
            data["repairs"].append(f"{encoding} 严格解码失败，字节范围 {exc.start}—{exc.end}。")
    if text is None or "\x00" in text or "\ufffd" in text:
        data["errors"].append({"reason": "无法可靠解码，或内容含NUL/替换字符；请从源平台重新导出UTF-8 CSV", "scope": "整个文件"})
        return data
    if not text.strip():
        data["errors"].append({"reason": "文件只有空白内容", "scope": "整个文件"})
        return data
    delimiter = ","
    try:
        delimiter = csv.Sniffer().sniff(text[:8192], delimiters=",;\t").delimiter
    except csv.Error:
        data["repairs"].append("无法稳定识别分隔符，按CSV逗号格式严格尝试；坏行会明确列出。")
    data["delimiter"] = delimiter
    if delimiter != ",":
        data["repairs"].append(f"识别并使用 {'制表符' if delimiter == chr(9) else '分号'} 分隔；未改变字段值。")
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    try:
        headers = next(reader)
        if (not headers or any(missing(h) for h in headers) or len(set(headers)) != len(headers)
                or len(headers) > MAX_COLUMNS):
            raise ValueError("表头为空、列名为空/重复，或超过128列；不能安全寻址字段")
        data["headers"] = headers
        record_index = 0
        while True:
            start = reader.line_num + 1
            try:
                values = next(reader)
            except StopIteration:
                break
            if not values:
                data["blank_lines"].append(start)
                continue
            record_index += 1
            if record_index > MAX_ROWS:
                raise ValueError("超过5000条记录限制；当前只读到限制处，后续未处理，禁止执行")
            if len(values) != len(headers):
                data["errors"].append({"line": start, "end_line": reader.line_num, "values": values,
                    "reason": f"列数错误：期望{len(headers)}列，实际{len(values)}列", "scope": "此记录未进入数据表"})
                data["bad_record_count"] += 1
                continue
            data["rows"].append({"id": f"{file_id}:{record_index}", "line": start,
                "end_line": reader.line_num, "values": dict(zip(headers, values))})
    except (csv.Error, ValueError, StopIteration) as exc:
        data["unread_remainder"] = True
        data["errors"].append({"line": reader.line_num, "reason": str(exc) or "缺少表头",
                                "scope": "记录边界或结构不可确定，读取在此停止；后续未处理，禁止执行"})
    data["row_count"] = len(data["rows"])
    if not data["rows"] and not data["errors"]:
        data["errors"].append({"reason": "只有表头，没有数据记录", "scope": "整个文件"})
    data["status"] = ("partial" if data["rows"] else "failed") if data["errors"] else "ok"
    groups = defaultdict(list)
    for row in data["rows"]:
        groups[tuple(row["values"].values())].append(row["id"])
    columns = {}
    for h in data["headers"]:
        values = [r["values"][h] for r in data["rows"]]
        populated = [v for v in values if not missing(v)]
        counts = Counter(populated)
        columns[h] = {"missing": len(values) - len(populated), "distinct": len(counts),
            "duplicate_values": {k: v for k, v in counts.items() if v > 1},
            "surrounding_whitespace": sum(v != v.strip() for v in values),
            "control_characters": sum(bool(re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", v)) for v in values)}
    duplicates = [ids for ids in groups.values() if len(ids) > 1]
    data["profile"] = {"columns": columns, "exact_duplicate_groups": duplicates,
                       "redundant_copies": sum(len(ids) - 1 for ids in duplicates)}
    return data
