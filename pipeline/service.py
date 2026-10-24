"""One workflow, persisted DWD boundary, resumable checkpoints and immutable DWS trials."""
import copy
import json
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .common import PipelineError, InputProblem, ExecutionFault, csv_bytes, dump, sha
from .dws import compare, generate_and_run, read_dwd
from .ingest import parse_file, suggest_plan
from .rules import infer_rules, compile_rules
from .interaction import validate_plan, presentation, plan_diff, token, issue_key, issue_guidance
from .semantic import fingerprint, profiles
from .intent import calculation_requested, wants_calculation
from integration.models import model_status


class RevisionError(PipelineError):
    pass


class PipelineStore:
    def __init__(self, root, model_config=None):
        self.root = Path(root).resolve() / "pipelines"
        self.root.mkdir(parents=True, exist_ok=True)
        self.model_config = model_config or {"enabled": False}
        self.lock = threading.RLock()

    def directory(self, run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise PipelineError("无效流程编号")
        return self.root / run_id

    def remember_business_facts(self, state, instruction):
        """Keep explicit future policies without turning data cleaning into scoring."""
        metrics = {m["target"] for p in state["plan"]["platforms"] for m in p.get("metrics", [])}
        try:
            parsed = infer_rules(instruction, metrics)
        except PipelineError:
            return  # Original instruction remains available for later conflict questions.
        facts = state.setdefault("known_business_facts", {})
        for key, value in parsed.items():
            if key == "source_instruction" or key == "missing" and not value:
                continue
            facts[key] = {**facts.get(key, {}), **value} if key == "missing" else copy.deepcopy(value)

    def load(self, run_id):
        path = self.directory(run_id) / "workflow.json"
        if not path.exists():
            raise PipelineError("流程不存在")
        with self.lock:
            state = json.loads(path.read_text(encoding="utf-8"))
            state.setdefault("updated_at", datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat())
            state.setdefault("instructions", [{"id": "initial", "text": state["task"], "mode": "execute"}])
            state.setdefault("decisions", [])
            state.setdefault("accepted_result", copy.deepcopy(state.get("last_successful_trial")))
            state.setdefault("requests", {})
            state.setdefault("calculation_requested", wants_calculation(state))
            if "known_business_facts" not in state:
                state["known_business_facts"] = {}
                for item in state["instructions"]:
                    self.remember_business_facts(state, item["text"])
            run_root = self.directory(run_id).resolve()
            artifacts = [state.get("stage1"), state.get("stage2"), state.get("accepted_result"), *state.get("trials", [])]
            foreign = any(a and a.get("directory") and not Path(a["directory"]).resolve().is_relative_to(run_root) for a in artifacts)
            binding = state.get("adjustment_binding")
            foreign = foreign or bool(binding and not Path(binding["path"]).resolve().is_relative_to(run_root))
            if foreign:
                state["migration_archive"] = {"phase": state["phase"], "stage1": state.get("stage1"), "stage2": state.get("stage2")}
                state.update(phase="migration_required", stage1=None, stage2=None, accepted_result=None,
                    migration_required=True, issues=[{"kind": "migration", "reason": "检测到历史状态引用其他目录。此状态仅供归档查看；请用原始CSV和已知规则在本机重新运行，旧调整基准不能自动替换。",
                        "required_input": "在上方重新选择原始CSV创建新流程；旧文件与审计保留"}])
            if (not wants_calculation(state) and (state.get("stage1") or {}).get("status") == "ready"
                    and state["phase"] == "business_checkpoint"
                    and all(q.get("kind") in ("business_rule", "missing_policy") for q in state.get("issues", []))):
                # Read-only compatibility for old workflows forced into DWS.
                # The persisted events/revision/hashes remain untouched.
                state.update(phase="organized", issues=[], stage2=None)
            state["view"] = presentation(state)
            return state

    def save(self, state):
        path = self.directory(state["id"]) / "workflow.json"
        previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        now = datetime.now(timezone.utc).isoformat()
        if previous is None:
            state.setdefault("created_at", now)
        state["updated_at"] = (previous.get("updated_at") or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()) if previous and previous["revision"] == state["revision"] else now
        state["semantic_plan"] = {"schema_version": 1, "revision": state["revision"],
            "calculation_requested": wants_calculation(state),
            "known_business_facts": copy.deepcopy(state.get("known_business_facts", {})),
            "data_plan": copy.deepcopy(state["plan"]), "business_rules": copy.deepcopy(state.get("rules")),
            "business_draft": copy.deepcopy(state.get("business_draft")),
            "decisions": copy.deepcopy(state.get("decisions", [])),
            "instruction_ids": [i["id"] for i in state.get("instructions", [])]}
        state["semantic_plan"]["dwd_manifest_sha256"] = (state.get("stage1") or {}).get("manifest_sha256")
        state["view"] = presentation(state)
        from .conversation import capture
        capture(state)
        directory = self.directory(state["id"])
        dump(directory / "semantic" / f"v{state['revision']}.json", state["semantic_plan"])
        dump(directory / "workflow.tmp", state)
        (directory / "workflow.tmp").replace(directory / "workflow.json")

    def check(self, state, revision):
        if state.get("migration_required"):
            raise InputProblem("历史状态包含旧路径，请使用原始CSV新建流程；未执行旧脚本或改写哈希", "migration")
        if type(revision) is not int or revision != state["revision"]:
            raise RevisionError("流程版本已变化，请刷新后使用当前版本继续")

    def checkpoint(self, state, phase, issues):
        if phase == 'data_checkpoint' and state.get('pending_clarifications'):
            issues = issues + [q for q in state['pending_clarifications'] if q.get('id') not in {i.get('id') for i in issues}]
        prompts = {"identity": ("选择主花名册编号并填写核对依据，或继续待确认", ["核对身份", "继续待确认"]),
            "deduplication": ("补充可核对的提交信息、明确裁决值或调整归并策略", ["填写裁决值与依据", "调整数据整理方式"]),
            "scale": ("明确原始满分、等级对应关系或裁决依据", ["调整分制", "填写裁决值与依据"]),
            "unmatched": ("明确追加主表或排除的依据，也可保留待确认", ["追加主花名册", "明确排除", "继续待确认"]),
            "reading": ("核对实际表头和原始文件结构", ["调整数据整理方式", "检查原始CSV"]),
            "missing_policy": ("明确该状态是否记0、保留标识或按明确值折算", ["补充状态处理规则"]),
            "business_rule": ("补充完整权重或明确计算公式", ["填写计算规则表单", "提供明确追加指令"]),
            "system": ("无需补充业务规则，可重试或在本机控制台查看诊断", ["重试", "在本机控制台查看诊断"])}
        impact = "相关记录待处理，阶段二尚未完成" if wants_calculation(state) else "本次整理要求尚未完成，原始上传保留"
        issues = [{"status": "本次暂停，原始上传保留", "impact": impact,
                   "required_input": prompts.get(q.get("kind"), ("核对对应字段设置", []))[0],
                   "actions": prompts.get(q.get("kind"), ("", ["调整数据整理方式"]))[1], **q} for q in issues]
        state.update(phase=phase, issues=issues, stage2=None)
        state["events"].append({"action": "checkpoint", "revision": state["revision"], "phase": phase, "issues": issues})

    def stage_one(self, state):
        validate_plan(state, state["plan"])
        destination = self.directory(state["id"]) / "dwd" / f"v{state['revision']}"
        destination.mkdir(parents=True, exist_ok=False)
        inputs_path, plan_path = destination / "inputs.json", destination / "plan.json"
        dump(inputs_path, state["files"])
        dump(plan_path, state["plan"])
        root = Path(__file__).resolve().parents[1]
        engine_hashes = {str(root / "pipeline" / name): sha(root / "pipeline" / name)
                         for name in ("common.py", "ingest.py", "dwd.py")}
        code = destination / "governance.py"
        script = ("# Generated deterministic DWD runner. No business weights or final-grade formulas.\n"
            "import sys, json\n" + f"sys.path.insert(0, {str(root)!r})\n"
            "from pipeline.common import sha, dump, PipelineError\n"
            "from pipeline.dwd import materialize\n"
            + f"INPUTS, PLAN, OUT = {str(inputs_path)!r}, {str(plan_path)!r}, {str(destination)!r}\n"
            + f"if sha(INPUTS) != {sha(inputs_path)!r} or sha(PLAN) != {sha(plan_path)!r}:\n"
            "    raise PipelineError('DWD input or governance-plan hash changed')\n"
            + f"SOURCE_HASHES = {engine_hashes!r}\n"
            "if any(sha(path) != expected for path, expected in SOURCE_HASHES.items()):\n"
            "    raise PipelineError('DWD engine version changed; create a new governance version')\n"
            "with open(INPUTS, encoding='utf-8') as handle: files = json.load(handle)\n"
            "with open(PLAN, encoding='utf-8') as handle: plan = json.load(handle)\n"
            "try:\n"
            "    result = materialize(files, plan, OUT)\n"
            "except PipelineError as exc:\n"
            "    dump(OUT + '/input_problem.json', {'kind':'plan','reason':str(exc),'status':'未生成有效整理结果','actions':['调整数据整理方式']})\n"
            "    sys.exit(2)\n"
            "dump(OUT + '/stage1_result.json', result)\n")
        code.write_text(script, encoding="utf-8", newline="\n")
        command = [sys.executable, "-I", "-B", "-X", "utf8", str(code)]
        try:
            process = subprocess.run(command, cwd=destination, stdin=subprocess.DEVNULL, capture_output=True,
                                     text=True, encoding="utf-8", timeout=30,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            dump(destination / "failure.json", {"reason": "阶段一Python执行超过30秒"})
            raise ExecutionFault("数据整理执行超时，请重试；上传数据已保留")
        if process.returncode:
            dump(destination / "failure.json", {"returncode": process.returncode, "error": process.stderr[-4000:]})
            if process.returncode == 2 and (destination / "input_problem.json").exists():
                problem = json.loads((destination / "input_problem.json").read_text(encoding="utf-8"))
                raise InputProblem(problem["reason"], problem["kind"], actions=problem["actions"])
            raise ExecutionFault("数据整理程序执行失败，请重试或在本机控制台查看诊断；上传数据已保留")
        state["stage1"] = json.loads((destination / "stage1_result.json").read_text(encoding="utf-8"))
        state["stage1"].update(command=command, generated_code_sha256=sha(code))
        dump(destination / "execution.json", {"command": command, "code_sha256": sha(code),
            "input_sha256": sha(inputs_path), "plan_sha256": sha(plan_path), "engine_hashes": engine_hashes,
            "python_version": sys.version, "dwd_manifest_sha256": state["stage1"]["manifest_sha256"]})
        state["stage1"]["version"] = state["revision"]
        state["last_successful_trial"] = None
        state["stage2"] = None
        state["accepted_result"] = None
        state.pop("adjustment_binding", None)
        state["events"].append({"action": "dwd_persisted", "revision": state["revision"],
            "manifest_sha256": state["stage1"]["manifest_sha256"], "counts": state["stage1"]["counts"]})
        if state["stage1"]["status"] != "ready":
            self.checkpoint(state, "data_checkpoint", state["stage1"]["issues"])
            return False
        return True

    def fault(self, state, exc, stage):
        state["stage2"] = None
        if stage == "DWD":
            state["stage1"] = None
            state["last_successful_trial"] = None
            state["accepted_result"] = None
            state.pop("adjustment_binding", None)
        state["diagnostic"] = {"stage": stage, "exception_type": type(exc).__name__, "detail": str(exc)}
        candidates = list((self.directory(state["id"]) / ("dwd" if stage == "DWD" else "dws")).glob("*/failure.json"))
        if candidates:
            latest = max(candidates, key=lambda p: p.stat().st_mtime_ns)
            state["diagnostic"]["worker_report"] = json.loads(latest.read_text(encoding="utf-8"))
        self.checkpoint(state, "system_error", [{"kind": "system", "reason": "程序执行未完成，请重试；原始上传与已保存的方案仍在。",
            "impact": "本次没有可用计算结果", "actions": ["重试", "在本机控制台查看诊断"]}])

    def stage_two(self, state, rules=None, instruction=None, trial=False):
        if state.get('pending_clarifications'):
            self.checkpoint(state,'understanding_checkpoint',state['pending_clarifications'])
            return
        stage1 = state.get("stage1")
        if not stage1 or stage1["status"] != "ready":
            self.checkpoint(state, "data_checkpoint", stage1["issues"] if stage1 else [{"reason": "阶段一尚未产出ready宽表"}])
            return
        if not wants_calculation(state):
            state.update(phase="organized", issues=[], stage2=None)
            state["events"].append({"action": "data_only_complete", "revision": state["revision"],
                                    "dwd_manifest_sha256": stage1["manifest_sha256"]})
            return
        manifest = Path(stage1["directory"]) / "manifest.json"
        try:
            try:
                data = read_dwd(manifest, stage1["manifest_sha256"])
            except PipelineError as exc:
                raise ExecutionFault(str(exc)) from exc
            previous = state.get("last_successful_trial")
            if rules is None:
                base = (state.get("rules") or state.get("known_business_facts") or (previous["rules"] if previous else None)) if instruction else state.get("known_business_facts")
                rules = infer_rules(instruction or state["task"], data["metrics"], base)
            state["rules"] = rules
            trial_id = len(state["trials"]) + 1
            destination = self.directory(state["id"]) / "dws" / f"t{trial_id}"
            # IDs never collide, even if a previous generated runner failed before a result was saved.
            while destination.exists():
                trial_id += 1
                destination = destination.parent / f"t{trial_id}"
            baseline = None
            adjustments = rules.get("adjustments", [])
            if adjustments:
                scope_key = fingerprint([{k: a.get(k) for k in ("id", "scope", "baseline")} for a in adjustments])
                binding = state.get("adjustment_binding")
                versions = {a.get("baseline") for a in adjustments}
                if len(versions) != 1:
                    raise PipelineError("本轮多条调整请采用同一个判断基准版本")
                explicit = next(iter(versions))
                if binding and binding["dwd_hash"] == stage1["manifest_sha256"] and (explicit == "before_adjustment" or binding.get("baseline_version") == explicit):
                    if sha(binding["path"]) != binding["sha256"]:
                        raise ExecutionFault("固定范围基准快照发生变化，不能继续计算")
                    baseline = json.loads(Path(binding["path"]).read_text(encoding="utf-8"))
                else:
                    reference = next((t for t in state["trials"] if t["trial_id"] == explicit), None) if type(explicit) is int else state.get("accepted_result")
                    if type(explicit) is int and not reference:
                        raise PipelineError("指定的调整前结果版本不存在，请选择已有计算版本")
                    if reference:
                        if reference["dwd_manifest_sha256"] != stage1["manifest_sha256"]:
                            raise PipelineError("调整判断版本对应旧阶段一数据，请重新指定基准")
                        artifact = Path(reference["directory"]) / "output.csv"
                        if sha(artifact) != reference["output_sha256"] or artifact.read_bytes() != csv_bytes(reference["columns"], reference["rows"]):
                            raise ExecutionFault("调整前结果完整性检查失败")
                        baseline = {"rows": reference["rows"], "dwd_manifest_sha256": reference["dwd_manifest_sha256"], "trial_id": reference["trial_id"]}
            result = generate_and_run(manifest, stage1["manifest_sha256"], rules, destination, baseline=baseline)
            if result["status"] == "checkpoint" and "rows" not in result:
                state["rules"] = rules
                self.checkpoint(state, "business_checkpoint", result["issues"])
                return
            result.update(trial_id=trial_id, instruction=instruction or state["task"], dwd_version=stage1["version"])
            result["mode"] = "trial" if trial else "execute"
            if previous and previous["dwd_manifest_sha256"] == result["dwd_manifest_sha256"]:
                result["comparison"] = compare(previous, result)
                dump(destination / "comparison.json", result["comparison"])
            else:
                result["comparison"] = None
            state["rules"] = rules
            state["stage2"] = result
            state["trials"].append(copy.deepcopy(result))
            state["phase"] = "complete" if result["status"] == "complete" else "business_checkpoint"
            state["issues"] = result["failures"]
            if result["status"] == "complete":
                state["last_successful_trial"] = copy.deepcopy(result)
                if not trial:
                    state["accepted_result"] = copy.deepcopy(result)
                if adjustments:
                    scope_path = destination / "adjustment_scope.json"
                    snapshot = json.loads(scope_path.read_text(encoding="utf-8"))
                    binding_path = destination / "frozen_baseline.json"
                    dump(binding_path, {"rows": snapshot["baseline_rows"], "dwd_manifest_sha256": stage1["manifest_sha256"],
                                        "trial_id": baseline.get("trial_id") if baseline else None})
                    state["adjustment_binding"] = {"scope_key": scope_key, "dwd_hash": stage1["manifest_sha256"],
                        "path": str(binding_path), "sha256": sha(binding_path),
                        "baseline_version": baseline.get("trial_id") if baseline else None}
                state.pop("business_draft", None)
                state["decisions"].append({"source": "validated_execution", "instruction": instruction or rules.get("source_instruction") or rules.get("reason") or state["task"],
                    "revision": state["revision"], "rules": copy.deepcopy(rules), "trial_id": trial_id})
            state["events"].append({"action": "dws_python_executed", "revision": state["revision"],
                "trial_id": trial_id, "dwd_manifest_sha256": result["dwd_manifest_sha256"],
                "generated_code_sha256": result["generated_code_sha256"], "summary": result["summary"]})
        except PipelineError as exc:
            self.checkpoint(state, "business_checkpoint", [{"kind": "business_rule", "reason": str(exc)}])
        except Exception as exc:
            self.fault(state, exc, "DWS")

    def create(self, task, uploads, plan=None, rules=None, request_id=None):
        if not isinstance(task, str) or not 0 < len(task.strip()) <= 10000 or not 2 <= len(uploads) <= 6:
            raise PipelineError("需要任务描述和2至6份CSV（可包含主花名册）")
        with self.lock:
            from .semantic import fingerprint
            import hashlib
            request_hash = fingerprint({'task':task,'uploads':[(n,hashlib.sha256(c).hexdigest()) for n,c in uploads],'plan':plan,'rules':rules})
            if request_id is not None:
                if not isinstance(request_id,str) or not 0<len(request_id)<=128:raise InputProblem('请求标识无效')
                for path in self.root.glob('*/workflow.json'):
                    try:existing=json.loads(path.read_text(encoding='utf-8'))
                    except (OSError,ValueError):continue
                    if not isinstance(existing,dict):continue
                    prior=existing.get('initial_request',{})
                    if prior.get('id')==request_id:
                        if prior['hash']!=request_hash:raise InputProblem('同一请求标识不能提交不同内容')
                        return self.load(existing['id'])
            run_id = uuid.uuid4().hex
            directory = self.directory(run_id)
            (directory / "input").mkdir(parents=True)
            files = []
            for index, (filename, content) in enumerate(uploads, 1):
                file_id = f"F{index}"
                (directory / "input" / (file_id + ".original")).write_bytes(content)
                files.append(parse_file(file_id, filename, content))
            state = {"id": run_id, "task": task, "revision": 1, "files": files,
                "initial_request": {"id":request_id,"hash":request_hash},
                "plan": plan or suggest_plan(files, task), "rules": rules, "stage1": None, "stage2": None,
                "trials": [], "last_successful_trial": None, "events": [], "issues": [], "phase": "reading", "suggestion": None,
                "instructions": [{"id": "initial", "text": task, "mode": "execute"}], "decisions": [],
                "accepted_result": None, "requests": {},
                "calculation_requested": rules is not None or calculation_requested(task)}
            self.remember_business_facts(state, task)
            if model_status(self.model_config)["enabled"] and plan is None and rules is None:
                if not self.understand(state, task):
                    self.save(state)
                    return state
                rules = state.get("rules")
            if rules is None and wants_calculation(state):
                try:
                    metrics = list({m["target"] for p in state["plan"]["platforms"] for m in p["metrics"]})
                    state["rules"] = infer_rules(task, metrics)
                except PipelineError as exc:
                    state["rule_parse_question"] = str(exc)
            try:
                if self.stage_one(state):
                    self.stage_two(state, rules or state.get("rules"), trial=bool(re.search(r"试试看|看看变化|先试算|试算一下", task)))
            except PipelineError as exc:
                self.checkpoint(state, "data_checkpoint", [getattr(exc, "problem", {"kind": "plan", "reason": str(exc)})])
            except Exception as exc:
                self.fault(state, exc, "DWD")
            self.save(state)
            return state

    def understand(self, state, instruction):
        """Only the real configured transport may produce semantic proposals."""
        from .advisor import suggest
        from .ingest import layout
        reading_issues = [{"kind": "reading", "file_id": file["id"], "file_name": file["name"], "reason": reason}
                          for file in state["files"] for reason in layout(file).get("errors", [])]
        if reading_issues:
            self.checkpoint(state, "data_checkpoint", reading_issues)
            return False
        response = suggest(self.model_config, state, instruction)
        state["data_profiles"] = profiles(state["files"], state["plan"])
        state["suggestion"] = response
        if response["status"] != "ok":
            self.checkpoint(state, "understanding_error", [{"kind": "model", "reason": response["message"],
                "actions": ["重试理解", "展开修改理解，使用手动表单"], "impact": "本次指令尚未执行"}])
            return False
        proposal = response["proposal"]
        if not proposal["questions"]:
            required = set()
            if proposal["stage1_plan"] != state["plan"]:
                required.add("data")
            proposed_rules = proposal.get("stage2_rules")
            if proposed_rules is not None:
                old = copy.deepcopy(state.get("rules") or {})
                new = copy.deepcopy(proposed_rules)
                if old.pop("adjustments", []) != new.pop("adjustments", []):
                    required.add("adjustments")
                if old != new:
                    required.add("business")
            current_instruction = state["instructions"][-1]["id"]
            supplied = {e["section"] for e in proposal.get("evidence", []) if e["instruction_id"] == current_instruction}
            if required - supplied:
                self.checkpoint(state, "understanding_error", [{"kind": "model", "reason": "模型返回的方案缺少可追溯的指令引用，尚未执行。可重试理解或用表单修正，已有明确指令无需重新确认。",
                    "required_input": "无需重复提供相同规则", "actions": ["重试理解", "修改理解"], "missing_sections": sorted(required - supplied)}])
                return False
        state["plan"] = proposal["stage1_plan"]
        if proposal.get("stage2_rules") is not None:
            state["rules"] = proposal["stage2_rules"]
            state.pop("business_draft", None)
        if proposal.get("stage2_draft") is not None:
            state["business_draft"] = proposal["stage2_draft"]
        state["decisions"].append({"source": state["instructions"][-1]["id"], "instruction": instruction,
            "rationale": proposal["rationale"], "evidence": proposal.get("evidence", []), "rules": copy.deepcopy(state.get("rules")), "revision": state["revision"]})
        if proposal["questions"]:
            from .clarifications import checkpoint_questions
            pending = checkpoint_questions(proposal['questions'],state)
            if any(q.get('clarification') for q in pending):state['pending_clarifications']=pending
            self.checkpoint(state, "understanding_checkpoint", pending)
            return False
        state.pop('pending_clarifications',None)
        return True

    def answer_question(self, run_id, revision, answer):
        from .clarifications import apply_choice, checkpoint_questions, normalize, question_input
        if not isinstance(answer,dict) or set(answer)-{'issue_id','choice_id','text','parameters','reason'}:
            raise InputProblem('问题答案结构无效','semantic')
        with self.lock:
            state=self.load(run_id);self.check(state,revision)
            issue=next((q for q in state['issues'] if q.get('id')==answer.get('issue_id') and q.get('kind')=='semantic'),None)
            if issue is None: raise InputProblem('此问题已变化，请刷新后重新选择','semantic')
            choice=answer.get('choice_id');text=answer.get('text','');followups=[];before=copy.deepcopy(state['plan'])
            selected=None
            if issue.get('clarification',{}).get('source')=='model' and choice not in ('custom','pending'):
                fresh=normalize(question_input(issue['clarification']),state)
                if fresh['binding']!=issue['clarification']['binding']:raise InputProblem('选项所依据的数据方案已变化，请重新生成选项','semantic')
                selected=next((c for c in fresh['choices'] if c['id']==choice),None)
                accepts_max=selected and selected['decision']=={'kind':'scale','mode':'points'}
                if selected is None or answer.get('parameters') and not accepts_max:raise InputProblem('请选择此问题的有效选项，不接受额外操作参数','semantic')
            needs_understanding=choice=='custom' or bool(selected and selected['decision']['kind']=='interpret')
            last=(state.get('clarification_answers') or [{}])[-1]
            if choice=='pending' and last.get('issue_id')==issue['id'] and last.get('choice_id')=='pending':
                return {**state,'feedback':{'message':'此问题仍待确认，记录已保留，没有重复保存。'}}

            remaining=[q for q in state['issues'] if q is not issue]
            if needs_understanding:
                if selected:text=selected['label']
                if not isinstance(text,str) or not text.strip() or len(text)>10000: raise InputProblem('请填写自定义处理方式','semantic')
                instruction='针对问题“'+issue['reason']+'”：'+text.strip()
            elif choice=='pending':
                instruction='继续待确认：'+issue['reason']
                remaining=state['issues']
            else:
                if 'clarification' not in issue: raise InputProblem('此旧问题暂不提供预设选项，请自定义补充','semantic')
                plan,followups=apply_choice(state,issue,choice,answer.get('parameters',{}))
                state['plan']=plan
                label=next(c['label'] for c in issue['clarification']['choices'] if c['id']==choice)
                instruction=issue['reason']+'；教师选择：'+label
                if answer.get('parameters'): instruction+='；原始满分：'+str(answer['parameters'].get('max_score',''))
            state['revision']+=1;state['stage2']=None
            state['instructions'].append({'id':uuid.uuid4().hex,'text':instruction,'mode':'execute'})
            state.setdefault('clarification_answers',[]).append({'issue_id':issue['id'],'question':issue['reason'],'choice_id':choice,'text':text,'selected_option':copy.deepcopy(selected),'parameters':answer.get('parameters',{}),'revision':state['revision'],'status':'pending' if choice=='pending' else 'submitted'})
            state['events'].append({'action':'clarification_answer','instruction':instruction,'revision':state['revision']})
            error_phase=None
            if needs_understanding:
                if model_status(self.model_config)['enabled']:
                    understood=self.understand(state,instruction)
                    if not understood:
                        remaining+=state['issues']
                        if state['phase'] in ('understanding_error','system_error'):error_phase=state['phase']
                else:
                    remaining.append(issue)
                    state['feedback']={'message':'自定义内容已保存；语义模型未启用，请使用中文整理或规则表单补充。'}
            remaining+=checkpoint_questions([normalize(q,state) for q in followups],state)
            unique={q.get('id') or q['reason']:q for q in remaining}
            # Rebind unanswered typed cards to current real fields after a related choice.
            for q in unique.values():
                if q.get('clarification'):
                    old=q['clarification']
                    if old.get('source')=='model':
                        try:
                            fresh=normalize(question_input(old),state)
                            if fresh.get('binding')!=old.get('binding'):q['requires_regeneration']=True
                            else:q['clarification']=fresh
                        except PipelineError:q['requires_regeneration']=True
                    else:q['clarification']=normalize(question_input(old),state)
            if before!=state['plan']:
                state.update(stage1=None,accepted_result=None,last_successful_trial=None)
                state.pop('adjustment_binding',None)
            state['pending_clarifications']=[q for q in unique.values() if q.get('kind')=='semantic']
            if error_phase:
                self.checkpoint(state,error_phase,list(unique.values()))
            elif state['pending_clarifications']:
                self.checkpoint(state,'understanding_checkpoint',list(unique.values()))
            else:
                try:
                    if (state.get('stage1') and state['stage1']['status']=='ready') or self.stage_one(state):
                        self.stage_two(state,state.get('rules'))
                except PipelineError as exc:
                    self.checkpoint(state,'data_checkpoint',[getattr(exc,'problem',{'kind':'plan','reason':str(exc)})])
                except Exception as exc: self.fault(state,exc,'DWD')
            if not needs_understanding: state.pop('feedback',None)
            self.save(state)
            return state

    def question_options(self,run_id,revision,issue_id):
        from .advisor import suggest_question_options
        with self.lock:
            state=self.load(run_id);self.check(state,revision)
            issue=next((q for q in state['issues'] if q.get('id')==issue_id and q.get('kind')=='semantic'),None)
            if issue is None:raise InputProblem('此问题已变化，请刷新后重试','semantic')
            response=suggest_question_options(self.model_config,state,issue)
            if response['status']!='ok':raise InputProblem(response['message'],'model',actions=['重试生成选项','保留自定义回答'])
            q=response['question'];issue['clarification']=q
            issue.pop('requires_regeneration',None)
            # Keep the original problem visible and addressable; only its candidate answers change.
            issue['options_origin_question']=q['question']
            state['pending_clarifications']=[p for p in state['issues'] if p.get('kind')=='semantic']
            state['revision']+=1
            state['events'].append({'action':'question_options_generated','issue_id':issue_id,'source':'real_model','revision':state['revision']})
            state['feedback']={'message':'选项已生成，尚未选择或执行。'}
            self.save(state)
            return state

    def instruct(self, run_id, revision, instruction, mode="execute", request_id=None):
        if not isinstance(instruction, str) or not 0 < len(instruction.strip()) <= 10000 or mode not in ("execute", "trial"):
            raise InputProblem("请填写本次指令并选择执行或试算", "semantic")
        instruction = instruction.strip()
        if re.search(r"试试看|看看变化|先试算|试算一下", instruction):
            mode = "trial"
        request_id = request_id or fingerprint({"revision": revision, "instruction": instruction, "mode": mode})
        if not isinstance(request_id, str) or len(request_id) > 128:
            raise InputProblem("请求标识无效")
        request_hash = fingerprint({"instruction": instruction, "mode": mode})
        with self.lock:
            state = self.load(run_id)
            prior = state["requests"].get(request_id)
            if prior:
                if prior["hash"] != request_hash:
                    raise InputProblem("同一请求标识不能提交不同指令")
                if state["phase"] != "understanding_error" or state["revision"] != prior["revision"]:
                    return {**state, "feedback": {"code": "duplicate_request", "message": "本次请求已保存，未重复计算或累计调整。"}}
            self.check(state, revision)
            context_hash = fingerprint({"plan": state["plan"], "rules": state.get("rules"), "dwd": (state.get("stage1") or {}).get("manifest_sha256")})
            if state.get("last_instruction_hash") == request_hash and state.get("last_instruction_context") == context_hash and state["phase"] == "complete":
                return {**state, "feedback": {"code": "instruction_unchanged", "message": "指令未变化，沿用已有执行结果，没有重复调整。"}}
            before_plan = copy.deepcopy(state["plan"])
            was_calculation = wants_calculation(state)
            state["calculation_requested"] = calculation_requested(instruction, wants_calculation(state))
            self.remember_business_facts(state, instruction)
            state["revision"] += 1
            state["instructions"].append({"id": uuid.uuid4().hex, "text": instruction, "mode": mode})
            state["stage2"] = None
            state["requests"][request_id] = {"hash": request_hash, "revision": state["revision"]}
            if model_status(self.model_config)["enabled"]:
                understood = self.understand(state, instruction)
            else:
                try:
                    metrics = {m["target"] for p in state["plan"]["platforms"] for m in p["metrics"]}
                    if wants_calculation(state):
                        base = state.get("rules") or state.get("known_business_facts")
                        try:
                            proposed = infer_rules(instruction, metrics, base)
                        except PipelineError:
                            if not was_calculation and base and base.get("weights") and not re.search(r"提高|降低|增加|减少|改", instruction):
                                proposed = copy.deepcopy(base)
                            else:
                                raise
                        compile_rules(proposed, metrics)
                        state["rules"] = proposed
                    elif not re.search(r"不.*计算|不.*计分|无需.*计算|只.*(?:清洗|整理)|继续(?:清洗|整理)", instruction):
                        raise PipelineError("本次数据整理补充超出手动模式的有限语法；可使用整理表单或启用真实模型")
                    understood = True
                except PipelineError as exc:
                    self.checkpoint(state, "understanding_checkpoint", [{"id": "S1", "kind": "semantic",
                        "reason": "语义分析未启用；" + str(exc), "required_input": "展开修改理解补充项目/规则，或配置真实模型"}])
                    understood = False
            if state["plan"] != before_plan and not understood:
                state["stage1"] = None
                state["accepted_result"] = None
                state.pop("adjustment_binding", None)
            if understood:
                plan_changed = state["plan"] != before_plan
                if mode == "trial" and plan_changed:
                    state["plan"] = before_plan
                    self.checkpoint(state, "understanding_checkpoint", [{"kind": "semantic", "reason": "试算仅改变阶段二规则；本次涉及数据整理，请明确以执行模式处理"}])
                else:
                    try:
                        if plan_changed or not state.get("stage1"):
                            state["stage1"] = None
                            state["accepted_result"] = None
                            if not self.stage_one(state):
                                self.save(state)
                                return state
                        if state["stage1"]["status"] == "ready":
                            self.stage_two(state, state.get("rules"), instruction, trial=mode == "trial")
                        else:
                            self.checkpoint(state, "data_checkpoint", state["stage1"]["issues"])
                    except PipelineError as exc:
                        self.checkpoint(state, "data_checkpoint", [getattr(exc, "problem", {"kind": "plan", "reason": str(exc)})])
                    except Exception as exc:
                        self.fault(state, exc, "DWD")
            if state["phase"] in ("complete", "organized"):
                state["last_instruction_hash"] = request_hash
                state["last_instruction_context"] = fingerprint({"plan": state["plan"], "rules": state.get("rules"), "dwd": (state.get("stage1") or {}).get("manifest_sha256")})
            self.save(state)
            return state

    def adopt_trial(self, run_id, revision):
        with self.lock:
            state = self.load(run_id)
            self.check(state, revision)
            result = state.get("stage2")
            if not result or result.get("mode") != "trial" or result["status"] != "complete":
                raise InputProblem("当前没有可采用的完整试算结果")
            self.download(run_id, "dws")
            state["accepted_result"] = copy.deepcopy(result)
            state["revision"] += 1
            state["events"].append({"action": "adopt_trial", "trial_id": result["trial_id"], "revision": state["revision"]})
            state["feedback"] = {"message": "已采用本次试算，原版本仍保留。"}
            self.save(state)
            return state

    def resume(self, run_id, revision, plan=None, rules=None, instruction=None, trial=False, reason=None, answered_issue_id=None):
        with self.lock:
            state = self.load(run_id)
            self.check(state, revision)
            answered = next((q for q in state['issues'] if q.get('id')==answered_issue_id and q.get('kind')=='semantic'),None) if answered_issue_id else None
            if answered_issue_id and (answered is None or (plan is None and rules is None)):
                raise InputProblem('请选择当前待补充问题，并提交经过检查的整理或计算表单','semantic')
            if answered:
                qtype=answered.get('clarification',{}).get('type')
                if qtype in ('identity_policy','strategy','scale','record_grain') and plan is None:
                    raise InputProblem('此问题需要补充数据整理方式，计算规则不能替代数据裁决','semantic')
            if rules is not None:
                state["calculation_requested"] = True
            elif instruction:
                state["calculation_requested"] = calculation_requested(instruction, wants_calculation(state))
            if plan is not None:
                validate_plan(state, plan)
                changes = plan_diff(state["plan"], plan)
                strategy_changes = [c for c in changes if c["path"].endswith(".strategy")]
                if strategy_changes and (not isinstance(reason, str) or not reason.strip()):
                    raise InputProblem("请填写调整归并方式的依据", "plan", field="reason", changes=strategy_changes)
                if strategy_changes:
                    state["events"].append({"action": "teacher_strategy_change", "revision": revision + 1, "reason": reason, "changes": strategy_changes})
            if trial and rules is None and (not isinstance(instruction, str) or not instruction.strip()):
                raise PipelineError("试算需要明确追加指令或新业务规则")
            plan_changed = plan is not None and plan != state["plan"]
            data_pending = not state.get("stage1") or state["stage1"]["status"] != "ready"
            submitted_rules = rules is not None or bool(instruction)
            # Save validated business intent before an unresolved DWD boundary can return.
            # It must also survive a newly changed governance plan producing more questions.
            if submitted_rules and (data_pending or plan_changed):
                metrics = list({m["target"] for p in (plan or state["plan"])["platforms"] for m in p["metrics"]})
                try:
                    if rules is None:
                        rules = infer_rules(instruction, metrics, state.get("rules"))
                    compile_rules(rules, metrics)
                except PipelineError as exc:
                    raise InputProblem(str(exc), "business_rule", field="rules", actions=["修正计算规则后重新保存"])
                if data_pending and not plan_changed and state["phase"] == "data_checkpoint" and rules == state.get("rules"):
                    return {**state, "feedback": {"code": "rules_unchanged_pending", "message": "计算规则已保存且没有变化。请先处理数据问题，当前不会开始计算。"}}
                state["rules"] = copy.deepcopy(rules)
                state["events"].append({"action": "business_rules_saved", "revision": revision + 1, "rules": copy.deepcopy(rules), "instruction": instruction})
                state["feedback"] = {"code": "rules_saved_pending", "message": "本次计算规则已保存，尚未计算。数据核对完成后将自动采用这套规则。"}
            elif data_pending and not plan_changed and state["phase"] == "data_checkpoint":
                return {**state, "feedback": {"code": "data_still_pending", "message": "数据问题尚未解决，请在问题卡片中补充时间、明确裁决或保持待确认；重复执行不会解决该问题。"}}
            else:
                state.pop("feedback", None)
            if answered:
                state['pending_clarifications']=[q for q in state.get('pending_clarifications',[]) if q.get('id')!=answered_issue_id]
                state.setdefault('clarification_answers',[]).append({'issue_id':answered_issue_id,'question':answered['reason'],'choice_id':'teacher_form','revision':revision+1,'status':'submitted','text':reason or '教师明确提交当前计算表单'})
            state["revision"] += 1
            state["stage2"] = None
            if (plan is not None and plan != state["plan"]) or (not trial and state["stage1"] is None):
                if trial:
                    raise PipelineError("试算只能更改阶段二规则；阶段一修订请使用确认继续")
                if plan is not None:
                    state["plan"] = plan
                try:
                    if not self.stage_one(state):
                        self.save(state)
                        return state
                except PipelineError as exc:
                    # Never leave an older ready manifest active under a changed/invalid governance plan.
                    state["stage1"] = None
                    state["last_successful_trial"] = None
                    self.checkpoint(state, "data_checkpoint", [getattr(exc, "problem", {"kind": "plan", "reason": str(exc)})])
                    self.save(state)
                    return state
                except Exception as exc:
                    self.fault(state, exc, "DWD")
                    self.save(state)
                    return state
            if rules is None and not trial and not instruction:
                rules = state.get("rules")
            self.stage_two(state, rules, instruction=instruction, trial=trial)
            if state["phase"] != "data_checkpoint":
                state.pop("feedback", None)
            self.save(state)
            return state

    def resolve(self, run_id, revision, decision):
        """One card per transaction. Other uploaded records and card drafts are untouched."""
        with self.lock:
            state = self.load(run_id)
            self.check(state, revision)
            if not isinstance(decision, dict):
                raise InputProblem("请提交一条明确的裁决")
            issue = next((q for q in state["issues"] if q.get("id") == decision.get("issue_id")), None)
            if not issue:
                raise InputProblem("此问题已变化，请核对当前待确认记录")
            reason = decision.get("reason", "")
            if decision.get("action") == "pending":
                if not isinstance(reason, str):
                    raise InputProblem("待确认备注须为文字", issue["kind"], field="reason")
                canonical = {"issue_id": issue["id"], "issue_key": issue_key(issue), "action": "pending", "reason": reason.strip()}
                previous = next((e.get("decision", {}) for e in reversed(state["events"])
                                 if e.get("action") == "kept_pending" and issue_key(e.get("issue", {})) == canonical["issue_key"]), None)
                if previous and previous.get("reason", "").strip() == canonical["reason"]:
                    return {**state, "feedback": {"code": "pending_unchanged", "message": "此条已保持待确认，备注没有变化；未重复保存，仍未解决，不会开始计算。"}}
                state["revision"] += 1
                state.setdefault("pending_decisions", {})[issue["id"]] = canonical
                state["events"].append({"action": "kept_pending", "revision": state["revision"], "decision": canonical, "issue": issue})
                state["feedback"] = {"code": "pending_saved", "message": "已保存待确认备注。该记录仍未解决，裁决值不会被采用，当前不会开始计算。"}
                self.save(state)
                return state
            plan = copy.deepcopy(state["plan"])
            if decision.get("action") == "timestamps":
                guidance = issue_guidance(state, issue)
                corrections = decision.get("timestamps")
                if guidance.get("kind") != "submission_time" or not guidance.get("timestamp_field"):
                    raise InputProblem("此问题没有实际配置的提交时间字段，请先调整数据整理方式", "submission_time")
                if not isinstance(reason, str) or not reason.strip():
                    raise InputProblem("请填写补充或修正提交时间的核对依据", "submission_time", field="reason")
                if not isinstance(corrections, list) or not corrections:
                    raise InputProblem("请至少补充或修正一条提交时间", "submission_time", field="timestamps")
                allowed = {r["record_id"] for r in issue["records"]}
                seen = set()
                for correction in corrections:
                    if not isinstance(correction, dict) or correction.get("record_id") not in allowed or correction["record_id"] in seen:
                        raise InputProblem("补充时间须选择此问题中实际展示的原始记录，不能重复提交同一条", "submission_time")
                    if correction.get("field", guidance["timestamp_field"]) != guidance["timestamp_field"]:
                        raise InputProblem("请选择该记录实际配置的提交时间字段", "submission_time", record_id=correction["record_id"])
                    seen.add(correction["record_id"])
                    plan.setdefault("timestamp_resolutions", {}).setdefault(correction["record_id"], {})[guidance["timestamp_field"]] = {
                        "value": correction.get("value"), "reason": reason.strip()}
            elif issue["kind"] == "identity":
                rid = issue["records"][0]["record_id"]
                plan.setdefault("identity_resolutions", {})[rid] = {"student_id": decision.get("student_id"), "reason": reason}
            elif issue["kind"] == "unmatched":
                if not isinstance(reason, str) or not reason.strip():
                    raise InputProblem("请填写主表外记录处理依据", "unmatched", field="reason")
                if decision.get("action") == "append":
                    plan["append_unmatched_ids"] = list(set(plan.get("append_unmatched_ids", []) + [issue["student_id"]]))
                    plan.setdefault("append_reasons", {})[issue["student_id"]] = reason
                elif decision.get("action") == "exclude":
                    for r in issue["records"]:
                        plan.setdefault("excluded_records", {})[r["record_id"]] = reason
                else:
                    raise InputProblem("请选择追加到主表、排除或继续待确认", "unmatched")
            elif issue.get("student_id") and (issue.get("metric") or issue.get("field")):
                if not isinstance(decision.get("value"), str) or not decision["value"].strip():
                    raise InputProblem("请填写明确的裁决值；暂不裁决请选择继续待确认", "cell", field="value", records=issue.get("records", []))
                key = issue["student_id"] + "|" + (issue.get("metric") or issue["field"])
                plan.setdefault("cell_resolutions", {})[key] = {"value": decision.get("value"), "reason": reason}
            else:
                raise InputProblem("请通过调整数据整理方式修正此问题", issue["kind"])
            validate_plan(state, plan)
            # Q1/Q2 can change after another card is resolved; match the source issue.
            for pending_id, pending in list(state.get("pending_decisions", {}).items()):
                pending_key = pending.get("issue_key") or next((issue_key(e["issue"]) for e in reversed(state["events"])
                    if e.get("action") == "kept_pending" and e.get("decision", {}).get("issue_id") == pending_id), None)
                if pending_key == issue_key(issue):
                    state["pending_decisions"].pop(pending_id)
            state["events"].append({"action": "teacher_decision", "revision": revision + 1, "decision": decision, "issue": issue})
            # resume loads from disk, so save the audit event before the atomic locked transition.
            self.save(state)
            result = self.resume(run_id, revision, plan=plan)
            result["feedback"] = {"code": "decision_saved", "message": (
                "核对内容已保存，整理和计算已完成。" if result["phase"] == "complete" else
                f"核对内容已保存，仍有{len(result['issues'])}项需要处理。请继续核对剩余问题。")}
            self.save(result)
            return result

    def inspect_layout(self, run_id, revision, file_id, header_line):
        from .ingest import layout
        state = self.load(run_id)
        self.check(state, revision)
        file = next((f for f in state["files"] if f["id"] == file_id), None)
        if not file or type(header_line) is not int:
            raise InputProblem("请选择实际文件和表头所在行")
        return layout(file, header_line)

    def check_rules(self, run_id, revision, rules):
        state = self.load(run_id)
        self.check(state, revision)
        metrics = list({m["target"] for p in state["plan"]["platforms"] for m in p["metrics"]})
        compile_rules(rules, metrics)
        return {"valid": True, "rules": rules}

    def prepare_rules(self, run_id, revision, rules):
        from decimal import Decimal
        from .common import number
        if not isinstance(rules, dict):
            raise InputProblem("请填写计算规则", "business_rule")
        normalized = copy.deepcopy(rules)
        percentages = normalized.pop("weights_percent", None)
        if percentages is not None:
            if not isinstance(percentages, dict) or not percentages:
                raise InputProblem("请补充各项目权重，合计100%", "business_rule", field="weights")
            normalized["weights"] = {k: str(number(v) / Decimal(100)) for k, v in percentages.items()}
            normalized.pop("formulas", None)
        for item in normalized.get("assessment_items", []):
            if isinstance(item, dict) and "weights_percent" in item:
                values = item.pop("weights_percent")
                if not isinstance(values, list):
                    raise InputProblem("内部权重须按来源顺序填写", "business_rule")
                item["weights"] = [str(number(v) / Decimal(100)) for v in values]
        return self.check_rules(run_id, revision, normalized)

    def preview_plan(self, run_id, revision, plan):
        state = self.load(run_id)
        self.check(state, revision)
        validate_plan(state, plan)
        return {"changes": plan_diff(state["plan"], plan), "token": token(plan, revision),
                "impact": "重新整理所有上传数据；旧计算结果失效，原始文件和历史审计保留", "revision": revision}

    def apply_plan(self, run_id, revision, plan, confirmation, reason, answered_issue_id=None):
        with self.lock:
            preview = self.preview_plan(run_id, revision, plan)
            if confirmation != preview["token"]:
                raise InputProblem("方案发生变化，请重新查看变更摘要并确认")
            if not preview["changes"]:
                return self.load(run_id)
            if not isinstance(reason, str) or not reason.strip():
                raise InputProblem("请填写调整数据整理方式的依据", field="reason")
            state = self.load(run_id)
            state["events"].append({"action": "teacher_plan_change", "revision": revision + 1, "reason": reason, "changes": preview["changes"]})
            self.save(state)
            return self.resume(run_id, revision, plan=plan, reason=reason,answered_issue_id=answered_issue_id)

    def download(self, run_id, kind, revision=None):
        state = self.load(run_id)
        if revision is not None and revision != state['revision']:
            raise RevisionError('任务版本已变化，请重新选择当前处理说明')
        if kind == 'report':
            from .audit_report import render
            return render(self,state)
        if kind == "audit":
            return json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8"), "application/json", "two-stage-audit.json"
        if kind in ("dwd", "dwd_audit", "dwd_code"):
            if not state["stage1"]:
                raise RevisionError("阶段一尚无有效产物")
            directory = Path(state["stage1"]["directory"])
            if sha(directory / "manifest.json") != state["stage1"]["manifest_sha256"]:
                raise RevisionError("阶段一manifest已变化，不能导出失效产物")
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
            if kind == "dwd_code":
                code = directory / "governance.py"
                if sha(code) != state["stage1"].get("generated_code_sha256"):
                    raise RevisionError("阶段一代码发生变化")
                return code.read_bytes(), "text/plain; charset=utf-8", "stage1-governance.py"
            name = "wide.csv" if kind == "dwd" else "audit.json"
            if sha(directory / "manifest.json") != state["stage1"]["manifest_sha256"] or sha(directory / name) != manifest["files"][name]:
                raise RevisionError("阶段一文件已变化，不能导出与预览不一致的产物")
            return (directory / name).read_bytes(), "text/csv; charset=utf-8" if kind == "dwd" else "application/json", "stage1-" + name
        if kind in ("dws", "code", "accepted"):
            if kind == "accepted":
                accepted = state.get("accepted_result")
                if not accepted or not state.get("stage1") or accepted["dwd_manifest_sha256"] != state["stage1"]["manifest_sha256"]:
                    raise RevisionError("没有与当前整理版本一致的已采用结果")
                artifact = Path(accepted["directory"]) / "output.csv"
                if sha(artifact) != accepted["output_sha256"] or artifact.read_bytes() != csv_bytes(accepted["columns"], accepted["rows"]):
                    raise RevisionError("已采用结果完整性检查失败")
                return artifact.read_bytes(), "text/csv; charset=utf-8", "accepted-result.csv"
            if not state["stage2"]:
                raise RevisionError("当前业务规则尚未完成执行；旧试算不能冒充本次结果")
            name = "output.csv" if kind == "dws" else "calculation.py"
            artifact = Path(state["stage2"]["directory"]) / name
            expected = state["stage2"].get("output_sha256" if kind == "dws" else "generated_code_sha256")
            if expected is not None and sha(artifact) != expected:
                raise RevisionError("阶段二产物已变化，不能导出与已执行预览不一致的结果")
            if kind == "dws" and artifact.read_bytes() != csv_bytes(state["stage2"]["columns"], state["stage2"]["rows"]):
                raise RevisionError("阶段二CSV与已执行预览不一致")
            return artifact.read_bytes(), "text/csv; charset=utf-8" if kind == "dws" else "text/plain; charset=utf-8", "stage2-" + name
        raise PipelineError("未知双阶段产物类型")

    def suggest(self, run_id, revision):
        from .advisor import suggest
        state = self.load(run_id)
        self.check(state, revision)
        response = suggest(self.model_config, state)
        with self.lock:
            state = self.load(run_id)
            self.check(state, revision)
            state["suggestion"] = response
            self.save(state)
            return state
