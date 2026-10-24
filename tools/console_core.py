"""Desktop process ownership and lifecycle, independent of Tk and developer directories."""
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from integration.local_settings import SettingsError, validate_config
from tools.doctor import inspect_environment

STATES = {"stopped": "已停止", "starting": "启动中", "running": "运行中", "stopping": "停止中", "failed": "启动失败"}


def interpreter():
    path = Path(sys.executable)
    return str(path.with_name("python.exe")) if path.name.lower() == "pythonw.exe" else str(path)


def child_environment(config, key):
    env = dict(os.environ)
    name = config.get("api_key_env", "INTEGRATION_MODEL_API_KEY")
    env.pop(name, None)  # No silent fallback when the teacher selected manual or another source.
    if key:
        env[name] = key
    env["GRADE_AGENT_MODEL_CONFIG_JSON"] = json.dumps(config, ensure_ascii=False)
    return env


def health(url):
    with urllib.request.urlopen(url + "/api/health", timeout=0.6) as response:
        return json.load(response)


class ServiceController:
    def __init__(self, root=ROOT, port=8765, state_dir=None):
        self.root = Path(root).resolve()
        self.port = port
        self.state_dir = Path(state_dir or self.root / "runtime/state").resolve()
        self.process = None
        self.state = "stopped"
        self.message = "服务尚未启动；关闭页面或停止服务均不会清空历史数据"
        self.diagnostic = ""
        self.foreign = False
        self.stop_timed_out = False
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.instance_id = None
        self._token = None
        self.starts = 0
        self.worker_error = ""
        self.reader = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def refresh(self):
        with self.lock:
            if self.process is not None and self.process.poll() is not None:
                self.release_pipes(self.process)
                if self.state == "stopping":
                    self.state = "stopped"
                    self.message = "本控制台服务已停止；上传、结果、草稿和审计保留"
                elif self.state in ("running", "starting"):
                    self.state = "failed"
                    self.message = "服务意外退出，当前没有运行中的受管服务；可重新启动"
                    self.diagnostic = "子进程退出码：" + str(self.process.returncode)
                self.stop_timed_out = False
            return self.state

    def start(self, config, key="", *, timeout=30):
        config = validate_config(config)
        with self.lock:
            self.refresh()
            if self.state in ("starting", "running", "stopping") or self.process is not None and self.process.poll() is None:
                return False
            self.state, self.message = "starting", "正在检查环境并启动；健康检查通过后才显示运行中"
            self.foreign = False
            self.diagnostic = ""
            self.stop_timed_out = False
            self.cancel.clear()
        try:
            report = inspect_environment(root=self.root, state_dir=self.state_dir, port=self.port)
            failures = [(f"本机端口{self.port}已被占用或被系统限制，请在控制台选择其他端口；不会结束其他进程"
                         if c["name"] == "本机端口" else c["message"]) for c in report["checks"] if not c["ok"]]
            if failures:
                if not next(c for c in report["checks"] if c["name"] == "本机端口")["ok"]:
                    try:
                        existing = health(self.url)
                        self.foreign = existing.get("status") == "ok" and existing.get("loopback_only") is True
                    except (OSError, ValueError):
                        pass
                raise SettingsError("；".join(failures) + ("。这是已有服务，未接管或停止；可打开已有页面或换端口" if self.foreign else ""))
            env = child_environment(config, key)
            identity, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
            env.update(GRADE_AGENT_INSTANCE_ID=identity, GRADE_AGENT_CONTROL_TOKEN=token)
            with self.lock:
                if self.cancel.is_set():
                    self.state = "stopped"
                    return False
                self.instance_id, self._token = identity, token
                self.worker_error = ""
                self.process = subprocess.Popen([interpreter(), "-B", "-u", "-X", "utf8", str(self.root / "tools/console_worker.py"),
                                                 "--port", str(self.port), "--state-dir", str(self.state_dir)],
                                                cwd=self.root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                process = self.process
                self.starts += 1
                self.reader = threading.Thread(target=self.read_worker_status, args=(process,), daemon=True)
                self.reader.start()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and process.poll() is None:
                if self.cancel.is_set():
                    return False
                try:
                    result = health(self.url)
                    if result.get("status") == "ok" and result.get("instance_id") == identity:
                        with self.lock:
                            if self.state != "stopping":
                                self.state, self.message = "running", "健康检查通过；服务仅监听本机"
                                self.active_config = dict(config)
                            return self.state == "running"
                except (OSError, ValueError):
                    pass
                time.sleep(0.1)
            self.diagnostic = ("子进程退出码：" + str(process.returncode) if process.poll() is not None else "健康检查超时；子进程仍在运行") + ("；异常类型：" + self.worker_error if self.worker_error else "")
            raise SettingsError("服务健康检查未通过，不能视为已启动；若子进程仍在，请先停止再重试")
        except SettingsError as exc:
            with self.lock:
                if self.state != "stopping":
                    self.state, self.message = "failed", str(exc)
            return False
        except Exception as exc:
            with self.lock:
                if self.state != "stopping":
                    self.state, self.message = "failed", "服务未能启动，请检查环境或展开诊断；没有结束其他进程"
                    self.diagnostic = "异常类型：" + type(exc).__name__
            return False

    def read_worker_status(self, process):
        try:
            for line in process.stdout:
                try:
                    value = json.loads(line)
                    kind = value.get("exception_type", "")
                    if value.get("code") == "worker_failure" and isinstance(kind, str) and re.fullmatch(r"[A-Za-z0-9_]{1,100}", kind) and self.process is process:
                        self.worker_error = kind
                except (ValueError, AttributeError):
                    pass  # Unstructured output is never copied to diagnostics or logs.
        finally:
            process.stdout.close()

    def release_pipes(self, process):
        if process.poll() is not None:
            if process.stdin and not process.stdin.closed:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            if self.reader and self.reader is not threading.current_thread():
                self.reader.join(timeout=1)

    def stop(self, *, timeout=8):
        with self.lock:
            self.cancel.set()
            self.refresh()
            process = self.process
            if process is None or process.poll() is not None:
                self.state, self.message = "stopped", "本控制台没有运行中的子服务；未停止其他服务"
                return True
            self.state, self.message = "stopping", "停止请求已发出，正在等待现有请求完成；不会清空数据"
            try:
                process.stdin.write(json.dumps({"operation": "stop", "instance_id": self.instance_id, "token": self._token}) + "\n")
                process.stdin.flush()
            except (OSError, ValueError):
                self.diagnostic = "私有控制管道暂不可用；仍须核对实际进程退出，未声称停止成功"
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            with self.lock:
                self.stop_timed_out = True
                self.message = "服务尚未退出，可能仍在处理任务。可继续等待；强制停止必须另行确认"
            return False
        self.refresh()
        return self.state == "stopped"

    def force_stop(self, *, confirmed=False):
        if confirmed is not True:
            raise SettingsError("强制停止需要用户明确选择")
        with self.lock:
            process = self.process
            if process is None or process.poll() is not None:
                self.refresh()
                return True
            self.state = "stopping"
            # Popen retains the process handle: never look up/kill a potentially recycled PID.
            process.kill()
        process.wait(timeout=5)
        self.refresh()
        self.message = "本控制台服务已强制停止；进行中的操作可能未完成，原始上传保留"
        return True


def probe_model(root, config, key):
    config = validate_config(config)
    env = child_environment(config, key)
    try:
        result = subprocess.run([interpreter(), "-B", "-X", "utf8", str(Path(root) / "tools/console_worker.py"), "--check-model"],
                                env=env, cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=70,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ValueError
        # Transport already redacts provider echoes; defend the desktop presentation boundary too.
        return json.loads(json.dumps(value).replace(key, "[已隐藏]")) if key else value
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"status": "error", "message": "模型检查未正常完成；未验证业务理解质量，可继续手动操作", "business_quality_verified": False}
