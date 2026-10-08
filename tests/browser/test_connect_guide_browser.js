'use strict';
const fs=require('fs'),path=require('path'),os=require('os'),net=require('net');
const {spawn}=require('child_process'),root=path.resolve(__dirname,'../..');
let chromium;for(const base of ['promo/node_modules/playwright-core','node_modules/playwright-core']){try{chromium=require(path.join(root,base)).chromium;break}catch(e){if(e.code!=='MODULE_NOT_FOUND')throw e}}
if(!chromium){console.error('SKIP: playwright-core unavailable');process.exit(2)}
const results=[],wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function check(name,ok){results.push(!!ok);console.log((ok?'PASS':'FAIL')+'  '+name)}
async function port(){return new Promise(resolve=>{const s=net.createServer();s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p))})})}
async function launch(){try{return await chromium.launch({channel:'chrome',headless:true})}catch{return chromium.launch({headless:true})}}
async function login(page,base,name){await page.goto(base);await page.waitForFunction(()=>document.getElementById('loginDialog').open);await page.fill('#loginUsername',name);await page.fill('#loginPassword','browser-fixture-password-2026');await page.click('#loginSubmit');await page.waitForFunction(()=>document.getElementById('accountName').textContent.length>0);await page.waitForFunction(()=>document.querySelector('#projectsBody button[data-project-id]'))}
async function select(page,adapter){await page.selectOption('#connectAdapter',adapter);await page.waitForFunction(a=>document.getElementById('connectConfig').textContent.includes('type: '+a)&&!document.getElementById('connectConfigCopy').disabled,adapter)}
async function main(){
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'specagent-guide-')),config=path.join(temp,'specagent.yaml'),spec=path.join(temp,'specs/behavior.yaml'),base='http://127.0.0.1:'+await port();
  fs.mkdirSync(path.join(temp,'specs'));fs.copyFileSync(path.join(root,'examples/fincare-agent/specs/behavior.yaml'),spec);fs.writeFileSync(config,'project: accounts-demo\nadapter:\n  type: demo\n  variant: patched\nspec: specs/behavior.yaml\n');
  const original=[fs.readFileSync(config),fs.readFileSync(spec)],env={...process.env,SPECAGENT_SKIP_DOTENV:'1',SPECAGENT_AUTH_MODE:'multiuser',SPECAGENT_DB:path.join(temp,'guide.db'),SPECAGENT_PROJECT_CONFIG:config,SPECAGENT_NOTIFICATION_CONFIG:'',SPECAGENT_API_TOKEN:'',OPENAI_API_KEY:'',OPENAI_BASE_URL:'',OPENAI_MODEL:'',SPECAGENT_AGENT_MODEL:'',SPECAGENT_AGENT_API_INSECURE:'',TARGET_AGENT_URL:'fake-private-guide-destination'};
  const server=spawn(process.env.SPECAGENT_PYTHON||'python',['-m','uvicorn','tests.browser.guide_server:app','--host','127.0.0.1','--port',new URL(base).port,'--log-level','error'],{cwd:root,env,stdio:['ignore','pipe','pipe']});server.stdout.on('data',()=>{});server.stderr.on('data',data=>process.stderr.write(data));
  let browser;
  try{
    let ready=false;for(let i=0;i<120;i++){try{if((await fetch(base+'/api/health')).ok){ready=true;break}}catch{}await wait(250)}if(!ready)throw new Error('private guide server startup timeout');
    browser=await launch();const context=await browser.newContext({viewport:{width:1440,height:1200},permissions:['clipboard-read','clipboard-write']}),viewerContext=await browser.newContext(),page=await context.newPage(),viewer=await viewerContext.newPage(),errors=[];
    for(const p of [page,viewer])p.on('pageerror',e=>errors.push(e.message));
    await login(page,base,'admin-demo');await page.selectOption('#languageSelect','en');await page.click('#connectOpen');await page.waitForFunction(()=>!document.getElementById('connectConfigCopy').disabled);
    check('guide lists the four existing CLI adapters',await page.locator('#connectAdapter option').count()===4);
    check('guide opens with the demo configuration and rule scaffold',/type: demo/.test(await page.textContent('#connectConfig'))&&/LARGE_REFUND_APPROVAL/.test(await page.textContent('#connectSpec')));
    check('opening never triggers target validation',(await (await fetch(base+'/__test/guide-stats')).json()).validations===0);
    check('English guide explains that it does not write files',/does not write files/.test(await page.textContent('#connectDialog')));
    await select(page,'http');check('HTTP guide lists names without exposing values',/TARGET_AGENT_URL/.test(await page.textContent('#connectEnvironment'))&&!/fake-private-guide-destination/.test(await page.textContent('#connectDialog')));
    check('HTTP template provides its equivalent init command',await page.textContent('#connectCommand')==='specagent init --adapter http');
    const configText=await page.textContent('#connectConfig');await page.click('#connectConfigCopy');await page.waitForFunction(()=>document.getElementById('connectStatus').textContent.includes('Template copied'));check('configuration copy matches displayed template',(await page.evaluate(()=>navigator.clipboard.readText())).replace(/\r\n/g,'\n')===configText);
    const specText=await page.textContent('#connectSpec');await page.click('#connectSpecCopy');await page.waitForFunction(()=>document.getElementById('connectStatus').textContent.includes('Template copied'));check('rule copy preserves Chinese source even in English mode',(await page.evaluate(()=>navigator.clipboard.readText())).replace(/\r\n/g,'\n')===specText&&/退款超过500元/.test(specText));
    await select(page,'openai');check('OpenAI guide includes required and optional environment names',/OPENAI_API_KEY/.test(await page.textContent('#connectEnvironment'))&&/OPENAI_BASE_URL/.test(await page.textContent('#connectOptional')));
    const authored=await page.locator('#connectDialog').evaluate(n=>{const w=document.createTreeWalker(n,NodeFilter.SHOW_TEXT);let text='';while(w.nextNode())if(!w.currentNode.parentElement.closest('pre,[data-verbatim]'))text+=w.currentNode.nodeValue;return text});check('English translates every authored guide instruction',!/[\u4e00-\u9fff]/.test(authored));
    let release,started;const gate=new Promise(r=>release=r),requested=new Promise(r=>started=r);
    await page.route('**/api/project/templates/http',async route=>{const response=await route.fetch();started();await gate;await route.fulfill({response})});
    await page.selectOption('#connectAdapter','http');await requested;await select(page,'python');release();await wait(150);
    check('late HTTP template cannot replace the selected Python template',/type: python/.test(await page.textContent('#connectConfig'))&&!/type: http/.test(await page.textContent('#connectConfig')));await page.unroute('**/api/project/templates/http');
    check('switching templates never validates or runs',(await (await fetch(base+'/__test/guide-stats')).json()).validations===0);
    await select(page,'http');await page.click('#connectValidate');await page.waitForFunction(()=>document.getElementById('connectValidation').textContent.includes('demo'));
    check('explicit refresh validates the server demo rather than HTTP template',(await (await fetch(base+'/__test/guide-stats')).json()).validations===1&&/demo/.test(await page.textContent('#connectValidation'))&&/type: http/.test(await page.textContent('#connectConfig')));
    check('validation result uses existing CLI report lines',await page.textContent('#connectValidation')===await page.textContent('#validateResult'));
    check('template and validation leave project files byte unchanged',fs.readFileSync(config).equals(original[0])&&fs.readFileSync(spec).equals(original[1]));
    await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.getElementById('connectDialog').open&&document.getElementById('connectConfig').textContent===''&&document.activeElement.id==='connectOpen');check('closing clears snapshots and returns focus',await page.textContent('#connectConfig')===''&&await page.evaluate(()=>document.activeElement.id==='connectOpen'));
    await login(viewer,base,'viewer-demo');await viewer.click('#connectOpen');await viewer.waitForFunction(()=>!document.getElementById('connectConfigCopy').disabled);check('viewer can read and copy but cannot validate target modules',await viewer.locator('#connectValidate').isDisabled()&&await viewer.locator('#connectConfigCopy').isEnabled());
    await page.selectOption('#languageSelect','zh-CN');await page.click('#connectOpen');await page.waitForFunction(()=>document.getElementById('connectNotes').textContent.includes('目标服务'));
    check('Chinese instructions and raw templates survive language changes',/在本机完成接入/.test(await page.textContent('#connectDialog'))&&await page.textContent('#connectConfig')===configText);
    await page.setViewportSize({width:390,height:844});check('narrow guide fits with stacked templates',await page.locator('#connectDialog').evaluate(n=>n.scrollWidth<=n.clientWidth&&n.getBoundingClientRect().right<=innerWidth));
    await page.locator('#connectClose').focus();await page.keyboard.press('Shift+Tab');check('guide traps keyboard focus',await page.evaluate(()=>document.getElementById('connectDialog').contains(document.activeElement)&&document.activeElement.id!=='connectClose'));
    check('guide exposes labels and focusable raw template areas',await page.locator('#connectDialog[aria-labelledby="connectTitle"]').count()===1&&await page.locator('#connectDialog pre[tabindex="0"][aria-label]').count()===3);
    await page.setViewportSize({width:1440,height:1800});await page.locator('#connectDialog').evaluate(n=>n.scrollTop=0);
    if(process.env.SPECAGENT_GUIDE_SCREENSHOT)await page.locator('#connectDialog').screenshot({path:process.env.SPECAGENT_GUIDE_SCREENSHOT,animations:'disabled'});
    check('guide flow has no uncaught browser errors',errors.length===0);
  }catch(e){console.error(e.stack);check('guide browser flow completed',false)}
  finally{if(browser)await browser.close().catch(()=>{});server.kill();await new Promise(resolve=>{if(server.exitCode!==null)return resolve();const timer=setTimeout(resolve,5000);server.once('exit',()=>{clearTimeout(timer);resolve()})});const resolved=path.resolve(temp);if(resolved.startsWith(path.resolve(os.tmpdir())+path.sep)&&path.basename(resolved).startsWith('specagent-guide-'))try{fs.rmSync(resolved,{recursive:true,force:true,maxRetries:3,retryDelay:200})}catch{}}
  console.log(results.filter(Boolean).length+' passed, '+results.filter(x=>!x).length+' failed (guide: '+results.length+' checks)');process.exit(results.every(Boolean)?0:1);
}
main().catch(e=>{console.error(e.message);process.exit(3)});
