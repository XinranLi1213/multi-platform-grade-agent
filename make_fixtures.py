"""Create explicitly simulated platform exports and teacher rule records."""

import copy
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INPUTS = ROOT / "inputs"


def write_csv(name, headers, rows):
    with (INPUTS / name).open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def write_json(name, value):
    (INPUTS / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    INPUTS.mkdir(parents=True, exist_ok=True)
    write_csv("platform_a.csv", ["学号", "实验A成绩"], [("1003", "80"), ("1001", "80"), ("1002", "0")])
    write_csv("platform_b.csv", ["学号", "实验B成绩"], [("1002", "90"), ("1003", ""), ("1001", "90")])
    write_csv("duplicate_a.csv", ["学号", "实验A成绩"], [("1001", "80"), ("1001", "85")])
    write_csv("single_b.csv", ["学号", "实验B成绩"], [("1001", "90")])
    write_csv("edge_a.csv", ["学号", "实验A成绩"], [
        ("0007", "12.5"), ("1004", "80"), ("1006", "50"), ("1007", "NaN"),
        ("1008", "待补"), ("1009", "80"), ("1010", "80.1"), ("1011", "80.05")])
    write_csv("edge_b.csv", ["学号", "实验B成绩"], [
        ("1011", "90.05"), ("1010", "90.1"), ("1009", "Infinity"), ("1008", "90"),
        ("1007", "90"), ("1005", "60"), ("1004", "   "), ("0007", "87.5")])
    write_csv("bad_header_a.csv", ["学号", "成绩"], [("1001", "80")])
    write_csv("blank_line_a.csv", ["学号", "实验A成绩"], [[], ["1001", "80"]])
    (INPUTS / "course_materials.txt").write_text(
        "正常规则：本轮教师明确总分＝实验A成绩×40%＋实验B成绩×60%；课程和学期一致、规则适用。沿用前轮测试约定：总分四舍五入（ROUND_HALF_UP）保留一位小数。\n"
        "仅用于冲突验收的模拟材料：同一课程学期总分＝实验A成绩×50%＋实验B成绩×50%；四舍五入保留一位小数。此行不属于正常批次规则。\n"
        "仅用于缺依据验收的模拟材料：需要计算两项实验总分，但未提供权重。\n",
        encoding="utf-8")
    (INPUTS / "teacher_confirmation.txt").write_text(
        "模拟教师补充确认（仅用于验收）：冲突批次仅采用 R40_60，R50_50 不适用于本批次，按 40%/60% 规则续算。\n",
        encoding="utf-8")
    normal_rule = {"id": "R40_60", "applicable": True, "method": "weighted_sum",
                   "weight_a": "0.4", "weight_b": "0.6", "decimal_places": 1,
                   "rounding": "ROUND_HALF_UP", "source": {"file": "course_materials.txt", "line": 1}}
    normal = {"version": "v1-explicit-teacher-rule", "rules": [normal_rule]}
    write_json("rules.json", normal)
    missing = copy.deepcopy(normal)
    missing["version"] = "v0-missing-weights"
    for key in ("weight_a", "weight_b"):
        del missing["rules"][0][key]
    missing["rules"][0]["source"]["line"] = 3
    write_json("rules_missing.json", missing)
    conflict = copy.deepcopy(normal)
    conflict["version"] = "v-conflict"
    other = copy.deepcopy(normal_rule)
    other.update(id="R50_50", weight_a="0.5", weight_b="0.5")
    other["source"]["line"] = 2
    conflict["rules"].append(other)
    write_json("rules_conflict.json", conflict)
    resolved = copy.deepcopy(conflict)
    resolved["version"] = "v2-teacher-resolved"
    resolved["resolution"] = {"selected_rule_id": "R40_60",
        "teacher_confirmation": "模拟教师明确本批次采用 R40_60，排除 R50_50",
        "source": {"file": "teacher_confirmation.txt", "line": 1}}
    write_json("rules_resolved.json", resolved)
    unconfirmed = copy.deepcopy(conflict)
    unconfirmed["resolution"] = {"selected_rule_id": "R40_60"}
    write_json("rules_unconfirmed.json", unconfirmed)
    unknown = copy.deepcopy(normal)
    del unknown["rules"][0]["applicable"]
    write_json("rules_unknown_applicability.json", unknown)
    no_source = copy.deepcopy(normal)
    del no_source["rules"][0]["source"]
    write_json("rules_no_source.json", no_source)
    print(f"模拟输入已生成：{INPUTS}")


if __name__ == "__main__":
    main()
