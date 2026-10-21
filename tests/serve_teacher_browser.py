"""Dedicated real browser test service. Fault injection exists only in this test launcher."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import create_server
import pipeline.service as service

original = service.generate_and_run
marker = ROOT / 'work' / 'teacher-browser-fault.once'


def run_or_inject(*args, **kwargs):
    if marker.exists():
        marker.unlink()
        raise RuntimeError('验收注入：未知阶段二故障（仅测试启动器）')
    return original(*args, **kwargs)


if __name__ == '__main__':
    service.generate_and_run = run_or_inject
    server = create_server(port=8766, state_dir=ROOT / 'work' / 'teacher-browser-state')
    print('Teacher browser acceptance: http://127.0.0.1:8766', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
