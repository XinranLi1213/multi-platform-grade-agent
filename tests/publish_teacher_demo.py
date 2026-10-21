"""Read the real desktop trial group through the formal HTTP API and retain reviewable artifacts."""
import base64
import csv
import io
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = Path.home() / 'Desktop' / '多平台试用数据_20261021_163831' / '01_完整流程_先试这组'
task = (source / '任务与预期.txt').read_text(encoding='utf-8-sig').split('可复制任务：')[1].split('预期：')[0].strip()
body = {'task': task, 'files': [{'name': p.name, 'content_base64': base64.b64encode(p.read_bytes()).decode()} for p in sorted(source.glob('*.csv'))]}
request = urllib.request.Request('http://127.0.0.1:8765/api/pipelines', data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(request, timeout=40) as response:
    state = json.load(response)
assert state['phase'] == 'complete', state['issues']
assert len(state['stage2']['rows']) == 40
out = ROOT / 'outputs' / 'teacher_demo'
out.mkdir(parents=True, exist_ok=True)
for kind, name, stage in [('dwd', '整理宽表.csv', 'stage1'), ('dws', '计算结果.csv', 'stage2'), ('audit', '工作流审计.json', None), ('dwd_audit', '原值与来源审计.json', None)]:
    with urllib.request.urlopen(f"http://127.0.0.1:8765/api/pipelines/{state['id']}/download?kind={kind}", timeout=20) as response:
        content = response.read()
    (out / name).write_bytes(content)
    if stage:
        assert list(csv.DictReader(io.StringIO(content.decode('utf-8-sig')))) == state[stage]['rows']
(out / 'state.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'input': str(source), 'output': str(out), 'id': state['id'], 'phase': state['phase'], 'rows': len(state['stage2']['rows']), 'stage1': state['stage1']['directory'], 'stage2': state['stage2']['directory']}, ensure_ascii=False, indent=2))
