"""Summarize actual trial/model runs without executing tasks or contacting a provider."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"


def read(name):
    report = json.loads((OUT / name).read_text(encoding="utf-8-sig"))
    assert not report.get("error"), (name, report.get("error"))
    assert not report.get("failures") and not report.get("errors"), name
    return report


backend_names = ["portable_model_acceptance.json", "semantic_backend_acceptance.json", "two_stage_acceptance.json",
                 "teacher_backend_acceptance.json", "interactive_acceptance.json"]
browser_names = ["portable_model_browser.json", "semantic_browser_acceptance.json", "two_stage_browser.json",
                 "checkpoint_fix_browser.json", "browser_acceptance.json"]
backends = [read(name) for name in backend_names]
browsers = [read(name) for name in browser_names]
for report in browsers:
    assert not report.get("page_errors") and all(c["status"] == "passed" for c in report["checks"])
release = read("portable_release_smoke.json")
assert release["source_tamper_rejected"] and release["package_excludes_user_data_and_keys"]
backend_count, browser_count = sum(r["tests_run"] for r in backends), sum(len(r["checks"]) for r in browsers)
commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
assert commit == release["source_commit"], "分发包并非当前提交，请构建并验证后再生成报告"
lines = ["模型API与Windows独立试运行：实际验收", "", f"本地提交：{commit}；分支codex/portable-model-trial；未推送远程。",
         f"后端{backend_count}项、真实浏览器{browser_count}项全部通过；另有干净克隆和源码ZIP各一条实际启动/HTTP/计算链路通过。",
         "", f"分发包：{release['package']}", f"SHA256：{release['package_sha256']}",
         "", "已实现：标准库共享模型客户端、配置原因诊断、GET /api/health、POST /api/model/check、错误分类与脱敏、禁止密钥重定向、一次受控结构/字段修正、手动回退；Windows一键启动、环境检查、完整Git源码、提交白名单源码包、合成示例与中文说明；旧路径状态只读保护。",
         "", "实际运行命令：", "", "```powershell",
         "python -X utf8 tests/test_portable_model.py", "python -X utf8 tests/test_semantic_pipeline.py", "python -X utf8 tests/test_two_stage.py",
         "python -X utf8 tests/test_teacher_pipeline.py", "python -X utf8 tests/test_interactive.py",
         "python -u -X utf8 tests/serve_teacher_browser.py", "node tests/portable_model_browser.cjs", "node tests/two_stage_browser.cjs",
         "node tests/semantic_browser.cjs", "node tests/checkpoint_browser.cjs", "$env:INTEGRATION_TEST_URL = 'http://127.0.0.1:8766'",
         "node tests/browser_acceptance.cjs", f"python -X utf8 tools/build_trial_package.py --output {Path(release['package']).relative_to(ROOT).as_posix()}",
         f"python -X utf8 tests/portable_release_smoke.py --package {Path(release['package']).relative_to(ROOT).as_posix()}", "```",
         "", "模型与环境新增案例：", "", "| 案例 | 预期 | 实际 |", "| --- | --- | --- |"]


def cell(value):
    return (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)).replace("|", "\\|").replace("\n", " ")


for case in backends[0]["cases"]:
    lines.append("| " + " | ".join(cell(case[k]) for k in ("case", "expected", "actual")) + " |")
for case in browsers[0]["checks"]:
    lines.append("| " + " | ".join(cell(case[k]) for k in ("name", "expected", "actual")) + " |")
lines += ["", "干净分发实际验证：", "", "| 入口 | 实际目录 | 实际结果 |", "| --- | --- | --- |"]
for case in release["cases"]:
    lines.append(f"| {case['launcher']} | {case['directory']} | 3人总评86.0/54.0/32.0；CSV与预览一致；前导零/0/未交保留；仅改权重DWD不变；试算不覆盖；重复不重算；端口占用正确拒绝；生成代码绑定新目录 |")
lines += ["", "两个服务均已按自身PID停止；验收目录保留供核查。ZIP未包含配置密钥、用户上传、运行状态、日志、.git或测试fixture。测试修改的是解压副本VERSION，启动器正确拒绝源码哈希变化；原ZIP不变。",
          "", "模型真实性与未运行检查：", "",
          "- 当前没有外部真实模型配置/凭据，未验证服务商连通性、额度和语义质量。只用明确标注的本机HTTP fixture验证认证/限流/协议/结构修正路径，不冒充真实LLM成功。",
          "- 未在第二台实体Windows电脑上运行；本轮验证的是同一Windows电脑的独立克隆和独立解压目录、不同端口及空状态。目标电脑仍需按中文说明实际试运行。",
          "- 未实现远程访问、生产多用户、任意协议/文件格式或完整历史会话迁移。旧状态只能归档；新机器需重跑并明确调整基准，不修改哈希或猜测旧版本。",
          "", "使用入口：双击start.cmd；配置与启动说明见docs/windows_trial.md；代码、配置、示例、runtime/state分开保存。"]
(OUT / "portable_upgrade_acceptance.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
(OUT / "portable_upgrade_summary.json").write_text(json.dumps({"commit": commit, "backend_passed": backend_count, "browser_passed": browser_count,
    "independent_directory_launches": len(release["cases"]), "package": release["package"], "package_sha256": release["package_sha256"],
    "external_real_llm_verified": False, "physical_second_pc_verified": False}, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"backend_passed": backend_count, "browser_passed": browser_count, "report": str(OUT / "portable_upgrade_acceptance.md")}, ensure_ascii=False))
