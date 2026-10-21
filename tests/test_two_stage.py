"""Real HTTP, real generated Python subprocesses and persisted boundary acceptance."""
import base64
import copy
import csv
import io
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import create_server
from pipeline.advisor import validate_proposal
from pipeline.common import PipelineError, sha
from pipeline.dws import generate_and_run
from pipeline.service import PipelineStore

DATA = ROOT / "tests/two_stage_data"
REPORT = []
TASK = "实验40%，作业60%，未交记0，保留一位小数"


class TwoStage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "work").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="two-stage-", dir=ROOT / "work")
        cls.state_root = Path(cls.temp.name)
        cls.server = create_server(port=0, state_dir=cls.state_root)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def api(self, path, body=None, status=200, raw=False):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            response = urllib.request.urlopen(req, timeout=40)
        except urllib.error.HTTPError as exc:
            response = exc
        content = response.read()
        self.assertEqual(response.code, status, content.decode(errors="replace") if response.code != status else path)
        return content if raw else json.loads(content)

    def create(self, names=("roster.csv", "practice.csv", "assignment.csv"), task=TASK, rules=None):
        files = [{"name": "用户自选_" + name, "content_base64": base64.b64encode((DATA / name).read_bytes()).decode()} for name in names]
        return self.api("/api/pipelines", {"task": task, "files": files, "rules": rules}, 201)

    def resume(self, state, **kwargs):
        return self.api(f"/api/pipelines/{state['id']}/resume", {"revision": state["revision"], **kwargs})

    def trial(self, state, **kwargs):
        return self.api(f"/api/pipelines/{state['id']}/trial", {"revision": state["revision"], **kwargs})

    def record(self, case, expected, actual):
        REPORT.append({"case": case, "expected": expected, "actual": actual, "status": "passed"})

    def assert_reference(self, state):
        expected = json.loads((DATA / "expected_python.json").read_text(encoding="utf-8"))
        self.assertEqual(state["phase"], "complete", state["issues"])
        for row in state["stage2"]["rows"]:
            self.assertEqual(row["总评"], expected[row["student_id"]]["总评"])
        return expected

    def test_01_auto_continuous(self):
        state = self.create()
        expected = self.assert_reference(state)
        c = state["stage1"]["counts"]
        self.assertEqual((c["input_records"], c["collapsed_submissions"], c["auto_resolved_no_id"], c["wide_rows"]), (11, 2, 1, 3))
        self.assertEqual(state["stage1"]["rows"][0]["student_id"], "001")
        self.assertEqual(state["stage2"]["dwd_manifest_sha256"], state["stage1"]["manifest_sha256"])
        self.assertIn("-I", state["stage1"]["command"])
        self.assertTrue((Path(state["stage1"]["directory"]) / "governance.py").exists())
        self.assertNotEqual(state["stage1"]["command"][-1], state["stage2"]["command"][-1])
        actions = [e["action"] for e in state["events"]]
        self.assertLess(actions.index("dwd_persisted"), actions.index("dws_python_executed"))
        for kind, stage in (("dwd", "stage1"), ("dws", "stage2")):
            content = self.api(f"/api/pipelines/{state['id']}/download?kind={kind}", raw=True)
            self.assertEqual(list(csv.DictReader(io.StringIO(content.decode("utf-8-sig")))), state[stage]["rows"])
        self.record("01 自动串联与数值", "无异常连续两阶段；与独立Python参考一致，两个CSV与持久预览一致", {"counts": c, "reference": expected})

    def test_02_header_footer_latest_and_audit(self):
        state = self.create()
        audit = json.loads((Path(state["stage1"]["directory"]) / "audit.json").read_text(encoding="utf-8"))
        self.assertEqual(audit["layouts"]["F1"]["header_line"], 3)
        self.assertTrue(any(r["reason"] == "底部汇总/空白行" for r in audit["layouts"]["F2"]["excluded"]))
        self.assertEqual(len(audit["source_ledger"]), 11)
        self.assertEqual(state["stage1"]["rows"][0]["作业"], "90")
        self.assertTrue(any(c["raw"] == " 001\u200b " and c["value"] == "001" for c in audit["conversions"]))
        self.assertTrue(any(d["strategy"] == "latest" and len(d["observations"]) == 2 for d in audit["deduplications"]))
        self.record("02 布局/时间/零丢失", "说明表头尾汇总留痕；latest按时间；所有11条业务来源均在账本", {"header": 3, "latest": state["stage1"]["rows"][0]["作业"], "ledger": len(audit["source_ledger"])})

    def test_03_identity_checkpoint_and_resume(self):
        state = self.create(("roster.csv", "practice.csv", "ambiguous.csv"))
        self.assertEqual(state["phase"], "data_checkpoint")
        self.assertIsNone(state["stage2"])
        issue = next(i for i in state["issues"] if i["kind"] == "identity")
        self.assertEqual(len(issue["candidates"]), 2)
        root = self.state_root / "pipelines" / state["id"]
        original = {p.name: sha(p) for p in (root / "input").iterdir()}
        plan = copy.deepcopy(state["plan"])
        plan["identity_resolutions"]["F3:5"] = {"student_id": "002", "reason": "教师核对平台原始2班名册，确认编号002"}
        resumed = self.resume(state, plan=plan)
        self.assert_reference(resumed)
        self.assertEqual(original, {p.name: sha(p) for p in (root / "input").iterdir()})
        self.assertEqual(resumed["stage1"]["counts"]["teacher_identity_resolutions"], 1)
        self.record("03 身份卡点", "同名无辅助信息暂停；教师确认后原上传续算，输入哈希不变", {"before": state["phase"], "after": resumed["phase"], "candidates": len(issue["candidates"])})

    def test_04_latest_missing_time(self):
        state = self.create(("roster.csv", "practice.csv", "missing_time.csv"))
        self.assertEqual(state["phase"], "data_checkpoint")
        self.assertTrue(any("提交时间" in q["reason"] for q in state["issues"]))
        plan = copy.deepcopy(state["plan"])
        plan["cell_resolutions"]["001|作业"] = {"value": "90", "reason": "教师核查最新有效提交为90分，另一记录时间导出缺失"}
        resumed = self.resume(state, plan=plan)
        self.assert_reference(resumed)
        self.record("04 latest时间不足", "不按末行猜成绩；明确裁决后继续且保留原记录", {"before": state["phase"], "after": resumed["phase"]})

    def test_05_grades_and_special_states(self):
        state = self.create(("roster.csv", "levels.csv"), "练习等级100%，缓考保留，保留一位小数")
        self.assertEqual(state["phase"], "complete", state["issues"])
        dwd = {r["student_id"]: r for r in state["stage1"]["rows"]}
        result = {r["student_id"]: r for r in state["stage2"]["rows"]}
        self.assertEqual([dwd[s]["练习等级"] for s in ("001", "002", "003")], ["95", "0", "缓考"])
        self.assertEqual(result["003"]["总评"], "缓考")
        self.assertEqual(state["stage2"]["summary"]["preserved"], 1)
        self.record("05 等级与缓考", "按明确默认表转换，缓考两阶段都不填0", {"dwd": dwd, "summary": state["stage2"]["summary"]})

    def test_06_business_checkpoint_reuses_dwd(self):
        state = self.create(task="实验40%，作业60%，保留一位小数")
        self.assertEqual(state["phase"], "business_checkpoint")
        self.assertEqual(state["stage1"]["status"], "ready")
        self.assertEqual(state["issues"][0]["state"], "未交")
        directory = Path(state["stage1"]["directory"])
        original = {p.name: p.read_bytes() for p in directory.iterdir()}
        rules = copy.deepcopy(state["rules"])
        rules["missing"]["未交"] = {"action": "zero"}
        resumed = self.resume(state, rules=rules)
        self.assert_reference(resumed)
        self.assertEqual(resumed["stage1"]["manifest_sha256"], state["stage1"]["manifest_sha256"])
        self.assertEqual(original, {p.name: p.read_bytes() for p in directory.iterdir()})
        self.assertEqual(resumed["stage1"]["rows"][2]["作业"], "未交")
        self.assertEqual(resumed["stage2"]["rows"][2]["作业（计算输入）"], "0")
        self.record("06 缺失政策补充", "未交政策缺失暂停；补0只重算DWS，DWD字节不变", {"before": state["phase"], "after": resumed["phase"], "dwd_unchanged": True})

    def test_07_attendance_requires_choice(self):
        state = self.create(("roster.csv", "attendance.csv"), "考勤100%，保留一位小数")
        self.assertEqual(state["phase"], "data_checkpoint")
        plan = copy.deepcopy(state["plan"])
        plan["platforms"][1]["metrics"][0]["strategy"] = "sum"
        resumed = self.resume(state, plan=plan, reason="教师确认考勤每次一分累加，满分三分")
        self.assertEqual(resumed["phase"], "complete", resumed["issues"])
        self.assertEqual(resumed["stage1"]["rows"][0]["考勤"], "100")
        self.record("07 考勤歧义", "不猜sum/mean；明确sum后聚合原分再折算，避免循环小数累加误差", resumed["stage2"]["summary"])

    def test_08_left_join_orphan_not_lost(self):
        state = self.create(("roster.csv", "practice.csv", "orphan.csv"))
        self.assertEqual(state["phase"], "data_checkpoint")
        self.assertEqual(state["stage1"]["counts"]["unmatched_records"], 1)
        self.assertEqual(len(state["stage1"]["rows"]), 3)
        audit = json.loads((Path(state["stage1"]["directory"]) / "audit.json").read_text(encoding="utf-8"))
        self.assertEqual(audit["source_ledger"]["F3:7"]["disposition"], "unmatched_left_join")
        plan = copy.deepcopy(state["plan"])
        plan["excluded_records"]["F3:7"] = "教师核验编号999不属于本教学班，明确排除；仍保留原始记录"
        resumed = self.resume(state, plan=plan)
        self.assert_reference(resumed)
        self.assertEqual(resumed["stage1"]["counts"]["excluded_by_user"], 1)
        self.assertEqual(resumed["stage1"]["counts"]["source_records_audited"], 12)
        self.record("08 Left Join零丢失", "主表外999阻断并审计；教师明确排除后继续，12条来源全部在账本", resumed["stage1"]["counts"])

    def test_09_complex_generated_calculation(self):
        rules = json.loads((DATA / "advanced_rules.json").read_text(encoding="utf-8"))
        state = self.create(("roster.csv", "many_metrics.csv"), "按明确公式执行实验3选2、封顶与绩点", rules)
        self.assertEqual(state["phase"], "complete", state["issues"])
        expected = json.loads((DATA / "advanced_expected_python.json").read_text(encoding="utf-8"))
        for row in state["stage2"]["rows"]:
            for key, value in expected[row["student_id"]].items():
                self.assertEqual(row[key], value)
        code = (Path(state["stage2"]["directory"]) / "calculation.py").read_text(encoding="utf-8")
        self.assertIn("FORMULAS", code)
        self.assertIn("best(2", code)
        self.assertIn("-I", state["stage2"]["command"])
        self.record("09 复杂公式", "N选M/封顶/条件绩点由实际生成Python执行，等于独立参考", {"expected_python": expected, "command": state["stage2"]["command"]})

    def test_10_trial_freeze_and_compare(self):
        rules = json.loads((DATA / "rules.json").read_text(encoding="utf-8"))
        state = self.create(rules=rules)
        before = {p.name: p.read_bytes() for p in Path(state["stage1"]["directory"]).iterdir()}
        changed = copy.deepcopy(rules)
        changed["weights"] = {"实验": "0", "作业": "1"}
        trial = self.trial(state, rules=changed, instruction="明确实验0%、作业100%，优秀>=90，分母为数值总评人数")
        self.assertEqual(trial["phase"], "complete", trial["issues"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in Path(trial["stage1"]["directory"]).iterdir()})
        self.assertNotEqual(trial["stage2"]["directory"], state["stage2"]["directory"])
        self.assertEqual(trial["stage2"]["comparison"]["changed_students"], 3)
        delta = Decimal(trial["stage2"]["summary"]["excellent_rate_percent"]) - Decimal(state["stage2"]["summary"]["excellent_rate_percent"])
        self.assertEqual(Decimal(trial["stage2"]["comparison"]["excellent_rate_change_percentage_points"]), delta)
        self.record("10 冻结宽表试算", "另存新试算、逐人差异与优秀率比较，DWD所有文件逐字节不变", trial["stage2"]["comparison"])

    def test_11_ambiguous_adjustment_and_unbalanced_weights(self):
        state = self.create(rules=json.loads((DATA / "rules.json").read_text(encoding="utf-8")))
        pending = self.trial(state, instruction="把实验权重提高10%，看看优秀率变化")
        self.assertEqual(pending["phase"], "business_checkpoint")
        self.assertIsNone(pending["stage2"])
        self.assertIn("百分点", pending["issues"][0]["reason"])
        self.assertEqual(len(pending["trials"]), 1)
        self.api(f"/api/pipelines/{state['id']}/download?kind=dws", status=409)
        restored = self.trial(pending, instruction="实验50%，作业50%")
        self.assertEqual(restored["phase"], "complete", restored["issues"])
        invalid = self.trial(restored, rules={"weights": {"实验": ".4", "作业": ".5"}})
        self.assertEqual(invalid["phase"], "business_checkpoint")
        self.assertIn("合计100%", invalid["issues"][0]["reason"])
        self.record("11 反悔边界", "歧义提高10%先问；完整50/50可重算；40/50不自行归一", {"ambiguous": pending["issues"], "invalid_sum": invalid["issues"]})

    def test_12_restricted_code_and_bad_math(self):
        state = self.create()
        for expression in ["__import__('os').getcwd()", "open('secret')", "field('实验').__class__", "[x for x in [1]]", "field('虚构项目')"]:
            invalid = self.trial(state, rules={"formulas": [{"name": "总评", "expr": expression}]})
            self.assertEqual(invalid["phase"], "business_checkpoint")
            self.assertIsNone(invalid["stage2"])
            state = invalid
        failed = self.trial(state, rules={"formulas": [{"name": "总评", "expr": 'field("实验")/0'}]})
        self.assertEqual(failed["phase"], "business_checkpoint")
        self.assertEqual(failed["stage2"]["summary"]["failed"], 3)
        self.assertTrue(all(r["总评"] == "" for r in failed["stage2"]["rows"]))
        self.record("12 代码与运算条件", "拒绝任意Python/未知列；除0明确待处理且不输出假分", {"blocked_expressions": 5, "runtime_failures": failed["stage2"]["failures"]})

    def test_13_manifest_integrity(self):
        blocked = self.create(("roster.csv", "practice.csv", "ambiguous.csv"))
        with self.assertRaises(PipelineError):
            generate_and_run(Path(blocked["stage1"]["directory"]) / "manifest.json", blocked["stage1"]["manifest_sha256"],
                             {"weights": {"实验": "1"}}, self.state_root / "illegal-stage2")
        state = self.create()
        wide = Path(state["stage1"]["directory"]) / "wide.csv"
        wide.write_bytes(wide.read_bytes() + b"\n")
        tampered = self.trial(state, instruction="实验50%，作业50%")
        self.assertEqual(tampered["phase"], "system_error")
        self.assertIn("哈希", tampered["diagnostic"]["detail"])
        self.record("13 持久边界完整性", "blocked或被改动的DWD均不能进入DWS", tampered["issues"])

    def test_14_reading_and_unknown_scale(self):
        state = self.create(("roster.csv", "bad.csv"), "实验100%")
        self.assertEqual(state["phase"], "data_checkpoint")
        self.assertTrue(any("列数" in q["reason"] for q in state["issues"]))
        plan = copy.deepcopy(self.create()["plan"])
        plan["platforms"][1]["metrics"][0]["scale"] = {"kind": "unknown"}
        normal = self.create()
        unclear = self.resume(normal, plan=plan)
        self.assertEqual(unclear["phase"], "data_checkpoint")
        self.assertTrue(any("分制不明确" in q["reason"] for q in unclear["issues"]))
        self.record("14 读取与分制", "坏行或未知分制不会假装数据干净", {"bad_rows": state["issues"], "scale_issues": len(unclear["issues"])})

    def test_15_persistence_versions_and_model(self):
        state = self.create()
        self.assertEqual(PipelineStore(self.state_root).load(state["id"]), state)
        updated = self.trial(state, instruction="实验50%，作业50%")
        self.api(f"/api/pipelines/{state['id']}/resume", {"revision": state["revision"]}, status=409)
        suggestion = self.api(f"/api/pipelines/{state['id']}/suggest", {"revision": updated["revision"]})
        self.assertEqual(suggestion["suggestion"]["status"], "disabled")
        invented = copy.deepcopy(state["plan"])
        invented["identity_resolutions"] = {"F3:5": {"student_id": "001", "reason": "模型声称已确认"}}
        with self.assertRaises(PipelineError):
            validate_proposal({"stage1_plan": invented}, state)
        self.record("15 恢复/版本/模型", "磁盘恢复相同、旧revision409、模型禁用明确、模型不能冒充身份确认", "全部通过；未调用真实LLM")

    def test_16_cross_platform_conflict(self):
        state = self.create(("roster.csv", "practice.csv", "lab_conflict.csv"), "实验100%")
        self.assertEqual(state["phase"], "data_checkpoint")
        self.assertTrue(state["stage1"]["rows"][0]["实验"].startswith("冲突:"))
        self.assertTrue(any("跨平台" in q["reason"] for q in state["issues"]))
        audit = json.loads((Path(state["stage1"]["directory"]) / "audit.json").read_text(encoding="utf-8"))
        self.assertEqual({o["source"]["file_id"] for o in audit["cell_sources"]["001"]["实验"]}, {"F2", "F3"})
        plan = copy.deepcopy(state["plan"])
        plan["cell_resolutions"]["001|实验"] = {"value": "90", "reason": "教师核对两平台同一实验的重修替代规则，明确采用90；双方记录保留"}
        result = self.resume(state, plan=plan)
        self.assertEqual(result["phase"], "complete", result["issues"])
        self.assertEqual(result["stage1"]["rows"][0]["实验"], "90")
        self.record("16 跨平台值冲突", "草稿明确显示冲突而非取一侧；保留双方来源，明确裁决后继续", {"before": state["phase"], "after": result["phase"], "both_sources_audited": True})

    def test_17_unrecognized_instruction_is_not_success(self):
        state = self.create()
        updated = self.trial(state, instruction="帮我把公式调整得合理一点")
        self.assertEqual(updated["phase"], "business_checkpoint")
        self.assertIsNone(updated["stage2"])
        self.assertIn("不能用旧规则", updated["issues"][0]["reason"])
        from pipeline.ingest import parse_file, suggest_plan
        raw = b'ID,score\n001,50\n'
        f = parse_file("F1", "custom.csv", '学号,姓名,实验成绩\n001,甲,50\n'.encode())
        plan = suggest_plan([f], "请将成绩换算成百分制")
        self.assertEqual(plan["platforms"][0]["metrics"][0]["scale"]["kind"], "unknown")
        self.record("17 不伪装理解指令", "未知追加指令不得静默沿用旧公式；目标百分制不等于声明原始百分制", {"trial": updated["phase"], "source_scale": "unknown"})

    def test_18_output_and_rule_binding(self):
        state = self.create()
        directory = Path(state["stage2"]["directory"])
        (directory / "output.csv").write_bytes(b"changed\n")
        self.api(f"/api/pipelines/{state['id']}/download?kind=dws", status=409)
        import subprocess
        rules = json.loads((directory / "rules.json").read_text(encoding="utf-8"))
        rules["missing"]["未交"] = {"action": "preserve"}
        (directory / "rules.json").write_text(json.dumps(rules), encoding="utf-8")
        rerun = subprocess.run(state["stage2"]["command"], capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(rerun.returncode, 0)
        self.assertIn("业务规则文件", rerun.stderr)
        self.record("18 输出/公式绑定", "产物被改不能当原预览下载；生成代码绑定规则哈希，改政策后重跑被拒", {"download": 409, "rerun_rejected": True})


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(TwoStage))
    out = ROOT / "outputs"
    out.mkdir(exist_ok=True)
    payload = {"tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "cases": REPORT,
        "not_run": ["真实LLM语义生成与质量（未配置，未用模拟回复替代）", "真实教学平台与生产并发/大规模性能", "CSV之外表格格式与OCR"]}
    (out / "two_stage_acceptance.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["双阶段执行：实际验收报告", "", f"运行：{result.testsRun}，失败：{len(result.failures)}，错误：{len(result.errors)}。", "",
        "实际命令：python -X utf8 tests/generate_two_stage.py；python -X utf8 tests/test_two_stage.py。", "",
        "参考答案由独立Python Decimal程序生成。每次DWS均真实启动生成的Python子进程，非模拟数学结果。", "",
        "| 案例 | 预期 | 实际 |", "| --- | --- | --- |"]
    for c in REPORT:
        actual = c["actual"] if isinstance(c["actual"], str) else json.dumps(c["actual"], ensure_ascii=False)
        lines.append("| " + " | ".join(str(v).replace("|", "\\|").replace("\n", " ") for v in (c["case"], c["expected"], actual)) + " |")
    lines += ["", "未运行："] + ["- " + text for text in payload["not_run"]]
    (out / "two_stage_acceptance.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.exit(0 if result.wasSuccessful() else 1)
