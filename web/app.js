const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"'\r\n\t]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;','\r':'&#13;','\n':'&#10;','\t':'&#9;'}[c]));
let session = null, config = null, dirty = false, invalidating = Promise.resolve();

async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
  return result;
}
function message(text, error=false) { $('message').hidden=false; $('message').textContent=text; $('message').className=error?'error':''; }
function table(columns, rows, id='', limit=50) {
  return `<div class="table-wrap"><table ${id?`id="${id}"`:''}><thead><tr>${columns.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${rows.slice(0,limit).map(r=>`<tr>${columns.map(c=>`<td data-raw="${esc(r[c] ?? '')}">${r[c] === '' ? '<span class="null" title="原值为空字符串">∅</span>' : esc(r[c] ?? '')}</td>`).join('')}</tr>`).join('')}</tbody></table></div><p class="hint">预览 ${Math.min(rows.length,limit)} / ${rows.length} 行；空字符串以 ∅ 表示，0 和前导零按原文本展示。</p>`;
}
function clearConfirmations() { document.querySelectorAll('#checks input').forEach(x=>x.checked=false); }
function route(action) { return `/api/sessions/${session.id}/${action}`; }
function markDirty() {
  if (!session) return;
  if (!dirty && session.result) {
    const editingSession=session.id;
    invalidating = api(route('invalidate'), {revision:session.revision}).then(updated=>{
      if(session.id===editingSession){session.revision=updated.revision; session.result=null; renderResult();}
    }).catch(e=>{ message('旧结果失效请求失败：'+e.message,true); throw e; });
  }
  dirty=true; clearConfirmations(); $('execute-btn').disabled=true;
  $('plan-state').textContent='方案已修改；旧结果已停止展示，请保存并重新检查';
  $('downloads').hidden=true; $('result-content').innerHTML='<div class="note warning">方案或任务已修改，相关旧结果已失效。请保存方案并重新确认执行。</div>';
}
function renderReading() {
  $('read-content').className='';
  $('read-content').innerHTML=session.files.map(f=>{
    const labels={ok:'完整读取',partial:'部分读取 · 禁止执行',failed:'读取失败 · 禁止执行'};
    const profiles=f.headers.map(h=>({字段:h,空值:f.profile.columns?.[h]?.missing ?? '',不同非空值:f.profile.columns?.[h]?.distinct ?? '',含首尾空格:f.profile.columns?.[h]?.surrounding_whitespace ?? '',含控制字符:f.profile.columns?.[h]?.control_characters ?? ''}));
    let sourceColumn='来源记录'; while(f.headers.includes(sourceColumn))sourceColumn='_'+sourceColumn;
    const previews=f.rows.map(r=>({[sourceColumn]:`${r.id} · 第${r.line}${r.end_line!==r.line?'—'+r.end_line:''}行`,...r.values}));
    return `<article class="file-report" data-file="${f.id}"><div class="file-title"><strong>${f.id} · ${esc(f.name)}</strong><span class="badge ${f.status==='ok'?'':'bad'}">${labels[f.status]}</span></div><p class="metadata" data-row-count="${f.row_count}">实际有效记录 ${f.row_count} 条 · 已知坏记录 ${f.bad_record_count} 条${f.unread_remainder?'（后续边界/总数未确定，读取不完整）':''} · ${f.headers.length} 列 · 编码 ${esc(f.encoding||'未解码')} · 分隔符 ${esc(f.delimiter==='\t'?'制表符':f.delimiter||'未知')}</p><p class="metadata">列名：${f.headers.map(esc).join('、')||'无可用列名'}</p>${f.repairs.map(r=>`<div class="note warning">技术性读取方式：${esc(r)}</div>`).join('')}${f.errors.map(e=>`<div class="note error">${esc(e.reason)}；影响：${esc(e.scope)} ${e.line?'· 第'+esc(e.line)+'行':''}${e.values?'<pre>'+esc(JSON.stringify(e.values))+'</pre>':''}</div>`).join('')}${f.blank_lines.length?`<p class="hint">明确识别的空白物理行：${f.blank_lines.join(', ')}；这些行无任何字段，未计为数据记录。</p>`:''}${table([sourceColumn,...f.headers],previews,'preview-'+f.id)}<details><summary>字段检查与完全重复记录（尚未删除）</summary>${table(['字段','空值','不同非空值','含首尾空格','含控制字符'],profiles)}<p>完全重复冗余副本：${f.profile.redundant_copies||0}</p><pre>${esc(JSON.stringify(f.profile.exact_duplicate_groups||[],null,2))}</pre></details><a href="/api/sessions/${session.id}/original/${f.id}">下载保留的原始输入</a></article>`;
  }).join('');
}
function renderMapping() {
  const rows=[];
  for (const f of session.files) for (const h of f.headers) {
    const entry=session.plan.mapping[f.id]?.[h] || {target:h,transform:'identity'};
    rows.push(`<tr><td>${f.id} · ${esc(f.name)}</td><td>${esc(h)}</td><td><input aria-label="${esc(f.id+' '+h+' 统一字段')}" data-map-file="${f.id}" data-map-column="${esc(h)}" value="${esc(entry.target)}"></td><td><select aria-label="${esc(f.id+' '+h+' 格式转换')}" data-transform-file="${f.id}" data-transform-column="${esc(h)}">${[['identity','保持原值'],['strip','去首尾空格（需确认）'],['casefold','大小写归一（需确认）']].map(([v,t])=>`<option value="${v}" ${entry.transform===v?'selected':''}>${t}</option>`).join('')}</select></td><td>待你确认；所有原字段保留</td></tr>`);
  }
  $('mapping').innerHTML=`<div class="table-wrap"><table><thead><tr><th>文件</th><th>原字段</th><th>统一字段</th><th>转换</th><th>判断状态</th></tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
  document.querySelectorAll('#mapping input').forEach(x=>x.addEventListener('input',()=>{markDirty();refreshKeys();}));
  document.querySelectorAll('#mapping select').forEach(x=>x.addEventListener('change',markDirty));
}
function refreshKeys(selected) {
  const current=selected || Array.from($('keys').selectedOptions).map(x=>x.value);
  const a=Array.from(document.querySelectorAll('[data-map-file="A"]')).map(x=>x.value),b=new Set(Array.from(document.querySelectorAll('[data-map-file="B"]')).map(x=>x.value));
  const common=Array.from(new Set(a.filter(x=>b.has(x))));
  $('keys').innerHTML=common.map(x=>`<option value="${esc(x)}" ${current.includes(x)?'selected':''}>${esc(x)}</option>`).join('');
}
function collectPlan() {
  const mapping={A:{},B:{}};
  document.querySelectorAll('[data-map-file]').forEach(x=>{
    const transform=Array.from(document.querySelectorAll('[data-transform-file]')).find(t=>t.dataset.transformFile===x.dataset.mapFile&&t.dataset.transformColumn===x.dataset.mapColumn);
    mapping[x.dataset.mapFile][x.dataset.mapColumn]={target:x.value,transform:transform.value};
  });
  return {row_meaning:$('row-meaning').value,mode:$('mode').value,key_fields:Array.from($('keys').selectedOptions).map(x=>x.value),link_basis:$('link-basis').value,dedupe:$('dedupe').value,dedupe_basis:$('dedupe-basis').value,mapping};
}
function renderPlan() {
  $('plan-editor').hidden=false; renderMapping();
  $('row-meaning').value=session.plan.row_meaning||''; $('mode').value=session.plan.mode;
  $('link-basis').value=session.plan.link_basis||''; $('dedupe').value=session.plan.dedupe;
  $('dedupe-basis').value=session.plan.dedupe_basis||''; refreshKeys(session.plan.key_fields);
  $('join-options').hidden=session.plan.mode!=='join';
  $('plan-state').textContent=`已保存方案 v${session.revision}；修改后需重新检查`;
}
function renderAnalysis() {
  const a=session.analysis;
  $('questions').innerHTML='<strong>需要你明确的问题</strong><ol>'+a.questions.map(q=>'<li>'+esc(q)+'</li>').join('')+'</ol>';
  $('candidates').innerHTML=a.value_candidates.length?a.value_candidates.map((c,i)=>`<div class="note"><strong>A.${esc(c.a)} ↔ B.${esc(c.b)}</strong><p>${esc(c.evidence)}</p><button class="small candidate-map" data-index="${i}">将此对应填入草稿，稍后确认</button></div>`).join(''):'<p class="hint">没有原值重合证据；请手动映射，或配置真实模型辅助判断。</p>';
  document.querySelectorAll('.candidate-map').forEach(btn=>btn.addEventListener('click',()=>{
    const c=a.value_candidates[Number(btn.dataset.index)];
    const input=Array.from(document.querySelectorAll('[data-map-file="B"]')).find(x=>x.dataset.mapColumn===c.b);
    const source=Array.from(document.querySelectorAll('[data-map-file="A"]')).find(x=>x.dataset.mapColumn===c.a);
    input.value=source.value; markDirty();refreshKeys();message('已填入映射草稿；这不代表已确认同义，请继续核对。');
  }));
  $('key-candidates').innerHTML=a.key_candidates.map(k=>`<details><summary>${esc(k.fields.join(' + '))} · 统计候选，待确认</summary><pre>${esc(JSON.stringify(k.statistics,null,2))}</pre><p class="hint">${esc(k.basis)}</p></details>`).join('')||'<p class="hint">保存可用映射后显示；候选仅统计证据。</p>';
  $('analysis').innerHTML=a.errors.map(e=>`<div class="note error">执行条件未满足：${esc(e)}</div>`).join('')+(Object.keys(a.key_statistics).length?'<h3>当前关联键检查（按拟定去重后的数据）</h3><pre>'+esc(JSON.stringify(a.key_statistics,null,2))+'</pre>':'')+a.problems.map(p=>`<details class="problem"><summary>${esc(p.reason)} · 键 ${esc(JSON.stringify(p.key))}</summary><pre>${esc(JSON.stringify(p,null,2))}</pre></details>`).join('');
  const p=session.plan, counts=a.projected_counts;
  if(a.proposed_actions)$('analysis').innerHTML+='<details><summary>拟执行操作：建议删除的副本、建议关联的记录及需保留的记录（尚未执行）</summary><pre>'+esc(JSON.stringify(a.proposed_actions,null,2))+'</pre></details>';
  $('plan-summary').className='note'; $('plan-summary').innerHTML=`<strong>方案 v${session.revision}</strong><p>每行代表：${esc(p.row_meaning||'待明确')}</p><p>字段归一：采用上方全部映射。关联：${p.mode==='join'?'按 '+esc(p.key_fields.join(' + '))+' 做全外关联':'保留独立记录'}。去重：${p.dedupe==='exact'?'仅文件内全部原值一致的冗余副本':'全部保留'}。</p><p>冲突、缺键及一对多部分独立保留为待确认；其他可处理记录继续。</p>${counts?`<p>预计：输入 ${counts.input_records} 条 − 冗余副本 ${counts.deleted_copies} 条 − 成功关联 ${counts.merged_pairs} 对 ＝ 输出 ${counts.output_rows} 行，其中待确认 ${counts.pending_output_rows} 行。</p>`:''}`;
  document.querySelectorAll('#checks label').forEach(label=>{ label.hidden=!a.required_confirmations.includes(label.querySelector('input').id.replace('confirm-','')); });
  $('execute-btn').disabled=dirty||a.errors.length>0;
}
function renderAdvice() {
  const suggestion=session.suggestion;
  $('model-advice').innerHTML=!suggestion?'':suggestion.status==='ok'?`<details open><summary>真实模型建议 · 尚未执行</summary><pre>${esc(JSON.stringify(suggestion.advice,null,2))}</pre><button id="apply-advice">填入方案草稿，仍需核对确认</button></details>`:`<div class="note warning">${esc(suggestion.message)}</div>`;
  if ($('apply-advice')) $('apply-advice').onclick=()=>{
    for (const item of suggestion.advice.mappings) {
      const input=Array.from(document.querySelectorAll('[data-map-file]')).find(x=>x.dataset.mapFile===item.file&&x.dataset.mapColumn===item.column);
      input.value=item.target;
    }
    if (suggestion.advice.row_meaning) $('row-meaning').value=suggestion.advice.row_meaning;
    refreshKeys(suggestion.advice.key_fields); markDirty(); message('模型建议已填入草稿。请核对关联方式、依据和重复处理后保存检查。');
  };
}
function renderResult() {
  const r=session.result;
  $('downloads').hidden=!r; $('result-revision').textContent=r?`执行版本 v${r.revision}`:'';
  if(!r) { $('result-content').innerHTML='<p class="empty">尚无有效结果。请保存方案、确认并执行。</p>'; return; }
  const c=r.counts;
  $('result-content').className='';
  $('result-content').innerHTML=`<div class="counts">${[['输入记录',c.input_records],['删除冗余副本',c.deleted_copies],['成功关联对',c.merged_pairs],['输出行',c.output_rows],['待确认行',c.pending_output_rows],['来源覆盖',c.source_records_covered]].map(([label,n])=>`<span><strong>${n}</strong>${label}</span>`).join('')}</div><p class="hint">数量解释：${c.input_records} − ${c.deleted_copies} − ${c.merged_pairs} ＝ ${c.output_rows}。全部 ${c.source_records_covered} 条原始记录均有来源，删除的仅是冗余副本，来源没有删除。</p><h3>整合结果（与CSV下载共用同一份数据）</h3>${table(r.export_columns,r.export_rows,'result-table')}<h3>待确认或未处理记录</h3>${table(r.export_columns,r.export_rows.filter(x=>x.__状态==='待确认'),'pending-table')}<details><summary>处理明细：映射、去重、转换、关联、冲突与逐字段来源</summary><pre>${esc(JSON.stringify({plan:r.audit.plan,deduplications:r.audit.deduplications,conversions:r.audit.conversions,merged:r.audit.merged,problems:r.problems,field_sources:r.records.map(x=>({record:x.id,sources:x.field_sources}))},null,2))}</pre></details>`;
  $('downloads').innerHTML=[['result','下载整合结果 CSV'],['pending','下载待确认 CSV'],['audit','下载处理明细 JSON']].map(([kind,label])=>`<a class="download" id="download-${kind}" href="${route('download')}?kind=${kind}">${label}</a>`).join('');
}
function adopt(state) {
  session=state; dirty=false; clearConfirmations(); localStorage.setItem('integration-session',state.id);
  $('task').value=state.task; $('session-label').textContent=`会话 ${state.id} · v${state.revision}`;
  $('suggest-btn').disabled=!config?.model.enabled||state.files.some(f=>f.status!=='ok');
  renderReading();renderPlan();renderAnalysis();renderAdvice();renderResult();
}
async function guarded(button, fn) {
  button.disabled=true;
  try { await fn(); } catch(e) { message(e.message,true); }
  finally { button.disabled=false; if(session) $('execute-btn').disabled=dirty||session.analysis.errors.length>0; }
}
async function encoded(file) {
  return new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});
}
$('upload-btn').onclick=()=>guarded($('upload-btn'),async()=>{
  const a=$('file-a').files[0],b=$('file-b').files[0];
  if(!a||!b)throw new Error('请选择两个文件');
  if(a.size>2*1024*1024||b.size>2*1024*1024)throw new Error('单文件不能超过2 MiB');
  adopt(await api('/api/sessions',{task:$('task').value,files:[{name:a.name,content_base64:await encoded(a)},{name:b.name,content_base64:await encoded(b)}]}));
  message('文件已实际读取；请先核对预览与异常，再编辑方案。');$('reading').scrollIntoView({behavior:'smooth'});
});
$('save-plan').onclick=()=>guarded($('save-plan'),async()=>{await invalidating;adopt(await api(route('plan'),{revision:session.revision,task:$('task').value,plan:collectPlan()}));message('方案已保存并校验，请核对执行摘要与相关记录。');});
$('suggest-btn').onclick=()=>guarded($('suggest-btn'),async()=>{
  if(dirty)throw new Error('请先保存当前任务和方案');
  const updated=await api(route('suggest'),{revision:session.revision});session.suggestion=updated.suggestion;renderAdvice();
});
$('execute-btn').onclick=()=>guarded($('execute-btn'),async()=>{
  if(dirty)throw new Error('请先保存修改');
  const confirmations={};document.querySelectorAll('#checks input').forEach(x=>confirmations[x.id.replace('confirm-','')]=x.checked);
  adopt(await api(route('execute'),{revision:session.revision,confirmations}));message('执行完成，未解决部分已保留，可修改方案后继续。');$('results').scrollIntoView({behavior:'smooth'});
});
['task','row-meaning','link-basis','dedupe-basis'].forEach(id=>$(id).addEventListener('input',markDirty));
['keys','mode','dedupe'].forEach(id=>$(id).addEventListener('change',()=>{markDirty();$('join-options').hidden=$('mode').value!=='join';}));
(async()=>{
  try { config=await api('/api/config');$('model-status').textContent=config.model.label;$('model-disclosure').textContent=config.model.disclosure;
    const sid=localStorage.getItem('integration-session');if(sid){try{adopt(await api('/api/sessions/'+sid));message('已恢复本机保存的会话，无需重新上传。');}catch(e){localStorage.removeItem('integration-session');}}
  }catch(e){message('无法连接本地服务：'+e.message,true);}
})();
