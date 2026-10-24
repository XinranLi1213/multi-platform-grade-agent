"""Owned child service. Shutdown is stdin-only; no web shutdown endpoint or secret arguments."""
import argparse
import hmac
import json
import os
import sys
import threading
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from integration.local_settings import validate_config
    from integration.model_client import check_connection
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--state-dir", type=Path, default=ROOT / "runtime/state")
    parser.add_argument("--check-model", action="store_true")
    args = parser.parse_args()
    config = validate_config(json.loads(os.environ.pop("GRADE_AGENT_MODEL_CONFIG_JSON")))
    if args.check_model:
        print(json.dumps(check_connection(config), ensure_ascii=False), flush=True)
        return 0
    # Checking an API connection does not require the web server or its routes.
    from app import create_server
    identity = os.environ.pop("GRADE_AGENT_INSTANCE_ID")
    token = os.environ.pop("GRADE_AGENT_CONTROL_TOKEN")
    if len(token) < 32 or len(identity) != 32:
        raise ValueError("invalid controller")
    server = create_server(port=args.port, state_dir=args.state_dir, model_config=config, instance_id=identity)
    # Let active requests finish before the owned service exits, including Python workers.
    server.daemon_threads = False
    server.block_on_close = True

    def control():
        for line in sys.stdin:
            try:
                data = json.loads(line)
                valid = (data.get("operation") == "stop" and data.get("instance_id") == identity
                         and isinstance(data.get("token"), str) and hmac.compare_digest(data["token"], token))
            except (ValueError, AttributeError):
                valid = False
            if valid:
                server.shutdown()
                return
        # An unexpected controller crash closes its pipe; do not leave an orphan service.
        server.shutdown()

    threading.Thread(target=control, daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # No traceback, provider body, environment or configuration values on stdout/stderr.
        print(json.dumps({"status": "error", "code": "worker_failure", "exception_type": type(exc).__name__,
                          "message": "本机服务未正常完成，请检查环境和目录；上传与历史数据未清空",
                          "business_quality_verified": False}, ensure_ascii=False), flush=True)
        sys.exit(1)
