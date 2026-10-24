"""Typed questions and bounded teacher choices. No model-supplied executable patches."""
import copy
import re
from .common import InputProblem, PipelineError, clean
from .ingest import layout
from .semantic import fingerprint

TITLES = {'identity_policy': '缺编号关联', 'strategy': '重复提交归并', 'record_grain': '区分业务活动', 'scale': '原始分制'}
STRATEGIES = {'max': '取最高分', 'latest': '按提交时间取最后一次', 'sum': '累加', 'mean': '平均', 'unique': '仅接受一致值', 'confirm': '暂不归并，保留待确认'}


def stated_maxima(text):
    """Business statements only; an observed maximum is never a scale."""
    return set(re.findall(r'(?:满分\s*(?:为|是|按|[:：=])?\s*)(\d+(?:\.\d+)?)|(?:(\d+(?:\.\d+)?)\s*分制)', text))


def scale_values(text):
    from .common import number
    return {number(a or b) for a,b in stated_maxima(text)}


def known_maxima(state, file_id, column):
    texts=[column]+[i['text'] for i in state.get('instructions',[]) if i.get('mode')!='trial']
    return scale_values('\n'.join(texts))


def normalize(question, state):
    if isinstance(question, str):
        if not question.strip(): raise PipelineError('问题文字不能为空')
        return question.strip()
    if not isinstance(question, dict): raise PipelineError('问题须为文字或结构化问题')
    if question.get('type') == 'choice' or 'choices' in question:
        return normalize_model_question(question,state)
    allowed = {'type', 'title', 'question', 'text', 'message', 'file_id', 'target'}
    if set(question) - allowed: raise PipelineError('结构化问题包含未知字段；选项由程序按合法动作构造')
    text = question.get('question', question.get('text', question.get('message')))
    if not isinstance(text, str) or not text.strip() or len(text) > 3000: raise PipelineError('请提供具体问题文字')
    if 'type' not in question:
        return text.strip()  # Preserve legacy textual envelopes without inventing a choice contract.
    kind = question.get('type', 'custom')
    if kind not in set(TITLES) | {'custom'}: raise PipelineError('不支持的问题类型，请用custom保留文字问题')
    descriptor = {k: question.get(k) for k in ('type', 'file_id', 'target')}
    descriptor['type'] = kind
    if kind == 'custom': descriptor['question'] = text
    result = {**descriptor, 'id': 'C'+fingerprint(descriptor)[:20], 'type': kind,
              'title': TITLES.get(kind, str(question.get('title') or '补充处理方式')[:40]),
              'question': text.strip(), 'choices': [], 'allow_custom': True}
    if kind == 'custom': return result
    file = next((f for f in state['files'] if f['id'] == question.get('file_id')), None)
    spec = next((p for p in state['plan']['platforms'] if p['file_id'] == question.get('file_id')), None)
    if file is None or spec is None: raise PipelineError('问题引用了不存在的文件')
    table = layout(file, spec.get('header_line'))
    metric = next((m for m in spec['metrics'] if m['target'] == question.get('target')), None)
    if kind in ('strategy', 'scale') and metric is None: raise PipelineError('问题引用了不存在的整理项目')
    choice = lambda id, label: {'id': id, 'label': label}
    if kind == 'identity_policy':
        if not spec.get('name_column') or not spec.get('class_column'): raise PipelineError('姓名与班级关联选项需要实际存在的姓名和班级字段')
        result['choices'] = [choice('unique_name_class', '仅姓名＋班级完全一致且候选唯一时关联；其余保留待确认'), choice('hold', '暂不关联缺编号记录，保留待确认')]
    elif kind == 'strategy':
        result['choices'] = [choice(k, v) for k, v in STRATEGIES.items()]
    elif kind == 'scale':
        result['choices'] = [choice('percent', '原始数据已是百分制'), choice('points', '按原始满分折算为百分制')]
    elif kind == 'record_grain':
        result['choices'] = [choice('same_submission', '同一项活动的重复提交；下一步选择归并方式')]
        result['choices'] += [choice('field:'+h, '不同业务活动，按“'+h+'”分别保留') for h in table['headers'] if h not in [spec.get(k) for k in ('id_column','name_column','class_column','phone_column')]]
    dependency = {'file': file['sha256'], 'header': spec.get('header_line'), 'identity': {k:spec.get(k) for k in ('id_column','name_column','class_column','phone_column')}}
    if kind == 'identity_policy': dependency['roster'] = state['plan'].get('roster_file')
    else: dependency.update(metric=metric, grain=spec.get('record_grain'))
    result['binding'] = fingerprint(dependency)
    result['revision'] = state['revision']
    return result


def question_input(question):
    """Only declarative proposal fields survive rebinding; no model execution flags."""
    keys=('type','title','question','file_id','target','references','choices') if question.get('source')=='model' else ('type','question','file_id','target')
    result={k:copy.deepcopy(question[k]) for k in keys if question.get(k) is not None}
    if 'choices' in result:
        result['choices']=[{k:c[k] for k in ('id','label','basis','decision') if k in c} for c in result['choices']]
    return result


def normalize_model_question(question,state):
    allowed={'type','title','question','file_id','target','references','choices'}
    if set(question)-allowed or question.get('type')!='choice':raise PipelineError('模型选项问题须使用choice类型及受限字段，不能含确认标志或代码')
    text=question.get('question');title=question.get('title')
    if not isinstance(text,str) or not text.strip() or len(text)>3000 or not isinstance(title,str) or not title.strip() or len(title)>80:raise PipelineError('选项问题需要具体的问题文字和简短标题')
    refs=question.get('references');choices=question.get('choices')
    if not isinstance(refs,list) or not 1<=len(refs)<=16:raise PipelineError('模型选项须引用实际读取字段references')
    tables={f['id']:layout(f,next((p.get('header_line') for p in state['plan']['platforms'] if p['file_id']==f['id']),None)) for f in state['files']}
    for ref in refs:
        if not isinstance(ref,dict) or set(ref)!={'file_id','column'} or ref.get('file_id') not in tables or ref.get('column') not in tables[ref['file_id']]['headers']:raise PipelineError('模型选项引用了不存在的文件或字段')
    question=copy.deepcopy(question)
    fid=question.get('file_id')
    # Canonicalise only an unambiguous explicit field reference, never infer
    # business meaning from text similarity or observed values.
    if fid is None or question.get('target') is None:
        matches=[(p['file_id'],m['target']) for p in state['plan']['platforms'] for m in p['metrics']
                 if {'file_id':p['file_id'],'column':m['column']} in refs
                 and (fid is None or fid==p['file_id'])
                 and (question.get('target') is None or question['target']==m['target'])]
        if len(set(matches))==1:
            fid,question['target']=matches[0];question['file_id']=fid
    spec=next((p for p in state['plan']['platforms'] if p['file_id']==fid),None)
    target=question.get('target');metric=next((m for m in (spec or {}).get('metrics',[]) if m['target']==target),None)
    if fid is not None and spec is None or target is not None and metric is None:
        raise PipelineError('模型选项范围不存在；收到file_id='+str(fid)+' target='+str(target)+'；合法范围：'+str({p['file_id']:[m['target'] for m in p['metrics']] for p in state['plan']['platforms']})+'。多个项目的interpret问题请省略target，仅在references列出实际字段')
    if fid and fid not in {r['file_id'] for r in refs}:raise PipelineError('选项依据须包含当前文件的实际字段')
    if not isinstance(choices,list) or not 2<=len(choices)<=8:raise PipelineError('请针对具体问题提出2至8个不同选项，自定义和待确认由界面提供')
    validated=[];seen=set();labels=set()
    for c in choices:
        if not isinstance(c,dict):raise PipelineError('选项必须是对象')
        if set(c)-{'id','label','basis','decision'}:raise PipelineError('选项不支持字段：'+','.join(sorted(set(c)-{'id','label','basis','decision'}))+'；只允许id、label、basis、decision')
        cid=c.get('id');label=c.get('label');basis=c.get('basis');decision=c.get('decision',{'kind':'interpret'})
        if not isinstance(cid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',cid) or cid in seen or cid in ('custom','pending'):raise PipelineError('选项编号必须唯一且不能冒充自定义或待确认')
        if not isinstance(label,str) or not label.strip() or len(label)>1000 or label in labels or not isinstance(basis,str) or not basis.strip() or len(basis)>1200:raise PipelineError('每个选项需要不同的具体处理文字和提出依据')
        if not isinstance(decision,dict):raise PipelineError('选项decision须为受限动作对象')
        kind=decision.get('kind');note='';attention=False
        # Free-form labels are not a loophole around the executable scale guard.
        proposed=scale_values(label)
        available=known_maxima(state,fid,metric['column'] if metric else '\n'.join(r['column'] for r in refs))
        if proposed and not proposed <= available | {__import__('decimal').Decimal(100)}:
            raise PipelineError('选项满分缺少教师指令或原字段依据；不得用样本最大值推定分制，请改为填写明确满分的scale选项')
        if kind=='interpret':
            if set(decision)!={'kind'}:raise PipelineError('复杂方案只提交选择文字供模型理解，不允许携带任意补丁')
            note='选择后理解此方案并校验，条件不足时继续询问'
            attention=True
        elif kind=='scale':
            if set(decision)!={'kind','mode'} or metric is None or decision['mode'] not in ('percent','points'):
                raise PipelineError('分制选项须引用实际项目，mode为percent或points；满分只能由教师填写')
            note='按教师填写的原始满分折算' if decision['mode']=='points' else '确认原始数据为百分制'
        elif kind=='same_activity':
            if set(decision)-{'kind','strategy','timestamp_column'} or metric is None or decision.get('strategy') not in STRATEGIES:raise PipelineError('同一活动归并须引用当前实际项目及有效策略')
            if spec.get('record_grain') and len(spec['metrics'])>1:raise PipelineError('该文件存在多个项目和活动粒度，不能用单项目选项清除其他项目的活动划分；请改用interpret完整理解')
            field=decision.get('timestamp_column',metric.get('timestamp_column'))
            if field is not None and field not in tables[fid]['headers']:raise PipelineError('选项引用了不存在的提交时间字段')
            note='同一活动 · '+STRATEGIES[decision['strategy']]
            if decision['strategy']=='latest':
                from .common import submission_time
                invalid=0
                for row in tables[fid]['rows']:
                    if any(row['values'].get(k)!=v for k,v in metric.get('where',{}).items()):continue
                    try:submission_time(row['values'].get(field,''))
                    except PipelineError:invalid+=1
                if invalid:
                    note+='；原表'+str(invalid)+'条记录缺少有效时间，重复提交需补充'
                    attention=True
        elif kind=='different_activities':
            if set(decision)-{'kind','column'} or metric is None:raise PipelineError('不同活动须引用当前实际项目')
            field=decision.get('column')
            if field is not None and (field not in tables[fid]['headers'] or field in [spec.get(k) for k in ('id_column','name_column','class_column','phone_column')]):raise PipelineError('区分活动的字段必须真实存在且不能直接以身份字段代替业务活动')
            note='保留不同活动'+('；按“'+field+'”区分' if field else '；继续补充区分活动的依据')
            attention=field is None
        else:raise PipelineError('不支持的选项动作；复杂处理请使用interpret，经模型理解后校验')
        seen.add(cid);labels.add(label);validated.append({'id':cid,'label':label.strip(),'basis':basis.strip(),'decision':copy.deepcopy(decision),'note':note,'requires_attention':attention})
    inputs={f['id']:f['sha256'] for f in state['files'] if f['id'] in {r['file_id'] for r in refs}}
    contexts=[]
    for p in state['plan']['platforms']:
        if p['file_id'] not in inputs:continue
        context={k:p.get(k) for k in ('file_id','header_line','id_column','name_column','class_column','phone_column','identity_policy')}
        context['record_grain']=p.get('record_grain') or []
        context['metrics']=[m for m in p['metrics'] if not target or p['file_id']!=fid or m['target']==target]
        contexts.append(context)
    binding=fingerprint({'inputs':inputs,'platforms':contexts,'roster':state['plan'].get('roster_file'),
                         'rules':state.get('rules') if any(c['decision']['kind']=='interpret' for c in validated) else None})
    descriptor={'type':'choice','file_id':fid,'target':target,'question':text.strip(),'choices':[{k:c[k] for k in ('id','label','decision')} for c in validated]}
    return {**descriptor,'id':'M'+fingerprint(descriptor)[:20],'title':title.strip(),'choices':validated,'references':copy.deepcopy(refs),'binding':binding,'revision':state['revision'],'allow_custom':True,'source':'model'}


def checkpoint_questions(questions, state):
    result=[]
    for i, question in enumerate(questions, 1):
        if isinstance(question, str):
            result.append({'id':'S'+fingerprint(question)[:20], 'kind':'semantic', 'reason':question})
        else:
            result.append({'id':question['id'], 'kind':'semantic', 'reason':question['question'], 'clarification':question})
    return result


def apply_choice(state, issue, choice_id, parameters):
    q=issue['clarification']
    fresh=normalize(question_input(q), state)
    if fresh.get('binding')!=q.get('binding'): raise InputProblem('此问题依赖的数据整理方案已变化，请刷新后重新选择', 'semantic')
    if choice_id not in [c['id'] for c in fresh['choices']]: raise InputProblem('请选择当前问题提供的有效选项', 'semantic')
    if not isinstance(parameters, dict) or set(parameters)-{'max_score'}: raise InputProblem('选项参数无效', 'semantic')
    plan=copy.deepcopy(state['plan'])
    spec=next(p for p in plan['platforms'] if p['file_id']==q['file_id'])
    metric=next((m for m in spec['metrics'] if m['target']==q.get('target')),None)
    followups=[]
    if q.get('source')=='model':
        selected=next(c for c in fresh['choices'] if c['id']==choice_id)
        decision=selected['decision']
        if decision['kind']=='interpret':raise InputProblem('此选择需要先进行语义理解，不能直接修改数据','semantic')
        if parameters and not (decision['kind']=='scale' and decision['mode']=='points'):raise InputProblem('此选项不接受额外参数','semantic')
        if decision['kind']=='scale':
            from .common import number
            if decision['mode']=='points':
                maximum=number(parameters.get('max_score'))
                if maximum<=0:raise InputProblem('请填写大于0的原始满分','semantic')
                metric['scale']={'kind':'points','max_score':str(maximum)}
            else:metric['scale']={'kind':'percent'}
        elif decision['kind']=='same_activity':
            spec['record_grain']=[]
            metric['strategy']=decision['strategy']
            if 'timestamp_column' in decision:metric['timestamp_column']=decision['timestamp_column']
        elif decision.get('column'):
            field=decision['column'];file=next(f for f in state['files'] if f['id']==q['file_id']);table=layout(file,spec.get('header_line'))
            values=sorted({r['values'][field] for r in table['rows']})
            if not values or any(not clean(v) for v in values) or len(values)>16:raise InputProblem('活动字段有空值或活动超过16项，请补充可核对的活动划分','semantic')
            spec['record_grain']=[field]
            pos=spec['metrics'].index(metric)
            spec['metrics'][pos:pos+1]=[{**copy.deepcopy(metric),'target':metric['target']+' · '+v,'where':{**metric.get('where',{}),field:v},'strategy':'confirm'} for v in values]
        else:
            metric['strategy']='confirm'
            followups=[{'type':'custom','title':'区分具体作业','question':'已选择不同业务活动，请说明用哪个实际字段区分各项作业，以及每项内部多次提交如何处理；尚未合并或删除原始记录。'}]
        from .interaction import validate_plan
        validate_plan(state,plan)
        return plan,followups
    if q['type']=='identity_policy': spec['identity_policy']=choice_id
    elif q['type']=='strategy': metric['strategy']=choice_id
    elif q['type']=='scale':
        from .common import number
        if choice_id=='points':
            if number(parameters.get('max_score'))<=0: raise InputProblem('请填写大于0的原始满分', 'semantic')
            metric['scale']={'kind':'points','max_score':str(parameters['max_score'])}
        else: metric['scale']={'kind':'percent'}
    elif q['type']=='record_grain':
        if choice_id=='same_submission': spec['record_grain']=[]
        else:
            field=choice_id[6:];file=next(f for f in state['files'] if f['id']==q['file_id']);table=layout(file,spec.get('header_line'))
            values=sorted({r['values'][field] for r in table['rows']})
            if not values or any(not clean(v) for v in values) or len(values)>16: raise InputProblem('活动字段存在空值或不同活动超过16项，请通过整理表单核对实际活动', 'semantic')
            spec['record_grain']=[field]
            spec['metrics']=[{**copy.deepcopy(m),'target':m['target']+' · '+v,'where':{**m.get('where',{}),field:v},'strategy':'confirm'} for m in spec['metrics'] for v in values]
        for m in spec['metrics']:
            m['strategy']='confirm'
            followups.append({'type':'strategy','file_id':q['file_id'],'target':m['target'],'question':m['target']+'：同一活动有多次提交时如何归并？'})
    from .interaction import validate_plan
    validate_plan(state,plan)
    return plan,followups
