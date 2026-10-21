"""Fresh clone and allowlisted ZIP in independent Unicode paths on this Windows PC."""
import argparse
import base64
import csv
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def available_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def api(base, path, data=None):
    request = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=40) as response:
        return json.load(response)


def exercise(directory, use_cmd):
    port = available_port()
    log = directory.parent / (directory.name + "-service.log")
    if use_cmd:
        # Paths are task-created under the verified workspace; no filesystem operation crosses shells.
        command = f'cmd.exe /d /s /c ""{directory / "start.cmd"}" --no-browser --port {port}"'
    else:
        command = [sys.executable, "-u", "-X", "utf8", str(directory / "tools/launch.py"), "--no-browser", "--port", str(port)]
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(command, cwd=ROOT / "work", stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        base = f"http://127.0.0.1:{port}"
        try:
            health = None
            for _ in range(120):
                if process.poll() is not None:
                    raise AssertionError("启动器退出：" + log.read_text(encoding="utf-8", errors="replace"))
                try:
                    health = api(base, "/api/health")
                    break
                except OSError:
                    time.sleep(0.1)
            assert health and health["status"] == "ok" and health["loopback_only"]
            assert not health["model"]["enabled"]
            names = ["花名册.csv", "平台记录.csv"]
            originals = {name: digest(directory / "examples/windows_trial" / name) for name in names}
            task = "以编号关联，原始数据百分制，实验40%，作业60%，未交记0，保留一位小数。"
            files = [{"name": name, "content_base64": base64.b64encode((directory / "examples/windows_trial" / name).read_bytes()).decode()} for name in names]
            state = api(base, "/api/pipelines", {"task": task, "files": files})
            assert state["phase"] == "complete", state["issues"]
            assert [r["总评"] for r in state["stage2"]["rows"]] == ["86.0", "54.0", "32.0"]
            assert [r["student_id"] for r in state["stage2"]["rows"]] == ["001", "002", "003"]
            assert state["stage1"]["rows"][2]["作业"] == "未交"
            run_id = state["id"]
            with urllib.request.urlopen(base + f"/api/pipelines/{run_id}/download?kind=dws") as response:
                exported = list(csv.DictReader(io.StringIO(response.read().decode("utf-8-sig"))))
            assert exported == state["stage2"]["rows"]
            for stage in ("stage1", "stage2"):
                assert Path(state[stage]["directory"]).is_relative_to(directory / "runtime/state")
                code = Path(state[stage]["directory"]) / ("governance.py" if stage == "stage1" else "calculation.py")
                text = code.read_text(encoding="utf-8")
                assert f"sys.path.insert(0, {str(directory)!r})" in text
                assert f"sys.path.insert(0, {str(ROOT)!r})" not in text
            dwd_hash = state["stage1"]["manifest_sha256"]
            accepted = state["accepted_result"]["trial_id"]
            state = api(base, f"/api/pipelines/{run_id}/instruct", {"revision": state["revision"],
                "instruction": "实验50%，作业50%，看看变化", "mode": "trial", "request_id": "release-smoke-once"})
            assert state["phase"] == "complete", state["issues"]
            assert state["stage1"]["manifest_sha256"] == dwd_hash and state["accepted_result"]["trial_id"] == accepted
            assert state["stage2"]["mode"] == "trial"
            again = api(base, f"/api/pipelines/{run_id}/instruct", {"revision": state["revision"] - 1,
                "instruction": "实验50%，作业50%，看看变化", "mode": "trial", "request_id": "release-smoke-once"})
            assert again["stage2"]["trial_id"] == state["stage2"]["trial_id"]
            assert originals == {name: digest(directory / "examples/windows_trial" / name) for name in names}
            occupied = subprocess.run([sys.executable, "-X", "utf8", str(directory / "tools/doctor.py"), "--port", str(port)], capture_output=True, encoding="utf-8")
            assert occupied.returncode == 1 and "不可用" in occupied.stdout
            return {"directory": str(directory), "launcher": "start.cmd" if use_cmd else "tools/launch.py", "rows": 3,
                    "totals": ["86.0", "54.0", "32.0"], "preview_csv_equal": True, "new_absolute_paths_verified": True,
                    "dwd_reused": True, "trial_does_not_overwrite": True, "duplicate_not_reexecuted": True,
                    "occupied_port_rejected": True, "original_examples_unchanged": True}
        finally:
            if process.poll() is None:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                process.wait(timeout=10)


def main(package):
    parent = Path(tempfile.mkdtemp(prefix="Windows 试运行 ", dir=ROOT / "work")).resolve()
    assert parent.is_relative_to(ROOT / "work")
    clone, extracted = parent / "干净 克隆项目", parent / "独立 解压目录"
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip()
    result = subprocess.run(["git", "clone", "--no-hardlinks", "--branch", branch, str(ROOT), str(clone)], capture_output=True)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        for name in names:
            assert not Path(name).is_absolute() and ".." not in Path(name).parts
            assert Path(name).parts[0] not in ("state", "runtime", "work", "outputs", ".git", "tests")
            assert not name.endswith("model_config.local.json") and not Path(name).name.startswith(".env")
        archive.extractall(extracted)
    manifest = json.loads((extracted / "RELEASE_MANIFEST.json").read_text(encoding="utf-8"))
    assert all(digest(extracted / name) == sha for name, sha in manifest["files"].items())
    results = [exercise(clone, False), exercise(extracted, True)]
    # Demonstrate that startup refuses a changed distribution source, without altering the original package.
    (extracted / "VERSION").write_text("tampered-test-copy", encoding="utf-8")
    refused = subprocess.run([sys.executable, "-X", "utf8", str(extracted / "tools/launch.py"), "--check-only", "--port", str(available_port())], capture_output=True, encoding="utf-8")
    assert refused.returncode == 1 and "文件已改变" in refused.stdout
    report = {"package": str(Path(package).resolve()), "package_sha256": digest(package), "source_commit": manifest["commit"],
              "cases": results, "source_tamper_rejected": True, "package_excludes_user_data_and_keys": True,
              "physical_second_pc": "not_run; two independent paths on this Windows PC", "external_real_llm": "not_run"}
    (ROOT / "outputs/portable_release_smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    main(parser.parse_args().package)
