"""Optional real model transport. Disabled without explicit configuration; never fabricates advice."""

import json
import os
from pathlib import Path
from .model_client import configuration, complete_json, ModelFailure


def load_config(path=None):
    path = Path(path or os.environ.get("INTEGRATION_MODEL_CONFIG", Path(__file__).resolve().parents[1] / "model_config.local.json"))
    if not path.exists():
        return {"enabled": False, "_config_error": "config_missing"}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {"enabled": False, "_config_error": "config_invalid"}
    except (OSError, ValueError):
        return {"enabled": False, "_config_error": "config_invalid"}


def model_status(config):
    try:
        configuration(config)
        available, code, message = True, "configured", "真实模型接口已配置（连通性待请求验证）"
    except ModelFailure as exc:
        available, code, message = False, exc.code, "语义分析尚未启用；" + exc.message
    return {"enabled": available, "label": message, "code": code, "connection_checked": False,
            "samples_enabled": available and config.get("allow_sample_values") is True,
            "disclosure": "通用工作台点击请求时发送；教师双阶段页在提交任务/追加指令后自动发送任务、真实表头及程序统计。配置允许后才发送每表最多3条真实样例。方案由程序校验，歧义需补充。"}


def validate_advice(value, files):
    if not isinstance(value, dict) or not isinstance(value.get("mappings"), list):
        raise ValueError("模型建议不是有效结构")
    known = {f["id"]: set(f["headers"]) for f in files}
    clean, seen = [], set()
    for entry in value["mappings"]:
        if not isinstance(entry, dict):
            raise ValueError("无效映射")
        file_id, column, target = entry.get("file"), entry.get("column"), entry.get("target")
        if (file_id not in known or column not in known[file_id] or (file_id, column) in seen
                or not isinstance(target, str) or not target.strip() or target.startswith("__") or len(target) > 80):
            raise ValueError("模型建议引用未知字段、重复字段或无效目标")
        seen.add((file_id, column))
        clean.append({"file": file_id, "column": column, "target": target, "reason": str(entry.get("reason", "待用户确认"))[:2000]})
    keys = value.get("key_fields", [])
    questions = value.get("questions", [])
    if (not isinstance(keys, list) or len(keys) > 3 or any(not isinstance(k, str) for k in keys)
            or not isinstance(questions, list) or any(not isinstance(q, str) for q in questions)):
        raise ValueError("关联键或问题结构无效")
    candidate_plan = {f["id"]: {h: h for h in f["headers"]} for f in files}
    for entry in clean:
        candidate_plan[entry["file"]][entry["column"]] = entry["target"]
    for mapping in candidate_plan.values():
        if len(mapping.values()) != len(set(mapping.values())):
            raise ValueError("模型将同一文件多个字段映射到同一列，可能丢值")
    common = set.intersection(*(set(v.values()) for v in candidate_plan.values()))
    if not set(keys) <= common or len(keys) != len(set(keys)):
        raise ValueError("模型关联键不在两侧实际映射字段中")
    return {"mappings": clean, "key_fields": keys, "questions": [q[:2000] for q in questions[:10]],
            "row_meaning": str(value.get("row_meaning", ""))[:2000], "rationale": str(value.get("rationale", ""))[:4000]}


def get_advice(config, task, files):
    if not model_status(config)["enabled"]:
        return {"status": "disabled", "message": model_status(config)["label"]}
    metadata = [{"file": f["id"], "name": f["name"], "headers": f["headers"], "row_count": f["row_count"],
                 "column_statistics": f["profile"].get("columns", {})} for f in files]
    # Distinct-value summaries may reveal content; send counts only unless samples are explicitly enabled.
    for f in metadata:
        f["column_statistics"] = {h: {k: v for k, v in stats.items() if k != "duplicate_values"}
                                  for h, stats in f["column_statistics"].items()}
    if config.get("allow_sample_values") is True:
        for item, f in zip(metadata, files):
            item["samples"] = [r["values"] for r in f["rows"][:3]]
    instructions = ("只提出方案，不执行。区分同义字段、同一对象、重复业务记录；不得仅按姓名关联，"
        "不得补值或猜测。不能确定时保留不同字段并提问。输出JSON对象，包含mappings数组"
        "（每项file=A或B,column=原列名,target=建议统一列名,reason=依据）、key_fields数组、"
        "questions数组、row_meaning、rationale。不能将同一文件两列归到同一目标。不要输出确认标记。")
    messages = [{"role": "system", "content": instructions},
        {"role": "user", "content": json.dumps({"task": task, "files": metadata}, ensure_ascii=False)}]
    try:
        return {"status": "ok", "advice": validate_advice(complete_json(config, messages), files)}
    except ModelFailure as exc:
        return exc.public()
    except Exception:
        return {"status": "error", "code": "plan_validation", "message": "模型建议未通过结构与真实字段检查；手动流程保留，未生成替代建议"}
