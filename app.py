"""Local interactive CSV integration server. Formal entry only handles uploaded files."""

import argparse
import base64
import binascii
import csv
import io
import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from integration.engine import PlanError, export_view
from integration.models import load_config, model_status
from integration.model_client import check_connection
from integration.store import SessionStore, StaleState, StateError
from pipeline.common import PipelineError
from pipeline.service import PipelineStore, RevisionError

WEB = Path(__file__).resolve().parent / "web"


def create_server(host="127.0.0.1", port=8765, state_dir="state", model_config=None):
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("试运行版只允许本机监听；另一台电脑请独立启动，不开放远程访问")
    store = SessionStore(state_dir, model_config)
    pipelines = PipelineStore(state_dir, model_config)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # No upload data, model payloads, paths or credentials in HTTP logs.

        def respond(self, content, content_type="application/json; charset=utf-8", status=200, filename=None):
            if isinstance(content, (dict, list)):
                content = json.dumps(content, ensure_ascii=False).encode("utf-8")
            elif isinstance(content, str):
                content = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if filename:
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(filename))
            self.end_headers()
            self.wfile.write(content)

        def check_origin(self):
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if self.headers.get("Host") not in allowed:
                raise StateError("仅接受本机访问")
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + host for host in allowed}:
                raise StateError("不接受跨站请求")

        def do_GET(self):
            try:
                self.check_origin()
                path = urllib.parse.urlparse(self.path)
                if path.path in ("/", "/app.js", "/style.css", "/pipeline", "/pipeline.js", "/semantic.js"):
                    name = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/pipeline": "pipeline.html", "/pipeline.js": "pipeline.js", "/semantic.js": "semantic.js"}[path.path]
                    kind = "text/html" if name.endswith(".html") else "text/javascript" if name.endswith(".js") else "text/css"
                    return self.respond((WEB / name).read_bytes(), kind + "; charset=utf-8")
                if path.path == "/api/config":
                    return self.respond({"model": model_status(store.model_config), "last_model_check": server.last_model_check,
                        "limits": {"files": 2, "max_mib": 2, "max_rows": 5000},
                        "pipeline_limits": {"min_files": 2, "max_files": 6, "max_mib": 2, "max_records": 5100}})
                if path.path == "/api/health":
                    return self.respond({"status": "ok", "version": (WEB.parent / "VERSION").read_text(encoding="utf-8").strip(),
                        "python": sys.version.split()[0], "loopback_only": True,
                        "model": model_status(store.model_config), "last_model_check": server.last_model_check})
                parts = path.path.strip("/").split("/")
                if len(parts) >= 3 and parts[:2] == ["api", "pipelines"]:
                    if len(parts) == 3:
                        return self.respond(pipelines.load(parts[2]))
                    if len(parts) == 4 and parts[3] == "download":
                        kind = urllib.parse.parse_qs(path.query).get("kind", ["audit"])[0]
                        content, content_type, filename = pipelines.download(parts[2], kind)
                        return self.respond(content, content_type, filename=filename)
                    return self.respond({"error": "双阶段接口不存在"}, status=404)
                if len(parts) < 3 or parts[:2] != ["api", "sessions"]:
                    return self.respond({"error": "接口不存在"}, status=404)
                sid = parts[2]
                if len(parts) == 3:
                    return self.respond(store.load(sid))
                if parts[3] == "original" and len(parts) == 5 and parts[4] in ("A", "B"):
                    state = store.load(sid)
                    f = next(f for f in state["files"] if f["id"] == parts[4])
                    return self.respond((store.directory(sid) / (parts[4] + ".original")).read_bytes(),
                                        "application/octet-stream", filename=f["name"])
                if len(parts) == 4 and parts[3] == "download":
                    state, result = store.current_result(sid)
                    kind = urllib.parse.parse_qs(path.query).get("kind", ["result"])[0]
                    if kind == "audit":
                        return self.respond({"session": sid, "revision": state["revision"], "task": state["task"],
                            "result": result, "events": state["events"], "model_suggestion": state["suggestion"]}, filename="processing-details.json")
                    if kind not in ("result", "pending"):
                        raise StateError("未知下载类型")
                    columns, rows = export_view(result, pending=kind == "pending")
                    buffer = io.StringIO(newline="")
                    writer = csv.DictWriter(buffer, fieldnames=columns)
                    writer.writeheader()
                    writer.writerows(rows)
                    return self.respond(buffer.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", filename=kind + ".csv")
                return self.respond({"error": "接口不存在"}, status=404)
            except (StaleState, RevisionError) as exc:
                return self.respond({"error": str(exc)}, status=409)
            except PipelineError as exc:
                return self.respond({"error": str(exc)}, status=422)
            except StateError as exc:
                return self.respond({"error": str(exc)}, status=400)

        def do_POST(self):
            try:
                self.check_origin()
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 18 * 1024 * 1024:
                    raise StateError("请求为空或超过18 MiB限制")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise StateError("请求体须为JSON对象")
                parts = urllib.parse.urlparse(self.path).path.strip("/").split("/")
                if parts == ["api", "model", "check"]:
                    if data:
                        raise StateError("连接检查不接收配置或密钥；请只在服务端配置环境变量")
                    server.last_model_check = check_connection(store.model_config)
                    return self.respond(server.last_model_check)
                if parts == ["api", "pipelines"]:
                    files = data.get("files")
                    if not isinstance(files, list) or not 2 <= len(files) <= 6:
                        raise PipelineError("双阶段流程需要2至6份上传CSV")
                    uploads = []
                    for file in files:
                        if not isinstance(file, dict) or not isinstance(file.get("name"), str) or not 0 < len(file["name"]) <= 255:
                            raise PipelineError("文件名无效")
                        content = base64.b64decode(file.get("content_base64", ""), validate=True)
                        if len(content) > 2 * 1024 * 1024:
                            raise PipelineError("单份CSV不能超过2 MiB")
                        uploads.append((file["name"], content))
                    return self.respond(pipelines.create(data.get("task"), uploads, data.get("plan"), data.get("rules")), status=201)
                if len(parts) == 4 and parts[:2] == ["api", "pipelines"]:
                    run_id, action = parts[2], parts[3]
                    if action == "suggest":
                        result = pipelines.suggest(run_id, data.get("revision"))
                    elif action == "instruct":
                        result = pipelines.instruct(run_id, data.get("revision"), data.get("instruction"),
                                                    data.get("mode", "execute"), data.get("request_id"))
                    elif action == "adopt-trial":
                        result = pipelines.adopt_trial(run_id, data.get("revision"))
                    elif action == "resolve":
                        result = pipelines.resolve(run_id, data.get("revision"), data.get("decision"))
                    elif action == "layout":
                        result = pipelines.inspect_layout(run_id, data.get("revision"), data.get("file_id"), data.get("header_line"))
                    elif action == "check-rules":
                        result = pipelines.check_rules(run_id, data.get("revision"), data.get("rules"))
                    elif action == "prepare-rules":
                        result = pipelines.prepare_rules(run_id, data.get("revision"), data.get("rules"))
                    elif action == "preview-plan":
                        result = pipelines.preview_plan(run_id, data.get("revision"), data.get("plan"))
                    elif action == "apply-plan":
                        result = pipelines.apply_plan(run_id, data.get("revision"), data.get("plan"), data.get("confirmation"), data.get("reason"))
                    elif action in ("resume", "trial"):
                        result = pipelines.resume(run_id, data.get("revision"), data.get("plan"), data.get("rules"),
                                                  data.get("instruction"), trial=action == "trial", reason=data.get("reason"))
                    else:
                        return self.respond({"error": "双阶段动作不存在"}, status=404)
                    return self.respond(result)
                if parts == ["api", "sessions"]:
                    uploads = data.get("files")
                    if not isinstance(uploads, list) or len(uploads) != 2:
                        raise StateError("需要两个上传文件")
                    decoded = []
                    for upload in uploads:
                        if not isinstance(upload, dict) or not isinstance(upload.get("name"), str) or not upload["name"] or len(upload["name"]) > 255:
                            raise StateError("文件名无效")
                        value = base64.b64decode(upload.get("content_base64", ""), validate=True)
                        if len(value) > 2 * 1024 * 1024:
                            raise StateError("单文件超过2 MiB，本版未读取该文件")
                        decoded.append((upload["name"], value))
                    return self.respond(store.create(data.get("task"), decoded), status=201)
                if len(parts) != 4 or parts[:2] != ["api", "sessions"]:
                    return self.respond({"error": "接口不存在"}, status=404)
                sid, action, revision = parts[2], parts[3], data.get("revision")
                if action == "plan":
                    state = store.update(sid, revision, data.get("plan"), data.get("task"))
                elif action == "invalidate":
                    state = store.invalidate(sid, revision)
                elif action == "execute":
                    state = store.execute(sid, revision, data.get("confirmations"))
                elif action == "suggest":
                    state = store.suggest(sid, revision)
                else:
                    return self.respond({"error": "接口不存在"}, status=404)
                return self.respond(state)
            except (StaleState, RevisionError) as exc:
                return self.respond({"error": str(exc)}, status=409)
            except PipelineError as exc:
                return self.respond({"error": str(exc), "problem": getattr(exc, "problem", {"kind": "input", "reason": str(exc)})}, status=422)
            except PlanError as exc:
                return self.respond({"error": str(exc), "errors": exc.errors}, status=422)
            except (StateError, ValueError, TypeError, binascii.Error) as exc:
                return self.respond({"error": str(exc)}, status=400)
            except Exception as exc:
                return self.respond({"error": "服务发生未预期故障，请重试；上传与已保存状态保留",
                    "problem": {"kind": "system", "reason": "服务发生未预期故障，请重试；上传与已保存状态保留", "actions": ["重试"]},
                    "diagnostic": {"exception_type": type(exc).__name__}}, status=500)

    server = ThreadingHTTPServer((host, port), Handler)
    server.store = store
    server.pipelines = pipelines
    server.last_model_check = None
    return server


def main():
    parser = argparse.ArgumentParser(description="本地交互式两CSV清洗与整合（只处理用户上传数据）")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--state-dir", type=Path, default=Path(__file__).resolve().parent / "state")
    parser.add_argument("--model-config", type=Path)
    args = parser.parse_args()
    server = create_server(port=args.port, state_dir=args.state_dir, model_config=load_config(args.model_config))
    print(f"Open http://127.0.0.1:{server.server_port} | state={args.state_dir.resolve()}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
