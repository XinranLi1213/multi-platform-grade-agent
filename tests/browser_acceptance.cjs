// Optional real-browser QA. The production app has no Node or browser-test dependency.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'..');
const modulePath=process.env.PLAYWRIGHT_MODULE||path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const {chromium}=require(modulePath);
const output=path.join(root,'outputs');fs.mkdirSync(output,{recursive:true});
const base=process.env.INTEGRATION_TEST_URL||'http://127.0.0.1:8765';
const checks=[];
function record(name,expected,actual){checks.push({name,expected,actual,status:'passed'});console.log('PASS '+name);}
function parseCSV(bytes){const p=spawnSync('python',['-X','utf8','-c','import csv,io,json,sys; print(json.dumps(list(csv.DictReader(io.StringIO(sys.stdin.buffer.read().decode("utf-8-sig")))),ensure_ascii=False))'],{input:bytes,encoding:'utf8'});assert.equal(p.status,0,p.stderr);return JSON.parse(p.stdout);}
async function observed(page,suffix,action){const [response]=await Promise.all([page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith(suffix)),action()]);const data=await response.json();assert.ok(response.ok(),JSON.stringify(data));return data;}
async function upload(page,scenario,a='a.csv',b='b.csv'){
  await page.goto(base);
  await page.locator('#task').fill('整合两个平台的联系人或业务记录；统一经核对的同义字段，保留冲突和未匹配记录。');
  for(const [side,name] of [['a',a],['b',b]])await page.locator('#file-'+side).setInputFiles({name:'自选导出-'+scenario+'-'+name,mimeType:'text/csv',buffer:fs.readFileSync(path.join(root,'tests/data',scenario,name))});
  const state=await observed(page,'/api/sessions',()=>page.locator('#upload-btn').click());
  await page.locator('.file-report').first().waitFor();
  for(const f of state.files){
    const count=await page.locator(`.file-report[data-file="${f.id}"] [data-row-count]`).getAttribute('data-row-count');assert.equal(Number(count),f.row_count);
    const shown=await page.locator('#preview-'+f.id+' tbody tr').evaluateAll(rows=>rows.map(r=>Array.from(r.querySelectorAll('td')).slice(1).map(c=>c.dataset.raw)));
    assert.deepEqual(shown,f.rows.slice(0,50).map(r=>f.headers.map(h=>r.values[h])));
  }
  return state;
}
async function configure(page,mapping,keys,options={}){
  for(const [side,fields] of Object.entries(mapping||{}))for(const [column,target] of Object.entries(fields))await page.getByRole('textbox',{name:`${side} ${column} 统一字段`,exact:true}).fill(target);
  await page.locator('#row-meaning').fill(options.row_meaning||'每行代表经业务编号核对的一个联系人或一条业务记录');
  await page.locator('#mode').selectOption(options.mode||'join');
  if((options.mode||'join')==='join'){await page.locator('#keys').selectOption(keys);await page.locator('#link-basis').fill('已核对编号是两平台共同使用的同一对象编号，组合键区分业务记录');}
  if(options.dedupe){await page.locator('#dedupe').selectOption(options.dedupe);await page.locator('#dedupe-basis').fill('已逐行核对，完全相同行是冗余导出副本');}
  const state=await observed(page,'/plan',()=>page.locator('#save-plan').click());
  await page.waitForFunction(v=>document.querySelector('#plan-state').textContent.includes('v'+v),state.revision);
  if(!state.analysis.errors.length){assert.ok(state.analysis.proposed_actions);assert.match(await page.locator('#analysis').textContent(),/拟执行操作/);}
  return state;
}
async function execute(page){
  for(const cb of await page.locator('#checks input').all())if(await cb.isVisible())await cb.check();
  const state=await observed(page,'/execute',()=>page.locator('#execute-btn').click());
  await page.locator('#result-table').waitFor();return state;
}
async function verifyDownload(page,context,state){
  const dom=await page.locator('#result-table tbody tr').evaluateAll(rows=>rows.map(r=>Array.from(r.querySelectorAll('td')).map(c=>c.dataset.raw)));
  assert.deepEqual(dom,state.result.export_rows.slice(0,50).map(r=>state.result.export_columns.map(c=>r[c])));
  const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#download-result').click()]);
  const file=path.join(output,'browser-'+path.parse(state.files[0].name).name+'-v'+state.revision+'.csv');await download.saveAs(file);
  assert.deepEqual(parseCSV(fs.readFileSync(file)),state.result.export_rows);
  const response=await context.request.get(`${base}/api/sessions/${state.id}/download?kind=pending`);
  assert.deepEqual(parseCSV(await response.body()),state.result.export_rows.filter(r=>r.__状态==='待确认'));
}
(async()=>{
  let browser;
  try{
    const edge=process.env.BROWSER_EXECUTABLE||path.join(process.env['ProgramFiles(x86)']||'C:/Program Files (x86)','Microsoft/Edge/Application/msedge.exe');
    browser=await chromium.launch({headless:true,...(fs.existsSync(edge)?{executablePath:edge}:{}),args:['--disable-extensions']});
    let context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});let page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
    let state=await upload(page,'01_synonyms');
    assert.equal(await page.locator('#execute-btn').isDisabled(),true);assert.match(await page.locator('#model-status').textContent(),/尚未启用/);
    record('读取与禁用状态','真实预览行数/原值等于API；任务待澄清时禁止执行，明确语义分析未启用','两侧各2条逐字段一致；未自动执行；禁用标识正确');
    state=await configure(page,{A:{客户号:'对象编号',联系人:'姓名',联系电话:'电话'},B:{编号:'对象编号',联络人:'姓名',手机号:'电话'}},['对象编号']);
    state=await execute(page);assert.equal(state.result.counts.output_rows,2);await verifyDownload(page,context,state);
    await page.screenshot({path:path.join(output,'ui-preview.png'),fullPage:true});
    record('确认与真实CSV下载','4条输入关联2对得到2行；DOM、实际下载和API逐字段一致','通过；0与001保留，待处理CSV也一致');
    await page.reload();await page.locator('#result-table').waitFor();assert.match(await page.locator('#session-label').textContent(),new RegExp(state.id));
    record('刷新恢复','无需重新上传恢复会话和结果','相同会话编号与结果表已恢复');
    const invalidation=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/invalidate'));
    await page.locator('#row-meaning').fill('修改后的每行含义');await invalidation;
    const stale=await context.request.get(`${base}/api/sessions/${state.id}/download`);assert.equal(stale.status(),409);assert.equal(await page.locator('#downloads').isHidden(),true);
    record('编辑使旧下载失效','修改已有方案，前端停止旧展示，服务端下载409','浏览器输入事件已触发失效；下载409');
    await context.close();context=await browser.newContext({acceptDownloads:true});page=await context.newPage();
    await upload(page,'03_exact_duplicates');
    state=await configure(page,{A:{客户号:'对象编号',联系人:'姓名',联系电话:'电话'},B:{编号:'对象编号',联络人:'姓名',手机号:'电话'}},['对象编号'],{dedupe:'exact'});
    state=await execute(page);assert.equal(state.result.counts.deleted_copies,1);assert.equal(state.result.counts.source_records_covered,5);await verifyDownload(page,context,state);
    record('浏览器去重','确认后只删除1冗余副本，来源覆盖5，导出与预览相同',state.result.counts);
    await context.close();context=await browser.newContext({acceptDownloads:true});page=await context.newPage();
    await upload(page,'07_conflicts');state=await configure(page,{},['编号']);state=await execute(page);
    assert.equal(state.result.counts.pending_output_rows,2);await verifyDownload(page,context,state);
    const originalId=state.id;state=await configure(page,{A:{城市:'平台A城市'},B:{城市:'平台B城市'}},['编号']);state=await execute(page);
    assert.equal(state.id,originalId);assert.equal(state.result.counts.output_rows,2);assert.equal(state.result.records[0].values.平台A城市,'上海');assert.equal(state.result.records[0].values.平台B城市,'北京');await verifyDownload(page,context,state);
    record('浏览器冲突续算','先保留冲突2行并处理正常记录；修改映射后无需重传，保留两侧城市继续','同一会话成功续算至2行，双方值均保留，CSV与预览一致');
    await context.close();context=await browser.newContext();page=await context.newPage();
    state=await upload(page,'08_bad_rows');assert.equal(state.files[0].status,'partial');assert.equal(await page.locator('#execute-btn').isDisabled(),true);assert.match(await page.locator('#read-content').textContent(),/期望2列，实际3列/);
    record('浏览器坏行反馈','展示实际有效2条、坏行1条及第3行错误，禁止执行','行数与原值核对通过；坏行原因可见，按钮禁用');
    await context.close();context=await browser.newContext({acceptDownloads:true});page=await context.newPage();
    state=await upload(page,'09_formats','multiline.csv','multiline.csv');state=await configure(page,{},['来源记录']);state=await execute(page);await verifyDownload(page,context,state);
    record('多行字段/元数据列名','原字段名为来源记录也正确展示；CRLF原值、0及导出一致','DOM与下载逐字段一致，来源列未覆盖业务列');
    await context.close();context=await browser.newContext();page=await context.newPage();
    state=await upload(page,'09_formats','gb18030.csv','gb18030.csv');assert.match(await page.locator('#read-content').textContent(),/gb18030/);assert.match(await page.locator('#read-content').textContent(),/分号/);
    record('编码与分隔符修复可见','实际GB18030、分号方式与中文原值均显示','两侧数据逐字段一致；修复方式清晰显示');
    assert.deepEqual(errors,[]);await context.close();
  }catch(error){checks.push({name:'browser_error',status:'failed',actual:error.stack});console.error(error);process.exitCode=1;}
  finally{if(browser)await browser.close();fs.writeFileSync(path.join(output,'browser_acceptance.json'),JSON.stringify({url:base,checks},null,2));}
})();
