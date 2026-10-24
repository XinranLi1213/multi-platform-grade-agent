"""Conservative execution scope; a data request does not authorize business scoring."""
import re


def calculation_requested(text, previous=False):
    text = str(text or "")
    # Inspect the new instruction, rather than letting an earlier scoring request
    # override an explicit request to stop calculating.
    if re.search(r"(?:暂时?|先|本次)?(?:不|无需|不用|不要)(?:需要|进行|再|做|计算)?\s*(?:计算|计分|评分|核算|加权|算总分|算总评)|只(?:做|进行)?(?:数据)?(?:清洗|整理|去重)(?:[，。；,;]|$)", text):
        return False
    if re.search(r"计算|计分|评分|核算|平均分|总评|总分|加权|权重|绩点|优秀率|及格率|合成(?:成绩|分数)|最终(?:成绩|分数)|打分|\d\s*[%％]|compute|weighted|calculate", text, re.I):
        return True
    return bool(previous)


def wants_calculation(state):
    value = state.get("calculation_requested")
    if type(value) is bool:
        return value
    # Old workflows remain readable; explicit saved rules are existing intent.
    return bool(state.get("rules") or state.get("stage2") or calculation_requested(state.get("task"))
                or any(calculation_requested(i.get("text")) for i in state.get("instructions", [])))
