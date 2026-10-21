"""Reviewable repair report, based on completed backend and actual browser results."""
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs'


def read(name):
    return json.loads((OUT/name).read_text(encoding='utf-8'))


backend = read('teacher_backend_acceptance.json')
two = read('two_stage_acceptance.json')
general = read('interactive_acceptance.json')
browser = read('checkpoint_fix_browser.json')
prior_browser = read('two_stage_browser.json')
general_browser = read('browser_acceptance.json')
existing = read('checkpoint-existing-flow-check.json')
state = read('checkpoint-fixed-state.json')
for result in (backend, two, general):
    assert result['failures'] == result['errors'] == 0
assert len(browser['checks']) == 8 and not browser.get('error') and not browser['page_errors']
assert len(prior_browser['checks']) == 15 and not prior_browser.get('error') and not prior_browser['page_errors']
assert len(general_browser['checks']) == 9
assert existing['all_existing_workflow_hashes_unchanged']
assert browser['original_files_unchanged']
backend_count = sum(r['tests_run'] for r in (backend, two, general))
browser_count = sum(len(r['checks']) for r in (browser, prior_browser, general_browser))
lines = ['待确认与规则保存：修复及实际验收', '', f'生成时间：{datetime.now().astimezone().isoformat(timespec="seconds")}。', '',
    f'通过{backend_count}项Python/HTTP检查（教师{backend["tests_run"]}、双阶段{two["tests_run"]}、通用{general["tests_run"]}），{browser_count}组真实Edge浏览器检查（诊断专项8、教师15、通用9）。浏览器JavaScript错误0。', '',
    '已修复的行为：', '',
    '- 问题卡片直接说明哪条82分记录缺时间、另一条99分记录有什么时间，为什么无法确定最后有效提交，以及需要补充什么。',
    '- 提供补充时间、明确裁决、保持待确认三个操作。待确认隐藏并禁用裁决值与时间输入，按钮明确表示暂不计算，反馈采用待处理提示。',
    '- 相同待确认备注和相同待保存规则重复提交不增加版本或事件。旧重复记录仅合并显示，原审计完整保留。',
    '- 提交时间须指向此问题实际记录和实际配置的时间列，提供日期、时分及依据。无效日期、仅日期、不存在记录、缺少依据均由后端独立拒绝。时间不足不按行序或推断的零点选择。',
    '- 输入“不填”、空值、NaN、Infinity时给出可操作提示。0保持有效。错误后保留草稿，修改相关输入后清除过时错误提示。',
    '- 数据阻断时先验证、保存新权重或明确追加指令；刷新恢复最新规则，数据问题解决后实际采用。无效新规则不覆盖已存规则，前端在收到保存确认后才清除草稿。', '',
    '实际按钮和响应检查：', '', '| 场景 | 预期 | 实际 |', '| --- | --- | --- |']


def cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


for c in browser['checks']:
    lines.append('| ' + ' | '.join(cell(v) for v in (c['name'], c['expected'], json.dumps(c['actual'], ensure_ascii=False))) + ' |')
lines += ['', '后端额外校验（含旧流程兼容）：', '', '| 检查 | 预期 | 实际 |', '| --- | --- | --- |']
for c in backend['cases'][11:]:
    lines.append('| ' + ' | '.join(cell(v) for v in (c['case'], c['expected'], json.dumps(c['actual'], ensure_ascii=False))) + ' |')
lines += ['', '原流程只读恢复检查：', '',
    f'- 原第{existing["old_revision"]}版流程仍为第{existing["old_revision"]}版；未替教师确认身份、补时间或改成绩。',
    f'- 原审计{existing["old_audit_pending_records"]}条待确认记录保留；页面合并显示为{existing["displayed_history_groups"]}组备注。',
    '- 正式服务仅GET恢复检查；重启前后的所有已有workflow.json SHA-256一致。独立浏览器本地模拟恢复旧“不填”草稿后，值输入隐藏且禁用。', '',
    '实际运行命令：', '', '```powershell', f"Set-Location '{ROOT}'",
    'python -X utf8 tests/test_teacher_pipeline.py', 'python -X utf8 tests/test_two_stage.py', 'python -X utf8 tests/test_interactive.py',
    '# 独立测试服务进程，不写入正式state目录', 'python -u -X utf8 tests/serve_teacher_browser.py',
    "$env:INTEGRATION_TEST_URL = 'http://127.0.0.1:8766'", 'node tests/checkpoint_browser.cjs', 'node tests/two_stage_browser.cjs', 'node tests/browser_acceptance.cjs',
    'python -X utf8 tests/write_checkpoint_report.py', 'git diff --check', '```', '',
    '正式页面：http://127.0.0.1:8765/pipeline。已用Start-Process -WindowStyle Hidden重启正式app.py载入修复；刷新原页面即可，无需重传。独立故障注入测试服务在检查完成后停止。', '',
    '本轮输入与产物：', '', f'- 实际输入：{backend["input_directory"]}下三组桌面CSV，原文件未覆盖。',
    '- 诊断专项使用第三组。补时间的验收是假设第7行HW-001-1实际晚于第41行，仅在测试副本中执行；没有替教师对原流程作出该裁决。',
    f'- 补时间成功样例阶段一目录：{state["stage1"]["directory"]}。',
    f'- 阶段二目录：{state["stage2"]["directory"]}。',
    f'- {OUT/"checkpoint-fixed-dwd.csv"}：按补充时间重新整理的40人宽表。',
    f'- {OUT/"checkpoint-fixed-dws.csv"}：实际采用50/30/20计算的40人结果。',
    f'- {OUT/"checkpoint-fixed-audit.json"}：原第7行时间仍为空，补充时间和依据独立进入conversions及plan.timestamp_resolutions。',
    f'- {OUT/"checkpoint-fixed-state.json"}：该测试流程的持久状态副本。',
    '- 这两个CSV经浏览器真实下载后由Python csv解析，与当前API及页面预览逐字段核对。',
    '- outputs/checkpoint_fix_browser.json、teacher_backend_acceptance.json保存逐项机器记录；checkpoint-existing-flow-check.json保存原流程只读核对。截图仅辅助视觉检查。', '',
    '实现位置：', '', '| 功能 | 位置 |', '| --- | --- |',
    '| 具体问题说明、时间来源与人工输入检查 | pipeline/interaction.py：issue_guidance、validate_plan |',
    '| 保存/未执行反馈、重复备注防护、新规则持久化 | pipeline/service.py：resolve、resume |',
    '| 提交时间严格解析 | pipeline/common.py：submission_time |',
    '| 使用补充时间归并、原时间和依据留痕 | pipeline/dwd.py：materialize、aggregate |',
    '| 模式切换、输入隐藏/禁用、错误草稿、历史合并 | web/pipeline.js：drawCards、updateCardMode、renderConfirmationHistory |',
    '| 规则保存确认、已存规则及未保存草稿说明 | web/pipeline.js：drawRules、resume-pipeline处理器 |',
    '| 模型不能冒充教师补充时间 | pipeline/advisor.py：manual_keys及请求信息脱敏 |', '',
    '限制与未运行：', '',
    '- 真实LLM未配置，模型联网与语义质量未运行；没有使用模拟模型回复。',
    '- CSV和标准库/原生前端范围不变，未扩展平台或其他文件格式；最新时间排序需要可解析、带时分且时区一致的提交时间。',
    '- 非Edge、移动端、生产多用户权限及压力测试未运行。',
    '- 旧版已经丢弃且未记录的权重草稿无法凭空恢复；本修复保证新提交持久化。旧“不填”等无效草稿仍保留，选择明确裁决时必须修正。',
    '- 不会自动采用82或99，也不会替教师确认真实提交时间；该选择须由教师提供依据。']
(OUT/'checkpoint_fixed_acceptance.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(OUT/'checkpoint_fixed_acceptance.md')
