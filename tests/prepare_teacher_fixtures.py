"""Explicit test-only snapshot of the teacher's desktop CSVs; never imported by app."""
import hashlib
import json
import shutil
from pathlib import Path

SOURCE = Path.home() / "Desktop" / "多平台试用数据_20261021_163831"
TARGET = Path(__file__).resolve().parent / "teacher_trial_data"

if __name__ == "__main__":
    if not SOURCE.is_dir():
        raise SystemExit("未找到桌面试用数据；不会编造替代输入")
    manifest = {}
    for directory in sorted(p for p in SOURCE.iterdir() if p.is_dir()):
        target = TARGET / directory.name
        target.mkdir(parents=True, exist_ok=True)
        for source in sorted(directory.iterdir()):
            if source.suffix not in (".csv", ".txt"):
                continue
            destination = target / source.name
            if destination.exists() and destination.read_bytes() != source.read_bytes():
                raise SystemExit(f"已有快照不同，未覆盖：{destination}")
            shutil.copyfile(source, destination)
            manifest[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
    (TARGET / "original_hashes.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"只读桌面输入，测试快照：{TARGET}")
