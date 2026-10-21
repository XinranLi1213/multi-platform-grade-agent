"""Summarize actual completed runs. Does not generate fixtures or execute the application."""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"


def read(name):
    value = json.loads((OUT / name).read_text(encoding="utf-8-sig"))
    assert not value.get("error"), (name, value.get("error"))
    return value


backends = [read(name) for name in ("semantic_backend_acceptance.json", "two_stage_acceptance.json",
                                   "teacher_backend_acceptance.json", "interactive_acceptance.json")]
for report in backends:
    assert report["failures"] == report["errors"] == 0
browsers = [read(name) for name in ("semantic_browser_acceptance.json", "two_stage_browser.json",
                                  "checkpoint_fix_browser.json", "browser_acceptance.json")]
for report in browsers:
    assert not report.get("page_errors")
    assert all(c["status"] == "passed" for c in report["checks"])
live = read("semantic_live_smoke.json")
assert live["workflow_files_unchanged"] and live["post_requests"] == 0 and not live["page_errors"]
assert backends[2]["original_hashes_unchanged"] and browsers[1]["original_hashes_unchanged"] and browsers[2]["original_files_unchanged"]
backend_count = sum(r["tests_run"] for r in backends)
browser_count = sum(len(r["checks"]) for r in browsers)

lines = ["自然语言教师双阶段流程：实际验收", "", f"后端{backend_count}项全部通过；真实Edge浏览器{browser_count}项全部通过；另有8765正式服务只读恢复检查通过。",
         "", "本轮修改：pipeline/semantic.py（统计/项目/范围）、advisor.py（真实模型结构与指令证据）、rules.py/dws.py（项目编译/确定性调整）、service.py/app.py（状态/统一指令/试算采用），web/pipeline.html/pipeline.js/semantic.js/style.css（单入口与中文表单）。保留标准库、原生前端及DWD/DWS物理边界。",
         "", "输入与实际产物：", "",
         f"- 模拟输入：{ROOT / 'tests/semantic_data/roster.csv'}；{ROOT / 'tests/semantic_data/performance.csv'}。",
         f"- 原教师数据：{backends[2]['input_directory']}（三组原文件哈希不变）。",
         f"- 浏览器实际执行状态：{ROOT / 'work/teacher-browser-state/pipelines'}；每个流程包含input、semantic、dwd、dws及workflow.json。",
         f"- 真实按钮下载的示例：{OUT / 'semantic-browser-dws.csv'}（50/30/20试算，保留固定调整范围）；桌面40人结果见teacher-browser-complete-dws.csv。",
         "- Python单元/HTTP验收使用独立临时目录，结束后清理临时状态；逐项结果保存在本目录JSON，正式入口不导入测试生成器。",
         "", "实际命令：", "", "```powershell",
         "python -X utf8 tests/test_semantic_pipeline.py", "python -X utf8 tests/test_two_stage.py",
         "python -X utf8 tests/test_teacher_pipeline.py", "python -X utf8 tests/test_interactive.py",
         "python -u -X utf8 tests/serve_teacher_browser.py", "node tests/semantic_browser.cjs",
         "node tests/two_stage_browser.cjs", "node tests/checkpoint_browser.cjs",
         "$env:INTEGRATION_TEST_URL = 'http://127.0.0.1:8766'", "node tests/browser_acceptance.cjs",
         "python -u -X utf8 app.py --port 8765", "node tests/semantic_live_smoke.cjs", "```",
         "", "首次HTTP测试因沙箱阻止本机套接字连接而未运行成功，随后在允许本机连接的执行环境实际重跑通过。开发中发现的脚本及兼容问题均修正后重跑；以下只汇总最终通过记录。",
         "", "新增后端逐项预期与实际：", "", "| 案例 | 预期 | 实际 |", "| --- | --- | --- |"]


def cell(value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False)
    return value.replace("|", "\\|").replace("\n", " ")


for case in backends[0]["cases"]:
    lines.append("| " + " | ".join(cell(case[k]) for k in ("case", "expected", "actual")) + " |")
lines += ["", "新增真实浏览器逐项预期与实际：", "", "| 案例 | 预期 | 实际 |", "| --- | --- | --- |"]
for case in browsers[0]["checks"]:
    lines.append("| " + " | ".join(cell(case[k]) for k in ("name", "expected", "actual")) + " |")
lines += ["", "原功能回归：", "", "| 范围 | 实际 | 详细记录 |", "| --- | --- | --- |",
          f"| 原双阶段后端 | {backends[1]['tests_run']}项通过，包括生成代码/规则哈希篡改拒绝 | two_stage_acceptance.json |",
          f"| 教师三组数据后端 | {backends[2]['tests_run']}项通过，原CSV哈希不变 | teacher_backend_acceptance.json |",
          f"| 通用工作台后端 | {backends[3]['tests_run']}项通过 | interactive_acceptance.json |",
          f"| 教师浏览器 | {len(browsers[1]['checks'])}项通过，40人/卡片/时间/版本/未知故障/实际下载 | two_stage_browser.json |",
          f"| 截图场景浏览器 | {len(browsers[2]['checks'])}项通过，重复待确认/规则保存/补时间/99裁决 | checkpoint_fix_browser.json |",
          f"| 通用工作台浏览器 | {len(browsers[3]['checks'])}项通过 | browser_acceptance.json |",
          f"| 正式服务恢复 | 原流程{live['restored_workflow']}仍为第{live['revision']}版；{live['workflow_files_checked']}个流程文件哈希不变；0 POST、0页面错误 | semantic_live_smoke.json |",
          "", "预览和导出核对：实际读取表的data-raw逐格对照后端解析值；真实下载CSV由Python csv.DictReader读取后逐行对照当前rows/columns。检查覆盖0、前导零、缓考、原始值、规则与来源账本。",
          "", "模型边界及未运行检查：", "",
          "- 未调用外部真实LLM：只通过明确标注的本机fixture服务验证真实传输路径、真实数据统计、追加指令入参、草稿继承、证据校验及不重复执行。fixture只在测试脚本中，正式程序没有模拟模型回复。真实模型质量与外部连通性未验收。",
          "- 未运行大规模性能、跨设备草稿同步或教务平台发布；这些能力未实现。",
          "- 当前范围条件只支持单个数值比较或编号列表，一批调整同一基准；重叠范围保守要求顺序。项目以DWD归一后的分数为输入，不自动推断混合原分单位的累加含义。",
          "- 原读取限制与旧流程仍保留；无新增文件格式、平台接入或任意代码执行。",
          "", "启动与操作详见docs/natural_language_pipeline.md。8765为当前教师服务；8766仅为独立验收服务。"]
(OUT / "semantic_acceptance.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
(OUT / "semantic_acceptance_summary.json").write_text(json.dumps({"backend_passed": backend_count, "browser_passed": browser_count,
    "live_smoke": live, "external_real_llm_run": False, "original_teacher_inputs_unchanged": True}, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"backend_passed": backend_count, "browser_passed": browser_count, "report": str(OUT / "semantic_acceptance.md")}, ensure_ascii=False))
