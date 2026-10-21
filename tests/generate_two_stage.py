"""Standalone simulated data and independent Python reference answers. Never imported by app."""
import csv
import io
import json
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "two_stage_data"


def write(name, rows):
    stream = io.StringIO(newline="")
    csv.writer(stream).writerows(rows)
    (ROOT / name).write_bytes(stream.getvalue().encode("utf-8-sig"))


def main():
    ROOT.mkdir(exist_ok=True)
    roster = [["本课程主花名册"], ["说明：编号按文本保存"], ["学号", "学生姓名", "班级", "手机号"],
        [" 001\u200b ", "张伟", "1班", "13800000001"], ["002", "张伟", "2班", "13800000002"],
        ["003", "李青", "1班", "13800000003"], ["合计", "3", "", ""]]
    practice = [["练习平台导出说明"], ["StudentID", "姓名", "班级", "实验成绩(满分80)", "备注"],
        ["001", "张伟", "1班", "40", "首次"], ["001", "张伟", "1班", "64", "重交"],
        ["002", "张伟", "2班", "72", "首次"], ["003", "李青", "1班", "56", "首次"], ["合计", "", "", "232", ""]]
    assignments = [["说明：文件行序不是提交时间顺序"], ["账号", "姓名", "班级", "作业成绩(满分100)", "提交时间"],
        ["001", "张伟", "1班", "90", "2026-10-20T10:00:00"], ["001", "张伟", "1班", "80", "2026-10-19T10:00:00"],
        ["", "张伟", "2班", "100", "2026-10-20T11:00:00"], ["003", "李青", "1班", "未交", "2026-10-20T11:00:00"],
        ["汇总", "", "", "270", ""]]
    write("roster.csv", roster)
    write("practice.csv", practice)
    write("assignment.csv", assignments)
    ambiguous = [row.copy() for row in assignments]
    ambiguous[4][2] = ""
    write("ambiguous.csv", ambiguous)
    missing_time = [row.copy() for row in assignments]
    missing_time[3][4] = ""
    write("missing_time.csv", missing_time)
    orphan = [row.copy() for row in assignments]
    orphan.insert(-1, ["999", "赵平", "3班", "60", "2026-10-20T11:00:00"])
    write("orphan.csv", orphan)
    write("levels.csv", [["工号", "姓名", "练习等级"], ["001", "张伟", "优"], ["001", "张伟", "良"],
        ["002", "张伟", "不及格"], ["003", "李青", "缓考"]])
    write("attendance.csv", [["学号", "姓名", "考勤成绩(满分3)"], ["001", "张伟", "1"], ["001", "张伟", "1"],
        ["001", "张伟", "1"], ["002", "张伟", "2"], ["003", "李青", "3"]])
    write("many_metrics.csv", [["学号", "姓名", "实验1成绩(满分100)", "实验2成绩(满分100)", "实验3成绩(满分100)", "作业成绩(满分100)"],
        ["001", "张伟", "80", "100", "90", "80"], ["002", "张伟", "70", "60", "80", "90"], ["003", "李青", "50", "60", "70", "70"]])
    write("bad.csv", [["学号", "姓名", "实验成绩(满分100)"], ["001", "张伟", "80"], ["002", "坏行", "90", "多一列"]])
    write("lab_conflict.csv", [["学号", "姓名", "实验成绩(满分100)"], ["001", "张伟", "90"], ["002", "张伟", "90"], ["003", "李青", "70"]])
    # Reference answers are computed by Python, not copied from model arithmetic.
    expected = {}
    for sid, attempts, homework in [("001", [Decimal(40), Decimal(64)], Decimal(90)),
                                    ("002", [Decimal(72)], Decimal(100)), ("003", [Decimal(56)], Decimal(0))]:
        lab = max(attempts) * 100 / 80
        expected[sid] = {"实验": str(lab), "作业": str(homework),
            "总评": str((lab * Decimal("0.4") + homework * Decimal("0.6")).quantize(Decimal("0.1")))}
    (ROOT / "expected_python.json").write_text(json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8")
    rules = {"weights": {"实验": "0.4", "作业": "0.6"}, "missing": {"未交": {"action": "zero"}}, "precision": 1,
             "excellent_threshold": "90", "excellent_denominator": "numeric", "summary_field": "总评"}
    (ROOT / "rules.json").write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    advanced = {"formulas": [
        {"name": "精选实验", "expr": 'mean(best(2,field("实验1"),field("实验2"),field("实验3")))'},
        {"name": "封顶平时", "expr": 'min(100,field("精选实验")+10)'},
        {"name": "总评", "expr": 'field("封顶平时")*0.4+field("作业")*0.6'},
        {"name": "绩点", "expr": 'ifelse(field("总评")>=90,4,ifelse(field("总评")>=80,3,0))'}],
        "precision": 1, "summary_field": "总评", "excellent_threshold": "90", "excellent_denominator": "all"}
    (ROOT / "advanced_rules.json").write_text(json.dumps(advanced, ensure_ascii=False, indent=2), encoding="utf-8")
    oracle = {}
    for sid, labs, homework in [("001", [80, 100, 90], 80), ("002", [70, 60, 80], 90), ("003", [50, 60, 70], 70)]:
        selected = sum(sorted([Decimal(v) for v in labs], reverse=True)[:2]) / 2
        cap = min(Decimal(100), selected + 10)
        total = cap * Decimal("0.4") + Decimal(homework) * Decimal("0.6")
        gpa = Decimal(4 if total >= 90 else 3 if total >= 80 else 0)
        oracle[sid] = {"精选实验": str(selected.quantize(Decimal("0.1"))), "封顶平时": str(cap.quantize(Decimal("0.1"))),
                       "总评": str(total.quantize(Decimal("0.1"))), "绩点": str(gpa.quantize(Decimal("0.1")))}
    (ROOT / "advanced_expected_python.json").write_text(json.dumps(oracle, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Generated independent two-stage fixtures:", ROOT)


if __name__ == "__main__":
    main()
