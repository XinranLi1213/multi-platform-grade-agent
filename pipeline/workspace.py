"""Read-only conversation index and verified file previews for the teacher workspace."""
import csv
import io
import json
import re
from datetime import datetime, timezone

from .common import PipelineError, sha
from .ingest import parse_file


def history(store):
    items = []
    with store.lock:
        for path in store.root.glob("*/workflow.json"):
            if not re.fullmatch(r"[a-f0-9]{32}", path.parent.name):
                continue
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                item = {"id": path.parent.name, "task": str(state.get("task", "未命名任务"))[:200],
                        "revision": state.get("revision"), "phase": state.get("phase"),
                        "files": [f["name"] for f in state.get("files", [])]}
            except (OSError, ValueError, KeyError, TypeError):
                item = {"id": path.parent.name, "task": "暂时无法读取的历史任务", "phase": "unavailable", "files": []}
            item["updated_at"] = state.get("updated_at") if item["phase"] != "unavailable" else None
            item["updated_at"] = item["updated_at"] or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
            item["created_at"] = state.get("created_at") if item["phase"] != "unavailable" else None
            item["time_label"] = "最后处理" if item["created_at"] else "最后保存"
            items.append(item)
    return {"items": sorted(items, key=lambda item: (item["updated_at"], item["id"]), reverse=True)}


def original(store, state, file_id):
    file = next((f for f in state["files"] if f["id"] == file_id), None)
    if file is None or not re.fullmatch(r"F[1-6]", file_id):
        raise PipelineError("该任务中不存在这个上传文件")
    path = store.directory(state["id"]) / "input" / (file_id + ".original")
    if not path.is_file() or sha(path) != file["sha256"]:
        raise PipelineError("原始上传文件缺失或完整性检查失败；未加载替代文件")
    return path.read_bytes(), file["name"]


def preview(store, run_id, artifact, revision):
    from .service import RevisionError
    with store.lock:
        state = store.load(run_id)
        if type(revision) is not int or revision != state["revision"]:
            raise RevisionError("任务版本已变化，请重新加载任务后选择文件")
        base = {"task_id": run_id, "revision": revision, "artifact": artifact}
        if artifact.startswith("input-"):
            file_id = artifact[6:]
            raw, name = original(store, state, file_id)
            parsed = parse_file(file_id, name, raw)
            records = parsed["raw_rows"]
            actual_width = max((len(r["cells"]) for r in records), default=0)
            width = min(actual_width, 128)
            columns = ["原始位置"] + ["原始列" + str(i + 1) for i in range(width)]
            rows = [{"原始位置": f"第{r['line']}行" if r['line'] == r['end_line'] else f"第{r['line']}—{r['end_line']}行",
                     **{columns[i + 1]: r["cells"][i] if i < len(r["cells"]) else "" for i in range(width)}} for r in records[:100]]
            return {**base, "name": name, "columns": columns, "rows": rows, "total_rows": len(records),
                    "status": "原始上传", "version_label": "原始上传 · " + file_id,
                    "notes": ["原文件按实际CSV记录显示，说明行、表头和汇总行均保留", *parsed["errors"],
                              *([f"原文件最多{actual_width}列；预览前128列，下载保留全部原始内容"] if actual_width > 128 else [])],
                    "download_url": f"/api/pipelines/{run_id}/original/{file_id}"}
        if artifact not in ("dwd", "dws", "accepted"):
            raise PipelineError("未知任务文件")
        raw, _, name = store.download(run_id, artifact)
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
        rows = list(reader)
        stage = state.get("stage1") if artifact == "dwd" else state.get("stage2") if artifact == "dws" else state.get("accepted_result")
        return {**base, "name": name, "columns": reader.fieldnames or [], "rows": rows[:100], "total_rows": len(rows),
                "version_label": "整理版本 " + str(stage["version"]) if artifact == "dwd" else "第" + str(stage["trial_id"]) + "次计算",
                "status": "待确认的整理宽表" if artifact == "dwd" and stage.get("status") != "ready" else
                          "已采用结果" if artifact == "accepted" else
                          "当前试算结果" if artifact == "dws" and stage.get("mode") == "trial" else
                          "当前计算结果" if artifact == "dws" else "阶段一整理宽表",
                "notes": ["从持久化文件读取，已执行现有哈希及版本校验；预览与下载使用同一文件"],
                "download_url": f"/api/pipelines/{run_id}/download?kind={artifact}"}
