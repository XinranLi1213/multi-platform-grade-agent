"""Human-readable, version-bound audit export. Reads verified artifacts; never computes grades."""
import copy
import json
from pathlib import Path
from .common import PipelineError, sha

LABELS={'student_id':'编号','name':'姓名','class':'班级','phone':'联系方式','target':'项目','column':'原字段','strategy':'归并方式','scale':'分制','kind':'类型','max_score':'原始满分','metric':'项目','field':'字段','value':'采用值','raw':'原值','reason':'原因或依据','basis':'依据','source':'来源','sources':'来源','file_id':'文件编号','file_name':'文件名','record_id':'原记录位置','line':'起始行','end_line':'结束行','values':'原始字段值','disposition':'处理去向','status':'状态','method':'关联方式','observations':'全部提交','timestamp':'采用时间','timestamp_original':'原始时间','action':'操作','revision':'流程版本','instruction':'教师指令','weights':'权重','missing':'缺失与特殊状态规则','precision':'小数位数','formulas':'计算公式','expr':'表达式','operation':'组成方式','assessment_items':'考核项目','adjustments':'局部调整','scope':'适用范围','baseline':'判断基准','special':'特殊状态处理','cap':'封顶','order':'执行顺序','record_grain':'区分业务活动字段','where':'活动筛选','timestamp_column':'提交时间字段','category':'数据类别','role':'文件用途','header_line':'真实表头行','id_column':'编号字段','name_column':'姓名字段','class_column':'班级字段','phone_column':'联系方式字段','identity_policy':'缺编号关联','roster_file':'主花名册','platforms':'各文件整理方式','counts':'数量','input_records':'输入业务记录','wide_rows':'整理后人数','collapsed_submissions':'折叠冗余提交数','auto_resolved_no_id':'唯一匹配关联数','teacher_identity_resolutions':'教师确认身份数','missing_cells':'缺项单元格数','missing_students':'缺项人数','unmatched_records':'未匹配记录数','excluded_by_user':'明确排除记录数','source_records_audited':'审计覆盖记录数','phase':'处理状态','choice_id':'所选方式','question':'问题','text':'补充文字','parameters':'补充参数','ids':'指定编号','op':'条件','before':'调整前','after':'调整后','raw_state':'原始状态','numeric_value':'计算输入值'}
VALUES={'complete':'已完成','organized':'整理完成','data_checkpoint':'待核对数据','business_checkpoint':'待补充计算规则','understanding_checkpoint':'待补充理解','system_error':'程序未完成','understanding_error':'理解未完成','max':'取最高分','latest':'按时间最后一次','sum':'累加','mean':'平均','unique':'仅接受一致值','confirm':'暂不归并','percent':'百分制','points':'按满分折算','grade':'等级制','unknown':'待补分制','hold':'保留待确认','preserve':'保留特殊状态','zero':'记0','unique_name_class':'姓名与班级唯一一致','roster_entity':'主表对象','retained':'保留','folded_with_all_sources_retained':'归并且保留全部来源','pending_identity':'身份待确认','excluded_by_user':'教师明确排除','unmatched_left_join':'主表外待处理','pending':'待确认','submitted':'已提交','teacher_confirmation':'教师核对','unique_name_plus_secondary':'姓名及辅助字段唯一一致'}
HIDDEN={'directory','command','diagnostic','worker_report','engine_hashes','generated_code_sha256','manifest_sha256','output_sha256','rules_sha256','dwd_manifest_sha256','path','sha256','failure','traceback'}


def render(store, state):
    lines=['教师数据处理说明', '任务：'+state['task'], '流程版本：'+str(state['revision']), '当前状态：'+VALUES.get(state['phase'],state['phase']), '']
    def show(value,depth=0,raw=False):
        prefix='  '*depth
        if isinstance(value,dict):
            if not value:lines.append(prefix+'无')
            for k,v in value.items():
                if not raw and k in HIDDEN:continue
                if isinstance(v,(dict,list)):
                    lines.append(prefix+(k if raw else LABELS.get(k,k))+'：');show(v,depth+1,raw=raw or k=='values')
                else:lines.append(prefix+(k if raw else LABELS.get(k,k))+'：'+('空缺（未补值）' if v=='' else '未记录' if v is None else str(v) if raw else VALUES.get(str(v),str(v))))
        elif isinstance(value,list):
            if not value:lines.append(prefix+'无')
            for i,v in enumerate(value,1):lines.append(prefix+str(i)+'、');show(v,depth+1,raw=raw)
        else:lines.append(prefix+('空缺（未补值）' if value=='' else VALUES.get(str(value),str(value))))
    def section(title,value):lines.extend(['',title]);show(value)
    section('一、上传文件',[{'file_id':f['id'],'file_name':f['name']} for f in state['files']])
    section('二、实际采用的数据整理方案',state['plan'])
    section('三、计算与特殊状态规则',state.get('rules') or '未形成计分规则；本说明不补写规则')
    section('四、教师指令和问题裁决',{'教师指令':state.get('instructions',[]),'选项及自定义回答':state.get('clarification_answers',[]),'人工操作记录':state.get('events',[])})
    stage=state.get('stage1');audit=None
    if stage:
        directory=Path(stage['directory']).resolve()
        if not directory.is_relative_to(store.directory(state['id']).resolve()):raise PipelineError('整理审计不属于当前任务')
        manifest=directory/'manifest.json'
        if sha(manifest)!=stage['manifest_sha256']:raise PipelineError('整理审计版本校验失败')
        expected=json.loads(manifest.read_text(encoding='utf-8'))
        for name in ('wide.csv','audit.json','data.json'):
            if sha(directory/name)!=expected['files'][name]:raise PipelineError('整理文件完整性校验失败，未导出替代记录')
        audit=json.loads((directory/'audit.json').read_text(encoding='utf-8'))
    section('五、输入输出数量',audit.get('counts',{}) if audit else '当前无有效整理产物')
    for title,key in [('六、重复提交归并（所有原值）','deduplications'),('七、身份关联及人工依据','identity_actions'),('八、分制与格式转换','conversions'),('九、逐项来源','cell_sources'),('十、全部原始记录及处理去向','source_ledger'),('十一、缺失和异常','missing')]:
        section(title,audit.get(key,[]) if audit else '未生成，原始上传仍保留')
    stage2=state.get('stage2')
    if stage2:
        directory=Path(stage2['directory']).resolve()
        if not directory.is_relative_to(store.directory(state['id']).resolve()):raise PipelineError('计算结果不属于当前任务')
        if not stage or stage2['dwd_manifest_sha256']!=stage['manifest_sha256']:raise PipelineError('计算与整理版本不一致')
        # Reuse exact CSV and hash validation, including historical rows restored from verified CSV.
        class Reader:
            def load(self,_):return state
        from .service import PipelineStore
        PipelineStore.download(Reader(),state['id'],'dws')
    section('十二、完整计算结果（不受预览行数限制）',stage2.get('rows',[]) if stage2 else '当前未计算')
    section('十三、试算、特殊状态与局部调整变化',{k:stage2.get(k,[]) for k in ('summary','state_changes','adjustment_scope','adjustment_changes','adjustment_differences','failures')} if stage2 else [])
    section('十四、仍需处理的问题',state.get('issues',[]))
    section('十五、历史试算目录',[{'计算序号':t.get('trial_id'),'status':t.get('status'),'是否当前计算':t.get('trial_id')==(stage2 or {}).get('trial_id')} for t in state.get('trials',[])])
    lines.extend(['','说明：空缺、数值0及特殊标记分别保留。已归并记录仍保留其原值和来源；本说明没有重新计算成绩。'])
    return ('\r\n'.join(lines)+'\r\n').encode('utf-8-sig'),'text/plain; charset=utf-8','processing-report-v'+str(state['revision'])+'.txt'
