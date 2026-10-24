"""A bounded formula language: models propose expressions; Python validates and interprets them."""
import ast
import copy
import re

from .common import PipelineError, number

FUNCTIONS = {"field", "state", "min", "max", "sum", "mean", "best", "ifelse", "abs"}
NODES = (ast.Expression, ast.Constant, ast.Call, ast.Name, ast.Load, ast.BinOp, ast.UnaryOp,
         ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd, ast.Compare, ast.Gt, ast.GtE,
         ast.Lt, ast.LtE, ast.Eq, ast.NotEq, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not)


def inspect_expression(expression, available):
    if not isinstance(expression, str) or not 0 < len(expression) <= 2000:
        raise PipelineError("表达式为空或超过2000字符")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        raise PipelineError("表达式语法错误")
    nodes = list(ast.walk(tree))
    if len(nodes) > 256 or any(not isinstance(node, NODES) for node in nodes):
        raise PipelineError("表达式超出受限语法；禁止属性、下标、导入、循环及任意Python代码")
    references = set()
    for node in nodes:
        if isinstance(node, ast.Name) and node.id not in FUNCTIONS:
            raise PipelineError("未知函数/变量：" + node.id)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS or node.keywords:
                raise PipelineError("函数调用不在白名单中")
            if node.func.id in ("field", "state"):
                if len(node.args) != 1 or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str) or node.args[0].value not in available:
                    raise PipelineError("field/state须引用已持久化项目或前序输出的真实字段")
                references.add(node.args[0].value)
            elif node.func.id == "ifelse" and len(node.args) != 3:
                raise PipelineError("ifelse需要条件、成立值和不成立值")
            elif node.func.id == "best":
                if (len(node.args) < 2 or not isinstance(node.args[0], ast.Constant)
                        or type(node.args[0].value) is not int or not 1 <= node.args[0].value <= len(node.args) - 1):
                    raise PipelineError("best的M须为正整数且不超过实际提供的N个项目")
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            if abs(number(ast.get_source_segment(expression, node))) > 1_000_000_000:
                raise PipelineError("表达式数值常量过大")
    return tree, references


def compile_rules(rules, metrics):
    if not isinstance(rules, dict):
        raise PipelineError("阶段二需要明确的业务规则对象")
    from .semantic import expand_projects, validate_adjustments
    original = copy.deepcopy(rules)
    rules = expand_projects(rules, metrics)
    allowed = {"weights", "output", "formulas", "missing", "missing_by_field", "precision", "cap", "summary_field",
               "excellent_threshold", "excellent_denominator", "source_instruction", "reason", "adjustments", "pass_threshold"}
    if set(rules) - allowed:
        raise PipelineError("未知业务规则项：" + ", ".join(sorted(set(rules) - allowed)))
    rule = copy.deepcopy(rules)
    if "weights" in rule:
        weights = rule["weights"]
        if not isinstance(weights, dict) or not weights or not set(weights) <= set(metrics):
            actual=','.join(weights) if isinstance(weights,dict) else type(weights).__name__
            raise PipelineError("权重必须引用阶段一真实项目；weights须为名称到小数权重的对象。允许名称："+','.join(sorted(metrics))+"；收到："+actual)
        values = {key: number(value) for key, value in weights.items()}
        if any(value < 0 for value in values.values()) or sum(values.values()) != 1:
            raise PipelineError("权重须非负且合计100%；不能自行归一或推断其他项目如何调整")
        if "formulas" in rule:
            raise PipelineError("weights与formulas不能同时给出，避免选取冲突计算规则")
        expression = "+".join(f"field({key!r})*{value}" for key, value in values.items())
        if "cap" in rule:
            expression = f"min({number(rule['cap'])},{expression})"
        rule["formulas"] = [{"name": rule.get("output", "总评"), "expr": expression}]
        rule.setdefault("summary_field", rule["formulas"][0]["name"])
    formulas = rule.get("formulas")
    if not isinstance(formulas, list) or not 1 <= len(formulas) <= 20:
        raise PipelineError("缺少明确公式；请给完整权重，或1至20条受限表达式")
    names, references = set(metrics), set()
    for item in formulas:
        if (not isinstance(item, dict) or set(item) != {"name", "expr"} or not isinstance(item["name"], str)
                or not item["name"].strip() or len(item["name"]) > 80 or item["name"] in names
                or item["name"] in ("student_id", "name", "class", "phone") or item["name"].startswith("__")):
            raise PipelineError("公式输出名无效、重复或覆盖原始项目")
        _, refs = inspect_expression(item["expr"], names)
        references.update(refs & set(metrics))
        names.add(item["name"])
    if rule.get('summary_field') is not None and rule['summary_field'] not in names:
        raise PipelineError('summary_field引用不存在的结果列：'+str(rule['summary_field'])+'；实际可用列：'+','.join(sorted(names))+'。output是列名，不是百分制格式；总评输出请使用output=总评')
    if "precision" in rule and (type(rule["precision"]) is not int or not 0 <= rule["precision"] <= 10):
        raise PipelineError("precision须为0至10；不指定时保留确定性十进制结果，不擅自舍入")
    if "excellent_threshold" in rule:
        number(rule["excellent_threshold"])
        if rule.get("summary_field") not in {item["name"] for item in formulas} or rule.get("excellent_denominator") not in ("numeric", "all"):
            raise PipelineError("优秀率需要明确summary_field与分母excellent_denominator（numeric或all）")
    for policies in [rule.get("missing", {})] + list(rule.get("missing_by_field", {}).values() if isinstance(rule.get("missing_by_field", {}), dict) else []):
        if not isinstance(policies, dict):
            raise PipelineError("特殊状态处理必须为对象")
        for status, policy in policies.items():
            if not isinstance(status, str) or not isinstance(policy, dict) or policy.get("action") not in ("zero", "preserve", "value", "hold"):
                raise PipelineError("特殊状态动作须为zero/preserve/value/hold")
            if policy["action"] == "value":
                number(policy.get("value"))
    by_field = rule.get("missing_by_field", {})
    if not isinstance(by_field, dict) or not set(by_field) <= set(metrics):
        raise PipelineError("missing_by_field须引用阶段一真实项目")
    rule["referenced_metrics"] = sorted(references)
    if "pass_threshold" in rule:
        number(rule["pass_threshold"])
    project_names = {i["name"] for i in original.get("assessment_items", [])}
    validate_adjustments(rule.get("adjustments", []), set(metrics) | project_names,
                         {f["name"] for f in formulas} - project_names)
    for adjustment in rule.get("adjustments", []):
        if adjustment["target"] in metrics:
            references.add(adjustment["target"])
    rule["referenced_metrics"] = sorted(references)
    rule["assessment_items"] = original.get("assessment_items", [])
    return rule


def infer_rules(task, metrics, previous=None):
    """Narrow, disclosed grammar, not a simulated LLM. Unrecognized math remains a question."""
    if re.search(r"(?:提高|增加|降低|减少)\s*\d+(?:\.\d+)?\s*(?:%|％|个百分点)", task):
        raise PipelineError("权重调整有歧义：增加百分点还是相对比例？其他项目如何调整？请提供调整后的完整权重")
    result = copy.deepcopy(previous or {})
    result.pop("referenced_metrics", None)
    weights = {}
    recognized = False
    for metric in list(metrics) + [item["name"] for item in result.get("assessment_items", [])]:
        matches = re.findall(re.escape(metric) + r"\s*(?:权重|占比)?\s*(?:占|为|是|[:：=])?\s*(\d+(?:\.\d+)?)\s*[%％]", task)
        if len({number(value) for value in matches}) > 1:
            raise PipelineError(f"{metric}权重出现冲突（{'、'.join(matches)}%），请确认本次唯一适用的权重")
        if matches:
            weights[metric] = str(number(matches[0]) / 100)
    if weights:
        recognized = True
        result.pop("formulas", None)
        result["weights"] = weights
    policies = result.setdefault("missing", {})
    for state in ("未交", "缺考", "缓考", "免修", "缺勤"):
        zero = re.search(re.escape(state) + r"\s*(?:按|记|计|为)?\s*0\s*分?", task)
        preserve = re.search(re.escape(state) + r"\s*(?:保持|保留)", task)
        if zero and preserve:
            raise PipelineError(f"{state}同时要求记0和保留，规则冲突；请确认本次适用的处理方式")
        if zero:
            policies[state] = {"action": "zero"}
            recognized = True
        if preserve:
            policies[state] = {"action": "preserve"}
            recognized = True
    if re.search(r"(?:空值|空缺|缺失值)\s*(?:按|记|计|为)?\s*0", task):
        policies[""] = {"action": "zero"}
        recognized = True
    cap = re.search(r"总评(?:分)?\s*封顶\s*(\d+(?:\.\d+)?)", task)
    if cap:
        result["cap"] = cap[1]
        recognized = True
    precision = re.search(r"保留\s*([0-9一二三四五六])\s*位小数", task)
    if precision:
        result["precision"] = int(precision[1]) if precision[1].isdigit() else "一二三四五六".index(precision[1]) + 1
        recognized = True
    if previous is not None and not recognized:
        raise PipelineError("追加指令尚未转成确定规则；请提供完整权重或受限公式，不能用旧规则冒充本次变更已执行")
    result["source_instruction"] = task
    if "优秀率" in task and ("excellent_threshold" not in result or "excellent_denominator" not in result):
        raise PipelineError("优秀率尚缺阈值和统计分母；请指定excellent_threshold及excellent_denominator")
    return result
