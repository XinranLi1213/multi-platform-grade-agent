// Forms edit the same typed rules sent to Python; no browser grade arithmetic.
const operations={direct:'直接采用',mean:'平均',sum:'累加',max:'最高分',best:'最佳N项',weighted:'内部加权'};
const adjustmentActions={add:'加分',multiply:'乘系数',set:'设为指定值',floor:'提高至最低值'};
const sourceMetrics=()=>[...new Set(current.plan.platforms.flatMap(p=>p.metrics.map(m=>m.target)))];
function activityFilterHTML(item,headers,rows){
  const field=Object.keys(item.where||{})[0]||'',value=item.where?.[field]||'';
  const values=field?[...new Set(rows.map(r=>r.values[field]))]:[];
  return `<div class="activity-filter"><label>仅整理某一业务活动（可选）<select data-filter-column>${opts(headers,field,'不筛选活动')}</select></label><label>实际活动原值<select data-filter-value>${opts(values,value,'请选择实际值')}</select></label></div>`;
}
function bindActivityFilters(){
  document.querySelectorAll('.file-form').forEach(node=>node.querySelectorAll('[data-filter-column]').forEach(select=>select.onchange=()=>{
    const field=select.value,rows=layoutDrafts[node.dataset.file]?.rows||[];
    select.closest('.activity-filter').querySelector('[data-filter-value]').innerHTML=opts(field?[...new Set(rows.map(r=>r.values[field]))]:[],'','请选择实际值');
    draft.plan=collectPlan();remember();
  }));
}
function projectEditor(item,index,items){
  const sources=[...sourceMetrics().map(s=>[s,s]),...items.filter(i=>i.id!==item.id&&i.name).map(i=>[i.id,i.name+'（考核项目）'])];
  return `<fieldset class="assessment-item" data-index="${index}"><legend>考核项目 ${index+1}</legend><div class="form-grid"><label>项目名称<input data-item="name" value="${esc(item.name)}"></label><label>组成方式<select data-item="operation">${opts([],null,'请选择')}${enumOpts(operations,item.operation)}</select></label><label>实际来源（按住Ctrl多选）<select data-item="sources" multiple size="4">${sources.map(([id,label])=>`<option value="${esc(id)}" ${(item.sources||[]).includes(id)?'selected':''}>${esc(label)}</option>`).join('')}</select></label><label class="best-setting" ${item.operation==='best'?'':'hidden'}>最佳项数N<input data-item="n" type="number" value="${esc(item.n??'')}"></label><label class="best-setting" ${item.operation==='best'?'':'hidden'}>最佳项如何组合<select data-item="reduce">${opts([],null,'请选择')}${enumOpts({mean:'平均',sum:'累加'},item.reduce)}</select></label><label class="internal-setting" ${item.operation==='weighted'?'':'hidden'}>内部权重（按所选来源顺序填写百分比，以逗号分隔）<input data-item="weights_percent" value="${esc(item.weights_percent?.join(',')??item.weights?.map(percentDisplay).join(',')??'')}"></label><label>合成满分（平均等默认沿用百分制；累加必须填写）<input data-item="max_score" value="${esc(item.max_score??'')}"></label><label>项目封顶（可选，合成原单位）<input data-item="cap" value="${esc(item.cap??'')}"></label></div><label>组成依据<input data-item="basis" value="${esc(item.basis??'')}" placeholder="例如：课程考核办法规定两次课堂展示平均"></label><button class="remove-item" type="button">移除此项目</button></fieldset>`;
}
function adjustmentEditor(item,index){
  const targets=[...sourceMetrics(),...(rulesBase.assessment_items||[]).map(i=>i.name).filter(Boolean)];
  const fields=[...targets,...(current.stage2?.compiled_formulas||[]).map(f=>f.name),rulesBase.output||'总评'];
  const ids=!!item.scope?.ids;
  return `<fieldset class="adjustment-item" data-index="${index}"><legend>局部调整 ${index+1}</legend><div class="form-grid"><label>判断基准<select data-adjust="baseline"><option value="before_adjustment" ${item.baseline==='before_adjustment'?'selected':''}>本次调整前结果（固定范围）</option>${(current.trials||[]).map(t=>`<option value="${t.trial_id}" ${item.baseline===t.trial_id?'selected':''}>第${t.trial_id}次计算</option>`).join('')}</select></label><label>适用范围<select data-adjust="scope_mode">${enumOpts({condition:'按调整前数值筛选',ids:'明确指定编号'},ids?'ids':'condition')}</select></label><label class="scope-ids" ${ids?'':'hidden'}>编号（逗号分隔，保留前导零）<input data-adjust="ids" value="${esc(item.scope?.ids?.join(',')||'')}"></label><label class="scope-condition" ${ids?'hidden':''}>筛选字段<select data-adjust="field">${opts([...new Set(fields)],item.scope?.field,'请选择实际项目')}</select></label><label class="scope-condition" ${ids?'hidden':''}>条件<select data-adjust="op">${enumOpts({lt:'低于',le:'不高于',eq:'等于',ge:'不低于',gt:'高于'},item.scope?.op)}</select></label><label class="scope-condition" ${ids?'hidden':''}>阈值<input data-adjust="threshold" value="${esc(item.scope?.value??rulesBase.pass_threshold??'')}"></label><label>调整项目<select data-adjust="target">${opts(targets,item.target,'请选择')}</select></label><label>调整动作<select data-adjust="action">${opts([],null,'请选择')}${enumOpts(adjustmentActions,item.action)}</select></label><label>数值（百分制项目；系数按原值填写）<input data-adjust="value" value="${esc(item.value??'')}"></label><label>调整后封顶（可选）<input data-adjust="cap" value="${esc(item.cap??'')}"></label><label>特殊状态<select data-adjust="special">${opts([],null,'请选择处理方式')}${enumOpts({preserve:'保留原特殊状态，不调整',hold:'暂停并补充依据'},item.special)}</select></label><label>执行顺序（范围重叠时必填且不同）<input data-adjust="order" type="number" value="${esc(item.order??'')}"></label></div><label>调整依据<input data-adjust="basis" value="${esc(item.basis??'')}" placeholder="引用教师指令、课程办法或明确裁决"></label><p class="hint">其他项目及权重保持本次方案；原始成绩不覆盖。范围绑定判断版本，重复提交不累计。</p><button type="button" class="remove-adjustment">移除此调整</button></fieldset>`;
}
function collectSemantic(r){
  if(!$('project-form').dataset.initialized)return;
  r.assessment_items=[...document.querySelectorAll('.assessment-item')].map(node=>{
    const item=clone(rulesBase.assessment_items[Number(node.dataset.index)]);
    node.querySelectorAll('[data-item]').forEach(input=>{
      const k=input.dataset.item;
      if(k==='sources')item.sources=[...input.selectedOptions].map(o=>o.value);
      else if(k==='n'){if(input.value)item.n=Number(input.value);else delete item.n;}
      else if(k==='weights_percent'){if(input.value.trim()){item.weights_percent=input.value.split(/[,，]/).map(v=>v.trim());delete item.weights;}else{delete item.weights;delete item.weights_percent;}}
      else if(['cap','max_score'].includes(k)){if(input.value.trim())item[k]=input.value.trim();else delete item[k];}
      else item[k]=input.value;
    });
    if(item.operation!=='weighted'){delete item.weights;delete item.weights_percent;}
    return item;
  });
  r.adjustments=[...document.querySelectorAll('.adjustment-item')].map(node=>{
    const item=clone(rulesBase.adjustments[Number(node.dataset.index)]),v={};
    node.querySelectorAll('[data-adjust]').forEach(input=>v[input.dataset.adjust]=input.value);
    item.baseline=v.baseline==='before_adjustment'?v.baseline:Number(v.baseline);
    item.scope=v.scope_mode==='ids'?{ids:v.ids.split(/[,，\s]+/).filter(Boolean)}:{field:v.field,op:v.op,value:v.threshold};
    for(const k of ['target','action','value','special','basis'])item[k]=v[k];
    for(const k of ['cap','order']){if(v[k].trim())item[k]=k==='order'?Number(v[k]):v[k];else delete item[k];}
    item.preserve='其他项目及权重保持本次方案';return item;
  });
  if(!r.assessment_items.length)delete r.assessment_items;
  if(!r.adjustments.length)delete r.adjustments;
}
function drawProjectForms(){
  const items=rulesBase.assessment_items||[];
  $('project-form').innerHTML=`<h3>考核项目与实际来源</h3><p class="hint">多个测量项可组成一个项目；这里的组合不会删除阶段一记录。</p>${items.map((i,n)=>projectEditor(i,n,items)).join('')}<button id="add-assessment" type="button">添加自定义考核项目</button>`;
  $('project-form').dataset.initialized='true';
  $('adjustment-form').innerHTML=`<h3>明确范围的局部调整</h3>${(rulesBase.adjustments||[]).map(adjustmentEditor).join('')}<button id="add-adjustment" type="button">添加局部调整</button>`;
  function saveDraft(){draft.rules=collectRules();remember();$('rule-preview').textContent='尚未执行的草稿：'+ruleSummary(draft.rules);}
  for(const id of ['project-form','adjustment-form'])$(id).oninput=saveDraft;
  $('add-assessment').onclick=()=>{saveDraft();draft.rules.assessment_items||=[];draft.rules.assessment_items.push({id:'item-'+crypto.randomUUID(),name:'',operation:'',sources:[],basis:''});refresh();};
  $('add-adjustment').onclick=()=>{saveDraft();draft.rules.adjustments||=[];draft.rules.adjustments.push({id:'adjust-'+crypto.randomUUID(),baseline:'before_adjustment',scope:{field:rulesBase.summary_field||'总评',op:'lt',value:rulesBase.pass_threshold||''},target:'',action:'',value:'',special:'',basis:''});refresh();};
  function refresh(){remember();drawRules();drawProjectForms();}
  document.querySelectorAll('.assessment-item').forEach(node=>{
    node.querySelector('.remove-item').onclick=()=>{saveDraft();draft.rules.assessment_items.splice(Number(node.dataset.index),1);refresh();};
    node.querySelector('[data-item="operation"]').onchange=()=>{saveDraft();refresh();};
    node.querySelector('[data-item="name"]').onchange=()=>{saveDraft();refresh();};
  });
  document.querySelectorAll('.adjustment-item').forEach(node=>{
    node.querySelector('.remove-adjustment').onclick=()=>{saveDraft();draft.rules.adjustments.splice(Number(node.dataset.index),1);refresh();};
    node.querySelector('[data-adjust="scope_mode"]').onchange=()=>{const ids=node.querySelector('[data-adjust="scope_mode"]').value==='ids';node.querySelectorAll('.scope-ids').forEach(n=>n.hidden=!ids);node.querySelectorAll('.scope-condition').forEach(n=>n.hidden=ids);saveDraft();};
  });
}
function drawSemantic(){
  $('send-instruction').hidden=false;$('start-pipeline').hidden=true;$('composer-upload').hidden=true;$('try-instruction').hidden=current.calculation_requested===false;$('instruction-history').hidden=false;
  $('instruction-list').innerHTML=(current.instructions||[{text:current.task}]).map(i=>`<p><strong>${i.mode==='trial'?'试算指令':'任务指令'}</strong>：${esc(i.text)}</p>`).join('');
  if(window.PipelineWorkspace)PipelineWorkspace.conversation(current);
  $('pipeline-task').value=draft.instruction||'';
  $('pipeline-task').placeholder='补充问题答案，或说明下一步要求；需要计分时，请说明项目与规则。';
  $('composer-title').textContent='你希望如何处理这些数据？';
  $('instruction-help').textContent=modelEnabled?'':'语义理解未启用，可展开“修改理解”补充规则。';
  $('pipeline-task').oninput=()=>{draft.instruction=$('pipeline-task').value;delete draft.instructionRequest;remember();};
  const rule=current.business_draft||current.rules||{},ops=operations;
  $('semantic-summary').innerHTML=(rule.assessment_items||[]).map(i=>`<p><strong>${esc(i.name)}</strong>：${esc(ops[i.operation])}，来源 ${esc((i.sources||[]).map(s=>rule.assessment_items.find(p=>p.id===s)?.name||s).join('、'))}${i.n?'，最佳'+esc(i.n)+'项':''}；依据：${esc(i.basis)}</p>`).join('')+(rule.adjustments||[]).map(a=>`<p><strong>局部调整</strong>：${esc(a.scope.ids?'编号 '+a.scope.ids.join('、'):'基准 '+a.scope.field+' '+({lt:'低于',le:'不高于',eq:'等于',ge:'不低于',gt:'高于'}[a.scope.op]||a.scope.op)+' '+a.scope.value)}；${esc(a.target)}${esc(adjustmentActions[a.action])}${esc(a.value)}；判断版本：${esc(a.baseline==='before_adjustment'?'本次调整前（执行时固定）':a.baseline)}；特殊状态${a.special==='preserve'?'保留':'待补充'}；依据：${esc(a.basis)}</p>`).join('');
  drawProjectForms();
  bindActivityFilters();
  if(current.business_draft)$('semantic-summary').insertAdjacentHTML('afterbegin','<p class="note warning">理解草稿 · 待补充，尚未执行</p>');
  if(current.phase==='understanding_error'){$('system-section').hidden=false;$('system-section').querySelector('h2').textContent='本次语义理解未完成';$('system-message').textContent=current.issues.map(q=>q.reason).join('；');$('retry-pipeline').textContent='重试理解';}else{$('system-section').querySelector('h2').textContent='本次执行未完成';$('retry-pipeline').textContent='重试当前方案';}
  const result=current.stage2,accepted=current.accepted_result;
  $('accepted-summary').innerHTML=accepted?`<p class="note">已采用：第${accepted.trial_id}次计算。<a id="download-accepted" href="/api/pipelines/${current.id}/download?kind=accepted">下载已采用结果</a>${result?.mode==='trial'?'；下方为独立试算，尚未替换已采用结果。':''}</p>`:'';
  $('adopt-trial').hidden=!(result?.mode==='trial'&&result.status==='complete'&&accepted?.trial_id!==result.trial_id);
  if(result?.mode==='trial')$('dws-status').textContent=`试算 · 第${result.trial_id}次计算 · ${result.status==='complete'?'已完成':'待处理'}`;
  const changes=result?.comparison?.changes||[];
  if(changes.length)$('trial-comparison').insertAdjacentHTML('beforeend',`<details open><summary>调整前后差异（${changes.length}人）</summary>${table(['student_id','变化'],changes.map(c=>({student_id:c.student_id,变化:Object.entries(c.fields).map(([k,v])=>`${k}：${v.before??'空缺'} → ${v.after??'空缺'}`).join('；')})),'difference-table')}</details>`);
  const scope=result?.adjustment_scope||[],audit=result?.adjustment_changes||[];
  $('adjustment-results').innerHTML=scope.length?`<h3>固定命中范围与调整明细</h3>${scope.map(s=>`<p>规则 ${esc(s.rule_id)}：${s.student_ids.length}人；编号 ${esc(s.student_ids.join('、')||'无')}</p>`).join('')}${table(['student_id','target','before','after','basis'],audit,'adjustment-table')}`:'';
  if(result?.adjustment_differences?.length)$('adjustment-results').insertAdjacentHTML('beforeend',`<h3>本次局部调整前后变化</h3>${table(['student_id','变化'],result.adjustment_differences.map(c=>({student_id:c.student_id,变化:Object.entries(c.fields).map(([k,v])=>`${k}：${v.before} → ${v.after}`).join('；')})),'adjustment-difference-table')}`);
}
async function submitInstruction(mode){
  const instruction=$('pipeline-task').value.trim();if(!instruction)throw new Error('请填写本次追加指令');
  draft.instruction=instruction;if(draft.instructionRequestMode!==mode){delete draft.instructionRequest;draft.instructionRequestMode=mode;}draft.instructionRequest||=crypto.randomUUID();remember();
  const state=await request(`/api/pipelines/${current.id}/instruct`,{revision:current.revision,instruction,mode,request_id:draft.instructionRequest});
  if(['complete','organized'].includes(state.phase)){delete draft.instruction;delete draft.instructionRequest;delete draft.rules;}
  const stream=document.getElementById('conversation-stream'),position=stream.scrollTop,follow=stream.scrollHeight-stream.clientHeight-position<80;render(state);stateMessage();if(follow)document.querySelector('.conversation-turn:last-child')?.scrollIntoView({block:'end'});else stream.scrollTop=position;
}
$('send-instruction').onclick=()=>action(()=>submitInstruction('execute'));
$('try-instruction').onclick=()=>action(()=>submitInstruction('trial'));
$('adopt-trial').onclick=()=>action(async()=>{render(await request(`/api/pipelines/${current.id}/adopt-trial`,{revision:current.revision}));stateMessage();});
function showModelCheck(check){
  if(!check)return;
  const flag=v=>v===null||v===undefined?'未验证':v?'通过':'未通过';
  $('model-check-result').textContent=`配置：${flag(check.configuration_valid)}；连接：${flag(check.transport_ok)}；协议：${flag(check.protocol_ok)}；业务方案契约：${flag(check.plan_contract_ok)}。${check.message}`;
}
$('check-model').onclick=()=>action(async()=>{const check=await request('/api/model/check',{});showModelCheck(check);note(check.message,check.status==='ok'?false:'warning');});
$('manual-fallback').onclick=()=>{if(current){const section=current.calculation_requested===false?'governance-section':'rules-section';if(section==='rules-section')$('optional-business').open=true;$(section).open=true;PipelineWorkspace.inspect(section==='rules-section'?'optional-business':'dwd','修改理解');}else note('请先描述任务并上传CSV；读取后可使用中文表单。');};
window.dispatchEvent(new Event('pipeline-semantic-ready'));
