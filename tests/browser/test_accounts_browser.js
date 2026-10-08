'use strict';
const fs=require('fs'),path=require('path'),os=require('os'),net=require('net');
const {spawn,spawnSync}=require('child_process'),root=path.resolve(__dirname,'../..');
let chromium;for(const base of ['promo/node_modules/playwright-core','node_modules/playwright-core']){try{chromium=require(path.join(root,base)).chromium;break}catch(e){if(e.code!=='MODULE_NOT_FOUND')throw e}}
if(!chromium){console.error('SKIP: playwright-core unavailable');process.exit(2)}
const results=[],wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function check(name,ok){results.push(!!ok);console.log((ok?'PASS':'FAIL')+'  '+name)}
async function port(){return new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p))})})}
async function launch(){try{return await chromium.launch({channel:'chrome',headless:true})}catch{return chromium.launch({headless:true})}}
async function login(page,base,name){await page.goto(base);await page.waitForFunction(()=>document.getElementById('loginDialog').open);await page.fill('#loginUsername',name);await page.fill('#loginPassword','browser-fixture-password-2026');await page.click('#loginSubmit');await page.waitForFunction(()=>document.getElementById('accountName').textContent.length>0);await page.waitForFunction(()=>document.querySelector('#projectsBody button[data-project-id]'))}
async function main(){
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'specagent-accounts-')),config=path.join(temp,'specagent.yaml'),base='http://127.0.0.1:'+await port();
  fs.mkdirSync(path.join(temp,'specs'));fs.copyFileSync(path.join(root,'examples/fincare-agent/specs/behavior.yaml'),path.join(temp,'specs/behavior.yaml'));fs.writeFileSync(config,'project: accounts-demo\nadapter:\n  type: demo\n  variant: patched\nspec: specs/behavior.yaml\n');
  const env={...process.env,SPECAGENT_SKIP_DOTENV:'1',SPECAGENT_AUTH_MODE:'multiuser',SPECAGENT_DB:path.join(temp,'accounts.db'),SPECAGENT_PROJECT_CONFIG:config,SPECAGENT_API_TOKEN:'ignored-browser-fixture',OPENAI_API_KEY:'',OPENAI_BASE_URL:'',OPENAI_MODEL:'',SPECAGENT_AGENT_MODEL:'',SPECAGENT_AGENT_API_INSECURE:'',TARGET_AGENT_URL:''},python=process.env.SPECAGENT_PYTHON||'python';
  const seed=spawnSync(python,['-m','tests.browser.seed_accounts'],{cwd:root,env,encoding:'utf8'});
  if(seed.status!==0){console.error(seed.stderr);process.exit(3)}
  const server=spawn(python,['-m','uvicorn','app.main:app','--host','127.0.0.1','--port',new URL(base).port,'--log-level','error'],{cwd:root,env,stdio:['ignore','pipe','pipe']});server.stdout.on('data',()=>{});server.stderr.on('data',()=>{});
  let browser;
  try{
    let ready=false;for(let i=0;i<120;i++){try{if((await fetch(base+'/api/health')).ok){ready=true;break}}catch{}await wait(250)}if(!ready)throw new Error('private server startup timeout');
    browser=await launch();const adminContext=await browser.newContext({viewport:{width:1400,height:1000}}),viewerContext=await browser.newContext(),editorContext=await browser.newContext();
    const admin=await adminContext.newPage(),viewer=await viewerContext.newPage(),editor=await editorContext.newPage(),errors=[];
    for(const page of [admin,viewer,editor])page.on('pageerror',e=>errors.push(e.message));
    await admin.goto(base);await admin.waitForFunction(()=>document.getElementById('loginDialog').open);
    check('anonymous visitors see login and no protected dashboard',await admin.locator('#mainContent').isHidden()&&await admin.locator('#tokenBox').isHidden());
    await admin.keyboard.press('Escape');check('login cannot be dismissed to reveal protected content',await admin.locator('#loginDialog').evaluate(n=>n.open));
    await admin.click('#loginLanguage');await admin.waitForFunction(()=>document.documentElement.lang==='en');
    check('login language switches without authentication',await admin.textContent('#loginTitle')==='Sign in to the dashboard');
    await admin.setViewportSize({width:390,height:844});check('narrow login fits the viewport',await admin.locator('#loginDialog').evaluate(n=>n.scrollWidth<=n.clientWidth&&n.getBoundingClientRect().right<=innerWidth));
    await admin.locator('#loginSubmit').focus();await admin.keyboard.press('Tab');check('login keyboard focus cycles',await admin.evaluate(()=>document.activeElement.id==='loginLanguage'));
    if(process.env.SPECAGENT_ACCOUNTS_LOGIN_SCREENSHOT)await admin.locator('#loginDialog').screenshot({path:process.env.SPECAGENT_ACCOUNTS_LOGIN_SCREENSHOT});
    await admin.fill('#loginUsername','admin-demo');await admin.fill('#loginPassword','incorrect-fixture');await admin.click('#loginSubmit');await admin.waitForFunction(()=>document.getElementById('loginStatus').textContent==='Incorrect username or password.');
    check('failed login clears the password and stays private',await admin.inputValue('#loginPassword')===''&&await admin.locator('#mainContent').isHidden());
    await admin.setViewportSize({width:1400,height:1000});await login(admin,base,'admin-demo');
    await admin.waitForFunction(()=>document.getElementById('accountRole').textContent==='Administrator');
    check('admin sign-in restores the language preference',await admin.getAttribute('html','lang')==='en');
    check('session is HttpOnly strict and absent from JavaScript', (await adminContext.cookies()).some(c=>c.name==='specagent_session'&&c.httpOnly&&c.sameSite==='Strict')&&await admin.evaluate(()=>!document.cookie.includes('specagent_session')));
    check('credentials and CSRF are not persisted in web storage',await admin.evaluate(()=>!Object.values({...localStorage,...sessionStorage}).some(v=>v===accountUI.csrf||v.includes('browser-fixture-password'))));
    check('admin sees both project metadata records',await admin.locator('#projectsBody button[data-project-id]').count()===2);
    await login(editor,base,'editor-demo');await login(viewer,base,'viewer-demo');
    check('viewer sees only assigned projects',await viewer.locator('#projectsBody button[data-project-id]').count()===1&&!(await viewer.textContent('#projectsBody')).includes('Cedar'));
    const viewerControls={run:await viewer.locator('#runBtn').isDisabled(),tools:await viewer.locator('#toolsControls').evaluate(n=>n.disabled)&&await viewer.locator('#validateBtn').isDisabled(),create:await viewer.locator('#projectCreate').isHidden(),agent:await viewer.locator('#agentSection').isHidden()};
    check('viewer has read access and disabled write controls',Object.values(viewerControls).every(Boolean));if(!Object.values(viewerControls).every(Boolean))console.error(JSON.stringify(viewerControls));
    check('nonadmin settings and membership controls are restricted',await viewer.locator('#settingsOpen').isDisabled()&&await viewer.locator('#accessOpen').isHidden());
    await editor.waitForFunction(()=>!document.getElementById('toolsControls').disabled);
    check('editor can use its configured project',await editor.locator('#runBtn').isEnabled()&&await editor.locator('#projectRunBtn').isEnabled());
    await editor.click('#runBtn');await editor.waitForFunction(()=>/^audit complete/.test(document.getElementById('status').textContent),{timeout:30000});
    const run=await editor.evaluate(()=>window.currentRunSummary.id);await viewer.reload();await viewer.waitForFunction(()=>document.querySelector('#runsBody tr[data-run-id]'));
    check('viewer can open completed evidence',await viewer.evaluate(id=>apiJson('/api/runs/'+id).then(d=>d.id===id),run));
    check('viewer baseline and management buttons remain disabled after refresh',await viewer.locator('#runsBody [data-project-write]').count()>=3&&await viewer.locator('#runsBody [data-project-write]:enabled').count()===0);
    check('forged viewer write is rejected by the server',await viewer.evaluate(id=>api('/api/runs/'+id+'/baseline',{method:'POST'}).then(r=>r.status===404),run));
    check('mutation without CSRF is rejected',await editor.evaluate(id=>fetch('/api/runs/'+id+'/baseline',{method:'POST'}).then(r=>r.status===403),run));
    await editor.evaluate(id=>apiJson('/api/runs/'+id+'/baseline',{method:'POST'}),run);
    await viewer.evaluate(()=>loadProjects());check('account audit displays the actual editor username',/editor-demo/.test(await viewer.textContent('#baselineHistoryBody')));
    const sid=await editor.evaluate(()=>apiJson('/api/agent/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(d=>d.session_id));
    check('another account cannot use an editor Agent session',await admin.evaluate(id=>api('/api/agent/sessions/'+id+'/messages',{method:'POST',headers:{'Content-Type':'application/json'},body:'{"text":"private"}'}).then(r=>r.status===404),sid));
    await admin.click('#accessOpen');await admin.waitForFunction(()=>document.querySelectorAll('#accessBody tr').length===2);
    check('administrator membership dialog translates loaded roles',/Viewer/.test(await admin.textContent('#accessBody'))&&/Editor/.test(await admin.textContent('#accessBody')));
    if(process.env.SPECAGENT_ACCOUNTS_ACCESS_SCREENSHOT)await admin.locator('#accessDialog').screenshot({path:process.env.SPECAGENT_ACCOUNTS_ACCESS_SCREENSHOT});
    await admin.setViewportSize({width:390,height:844});check('narrow membership dialog fits the viewport',await admin.locator('#accessDialog').evaluate(n=>n.scrollWidth<=n.clientWidth&&n.getBoundingClientRect().right<=innerWidth));await admin.setViewportSize({width:1400,height:1000});
    const viewerRow=admin.locator('#accessBody tr').filter({hasText:'viewer-demo'});await viewerRow.locator('button').click();await admin.waitForFunction(()=>document.querySelectorAll('#accessBody tr').length===1);
    check('revocation immediately blocks an existing viewer session',await viewer.evaluate(id=>api('/api/runs/'+id).then(r=>r.status===404),run));
    const viewerId=await admin.locator('#accessUser option').filter({hasText:'viewer-demo'}).getAttribute('value');await admin.selectOption('#accessUser',viewerId);await admin.selectOption('#accessRole','editor');await admin.click('#accessSubmit');await admin.waitForFunction(()=>document.querySelectorAll('#accessBody tr').length===2);
    await viewer.reload();await viewer.waitForFunction(()=>document.getElementById('accountRole').textContent==='编辑用户');check('granting editor access is reflected after reload',await viewer.locator('#runBtn').isEnabled());
    await admin.keyboard.press('Escape');check('closing membership dialog restores focus',await admin.evaluate(()=>document.activeElement.id==='accessOpen'));
    const oldCookie=(await editorContext.cookies()).find(c=>c.name==='specagent_session');await editor.click('#logoutButton');await editor.waitForFunction(()=>document.getElementById('loginDialog').open);
    check('logout clears previously loaded protected evidence',await editor.locator('#mainContent').isHidden()&&await editor.locator('#accountBox').isHidden()&&!(await editor.textContent('#tests')).includes('test_case'));
    check('logout invalidates the cookie on the server', (await fetch(base+'/api/auth/me',{headers:{Cookie:'specagent_session='+oldCookie.value}})).status===401);
    await login(editor,base,'editor-demo');check('relogin cannot resume the previous Agent session',await editor.evaluate(id=>api('/api/agent/sessions/'+id+'/messages',{method:'POST',headers:{'Content-Type':'application/json'},body:'{"text":"old"}'}).then(r=>r.status===404),sid));
    check('account browser flow has no uncaught errors',errors.length===0);
  }catch(e){console.error(e.stack);check('account browser run completed',false)}
  finally{if(browser)await browser.close().catch(()=>{});server.kill();await new Promise(resolve=>{if(server.exitCode!==null)return resolve();const timer=setTimeout(resolve,5000);server.once('exit',()=>{clearTimeout(timer);resolve()})});const resolved=path.resolve(temp);if(resolved.startsWith(path.resolve(os.tmpdir())+path.sep)&&path.basename(resolved).startsWith('specagent-accounts-'))try{fs.rmSync(resolved,{recursive:true,force:true,maxRetries:3,retryDelay:200})}catch{}}
  console.log(results.filter(Boolean).length+' passed, '+results.filter(x=>!x).length+' failed (accounts: '+results.length+' checks)');process.exit(results.every(Boolean)?0:1);
}
main().catch(e=>{console.error(e.message);process.exit(3)});
