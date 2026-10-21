// Read-only smoke of the actual teacher service and a saved legacy workflow. No POST requests.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const root=path.resolve(__dirname,'..'),base='http://127.0.0.1:8765';
(async()=>{let browser;try{
 const previous=JSON.parse(fs.readFileSync(path.join(root,'work/pre-semantic-restart-state-hashes.json'),'utf8').replace(/^\uFEFF/,''));
 const paths=Object.keys(previous),preferred=paths.find(p=>p.includes('38bfb4bbab0246a2859609a4aefbf04c'))||paths[0],id=path.basename(path.dirname(preferred));
 browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 const context=await browser.newContext();await context.addInitScript(id=>{localStorage.setItem('two-stage-run',id);},id);
 const page=await context.newPage(),errors=[],posts=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()==='POST')posts.push(r.url());});
 await page.goto(base+'/pipeline');await page.waitForFunction(()=>!document.querySelector('#pipeline-task').disabled&&document.querySelector('#pipeline-id').textContent.includes('版'));
 assert.deepEqual(posts,[]);assert.deepEqual(errors,[]);assert.equal(await page.locator('#send-instruction').isVisible(),true);assert.equal(await page.locator('#rules-section').evaluate(n=>n.open),false);
 const state=await (await page.request.get(base+'/api/pipelines/'+id)).json();assert.equal(state.id,id);assert.ok(state.instructions.length);
 assert.deepEqual(paths.filter(p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex').toUpperCase()!==previous[p]),[]);
 const report={url:base+'/pipeline',restored_workflow:id,phase:state.phase,revision:state.revision,workflow_files_checked:paths.length,workflow_files_unchanged:true,post_requests:posts.length,page_errors:errors};
 fs.writeFileSync(path.join(root,'outputs/semantic_live_smoke.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }catch(e){console.error(e);process.exitCode=1;}finally{if(browser)await browser.close();}})();
