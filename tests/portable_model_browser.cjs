// Real browser/button/download checks against the independent disabled-model service.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),{spawnSync}=require('node:child_process');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),out=path.join(root,'outputs'),base=process.env.INTEGRATION_TEST_URL||'http://127.0.0.1:8766',checks=[];
function record(name,expected,actual){checks.push({name,expected,actual,status:'passed'});console.log('PASS '+name);}
(async()=>{let browser;try{
 browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const context=await browser.newContext({acceptDownloads:true}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base+'/pipeline');await page.waitForFunction(()=>!document.querySelector('#pipeline-task').disabled);
 await page.locator('.model-settings > summary').click();
 const [response]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/api/model/check'),page.locator('#check-model').click()]);
 const check=await response.json();assert.equal(response.request().postData(),'{}');assert.equal(check.status,'error');assert.equal(check.business_quality_verified,false);assert.equal(check.transport_ok,null);
 assert.match(await page.locator('#model-check-result').textContent(),/业务方案契约：未验证/);assert.equal(await page.locator('input[type="password"]').count(),0);
 record('连接检查按钮','无模型时不伪造连接成功，探测不发送文件或密钥',{status:check.status,code:check.code,body:response.request().postData()});
 await page.locator('#manual-fallback').click();assert.match(await page.locator('#message').textContent(),/先上传CSV/);
 await page.locator('#pipeline-task').fill('以编号关联，原始数据百分制，实验40%，作业60%，未交记0，保留一位小数。');
 await page.locator('#pipeline-files').setInputFiles(['花名册.csv','平台记录.csv'].map(n=>path.join(root,'examples/windows_trial',n)));
 const [created]=await Promise.all([page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/api/pipelines'),page.locator('#start-pipeline').click()]);
 const s=await created.json();assert.equal(s.phase,'complete',JSON.stringify(s.issues));await page.locator('#dws-table').waitFor();
 assert.deepEqual(s.stage2.rows.map(r=>r.总评),['86.0','54.0','32.0']);await page.locator('#manual-fallback').click();assert.equal(await page.locator('#rules-section').evaluate(n=>n.open),true);
 const dom=await page.locator('#dws-table tbody tr').evaluateAll(rows=>rows.map(r=>[...r.querySelectorAll('td')].map(c=>c.dataset.raw)));assert.deepEqual(dom,s.stage2.rows.map(r=>s.stage2.columns.map(c=>r[c])));
 const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#download-dws').click()]);const target=path.join(out,'windows-trial-browser-result.csv');await download.saveAs(target);
 const parsed=spawnSync('python',['-X','utf8','-c',"import csv,json,sys;print(json.dumps(list(csv.DictReader(open(sys.argv[1],encoding='utf-8-sig',newline=''))),ensure_ascii=False))",target],{encoding:'utf8'});assert.equal(parsed.status,0,parsed.stderr);assert.deepEqual(JSON.parse(parsed.stdout),s.stage2.rows);
 record('手动回退与分发示例','无模型完成示例，中文表单可展开，0/前导零/未交及下载一致',{totals:s.stage2.rows.map(r=>r.总评),csv:target});
 await page.reload();await page.waitForFunction(()=>!document.querySelector('#pipeline-task').disabled);await page.locator('.model-settings > summary').click();assert.match(await page.locator('#model-check-result').textContent(),/业务方案契约：未验证/);assert.equal(await page.locator('#dws-table').count(),1);assert.deepEqual(errors,[]);
 record('刷新恢复','结果与连接检查信息恢复，页面无脚本错误',{phase:s.phase,page_errors:errors});
 fs.writeFileSync(path.join(out,'portable_model_browser.json'),JSON.stringify({checks,page_errors:errors,external_real_llm_run:false},null,2));
 }catch(e){fs.writeFileSync(path.join(out,'portable_model_browser.json'),JSON.stringify({checks,error:e.stack},null,2));console.error(e);process.exitCode=1;}finally{if(browser)await browser.close();}})();
