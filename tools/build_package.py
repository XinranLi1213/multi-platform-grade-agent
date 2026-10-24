"""Build an allowlisted source ZIP from committed Git blobs, never from runtime/user directories."""
import argparse
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.doctor import REQUIRED

EXACT = {"LICENSE", "docs/patch_031_validation.md", "docs/release_validation.md", "tools/verify_demo.py", "tools/build_package.py", "VERSION", "app.py", "pipeline_cli.py", "start.cmd", "model_config.example.json", ".gitignore", ".gitattributes",
         "启动控制台.vbs", "启动控制台.cmd", "tools/create_shortcut.vbs", "tools/desktop_console.py", "tools/console_core.py", "tools/console_worker.py", "tools/project_window.py", "docs/desktop_console.md", "config/README.md", "docs/windows_trial.md", "docs/agent_capabilities.md", "tools/__init__.py",
         "tools/doctor.py", "tools/launch.py", "grade_agent.py", "README.md", "docs/legacy_grades.md", "docs/two_stage.md", "docs/natural_language_pipeline.md", "docs/pipeline_teacher_ux.md", "docs/project_layout.md", "docs/workspace.md", "docs/intent_agent.md"}


def allowed(name):
    if name in EXACT:
        return True
    path = Path(name)
    return (path.parts[0] == 'examples' and path.suffix in ('.csv','.json','.txt','.md') or path.parent.as_posix() in ("pipeline", "integration") and path.suffix == ".py"
            or path.parent.as_posix() == "web" and path.suffix in (".html", ".js", ".css"))


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], stderr=subprocess.PIPE)


def build(output):
    tracked = set(git("ls-tree", "-r", "--name-only", "-z", "HEAD").decode("utf-8").split("\0")) - {""}
    indexed = git("ls-files", "-z").decode("utf-8").split("\0")
    if any(Path(n).name == "secrets.local.json" for n in tracked | set(indexed)):
        raise ValueError("Git仍跟踪本机密钥文件，拒绝构建；请核对版本控制，不会自动删除或改索引")
    files = sorted(n for n in tracked if allowed(n))
    missing = (set(REQUIRED) | EXACT) - set(files)
    if missing:
        raise ValueError("核心文件尚未提交，不能构建不完整分发包：" + "、".join(sorted(missing)))
    # Explicitly reject modified or untracked allowlisted sources instead of silently shipping an older version.
    for name in files:
        status = subprocess.run(["git", "-C", str(ROOT), "diff", "--quiet", "HEAD", "--", name], capture_output=True)
        if status.returncode:
            raise ValueError("源文件有未提交修改，请先提交再构建：" + name)
    untracked = git("ls-files", "--others", "--exclude-standard", "-z").decode("utf-8").split("\0")
    if any(n and allowed(n) for n in untracked):
        raise ValueError("分发白名单中仍有未跟踪源码，请先提交")
    contents = {n: git("show", "HEAD:" + n) for n in files}
    contents["试运行说明.md"] = contents["docs/windows_trial.md"]
    manifest = {"format": 1, "commit": git("rev-parse", "HEAD").decode().strip(),
                "version": contents["VERSION"].decode().strip(), "history_migration": "archive-and-rerun",
                "files": {n: hashlib.sha256(data).hexdigest() for n, data in contents.items()}}
    output = Path(output).resolve()
    if output == ROOT or output.is_relative_to(ROOT):
        raise ValueError("分发包输出必须位于正式项目目录之外")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError("输出文件已存在，请使用新文件名，避免覆盖既有分发包")
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in contents.items():
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
        archive.writestr("RELEASE_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    print(json.dumps({"package": str(output), "sha256": digest, "commit": manifest["commit"], "files": len(contents)}, ensure_ascii=False))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="从已提交版本生成不含密钥或用户状态的源码试运行包")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        build(args.output)
    except (ValueError, subprocess.CalledProcessError) as exc:
        print("构建失败：" + (str(exc) if isinstance(exc, ValueError) else "Git读取失败"), file=sys.stderr)
        sys.exit(1)
