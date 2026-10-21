"""Execute the real CLI with fixtures and verify persisted CSV/JSON, not mock results."""

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
RUNS_ROOT = ROOT / "runs"
CHECKS, COMMANDS = [], []


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(name, rules="rules.json", a="platform_a.csv", b="platform_b.csv", previous=None,
        expected_exit=0, output_name=None):
    output = RUNS_ROOT / (output_name or name)
    args = [sys.executable, "-X", "utf8", "grade_agent.py", "--platform-a", f"inputs/{a}",
            "--platform-b", f"inputs/{b}", "--rules", f"inputs/{rules}",
            "--output-dir", str(output)]
    if previous:
        args += ["--previous-audit", str(RUNS_ROOT / previous / "audit.json")]
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    COMMANDS.append({"name": name, "cwd": str(ROOT), "argv": args,
                     "command": subprocess.list2cmdline(args), "exit_code": result.returncode,
                     "stdout": result.stdout, "stderr": result.stderr})
    if result.returncode != expected_exit:
        raise RuntimeError(f"{name}: expected exit {expected_exit}, got {result.returncode}: {result.stderr}")
    if expected_exit != 0:
        return {"output": output, "error": result.stderr, "exit": result.returncode}
    with (output / "summary.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {"output": output, "rows": {row["学号"]: row for row in rows}, "raw_rows": rows,
            "audit": json.loads((output / "audit.json").read_text(encoding="utf-8"))}


def record(identifier, label, input_text, expected, actual, passed):
    CHECKS.append({"编号": identifier, "检查": label, "输入": input_text,
                   "预期": expected, "实际": actual, "结果": "通过" if passed else "失败"})


def compact(row):
    return ", ".join(f"{key}={row[key]!r}" for key in ("学号", "实验A成绩", "实验B成绩", "总分", "处理状态", "原因"))


def execute_checks():
    base = run("baseline")
    rows = base["rows"]
    row = rows["1001"]
    record("T01", "完整规则计算", "A=80, B=90, rules.json", "86.0 / 已计算 / 原值80和90",
           compact(row), row["总分"] == "86.0" and row["处理状态"] == "已计算"
           and row["实验A成绩"] == "80" and row["实验B成绩"] == "90" and row["原因"] == "")
    row = rows["1002"]
    record("T02", "零分有效", "A=0, B=90", "54.0 / 已计算 / A原值0", compact(row),
           row["总分"] == "54.0" and row["实验A成绩"] == "0" and row["处理状态"] == "已计算")
    row = rows["1003"]
    record("T03", "空值不补零", "A=80, B为空单元格", "A=80, B和总分为空 / 待处理 / 缺少实验 B 成绩",
           compact(row), row["实验A成绩"] == "80" and row["实验B成绩"] == ""
           and row["总分"] == "" and row["处理状态"] == "待处理" and row["原因"] == "缺少实验 B 成绩")
    record("T04", "按学号关联及每人一行", "平台A顺序1003/1001/1002；B顺序1002/1003/1001",
           "并集恰为1001/1002/1003且无重复，1001/1002/1003分别满足T01/T02/T03",
           str([row["学号"] for row in base["raw_rows"]]), len(base["raw_rows"]) == 3
           and set(rows) == {"1001", "1002", "1003"}
           and rows["1001"]["总分"] == "86.0" and rows["1002"]["总分"] == "54.0" and rows["1003"]["总分"] == "")

    student = next(item for item in base["audit"]["students"] if item["student_id"] == "1001")
    evidence = base["audit"]["rules"]["rules"][0]["evidence"]
    provenance_ok = (student["platform_a"][0]["line"] == 3 and student["platform_b"][0]["line"] == 4
        and evidence["line"] == 1 and "40%" in evidence["text"]
        and evidence["sha256"] == digest(ROOT / "inputs/course_materials.txt")
        and base["audit"]["inputs"]["platform_a"]["sha256"] == digest(ROOT / "inputs/platform_a.csv")
        and "platform_a.csv:3" in rows["1001"]["实验A来源"]
        and "platform_b.csv:4" in rows["1001"]["实验B来源"]
        and "course_materials.txt:1" in rows["1001"]["规则依据"])
    record("T05", "成绩与规则可追溯", "1001的两条原始记录和R40_60", "A第3行、B第4行、课程材料第1行；文件哈希一致",
           f"A行{student['platform_a'][0]['line']}, B行{student['platform_b'][0]['line']}, 规则行{evidence['line']}, 哈希核对={provenance_ok}", provenance_ok)

    missing = run("missing_rules", "rules_missing.json")
    row = missing["rows"]["1001"]
    record("T06", "缺权重挂起", "rules_missing.json没有weight_a和weight_b", "总分空 / 待处理 / 明确列出两个缺失权重",
           compact(row), row["总分"] == "" and row["处理状态"] == "待处理"
           and "weight_a" in row["原因"] and "weight_b" in row["原因"])
    supplemented = run("supplemented", previous="missing_rules")
    record("T07", "补规则后续算", "同一CSV，rules_missing.json补为rules.json，关联上次audit", "1001=86.0；保留前次审计哈希与规则版本",
           f"总分={supplemented['rows']['1001']['总分']}, 变更人数={len(supplemented['audit']['changes'])}",
           supplemented["rows"]["1001"]["总分"] == "86.0"
           and supplemented["audit"]["previous_run"]["sha256"] == digest(missing["output"] / "audit.json")
           and missing["audit"]["rules"]["version"] == "v0-missing-weights"
           and supplemented["audit"]["rules"]["version"] == "v1-explicit-teacher-rule")

    conflict = run("conflict", "rules_conflict.json")
    row = conflict["rows"]["1001"]
    record("T08", "冲突不自行选择", "R40_60与R50_50均声明适用", "总分空，列出两条冲突规则及材料第1/2行，未选择规则",
           compact(row), row["总分"] == "" and row["处理状态"] == "待处理"
           and all(token in row["原因"] for token in ("R40_60", "R50_50", "course_materials.txt:1", "course_materials.txt:2"))
           and conflict["audit"]["rules"]["selected_rule_id"] is None)
    resolved = run("resolved", "rules_resolved.json", previous="conflict")
    audit = resolved["audit"]
    record("T09", "教师明确规则后续算", "模拟教师确认采用R40_60，保留两份候选规则及确认出处", "1001=86.0，1002=54.0；选择R40_60；保留上次审计和确认",
           f"1001={resolved['rows']['1001']['总分']}, 1002={resolved['rows']['1002']['总分']}, 规则={audit['rules']['selected_rule_id']}",
           resolved["rows"]["1001"]["总分"] == "86.0" and resolved["rows"]["1002"]["总分"] == "54.0"
           and audit["rules"]["selected_rule_id"] == "R40_60" and len(audit["rules"]["rules"]) == 2
           and audit["rules"]["resolution"]["evidence"]["line"] == 1
           and audit["previous_run"]["sha256"] == digest(conflict["output"] / "audit.json")
           and len(audit["changes"]) == 3)
    row = resolved["rows"]["1003"]
    record("T10", "补规则不补成绩", "1003仍然缺B，但规则冲突已解除", "总分仍空，仅剩缺少实验 B 成绩", compact(row),
           row["总分"] == "" and row["处理状态"] == "待处理" and row["原因"] == "缺少实验 B 成绩")

    repeat = run("repeat")
    equal = all((base["output"] / name).read_bytes() == (repeat["output"] / name).read_bytes()
                for name in ("summary.csv", "audit.json"))
    record("T11", "重复运行一致", "相同CSV、规则，输出到两个不同目录", "summary.csv和audit.json均逐字节一致",
           f"逐字节一致={equal}", equal)
    duplicate = run("duplicate", a="duplicate_a.csv", b="single_b.csv")
    row = duplicate["rows"]["1001"]
    record("T12", "重复记录防误选", "同一学号A=80和85，B=90（超出唯一性假设的防御检查）", "保留两个A值及来源；总分空并标明重复/冲突",
           compact(row), json.loads(row["实验A成绩"]) == ["80", "85"] and row["总分"] == ""
           and "冲突" in row["原因"] and len(duplicate["audit"]["students"][0]["platform_a"]) == 2)

    edge = run("edge", a="edge_a.csv", b="edge_b.csv")
    expected_ids = {"0007", "1004", "1005", "1006", "1007", "1008", "1009", "1010", "1011"}
    record("T13", "单边记录保留", "1005仅B有记录，1006仅A有记录", "并集9人且每人一行；分别缺A、缺B；总分均空",
           f"人数={len(edge['raw_rows'])}; 1005原因={edge['rows']['1005']['原因']}; 1006原因={edge['rows']['1006']['原因']}",
           len(edge["raw_rows"]) == 9 and set(edge["rows"]) == expected_ids
           and "缺少实验 A" in edge["rows"]["1005"]["原因"] and "缺少实验 B" in edge["rows"]["1006"]["原因"]
           and edge["rows"]["1005"]["总分"] == edge["rows"]["1006"]["总分"] == "")
    row = edge["rows"]["0007"]
    record("T14", "学号按字符串保留", "学号0007，A=12.5，B=87.5", "学号仍0007，总分57.5", compact(row),
           row["学号"] == "0007" and row["总分"] == "57.5")
    row = edge["rows"]["1004"]
    record("T15", "纯空格成绩视为缺失", "B为三个空格", "原始三个空格仍保留；总分空，说明缺B", compact(row),
           row["实验B成绩"] == "   " and row["总分"] == "" and row["原因"] == "缺少实验 B 成绩")
    invalid_ok = all(edge["rows"][student_id]["总分"] == "" and "有效有限数值" in edge["rows"][student_id]["原因"]
                     for student_id in ("1007", "1008", "1009"))
    record("T16", "异常数值不参与计算", "A=NaN、A=待补、B=Infinity", "三个学生均待处理，保留原值并说明不是有效有限数值",
           "；".join(compact(edge["rows"][student_id]) for student_id in ("1007", "1008", "1009")), invalid_ok)
    tie = next(item for item in edge["audit"]["students"] if item["student_id"] == "1011")
    record("T17", "十进制精度及最终舍入", "1010:80.1/90.1；1011:80.05/90.05", "1010=86.1；1011精确86.05、四舍五入86.1",
           f"1010={edge['rows']['1010']['总分']}; 1011精确={tie['exact_total']}, 输出={tie['total']}",
           edge["rows"]["1010"]["总分"] == "86.1" and tie["exact_total"] == "86.050" and tie["total"] == "86.1")

    for identifier, name, rules_name, reason in [
        ("T18", "无教师依据的选规则无效", "rules_unconfirmed.json", "缺少教师明确说明及出处"),
        ("T19", "适用性不明时挂起", "rules_unknown_applicability.json", "适用性不明确"),
        ("T20", "规则无来源时挂起", "rules_no_source.json", "缺少有效出处")]:
        result = run(identifier.lower(), rules_name)
        row = result["rows"]["1001"]
        record(identifier, name, rules_name, f"总分空 / 待处理 / {reason}", compact(row),
               row["总分"] == "" and row["处理状态"] == "待处理" and reason in row["原因"])
    malformed = run("bad_header", a="bad_header_a.csv", expected_exit=2)
    record("T21", "表头错误明确失败", "平台A成绩列被命名为成绩", "退出码2，解释表头要求，不生成summary.csv",
           malformed["error"].strip(), malformed["exit"] == 2 and "表头" in malformed["error"]
           and not (malformed["output"] / "summary.csv").exists())
    changed = run("changed_inputs", a="edge_a.csv", b="edge_b.csv", previous="baseline", expected_exit=2)
    record("T22", "续算核对同一批数据", "对baseline审计使用另一个CSV批次续算", "退出码2，说明输入已变，不生成summary.csv",
           changed["error"].strip(), "与上次不一致" in changed["error"] and not (changed["output"] / "summary.csv").exists())
    before = digest(conflict["output"] / "audit.json")
    protected = run("protect_previous", "rules_resolved.json", previous="conflict", expected_exit=2, output_name="conflict")
    record("T23", "续算保留历史审计", "将续算结果写回previous-audit所在目录", "退出码2，旧audit.json哈希不变",
           protected["error"].strip(), "新输出目录" in protected["error"] and before == digest(conflict["output"] / "audit.json"))
    blank_line = run("blank_line", a="blank_line_a.csv", b="single_b.csv")
    source = blank_line["audit"]["students"][0]["platform_a"][0]
    record("T24", "空白行后的物理来源行号", "A文件第1行表头、第2行空白、第3行1001=80", "1001总分86.0；来源为物理第3行",
           f"总分={blank_line['rows']['1001']['总分']}, A来源行={source['line']}",
           blank_line["rows"]["1001"]["总分"] == "86.0" and source["line"] == 3)


def save_reports(fatal=None):
    REPORTS.mkdir(parents=True, exist_ok=True)
    if fatal:
        record("ERROR", "验收执行异常", "详见命令日志", "全部检查完成", fatal, False)
    fields = ["编号", "检查", "输入", "预期", "实际", "结果"]
    with (REPORTS / "acceptance_results.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(CHECKS)
    not_run = ["真实平台文件及真实课程材料验收（本轮全部为模拟数据）",
               "自然语言/PDF/OCR规则自动提取（本版使用明确的结构化规则JSON）",
               "学号格式、课程、学期的跨平台真实性校验（本轮沿用一致性假设）",
               "教师身份认证与真实审批（验收使用明确标注的模拟教师确认记录）",
               "平台API连接、LLM调用、图形界面及大规模性能检查（本轮离线CLI范围）"]
    payload = {"checks": CHECKS, "passed": sum(item["结果"] == "通过" for item in CHECKS),
               "failed": sum(item["结果"] == "失败" for item in CHECKS), "not_run": not_run,
               "commands": COMMANDS}
    (REPORTS / "execution_log.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["实际运行验收报告", "", f"运行环境：Python {sys.version.split()[0]}；执行器 {sys.executable}",
             f"工作目录：{ROOT}", f"验收输出根目录：{RUNS_ROOT}（下文的runs/代表此根目录）", f"结果：{payload['passed']} 通过，{payload['failed']} 失败。", "",
             "输入位置：inputs/platform_a.csv、inputs/platform_b.csv；明确规则 inputs/rules.json；原文依据 inputs/course_materials.txt。",
             "正常输出：runs/baseline/summary.csv 和 runs/baseline/audit.json。",
             "冲突输出：runs/conflict/；教师明确后续算输出：runs/resolved/；缺规则及补充后输出：runs/missing_rules/、runs/supplemented/。", "",
             "表格最后一行没有输入，按前文解释为规则冲突，并用额外模拟材料及模拟教师确认验收。",
             "40%/60%来自本轮要求；一位小数、ROUND_HALF_UP沿用上轮测试约定，在规则JSON中显式配置，不在计算函数中猜测。", "",
             "| 编号 | 检查 | 预期 | 实际 | 结果 |", "| --- | --- | --- | --- | --- |"]
    for item in CHECKS:
        values = [item[key].replace("|", "\\|").replace("\n", " ") for key in ("编号", "检查", "预期", "实际", "结果")]
        lines.append("| " + " | ".join(values) + " |")
    lines += ["", "已执行的完整命令（工作目录同上；退出码2为预期的输入拒绝检查）：", "", "```powershell"]
    for command in COMMANDS:
        lines.append(command["command"] + f"  # exit={command['exit_code']}")
    lines += ["```", "", "未运行的检查：", ""] + [f"- {item}" for item in not_run]
    lines += ["", "实现定位：grade_agent.py 的 read_platform 读取CSV；associate_students 按字符串学号做全外关联；",
              "check_score 检查空单元格/空格/无记录、重复记录与非数字；resolve_rules 检查计分依据与教师冲突说明；",
              "calculate_total 用Decimal执行加权求和和最终舍入；integrate 串联流程、输出CSV/审计、对照上次审计记录变更。", ""]
    (REPORTS / "acceptance_report.md").write_text("\n".join(lines), encoding="utf-8")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="读取已有模拟数据并执行验收；不自动生成数据")
    parser.add_argument("--output-root", type=Path, default=RUNS_ROOT, help="验收结果根目录，默认项目runs目录")
    RUNS_ROOT = parser.parse_args().output_root.resolve()
    failure = None
    try:
        execute_checks()
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
    report = save_reports(failure)
    print(json.dumps({"passed": report["passed"], "failed": report["failed"], "reports": str(REPORTS)}))
    sys.exit(1 if report["failed"] else 0)
