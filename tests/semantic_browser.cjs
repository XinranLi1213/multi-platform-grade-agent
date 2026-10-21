// Real Edge clicks/submissions/downloads. Model is deliberately disabled on this test service.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),{spawnSync}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=path.join(root,'outputs'),base=process.env.INTEGRATION_TEST_URL||'http://127.0.0.1:8766',checks=[];
function record(name,expected,actual){checks.push({name,expected,actual,status:'passed'});console.log('PASS '+name);}
async function submitted(page,suffix,fn,status=200){const [r]=await Promise.all([page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith(suffix)),fn()]);const s=await r.json();assert.equal(r.status(),status,JSON.stringify(s));if(status<300)await page.waitForFunction(v=>document.querySelector('#pipeline-id').textContent.startsWith('第'+v+'版')&&!document.querySelector('#start-pipeline').disabled,s.revision);return s;}
async function openRules(page){if(!await page.locator('#rules-section').evaluate(n=>n.open))await page.locator('#rules-section > summary').click();}
async function addItem(page,name,operation,sources,basis){
 await page.locator('#add-assessment').click();let item=page.locator('.assessment-item').last();
 await item.locator('[data-item="name"]').fill(name);await item.locator('[data-item="name"]').press('Tab');
 item=page.locator('.assessment-item').last();await item.locator('[data-item="operation"]').selectOption(operation);
 item=page.locator('.assessment-item').last();await item.locator('[data-item="sources"]').selectOption(sources);await item.locator('[data-item="basis"]').fill(basis);
}
async function compareDownload(page,s,kind='dws'){
 const r=s[kind==='dwd'?'stage1':'stage2'];
 const displayed=await page.locator('#'+kind+'-table tbody tr').evaluateAll(rows=>rows.map(row=>[...row.querySelectorAll('td')].map(c=>c.dataset.raw)));
 assert.deepEqual(displayed,r.rows.slice(0,50).map(row=>r.columns.map(c=>row[c])));
 const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#download-'+kind).click()]);
 const dest=path.join(out,'semantic-browser-'+kind+'.csv');await download.saveAs(dest);
 const parsed=spawnSync('python',['-X','utf8','-c',"import csv,json,sys;print(json.dumps(list(csv.DictReader(open(sys.argv[1],encoding='utf-8-sig',newline=''))),ensure_ascii=False))",dest],{encoding:'utf8'});
 assert.equal(parsed.status,0,parsed.stderr);assert.deepEqual(JSON.parse(parsed.stdout),r.rows);return dest;
}
(async()=>{let browser;try{
 browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',args:['--disable-extensions']});
 const context=await browser.newContext({acceptDownloads:true,viewport:{width:1440,height:1080}}),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));await page.goto(base+'/pipeline');await page.waitForFunction(()=>!document.querySelector('#pipeline-task').disabled);
 assert.equal(await page.locator('textarea:not(#advanced textarea)').count(),1);
 await page.locator('#pipeline-task').fill('原始数据百分制，缓考保留，保留一位小数');await page.locator('#pipeline-files').setInputFiles(['roster.csv','performance.csv'].map(n=>path.join(root,'tests/semantic_data',n)));
 let s=await submitted(page,'/api/pipelines',()=>page.locator('#start-pipeline').click(),201);
 assert.equal(s.phase,'business_checkpoint');assert.equal(await page.locator('#rules-section').evaluate(n=>n.open),false);assert.match(await page.locator('#instruction-help').textContent(),/语义理解未启用/);
 await page.getByText('查看文件实际读取结果与原值',{exact:true}).click();
 for(const t of s.view.tables){const actual=await page.locator('#reading-'+t.file_id+' tbody tr').evaluateAll(rows=>rows.map(row=>[...row.querySelectorAll('td')].map(c=>c.dataset.raw)));assert.deepEqual(actual,t.rows.slice(0,50).map(row=>t.headers.map(h=>row.values[h])));}
 record('01 单一入口与实际读取','一个主要自然语言框；未配置模型明确说明；原值=页面DOM；表单默认折叠',{phase:s.phase,files:s.files.length});
 await openRules(page);await addItem(page,'课堂展示','mean',['展示一','展示二'],'教师规定两次课堂展示平均');await addItem(page,'实践考核','direct',['实践'],'教师指定实践采集项');await addItem(page,'平时表现','direct',['平时'],'教师指定平时采集项');
 for(const [name,value] of Object.entries({课堂展示:'40',实践考核:'40',平时表现:'20'}))await page.locator(`[data-weight="${name}"]`).fill(value);
 s=await submitted(page,'/resume',()=>page.locator('#resume-pipeline').click());assert.equal(s.phase,'complete',JSON.stringify(s.issues));assert.deepEqual(s.stage2.rows.map(r=>r.总评),['48.0','86.0','0.0','缓考']);
 assert.equal(s.stage1.counts.collapsed_submissions,0);assert.equal(await page.locator('#advanced').evaluate(n=>n.open),false);assert.match(await page.locator('#semantic-summary').textContent(),/课堂展示.*展示一.*展示二/);const dwd=s.stage1.manifest_sha256;
 await compareDownload(page,s);record('02 自定义项目中文表单闭环','不用JSON定义课堂展示/实践/平时；两展示不误去重；CSV=预览',s.stage2.rows.map(r=>({id:r.student_id,total:r.总评})));
 await page.locator('#add-adjustment').click();let a=page.locator('.adjustment-item').last();
 await a.locator('[data-adjust="field"]').selectOption('总评');await a.locator('[data-adjust="threshold"]').fill('60');await a.locator('[data-adjust="target"]').selectOption('平时表现');await a.locator('[data-adjust="action"]').selectOption('set');await a.locator('[data-adjust="value"]').fill('100');await a.locator('[data-adjust="cap"]').fill('100');await a.locator('[data-adjust="special"]').selectOption('preserve');await a.locator('[data-adjust="basis"]').fill('教师要求调整前总评低于60者平时设为100，其他规则不变');
 s=await submitted(page,'/resume',()=>page.locator('#resume-pipeline').click());assert.equal(s.phase,'complete',JSON.stringify(s.issues));assert.deepEqual(s.stage2.adjustment_scope[0].student_ids,['001','003']);assert.deepEqual(s.stage2.rows.map(r=>r.总评),['64.0','86.0','20.0','缓考']);assert.equal(s.stage1.manifest_sha256,dwd);assert.match(await page.locator('#adjustment-results').textContent(),/001.*003/s);
 const baseline=JSON.stringify(s.stage2.rows);s=await submitted(page,'/resume',()=>page.locator('#resume-pipeline').click());assert.equal(JSON.stringify(s.stage2.rows),baseline);await compareDownload(page,s);
 record('03 局部调整与重复提交','只命中001/003，重复不累计，原值与DWD不变',{scope:s.stage2.adjustment_scope,raw:s.stage2.rows[0].平时});
 const accepted=s.accepted_result.trial_id;await page.locator('#pipeline-task').fill('课堂展示50%，实践考核30%，平时表现20%，看看变化');
 s=await submitted(page,'/instruct',()=>page.locator('#try-instruction').click());assert.equal(s.phase,'complete',JSON.stringify(s.issues));assert.equal(s.stage2.mode,'trial');assert.equal(s.accepted_result.trial_id,accepted);assert.equal(s.stage1.manifest_sha256,dwd);assert.equal(s.rules.weights.课堂展示,'0.5');assert.ok(s.stage2.comparison.changes.length);assert.equal(await page.locator('#difference-table').count(),1);assert.equal(await page.locator('#adopt-trial').isVisible(),true);await compareDownload(page,s);
 record('04 追加自然语言试算','真实请求采用新权重，保持DWD与已采用版本，展示差异',{weights:s.rules.weights,accepted:s.accepted_result.trial_id,trial:s.stage2.trial_id});
 const before=s.revision;await page.locator('#pipeline-task').fill('课堂展示50%，实践考核30%，平时表现20%，看看变化');s=await submitted(page,'/instruct',()=>page.locator('#try-instruction').click());assert.equal(s.revision,before);assert.equal(s.feedback.code,'instruction_unchanged');
 s=await submitted(page,'/adopt-trial',()=>page.locator('#adopt-trial').click());assert.equal(s.accepted_result.trial_id,s.stage2.trial_id);await page.reload();await page.waitForFunction(v=>document.querySelector('#pipeline-id').textContent.startsWith('第'+v+'版'),s.revision);
 record('05 重复指令与采用','相同指令不再次调整，明确采用切换当前版本',{revision:s.revision,accepted:s.accepted_result.trial_id});
 await page.waitForFunction(()=>!document.querySelector('#pipeline-task').disabled);await page.locator('#pipeline-task').fill('请综合一下其他课堂情况');
 s=await submitted(page,'/instruct',()=>page.locator('#send-instruction').click());assert.equal(s.phase,'understanding_checkpoint');assert.equal(s.stage2,null);assert.match(await page.locator('#quick-resolutions').textContent(),/语义分析未启用/);assert.equal(await page.locator('#pipeline-task').inputValue(),'请综合一下其他课堂情况');await page.reload();await page.waitForFunction(()=>!document.querySelector('#pipeline-task').disabled);assert.equal(await page.locator('#pipeline-task').inputValue(),'请综合一下其他课堂情况');assert.equal(await page.locator('#dws-table').count(),0);
 record('06 缺项与草稿恢复','未理解不冒充执行成功，具体问题与草稿刷新保留',{phase:s.phase,reason:s.issues[0].reason});
 // Seed the model-style stable-ID representation through the real API, then edit only a Chinese form control.
 const aliasRules=structuredClone(s.rules);delete aliasRules.adjustments;const named=aliasRules.weights;
 aliasRules.weights=Object.fromEntries(aliasRules.assessment_items.map(i=>[i.id,named[i.name]]));
 const seed=await page.request.post(base+'/api/pipelines',{data:{task:'原始数据百分制，沿用明确项目规则',rules:aliasRules,files:['roster.csv','performance.csv'].map(name=>({name,content_base64:fs.readFileSync(path.join(root,'tests/semantic_data',name)).toString('base64')}))}});
 assert.equal(seed.status(),201);s=await seed.json();assert.equal(s.phase,'complete',JSON.stringify(s.issues));
 await page.evaluate(id=>localStorage.setItem('two-stage-run',id),s.id);await page.reload();await page.waitForFunction(()=>!document.querySelector('#pipeline-task').disabled);await openRules(page);
 assert.match(await page.locator('[data-weight="课堂展示"]').inputValue(),/^50(?:\.0+)?$/);assert.match(await page.locator('#effective-summary').textContent(),/课堂展示 50%/);
 const frozen=s.stage1.manifest_sha256;await page.locator('#rule-cap').fill('100');s=await submitted(page,'/resume',()=>page.locator('#resume-pipeline').click());assert.equal(s.phase,'complete',JSON.stringify(s.issues));assert.equal(s.rules.weights.课堂展示,'0.5');assert.equal(s.stage1.manifest_sha256,frozen);
 record('07 模型项目ID与表单往返','内部ID显示中文项目名及正确权重，仅改封顶不清空已有合法配置',{weights:s.rules.weights,dwd_unchanged:true});
 assert.deepEqual(errors,[]);await page.screenshot({path:path.join(out,'semantic-browser-checkpoint.png'),fullPage:true});
 fs.writeFileSync(path.join(out,'semantic_browser_acceptance.json'),JSON.stringify({checks,page_errors:errors,real_llm:'not configured; manual UI and disclosed limited grammar tested'},null,2));console.log('ALL SEMANTIC BROWSER CHECKS PASSED');
 }catch(e){fs.writeFileSync(path.join(out,'semantic_browser_acceptance.json'),JSON.stringify({checks,error:e.stack},null,2));console.error(e);process.exitCode=1;}finally{if(browser)await browser.close();}})();
