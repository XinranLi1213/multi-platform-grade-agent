import csv
import hashlib
import io
import json
import re
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


class PipelineError(ValueError):
    pass


class InputProblem(PipelineError):
    """A correctable, located input error, safe to show without a traceback."""
    def __init__(self, reason, kind="plan", **context):
        super().__init__(reason)
        self.problem = {"kind": kind, "reason": reason, "status": "未执行，原始上传与草稿保留",
                        "impact": "当前请求未通过检查", "actions": ["修正后重试"], **context}


class ExecutionFault(RuntimeError):
    """Unexpected runner failure, never a request for business rules."""
    pass


def clean(value):
    return "".join(c for c in str(value) if unicodedata.category(c) not in ("Cf", "Cc")).strip()


def number(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError):
        raise PipelineError(f"不是有限数值：{value}")


def submission_time(value):
    """Do not silently turn a date-only submission into midnight."""
    text = clean(value)
    if not re.search(r"[Tt ]\d{2}:\d{2}", text):
        raise PipelineError("提交时间须包含日期和时分，不能推断缺失的时间")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise PipelineError("提交时间无法解析，请核对日期、时分和时区")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def csv_bytes(columns, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


ID_ALIASES = {"学号", "学生编号", "工号", "账号", "studentid", "student_id", "编号"}
NAME_ALIASES = {"姓名", "学生姓名", "name"}
CLASS_ALIASES = {"班级", "行政班", "class"}
PHONE_ALIASES = {"电话", "手机号", "联系电话", "phone"}
TIME_ALIASES = {"提交时间", "提交日期", "完成时间", "timestamp", "submitted_at"}
LEVELS = {"优": "95", "良": "85", "中": "75", "及格": "65", "不及格": "0"}
SPECIAL = {"", "缺考", "缓考", "免修", "未交", "缺勤"}


def column_role(value):
    value = clean(value).casefold()
    for role, aliases in (("id", ID_ALIASES), ("name", NAME_ALIASES), ("class", CLASS_ALIASES),
                          ("phone", PHONE_ALIASES), ("time", TIME_ALIASES)):
        if value in aliases:
            return role
    return None
