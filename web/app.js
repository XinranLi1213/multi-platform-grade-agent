// Shared task context: server uploads and newly selected local files are distinct.
window.TaskContext={
  options:null,state:null,restored:false,
  read(key,fallback){try{return JSON.parse(localStorage.getItem(key)||'null')??fallback;}catch{return fallback;}},
  write(key,value){localStorage.setItem(key,JSON.stringify(value));},
  setup(options){
    this.options=options;this.prefix='teacher-context:'+options.mode+':';
    if(options.mode==='pipeline'){PipelineWorkspace.setup(this);return;}
    const target=document.createElement('section');target.className='task-context';target.id='task-context';
    target.innerHTML='<div class="section-title"><h2 id="task-context-title">新任务 · 尚未上传</h2><button id="new-task" type="button">新建任务 / 从空白开始</button></div><p id="task-origin"></p><div id="saved-file-context" hidden><strong>服务器已保存的文件</strong><ul id="saved-file-list"></ul></div><p id="new-file-context">本次新选择的本地文件：尚未选择。未选择不代表服务器没有历史文件。</p><details><summary>返回本浏览器保留的任务或草稿</summary><label for="task-history">选择历史任务（不会删除其他版本）</label><select id="task-history"><option value="">请选择</option></select><p class="hint">此列表记录本浏览器使用过的任务。服务器数据另存于控制台所示状态目录；关闭页面、停止服务均不清空。</p></details>';
    document.querySelector('main').insertBefore(target,document.querySelector('main > section'));
    document.getElementById('new-task').onclick=()=>this.fresh();
    document.getElementById('task-history').onchange=()=>this.load(document.getElementById('task-history').value);
    for(const id of options.files)document.getElementById(id).addEventListener('change',()=>this.selection());
    this.list();this.selection();
  },
  list(){
    if(this.options.mode==='pipeline'){PipelineWorkspace.list();return;}
    const select=document.getElementById('task-history'),current=select.value;select.replaceChildren(new Option('请选择',''));
    for(const item of this.read(this.prefix+'history',[]))select.add(new Option((item.draft?'未上传草稿：':'历史任务：')+(item.task||item.id).slice(0,55),item.id));
    select.value=current;
  },
  sync(state){
    if(this.state?.id!==state.id)this.restored=false;this.state=state;
    const list=this.read(this.prefix+'history',[]),old=list.findIndex(x=>x.id===state.id);
    const item={id:state.id,task:state.task,files:state.files.map(f=>f.name),draft:false,phase:state.phase,revision:state.revision,updated_at:state.updated_at||list[old]?.updated_at||'',created_at:state.created_at,last_opened_at:new Date().toISOString()};
    if(old>=0)list.splice(old,1);list.unshift(item);this.write(this.prefix+'history',list);this.list();this.draw();
  },
  markRestored(){this.restored=true;this.draw();},
  draw(){
    const s=this.state;if(!s)return;
    document.getElementById('task-context-title').textContent=this.restored?'已恢复历史任务':this.options.mode==='pipeline'?'当前任务':'当前任务 · 文件已保存';
    document.getElementById('task-origin').textContent=this.options.mode==='pipeline'?'已保存 '+s.files.length+' 份文件':this.restored?'数据来自服务器先前保存的上传，并非本次文件选择。可以直接继续；若要处理另一批数据，请新建任务。':'文件已经实际上传并由服务器保存。重新选择本地文件尚不会替换本任务；上传后创建新的独立任务。';
    document.getElementById('saved-file-context').hidden=false;
    const ul=document.getElementById('saved-file-list');ul.replaceChildren();
    for(const f of s.files){const li=document.createElement('li');li.textContent=f.name;li.dataset.savedFile=f.name;ul.append(li);}
    this.selection();
    if(this.options.mode==='pipeline')PipelineWorkspace.sync(s);
  },
  selection(){
    const names=this.options.files.flatMap(id=>[...document.getElementById(id).files].map(f=>f.name));
    if(this.options.mode==='pipeline'&&this.state&&names.length){document.getElementById('composer-upload').hidden=false;document.getElementById('start-pipeline').hidden=false;document.getElementById('start-pipeline').textContent='用所选文件新建任务';}
    const selected=document.getElementById('new-file-context');selected.hidden=this.options.mode==='pipeline'&&(!names.length||!this.state);selected.textContent='本次新选择的本地文件：'+(names.length?names.join('、')+'（尚未上传，不等于服务器已保存的文件）':'尚未选择。可继续处理已保存任务；新任务需另选文件。');
  },
  form(){
    return [...document.querySelectorAll('input:not([type="file"]):not([type="password"]),textarea,select')].filter(n=>!n.closest('.workspace-nav')&&((n.id&&n.id!=='task-history')||n.dataset.mapFile||n.dataset.transformFile)).map(n=>({id:n.id,mapFile:n.dataset.mapFile,mapColumn:n.dataset.mapColumn,transformFile:n.dataset.transformFile,transformColumn:n.dataset.transformColumn,value:n.multiple?[...n.selectedOptions].map(o=>o.value):n.value,checked:n.checked}));
  },
  applyForm(id){
    const saved=this.read(this.prefix+'form:'+id,[]);let changed=false;
    const ordered=[...saved.filter(r=>r.id!=='keys'),...saved.filter(r=>r.id==='keys')];
    for(const row of ordered){if(row.id==='keys'&&this.options.refreshKeys)this.options.refreshKeys();const node=row.id?document.getElementById(row.id):[...document.querySelectorAll('[data-map-file],[data-transform-file]')].find(n=>row.mapFile?n.dataset.mapFile===row.mapFile&&n.dataset.mapColumn===row.mapColumn:n.dataset.transformFile===row.transformFile&&n.dataset.transformColumn===row.transformColumn);if(!node||node.type==='file')continue;
      const before=node.multiple?[...node.selectedOptions].map(o=>o.value):node.value;
      if(JSON.stringify(before)!==JSON.stringify(row.value)||node.checked!==row.checked)changed=true;
      if(node.multiple){for(const o of node.options)o.selected=row.value.includes(o.value);}else node.value=row.value;
      if(typeof row.checked==='boolean')node.checked=row.checked;
    }return changed;
  },
  async fresh(){
    if(this.options.mode==='pipeline'){await PipelineWorkspace.navigate('new');return;}
    if(this.options.canNavigate&&!this.options.canNavigate())return;
    if(this.options.beforeNew)await this.options.beforeNew();
    const form=this.form(),task=document.getElementById(this.options.task).value;
    if(this.state){this.write(this.prefix+'form:'+this.state.id,form);}
    else if(task.trim()){
      const id='draft:'+Date.now();this.write(this.prefix+'form:'+id,form);
      const list=this.read(this.prefix+'history',[]);list.unshift({id,task,draft:true});this.write(this.prefix+'history',list);
    }
    // Only the active pointer is cleared. No upload, result, per-task draft, or audit is deleted.
    localStorage.removeItem(this.options.activeKey);location.reload();
  },
  async load(id){
    if(!id||this.options.canNavigate&&!this.options.canNavigate())return;
    if(id.startsWith('draft:')){this.applyForm(id);document.getElementById('task-origin').textContent='已载入未上传草稿；本地文件尚未上传，请重新选择。';return;}
    try{await this.options.load(id);this.markRestored();}
    catch(e){document.getElementById('task-origin').textContent='未能恢复该任务，请核对服务状态目录。原任务和草稿未删除。'+e.message;}
  }
};

// The pipeline workspace reuses the existing task/draft persistence and execution code.
window.PipelineWorkspace={
  context:null,serverItems:[],previewNonce:0,selected:null,draftId:null,
  escape(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));},
  phase(p){return ({organized:'整理已完成',complete:'已完成',data_checkpoint:'待核对数据',business_checkpoint:'待补充规则',understanding_checkpoint:'待补充理解',understanding_error:'理解未完成',system_error:'执行未完成',migration_required:'历史归档',unavailable:'读取异常'})[p]||'已保存';},
  setup(context){
    this.context=context;const main=document.querySelector('main'),shell=document.createElement('div');shell.className='pipeline-workspace';
    const nav=document.createElement('aside');nav.className='workspace-nav';nav.setAttribute('aria-label','历史任务对话');
    nav.innerHTML='<div class="rail-heading">任务对话</div><button id="new-task" type="button" class="new-conversation">＋ 新建任务</button><input id="history-search" type="search" aria-label="搜索历史任务" placeholder="搜索任务"><p id="history-source" class="hint" hidden>正在读取历史任务…</p><div id="conversation-list"></div>';
    const files=document.createElement('aside');files.className='workspace-files';files.setAttribute('aria-label','当前任务文件');
    files.innerHTML='<div class="rail-heading">任务文件 <span id="workspace-file-count">0</span></div><p id="files-task-label" class="hint"></p><div id="workspace-file-list"><p class="empty">暂无文件</p></div><details id="saved-file-context" hidden><summary>原文件清单</summary><ul id="saved-file-list"></ul></details>';
    main.before(shell);shell.append(nav,main,files);const toggle=document.createElement('button');toggle.id='toggle-files';toggle.textContent='文件';toggle.onclick=()=>files.classList.toggle('mobile-open');document.querySelector('header').append(toggle);
    const target=document.createElement('section');target.id='task-context';target.className='task-context conversation-context';
    target.innerHTML='<h1 id="task-context-title">新任务</h1><p id="task-origin"></p><p id="new-file-context"></p>';
    main.prepend(target);
    const history=document.getElementById('instruction-history');history.open=true;history.classList.add('workspace-conversation');history.querySelector('summary').textContent='任务对话';
    const composer=document.getElementById('task-composer');const stream=document.createElement('div');stream.id='conversation-stream';const region=document.createElement('div');region.id='conversation-region';main.querySelector('footer').before(region,composer);region.append(stream);stream.append(history);stream.addEventListener('scroll',()=>this.updateRound?.(),{passive:true});const tooltip=document.createElement('div');tooltip.id='round-tooltip';tooltip.role='tooltip';tooltip.hidden=true;document.body.append(tooltip);document.addEventListener('keydown',e=>{if(e.key==='Escape')tooltip.hidden=true;});window.addEventListener('resize',()=>tooltip.hidden=true);const dock=document.createElement('div');dock.id='question-dock';composer.before(dock);dock.append(document.getElementById('checkpoint'),document.getElementById('system-section'));
    const inspect=document.createElement('dialog');inspect.id='data-inspector';inspect.innerHTML='<div class="preview-heading"><h2 id="inspector-title"></h2><button type="button" id="close-inspector">关闭 ×</button></div><div id="inspector-body"></div>';document.body.append(inspect);document.getElementById('close-inspector').onclick=()=>inspect.close();
    const tools=document.createElement('div');tools.className='inspection-actions';for(const [id,label] of [['understood','读取与理解'],['dwd','数据整理'],['optional-business','计算与试算']]){const b=document.createElement('button');b.type='button';b.textContent=label;b.dataset.inspect=id;b.onclick=()=>this.inspect(id,label);tools.append(b);}stream.prepend(tools);
    const stash=document.createElement('div');stash.hidden=true;stash.id='inspection-stash';main.append(stash);for(const id of ['understood','dwd','optional-business','confirmation-history']){const node=document.getElementById(id);if(node)stash.append(node);}document.getElementById('composer-status').append(document.getElementById('message'));
    const optional=document.createElement('details');optional.id='optional-business';optional.hidden=true;optional.innerHTML='<summary>计算与试算（可选）</summary>';
    document.getElementById('rules-section').before(optional);optional.append(document.getElementById('rules-section'),document.getElementById('dws'));document.getElementById('inspection-stash').append(optional);
    for(const id of ['dwd','understood'])document.getElementById(id).hidden=true;
    document.getElementById('new-task').onclick=()=>context.fresh();
    document.getElementById('history-search').oninput=()=>this.list();
    for(const id of context.options.files)document.getElementById(id).addEventListener('change',()=>context.selection());
    const dialog=document.createElement('dialog');dialog.id='file-preview';dialog.setAttribute('aria-labelledby','file-preview-title');
    dialog.innerHTML='<div class="preview-heading"><div><p id="file-preview-kind" class="eyebrow"></p><h2 id="file-preview-title">文件预览</h2></div><button id="close-file-preview" type="button" aria-label="关闭文件预览">关闭 ×</button></div><p id="file-preview-meta"></p><div id="file-preview-notes"></div><div id="file-preview-body"></div><div class="actions"><a id="file-preview-download" class="download" hidden>下载此文件</a><span id="file-preview-location" class="hint"></span></div>';
    document.body.append(dialog);document.getElementById('close-file-preview').onclick=()=>dialog.close();
    dialog.addEventListener('close',()=>{this.previewNonce++;this.selected=null;});
    this.list();context.selection();this.refreshHistory();
    const pending=sessionStorage.getItem('pipeline-open-draft');if(pending){sessionStorage.removeItem('pipeline-open-draft');this.draftId=pending;const apply=()=>{if(document.getElementById(context.options.task).disabled){setTimeout(apply,50);return;}context.applyForm(pending);document.getElementById('task-context-title').textContent='未上传的任务草稿';document.getElementById('task-origin').textContent='已恢复草稿。文件尚未上传，请重新选择本地文件。';this.list();};setTimeout(apply,0);}
  },
  inspect(id,label){const dialog=document.getElementById('data-inspector'),body=document.getElementById('inspector-body');for(const child of [...body.children])document.getElementById('inspection-stash').append(child);const node=document.getElementById(id);body.append(node);node.hidden=false;if(node.tagName==='DETAILS')node.open=true;document.getElementById('inspector-title').textContent=label;dialog.showModal();},
  async refreshHistory(){
    try{const r=await fetch('/api/pipelines');if(!r.ok)throw new Error();const data=await r.json();if(!Array.isArray(data.items))throw new Error();this.serverItems=data.items;document.getElementById('history-source').hidden=true;document.getElementById('history-source').textContent='当前服务保存的任务与本浏览器草稿';}
    catch{document.getElementById('history-source').hidden=false;document.getElementById('history-source').textContent='本浏览器保存的任务。当前服务为旧接口；重启服务后可显示服务器完整历史。';}
    this.list();
  },
  list(){
    const ctx=this.context;if(!ctx)return;const records=new Map(),rail=document.querySelector('.workspace-nav'),scroll=rail.scrollTop;
    for(const item of ctx.read(ctx.prefix+'history',[]))records.set(item.id,item);
    for(const item of this.serverItems)records.set(item.id,{...records.get(item.id),...item});
    if(ctx.state)records.set(ctx.state.id,{...records.get(ctx.state.id),phase:ctx.state.phase,revision:ctx.state.revision,updated_at:ctx.state.updated_at||records.get(ctx.state.id)?.updated_at,created_at:ctx.state.created_at});
    const search=document.getElementById('history-search').value.trim().toLocaleLowerCase(),list=document.getElementById('conversation-list');list.replaceChildren();
    const items=[...records.values()].sort((a,b)=>(b.updated_at||'').localeCompare(a.updated_at||'')||b.id.localeCompare(a.id));let lastGroup='';
    for(const item of items){if(search&&!(item.task+' '+(item.files||[]).join(' ')).toLocaleLowerCase().includes(search))continue;
      const button=document.createElement('button');button.type='button';button.className='conversation-item';button.dataset.taskId=item.id;
      const date=item.updated_at?new Date(item.updated_at):null,today=new Date(),yesterday=new Date();yesterday.setDate(today.getDate()-1);const group=!date||isNaN(date)?'时间未记录':date.toDateString()===today.toDateString()?'今天':date.toDateString()===yesterday.toDateString()?'昨天':'更早';if(group!==lastGroup){const label=document.createElement('div');label.className='history-date-group';label.textContent=group;list.append(label);lastGroup=group;}
      if((ctx.state?.id||this.draftId)===item.id){button.classList.add('active');button.setAttribute('aria-current','true');}
      const title=document.createElement('strong');title.textContent=(item.task||'未命名任务').trim().split('\n')[0];
      const status=document.createElement('span');status.textContent=item.draft?'未上传草稿':this.phase(item.phase)+(item.revision?' · 第'+item.revision+'版':'');
      const time=document.createElement('time');time.textContent=date&&!isNaN(date)?date.toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'';button.append(title,status,time);button.title=(item.task||item.id)+(item.created_at?'\n创建：'+new Date(item.created_at).toLocaleString('zh-CN'):'')+'\n'+(item.time_label||'最后处理')+'：'+(date&&!isNaN(date)?date.toLocaleString('zh-CN'):'未记录');button.onclick=()=>this.navigate(item.id);list.append(button);
    }
    if(!list.children.length){const p=document.createElement('p');p.className='empty';p.textContent=search?'没有匹配的任务':'暂无历史任务';list.append(p);}
    rail.scrollTop=scroll;
  },
  async navigate(id){
    const ctx=this.context;if(this.navigating||ctx.options.canNavigate&&!ctx.options.canNavigate())return;
    if(id===ctx.state?.id)return;
    this.navigating=true;try{
    if(ctx.options.beforeNew)await ctx.options.beforeNew();
    const form=ctx.form(),task=document.getElementById(ctx.options.task).value;
    if(ctx.state)ctx.write(ctx.prefix+'form:'+ctx.state.id,form);
    else if(task.trim()){
      const draftId=this.draftId||'draft:'+Date.now(),items=ctx.read(ctx.prefix+'history',[]).filter(i=>i.id!==draftId);
      ctx.write(ctx.prefix+'form:'+draftId,form);items.unshift({id:draftId,task,draft:true,updated_at:new Date().toISOString(),files:[]});ctx.write(ctx.prefix+'history',items);
    }
    if(id==='new'||id.startsWith('draft:')){localStorage.removeItem(ctx.options.activeKey);if(id!=='new')sessionStorage.setItem('pipeline-open-draft',id);location.reload();return;}
    await ctx.load(id);window.scrollTo({top:0,behavior:'smooth'});
    }finally{this.navigating=false;}
  },
  sync(state){
    document.getElementById('dwd').hidden=false;
    const optional=document.getElementById('optional-business');optional.hidden=false;optional.open=state.calculation_requested!==false;
    if(this.selected&&(this.selected.task!==state.id||this.selected.revision!==state.revision||
       this.selected.artifact==='dws'&&!state.stage2||this.selected.artifact==='dwd'&&!state.stage1)){
      this.previewNonce++;document.getElementById('file-preview').close();this.selected=null;
    }
    this.list();const target=document.getElementById('workspace-file-list');target.replaceChildren();let count=0;
    const group=(label,items)=>{const section=document.createElement('div');section.className='artifact-group';const title=document.createElement('h3');title.textContent=label;section.append(title);
      for(const item of items){count++;const button=document.createElement('button');button.type='button';button.className='artifact-file';button.dataset.artifact=item.key;button.title=item.name;
        const icon=document.createElement('span');icon.className='file-icon';icon.textContent='CSV';const text=document.createElement('span');text.className='artifact-label';const name=document.createElement('strong');name.textContent=item.name;const meta=document.createElement('small');meta.textContent=item.note; text.append(name);if(item.note)text.append(meta);button.append(icon,text);button.onclick=()=>this.openFile(item,state);section.append(button);}
      if(!items.length){const p=document.createElement('p');p.className='empty';p.textContent='尚未生成';section.append(p);}target.append(section);};
    group('原始上传',state.files.map(f=>({key:'input-'+f.id,name:f.name,note:f.errors?.length?'读取异常':''})));
    const d=state.stage1;group('整理宽表',d?[{key:'dwd',name:'整理宽表.csv',note:'整理版本 '+d.version+' · '+(d.status==='ready'?'已整理':'仍有待确认问题')}]:[]);
    const s=state.stage2,outputs=[];if(s)outputs.push({key:'dws',name:s.mode==='trial'?'当前试算结果.csv':'当前计算结果.csv',note:'第'+s.trial_id+'次计算 · '+(s.status==='complete'?'已完成':'含待处理项')});
    const accepted=state.accepted_result;if(accepted&&d&&accepted.dwd_manifest_sha256===d.manifest_sha256&&accepted.trial_id!==s?.trial_id)outputs.push({key:'accepted',name:'已采用结果.csv',note:'第'+accepted.trial_id+'次计算 · 与当前整理版本一致'});
    if(state.calculation_requested!==false||outputs.length)group('计算结果',outputs);document.getElementById('workspace-file-count').textContent=count;document.getElementById('files-task-label').textContent='第'+state.revision+'版 · '+this.phase(state.phase);this.conversation(state);
  },
  conversation(state){
    const target=document.getElementById('instruction-list');target.replaceChildren();
    const turns=state.conversation?.length?state.conversation:(state.instructions||[{text:state.task}]).map((i,n)=>({id:'legacy'+n,text:i.text,phase:null}));
    document.querySelector('.round-navigation')?.remove();const nav=document.createElement('div');nav.className='round-navigation';
    for(const [index,t] of turns.entries()){
      const pair=document.createElement('div');pair.id='turn-'+t.id;pair.className='conversation-turn';
      const user=document.createElement('article');user.className='conversation-message user-message';user.textContent=t.text;pair.append(user);
      const reply=document.createElement('article');reply.className='conversation-message system-message';reply.textContent=t.phase?this.phase(t.phase):'此历史轮次未保存完整反馈';
      for(const q of t.issues||[]){const p=document.createElement('p');p.textContent=q.reason;reply.append(p);}
      if(t.understanding&&typeof t.understanding==='string'){const explanation=document.createElement('p');explanation.textContent=t.understanding;reply.append(explanation);}if(t.snapshot){const summary=document.createElement('p');summary.textContent=(t.summary||[]).map(p=>(p.items||[]).map(m=>m.target+'：'+m.strategy).join('、')).filter(Boolean).join('；');reply.append(summary);if(t.snapshot.rules?.weights){const rule=document.createElement('p');rule.textContent='权重：'+Object.entries(t.snapshot.rules.weights).map(([k,v])=>k+' '+percentDisplay(v)+'%').join('、');reply.append(rule);}for(const item of t.snapshot.rules?.assessment_items||[]){const description=document.createElement('p');description.textContent=item.name+'：'+(typeof operations!=='undefined'?operations[item.operation]||item.operation:item.operation)+'；来源 '+(item.sources||[]).join('、');reply.append(description);}for(const adjustment of t.snapshot.rules?.adjustments||[]){const description=document.createElement('p');description.textContent='调整 '+adjustment.target+'；范围 '+(adjustment.scope?.ids?.join('、')||((adjustment.scope?.field||'')+' '+(adjustment.scope?.op||'')+' '+(adjustment.scope?.value??'')))+'；依据 '+(adjustment.basis||'待补充');reply.append(description);}for(const [key,stage,label] of [['dwd','stage1','整理宽表'],['dws','stage2','计算结果']])if(t.snapshot[stage]){const button=document.createElement('button');button.type='button';button.textContent=label;button.onclick=()=>this.openHistorical(state,t,key,label);reply.append(button);}if(t.snapshot.events){const link=document.createElement("a");link.href="/api/pipelines/"+state.id+"/artifacts/"+t.id+"/report";link.textContent="处理说明（TXT）";reply.append(link);}}
      pair.append(reply);target.append(pair);const chip=document.createElement('button');chip.type='button';chip.dataset.turn=t.id;chip.setAttribute('aria-label','第'+(index+1)+'轮：'+t.text);chip.setAttribute('aria-describedby','round-tooltip');const show=()=>{const tip=document.getElementById('round-tooltip');tip.textContent='第'+(index+1)+'轮 · '+(t.created_at?new Date(t.created_at).toLocaleString('zh-CN'):'时间未记录')+' · '+(t.phase?this.phase(t.phase):'反馈未记录')+'\n'+t.text.slice(0,80);tip.hidden=false;const r=chip.getBoundingClientRect();tip.style.left=Math.min(r.right+8,innerWidth-tip.offsetWidth-8)+'px';tip.style.top=Math.max(8,Math.min(r.top,innerHeight-tip.offsetHeight-8))+'px';};chip.onmouseenter=show;chip.onfocus=show;chip.onmouseleave=chip.onblur=()=>document.getElementById('round-tooltip').hidden=true;chip.onclick=()=>{const stream=document.getElementById('conversation-stream');stream.scrollTo({top:stream.scrollTop+pair.getBoundingClientRect().top-stream.getBoundingClientRect().top,behavior:'auto'});this.updateRound?.();show();};nav.append(chip);
    }
    nav.setAttribute('aria-label','当前对话轮次');document.getElementById('conversation-region').prepend(nav);document.getElementById('round-tooltip').hidden=true;
    this.updateRound=()=>{const stream=document.getElementById('conversation-stream'),pairs=[...target.children];let active=pairs[0];for(const pair of pairs)if(pair.getBoundingClientRect().top<=stream.getBoundingClientRect().top+40)active=pair;if(stream.scrollHeight-stream.clientHeight-stream.scrollTop<4)active=pairs.at(-1);for(const button of nav.children){const selected='turn-'+button.dataset.turn===active?.id;button.classList.toggle('current-round',selected);if(selected)button.setAttribute('aria-current','true');else button.removeAttribute('aria-current');}};this.updateRound();
    if(this.issueTask!==state.id){this.activeIssueKey=null;this.issueTask=state.id;}
    const cards=[...document.querySelectorAll('#quick-resolutions .question-card')];document.getElementById('question-navigation')?.remove();
    if(cards.length){let selected=cards.findIndex(c=>c.dataset.key===this.activeIssueKey);if(selected<0)selected=0;this.activeIssueKey=cards[selected].dataset.key;const control=document.createElement('div');control.id='question-navigation';if(cards.length>1){const select=document.createElement('select');select.setAttribute('aria-label','选择待确认问题');cards.forEach((c,i)=>select.add(new Option((i+1)+' / '+cards.length+' '+c.querySelector('h3').textContent,c.dataset.key)));select.value=cards[selected].dataset.key;select.onchange=()=>{this.activeIssueKey=select.value;cards.forEach(c=>c.hidden=c.dataset.key!==select.value);};control.append(select);}document.getElementById('quick-resolutions').before(control);cards.forEach((c,i)=>{c.hidden=i!==selected;const records=[...c.querySelectorAll(':scope > .raw-record')];if(records.length){const details=document.createElement('details');details.innerHTML='<summary>核对原始记录（'+records.length+'条）</summary>';records[0].before(details);details.append(...records);}});}

  },
  async openHistorical(state,turn,kind,label){
    const url='/api/pipelines/'+state.id+'/artifacts/'+turn.id+'/'+kind;
    const response=await fetch(url+'/preview');if(!response.ok){const error=await response.json();document.getElementById('message').hidden=false;document.getElementById('message').textContent=error.error||'历史文件不可用';return;}
    const stage=turn.snapshot[kind==='dwd'?'stage1':'stage2'];const dialog=document.getElementById('file-preview');document.getElementById('file-preview-title').textContent=label;document.getElementById('file-preview-kind').textContent='第'+turn.revision+'版 · 历史文件';document.getElementById('file-preview-meta').textContent='';document.getElementById('file-preview-notes').replaceChildren();const file=await response.json();const wrap=document.createElement('div');wrap.className='table-wrap';const table=document.createElement('table');table.id='artifact-preview-table';const head=document.createElement('tr');for(const c of file.columns){const th=document.createElement('th');th.textContent=c;head.append(th);}table.append(head);const body=document.createElement('tbody');for(const row of file.rows){const tr=document.createElement('tr');for(const c of file.columns){const td=document.createElement('td');td.dataset.raw=row[c];td.textContent=row[c]===''?'空缺':row[c];tr.append(td);}body.append(tr);}table.append(body);wrap.append(table);document.getElementById('file-preview-body').replaceChildren(wrap);document.getElementById('file-preview-meta').textContent='预览 '+file.rows.length+' / '+file.total_rows+' 行';const link=document.getElementById('file-preview-download');link.href=url;link.hidden=false;document.getElementById('file-preview-location').textContent='历史版本';dialog.showModal();
  },
  csvText(columns,rows){const cell=v=>{const s=String(v??'');return /[",\r\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;};return [columns,...rows.map(r=>columns.map(c=>r[c]))].map(row=>row.map(cell).join(',')).join('\r\n')+'\r\n';},
  async legacyPreview(item,state){
    if(item.key.startsWith('input-')){const f=state.files.find(f=>'input-'+f.id===item.key),records=f.raw_rows||[],width=Math.min(128,Math.max(0,...records.map(r=>r.cells.length))),columns=['原始位置',...Array.from({length:width},(_,i)=>'原始列'+(i+1))];return {name:f.name,status:'原始上传',columns,rows:records.slice(0,100).map(r=>Object.fromEntries(columns.map((c,i)=>[c,i===0?'第'+r.line+(r.end_line!==r.line?'—'+r.end_line:'')+'行':r.cells[i-1]??'']))),total_rows:records.length,notes:['按服务器实际读取的原始记录预览；说明行与表头保留，最多显示100条、128列，完整输入不受预览限制。',...(f.errors||[]),'当前后端为旧接口；重启服务后可下载原始上传文件。']};}
    const response=await fetch('/api/pipelines/'+state.id+'/download?kind='+item.key);if(!response.ok){const error=await response.json();throw new Error(error.error||'文件校验失败');}
    const text=(await response.text()).replace(/^\uFEFF/,''),stage=item.key==='dwd'?state.stage1:item.key==='accepted'?state.accepted_result:state.stage2;
    if(this.csvText(stage.columns,stage.rows)!==text)return {name:item.name,status:'持久化CSV原文',raw:text,total_rows:stage.rows.length,notes:['当前后端为旧接口，CSV内容与缓存表格不同；直接显示已校验文件原文，未使用缓存表格。'],download_url:'/api/pipelines/'+state.id+'/download?kind='+item.key};
    return {name:item.name,status:item.note,columns:stage.columns,rows:stage.rows.slice(0,100),total_rows:stage.rows.length,notes:['持久化下载通过现有完整性检查，内容与预览逐值核对一致。'],download_url:'/api/pipelines/'+state.id+'/download?kind='+item.key};
  },
  async openFile(item,state){
    const sequence=++this.previewNonce;this.selected={task:state.id,revision:state.revision,artifact:item.key};const dialog=document.getElementById('file-preview');document.getElementById('file-preview-title').textContent=item.name;document.getElementById('file-preview-body').textContent='正在加载并校验文件…';document.getElementById('file-preview-meta').textContent='';document.getElementById('file-preview-kind').textContent='任务文件';document.getElementById('file-preview-notes').replaceChildren();document.getElementById('file-preview-download').hidden=true;if(!dialog.open)dialog.showModal();
    try{const r=await fetch('/api/pipelines/'+state.id+'/files/'+item.key+'?revision='+state.revision);let file;if(r.status===404)file=await this.legacyPreview(item,state);else{file=await r.json();if(!r.ok)throw new Error(file.error||'文件加载失败');}
      if(sequence!==this.previewNonce)return;
      document.getElementById('file-preview-title').textContent=file.name;document.getElementById('file-preview-kind').textContent=file.status;
      document.getElementById('file-preview-meta').textContent='任务第'+state.revision+'版 · '+(file.version_label||item.note)+' · 共'+file.total_rows+'条记录'+(file.rows?' · 显示前'+file.rows.length+'条':'');
      const body=document.getElementById('file-preview-body');body.replaceChildren();if(file.raw!==undefined){const pre=document.createElement('pre');pre.textContent=file.raw;body.append(pre);}else{const wrap=document.createElement('div');wrap.className='table-wrap';const table=document.createElement('table');table.id='artifact-preview-table';const head=document.createElement('thead'),tr=document.createElement('tr');for(const col of file.columns){const th=document.createElement('th');th.textContent=col;tr.append(th);}head.append(tr);table.append(head);const tbody=document.createElement('tbody');for(const row of file.rows){const tr=document.createElement('tr');for(const col of file.columns){const td=document.createElement('td');td.textContent=row[col]===''?'空缺':String(row[col]??'');td.dataset.raw=row[col]??'';if(row[col]==='')td.className='null';tr.append(td);}tbody.append(tr);}table.append(tbody);wrap.append(table);body.append(wrap);}
      const notes=document.getElementById('file-preview-notes');for(const text of file.notes||[]){const p=document.createElement('p');p.className='hint';p.textContent=text;notes.append(p);}
      const link=document.getElementById('file-preview-download');link.hidden=!file.download_url;if(file.download_url)link.href=file.download_url;document.getElementById('file-preview-location').textContent=file.rows&&file.rows.length<file.total_rows?'预览 '+file.rows.length+' / '+file.total_rows+' 条；下载包含全部记录':'';
    }catch(error){if(sequence!==this.previewNonce)return;document.getElementById('file-preview-body').textContent='未能加载：'+error.message+'。请重新加载任务后再试；原文件未修改。';}
  }
};

// Generic workbench code runs only on the homepage; the pipeline reuses the context utility.
if(document.getElementById("file-a")){
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
  TaskContext.sync(state);
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
  const uploaded=await api('/api/sessions',{task:$('task').value,files:[{name:a.name,content_base64:await encoded(a)},{name:b.name,content_base64:await encoded(b)}]});
  $('file-a').value='';$('file-b').value='';adopt(uploaded);
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
TaskContext.setup({mode:'generic',activeKey:'integration-session',task:'task',files:['file-a','file-b'],refreshKeys:()=>refreshKeys(),beforeNew:()=>invalidating,load:async id=>{adopt(await api('/api/sessions/'+id));if(TaskContext.applyForm(id)){dirty=true;$('execute-btn').disabled=true;$('downloads').hidden=true;$('plan-state').textContent='已恢复未提交草稿，请保存并重新检查';}}});
(async()=>{
  try { config=await api('/api/config');$('model-status').textContent=config.model.label;$('model-disclosure').textContent=config.model.disclosure;
    const sid=localStorage.getItem('integration-session');if(sid){try{adopt(await api('/api/sessions/'+sid));TaskContext.markRestored();if(TaskContext.applyForm(sid)){dirty=true;$('execute-btn').disabled=true;$('downloads').hidden=true;}message('已恢复历史任务，文件来自服务器已保存的上传；无需重新选择。');}catch(e){message('历史任务未能载入，请核对状态目录；原草稿未删除。',true);}}
  }catch(e){message('无法连接本地服务：'+e.message,true);}
})();

}
