"""Teacher-facing Tk console. No browser ever receives a credential input."""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class Console:
    def __init__(self, window, *, root=ROOT, port=8765, state_dir=None):
        import tkinter as tk
        from tkinter import ttk
        from integration.local_settings import SecretStore, read_config, SettingsError
        from tools.console_core import ServiceController
        from tools.project_window import ProjectWindow
        self.tk, self.ttk = tk, ttk
        self.window, self.root = window, Path(root).resolve()
        self.controller = ServiceController(self.root, port, state_dir)
        self.page = ProjectWindow(self.root)
        self.store = SecretStore(self.root)
        self.events = queue.Queue()
        self.pending = False
        self.exit_pending = False
        self.exit_dialog = None
        self.close_on_exit = True
        self.last_check = "尚未检查模型连接；真实任务理解质量：未验证"
        self.active_key_source = None
        self.notice = ""
        self.callback_errors = []
        window.report_callback_exception = lambda kind, value, trace: (self.callback_errors.append(kind.__name__), self.info("界面操作未完成，可重试；异常类型：" + kind.__name__))
        self.config_read_error = None
        try:
            self.base_config = read_config(self.root / "config/model_config.local.json")
        except SettingsError as exc:
            self.config_read_error = str(exc)
            self.base_config = json.loads((self.root / "model_config.example.json").read_text(encoding="utf-8"))
            self.notice = str(exc)
        c = self.base_config
        window.title("教师数据整合 · 项目控制台 — " + self.root.name)
        window.geometry("990x" + str(min(820, window.winfo_screenheight() - 90)))
        window.minsize(930, min(740, window.winfo_screenheight() - 120))
        style = ttk.Style(window)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("TLabel", font=("Microsoft YaHei UI", 10))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(10, 7))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("Status.TLabel", font=("Microsoft YaHei UI", 14, "bold"), foreground="#126456")
        container = ttk.Frame(window)
        container.pack(fill="both", expand=True)
        canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        frame = ttk.Frame(canvas, padding=20)
        frame_id = canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(frame_id, width=e.width))
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-int(e.delta / 120), "units"))
        ttk.Label(frame, text="教师数据整合 · 项目控制台", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text="先启动服务，再打开教师页面。关闭页面、停止服务、退出控制台是三个独立动作。").pack(anchor="w", pady=(6, 14))
        service = ttk.LabelFrame(frame, text="本控制台服务（不接管其他已有服务）", padding=12)
        service.pack(fill="x")
        self.status = tk.StringVar(value="已停止")
        ttk.Label(service, textvariable=self.status, style="Status.TLabel").grid(row=0, column=0, sticky="w")
        self.address = tk.StringVar(value=self.controller.url + "/pipeline")
        ttk.Label(service, textvariable=self.address).grid(row=0, column=1, columnspan=2, sticky="w", padx=20)
        ttk.Button(service, text="复制地址", command=self.copy_address).grid(row=0, column=3, sticky="e")
        self.port = tk.StringVar(value=str(port))
        self.state_dir = tk.StringVar(value=str(self.controller.state_dir))
        ttk.Label(service, text="端口").grid(row=1, column=0, sticky="w", pady=7)
        self.port_entry = ttk.Entry(service, textvariable=self.port, width=9)
        self.port_entry.grid(row=1, column=1, sticky="w")
        ttk.Label(service, text="状态目录").grid(row=2, column=0, sticky="w")
        self.state_entry = ttk.Entry(service, textvariable=self.state_dir, width=76)
        self.state_entry.grid(row=2, column=1, columnspan=2, sticky="ew")
        self.browse = ttk.Button(service, text="选择目录", command=self.select_directory)
        self.browse.grid(row=2, column=3)
        ttk.Label(service, text="状态目录保存上传、决定和结果；不要与其他运行中的服务共用。停止不会清空数据。").grid(row=3, column=0, columnspan=4, sticky="w", pady=7)
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=12)
        self.buttons = {}
        for name, label, fn in [("start", "启动服务", self.start), ("open", "打开教师页面", self.open_page),
                                ("close", "关闭项目页面", self.close_page), ("stop", "停止服务", self.stop), ("exit", "退出", self.request_exit)]:
            button = ttk.Button(actions, text=label, command=fn)
            button.pack(side="left", padx=(0, 8))
            self.buttons[name] = button
        self.foreign_button = ttk.Button(actions, text="打开已有页面（不接管）", command=self.open_existing)
        self.force_button = ttk.Button(actions, text="强制停止…", command=self.force)
        self.feedback = tk.StringVar()
        ttk.Label(frame, textvariable=self.feedback, wraplength=930, foreground="#205c52").pack(anchor="w", fill="x")
        self.page_status = tk.StringVar(value=self.page.message)
        ttk.Label(frame, textvariable=self.page_status, wraplength=930).pack(anchor="w", pady=(4, 10))
        model = ttk.LabelFrame(frame, text="模型接口与密钥（未配置也能使用手动流程）", padding=12)
        model.pack(fill="x")
        self.endpoint = tk.StringVar(value=c["endpoint"])
        self.model = tk.StringVar(value=c["model"])
        self.enabled = tk.BooleanVar(value=c.get("enabled", False))
        self.key = tk.StringVar(value="")
        self.remember = tk.BooleanVar(value=False)
        self.key_source = tk.StringVar(value="不使用密钥（手动流程）")
        self.sources = {"本次输入": "input", "服务端环境变量": "environment", "本机已保存": "saved", "不使用密钥（手动流程）": "none"}
        for row, (label, variable) in enumerate((("接口地址", self.endpoint), ("模型名称", self.model))):
            ttk.Label(model, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(model, textvariable=variable, width=86).grid(row=row, column=1, columnspan=3, sticky="ew")
        ttk.Checkbutton(model, text="启用真实模型理解（连接与业务质量须分别验证）", variable=self.enabled).grid(row=2, column=1, columnspan=3, sticky="w", pady=4)
        ttk.Label(model, text="API密钥").grid(row=3, column=0, sticky="w")
        self.key_entry = ttk.Entry(model, textvariable=self.key, show="●", width=48)
        self.key_entry.grid(row=3, column=1, sticky="ew")
        self.key_entry.bind("<KeyRelease>", lambda _: self.key_source.set("本次输入") if self.key.get() else None)
        self.key.trace_add("write", lambda *_: self.key_source.set("本次输入") if self.key.get() else None)
        self.source_combo = ttk.Combobox(model, state="readonly", values=list(self.sources), textvariable=self.key_source, width=26)
        self.source_combo.grid(row=3, column=2, columnspan=2, padx=6, sticky="w")
        ttk.Checkbutton(model, text="在本机记住密钥（明文文件，默认不保存）", variable=self.remember).grid(row=4, column=1, columnspan=3, sticky="w", pady=5)
        self.source_note = tk.StringVar()
        ttk.Label(model, textvariable=self.source_note, wraplength=885).grid(row=5, column=0, columnspan=4, sticky="w")
        ttk.Label(model, text="以明确选择的来源为准；输入新密钥会切换到本次输入。默认：环境变量 → 本机已存 → 手动，已有密钥不回填。", wraplength=885).grid(row=6, column=0, columnspan=4, sticky="w", pady=4)
        row = ttk.Frame(model)
        row.grid(row=7, column=0, columnspan=4, sticky="w")
        for name, label, fn in [("save", "保存配置 / 按选择记住密钥", self.save), ("clear", "清除本机已存密钥", self.clear_key), ("check", "检查模型连接", self.check_model)]:
            self.buttons[name] = ttk.Button(row, text=label, command=fn)
            self.buttons[name].pack(side="left", padx=(0, 7))
        self.check_text = tk.StringVar(value=self.last_check)
        ttk.Label(model, textvariable=self.check_text, wraplength=900).grid(row=8, column=0, columnspan=4, sticky="w", pady=7)
        self.active_model_text = tk.StringVar(value="当前没有本控制台运行中的服务；上方表单用于下一次启动或检查")
        ttk.Label(model, textvariable=self.active_model_text, wraplength=900, foreground="#205c52").grid(row=9, column=0, columnspan=4, sticky="w")
        ttk.Label(frame, text="接口/密钥的修改不会更新正在运行的服务，需停止后重新启动。未点击保存的接口更改仅用于本次操作。", wraplength=930).pack(anchor="w", pady=(9, 3))
        self.show_diagnostics = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="展开诊断信息", variable=self.show_diagnostics, command=self.toggle_diagnostics).pack(anchor="w")
        self.diagnostics = ttk.Frame(frame)
        self.diagnostic_text = tk.StringVar()
        ttk.Label(self.diagnostics, textvariable=self.diagnostic_text, wraplength=920).pack(anchor="w")
        ttk.Button(frame, text="创建桌面快捷方式", command=self.create_shortcut).pack(anchor="w", pady=4)
        window.protocol("WM_DELETE_WINDOW", self.request_exit)
        window.bind("<Destroy>", self.cancel_tick, add="+")
        self.update_source(default=True)
        self.tick()

    def info(self, text, error=False):
        self.notice = text

    def select_directory(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(parent=self.window, title="选择服务状态目录（不会清空历史）", initialdir=str(self.root))
        if path:
            self.state_dir.set(path)
            self.info("已选择状态目录，旧位置未迁移或清空；不要与其他服务同时使用此目录")

    def copy_address(self):
        self.window.clipboard_clear()
        self.window.clipboard_append(self.controller.url + "/pipeline")
        self.info("访问地址已复制；普通浏览器标签页需要手动关闭，停止服务是独立操作")

    def config(self):
        from integration.local_settings import validate_config
        return validate_config({**self.base_config, "endpoint": self.endpoint.get().strip(), "model": self.model.get().strip(), "enabled": self.enabled.get()})

    def selected_key(self, config):
        from integration.local_settings import choose_key
        return choose_key(self.sources[self.key_source.get()], config, self.store, self.key.get())

    def update_source(self, default=False):
        name = self.base_config.get("api_key_env", "INTEGRATION_MODEL_API_KEY")
        try:
            saved = bool(self.store.read().get(name))
            if default:
                self.key_source.set("服务端环境变量" if os.environ.get(name) else "本机已保存" if saved else "不使用密钥（手动流程）")
            self.source_note.set("本机文件：" + ("已保存当前接口密钥（明文）" if saved else "未保存") + "；服务端环境：" + ("已设置" if os.environ.get(name) else "未设置") + "；配置来源：config/model_config.local.json")
        except ValueError as exc:
            self.source_note.set(str(exc))

    def submit(self, fn, done=None):
        if self.pending:
            return
        self.pending = True
        self.notice = ""
        def run():
            try:
                value = fn()
                self.events.put(("done", done, value))
            except ValueError as exc:
                self.events.put(("error", None, str(exc)))
            except Exception as exc:
                self.events.put(("error", None, "本次操作未完成，异常类型：" + type(exc).__name__ + "；可重试，未清空数据"))
        threading.Thread(target=run, daemon=True).start()

    def start(self):
        if self.pending:
            return
        try:
            port = int(self.port.get())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            self.info("端口须为1至65535之间的整数")
            return
        try:
            config = self.config()
            key = self.selected_key(config)
            if self.remember.get():
                if self.key_source.get() != "本次输入":
                    raise ValueError("记住或更新密钥时请选择本次输入并填写密钥；未改动现有密钥")
                self.store.save(config.get("api_key_env", "INTEGRATION_MODEL_API_KEY"), key)
                self.update_source()
            self.controller.port, self.controller.state_dir = port, Path(self.state_dir.get()).resolve()
        except ValueError as exc:
            self.info(str(exc))
            return
        source = self.key_source.get()
        self.active_key_source = source
        self.submit(lambda: self.controller.start(config, key), lambda success: self.started(success, source))

    def started(self, success, source):
        if success:
            self.active_key_source = source
        self.info(self.controller.message + "；本次密钥来源：" + source)

    def stop(self):
        self.submit(self.controller.stop, lambda _: self.info(self.controller.message))

    def force(self):
        from tkinter import messagebox
        if messagebox.askyesno("确认强制停止", "只强制结束本控制台启动的服务。正在处理的任务可能中断。是否继续？", parent=self.window):
            self.submit(lambda: self.controller.force_stop(confirmed=True), lambda _: self.info(self.controller.message))

    def open_page(self):
        self.submit(lambda: self.page.open(self.controller.url), lambda _: self.info(self.page.message))

    def open_existing(self):
        import webbrowser
        webbrowser.open(self.controller.url + "/pipeline")
        self.info("已请求打开已有服务的普通标签页，未接管服务或窗口；请手动关闭标签页")

    def close_page(self):
        self.submit(self.page.close, lambda _: self.info(self.page.message))

    def save(self):
        from integration.local_settings import atomic_json, read_config
        from tkinter import messagebox
        key_saved = False
        try:
            config = self.config()
            # Reload to avoid silently dropping legal configuration changed outside this console.
            try:
                previous = read_config(self.root / "config/model_config.local.json")
            except ValueError:
                if not self.config_read_error or not messagebox.askyesno("修正无法读取的配置", "当前模型配置无效。是否按表单替换普通配置文件？不会保存密钥字段，也不影响上传和历史状态。", parent=self.window):
                    raise ValueError("原配置未覆盖；可继续使用手动模式，或修正配置后重新打开控制台")
                previous = self.base_config
            config = {**previous, **{k: config[k] for k in ("endpoint", "model", "enabled")}}
            if self.remember.get():
                if self.key_source.get() != "本次输入":
                    raise ValueError("若要更新已存密钥，请选择本次输入并填写新密钥；未改动现有密钥")
                self.store.save(config.get("api_key_env", "INTEGRATION_MODEL_API_KEY"), self.key.get())
                key_saved = True
            atomic_json(self.root / "config/model_config.local.json", config)
            self.base_config = config
            self.config_read_error = None
            self.info("配置已保存；" + ("已按明确选择将密钥保存为本机明文文件" if self.remember.get() else "未写入密钥，仅本次输入仍只在内存中") + "。正在运行的服务需重启生效")
            self.update_source()
        except ValueError as exc:
            self.update_source()
            self.info(("密钥已按选择保存，但接口配置尚未保存；" if key_saved else "") + str(exc))

    def clear_key(self):
        from tkinter import messagebox
        if messagebox.askyesno("清除本机密钥", "清除当前接口在本机文件中保存的密钥。不会清除环境变量或已运行服务内存，重启后生效。", parent=self.window):
            try:
                self.store.clear(self.base_config.get("api_key_env", "INTEGRATION_MODEL_API_KEY"))
                self.key.set("")
                self.remember.set(False)
                self.key_source.set("不使用密钥（手动流程）")
                self.update_source()
                self.info("当前本机已存密钥已清除；已选择手动来源，正在运行的服务需重启生效")
            except ValueError as exc:
                self.info(str(exc))

    def check_model(self):
        from tools.console_core import probe_model
        try:
            config, key = self.config(), self.selected_key(self.config())
            config["enabled"] = True  # Probe only; neither save nor silently enable the actual service.
        except ValueError as exc:
            self.info(str(exc))
            return
        self._probe_description = config["model"] + " / " + config["endpoint"] + "；密钥来源：" + self.key_source.get()
        self.check_text.set("正在检查当前表单：" + self._probe_description + "。不发送教师文件；业务质量未验证")
        self.submit(lambda: probe_model(self.root, config, key), self.checked)

    def checked(self, value):
        self.last_check = "检查对象：" + self._probe_description + "。" + value.get("message", "检查未完成") + "；真实任务理解质量：未验证"
        self.check_text.set(self.last_check)

    def toggle_diagnostics(self):
        if self.show_diagnostics.get():
            self.diagnostics.pack(fill="x", pady=4)
        else:
            self.diagnostics.pack_forget()

    def create_shortcut(self):
        self.submit(lambda: subprocess.run(["cscript.exe", "//B", "//Nologo", str(self.root / "tools/create_shortcut.vbs")],
                                           capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode,
                    lambda code: self.info("桌面快捷方式已创建" if code == 0 else "快捷方式未创建，可能已存在同名入口；可直接双击项目中的启动控制台.vbs"))

    def request_exit(self):
        from tkinter import ttk
        if self.exit_pending or self.exit_dialog:
            return
        active = self.controller.refresh() in ("starting", "running", "stopping") or self.controller.process and self.controller.process.poll() is None
        if not active and self.page.refresh() == "closed" and not self.pending:
            self.window.destroy()
            return
        dialog = self.tk.Toplevel(self.window)
        self.exit_dialog = dialog
        dialog.title("退出项目控制台")
        dialog.transient(self.window)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        text = ("是否停止本控制台服务并退出？" if active else "本控制台服务已经停止，是否退出？")
        ttk.Label(frame, text=text + "\n返回控制台会保持当前状态；不会停止其他已有服务。\n上传、结果、决定和审计均保留。").pack(anchor="w")
        close_page = self.tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="同时关闭本控制台可核验的项目页面", variable=close_page).pack(anchor="w", pady=8)
        def cancel():
            dialog.destroy()
            self.exit_dialog = None
            self.info("已取消退出；服务和项目页面保持实际状态")
        def accept():
            if self.pending:
                self.info("当前操作尚未完成，请稍后再退出；未自动强制结束")
                cancel()
                return
            self.close_on_exit = close_page.get()
            dialog.destroy()
            self.exit_dialog = None
            self.exit_pending = True
            self.submit(self.controller.stop, self.exit_stopped)
        self.exit_cancel = ttk.Button(frame, text="返回控制台", command=cancel)
        self.exit_cancel.pack(side="left", padx=5)
        self.exit_accept = ttk.Button(frame, text="停止服务并退出", command=accept)
        self.exit_accept.pack(side="left", padx=5)
        dialog.protocol("WM_DELETE_WINDOW", cancel)

    def exit_stopped(self, success):
        from tkinter import messagebox
        if not success:
            self.exit_pending = False
            self.info("服务尚未停止，控制台保持打开；请继续等待或明确选择强制停止")
            return
        mode = self.page.refresh()
        if self.close_on_exit and self.page.owned():
            self.submit(self.page.close, self.exit_page_closed)
        else:
            page = ("普通浏览器页面请手动关闭，未声称已关闭。" if mode == "unmanaged" else
                    "项目页面已关闭。" if mode == "closed" else "项目页面按选择保留。")
            messagebox.showinfo("退出结果", "本控制台服务已停止；" + page + "历史数据未清空。", parent=self.window)
            self.window.destroy()

    def exit_page_closed(self, success):
        from tkinter import messagebox
        if not success:
            self.exit_pending = False
            self.info("服务已停止，但页面尚未关闭；请手动处理浏览器提示后再退出。" + self.page.message)
            return
        messagebox.showinfo("退出结果", "本控制台服务已停止，项目页面已关闭。历史数据未清空。", parent=self.window)
        self.window.destroy()

    def tick(self):
        from tools.console_core import STATES
        try:
            while True:
                kind, done, value = self.events.get_nowait()
                self.pending = False
                if kind == "error":
                    self.info(value)
                    self.exit_pending = False
                elif done:
                    done(value)
        except queue.Empty:
            pass
        try:
            if not self.window.winfo_exists():
                return
        except self.tk.TclError:
            return
        state = self.controller.refresh()
        self.status.set(STATES[state])
        if state == "running":
            c = self.controller.active_config
            self.active_model_text.set("运行中实际采用：" + c["model"] + " / " + c["endpoint"] + "；密钥来源：" + str(self.active_key_source)
                                       + ("；模型理解未启用，使用手动流程" if not c.get("enabled") or self.active_key_source == "不使用密钥（手动流程）" else "；连接及真实理解质量另行验证"))
        elif state in ("starting", "stopping"):
            self.active_model_text.set("服务当前为" + STATES[state] + "，请等待实际结果；表单修改不会更新正在退出的服务")
        else:
            self.active_model_text.set("当前没有本控制台运行中的服务；上方表单用于下一次启动或检查")
        self.address.set(self.controller.url + "/pipeline")
        self.page_status.set(self.page.message)
        self.page.refresh()
        self.feedback.set(self.notice or self.controller.message)
        owned_alive = self.controller.process is not None and self.controller.process.poll() is None
        enabled = {"start": state in ("stopped", "failed") and not owned_alive,
                   "open": state == "running", "close": self.page.owned(), "stop": owned_alive and state != "stopping",
                   "exit": True, "save": True, "clear": True, "check": True}
        for name, button in self.buttons.items():
            button.configure(state="normal" if enabled[name] and not self.pending else "disabled")
        # During a stop timeout the normal stop button means wait again, never force silently.
        if self.controller.stop_timed_out and not self.pending:
            self.buttons["stop"].configure(state="normal", text="继续等待停止")
            self.force_button.pack(side="left", padx=3)
        else:
            self.buttons["stop"].configure(text="停止服务")
            self.force_button.pack_forget()
        self.force_button.configure(state="disabled" if self.pending else "normal")
        if self.controller.foreign:
            self.foreign_button.pack(side="left", padx=3)
        else:
            self.foreign_button.pack_forget()
        mutable = not self.pending and not owned_alive and state not in ("starting", "stopping")
        for widget in (self.port_entry, self.state_entry, self.browse):
            widget.configure(state="normal" if mutable else "disabled")
        self.diagnostic_text.set("Python：" + sys.version.split()[0] + "；解释器：" + sys.executable
                                 + "\n配置：" + str(self.root / "config/model_config.local.json")
                                 + "\n控制方式：本控制台进程句柄 + 私有标准输入管道（网页无停止接口）\n" + self.controller.diagnostic
                                 + ("\n界面异常类型：" + ",".join(self.callback_errors) if self.callback_errors else ""))
        self._after_id = self.window.after(200, self.tick)

    def cancel_tick(self, event):
        if event.widget is self.window and getattr(self, "_after_id", None):
            try:
                self.window.after_cancel(self._after_id)
            except self.tk.TclError:
                pass


def main():
    import tkinter as tk
    parser = argparse.ArgumentParser(description="教师本机项目控制台")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--state-dir", type=Path)
    args = parser.parse_args()
    root = tk.Tk()
    Console(root, port=args.port, state_dir=args.state_dir)
    root.mainloop()


if __name__ == "__main__":
    try:
        if sys.version_info < (3, 11):
            raise RuntimeError("python_version")
        main()
    except Exception:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, "控制台未能启动。请确认已安装Python 3.11及以上且包含Tcl/Tk，源码包文件齐全，并将项目解压到可写目录。可使用原start.cmd诊断。", "教师项目控制台", 0x10)
