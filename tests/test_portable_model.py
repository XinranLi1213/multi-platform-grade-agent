"""Local HTTP fixture tests; never describe fixture replies as real LLM validation."""
import copy
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import create_server
from integration.model_client import check_connection, complete_json, ModelFailure
from integration.models import load_config, model_status
from pipeline.advisor import suggest
from pipeline.common import PipelineError, sha
from pipeline.service import PipelineStore
from tools.doctor import inspect_environment
import tests.test_semantic_pipeline as fixtures

REPORT = []


class PortableModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.replies, cls.received = [], []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                cls.received.append(request)
                status, content = cls.replies.pop(0)
                if status == 302:
                    self.send_response(status); self.send_header("Location", "/should-not-follow"); self.end_headers(); return
                body = (json.dumps({"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}) if status == 200 else str(content)).encode()
                if status == 201:  # Deliberately incompatible protocol, represented by an actual HTTP 200.
                    status, body = 200, b'{"output": "unsupported protocol"}'
                self.send_response(status); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_GET(self):
                cls.received.append({"unexpected_redirect": True})
                self.send_response(500); self.end_headers()
        cls.model_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.model_server.serve_forever, daemon=True); cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.model_server.shutdown(); cls.model_server.server_close(); cls.thread.join()

    def setUp(self):
        self.replies.clear(); self.received.clear()
        self.env = patch.dict(os.environ, {"TRIAL_TEST_KEY": "only-fixture-secret"}); self.env.start()
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "work", prefix="portable-model-")
        self.root = Path(self.temp.name)
        self.config = {"enabled": True, "endpoint": f"http://127.0.0.1:{self.model_server.server_port}/chat/completions",
                       "model": "explicit-test-fixture", "api_key_env": "TRIAL_TEST_KEY"}

    def tearDown(self):
        self.env.stop(); self.temp.cleanup()

    def record(self, name, expected, actual):
        REPORT.append({"case": name, "expected": expected, "actual": actual, "status": "passed"})

    def create(self):
        self.store = PipelineStore(self.root / "state")
        uploads = [(name, (fixtures.DATA / name).read_bytes()) for name in ("roster.csv", "performance.csv")]
        return self.store.create(fixtures.TASK, uploads, rules=fixtures.business())

    def test_01_configuration(self):
        self.assertEqual(load_config(self.root / "missing.json")["_config_error"], "config_missing")
        bad = self.root / "bad.json"; bad.write_text("{not json", encoding="utf-8")
        self.assertEqual(load_config(bad)["_config_error"], "config_invalid")
        self.assertTrue(model_status(self.config)["enabled"])
        for changes, code in [({"api_key": "forbidden"}, "secret_in_config"), ({"protocol": "other"}, "unsupported_protocol"),
                              ({"endpoint": "https://example.test/api?key=secret"}, "endpoint_credentials"),
                              ({"api_key_env": "MISSING_TEST_ENV"}, "key_missing")]:
            self.assertEqual(model_status({**self.config, **changes})["code"], code)
        self.assertFalse(model_status(self.config)["connection_checked"])
        self.record("配置检查", "缺文件/坏JSON/URL密钥/明文密钥/协议/环境变量明确分类，不冒充连接成功", "全部拒绝或正确标注，配置完整仍未连接验证")

    def test_02_connection_probe(self):
        self.replies.append((200, {"connection_check": "ok"}))
        result = check_connection(self.config)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["protocol_ok"])
        self.assertIsNone(result["plan_contract_ok"])
        self.assertFalse(result["business_quality_verified"])
        self.assertNotIn("files", json.dumps(self.received[0]))
        self.record("真实连接检查传输路径", "只发固定探测，连接不等于业务理解成功", result)

    def test_03_http_errors_and_redirect(self):
        for status, code in [(401, "authentication"), (403, "forbidden"), (429, "rate_limit"), (500, "http_error"), (302, "redirect_blocked")]:
            self.replies.append((status, "only-fixture-secret provider echo"))
            before = len(self.received)
            result = check_connection(self.config)
            self.assertEqual(result["code"], code)
            self.assertNotIn("only-fixture-secret", json.dumps(result))
            self.assertEqual(len(self.received), before + 1)
        self.record("服务错误及重定向", "401/403/429/500分类；不读错误正文，不转发重定向密钥", "全部通过，无密钥回显，无第二次重定向请求")

    def test_04_protocol_json_and_timeout(self):
        self.replies.append((201, None))
        self.assertEqual(check_connection(self.config)["code"], "protocol")
        self.replies.append((200, "not a json object"))
        self.assertEqual(check_connection(self.config)["code"], "json")
        with patch("urllib.request.OpenerDirector.open", side_effect=TimeoutError()):
            self.assertEqual(check_connection(self.config)["code"], "timeout")
        self.record("协议与超时", "不兼容返回、非对象JSON、超时均不冒充成功", "protocol/json/timeout，保留手动流程")

    def test_05_redaction(self):
        self.replies.append((200, {"rationale": "only-fixture-secret", "nested": ["only-fixture-secret"]}))
        value = complete_json(self.config, [{"role": "user", "content": "test"}])
        self.assertNotIn("only-fixture-secret", json.dumps(value))
        self.record("响应脱敏", "嵌套响应密钥回显不能进入状态或页面", value)

    def test_06_one_bounded_repair(self):
        state = self.create()
        bad = fixtures.business(); bad["assessment_items"][0]["sources"] = ["虚构字段"]
        self.replies.append((200, {"stage2_rules": bad, "questions": []}))
        self.replies.append((200, {"stage2_rules": fixtures.business(), "questions": [], "rationale": "fixture"}))
        result = suggest(self.config, state, fixtures.TASK)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(len(self.received), 2)
        self.assertIn("程序校验反馈", self.received[1]["messages"][-1]["content"])
        self.assertEqual(state["rules"], fixtures.business())
        self.replies.extend([(200, {"unknown": "invalid"}), (200, {"unknown": "still invalid"})])
        self.assertEqual(suggest(self.config, state)["status"], "invalid")
        self.assertEqual(len(self.received), 4)
        self.record("受控方案修正", "坏字段反馈后最多修正一次，失败不执行，不无限循环", "成功2次请求；连续非法仍只2次请求，原规则不变")

    def test_07_forged_human_decision(self):
        state = self.create(); plan = copy.deepcopy(state["plan"])
        plan["identity_resolutions"] = {"F2:2": {"student_id": "001", "reason": "模型声称已确认"}}
        self.replies.append((200, {"stage1_plan": plan}))
        result = suggest(self.config, state)
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(len(self.received), 1)
        self.record("人工边界", "伪造身份确认直接拒绝，不用修正机会代替教师确认", "拒绝，只有1次请求")

    def test_08_http_health_and_probe(self):
        server = create_server(port=0, state_dir=self.root / "api", model_config=self.config)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            health = json.load(urllib.request.urlopen(url + "/api/health"))
            self.assertTrue(health["loopback_only"])
            self.replies.append((200, {"connection_check": "ok"}))
            req = urllib.request.Request(url + "/api/model/check", data=b"{}", headers={"Content-Type": "application/json"})
            self.assertEqual(json.load(urllib.request.urlopen(req))["status"], "ok")
            config = json.load(urllib.request.urlopen(url + "/api/config"))
            self.assertTrue(config["last_model_check"]["protocol_ok"])
            self.assertEqual(config["pipeline_limits"]["max_files"], 6)
            req = urllib.request.Request(url + "/api/model/check", data=b'{"key":"forbidden"}', headers={"Content-Type": "application/json"})
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(req)
            self.assertEqual(error.exception.code, 400)
            with self.assertRaises(ValueError):
                create_server(host="0.0.0.0", state_dir=self.root / "blocked")
            self.record("健康与连接HTTP接口", "本机接口不接前端密钥；配置/连通状态分开，禁止远程监听", "GET健康、POST检查、持久于服务内的检查状态通过；前端密钥400，0.0.0.0拒绝")
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_09_foreign_state(self):
        state = self.create()
        old_file = self.store.directory(state["id"]) / "workflow.json"
        old_hash = sha(old_file)
        moved_root = self.root / "另一台电脑 中文路径" / "state"
        shutil.copytree(self.root / "state", moved_root)
        moved = PipelineStore(moved_root)
        loaded = moved.load(state["id"])
        self.assertEqual(loaded["phase"], "migration_required")
        self.assertIsNone(loaded["stage2"])
        with self.assertRaises(PipelineError):
            moved.resume(state["id"], loaded["revision"], rules=fixtures.business())
        self.assertEqual(sha(old_file), old_hash)
        self.assertEqual(sha(moved.directory(state["id"]) / "workflow.json"), old_hash)
        self.record("历史状态换目录", "不执行旧路径、不改哈希，原状态保留，只允许归档和新建重跑", "migration_required，继续被拒，两个状态文件哈希不变")

    def test_10_environment_failures(self):
        report = inspect_environment(root=self.root, port=8765)
        self.assertFalse(report["checks"][1]["ok"])
        blocking = self.root / "not-a-directory"; blocking.write_text("file", encoding="utf-8")
        report = inspect_environment(root=ROOT, state_dir=blocking)
        self.assertFalse(report["checks"][2]["ok"])
        with patch("sys.version_info", (3, 10)):
            self.assertFalse(inspect_environment(root=ROOT, state_dir=self.root / "runtime")["checks"][0]["ok"])
        (self.root / "RELEASE_MANIFEST.json").write_text('{"files":["invalid-type"]}', encoding="utf-8")
        malformed = inspect_environment(root=self.root)
        self.assertFalse(next(c for c in malformed["checks"] if c["name"] == "分发完整性")["ok"])
        self.record("环境检查失败", "缺源码、不可写目录、旧Python不能显示启动成功", "全部正确拒绝")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PortableModel)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    (ROOT / "outputs").mkdir(exist_ok=True)
    (ROOT / "outputs/portable_model_acceptance.json").write_text(json.dumps({"tests_run": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "cases": REPORT,
        "external_real_llm": "not_run; explicitly labelled local fixture transport only", "second_physical_pc": "not_run"}, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.exit(not result.wasSuccessful())
