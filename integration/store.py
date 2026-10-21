"""Disk-backed sessions, immutable uploads, optimistic revisions, stale-result invalidation."""

import copy
import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .csv_io import read_upload
from .engine import analyze, default_plan, execute
from .models import get_advice


class StateError(ValueError):
    pass


class StaleState(StateError):
    pass


class SessionStore:
    def __init__(self, root, model_config=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.model_config = model_config or {"enabled": False}
        self.lock = threading.RLock()

    def directory(self, sid):
        if not re.fullmatch(r"[a-f0-9]{32}", sid):
            raise StateError("无效会话编号")
        return self.root / sid

    def save(self, state):
        directory = self.directory(state["id"])
        temporary = directory / "session.tmp"
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, directory / "session.json")

    def load(self, sid):
        with self.lock:
            path = self.directory(sid) / "session.json"
            if not path.exists():
                raise StateError("会话不存在；可重新上传创建新会话")
            return json.loads(path.read_text(encoding="utf-8"))

    def create(self, task, uploads):
        if not isinstance(task, str) or not task.strip() or len(task) > 5000:
            raise StateError("任务必须为1至5000个字符的自然语言描述")
        if len(uploads) != 2:
            raise StateError("本轮需要恰好两个文件")
        with self.lock:
            sid = uuid.uuid4().hex
            directory = self.directory(sid)
            directory.mkdir()
            files = []
            for file_id, (name, content) in zip(("A", "B"), uploads):
                (directory / f"{file_id}.original").write_bytes(content)
                files.append(read_upload(file_id, name, content))
            state = {"id": sid, "task": task, "revision": 0,
                "created_at": datetime.now(timezone.utc).isoformat(), "files": files,
                "plan": default_plan(files), "result": None, "suggestion": None, "events": []}
            state["analysis"] = analyze(task, files, state["plan"])
            self.save(state)
            return state

    def check_revision(self, state, revision):
        if type(revision) is not int or revision != state["revision"]:
            raise StaleState("会话已更新，请刷新并重新核对方案；旧版本不能执行或覆盖新结果")

    def update(self, sid, revision, plan, task=None):
        with self.lock:
            state = self.load(sid)
            self.check_revision(state, revision)
            if not isinstance(plan, dict):
                raise StateError("方案必须是JSON对象")
            # Execution confirmations are accepted only by execute, never from a model or plan payload.
            plan = {k: copy.deepcopy(plan.get(k)) for k in default_plan(state["files"])}
            if task is not None:
                if not isinstance(task, str) or not task.strip() or len(task) > 5000:
                    raise StateError("任务描述为空或过长")
                state["task"] = task
                state["suggestion"] = None
            state["revision"] += 1
            state["plan"] = plan
            state["result"] = None
            state["analysis"] = analyze(state["task"], state["files"], plan)
            state["events"].append({"action": "plan_updated_results_invalidated", "revision": state["revision"]})
            self.save(state)
            return state

    def invalidate(self, sid, revision):
        with self.lock:
            state = self.load(sid)
            self.check_revision(state, revision)
            state["revision"] += 1
            state["result"] = None
            state["events"].append({"action": "editing_started_results_invalidated", "revision": state["revision"]})
            self.save(state)
            return state

    def execute(self, sid, revision, confirmations):
        with self.lock:
            state = self.load(sid)
            self.check_revision(state, revision)
            result = execute(state["task"], state["files"], state["plan"], confirmations)
            result["revision"] = revision
            state["result"] = result
            state["events"].append({"action": "executed", "revision": revision, "counts": result["counts"]})
            self.save(state)
            directory = self.directory(sid)
            (directory / f"result-v{revision}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            return state

    def suggest(self, sid, revision):
        state = self.load(sid)
        self.check_revision(state, revision)
        advice = get_advice(self.model_config, state["task"], state["files"])
        with self.lock:
            latest = self.load(sid)
            self.check_revision(latest, revision)
            latest["suggestion"] = advice
            self.save(latest)
            return latest

    def current_result(self, sid):
        state = self.load(sid)
        if not state["result"] or state["result"]["revision"] != state["revision"]:
            raise StaleState("结果尚未执行或已因方案修改失效；请重新核对并执行")
        return state, state["result"]
