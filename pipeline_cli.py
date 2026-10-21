"""Batch entry for exactly the same persisted workflow used by the interactive UI."""
import argparse
import json
from pathlib import Path

from pipeline.service import PipelineStore
from integration.models import load_config


def main():
    parser = argparse.ArgumentParser(description="DWD→检查点→DWS双阶段执行")
    parser.add_argument("--task", required=True)
    parser.add_argument("--file", type=Path, action="append", required=True)
    parser.add_argument("--state-dir", type=Path, default=Path("state"))
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--rules", type=Path)
    parser.add_argument("--model-config", type=Path)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8-sig")) if args.plan else None
    rules = json.loads(args.rules.read_text(encoding="utf-8-sig")) if args.rules else None
    state = PipelineStore(args.state_dir, load_config(args.model_config)).create(args.task, [(p.name, p.read_bytes()) for p in args.file], plan, rules)
    print(json.dumps({"id": state["id"], "phase": state["phase"], "stage1": state["stage1"] and state["stage1"]["directory"],
        "stage2": state["stage2"] and state["stage2"]["directory"], "issues": state["issues"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
