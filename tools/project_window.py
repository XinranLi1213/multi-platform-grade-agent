"""Close only a verified, tagged Edge app window created by this controller."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import secrets
import subprocess
import time
import webbrowser


def edge_path():
    for folder in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if folder:
            path = Path(folder) / "Microsoft/Edge/Application/msedge.exe"
            if path.is_file():
                return path
    return None


class Windows:
    def __init__(self):
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        signatures = {
            "EnumWindows": ([self.callback, wintypes.LPARAM], wintypes.BOOL),
            "GetWindowThreadProcessId": ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL), "IsWindow": ([wintypes.HWND], wintypes.BOOL),
            "GetClassNameW": ([wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            "SetPropW": ([wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE], wintypes.BOOL),
            "GetPropW": ([wintypes.HWND, wintypes.LPCWSTR], wintypes.HANDLE),
            "PostMessageW": ([wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
            "ShowWindow": ([wintypes.HWND, ctypes.c_int], wintypes.BOOL),
            "SetForegroundWindow": ([wintypes.HWND], wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.api, name)
            fn.argtypes, fn.restype = args, result

    def pid(self, hwnd):
        value = wintypes.DWORD()
        self.api.GetWindowThreadProcessId(hwnd, ctypes.byref(value))
        return value.value

    def windows(self, pid):
        result = []
        @self.callback
        def visit(hwnd, _):
            if self.api.IsWindowVisible(hwnd) and self.pid(hwnd) == pid:
                name = ctypes.create_unicode_buffer(256)
                self.api.GetClassNameW(hwnd, name, len(name))
                if name.value == "Chrome_WidgetWin_1":
                    result.append(hwnd)
            return True
        self.api.EnumWindows(visit, 0)
        return result


class ProjectWindow:
    def __init__(self, root):
        self.root = Path(root)
        self.process = None
        self.hwnd = None
        self.url = None
        self.mode = "closed"
        self.message = "项目页面尚未打开"
        self.tag = "GradeAgent.ProjectWindow." + secrets.token_hex(12)
        self.value = secrets.randbits(30) + 1
        self.win = Windows() if os.name == "nt" else None

    def owned(self):
        return bool(self.win and self.process and self.process.poll() is None and self.hwnd
                    and self.win.api.IsWindow(self.hwnd) and self.win.pid(self.hwnd) == self.process.pid
                    and self.win.api.GetPropW(self.hwnd, self.tag) == self.value)

    def refresh(self):
        if self.mode == "owned" and not self.owned():
            self.mode = "closed"
            self.message = "项目页面已关闭；后端服务状态不受影响"
        return self.mode

    def open(self, url):
        if self.owned() and url == self.url:
            self.win.api.ShowWindow(self.hwnd, 9)
            self.win.api.SetForegroundWindow(self.hwnd)
            self.message = "已激活本控制台创建的项目页面"
            return True
        if self.owned() and not self.close():
            return False
        executable = edge_path()
        if not executable or not self.win:
            opened = webbrowser.open(url + "/pipeline")
            self.mode = "unmanaged" if opened else "closed"
            self.message = ("已请求默认浏览器打开普通标签页，请手动关闭；控制台不会关闭其他浏览器窗口" if opened
                            else "无法自动打开浏览器，请复制访问地址到浏览器")
            return False
        profile = self.root / "runtime/browser-project"
        profile.mkdir(parents=True, exist_ok=True)
        previous = self.process if self.process and self.process.poll() is None else None
        before = set(self.win.windows(previous.pid)) if previous else set()
        command = [str(executable), "--user-data-dir=" + str(profile), "--app=" + url + "/pipeline",
                   "--no-first-run", "--no-default-browser-check", "--disable-background-mode", "--disable-logging"]
        launcher = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        owners = [p for p in (previous, launcher) if p is not None]
        retried = False
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            alive = [p for p in owners if p.poll() is None]
            if not alive:
                if previous and not retried:
                    # Edge may finish quitting between close and reopen. Retry only our known launch.
                    owners = [subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
                    retried = True
                    time.sleep(0.2)
                    continue
                break
            candidates = [(p, h) for p in alive for h in self.win.windows(p.pid) if h not in before]
            if len(candidates) == 1:
                owner, hwnd = candidates[0]
                if self.win.api.SetPropW(hwnd, self.tag, self.value):
                    self.process, self.hwnd, self.url = owner, hwnd, url
                    self.mode, self.message = "owned", "项目页面已打开（独立Edge应用窗口，可单独关闭）"
                    return True
            time.sleep(0.1)
        self.mode = "unmanaged"
        self.message = "已请求Edge打开页面，但无法核验窗口归属；请手动关闭，本控制台不会结束浏览器进程"
        return False

    def close(self, *, timeout=5):
        if not self.owned():
            self.refresh()
            if self.mode == "unmanaged":
                self.message = "当前是无法核验归属的页面，请手动关闭浏览器标签页；后端服务未停止"
                return False
            return True
        hwnd = self.hwnd
        if not self.win.api.PostMessageW(hwnd, 0x0010, 0, 0):  # WM_CLOSE, never a browser-wide kill.
            self.message = "未能请求关闭项目窗口，请检查浏览器提示并重试"
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.owned():
                self.mode, self.message = "closed", "项目页面已关闭；后端服务继续按控制台所示状态运行"
                return True
            time.sleep(0.1)
        self.message = "项目窗口尚未关闭，可能在等待浏览器确认；请处理后重试。未结束其他窗口"
        return False
