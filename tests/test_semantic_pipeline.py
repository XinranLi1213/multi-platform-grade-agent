"""Real deterministic runners; optional model transport uses an explicitly labelled fixture server."""
import copy
import csv
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.advisor import validate_proposal
from pipeline.common import PipelineError, sha
from pipeline.rules import compile_rules
from pipeline.service import PipelineStore, RevisionError

DATA = ROOT / "tests/semantic_data"
REPORT = []
TASK = "以编号关联，原始数据百分制；课堂展示为展示一和展示二平均，占40%；实践考核占40%；平时表现占20%；缓考保留，保留一位小数。"


def business():
    return {"assessment_items": [
        {"id": "presentation", "name": "课堂展示", "sources": ["展示一", "展示二"], "operation": "mean", "basis": "教师要求两次展示平均"},
        {"id": "practice", "name": "实践考核", "sources": ["实践"], "operation": "direct", "basis": "教师指定实践成绩"},
        {"id": "usual", "name": "平时表现", "sources": ["平时"], "operation": "direct", "basis": "教师指定平时成绩"}],
        "weights": {"课堂展示": "0.4", "实践考核": "0.4", "平时表现": "0.2"},
        "missing": {"缓考": {"action": "preserve"}}, "precision": 1, "pass_threshold": "60", "source_instruction": TASK}


def adjustment(**changes):
    value = {"id": "usual-pass", "baseline": "before_adjustment", "scope": {"field": "总评", "op": "lt", "value": "60"},
             "target": "平时表现", "action": "set", "value": "100", "special": "preserve", "cap": "100",
             "preserve": "其他项目与权重不变", "basis": "教师以本次调整前总评低于60为范围，将平时表现设为100"}
    value.update(changes)
    return value


class SemanticPipeline(unittest.TestCase):
    def setUp(self):
        (ROOT / "work").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "work", prefix="semantic-")
        self.store = PipelineStore(self.temp.name)
        self.uploads = [(name, (DATA / name).read_bytes()) for name in ("roster.csv", "performance.csv")]

    def tearDown(self):
        self.temp.cleanup()

    def create(self, rules=None):
        return self.store.create(TASK, self.uploads, rules=rules or business())

    def record(self, name, expected, actual):
        REPORT.append({"case": name, "expected": expected, "actual": actual, "status": "passed"})

    def test_01_complete_and_custom_items(self):
        s = self.create()
        self.assertEqual(s["phase"], "complete", s["issues"])
        rows = s["stage2"]["rows"]
        self.assertEqual([r["总评"] for r in rows], ["48.0", "86.0", "0.0", "缓考"])
        self.assertEqual(rows[2]["展示一"], "0")
        self.assertEqual([r["student_id"] for r in rows], ["001", "002", "003", "004"])
        self.assertEqual(s["stage1"]["counts"]["collapsed_submissions"], 0)
        self.assertTrue((self.store.directory(s["id"]) / "semantic/v1.json").exists())
        self.record("完整规则与自定义项目", "连续两阶段；两个展示来源不去重；0/前导零/缓考保留", [r["总评"] for r in rows])

    def test_02_operations_and_dependencies(self):
        metrics = ["甲", "乙", "丙"]
        for op, extra in [("direct", {}), ("mean", {}), ("max", {}), ("sum", {"max_score": "300"}),
                          ("best", {"n": 2, "reduce": "mean"}), ("weighted", {"weights": ["0.2", "0.3", "0.5"]})]:
            item = {"id": "p", "name": "自定义答辩", "sources": metrics[:1] if op == "direct" else metrics,
                    "operation": op, "basis": "课程说明", **extra}
            rule = compile_rules({"assessment_items": [item], "weights": {"p": "1"}}, metrics)
            self.assertEqual(rule["formulas"][0]["name"], "自定义答辩")
        cyclic = [{"id": "a", "name": "项目甲", "sources": ["b"], "operation": "mean", "basis": "测试"},
                  {"id": "b", "name": "项目乙", "sources": ["a"], "operation": "mean", "basis": "测试"}]
        with self.assertRaisesRegex(PipelineError, "循环"):
            compile_rules({"assessment_items": cyclic, "weights": {"a": "1"}}, metrics)
        with self.assertRaisesRegex(PipelineError, "来源不存在"):
            compile_rules({"assessment_items": [{**cyclic[0], "sources": ["不存在"]}], "weights": {"a": "1"}}, metrics)
        self.record("组合与依赖校验", "有限组合可编译；虚构来源及循环被拒绝", "6类操作通过，非法方案拒绝")

    def test_03_fixed_scope_and_repeat(self):
        s = self.create()
        frozen = s["stage1"]["manifest_sha256"]
        rule = business(); rule["adjustments"] = [adjustment()]
        s = self.store.resume(s["id"], s["revision"], rules=rule)
        self.assertEqual(s["phase"], "complete", s["issues"])
        self.assertEqual(s["stage2"]["adjustment_scope"][0]["student_ids"], ["001", "003"])
        self.assertEqual([r["总评"] for r in s["stage2"]["rows"]], ["64.0", "86.0", "20.0", "缓考"])
        before = copy.deepcopy(s["stage2"]["rows"])
        s = self.store.resume(s["id"], s["revision"], rules=rule)
        self.assertEqual(s["stage2"]["rows"], before)
        self.assertEqual(s["stage1"]["manifest_sha256"], frozen)
        changed = copy.deepcopy(rule); changed["weights"] = {"课堂展示": "0.2", "实践考核": "0.4", "平时表现": "0.4"}
        s = self.store.resume(s["id"], s["revision"], rules=changed)
        self.assertEqual(s["stage2"]["adjustment_scope"][0]["student_ids"], ["001", "003"])
        self.assertEqual(s["stage2"]["rows"][0]["平时"], "20")
        self.record("固定范围与重算", "仅001/003调整，越过及格线后仍保持固定范围；重复不累计；原值不覆盖", {"scope": s["stage2"]["adjustment_scope"], "dwd_unchanged": True})

    def test_04_id_scopes_and_actions(self):
        s = self.create()
        for action, value, expected in [("add", "10", "30"), ("multiply", "2", "40"), ("set", "80", "80"), ("floor", "50", "50")]:
            rule = business(); rule["adjustments"] = [adjustment(action=action, value=value, scope={"ids": ["001"]})]
            s = self.store.resume(s["id"], s["revision"], rules=rule)
            self.assertEqual(s["phase"], "complete", s["issues"])
            self.assertEqual(s["stage2"]["rows"][0]["平时表现（调整后）"], expected)
            self.assertEqual(s["stage2"]["rows"][1]["总评"], "86.0")
        rule["adjustments"][0]["scope"] = {"ids": ["1"]}
        s = self.store.resume(s["id"], s["revision"], rules=rule)
        self.assertEqual(s["phase"], "business_checkpoint")
        self.assertIn("编号不在", s["issues"][0]["reason"])
        self.record("指定编号与四类动作", "001保留前导零；其余不变；不存在编号拒绝", "四动作通过，编号1被拒绝")

    def test_05_overlap_and_order(self):
        s = self.create(); rule = business()
        rule["adjustments"] = [adjustment(scope={"ids": ["001"]}), adjustment(id="bonus", scope={"ids": ["001"]}, action="add", value="10")]
        s = self.store.resume(s["id"], s["revision"], rules=rule)
        self.assertEqual(s["phase"], "business_checkpoint")
        self.assertIn("执行顺序", s["issues"][0]["reason"])
        self.assertIsNone(s["stage2"])
        rule["adjustments"][0]["order"] = 1; rule["adjustments"][1]["order"] = 2
        s = self.store.resume(s["id"], s["revision"], rules=rule)
        self.assertEqual(s["phase"], "complete", s["issues"])
        self.assertEqual(s["stage2"]["rows"][0]["平时表现（调整后）"], "100")
        self.record("重叠调整", "未指定顺序暂停，明确顺序后执行并封顶", "冲突被阻断，补充1/2后完成")

    def test_06_special_status(self):
        s = self.create(); rule = business()
        rule["adjustments"] = [adjustment(target="课堂展示", scope={"ids": ["004"]})]
        s = self.store.resume(s["id"], s["revision"], rules=rule)
        self.assertEqual(s["phase"], "complete", s["issues"])
        self.assertEqual(s["stage2"]["rows"][3]["课堂展示"], "缓考")
        self.assertEqual(s["stage2"]["adjustment_changes"][0]["status"], "特殊状态保留")
        rule["adjustments"][0]["special"] = "hold"
        s = self.store.resume(s["id"], s["revision"], rules=rule)
        self.assertEqual(s["phase"], "business_checkpoint")
        self.assertIn("特殊状态", s["issues"][0]["reason"])
        self.record("特殊状态", "保留政策不强制改数值，hold时暂停", "缓考保留；hold阻断")

    def test_07_trial_and_adoption(self):
        s = self.create(); accepted = s["accepted_result"]["trial_id"]; dwd = s["stage1"]["manifest_sha256"]
        rule = business(); rule["weights"] = {"课堂展示": "0.5", "实践考核": "0.3", "平时表现": "0.2"}
        s = self.store.resume(s["id"], s["revision"], rules=rule, trial=True)
        self.assertEqual(s["stage2"]["mode"], "trial")
        self.assertEqual(s["accepted_result"]["trial_id"], accepted)
        self.assertEqual(s["stage1"]["manifest_sha256"], dwd)
        self.assertTrue(s["stage2"]["comparison"]["changes"])
        old_csv = list(csv.DictReader(io.StringIO(self.store.download(s["id"], "accepted")[0].decode("utf-8-sig"))))
        self.assertEqual(old_csv, s["accepted_result"]["rows"])
        s = self.store.adopt_trial(s["id"], s["revision"])
        self.assertEqual(s["accepted_result"]["trial_id"], s["stage2"]["trial_id"])
        self.record("试算与采用", "试算保持采用版本，差异及两个CSV可核对；明确采用后切换", "全部通过")

    def test_08_dwd_change_and_stale_baseline(self):
        s = self.create(); old = s["stage1"]["manifest_sha256"]
        plan = copy.deepcopy(s["plan"]); plan["platforms"][1]["metrics"][0]["strategy"] = "mean"
        rule = business(); rule["adjustments"] = [adjustment(baseline=s["stage2"]["trial_id"])]
        s = self.store.resume(s["id"], s["revision"], plan=plan, rules=rule, reason="教师改变归并策略")
        self.assertNotEqual(s["stage1"]["manifest_sha256"], old)
        self.assertEqual(s["phase"], "business_checkpoint")
        self.assertIn("旧阶段一", s["issues"][0]["reason"])
        self.assertIsNone(s["stage2"])
        self.record("数据依赖失效", "DWD变化后旧基准不可沿用", "拒绝旧阶段一基准，无当前计算结果")

    def test_09_manual_fallback_and_versions(self):
        s = self.create()
        s = self.store.instruct(s["id"], s["revision"], "把答辩情况综合一下", request_id="manual-1")
        self.assertEqual(s["phase"], "understanding_checkpoint")
        self.assertIn("语义分析未启用", s["issues"][0]["reason"])
        self.assertEqual(s["rules"], business())
        with self.assertRaises(RevisionError):
            self.store.instruct(s["id"], s["revision"] - 1, "课堂展示40%，实践考核40%，平时表现20%")
        self.record("未配置模型与版本", "不伪造理解；规则保留，旧请求拒绝", "明确待补充，旧revision被拒绝")

    def test_10_form_percentages_and_export(self):
        s = self.create(); form = business()
        form["assessment_items"][0].update(operation="weighted", weights_percent=["40", "60"])
        form["weights_percent"] = {"课堂展示": "40", "实践考核": "40", "平时表现": "20"}
        form.pop("weights")
        normalized = self.store.prepare_rules(s["id"], s["revision"], form)["rules"]
        self.assertEqual(normalized["assessment_items"][0]["weights"], ["0.4", "0.6"])
        s = self.store.resume(s["id"], s["revision"], rules=normalized)
        self.assertEqual(s["phase"], "complete", s["issues"])
        downloaded = list(csv.DictReader(io.StringIO(self.store.download(s["id"], "dws")[0].decode("utf-8-sig"))))
        self.assertEqual(downloaded, s["stage2"]["rows"])
        self.record("表单与CSV", "Python转换内部/总评权重，下载等于当前预览", "完整一致")

    def test_11_model_cannot_confirm(self):
        s = self.create(); plan = copy.deepcopy(s["plan"])
        plan["identity_resolutions"] = {"F2:2": {"student_id": "001", "reason": "模型声称已确认"}}
        with self.assertRaisesRegex(PipelineError, "不得冒充"):
            validate_proposal({"stage1_plan": plan}, s)
        with self.assertRaises(PipelineError):
            validate_proposal({"confirmed": True}, s)
        self.record("模型越权校验", "确认标志和人工裁决不能由模型伪造", "均被拒绝")

    def test_12_long_form_grain(self):
        uploads = [self.uploads[0], ("activities.csv", "编号,活动,得分（百分制）\n001,展示一,40\n001,展示二,60\n".encode())]
        s = self.store.create("原始数据百分制", uploads)
        plan = copy.deepcopy(s["plan"]); spec = plan["platforms"][1]
        spec["record_grain"] = ["活动"]
        spec["metrics"][0]["strategy"] = "max"
        s = self.store.resume(s["id"], s["revision"], plan=plan, reason="按活动区分业务记录")
        self.assertEqual(s["phase"], "data_checkpoint")
        self.assertTrue(any("不同活动" in q["reason"] for q in s["issues"]))
        original = copy.deepcopy(spec["metrics"][0]); spec["metrics"] = [{**original, "target": "展示一", "where": {"活动": "展示一"}},
                                                                     {**original, "target": "展示二", "where": {"活动": "展示二"}}]
        s = self.store.resume(s["id"], s["revision"], plan=plan, rules={"weights": {"展示一": ".5", "展示二": ".5"}, "missing": {"": {"action": "preserve"}}})
        self.assertEqual(s["phase"], "complete", s["issues"])
        self.assertEqual(s["stage1"]["rows"][0]["展示一"], "40")
        self.assertEqual(s["stage1"]["rows"][0]["展示二"], "60")
        self.record("长表活动粒度", "不同活动不能max去重，按实际活动分列后继续", "阻断误归并，补充活动筛选后保留40与60")

    def test_13_unknown_execution_fault(self):
        s = self.create()
        with patch("pipeline.dws.subprocess.run", side_effect=RuntimeError("fixture unexpected fault")):
            s = self.store.resume(s["id"], s["revision"], rules=business())
        self.assertEqual(s["phase"], "system_error")
        self.assertIsNone(s["stage2"])
        self.assertEqual(s["issues"][0]["kind"], "system")
        self.record("未知执行故障", "系统错误不能伪装为规则缺项", "system_error，当前结果清空")

    def test_14_legacy_state_and_partial_validation(self):
        s = self.create()
        legacy = copy.deepcopy(s)
        for key in ("instructions", "decisions", "requests", "accepted_result", "semantic_plan"):
            legacy.pop(key, None)
        path = self.store.directory(s["id"]) / "workflow.json"
        path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        restored = self.store.load(s["id"])
        self.assertEqual(restored["rules"], business())
        self.assertEqual(restored["accepted_result"]["trial_id"], s["stage2"]["trial_id"])
        with self.assertRaises(PipelineError):
            validate_proposal({"stage2_draft": {"confirmed": True}, "questions": ["补充"]}, restored)
        with self.assertRaises(PipelineError):
            compile_rules({**business(), "weights": "bad structure"}, [m["target"] for p in restored["plan"]["platforms"] for m in p["metrics"]])
        self.record("旧状态与草稿边界", "旧状态直接恢复；草稿不能含确认标记，错误类型须明确拒绝", "全部通过")


class ModelTransport(unittest.TestCase):
    """Explicitly labelled transport fixture, separate from deterministic engine checks."""
    setUp = SemanticPipeline.setUp
    tearDown = SemanticPipeline.tearDown
    record = SemanticPipeline.record
    def test_20_actual_transport_profile_and_instructions(self):
        responses = [{"stage2_rules": business(), "questions": [], "rationale": "固定测试响应，不是真实模型判断"}]
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                content = responses.pop(0)
                omit_evidence = content.pop("_fixture_omit_evidence", False)
                # Evidence quotes originate in the actual submitted task, never fixture-invented student data.
                if content.get("stage2_rules") is not None and not content.get("questions") and not omit_evidence:
                    context = json.loads(requests[-1]["messages"][1]["content"])
                    latest = context["conversation"][-1]
                    content["evidence"] = [{"section": section, "instruction_id": latest["id"], "quote": latest["text"]}
                                           for section in ("business", "adjustments")]
                body = json.dumps({"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with patch.dict(os.environ, {"SEMANTIC_TEST_KEY": "fixture-secret-only"}):
                self.store.model_config = {"enabled": True, "model": "labelled-fixture", "endpoint": f"http://127.0.0.1:{server.server_port}", "api_key_env": "SEMANTIC_TEST_KEY"}
                s = self.store.create(TASK, self.uploads)
                self.assertEqual(s["phase"], "complete", s["issues"])
                context = json.loads(requests[0]["messages"][1]["content"])
                self.assertEqual(context["files"][1]["row_count"], 4)
                self.assertGreater(context["files"][1]["fields"][0]["leading_zero_count"], 0)
                self.assertNotIn("samples", context["files"][1])
                rule = business(); rule["adjustments"] = [adjustment()]
                responses.append({"stage2_rules": rule, "questions": [], "rationale": "固定测试调整方案"})
                rev = s["revision"]
                s = self.store.instruct(s["id"], rev, "低于60分者平时表现设为100，其他不变，缓考保留", request_id="one")
                self.assertEqual(s["phase"], "complete", s["issues"])
                result = copy.deepcopy(s["stage2"])
                same = self.store.instruct(s["id"], rev, "低于60分者平时表现设为100，其他不变，缓考保留", request_id="one")
                self.assertEqual(same["stage2"], result)
                self.assertEqual(len(requests), 2)
                self.assertEqual(json.loads(requests[1]["messages"][1]["content"])["instruction"], "低于60分者平时表现设为100，其他不变，缓考保留")
                responses.append({"questions": ["实践考核改用哪个真实来源字段？"], "rationale": "只问缺项"})
                s = self.store.instruct(s["id"], s["revision"], "改用另一项实践成绩", request_id="two")
                self.assertEqual(s["phase"], "understanding_checkpoint")
                self.assertEqual(len(s["issues"]), 1)
                self.assertEqual(s["rules"], rule)
                self.assertNotIn("fixture-secret-only", json.dumps(s))
                draft = business(); draft.pop("adjustments", None); draft["assessment_items"][0]["sources"] = []
                responses.append({"stage2_draft": draft, "questions": ["课堂展示来源具体是哪两列？"], "rationale": "保留明确权重只问来源"})
                s = self.store.instruct(s["id"], s["revision"], "课堂展示来源待补充，已有权重不变", request_id="three")
                self.assertEqual(s["business_draft"]["weights"], business()["weights"])
                self.assertEqual(s["phase"], "understanding_checkpoint")
                responses.append({"stage2_rules": business(), "questions": [], "rationale": "沿用草稿权重"})
                s = self.store.instruct(s["id"], s["revision"], "课堂展示来源为展示一与展示二", request_id="four")
                self.assertEqual(s["phase"], "complete", s["issues"])
                context = json.loads(requests[-1]["messages"][1]["content"])
                self.assertEqual(context["stage2_draft"]["weights"], business()["weights"])
                new_rule = business(); new_rule["weights"] = {"课堂展示": ".5", "实践考核": ".3", "平时表现": ".2"}
                responses.append({"stage2_rules": new_rule, "questions": [], "rationale": "测试模型漏引用", "_fixture_omit_evidence": True})
                s = self.store.instruct(s["id"], s["revision"], "课堂展示50%，实践考核30%，平时表现20%", request_id="five")
                self.assertEqual(s["phase"], "understanding_error")
                self.assertEqual(s["issues"][0]["kind"], "model")
                self.assertIn("无需重新确认", s["issues"][0]["reason"])
                self.record("真实传输路径的离线契约验证", "实际读取后发送统计，不默认发样本；首次自动执行，追加生效，重试不累计，只问缺项", "全部通过；服务响应为明确标注fixture，未调用真实LLM")
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SemanticPipeline)
    suite.addTest(ModelTransport("test_20_actual_transport_profile_and_instructions"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    path = ROOT / "outputs/semantic_backend_acceptance.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "cases": REPORT, "real_llm": "not_run; labelled local fixture transport only"}, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.exit(not result.wasSuccessful())
