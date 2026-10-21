"""Real HTTP acceptance tests. Use existing fixtures; never silently generate inputs."""

import base64
import copy
import csv
import io
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import create_server
from integration.engine import required_confirmations
from integration.models import model_status, validate_advice
from integration.store import SessionStore

DATA = ROOT / "tests/data"
REPORT = []


class Acceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / "work").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="interactive-tests-", dir=ROOT / "work")
        cls.state_dir = Path(cls.temp.name) / "state"
        cls.server = create_server(port=0, state_dir=cls.state_dir)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def request(self, path, body=None, status=200, raw=False):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            response = urllib.request.urlopen(req, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        self.assertEqual(response.code, status, response.read().decode() if response.code != status else path)
        content = response.read()
        return content if raw else json.loads(content)

    def uploaded(self, case, a="a.csv", b="b.csv"):
        files = [(f"自选-{side}-{Path(name).name}", (DATA / case / name).read_bytes()) for side, name in (("甲", a), ("乙", b))]
        state = self.request("/api/sessions", {"task": "整合两个平台的业务数据，确认同义字段后关联，保留无法确定的原始记录。",
            "files": [{"name": name, "content_base64": base64.b64encode(value).decode()} for name, value in files]}, 201)
        return state

    def configure(self, state, mapping=None, keys=None, mode="join", **updates):
        plan = copy.deepcopy(state["plan"])
        plan.update(row_meaning="每行代表一个明确的业务对象或由组合键区分的业务记录", mode=mode,
                    key_fields=keys or [], link_basis="用户核对：所选编号作用域一致，组合键区分有效业务记录")
        if mapping:
            for side, entries in mapping.items():
                for source, target in entries.items():
                    plan["mapping"][side][source]["target"] = target
        plan.update(updates)
        return self.request(f"/api/sessions/{state['id']}/plan", {"revision": state["revision"], "plan": plan})

    def execute(self, state, status=200, confirmations=None):
        flags = {key: True for key in required_confirmations(state["plan"])} if confirmations is None else confirmations
        return self.request(f"/api/sessions/{state['id']}/execute", {"revision": state["revision"], "confirmations": flags}, status)

    def evidence(self, code, expected, actual):
        REPORT.append({"case": code, "expected": expected, "actual": actual, "status": "passed"})

    def contacts(self, case="01_synonyms", **updates):
        state = self.uploaded(case)
        return self.configure(state, {"A": {"客户号": "对象编号", "联系人": "姓名", "联系电话": "电话"},
                "B": {"编号": "对象编号", "联络人": "姓名", "手机号": "电话"}}, ["对象编号"], **updates)

    def test_01_synonyms(self):
        state = self.uploaded("01_synonyms")
        candidate = next(c for c in state["analysis"]["value_candidates"] if c["a"] == "联系电话" and c["b"] == "手机号")
        self.assertEqual(candidate["overlap"], 2)
        self.assertIn("待确认", candidate["evidence"])
        self.assertIsNone(state["result"])
        state = self.contacts()
        result = self.execute(state)["result"]
        self.assertEqual(result["counts"]["merged_pairs"], 2)
        self.assertEqual(result["counts"]["output_rows"], 2)
        row = next(r for r in result["records"] if r["values"]["对象编号"] == "001")
        self.assertEqual(row["values"]["次数"], "0")
        self.assertEqual(len(row["sources"]), 2)
        self.evidence("01 同义字段", "提出2个电话原值交集的待确认候选；确认后4→2，保留001和0", {"candidate": candidate, "counts": result["counts"]})

    def test_02_different_meanings(self):
        state = self.uploaded("02_different_meanings")
        self.assertEqual(state["plan"]["mapping"]["A"]["总成绩"]["target"], "总成绩")
        self.assertEqual(state["plan"]["mapping"]["B"]["期末成绩"]["target"], "期末成绩")
        state = self.configure(state, {"A": {"单据号": "业务编号"}}, ["业务编号"])
        result = self.execute(state)["result"]
        self.assertEqual(result["records"][0]["values"], {"业务编号": "001", "总成绩": "80", "期末成绩": "80"})
        self.assertNotIn("总分", result["fields"])
        self.evidence("02 近名不同义", "总成绩和期末成绩不同列，无计算", result["records"][0]["values"])

    def test_03_duplicates(self):
        state = self.contacts("03_exact_duplicates")
        kept = self.execute(state)["result"]
        self.assertEqual(kept["counts"]["deleted_copies"], 0)
        state = self.configure(state, keys=["对象编号"], dedupe="exact", dedupe_basis="已核对为重复导出同一记录的冗余副本")
        self.execute(state, 422, {"mapping": True, "grain": True, "link": True})
        result = self.execute(state)["result"]
        self.assertEqual(result["counts"]["deleted_copies"], 1)
        self.assertEqual(result["counts"]["output_rows"], 2)
        self.assertEqual(result["counts"]["source_records_covered"], 5)
        self.assertEqual(len(result["audit"]["deduplications"]), 1)
        self.evidence("03 完全重复", "未确认不删；确认后5−1−2=2，来源覆盖5", result["counts"])

    def test_04_business_records(self):
        state = self.configure(self.uploaded("04_business_records"), {"B": {"对象编号": "客户号", "轮次": "服务轮次"}}, ["客户号"])
        result = self.execute(state)["result"]
        self.assertEqual(result["counts"]["pending_output_rows"], 4)
        self.assertEqual(result["counts"]["deleted_copies"], 0)
        state = self.configure(state, keys=["客户号", "服务轮次"])
        result = self.execute(state)["result"]
        self.assertEqual(result["counts"]["merged_pairs"], 2)
        self.assertEqual(result["counts"]["deleted_copies"], 0)
        self.evidence("04 不同业务记录", "单键多对多保留4；组合键关联2对，0删除", result["counts"])

    def test_05_same_name(self):
        state = self.configure(self.uploaded("05_same_name"), keys=["姓名"])
        self.assertTrue(any("不能仅凭姓名" in e for e in state["analysis"]["errors"]))
        self.execute(state, 422)
        state = self.configure(state, keys=["电话"])
        result = self.execute(state)["result"]
        self.assertEqual(result["counts"]["merged_pairs"], 0)
        self.assertEqual(result["counts"]["output_rows"], 2)
        self.evidence("05 同名异人", "仅姓名被阻止；电话不同，全外关联保留2条", result["counts"])

    def test_06_no_school_id(self):
        state = self.configure(self.uploaded("06_no_school_id"), {"B": {"账户编码": "外部编号", "Email": "邮箱"}}, ["外部编号"])
        result = self.execute(state)["result"]
        self.assertEqual(result["counts"]["merged_pairs"], 1)
        self.assertEqual(result["records"][0]["values"]["外部编号"], "AC-001")
        self.evidence("06 非学号依据", "外部编号确认后2→1", result["counts"])

    def test_07_conflict_and_resume(self):
        state = self.configure(self.uploaded("07_conflicts"), keys=["编号"])
        state = self.execute(state)
        result = state["result"]
        self.assertEqual(result["counts"]["output_rows"], 3)
        self.assertEqual(result["counts"]["pending_output_rows"], 2)
        self.assertEqual(result["counts"]["merged_pairs"], 1)
        cities = {r["values"]["城市"] for r in result["records"] if r["status"] == "待确认"}
        self.assertEqual(cities, {"上海", "北京"})
        old_revision, sid = state["revision"], state["id"]
        state = self.configure(state, {"A": {"城市": "平台A城市"}, "B": {"城市": "平台B城市"}}, ["编号"])
        self.assertIsNone(state["result"])
        self.request(f"/api/sessions/{sid}/download", status=409)
        self.request(f"/api/sessions/{sid}/execute", {"revision": old_revision, "confirmations": {}}, 409)
        state = self.execute(state)
        self.assertEqual(state["id"], sid)
        self.assertEqual(state["result"]["counts"]["output_rows"], 2)
        values = state["result"]["records"][0]["values"]
        self.assertEqual((values["平台A城市"], values["平台B城市"]), ("上海", "北京"))
        self.evidence("07 冲突与续算", "冲突保留2，其余关联；分别保留城市列后同会话4→2，旧下载409", state["result"]["counts"])

    def test_07b_missing_key_resume(self):
        state = self.configure(self.uploaded("07_missing_keys"), {"B": {"账户编号": "客户号"}}, ["电话"])
        result = self.execute(state)["result"]
        self.assertEqual(result["counts"]["pending_output_rows"], 2)
        self.assertEqual(result["counts"]["merged_pairs"], 0)
        state = self.configure(state, keys=["客户号"])
        result = self.execute(state)["result"]
        self.assertEqual(result["counts"]["merged_pairs"], 1)
        self.assertEqual(result["records"][0]["values"]["电话"], "")
        self.evidence("07b 缺关联依据", "空电话不关联；换已上传的客户号后关联，电话仍空", result["counts"])

    def test_08_bad_rows(self):
        state = self.uploaded("08_bad_rows")
        f = state["files"][0]
        self.assertEqual((f["status"], f["row_count"], f["bad_record_count"]), ("partial", 2, 1))
        self.assertEqual(f["errors"][0]["line"], 3)
        state = self.configure(state, keys=["业务键"])
        self.execute(state, 422)
        self.request(f"/api/sessions/{state['id']}/download", status=409)
        raw = self.request(f"/api/sessions/{state['id']}/original/A", raw=True)
        self.assertEqual(raw, (DATA / "08_bad_rows/a.csv").read_bytes())
        self.evidence("08 坏行", "部分读取：有效2、坏1、物理第3行；禁止执行，原始字节未变", f)

    def test_08b_failed_files(self):
        for name, raw in [("empty.csv", b""), ("quoted.csv", b'a,b\n"unterminated,2'),
                          ("wrong.xlsx", b"not a CSV"), ("binary.csv", b"a,b\n1,\x00")]:
            state = self.request("/api/sessions", {"task": "检查用户提供的两份文件", "files": [
                {"name": name, "content_base64": base64.b64encode(raw).decode()},
                {"name": "normal.csv", "content_base64": base64.b64encode(b"a,b\n1,2\n").decode()}]}, 201)
            self.assertEqual(state["files"][0]["status"], "failed")
            state = self.configure(state, mode="keep")
            self.execute(state, 422)
        self.evidence("08b 失败与格式", "空文件/未闭合引号/XLSX/NUL均明确失败且不能执行", "4种输入逐项阻止，无替代表")

    def test_09_confirmations_and_structure(self):
        state = self.contacts()
        self.execute(state, 422, {"confirmed": True})
        state["plan"]["mapping"]["A"]["不存在的列"] = {"target": "伪造", "transform": "identity"}
        state = self.request(f"/api/sessions/{state['id']}/plan", {"revision": state["revision"], "plan": state["plan"]})
        self.execute(state, 422, {"mapping": True, "grain": True, "link": True})
        self.evidence("09 条件检查", "一句已确认、伪造列均不能执行", "两个请求均422，未生成结果")

    def test_10_formats_and_transform(self):
        from integration.csv_io import read_upload
        f = read_upload("A", "custom.csv", (DATA / "09_formats/gb18030.csv").read_bytes())
        self.assertEqual((f["status"], f["encoding"], f["delimiter"]), ("ok", "gb18030", ";"))
        self.assertEqual(f["rows"][0]["values"]["备注"], "中文")
        multi = read_upload("A", "custom.csv", (DATA / "09_formats/multiline.csv").read_bytes())
        self.assertEqual((multi["rows"][0]["line"], multi["rows"][0]["end_line"], multi["rows"][1]["line"]), (2, 3, 4))
        self.assertEqual(multi["rows"][0]["values"]["文本"], "第一行\r\n第二行")
        state = self.uploaded("09_formats", "spaced.csv", "spaced.csv")
        state = self.configure(state, keys=["编号"])
        state["plan"]["mapping"]["A"]["编号"]["transform"] = "strip"
        state["plan"]["mapping"]["B"]["编号"]["transform"] = "strip"
        state = self.request(f"/api/sessions/{state['id']}/plan", {"revision": state["revision"], "plan": state["plan"]})
        self.execute(state, 422, {"mapping": True, "grain": True, "link": True})
        result = self.execute(state)["result"]
        self.assertEqual(result["records"][0]["values"]["编号"], "001")
        self.assertEqual(result["audit"]["conversions"][0]["raw"], " 001 ")
        self.evidence("10 编码/格式/原值", "记录GB18030与分号修复、多行物理行号；转换需独立确认且留原值", {"repairs": f["repairs"], "conversions": result["audit"]["conversions"]})

    def test_11_export_and_persistence(self):
        state = self.execute(self.contacts())
        content = self.request(f"/api/sessions/{state['id']}/download", raw=True)
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
        self.assertEqual(rows, state["result"]["export_rows"])
        reloaded = SessionStore(self.state_dir).load(state["id"])
        self.assertEqual(reloaded, state)
        old = state["revision"]
        changed = self.request(f"/api/sessions/{state['id']}/invalidate", {"revision": old})
        self.assertIsNone(changed["result"])
        self.request(f"/api/sessions/{state['id']}/download", status=409)
        self.evidence("11 导出与状态", "CSV逐字段等于预览数据；磁盘恢复相同；编辑后下载409", {"rows_equal": True, "persisted_equal": True, "invalidated": True})

    def test_12_model_disabled_and_validation(self):
        config = self.request("/api/config")
        self.assertFalse(config["model"]["enabled"])
        state = self.uploaded("01_synonyms")
        advice = self.request(f"/api/sessions/{state['id']}/suggest", {"revision": state["revision"]})
        self.assertEqual(advice["suggestion"]["status"], "disabled")
        self.assertIsNone(advice["result"])
        with self.assertRaises(ValueError):
            validate_advice({"mappings": [{"file": "A", "column": "虚构列", "target": "X"}]}, state["files"])
        self.evidence("12 模型边界", "未配置不伪造回复；未知列建议拒绝", "disabled，未调用模型；结构验证拒绝未知列（不是语义模型验收）")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Acceptance)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    output = ROOT / "outputs"
    output.mkdir(exist_ok=True)
    payload = {"tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "cases": REPORT, "not_run": ["真实模型接口和语义建议质量；未配置接口，不使用模拟回复代替", "真实平台数据、生产认证、多用户和大规模性能", "任意文档解析、成绩计算、规则检索（不属于本轮）"]}
    (output / "interactive_acceptance.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["交互式最小版本实际验收", "", f"Python {sys.version.split()[0]}；{result.testsRun}项，失败{len(result.failures)}，错误{len(result.errors)}。", "",
        "实际命令：python -X utf8 tests/generate_fixtures.py；python -X utf8 tests/test_interactive.py。生成器独立运行，验收脚本只读已有测试文件。", "",
        "| 案例 | 预期 | 实际 |", "| --- | --- | --- |"]
    for item in REPORT:
        actual = json.dumps(item["actual"], ensure_ascii=False) if not isinstance(item["actual"], str) else item["actual"]
        lines.append("| " + " | ".join(str(v).replace("|", "\\|").replace("\n", " ") for v in (item["case"], item["expected"], actual)) + " |")
    lines += ["", "未运行：", ""] + ["- " + item for item in payload["not_run"]]
    (output / "interactive_acceptance.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.exit(0 if result.wasSuccessful() else 1)
