"""Portable Windows/source launcher. Uses the selected interpreter for both pipeline stages."""
import argparse
import json
import sys
if sys.version_info < (3, 11):
    print("需要Python 3.11及以上，请安装后重新打开启动器。")
    sys.exit(1)
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.doctor import inspect_environment
from integration.models import load_config
from app import create_server


def main():
    parser = argparse.ArgumentParser(description="交汇教师数据工作台：本机独立试运行")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--state-dir", type=Path, default=ROOT / "runtime/state")
    parser.add_argument("--model-config", type=Path, default=ROOT / "config/model_config.local.json")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    report = inspect_environment(state_dir=args.state_dir, port=args.port, model_config=args.model_config)
    for check in report["checks"]:
        print(("通过：" if check["ok"] else "未通过：") + check["name"] + "；" + check["message"], flush=True)
    print(report["model"]["label"], flush=True)
    if not report["ok"]:
        return 1
    if args.check_only:
        return 0
    try:
        server = create_server(port=args.port, state_dir=args.state_dir, model_config=load_config(args.model_config))
    except OSError:
        print("服务未启动：端口被占用或目录不可访问，请换端口或检查写权限。", flush=True)
        return 1
    url = f"http://127.0.0.1:{server.server_port}/pipeline"
    print("已启动：" + url + "；仅供本机访问，Ctrl+C停止。", flush=True)
    if not args.no_browser:
        timer = threading.Timer(0.5, webbrowser.open, args=(url,))
        timer.daemon = True
        timer.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
