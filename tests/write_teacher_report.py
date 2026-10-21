"""Build the delivery report from completed checks, never infer success from screenshots."""
import json
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs'


def read(name):
    return json.loads((OUT / name).read_text(encoding='utf-8'))


backend = read('teacher_backend_acceptance.json')
two = read('two_stage_acceptance.json')
generic = read('interactive_acceptance.json')
browser = read('two_stage_browser.json')
old_browser = read('browser_acceptance.json')
demo = read('teacher_demo/state.json')
for result in (backend, two, generic):
    assert result['failures'] == result['errors'] == 0, result
assert len(browser['checks']) == 15 and not browser.get('error') and browser['page_errors'] == []
assert len(old_browser['checks']) == 9
assert backend['original_hashes_unchanged'] and browser['original_hashes_unchanged']
assert all(c['status'] == 'passed' for c in browser['checks'])

rows = [
    ('1 完整执行', '普通流程无需JSON，输出40人', '第一组实际自动完成；4表190条业务记录→40人，归并30条重复提交，3条无编号记录精确对齐，190条全部留来源审计。'),
    ('2 同名候选与卡片提交', '两名候选展示编号/班级，不预选；卡内确认继续', '第22行张伟：0020260001/软件工程2601班、0020260002/软件工程2602班；初始选择为空；选择0020260002并填写依据后，同一流程完成40人。'),
    ('3 非法身份', '空选择、空依据、无效编号都明确拒绝，后端独立检查', '页面分别提示“请选择主花名册中的编号”“请填写核对依据”；伪造999编号实际HTTP422，同样保留已填依据；不存在的原记录也拒绝。'),
    ('4 保持待确认', '不丢记录，不解决问题，不启动DWS', '继续待确认后仍1个阻断问题、stage2为空；原始记录与草稿刷新恢复，审计明确标为尚未解决。'),
    ('5 缺少提交时间', '展示具体提交，不按行序猜；补充依据后继续', '第三组显示F3第7行82分/空时间与第41行99分/明确时间；未确认时作业留空，教师明确裁决99及依据后完成。'),
    ('6 中文表单调整整理', '先摘要后确认，生成新DWD并使旧DWS失效；无变更不生成', '实验由最高分改为暂不处理，摘要显示变更与影响，填写依据后产生新DWD、当前DWS清空且旧下载409；相同方案版本不增加。绕过表单不填策略依据也被后端拒绝。'),
    ('7 仅修改权重', '实际执行新权重，DWD保持不变', '50/30/20、60/20/20与33.3/33.3/33.4均实际进入执行；新DWS目录和结果，DWD哈希及全部文件字节不变。百分比到权重由Python Decimal转换。'),
    ('8 已知身份错误', '页面直接说明问题，无需查failure.json', '卡片实际提交非法999，收到具体编号提示；缺依据单独提示。普通页面没有failure.json查找要求，当前草稿保留。'),
    ('9 未知执行故障', '系统故障与业务补充区分，重试可恢复', '专用8766测试服务真实注入一次RuntimeError；页面显示系统故障与重试，不显示旧结果；解除注入后点击重试完成。后端另测真实runner非零退出，不伪装为规则问题。'),
    ('10 恢复/重复/版本/多条', '刷新恢复；双击不重复；旧版本拒绝；逐条保留草稿', '2→1→0逐条确认，另一条编号与依据在提交和刷新后都保留；连续双击仅1个POST；旧revision实际409，恢复最新版本并保留草稿后可以继续。'),
    ('11 原值与导出一致', '实际读取=页面；预览=当前下载；0/前导零/状态/来源保留', '四表前50行DOM data-raw与解析原值逐字段一致；多次实际下载CSV经Python csv解析后与当前API/DOM逐字段一致。0、00开头编号、缓考/免修及全部来源保留；桌面12个CSV SHA-256未变。'),
]

def cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')

lines = ['教师交互改进：实际验收报告', '', f'生成时间：{datetime.now().astimezone().isoformat(timespec="seconds")}。', '',
    '结论：本轮要求的11项均实际验证通过。43项Python/HTTP检查（教师11、双阶段18、通用14），24组真实Edge浏览器检查（教师15、通用9）通过；教师浏览器JavaScript异常0。没有以截图代替按钮提交、状态变化或下载核对。', '',
    '| 项目 | 预期 | 实际 |', '| --- | --- | --- |']
lines += ['| ' + ' | '.join(map(cell, row)) + ' |' for row in rows]
lines += ['', '输入和输出：', '',
    f'- 实际输入目录：{backend["input_directory"]}（三组，原文件只读）。',
    f'- 独立测试快照：{ROOT / "tests" / "teacher_trial_data"}。',
    f'- 正式服务实际样例流程：{demo["id"]}；状态{demo["phase"]}，40人。',
    f'- 原始上传字节：{ROOT / "state" / "pipelines" / demo["id"] / "input"}，原文件名与物理行保存于工作流及来源审计。',
    f'- 状态：{ROOT / "state" / "pipelines" / demo["id"] / "workflow.json"}。',
    f'- 阶段一独立产物：{demo["stage1"]["directory"]}。',
    f'- 阶段二独立产物：{demo["stage2"]["directory"]}。',
    f'- 固定交付副本：{OUT / "teacher_demo"}，包含整理宽表.csv、计算结果.csv、工作流审计.json、原值与来源审计.json、state.json。两个CSV均通过正式HTTP下载并再次核对预览。',
    f'- 浏览器真实下载副本：{OUT}下teacher-browser-*-dwd.csv / teacher-browser-*-dws.csv。',
    '- 已提交的状态在服务器workflow.json；尚未提交的表单草稿在当前浏览器localStorage，按流程和原记录标识保存。', '',
    '启动与本轮实际运行命令（PowerShell；均在项目根目录执行）：', '', '```powershell',
    f"Set-Location '{ROOT}'", 'python -u -X utf8 app.py --port 8765',
    'python -X utf8 tests/prepare_teacher_fixtures.py', 'python -X utf8 tests/test_teacher_pipeline.py',
    'python -X utf8 tests/test_two_stage.py', 'python -X utf8 tests/test_interactive.py',
    '# 单独的测试服务进程，仅用于浏览器未知故障注入', 'python -u -X utf8 tests/serve_teacher_browser.py',
    "$env:INTEGRATION_TEST_URL = 'http://127.0.0.1:8766'", 'node tests/two_stage_browser.cjs',
    "$env:INTEGRATION_TEST_URL = 'http://127.0.0.1:8765'", 'node tests/browser_acceptance.cjs',
    'python -X utf8 tests/publish_teacher_demo.py', 'git diff --check', '```', '',
    '上面两个服务在本轮由Start-Process -WindowStyle Hidden后台启动。交付时正式8765服务保留；8766专用测试服务在验收结束后停止。浏览器测试使用本机Playwright及Edge，正式程序不依赖它们。', '',
    '正式样例实际生成并执行的Python命令：', '', '```powershell',
    subprocess.list2cmdline(demo['stage1']['command']), subprocess.list2cmdline(demo['stage2']['command']), '```', '',
    '操作步骤及修改定位见docs/pipeline_teacher_ux.md。核心修改：web/pipeline.html、web/pipeline.js、web/style.css；app.py；pipeline/service.py、interaction.py、common.py、rules.py、dwd.py、dws.py。测试在tests/test_teacher_pipeline.py、test_two_stage.py、two_stage_browser.cjs及独立测试启动器；未重建项目或引入运行时依赖。', '',
    '完整逐项机器记录：', '',
    '- outputs/teacher_backend_acceptance.json：教师11项后端预期与实际、原始SHA-256。',
    '- outputs/two_stage_browser.json：教师15组真实浏览器预期与实际。',
    '- outputs/two_stage_acceptance.json：原双阶段18项；outputs/interactive_acceptance.json：原通用工作台14项。',
    '- outputs/browser_acceptance.json：通用工作台9组真实浏览器检查。',
    '- outputs/teacher-pipeline-complete.png、teacher-identity-question.png：辅助布局核对截图。', '',
    '未运行与限制：', '',
    '- 真实LLM联网、语义质量与模型建议落地未运行：没有配置模型，界面明确关闭该能力；没有模拟模型回复。模型接口与服务端密钥配置入口保留，普通流程不需要模型或JSON。',
    '- 非Edge浏览器、移动端、真实教学平台数据、生产权限、多用户审批、压力及大规模性能未验证。',
    '- 只支持当前CSV范围和有限自然语言语法；不扩展Excel/PDF/平台API；复杂业务仍需高级受限公式或真实模型建议。latest时间需可解析ISO格式。',
    '- 未完成的草稿保存在该浏览器本机，换浏览器不会同步；服务器保留已提交数据和审计。',
    '- 旧通用工作台报告中的“不计算成绩”仅描述根路径/的范围；本轮/pipeline的DWS实际计算已执行并验证。', '',
    '教师后端补充检查（预期与实际）：', '', '| 检查 | 预期 | 实际 |', '| --- | --- | --- |']
for entry in backend['cases']:
    lines.append('| ' + ' | '.join(cell(v) for v in (entry['case'], entry['expected'], json.dumps(entry['actual'], ensure_ascii=False))) + ' |')
lines += ['', '真实浏览器补充检查（预期与实际）：', '', '| 检查 | 预期 | 实际 |', '| --- | --- | --- |']
for entry in browser['checks']:
    lines.append('| ' + ' | '.join(cell(v) for v in (entry['name'], entry['expected'], json.dumps(entry['actual'], ensure_ascii=False))) + ' |')
(OUT / 'teacher_ux_acceptance.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
print(OUT / 'teacher_ux_acceptance.md')
