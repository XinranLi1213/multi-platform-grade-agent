"""Teacher UX contracts over real HTTP and real deterministic Python workers."""
import base64
import copy
import csv
import io
import json
import subprocess
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from test_two_stage import TwoStage
from pipeline.common import sha
from pipeline.service import PipelineStore

DESKTOP = Path.home() / "Desktop" / "多平台试用数据_20261021_163831"
DATA = DESKTOP if DESKTOP.is_dir() else Path(__file__).parent / "teacher_trial_data"
REPORT = []
ORIGINAL = {str(p): sha(p) for p in DATA.rglob("*.csv")}


class Teacher(TwoStage):
    def group(self, number, transform=None):
        directory = sorted(p for p in DATA.iterdir() if p.is_dir())[number-1]
        task = (directory / "任务与预期.txt").read_text(encoding="utf-8-sig").split("可复制任务：")[1].split("预期：")[0].strip()
        uploads = [(p.name, p.read_bytes()) for p in sorted(directory.glob("*.csv"))]
        if transform:
            uploads = transform(uploads)
        return self.api("/api/pipelines", {"task": task, "files": [{"name": n, "content_base64": base64.b64encode(b).decode()} for n, b in uploads]}, 201)

    def resolve(self, state, decision, status=200):
        return self.api(f"/api/pipelines/{state['id']}/resolve", {"revision": state["revision"], "decision": decision}, status)

    def record(self, name, expected, actual):
        REPORT.append({"case": name, "expected": expected, "actual": actual, "status": "passed"})

    def test_teacher_01_complete_and_values(self):
        s = self.group(1)
        self.assertEqual(s["phase"], "complete", s["issues"])
        self.assertEqual(len(s["stage2"]["rows"]), 40)
        self.assertTrue(all(r["student_id"].startswith("00") for r in s["stage1"]["rows"]))
        self.assertTrue(any(r[m] == "0" for r in s["stage1"]["rows"] for m in ("实验", "作业", "测验")))
        self.assertTrue(any(r["总评"] == "缓考" for r in s["stage2"]["rows"]))
        for stage, kind in (("stage1", "dwd"), ("stage2", "dws")):
            content = self.api(f"/api/pipelines/{s['id']}/download?kind={kind}", raw=True)
            self.assertEqual(list(csv.DictReader(io.StringIO(content.decode("utf-8-sig")))), s[stage]["rows"])
        (ROOT / "outputs" / "teacher_full_state.json").write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        self.record("01 桌面完整流程", "不提供JSON，连续执行，输出40人，0/前导零/特殊状态与CSV正确", {"phase": s["phase"], "counts": s["stage1"]["counts"], "run_id": s["id"]})

    def test_teacher_02_identity_invalid(self):
        s = self.group(2); q = s["issues"][0]
        self.assertEqual([(c["student_id"], c["master"]["class"]) for c in q["candidates"]], [("0020260001", "软件工程2601班"), ("0020260002", "软件工程2602班")])
        messages = []
        for sid, reason, expected in (("", "已确认", "请选择主花名册中的编号"), ("0020260002", "", "请填写核对依据"), ("999", "教师核对原始名册", "请选择主花名册中的编号")):
            result = self.resolve(s, {"issue_id": q["id"], "student_id": sid, "reason": reason}, 422)
            self.assertEqual(result["problem"]["reason"], expected); messages.append(result["problem"])
            self.assertEqual(self.api('/api/pipelines/'+s['id'])["revision"], s["revision"])
        plan = copy.deepcopy(s["plan"]); plan["identity_resolutions"]["F3:99999"] = {"student_id": "0020260002", "reason": "核验"}
        self.api(f"/api/pipelines/{s['id']}/resume", {"revision": s["revision"], "plan": plan}, 422)
        self.record("02 后端独立身份校验", "空编号、空依据、无效编号和不存在源记录分别拒绝，不执行子进程或改版本", messages)

    def test_teacher_03_pending_and_confirm(self):
        s = self.group(2); q = s["issues"][0]
        rawdir = self.state_root / "pipelines" / s["id"] / "input"
        before = {p.name: sha(p) for p in rawdir.iterdir()}
        pending = self.resolve(s, {"issue_id": q["id"], "action": "pending", "reason": "等待名册核对"})
        self.assertEqual(pending["issues"], s["issues"]); self.assertIsNone(pending["stage2"])
        self.assertEqual(PipelineStore(self.state_root).load(s["id"])["pending_decisions"][q["id"]]["action"], "pending")
        ready = self.resolve(pending, {"issue_id": q["id"], "student_id": "0020260002", "reason": "核对软件工程2602班原始名册，编号0020260002"})
        self.assertEqual(ready["phase"], "complete", ready["issues"])
        self.assertEqual(before, {p.name: sha(p) for p in rawdir.iterdir()})
        self.assertTrue(any(e["action"] == "teacher_decision" for e in ready["events"]))
        self.resolve(pending, {"issue_id": q["id"], "student_id": "0020260002", "reason": "再次点击"}, 409)
        audit = json.loads((Path(ready["stage1"]["directory"]) / "audit.json").read_text(encoding="utf-8"))
        self.assertEqual(audit["identity_actions"][-1]["student_id"] if audit["identity_actions"][-1]["method"] == "teacher_confirmation" else ready["plan"]["identity_resolutions"]["F3:22"]["student_id"], "0020260002")
        self.record("03 待确认与恢复", "待确认保留源记录并阻止DWS；明确编号及依据后同一上传继续，重复版本409", {"pending": pending["phase"], "after": ready["phase"], "raw_hashes_unchanged": True})

    def test_teacher_04_missing_timestamp(self):
        s = self.group(3); q = s["issues"][0]
        self.assertIn("提交时间", q["reason"])
        self.assertEqual([r["line"] for r in q["records"]], [7, 41])
        self.assertEqual(q["records"][0]["values"]["提交时间"], "")
        self.assertEqual(s["stage1"]["rows"][0]["作业"], "")
        r = self.resolve(s, {"issue_id": q["id"], "value": "99", "reason": "核查平台有效提交HW-001-2为99分，确认该次为最后有效作业"})
        self.assertEqual(r["phase"], "complete", r["issues"])
        self.assertEqual(r["stage1"]["rows"][0]["作业"], "99")
        self.record("04 缺少提交时间", "展示第7/41行两次提交，未裁决时不猜；教师明确99后恢复", {"before": s["phase"], "after": r["phase"], "rows": [r["line"] for r in q["records"]]})

    def test_teacher_05_plan_changes(self):
        s = self.group(1); plan = copy.deepcopy(s["plan"])
        preview = self.api(f"/api/pipelines/{s['id']}/preview-plan", {"revision": s["revision"], "plan": plan})
        self.assertEqual(preview["changes"], [])
        noop = self.api(f"/api/pipelines/{s['id']}/apply-plan", {"revision": s["revision"], "plan": plan, "confirmation": preview["token"]})
        self.assertEqual(noop["revision"], s["revision"])
        plan["platforms"][1]["metrics"][0]["strategy"] = "confirm"
        rejected = self.api(f"/api/pipelines/{s['id']}/resume", {"revision": s["revision"], "plan": plan}, 422)
        self.assertEqual(rejected['problem']['reason'], '请填写调整归并方式的依据')
        preview = self.api(f"/api/pipelines/{s['id']}/preview-plan", {"revision": s["revision"], "plan": plan})
        self.api(f"/api/pipelines/{s['id']}/apply-plan", {"revision": s["revision"], "plan": plan, "confirmation": preview["token"]}, 422)
        r = self.api(f"/api/pipelines/{s['id']}/apply-plan", {"revision": s["revision"], "plan": plan, "confirmation": preview["token"], "reason": "教师需逐条核对重复实验，暂不采用最高分"})
        self.assertNotEqual(r["stage1"]["manifest_sha256"], s["stage1"]["manifest_sha256"]); self.assertIsNone(r["stage2"])
        self.api(f"/api/pipelines/{s['id']}/download?kind=dws", status=409)
        self.record("05 整理方式变更", "无变更不生成；变更须摘要确认与依据，新DWD使旧DWS失效", {"changes": preview["changes"], "after": r["phase"], "old_calculation_invalid": r["stage2"] is None})

    def test_teacher_06_weights_and_latest_instruction(self):
        s = self.group(1); before = {p.name: p.read_bytes() for p in Path(s["stage1"]["directory"]).iterdir()}
        r = self.resume(s, instruction="实验50%，作业30%，测验20%")
        self.assertEqual(r["phase"], "complete", r["issues"])
        self.assertEqual(r["rules"]["weights"], {"实验": "0.5", "作业": "0.3", "测验": "0.2"})
        self.assertEqual(before, {p.name: p.read_bytes() for p in Path(r["stage1"]["directory"]).iterdir()})
        self.assertNotEqual(r["stage2"]["directory"], s["stage2"]["directory"])
        self.record("06 新规则实际执行", "resume指令也进入新规则，不静默沿用旧权重；DWD逐字节不变", {"rules": r["rules"], "dwd_unchanged": True})

    def test_teacher_07_unknown_fault_and_retry(self):
        s = self.group(1)
        with patch('pipeline.service.generate_and_run', side_effect=RuntimeError('验收注入：未知执行故障')):
            failed = self.trial(s, instruction="实验50%，作业30%，测验20%")
        self.assertEqual(failed["phase"], "system_error"); self.assertEqual(failed["issues"][0]["kind"], "system"); self.assertIsNone(failed["stage2"])
        self.api(f"/api/pipelines/{s['id']}/download?kind=dws", status=409)
        retried = self.resume(failed)
        self.assertEqual(retried["phase"], "complete", retried["issues"])
        with patch('pipeline.service.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', '验收注入：worker crash')):
            crashed = self.group(1)
        self.assertEqual(crashed["phase"], "system_error"); self.assertIsNone(crashed["stage1"])
        self.record("07 未知故障", "未知异常和子进程崩溃标为系统故障，无旧结果；解除注入后可重试", {"dws_fault": failed["phase"], "dwd_fault": crashed["phase"], "retry": retried["phase"]})

    def test_teacher_08_multiple_questions(self):
        def change(uploads):
            n, raw = uploads[2]; rows = list(csv.reader(io.StringIO(raw.decode('utf-8-sig')))); header = rows[2]
            row = next(r for r in rows[3:] if len(r) == len(header) and r[0] == '0020260003')
            row[0] = ''; row[2] = ''
            buf = io.StringIO(newline=''); csv.writer(buf).writerows(rows); uploads[2] = (n, buf.getvalue().encode('utf-8-sig')); return uploads
        s = self.group(2, change)
        self.assertEqual(len(s["issues"]), 2)
        q = next(q for q in s["issues"] if q["records"][0]["values"]["姓名"] == '张伟')
        r = self.resolve(s, {"issue_id": q["id"], "student_id": "0020260002", "reason": "教师核对2602班名册"})
        self.assertEqual(len(r["issues"]), 1); self.assertIsNone(r["stage2"])
        q = r["issues"][0]
        done = self.resolve(r, {"issue_id": q["id"], "student_id": "0020260003", "reason": "教师核对原导出提交编号HW-003-1"})
        self.assertEqual(done["phase"], "complete", done["issues"])
        self.record("08 多条逐项确认", "2→1→0条；未全部解决不执行DWS，原始记录全部留审计", {"remaining": [len(s["issues"]), len(r["issues"]), len(done["issues"])]})

    def test_teacher_09_preflight_and_old_state(self):
        s = self.group(1); plan = copy.deepcopy(s["plan"]); plan["platforms"][1]["id_column"] = '不存在列'
        bad = self.api(f"/api/pipelines/{s['id']}/preview-plan", {"revision": s["revision"], "plan": plan}, 422)
        self.assertEqual(bad["problem"]["field"], 'id_column')
        self.assertEqual(bad["problem"]["file_id"], 'F2')
        old = self.server.pipelines.load(s["id"]); old.pop('view', None)
        path = self.server.pipelines.directory(s['id']) / 'workflow.json'; path.write_text(json.dumps(old, ensure_ascii=False), encoding='utf-8')
        self.assertEqual(len(self.api('/api/pipelines/'+s['id'])["view"]["tables"]), 4)
        self.record("09 预检与旧状态", "不存在列执行前指向F2及编号字段；旧workflow不含view仍可恢复", bad["problem"])

    def test_teacher_10_exact_percentages_and_summary(self):
        s = self.group(1)
        form = copy.deepcopy(s["rules"]); form.pop('weights')
        form['weights_percent'] = {"实验": "33.3", "作业": "33.3", "测验": "33.4"}
        prepared = self.api(f"/api/pipelines/{s['id']}/prepare-rules", {"revision": s['revision'], "rules": form})
        self.assertEqual(prepared['rules']['weights'], {"实验": "0.333", "作业": "0.333", "测验": "0.334"})
        r = self.resume(s, rules=prepared['rules'])
        self.assertEqual(r['phase'], 'complete', r['issues'])
        self.assertEqual(r['view']['weight_percentages'], {"实验": "33.300", "作业": "33.300", "测验": "33.400"})
        self.record("10 小数权重", "33.3/33.3/33.4由Python Decimal转为权重，避免浏览器浮点误差", prepared['rules']['weights'])

    def test_teacher_11_conflicting_natural_rules(self):
        s = self.group(1)
        for instruction in ('实验40%，实验50%，作业30%，测验20%', '未交记0，未交保留'):
            s = self.trial(s, instruction=instruction)
            self.assertEqual(s['phase'], 'business_checkpoint')
            self.assertIn('冲突', s['issues'][0]['reason'])
            self.assertIsNone(s['stage2'])
        self.record("11 规则冲突", "同一项目不同权重、同一状态记0与保留冲突均暂停，不选任一规则", s['issues'])

    def test_teacher_12_pending_is_idempotent(self):
        s = self.group(3)
        decision = {'issue_id': 'Q1', 'action': 'pending', 'value': '不填', 'reason': '核对原始花名册'}
        pending = self.resolve(s, decision)
        self.assertIsNone(pending['stage2']); self.assertEqual(len(pending['issues']), 1)
        self.assertNotIn('value', pending['pending_decisions']['Q1'])
        decision['value'] = '99'
        again = self.resolve(pending, decision)
        self.assertEqual(again['revision'], pending['revision'])
        self.assertEqual(len(again['events']), len(pending['events']))
        self.assertEqual(again['feedback']['code'], 'pending_unchanged')
        self.assertEqual(again['plan']['cell_resolutions'], {})
        self.record('12 截图重复待确认', '仅保存备注，不采用不填/99；相同备注重复提交不增加版本或审计', {'revision': again['revision'], 'feedback': again['feedback'], 'remaining': len(again['issues'])})

    def test_teacher_13_numeric_errors_are_actionable(self):
        s = self.group(3)
        for value in ('不填', '', 'NaN', 'Infinity'):
            rejected = self.resolve(s, {'issue_id': 'Q1', 'value': value, 'reason': '核对原始花名册'}, 422)
            self.assertIn('裁决' if value == '' else '成绩数值', rejected['problem']['reason'])
            self.assertEqual(self.api('/api/pipelines/'+s['id'])['revision'], s['revision'])
        done = self.resolve(s, {'issue_id': 'Q1', 'value': '0', 'reason': '验收模拟明确裁决0分，验证有效零值'})
        self.assertEqual(done['phase'], 'complete'); self.assertEqual(done['stage1']['rows'][0]['作业'], '0')
        self.record('13 非数值与有效0', '空值/不填/NaN/Infinity明确拒绝，0仍有效', {'invalid': 4, 'zero': done['stage1']['rows'][0]['作业']})

    def test_teacher_14_timestamp_validation(self):
        s = self.group(3)
        good = {'issue_id': 'Q1', 'action': 'timestamps', 'reason': '核对平台提交日志', 'timestamps': [{'record_id': 'F3:7', 'value': '2026-10-20T14:00:00'}]}
        cases = [dict(good, reason=''), dict(good, timestamps=[]), dict(good, timestamps=[{'record_id': 'F3:99999', 'value': '2026-10-20T14:00:00'}]),
                 dict(good, timestamps=[{'record_id': 'F3:7', 'field': '不存在列', 'value': '2026-10-20T14:00:00'}]),
                 dict(good, timestamps=[{'record_id': 'F3:7', 'value': '昨天'}]), dict(good, timestamps=[{'record_id': 'F3:7', 'value': '2026-02-30T14:00:00'}]),
                 dict(good, timestamps=[{'record_id': 'F3:7', 'value': '2026-10-19'}])]
        for decision in cases:
            result = self.resolve(s, decision, 422)
            self.assertEqual(result['problem']['kind'], 'submission_time')
            self.assertEqual(self.api('/api/pipelines/'+s['id'])['revision'], s['revision'])
        self.record('14 时间独立校验', '空依据、无修改、错误记录/字段、无效日期均拒绝且保留原版本', {'rejected': len(cases)})

    def test_teacher_15_time_correction_uses_saved_rules(self):
        from decimal import ROUND_HALF_UP
        s = self.group(3)
        raw_dir = self.state_root/'pipelines'/s['id']/'input'
        raw_hashes = {p.name: sha(p) for p in raw_dir.iterdir()}
        original_dwd = {p.name: p.read_bytes() for p in Path(s['stage1']['directory']).iterdir()}
        rules = copy.deepcopy(s['rules']); rules['weights'] = {'实验': '0.5', '作业': '0.3', '测验': '0.2'}
        saved = self.resume(s, rules=rules)
        self.assertEqual(saved['phase'], 'data_checkpoint'); self.assertEqual(saved['rules'], rules)
        self.assertIsNone(saved['stage2'])
        self.assertEqual(original_dwd, {p.name: p.read_bytes() for p in Path(saved['stage1']['directory']).iterdir()})
        restored = PipelineStore(self.state_root).load(s['id'])
        result = self.resolve(restored, {'issue_id': 'Q1', 'action': 'timestamps', 'reason': '验收模拟核对平台日志：HW-001-1实际在20日提交', 'timestamps': [{'record_id': 'F3:7', 'value': '2026-10-20T14:00:00'}]})
        self.assertEqual(result['phase'], 'complete', result['issues'])
        self.assertEqual(result['stage1']['rows'][0]['作业'], '82')
        self.assertEqual(result['rules'], rules)
        row = result['stage1']['rows'][0]
        expected = sum(Decimal(row[k])*Decimal(v) for k,v in rules['weights'].items()).quantize(Decimal('.1'), rounding=ROUND_HALF_UP)
        self.assertEqual(Decimal(result['stage2']['rows'][0]['总评']), expected)
        audit = json.loads((Path(result['stage1']['directory'])/'audit.json').read_text(encoding='utf-8'))
        self.assertEqual(audit['source_ledger']['F3:7']['source']['values']['提交时间'], '')
        self.assertTrue(any(c['record_id']=='F3:7' and c['field']=='提交时间' and c['raw']=='' and c['value']=='2026-10-20T14:00:00' for c in audit['conversions']))
        self.assertEqual(raw_hashes, {p.name: sha(p) for p in raw_dir.iterdir()})
        self.record('15 补时间后按新规则续算', '50/30/20先保存；补第7行20日时间后Python选82，保留原空时间/依据/原CSV', {'assignment': row['作业'], 'total': str(expected), 'rules': result['rules']['weights'], 'original_input_unchanged': True})

    def test_teacher_16_pending_rule_saves_and_rejections(self):
        s = self.group(3)
        invalid = copy.deepcopy(s['rules']); invalid['weights'] = {'实验': '.4', '作业': '.3', '测验': '.2'}
        self.api(f"/api/pipelines/{s['id']}/resume", {'revision': s['revision'], 'rules': invalid}, 422)
        self.assertEqual(self.api('/api/pipelines/'+s['id'])['rules'], s['rules'])
        rules = copy.deepcopy(s['rules']); rules['weights'] = {'实验': '.5', '作业': '.3', '测验': '.2'}
        saved = self.resume(s, rules=rules)
        self.assertEqual(saved['feedback']['code'], 'rules_saved_pending')
        repeat = self.resume(saved, rules=rules)
        self.assertEqual(repeat['revision'], saved['revision'])
        self.assertEqual(repeat['rules'], rules)
        repeat_without_rule = self.resume(repeat)
        self.assertEqual(repeat_without_rule['revision'], saved['revision'])
        self.record('16 阻断时规则保存', '无效规则不覆盖；有效规则持久化；重复规则或空执行请求不增加版本', {'rules': saved['rules']['weights'], 'revision': saved['revision'], 'noop': repeat_without_rule['feedback']})

    def test_teacher_17_pending_natural_instruction(self):
        s = self.group(3)
        updated = self.trial(s, instruction='实验50%，作业30%，测验20%')
        self.assertEqual(updated['phase'], 'data_checkpoint')
        self.assertEqual(updated['rules']['weights'], {'实验': '0.5', '作业': '0.3', '测验': '0.2'})
        self.assertEqual(updated['stage1']['manifest_sha256'], s['stage1']['manifest_sha256'])
        self.api(f"/api/pipelines/{s['id']}/trial", {'revision': updated['revision'], 'instruction': '把公式改得合理些'}, 422)
        done = self.resolve(updated, {'issue_id': 'Q1', 'value': '99', 'reason': '验收模拟明确采用99分'})
        self.assertEqual(done['phase'], 'complete'); self.assertEqual(done['rules'], updated['rules'])
        self.record('17 阻断时追加指令', '明确指令先保存，无法解析则保留规则和数据；裁决后沿用保存的规则', done['rules']['weights'])

    def test_teacher_18_rules_and_blocked_plan_together(self):
        s = self.group(1); plan = copy.deepcopy(s['plan']); plan['platforms'][1]['metrics'][0]['strategy'] = 'confirm'
        rules = copy.deepcopy(s['rules']); rules['weights'] = {'实验': '.5', '作业': '.3', '测验': '.2'}
        blocked = self.resume(s, plan=plan, rules=rules, reason='验收模拟需要逐条核对实验')
        self.assertEqual(blocked['phase'], 'data_checkpoint'); self.assertEqual(blocked['rules'], rules)
        self.assertIsNone(blocked['stage2'])
        self.record('18 同时修改计划与规则', '新计划产生数据问题时，同请求中已确认的规则仍保存，旧结果失效', {'phase': blocked['phase'], 'weights': blocked['rules']['weights']})

    def test_teacher_19_legacy_pending_and_guidance(self):
        s = self.group(3)
        for _ in range(3):
            s['events'].append({'action': 'kept_pending', 'revision': s['revision'], 'issue': copy.deepcopy(s['issues'][0]), 'decision': {'issue_id': 'Q1', 'action': 'pending', 'value': '不填', 'reason': '核对原始花名册'}})
        s['pending_decisions'] = {'Q1': copy.deepcopy(s['events'][-1]['decision'])}
        self.server.pipelines.save(s)
        loaded = self.api('/api/pipelines/'+s['id'])
        guidance = loaded['view']['issue_guidance'][0]
        self.assertIn('最后有效提交', guidance['explanation']); self.assertEqual(guidance['timestamps'][0]['original'], '')
        same = self.resolve(loaded, loaded['pending_decisions']['Q1'])
        self.assertEqual(same['revision'], loaded['revision']); self.assertEqual(len(same['events']), len(loaded['events']))
        finished = self.resolve(same, {'issue_id': 'Q1', 'value': '99', 'reason': '验收模拟后续明确裁决99'})
        self.assertEqual(finished['pending_decisions'], {})
        self.record('19 旧流程兼容', '旧待确认数据仍可读，提供明确说明；原审计保留，不再重复添加相同备注', {'title': guidance['title'], 'legacy_events_preserved': True})

    def test_teacher_20_pending_survives_question_renumbering(self):
        def change(uploads):
            name, raw = uploads[2]; rows = list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))
            row = next(r for r in rows[3:] if len(r)==6 and r[0]=='0020260003')
            row[0]=''; row[2]=''; buf=io.StringIO(newline=''); csv.writer(buf).writerows(rows)
            uploads[2]=(name,buf.getvalue().encode('utf-8-sig')); return uploads
        s = self.group(2, change)
        first, second = s['issues']
        sid = lambda q: '0020260002' if q['records'][0]['values']['提交编号']=='HW-002-1' else '0020260003'
        pending = self.resolve(s, {'issue_id': second['id'], 'action': 'pending', 'reason': '等待核对第二条提交'})
        one_left = self.resolve(pending, {'issue_id': first['id'], 'student_id': sid(first), 'reason': '验收核对第一条身份'})
        self.assertEqual(len(one_left['issues']), 1)
        remaining = one_left['issues'][0]
        self.assertEqual(remaining['id'], 'Q1')
        repeated = self.resolve(one_left, {'issue_id': remaining['id'], 'action': 'pending', 'reason': '等待核对第二条提交'})
        self.assertEqual(repeated['revision'], one_left['revision'])
        done = self.resolve(repeated, {'issue_id': remaining['id'], 'student_id': sid(second), 'reason': '验收核对第二条身份'})
        self.assertEqual(done['phase'], 'complete'); self.assertEqual(done['pending_decisions'], {})
        self.assertEqual(sum(e['action']=='kept_pending' for e in done['events']), 1)
        self.record('20 问题编号变化', '处理第一条后按源记录识别第二条，重复备注不增事件；完成后清除待确认状态，审计保留', {'phase': done['phase'], 'pending': done['pending_decisions']})


if __name__ == '__main__':
    suite = unittest.TestSuite(Teacher(name) for name in unittest.defaultTestLoader.getTestCaseNames(Teacher) if name.startswith('test_teacher_'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    unchanged = ORIGINAL == {str(p): sha(p) for p in DATA.rglob('*.csv')}
    payload = {"tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors), "input_directory": str(DATA), "original_hashes_unchanged": unchanged, "original_hashes": ORIGINAL, "cases": REPORT}
    (ROOT/'outputs'/'teacher_backend_acceptance.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    sys.exit(0 if result.wasSuccessful() and unchanged else 1)
