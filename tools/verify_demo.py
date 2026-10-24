"""Opt-in stdlib HTTP smoke test; never reads an existing user's state."""
import base64
import csv
import io
import json
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import create_server


def main():
    with tempfile.TemporaryDirectory(prefix='grade-agent-demo-') as directory:
        server = create_server(port=0, state_dir=Path(directory), model_config={'enabled':False})
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f'http://127.0.0.1:{server.server_port}'

        def request(path, data=None):
            body = None if data is None else json.dumps(data,ensure_ascii=False).encode()
            req = urllib.request.Request(base+path, data=body, headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.read()

        try:
            for path in ('/', '/pipeline', '/api/health'):
                assert request(path), path
            example=ROOT/'examples/01_基础计分_无需模型'
            uploads=[{'name':p.name,'content_base64':base64.b64encode(p.read_bytes()).decode()} for p in sorted(example.glob('*.csv'))]
            result=json.loads(request('/api/pipelines',{'task':(example/'任务提示词.txt').read_text(encoding='utf-8-sig'),'files':uploads}))
            assert result['phase']=='complete', result['issues']
            assert [r['student_id'] for r in result['stage2']['rows']]==['001','002','003']
            assert [r['总评'] for r in result['stage2']['rows']]==['86.0','54.0','32.0']
            endpoint='/api/pipelines/'+result['id']
            downloaded=list(csv.DictReader(io.StringIO(request(endpoint+'/download?kind=dws').decode('utf-8-sig'))))
            assert downloaded==result['stage2']['rows']
            manifest=result['stage1']['manifest_sha256']
            trial=json.loads(request(endpoint+'/instruct',{'revision':result['revision'],'instruction':'实验50%，作业50%，看看变化','mode':'trial','request_id':'demo-trial'}))
            assert trial['stage1']['manifest_sha256']==manifest
            assert [r['总评'] for r in trial['stage2']['rows']]==['85.0','45.0','40.0']
            print('通过：页面、健康检查、无模型上传计分、0与前导零、实际CSV下载、试算及阶段一复用。')
            print('未测试：真实模型理解；此检查没有调用模型API，也没有使用用户历史数据。')
        finally:
            server.shutdown();server.server_close();worker.join()


if __name__=='__main__':
    main()
