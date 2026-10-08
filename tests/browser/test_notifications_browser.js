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
async function open(page){await page.click('#notificationsOpen');await page.waitForFunction(()=>document.querySelectorAll('.notification-channel').length===4)}
async function main(){
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'specagent-notifications-')),config=path.join(temp,'specagent.yaml'),notify=path.join(temp,'notifications.json'),base='http://127.0.0.1:'+await port();
  fs.mkdirSync(path.join(temp,'specs'));fs.copyFileSync(path.join(root,'examples/fincare-agent/specs/behavior.yaml'),path.join(temp,'specs/behavior.yaml'));fs.writeFileSync(config,'project: accounts-demo\nadapter:\n  type: demo\n  variant: patched\nspec: specs/behavior.yaml\n');
  const channels=[{id:'team-hook',name:'Release feed',kind:'webhook',url:'https://fixture.invalid/private',secret_env:'NOTIFY_FIXTURE_AUTH'},{id:'qa-mail',name:'QA mailbox',kind:'email',host:'smtp.fixture.invalid',sender:'bot@fixture.invalid',recipients:['qa@fixture.invalid']},{id:'review-pr',name:'Review thread',kind:'pr_comment',repository:'fixture/example',pull_number:123,token_env:'NOTIFY_FIXTURE_AUTH'},{id:'failed-hook',name:'Delivery recovery',kind:'webhook',url:'https://fixture.invalid/failed'}];fs.writeFileSync(notify,JSON.stringify({channels}));
  const env={...process.env,SPECAGENT_SKIP_DOTENV:'1',SPECAGENT_AUTH_MODE:'multiuser',SPECAGENT_DB:path.join(temp,'notifications.db'),SPECAGENT_PROJECT_CONFIG:config,SPECAGENT_NOTIFICATION_CONFIG:notify,NOTIFY_FIXTURE_AUTH:'fake-private-notification-credential',SPECAGENT_API_TOKEN:'',OPENAI_API_KEY:'',OPENAI_BASE_URL:'',OPENAI_MODEL:'',SPECAGENT_AGENT_MODEL:'',SPECAGENT_AGENT_API_INSECURE:'',TARGET_AGENT_URL:''};
  const server=spawn(process.env.SPECAGENT_PYTHON||'python',['-m','uvicorn','tests.browser.notification_server:app','--host','127.0.0.1','--port',new URL(base).port,'--log-level','error'],{cwd:root,env,stdio:['ignore','pipe','pipe']});server.stdout.on('data',()=>{});server.stderr.on('data',data=>process.stderr.write(data));
  let browser;
  try{
    let ready=false;for(let i=0;i<120;i++){try{if((await fetch(base+'/api/health')).ok){ready=true;break}}catch{}await wait(250)}if(!ready)throw new Error('private notification server startup timeout');
    browser=await launch();const adminContext=await browser.newContext({viewport:{width:1440,height:1100}}),viewerContext=await browser.newContext(),editorContext=await browser.newContext();
    const admin=await adminContext.newPage(),viewer=await viewerContext.newPage(),editor=await editorContext.newPage(),errors=[];
    for(const page of [admin,viewer,editor])page.on('pageerror',e=>errors.push(e.message));
    await login(admin,base,'admin-demo');await admin.selectOption('#languageSelect','en');await open(admin);await admin.waitForFunction(()=>document.getElementById('notificationTitle').textContent==='Notifications');
    check('English dialog lists all three notification types',/Email/.test(await admin.textContent('#notificationChannels'))&&/PR comment/.test(await admin.textContent('#notificationChannels'))&&/Webhook/.test(await admin.textContent('#notificationChannels')));
    check('all channels start disabled',await admin.locator('.notification-channel button').filter({hasText:'Enable channel'}).count()===4);
    check('opening notifications never sends',(await (await fetch(base+'/__test/notification-attempts')).json()).length===0);
    check('browser DOM never contains destinations or credentials',!/(fixture\.invalid|fake-private-notification-credential|NOTIFY_FIXTURE_AUTH)/.test(await admin.textContent('#notificationDialog')));
    check('empty runs keep preview disabled',await admin.locator('#notificationPrepare').isDisabled());
    await admin.locator('.notification-channel').filter({hasText:'Release feed'}).locator('button').click();await admin.waitForFunction(()=>document.getElementById('notificationChannel').options.length===1);
    check('enabling a channel does not send',(await (await fetch(base+'/__test/notification-attempts')).json()).length===0);
    await admin.keyboard.press('Escape');await admin.waitForFunction(()=>!document.getElementById('notificationDialog').open);
    check('closing returns focus to the notification action',await admin.evaluate(()=>document.activeElement.id==='notificationsOpen'));
    await login(editor,base,'editor-demo');await editor.click('#runBtn');await editor.waitForFunction(()=>/^audit complete/.test(document.getElementById('status').textContent),{timeout:30000});
    await open(editor);await editor.waitForFunction(()=>!document.getElementById('notificationPrepare').disabled);
    check('editor can preview but cannot configure channels',await editor.locator('#notificationChannels button:enabled').count()===0&&await editor.locator('#notificationPrepare').isEnabled());
    await editor.click('#notificationPrepare');await editor.waitForFunction(()=>!document.getElementById('notificationPreview').hidden);
    check('preview has summary counts without test evidence',/"passed"/.test(await editor.textContent('#notificationContent'))&&!/trace|response|label|spec|credential/.test(await editor.textContent('#notificationContent')));
    check('preview persists without delivery',(await (await fetch(base+'/__test/notification-attempts')).json()).length===0);
    check('explicit checkbox is required',await editor.locator('#notificationSend').isDisabled());
    await editor.check('#notificationConfirm');check('checking consent enables confirmation',await editor.locator('#notificationSend').isEnabled());
    await editor.selectOption('#notificationRun',await editor.inputValue('#notificationRun'));check('changing selection invalidates consent',await editor.locator('#notificationPreview').isHidden()&&await editor.locator('#notificationSend').isDisabled());
    await editor.click('#notificationPrepare');await editor.waitForFunction(()=>!document.getElementById('notificationPreview').hidden);await editor.check('#notificationConfirm');await editor.click('#notificationSend');
    await editor.waitForFunction(()=>document.getElementById('notificationStatus').textContent==='通知已发送。');
    check('confirmed delivery sends once and records actual account',(await (await fetch(base+'/__test/notification-attempts')).json()).length===1&&/editor-demo/.test(await editor.textContent('#notificationHistory'))&&/已发送/.test(await editor.textContent('#notificationHistory')));
    check('confirmation clears after sending',await editor.locator('#notificationPreview').isHidden()&&await editor.locator('#notificationSend').isDisabled());
    await login(viewer,base,'viewer-demo');await open(viewer);await viewer.waitForFunction(()=>/不能发送/.test(document.getElementById('notificationStatus').textContent));
    check('viewer reads history with every send and configuration control disabled',await viewer.locator('#notificationPrepare').isDisabled()&&await viewer.locator('#notificationChannels button:enabled').count()===0&&/editor-demo/.test(await viewer.textContent('#notificationHistory')));
    await open(admin);await admin.locator('.notification-channel').filter({hasText:'Delivery recovery'}).locator('button').click();await admin.waitForFunction(()=>document.getElementById('notificationChannel').options.length===2);
    await admin.selectOption('#notificationChannel','failed-hook');await admin.click('#notificationPrepare');await admin.waitForFunction(()=>!document.getElementById('notificationPreview').hidden);await admin.check('#notificationConfirm');await admin.click('#notificationSend');
    await admin.waitForFunction(()=>/Delivery failed and may have arrived/.test(document.getElementById('notificationStatus').textContent));
    check('failed fake delivery warns about ambiguity and does not retry',(await (await fetch(base+'/__test/notification-attempts')).json()).length===2&&/Failed; may have arrived/.test(await admin.textContent('#notificationHistory')));
    await admin.locator('.notification-channel').filter({hasText:'Release feed'}).locator('button').click();await admin.waitForFunction(()=>document.getElementById('notificationChannel').options.length===1);
    check('disabling a channel updates available send choices',await admin.locator('#notificationChannel option[value="team-hook"]').count()===0);
    await admin.setViewportSize({width:390,height:844});check('narrow notification dialog fits the viewport',await admin.locator('#notificationDialog').evaluate(n=>n.scrollWidth<=n.clientWidth&&n.getBoundingClientRect().right<=innerWidth));
    await admin.locator('#notificationNext').focus();await admin.locator('#notificationClose').focus();await admin.keyboard.press('Shift+Tab');check('notification modal cycles focus',await admin.evaluate(()=>document.getElementById('notificationDialog').contains(document.activeElement)&&document.activeElement.id!=='notificationClose'));
    await admin.setViewportSize({width:1440,height:1200});
    if(process.env.SPECAGENT_NOTIFICATIONS_SCREENSHOT)await admin.locator('#notificationDialog').screenshot({path:process.env.SPECAGENT_NOTIFICATIONS_SCREENSHOT,animations:'disabled'});
    await admin.keyboard.press('Escape');await admin.selectOption('#languageSelect','zh-CN');await open(admin);await admin.waitForFunction(()=>document.getElementById('notificationTitle').textContent==='通知');check('Chinese translates loaded delivery states and channel labels',/发送失败，可能已送达/.test(await admin.textContent('#notificationHistory'))&&/关闭渠道/.test(await admin.textContent('#notificationChannels')));
    check('notification browser flow has no uncaught errors',errors.length===0);
  }catch(e){console.error(e.stack);check('notification browser run completed',false)}
  finally{if(browser)await browser.close().catch(()=>{});server.kill();await new Promise(resolve=>{if(server.exitCode!==null)return resolve();const timer=setTimeout(resolve,5000);server.once('exit',()=>{clearTimeout(timer);resolve()})});const resolved=path.resolve(temp);if(resolved.startsWith(path.resolve(os.tmpdir())+path.sep)&&path.basename(resolved).startsWith('specagent-notifications-'))try{fs.rmSync(resolved,{recursive:true,force:true,maxRetries:3,retryDelay:200})}catch{}}
  console.log(results.filter(Boolean).length+' passed, '+results.filter(x=>!x).length+' failed (notifications: '+results.length+' checks)');process.exit(results.every(Boolean)?0:1);
}
main().catch(e=>{console.error(e.message);process.exit(3)});
