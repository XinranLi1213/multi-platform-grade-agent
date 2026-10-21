"""Local environment check; no external network or model call unless explicitly requested."""
import argparse
import hashlib
import json
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integration.models import load_config, model_status
from integration.model_client import check_connection

REQUIRED = ("VERSION", "app.py", "pipeline_cli.py", "integration/__init__.py", "integration/model_client.py",
            "integration/models.py", "integration/store.py", "integration/engine.py", "integration/csv_io.py",
            "pipeline/__init__.py", "pipeline/common.py", "pipeline/ingest.py", "pipeline/semantic.py", "pipeline/service.py",
            "pipeline/interaction.py", "pipeline/advisor.py", "pipeline/rules.py", "pipeline/dwd.py", "pipeline/dws.py",
            "web/index.html", "web/app.js", "web/pipeline.html", "web/pipeline.js", "web/semantic.js", "web/style.css")


def inspect_environment(root=ROOT, state_dir=None, port=8765, model_config=None):
    root = Path(root).resolve()
    state_dir = Path(state_dir or root / "runtime/state").resolve()
    missing = [name for name in REQUIRED if not (root / name).is_file()]
    checks = [{"name": "Python", "ok": sys.version_info >= (3, 11), "message": "需要Python 3.11及以上；当前" + sys.version.split()[0]},
              {"name": "核心源码", "ok": not missing, "message": "核心文件齐全" if not missing else "缺少：" + "、".join(missing)}]
    if (root / "RELEASE_MANIFEST.json").exists():
        try:
            manifest = json.loads((root / "RELEASE_MANIFEST.json").read_text(encoding="utf-8"))
            valid = bool(manifest["files"]) and all((root / name).resolve().is_relative_to(root)
                and (root / name).is_file() and hashlib.sha256((root / name).read_bytes()).hexdigest() == expected
                for name, expected in manifest["files"].items())
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            valid = False
        checks.append({"name": "分发完整性", "ok": valid, "message": "分发文件哈希一致" if valid else "源码包缺失或文件已改变，请重新解压原包；不会忽略哈希检查"})
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=state_dir) as handle:
            handle.write(b"write-check")
        writable = True
    except OSError:
        writable = False
    checks.append({"name": "运行目录", "ok": writable, "message": "运行目录可写" if writable else "目录不可写，请解压到有写权限的普通文件夹"})
    try:
        if not 1 <= port <= 65535:
            raise ValueError
        with socket.socket() as probe:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", port))
        free = True
    except (OSError, ValueError):
        free = False
    checks.append({"name": "本机端口", "ok": free, "message": f"127.0.0.1:{port}可用" if free else f"端口{port}不可用；关闭旧服务或使用start.cmd --port 8767"})
    config = load_config(model_config or root / "config/model_config.local.json")
    status = model_status(config)
    return {"ok": all(c["ok"] for c in checks), "checks": checks, "model": status, "loopback_only": True}


def main():
    parser = argparse.ArgumentParser(description="本机试运行环境检查；不安装依赖、不上传文件")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--model-config", type=Path)
    parser.add_argument("--check-model", action="store_true", help="主动发起一次真实模型连接检查，不使用教师数据")
    args = parser.parse_args()
    report = inspect_environment(state_dir=args.state_dir, port=args.port, model_config=args.model_config)
    if args.check_model:
        report["model_check"] = check_connection(load_config(args.model_config or ROOT / "config/model_config.local.json"))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] and (not args.check_model or report["model_check"]["status"] == "ok") else 1


if __name__ == "__main__":
    sys.exit(main())
