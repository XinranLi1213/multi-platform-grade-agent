"""Persisted feedback and read-only artifact bindings; never runs historical code."""
import copy
import csv
import io
from datetime import datetime, timezone
from pathlib import Path
from .common import PipelineError

def capture(state):
    turns=state.setdefault('conversation',[])
    if turns and turns[-1]['revision']==state['revision']:
        return
    if not turns and len(state.get('instructions',[]))>1:
        for n,old in enumerate(state['instructions'][:-1]):
            turns.append({'id':'legacy'+str(n),'revision':None,'instruction_id':old.get('id'),'text':old['text'],'phase':None})
    instruction=(state.get('instructions') or [{'text':state['task']}])[-1]
    previous=turns[-1] if turns else None
    text=instruction['text']
    if previous and previous.get('instruction_id')==instruction.get('id'):
        event=next((e for e in reversed(state.get('events',[])) if e.get('action') in ('teacher_decision','kept_pending','teacher_plan_change','business_rules_saved','adopt_trial','question_options_generated')),None)
        text={'teacher_decision':'补充核对信息','kept_pending':'继续待确认','teacher_plan_change':'调整数据整理方式','business_rules_saved':'修改计算规则','adopt_trial':'采用试算结果','question_options_generated':'生成待确认选项'}.get((event or {}).get('action'),'继续处理当前方案')
        if event:
            text+='：'+str(event.get('reason') or event.get('decision',{}).get('reason') or '')
    retry=previous if previous and (previous.get('text')==text or previous.get('instruction_id')==instruction.get('id')) and previous.get('phase') in ('understanding_error','system_error') else None
    if retry:
        text=retry['text']
        turns.pop()
    turns.append({'id':'r'+str(state['revision']),'revision':state['revision'],'instruction_id':instruction.get('id'),
        'text':text,'created_at':datetime.now(timezone.utc).isoformat(),'phase':state['phase'],
        'issues':copy.deepcopy(state.get('issues',[])),
        'understanding':copy.deepcopy(next((d.get('rationale') for d in reversed(state.get('decisions',[])) if d.get('source')==instruction.get('id')),None)),
        'summary':copy.deepcopy(state.get('view',{}).get('governance_summary',[])),
        'snapshot':{k:copy.deepcopy(state.get(k)) for k in ('stage1','stage2','rules','plan','accepted_result','events','instructions','clarification_answers','trials')}})
    if retry:
        turns[-1]['id']=retry['id']
        turns[-1]['attempts']=retry.get('attempts',[])+[{'revision':retry['revision'],'phase':retry['phase'],'issues':retry.get('issues',[])}]
    for stage_key in ('stage1','stage2','accepted_result'):
        if turns[-1]['snapshot'].get(stage_key):
            turns[-1]['snapshot'][stage_key].pop('rows',None)
    for trial in turns[-1]['snapshot'].get('trials') or []:
        trial.pop('rows',None)
    state['conversation_schema_version']=1

def historical(store,run_id,turn_id,kind):
    if kind not in ('dwd','dws','accepted','report'):raise PipelineError('未知产物')
    state=store.load(run_id)
    turn=next((t for t in state.get('conversation',[]) if t['id']==turn_id),None)
    if not turn:raise PipelineError('此历史轮次没有完整文件记录')
    saved=copy.deepcopy(state);saved.update(copy.deepcopy(turn['snapshot']))
    if kind=='report':
        from .audit_report import render
        if 'events' not in turn['snapshot']:raise PipelineError('此旧轮未保存完整处理记录，不能用当前审计冒充历史说明')
        saved.update(revision=turn['revision'],phase=turn['phase'],issues=turn.get('issues',[]))
        stage=saved.get('stage2')
        if stage:
            from .common import sha
            artifact=Path(stage['directory'])/'output.csv'
            if not artifact.resolve().is_relative_to(store.directory(run_id).resolve()) or sha(artifact)!=stage.get('output_sha256'):raise PipelineError('历史计算完整性校验失败')
            stage['rows']=list(csv.DictReader(io.StringIO(artifact.read_text(encoding='utf-8-sig'),newline='')))
        return render(store,saved)
    stage=saved.get({'dwd':'stage1','dws':'stage2','accepted':'accepted_result'}[kind])
    if not stage:raise PipelineError('此轮未生成该文件')
    if not Path(stage['directory']).resolve().is_relative_to(store.directory(run_id).resolve()):raise PipelineError('产物不属于当前任务')
    artifact=Path(stage['directory'])/('wide.csv' if kind=='dwd' else 'output.csv')
    if not artifact.resolve().is_relative_to(store.directory(run_id).resolve()):raise PipelineError('文件位置不属于当前任务')
    if kind!='dwd':
        from .common import sha
        if sha(artifact)!=stage.get('output_sha256'):raise PipelineError('历史文件完整性检查失败')
        if not saved.get('stage1') or stage.get('dwd_manifest_sha256')!=saved['stage1']['manifest_sha256']:raise PipelineError('历史计算与整理版本不一致')
        stage['rows']=list(csv.DictReader(io.StringIO(artifact.read_text(encoding='utf-8-sig'),newline='')))
    class Reader:
        directory=store.directory
        def load(self,_):return saved
    from .service import PipelineStore
    raw,content_type,name=PipelineStore.download(Reader(),run_id,kind)
    return raw,content_type,name

def preview(store,run_id,turn_id,kind):
    raw,_,name=historical(store,run_id,turn_id,kind)
    reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig'),newline=''))
    rows=list(reader)
    return {'name':name,'columns':reader.fieldnames or [],'rows':rows[:100],'total_rows':len(rows),'status':'历史版本'}

def replay(store,run_id,action,data):
    from .semantic import fingerprint
    key=data.get('request_id')
    if not key:return None
    if not isinstance(key,str) or not 0<len(key)<=128:raise PipelineError('请求标识无效')
    state=store.load(run_id)
    prior=state.get('interaction_requests',{}).get(key)
    digest=fingerprint({'action':action,'data':{k:v for k,v in data.items() if k not in ('revision','request_id')}})
    if prior:
        if prior['hash']!=digest:raise PipelineError('同一请求标识不能提交不同内容')
        state['feedback']={'code':'request_replayed','message':'此操作已处理，已恢复当前状态，没有重复执行。'}
        return state
    return None

def record_request(store,state,action,data):
    from .semantic import fingerprint
    key=data.get('request_id')
    if not key:return
    state.setdefault('interaction_requests',{})[key]={'hash':fingerprint({'action':action,'data':{k:v for k,v in data.items() if k not in ('revision','request_id')}}),'revision':state['revision']}
    store.save(state)
