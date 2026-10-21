const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"'\r\n\t]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;','\r':'&#13;','\n':'&#10;','\t':'&#9;'}[c]));
const clone=v=>JSON.parse(JSON.stringify(v)),pretty=v=>JSON.stringify(v,null,2);
const semanticReady=new Promise(resolve=>window.addEventListener('pipeline-semantic-ready',resolve,{once:true}));
const strategies={max:'取最高分',latest:'按提交时间取最后一次',sum:'累加',mean:'平均',unique:'仅接受一致值',confirm:'暂不处理'};
const phases={complete:'已完成',data_checkpoint:'等待核对数据',business_checkpoint:'等待补充计算规则',system_error:'程序执行未完成',reading:'正在读取',understanding_checkpoint:'等待补充理解',understanding_error:'语义服务未完成',migration_required:'历史状态需在本机重跑'};
const labels={student_id:'编号',name:'姓名',class:'班级',phone:'联系方式',__calculation_status:'计算状态',__calculation_reason:'处理原因'};
let current=null,modelEnabled=false,busy=false,planBase=null,rulesBase=null,layoutDrafts={},draft={cards:{}},planPreview=null;
function storageKey(){return 'pipeline-teacher-drafts:'+current.id;}
function remember(){if(current)localStorage.setItem(storageKey(),pretty(draft));}
function note(text,tone=false){$('message').hidden=false;$('message').textContent=text;$('message').className=tone===true?'error':tone==='warning'?'note warning':'note';}
async function request(path,data){const r=await fetch(path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});const j=await r.json();if(!r.ok){const e=new Error(j.problem?.reason||j.error||`请求失败（${r.status}）`);e.status=r.status;e.problem=j.problem;throw e;}return j;}
async function action(fn,scope=null){if(busy)return;busy=true;document.querySelectorAll('button').forEach(b=>b.disabled=true);try{await fn();}catch(e){let message=e.message;if(e.status===409&&current){message='流程已在其他操作中更新。已恢复最新状态，草稿保留，请核对后再次提交。';render(await request('/api/pipelines/'+current.id));}if(e.problem?.kind==='system'){if(current)current.stage2=null;$('dws-card').innerHTML='<p class="note error">本次执行失败，没有当前计算结果。</p>';$('dws-links').hidden=true;$('system-section').hidden=false;$('system-message').textContent=message;}if(scope&&document.contains(scope))scope.querySelector('.card-error').textContent=message;note(message,true);}finally{busy=false;document.querySelectorAll('button').forEach(b=>b.disabled=false);$('pipeline-suggest').disabled=!modelEnabled;}}
function opts(values,selected,empty='未指定'){return `<option value="">${empty}</option>`+values.map(v=>`<option value="${esc(v)}" ${v===selected?'selected':''}>${esc(v)}</option>`).join('');}
function enumOpts(values,selected){return Object.entries(values).map(([v,label])=>`<option value="${v}" ${v===selected?'selected':''}>${esc(label)}</option>`).join('');}
function table(columns,rows,id){return `<div class="table-wrap"><table id="${id}"><thead><tr>${columns.map(c=>`<th>${esc(labels[c]||c)}</th>`).join('')}</tr></thead><tbody>${rows.slice(0,50).map(r=>`<tr>${columns.map(c=>`<td data-raw="${esc(r[c])}">${r[c]===''?'<span class="null">空缺</span>':esc(r[c])}</td>`).join('')}</tr>`).join('')}</tbody></table></div><p class="hint">预览${Math.min(50,rows.length)}/${rows.length}行，CSV包含全部行。原值保留；空缺不代表0。</p>`;}
function links(kinds){return kinds.map(([k,label])=>`<a class="download" id="download-${k}" href="/api/pipelines/${current.id}/download?kind=${k}">${label}</a>`).join('');}
function stateMessage(){if(current.phase==='system_error')note('本次程序执行未完成，上传和方案已保留，可以重试。',true);else if(current.feedback)note(current.feedback.message,current.phase==='complete'?false:'warning');else if(current.phase==='complete')note('整理和计算已完成，可核对并下载结果。');else note(`还有${current.issues.length}项需要核对。请在问题卡片中选择补充方式；计算规则可以先保存。`,'warning');}
// Formatting only. Actual percentage-to-weight conversion runs in Python Decimal.
function percentDisplay(value){
  if(value===undefined)return '';
  const text=String(value),match=text.match(/^([+-]?)(\d*)(?:\.(\d*))?$/);
  if(!match)return text; // scientific notation is displayed as entered in advanced rules
  const tail=match[3]||'',integer=((match[2]||'0')+(tail+'00').slice(0,2)).replace(/^0+(?=\d)/,'');
  const fraction=tail.slice(2).replace(/0+$/,'');return match[1]+integer+(fraction?'.'+fraction:'');
}
function ruleSummary(r){if(!r)return '尚未形成可执行计算规则，请补充完整权重或明确公式。';const pieces=[];if(r.weights_percent||r.weights)pieces.push('权重：'+Object.entries(r.weights_percent||r.weights).map(([k,v])=>`${r.assessment_items?.find(i=>i.id===k)?.name||k} ${r.weights_percent?v:percentDisplay(v)}%`).join('、'));else if(r.formulas)pieces.push('公式：'+r.formulas.map(x=>x.name+' = '+x.expr).join('；'));const actions={zero:'记0',preserve:'保留特殊标识',hold:'待确认',value:'折算为'};for(const [state,p] of Object.entries(r.missing||{}))pieces.push(`${state||'空缺'}：${actions[p.action]||'待确认'}${p.action==='value'?p.value:''}`);for(const [metric,policies] of Object.entries(r.missing_by_field||{}))for(const [state,p] of Object.entries(policies))pieces.push(`${metric}的${state||'空缺'}：${actions[p.action]}${p.action==='value'?p.value:''}`);if(r.precision!==undefined)pieces.push(`保留${r.precision}位小数`);if(r.cap!==undefined)pieces.push('总评分封顶'+r.cap);if(r.excellent_threshold!==undefined)pieces.push(`优秀阈值${r.excellent_threshold}，分母${r.excellent_denominator==='all'?'全部编号':'数值结果人数'}`);return pieces.join('；')||'尚无明确计算公式';}
function drawReading(){const tables=current.view?.tables||[];$('reading-results').innerHTML=tables.map(t=>`<article><h3>${esc(t.name)}</h3><p>${t.errors.length?'读取有异常，暂停执行':'已读取'} · 表头位于第${t.header_line}行 · ${t.rows.length}条数据 · ${esc(t.headers.join('、'))}</p><p class="hint">${esc(pretty(t.reading_actions))}</p>${t.errors.map(x=>`<p class="error">${esc(x)}</p>`).join('')}${table(t.headers,t.rows.map(r=>r.values),'reading-'+t.file_id)}<p class="hint">说明、重复表头、汇总及空白等非业务行：${t.excluded.length}条，完整原值留在审计中。</p></article>`).join('');}
function metricHTML(m,index,headers,rows=[]){return `<fieldset class="metric-form" data-index="${index}"><legend>整理项目 ${index+1}</legend><div class="form-grid"><label>原字段<select data-m="column">${opts(headers,m.column)}</select></label><label>统一项目名称<input data-m="target" value="${esc(m.target)}"></label><label>归并方式<select data-m="strategy">${enumOpts(strategies,m.strategy)}</select></label><label class="time-setting" ${m.strategy==='latest'?'':'hidden'}>提交时间字段<select data-m="timestamp_column">${opts(headers,m.timestamp_column,'请选择时间字段')}</select></label><label>原始分制<select data-scale="kind">${enumOpts({percent:'百分制',points:'明确原始满分',grade:'等级对应',unknown:'待明确',conflict:'原分制存在冲突'},m.scale?.kind)}</select></label><label class="points-setting" ${m.scale?.kind==='points'?'':'hidden'}>原始满分<input data-scale="max_score" value="${esc(m.scale?.max_score||'')}"></label></div>${activityFilterHTML(m,headers,rows)}<div class="grade-setting" ${m.scale?.kind==='grade'?'':'hidden'}><p>等级与百分制对应（保留原等级值与转换依据）</p><div class="grade-rows">${Object.entries(m.scale?.levels||{优:'95',良:'85',中:'75',及格:'65',不及格:'0'}).map(([k,v])=>`<label>${esc(k)}<input data-level="${esc(k)}" value="${esc(v)}"></label>`).join('')}</div><button type="button" class="add-level">添加等级</button></div></fieldset>`;}
function drawGovernance(){const tables=current.view?.tables||[];layoutDrafts={...clone(tables.reduce((a,t)=>(a[t.file_id]=t,a),{})),...(draft.layouts||{})};planBase=clone(draft.plan||current.plan);for(const key of ['identity_resolutions','cell_resolutions','timestamp_resolutions','excluded_records','append_unmatched_ids','append_reasons'])if(current.plan[key]!==undefined)planBase[key]=clone(current.plan[key]);$('governance-form').innerHTML=`<label>主花名册<select id="roster-file">${opts(current.files.map(f=>f.id),planBase.roster_file,'按实际唯一编号人数选择')}</select></label>`+planBase.platforms.map(p=>{const f=current.files.find(f=>f.id===p.file_id),t=layoutDrafts[p.file_id];if(!t)return '';return `<article class="file-form" data-file="${p.file_id}"><h3>${esc(f.name)} <small>${p.file_id}</small></h3><label>真实表头所在行<select data-p="header_line">${(f.raw_rows||[]).slice(0,40).map(r=>`<option value="${r.line}" ${r.line===p.header_line?'selected':''}>第${r.line}行 · ${esc(r.cells.join(' / '))}</option>`).join('')}</select></label><div class="header-preview">${table(t.headers,t.rows.slice(0,3).map(r=>r.values),'header-'+p.file_id)}</div><div class="form-grid">${[['id_column','编号字段'],['name_column','姓名字段'],['class_column','班级字段'],['phone_column','联系方式字段']].map(([key,label])=>`<label>${label}<select data-p="${key}">${opts(t.headers,p[key])}</select></label>`).join('')}</div><label>区分不同业务活动的字段（可多选；同一学生不同活动不视为重复提交）<select data-grain multiple>${t.headers.map(h=>`<option value="${esc(h)}" ${(p.record_grain||[]).includes(h)?'selected':''}>${esc(h)}</option>`).join('')}</select></label><div class="metric-list">${p.metrics.map((m,i)=>metricHTML(m,i,t.headers,t.rows)).join('')}</div><button class="add-metric">添加整理项目</button></article>`;}).join('');
  for(const option of $('roster-file').options)if(option.value)option.textContent=current.files.find(f=>f.id===option.value).name;
  $('plan-reason').value=draft.planReason||'';
  document.querySelectorAll('.file-form').forEach(node=>{node.querySelector('[data-p="header_line"]').onchange=()=>action(async()=>{const selected=Number(node.querySelector('[data-p="header_line"]').value);const t=await request(`/api/pipelines/${current.id}/layout`,{revision:current.revision,file_id:node.dataset.file,header_line:selected});const p=planBase.platforms.find(p=>p.file_id===node.dataset.file);p.header_line=selected;layoutDrafts[p.file_id]=t;draft.layouts=clone(layoutDrafts);node.querySelector('.header-preview').innerHTML=table(t.headers,t.rows.slice(0,3).map(r=>r.values),'header-'+p.file_id);node.querySelectorAll('[data-p]:not([data-p="header_line"]),[data-m="column"],[data-m="timestamp_column"]').forEach(s=>{const val=s.value;s.innerHTML=opts(t.headers,t.headers.includes(val)?val:'');});draft.plan=collectPlan();remember();note(t.errors.length?t.errors.join('；'):'已更新实际表头预览，请核对字段映射。',!!t.errors.length);});node.querySelector('.add-metric').onclick=()=>{const p=planBase.platforms.find(p=>p.file_id===node.dataset.file);const m={column:'',target:'',strategy:'confirm',scale:{kind:'unknown'}};p.metrics.push(m);node.querySelector('.metric-list').insertAdjacentHTML('beforeend',metricHTML(m,p.metrics.length-1,layoutDrafts[p.file_id].headers,layoutDrafts[p.file_id].rows));bindMetricVisibility(node);};bindMetricVisibility(node);});
  $('governance-form').oninput=()=>{draft.plan=collectPlan();planPreview=null;$('plan-preview').hidden=true;remember();};$('plan-reason').oninput=()=>{draft.planReason=$('plan-reason').value;remember();};$('governance-plan').value=pretty(planBase);
}
function bindMetricVisibility(node){node.querySelectorAll('.metric-form').forEach(m=>{m.querySelector('[data-m="strategy"]').onchange=()=>m.querySelector('.time-setting').hidden=m.querySelector('[data-m="strategy"]').value!=='latest';m.querySelector('[data-scale="kind"]').onchange=()=>{const k=m.querySelector('[data-scale="kind"]').value;m.querySelector('.points-setting').hidden=k!=='points';m.querySelector('.grade-setting').hidden=k!=='grade';};m.querySelector('.add-level').onclick=()=>{const row=document.createElement('label');row.innerHTML='<input class="level-name" placeholder="原始等级"><input class="level-value" placeholder="对应值">';m.querySelector('.grade-rows').append(row);};});}
function collectPlan(){const p=clone(planBase);p.roster_file=$('roster-file').value||null;document.querySelectorAll('.file-form').forEach(node=>{const spec=p.platforms.find(x=>x.file_id===node.dataset.file);if(node.querySelector('[data-grain]')){const selected=[...node.querySelector('[data-grain]').selectedOptions].map(o=>o.value);if(selected.length||spec.record_grain)spec.record_grain=selected;}node.querySelectorAll('[data-p]').forEach(x=>spec[x.dataset.p]=x.dataset.p==='header_line'?Number(x.value):(x.value||null));node.querySelectorAll('.metric-form').forEach(n=>{const m=spec.metrics[Number(n.dataset.index)];n.querySelectorAll('[data-m]').forEach(x=>{if(x.dataset.m==='timestamp_column'&&!x.value&&!Object.hasOwn(m,'timestamp_column'))return;m[x.dataset.m]=x.dataset.m==='timestamp_column'?(x.value||null):x.value;});if(n.querySelector('[data-filter-column]')){const col=n.querySelector('[data-filter-column]').value,val=n.querySelector('[data-filter-value]').value;const previous=Object.keys(m.where||{})[0],where={...(m.where||{})};if(previous)delete where[previous];if(col)where[col]=val;if(Object.keys(where).length||Object.hasOwn(m,'where'))m.where=where;}m.scale||={};m.scale.kind=n.querySelector('[data-scale="kind"]').value;if(m.scale.kind==='points')m.scale.max_score=n.querySelector('[data-scale="max_score"]').value;if(m.scale.kind==='grade'){m.scale.levels={};n.querySelectorAll('[data-level]').forEach(x=>m.scale.levels[x.dataset.level]=x.value);n.querySelectorAll('.level-name').forEach(x=>{if(x.value)m.scale.levels[x.value]=x.parentNode.querySelector('.level-value').value;});}});});return p;}
function cardKey(q){return [q.kind,q.student_id||'',q.metric||q.field||'',...(q.records||[]).map(r=>r.record_id)].join('|');}
function rawRecords(q){return (q.records||q.sources?.map(s=>s.source||s)||[]).map(r=>`<div class="raw-record"><strong>${esc(r.file_name||current.files.find(f=>f.id===r.file_id)?.name||'原始来源')} · 第${r.line??'?'}行</strong><dl>${Object.entries(r.values||{}).map(([k,v])=>`<div><dt>${esc(k)}</dt><dd data-raw="${esc(v)}">${v===''?'空缺':esc(v)}</dd></div>`).join('')}</dl></div>`).join('');}
function cardValues(node){
  const value={};
  node.querySelectorAll('[data-card]').forEach(input=>value[input.dataset.card]=input.value);
  value.action=value.student_id==='__pending__'?'pending':value.action||(value.student_id?'confirm':'');
  value.timestamps=Object.fromEntries([...node.querySelectorAll('[data-time-record]')].map(input=>[input.dataset.timeRecord,input.value]));
  return value;
}

function updateCardMode(node,question){
  const mode=cardValues(node).action;
  const pending=mode==='pending';
  const valueSection=node.querySelector('.cell-value-section');
  if(valueSection){valueSection.hidden=mode!=='confirm';valueSection.querySelector('input').disabled=mode!=='confirm';}
  const timeSection=node.querySelector('.timestamp-section');
  if(timeSection){timeSection.hidden=mode!=='timestamps';timeSection.querySelectorAll('input').forEach(input=>input.disabled=mode!=='timestamps');}
  node.querySelector('.reason-title').textContent=pending?'待确认备注（可选）':mode==='timestamps'?'时间核对依据（必填）':'核对依据（必填）';
  const button=node.querySelector('.confirm-card');
  button.textContent=pending?'保存待确认，暂不计算':mode==='timestamps'?'保存提交时间并重新整理':question.kind==='identity'?'确认身份并继续':'确认此裁决并继续';
  node.querySelector('.mode-help').textContent=pending?'本次只保存备注，问题仍保留，不采用任何裁决成绩，也不会开始计算。':mode==='timestamps'?'按原始记录逐条补充或修正时间。保留原值和依据，再由Python按时间重新整理。':mode==='confirm'?'将明确的裁决值和依据保存后重新处理。请以核对到的提交记录或业务规则为依据。':'请先选择：补充时间、明确裁决，或暂时保留待确认。';
}

function drawCards(){
  const questions=current.issues.filter(q=>!['business_rule','missing_policy','system','semantic','model','migration'].includes(q.kind));
  $('remaining-count').textContent=`剩余 ${current.issues.length} 项`;
  $('checkpoint').hidden=!current.issues.length;
  const guidanceFor=q=>(current.view?.issue_guidance||[]).find(g=>g.issue_id===q.id)||{};
  $('quick-resolutions').innerHTML=questions.map((q,index)=>{
    const key=cardKey(q),saved=draft.cards[key]||{},guidance=guidanceFor(q);
    let fields='';
    if(q.kind==='identity'){
      const candidates=q.candidates?.length?q.candidates:(current.stage1?.rows||[]);
      fields=`<p>请核对候选人的编号、班级与联系方式。姓名相同不代表同一个人。</p><div class="candidate-list">${candidates.map(c=>{const m=c.master||c;return `<p>${esc(c.student_id)} · ${esc(m.name)} · ${esc(m.class||'班级未提供')} · ${esc(m.phone||'联系方式未提供')}</p>`;}).join('')}</div><label>这条记录对应谁？<select data-card="student_id" data-identity="${esc(q.records?.[0]?.record_id)}">${opts(candidates.map(c=>c.student_id),saved.student_id,'请选择身份（不自动选择）')}<option value="__pending__" ${saved.action==='pending'?'selected':''}>继续待确认，保留该记录</option></select></label>`;
    }else if(q.kind==='unmatched'){
      fields=`<label>如何处理主表外记录？<select data-card="action">${opts([],null,'请选择处理方式')}${enumOpts({append:'追加此编号到主花名册',exclude:'明确排除此记录',pending:'继续待确认，保留该记录'},saved.action)}</select></label>`;
    }else if(q.student_id&&(q.metric||q.field)){
      const isTime=guidance.kind==='submission_time';
      const modes=isTime?{timestamps:'补充或修正提交时间，按时间重新整理',confirm:'明确裁决采用的成绩',pending:'继续待确认，保留全部记录'}:{confirm:'提供明确裁决值',pending:'继续待确认，保留全部记录'};
      fields=`<p>${esc(isTime?guidance.question:'请对照原始记录提供明确的裁决与依据，也可以调整数据整理方式。')}</p><label>本次要如何处理？<select data-card="action">${isTime?opts([],null,'请选择本次处理方式'):''}${enumOpts(modes,saved.action||(isTime?'':'confirm'))}</select></label>`;
      if(isTime&&guidance.timestamp_field){
        fields+=`<div class="timestamp-section" hidden>${guidance.timestamps.map(time=>`<label>${esc(time.file_name)} · 第${time.line}行 · ${esc(time.field)}<small class="hint">原值：${esc(time.original||'空缺')}；当前采用：${esc(time.effective||'尚未提供')}</small><input data-time-record="${esc(time.record_id)}" data-time-effective="${esc(time.effective)}" value="${esc(saved.timestamps?.[time.record_id]??time.effective)}" placeholder="例如2026-10-18T14:00:00，不会自动填时间"></label>`).join('')}</div>`;
      }
      fields+=`<label class="cell-value-section">确认采用的值${q.metric?'（成绩使用百分制）':''}<input data-card="value" value="${esc(saved.value??'')}" placeholder="填写核对后确定的值；暂不裁决请选继续待确认"></label>`;
    }
    const actionable=fields.includes('data-card');
    const explanation=guidance.explanation||q.reason;
    return `<article class="question-card" id="question-${index}" data-key="${esc(key)}"><h3>${esc(guidance.title||(q.kind==='identity'?'这条记录属于哪位学生？':q.kind==='deduplication'?'这些提交应如何整理？':'需要核对的数据'))}</h3><p>${esc(explanation)}</p>${rawRecords(q)}${fields||'<p>请展开“调整数据整理方式”，核对实际字段和分制后重新处理。</p>'}${actionable?`<p class="mode-help note"></p><label><span class="reason-title">核对依据（必填）</span><input data-card="reason" data-identity-reason="${esc(q.records?.[0]?.record_id||'')}" value="${esc(saved.reason||'')}" placeholder="${guidance.kind==='submission_time'?'说明核对到的提交记录、平台日志等出处':'说明核对到的记录和依据'}"></label><p class="card-error" role="alert"></p><button class="primary confirm-card">提交本次选择</button>`:''}</article>`;
  }).join('');
  $('quick-resolutions').insertAdjacentHTML('beforeend',current.issues.filter(q=>['semantic','model','business_rule','missing_policy','migration'].includes(q.kind)).map(q=>`<article class="question-card"><h3>${q.kind==='model'?'理解服务尚未完成':'需要补充的具体内容'}</h3><p>${esc(q.reason)}</p>${rawRecords(q)}<p>${esc(q.required_input||'在上方任务框补充，或展开修改理解；已有明确规则继续保留。')}</p><button class="focus-instruction">在任务框补充</button> <button class="open-understanding">展开修改理解</button></article>`).join(''));
  document.querySelectorAll('.focus-instruction').forEach(b=>b.onclick=()=>{$('pipeline-task').focus();$('pipeline-task').scrollIntoView({block:'center'});});
  document.querySelectorAll('.open-understanding').forEach(b=>b.onclick=()=>{$('rules-section').open=true;$('rules-section').scrollIntoView({block:'start'});});
  questions.forEach((q,index)=>{
    const node=$('question-'+index),key=cardKey(q),guidance=guidanceFor(q);
    if(!node.querySelector('.confirm-card'))return;
    updateCardMode(node,q);
    node.oninput=()=>{draft.cards[key]=cardValues(node);remember();updateCardMode(node,q);node.querySelector('.card-error').textContent='';if($('message').classList.contains('error'))$('message').hidden=true;};
    node.querySelector('.confirm-card').onclick=()=>{
      const values=cardValues(node);
      const decision={issue_id:q.id,action:values.action,reason:values.reason};
      let error='';
      if(values.action!=='pending'){
        if(q.kind==='identity'&&!values.student_id)error='请选择主花名册中的编号';
        else if(!values.action)error='请选择本次处理方式';
        else if(!values.reason?.trim())error=values.action==='timestamps'?'请填写补充或修正提交时间的核对依据':'请填写核对依据';
        else if(values.action==='timestamps'){
          decision.timestamps=(guidance.timestamps||[]).filter(time=>(values.timestamps[time.record_id]||'').trim()!==time.effective).map(time=>({record_id:time.record_id,value:(values.timestamps[time.record_id]||'').trim()}));
          if(!decision.timestamps.length)error='请至少补充或修正一条提交时间';
          else if(decision.timestamps.some(time=>!/^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}/.test(time.value)))error='请填写包含日期和时分的提交时间，例如2026-10-18T14:00:00';
        }else if(q.kind==='identity')decision.student_id=values.student_id;
        else if(q.kind!=='unmatched'){
          decision.value=values.value?.trim();
          if(!decision.value)error='请填写明确的裁决值；暂不裁决请选择继续待确认';
          else if(q.metric&&!['未交','缺考','缓考','免修','缺勤'].includes(decision.value)&&!/^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(decision.value))error='请填写具体成绩数值（例如99）或允许的特殊状态；暂不裁决请选择继续待确认';
        }
      }
      if(error){node.querySelector('.card-error').textContent=error;return;}
      action(async()=>{
        const state=await request(`/api/pipelines/${current.id}/resolve`,{revision:current.revision,decision});
        if(values.action!=='pending')delete draft.cards[key];
        remember();render(state);stateMessage();
      },node);
    };
  });
  renderConfirmationHistory();
}

function renderConfirmationHistory(){
  const groups=new Map();
  const active=new Set(current.issues.map(cardKey));
  for(const event of current.events||[]){
    if(!['teacher_decision','kept_pending'].includes(event.action))continue;
    const key=event.action==='kept_pending'?cardKey(event.issue)+'|'+(event.decision.reason||'').trim():JSON.stringify(event);
    const count=(groups.get(key)?.count||0)+1;groups.delete(key);groups.set(key,{event,count});
  }
  $('confirmation-history').innerHTML=[...groups.values()].slice(-8).map(({event,count})=>{
    const pending=event.action==='kept_pending',d=event.decision;
    const laterResolved=pending&&!active.has(cardKey(event.issue))&&current.stage1&&current.phase!=='system_error';
    const content=pending?(laterResolved?'当时保留待确认，后续已处理':'保留该记录，等待核对'):d.action==='timestamps'?'补充提交时间：'+d.timestamps.map(t=>t.record_id+' → '+t.value).join('；'):d.student_id||d.value||({append:'追加主花名册',exclude:'明确排除'}[d.action]||'已保存');
    return `<p class="note ${pending&&!laterResolved?'warning':''}">${pending?(laterResolved?'此前待确认（现已处理）':'继续待确认（尚未解决）'):'已保存本次核对'}：${esc(event.issue.records?.map(r=>`${r.file_name}第${r.line}行`).join('、'))} → ${esc(content)}。依据：${esc(d.reason||'尚未提供')}${count>1?`（同样备注累计保存${count}次，已合并显示；完整审计保留）`:''}</p>`;
  }).join('');
}
function drawRules(){
  rulesBase=clone(draft.rules||current.business_draft||current.rules||{missing:{}});
  const metrics=[...new Set([...current.plan.platforms.flatMap(p=>p.metrics.map(m=>m.target)),...(rulesBase.assessment_items||[]).map(i=>i.name)])];
  const policyKeys=Object.keys(rulesBase.missing||{});
  for(const q of current.issues)if(q.kind==='missing_policy'&&!policyKeys.includes(q.state))policyKeys.push(q.state);
  const policyRows=policyKeys.map(state=>({state,field:'',policy:rulesBase.missing?.[state]}));
  for(const [field,policies] of Object.entries(rulesBase.missing_by_field||{}))for(const [state,policy] of Object.entries(policies))policyRows.push({state,field,policy});
  const policyHTML=({state,field,policy})=>`<div class="policy-row" data-policy="${esc(state)}" data-policy-field="${esc(field)}"><label>${esc(field?field+'的':'')}${esc(state||'空缺')}如何处理？<select data-policy-action>${enumOpts({hold:'继续待确认',zero:'明确记0',preserve:'保留特殊标识',value:'按明确数值折算'},policy?.action||'hold')}</select></label><label class="policy-value" ${policy?.action==='value'?'':'hidden'}>折算值<input value="${esc(policy?.value??'')}"></label></div>`;
  const percentFor=m=>{const key=Object.hasOwn(rulesBase.weights||{},m)?m:(rulesBase.assessment_items||[]).find(i=>i.name===m)?.id||m;return rulesBase.weights_percent?.[m]??rulesBase.weights_percent?.[key]??(pretty(rulesBase.weights)===pretty(current.rules?.weights)?current.view?.weight_percentages?.[key]:percentDisplay(rulesBase.weights?.[key]));};
  const dataPending=!current.stage1||current.stage1.status!=='ready';
  $('rule-question').textContent=dataPending?`当前有${current.issues.length}项数据问题待核对。这里可以先保存计算规则；数据核对完成后将自动采用保存的规则。`:current.issues.filter(q=>['business_rule','missing_policy','semantic','model'].includes(q.kind)).map(q=>q.reason).join('；')||'请核对本次将执行的有效规则。未填写的状态保持待确认。';
  $('resume-pipeline').textContent=dataPending?'保存计算规则，待数据核对后执行':'按上述规则计算';
  $('business-form').innerHTML=`${rulesBase.formulas&&!rulesBase.weights&&!rulesBase.weights_percent?'<p>当前使用复杂公式，常见权重表不会覆盖它。修改公式请使用高级设置。</p>':`<div class="form-grid">${metrics.map(m=>`<label>${esc(m)}权重（%）<input data-weight="${esc(m)}" type="number" step="any" min="0" max="100" value="${esc(percentFor(m)??'')}"></label>`).join('')}</div>`}
  <div class="form-grid"><label>小数位数<select id="precision">${opts(['0','1','2','3','4','5','6','7','8','9','10'],rulesBase.precision===undefined?'':String(rulesBase.precision),'不额外舍入')}</select></label><label>总评分封顶（可选）<input id="rule-cap" value="${esc(rulesBase.cap??'')}"></label></div>
  <details><summary>优秀率统计（可选）</summary><div class="form-grid"><label>优秀阈值<input id="excellent-threshold" value="${esc(rulesBase.excellent_threshold??'')}" placeholder="例如90，由教师明确"></label><label>统计分母<select id="excellent-denominator">${opts([],null,'请选择统计人数范围')}${enumOpts({numeric:'数值计算结果人数',all:'全部编号人数'},rulesBase.excellent_denominator)}</select></label><label>统计结果字段<select id="summary-field">${opts(rulesBase.formulas?.map(f=>f.name)||[rulesBase.output||'总评'],rulesBase.summary_field||rulesBase.output||'总评')}</select></label></div></details>
  <div class="policy-list">${policyRows.map(policyHTML).join('')}</div><p class="hint">特殊状态仅影响计算输入，阶段一的原始状态保留。空缺与“未交”分别处理。单项目规则优先于全局规则。</p>`;
  $('business-form').oninput=()=>{draft.rules=collectRules();remember();$('rule-preview').textContent=(dataPending?'本次将保存，待数据核对后执行：':'本次将执行：')+ruleSummary(draft.rules);};
  document.querySelectorAll('[data-policy-action]').forEach(x=>x.onchange=()=>x.closest('.policy-row').querySelector('.policy-value').hidden=x.value!=='value');
  $('rule-preview').textContent=(draft.rules?'尚未保存的规则草稿：':dataPending?'已保存规则，待数据核对后执行：':'本次将执行：')+ruleSummary(rulesBase);
  $('business-rules').value=pretty(current.rules||{});
}
function collectRules(){
  const r=clone(rulesBase),weights=[...document.querySelectorAll('[data-weight]')];
  // Send exact percentage text. Decimal conversion and validation run in Python.
  if(weights.length){r.weights_percent={};for(const x of weights)if(x.value!=='')r.weights_percent[x.dataset.weight]=x.value;delete r.weights;delete r.formulas;}
  if($('precision').value==='')delete r.precision;else r.precision=Number($('precision').value);
  if($('rule-cap').value.trim())r.cap=$('rule-cap').value.trim();else delete r.cap;
  if($('excellent-threshold').value.trim()){r.excellent_threshold=$('excellent-threshold').value.trim();r.excellent_denominator=$('excellent-denominator').value;r.summary_field=$('summary-field').value;}else{delete r.excellent_threshold;delete r.excellent_denominator;}
  r.missing||={};
  document.querySelectorAll('[data-policy]').forEach(n=>{const p={action:n.querySelector('[data-policy-action]').value};if(p.action==='value')p.value=n.querySelector('.policy-value input').value;const field=n.dataset.policyField;if(field){r.missing_by_field||={};r.missing_by_field[field]||={};r.missing_by_field[field][n.dataset.policy]=p;}else r.missing[n.dataset.policy]=p;});
  if(typeof collectSemantic==='function')collectSemantic(r);
  r.reason='教师通过中文表单确认本次有效计算规则';return r;
}
function render(state){current=state;localStorage.setItem('two-stage-run',state.id);$('pipeline-id').textContent=`第${state.revision}版 · ${phases[state.phase]||'等待处理'}`;if(!draft.runId||draft.runId!==state.id){try{draft=JSON.parse(localStorage.getItem(storageKey())||'{"cards":{}}');}catch{draft={cards:{}};}draft.runId=state.id;draft.cards||={};}$('pipeline-task').value=state.task;$('understood').hidden=false;$('governance-section').hidden=false;$('rules-section').hidden=false;
  $('effective-summary').innerHTML=(state.view?.governance_summary||[]).map(p=>`<p><strong>${esc(state.files.find(f=>f.id===p.file_id)?.name)}</strong>：${p.items.length?p.items.map(m=>`${esc(m.target)} → ${esc(m.strategy)}；${m.scale?.kind==='points'?'原始满分'+esc(m.scale.max_score):m.scale?.kind==='percent'?'明确百分制':m.scale?.kind==='grade'?'按明确等级对应关系折算':'分制待确认'}`).join(' / '):'提供身份与主表信息'}</p>`).join('')+`<p><strong>当前有效计算规则</strong>：${esc(ruleSummary(state.rules))}</p>`;drawReading();drawGovernance();drawRules();drawCards();
  const d=state.stage1;$('dwd-status').textContent=d?`${d.status==='ready'?'已整理并保存':'整理草稿，仍需核对'} · 第${d.version}版`:'未形成当前版本的有效整理结果';$('dwd-links').hidden=!d;if(d){const c=d.counts;$('dwd-card').className='';$('dwd-card').innerHTML=`<div class="counts">${[['输入表',c.platforms],['原始业务记录',c.input_records],['归并重复提交',c.collapsed_submissions],['自动补齐编号',c.auto_resolved_no_id],['整理后人数',c.wide_rows],['缺项人数',c.missing_students],['主表外待处理',c.unmatched_records]].map(([k,v])=>`<span><strong>${v}</strong>${k}</span>`).join('')}</div>${table(d.columns,d.rows,'dwd-table')}`;$('dwd-links').innerHTML=links([['dwd','下载整理宽表'],['dwd_audit','下载来源与整理明细']]);}else $('dwd-card').innerHTML='<p class="note warning">当前版本整理未完成。上传数据和方案已保留，修正后继续。</p>';
  const s=state.stage2;$('dws-links').hidden=!s;$('dws-status').textContent=s?`第${s.trial_id}次计算 · ${s.status==='complete'?'已完成':'仍有计算问题'}`:'本次尚无有效计算结果';if(s){$('dws-card').className='';$('dws-card').innerHTML=`<p>共${s.summary.rows}人；数值计算${s.summary.calculated}人；保留特殊状态${s.summary.preserved}人；计算待处理${s.summary.failed}人。</p>${table(s.columns,s.rows,'dws-table')}`;$('dws-links').innerHTML=links([['dws','下载当前计算结果CSV'],['audit','下载完整处理明细']]);}else $('dws-card').innerHTML='<p class="note warning">请先解决待确认问题或执行当前计算规则。历史结果不作为本次结果展示。</p>';
  $('trial-comparison').innerHTML=s?.comparison?`<p class="note">与上次相比，${s.comparison.changed_students}人的结果变化；优秀率变化：${esc(s.comparison.excellent_rate_change_percentage_points??'未定义同一统计口径')}。</p>`:'';$('system-section').hidden=state.phase!=='system_error';$('system-message').textContent=state.issues.find(q=>q.kind==='system')?.reason||'';$('pipeline-execution').innerHTML=`<h3>技术诊断（仅供排查）</h3><pre>${esc(pretty({phase:state.phase,diagnostic:state.diagnostic,stage1:d?{directory:d.directory,command:d.command,hash:d.manifest_sha256}:null,stage2:s?{directory:s.directory,command:s.command}:null,events:state.events}))}</pre>`;$('pipeline-suggest').disabled=!modelEnabled;$('plan-preview').hidden=true;planPreview=null;if(typeof drawSemantic==='function')drawSemantic();remember();}
async function encoded(file){return new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',')[1]);r.onerror=reject;r.readAsDataURL(file);});}
$('start-pipeline').onclick=()=>action(async()=>{const files=[...$('pipeline-files').files];if(files.length<2||files.length>6)throw new Error('请选择2至6份CSV文件');if(files.some(f=>!f.name.toLowerCase().endsWith('.csv')))throw new Error('本轮仅支持CSV，其他格式尚未读取');if(files.some(f=>f.size>2*1024*1024))throw new Error('每份文件最多2 MiB');const task=$('pipeline-task').value;if(!task.trim())throw new Error('请先描述你希望如何处理数据');const s=await request('/api/pipelines',{task,files:await Promise.all(files.map(async f=>({name:f.name,content_base64:await encoded(f)})))});draft={cards:{}};render(s);stateMessage();});
function canonical(value){return JSON.stringify(value,(_,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);}
$('resume-pipeline').onclick=()=>action(async()=>{
  const form=collectRules();draft.rules=form;remember();
  const prepared=await request(`/api/pipelines/${current.id}/prepare-rules`,{revision:current.revision,rules:form});
  const state=await request(`/api/pipelines/${current.id}/resume`,{revision:current.revision,rules:prepared.rules});
  if(canonical(state.rules)!==canonical(prepared.rules))throw new Error('本次规则尚未得到保存确认，已保留表单草稿，请重试。');
  delete draft.rules;render(state);stateMessage();
});
function changePath(path,plan){const parts=path.split('.');const p=plan.platforms[Number(parts[1])];const file=current.files.find(f=>f.id===p?.file_id)?.name||'';const names={header_line:'表头所在行',id_column:'编号字段',name_column:'姓名字段',class_column:'班级字段',phone_column:'联系方式字段',strategy:'归并方式',target:'统一项目名称',column:'原字段',timestamp_column:'提交时间字段',max_score:'原始满分',kind:'原始分制',roster_file:'主花名册'};return `${file} ${names[parts.at(-1)]||parts.at(-1)}`;}
$('preview-plan').onclick=()=>action(async()=>{const plan=collectPlan();const preview=await request(`/api/pipelines/${current.id}/preview-plan`,{revision:current.revision,plan});planPreview={plan,...preview};$('plan-preview').hidden=false;const val=v=>typeof v==='string'?(strategies[v]||v):pretty(v);$('plan-preview').innerHTML=preview.changes.length?`<h3>即将变更${preview.changes.length}项</h3><ul>${preview.changes.map(c=>`<li>${esc(changePath(c.path,plan))}：${esc(val(c.before))} → ${esc(val(c.after))}</li>`).join('')}</ul><p>${esc(preview.impact)}</p><button class="primary" id="apply-plan">确认重新处理</button>`:'<p>没有实际变更，无需重新生成整理宽表。</p>';if($('apply-plan'))$('apply-plan').onclick=()=>action(async()=>{if(!planPreview||pretty(collectPlan())!==pretty(planPreview.plan))throw new Error('方案已变化，请重新查看变更摘要');const s=await request(`/api/pipelines/${current.id}/apply-plan`,{revision:current.revision,plan:planPreview.plan,confirmation:planPreview.token,reason:$('plan-reason').value});delete draft.plan;delete draft.planReason;delete draft.layouts;render(s);stateMessage();});});
$('import-plan').onclick=()=>action(async()=>{try{const p=JSON.parse($('governance-plan').value);await request(`/api/pipelines/${current.id}/preview-plan`,{revision:current.revision,plan:p});if(draft.plan&&pretty(collectPlan())!==pretty(current.plan)&&pretty(collectPlan())!==pretty(p))throw new Error('JSON与当前表单草稿不一致，请先核对，不能静默覆盖表单');draft.plan=p;remember();drawGovernance();note('JSON已通过字段检查并同步表单；请查看变更摘要后确认。');}catch(e){$('advanced-error').textContent=e.message;throw e;}});
$('import-rules').onclick=()=>action(async()=>{const r=JSON.parse($('business-rules').value);if(draft.rules&&pretty(draft.rules)!==pretty(r))throw new Error('JSON与规则表单草稿不一致，请核对后同步');await request(`/api/pipelines/${current.id}/check-rules`,{revision:current.revision,rules:r});draft.rules=r;remember();drawRules();note('复杂规则已校验并同步草稿，请点击“按上述规则计算”。');});
$('retry-pipeline').onclick=()=>action(async()=>{if(current.phase==='understanding_error'){const instruction=draft.instruction||current.instructions.at(-1).text;render(await request(`/api/pipelines/${current.id}/instruct`,{revision:current.revision,instruction,mode:current.instructions.at(-1).mode,request_id:crypto.randomUUID()}));}else render(await request(`/api/pipelines/${current.id}/resume`,{revision:current.revision,rules:current.rules}));stateMessage();});
$('pipeline-suggest').onclick=()=>action(async()=>{const s=await request(`/api/pipelines/${current.id}/suggest`,{revision:current.revision});render(s);$('pipeline-suggestion').textContent=pretty(s.suggestion);});
(async()=>{await semanticReady;try{const cfg=await request('/api/config');modelEnabled=cfg.model.enabled;$('model-state').textContent=cfg.model.label;if(typeof showModelCheck==='function')showModelCheck(cfg.last_model_check);const id=localStorage.getItem('two-stage-run');if(id){render(await request('/api/pipelines/'+id));note('已恢复上传、处理状态与草稿，无需重新上传。');}}catch(e){note(e.message,true);}finally{$('pipeline-task').disabled=false;$('pipeline-files').disabled=false;$('start-pipeline').disabled=false;}})();
