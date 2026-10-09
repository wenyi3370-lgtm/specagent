'use strict';
const fs=require('fs'),path=require('path'),os=require('os'),net=require('net');
const {spawn}=require('child_process');
const root=path.resolve(__dirname,'../..');
let chromium;
for(const base of ['promo/node_modules/playwright-core','node_modules/playwright-core']){
    try{chromium=require(path.join(root,base)).chromium;break}catch(e){if(e.code!=='MODULE_NOT_FOUND')throw e}
}
if(!chromium){console.error('SKIP: playwright-core unavailable');process.exit(2)}
const results=[];
function check(name,ok,detail=''){results.push({name,ok:!!ok,detail});console.log((ok?'PASS':'FAIL')+'  '+name+(detail?'  ['+detail+']':''))}
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function port(){return new Promise((resolve,reject)=>{const server=net.createServer();server.on('error',reject);server.listen(0,'127.0.0.1',()=>{const number=server.address().port;server.close(()=>resolve(number))})})}
async function launch(){try{return await chromium.launch({channel:'chrome',headless:true})}catch{return chromium.launch({headless:true})}}
async function themeContrast(page){return page.evaluate(()=>{
    function rgb(s){return s.match(/\d+(?:\.\d+)?/g).slice(0,3).map(Number)}
    function luminance(c){return c.map(n=>{const x=n/255;return x<=.04045?x/12.92:((x+.055)/1.055)**2.4}).reduce((sum,x,i)=>sum+x*[.2126,.7152,.0722][i],0)}
    function ratio(a,b){const x=luminance(rgb(a)),y=luminance(rgb(b));return (Math.max(x,y)+.05)/(Math.min(x,y)+.05)}
    const s=getComputedStyle(document.documentElement),bg=s.getPropertyValue('--panel').trim();
    const canvas=document.createElement('canvas'),ctx=canvas.getContext('2d');function color(c){ctx.fillStyle=c;const hex=ctx.fillStyle;return hex.startsWith('#')?'rgb('+[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)).join(',')+')':hex}
    return ['--text','--muted','--green','--red','--amber','--blue'].map(key=>({key,ratio:ratio(color(s.getPropertyValue(key).trim()),color(bg))}));
})}
async function main(){
    const temp=fs.mkdtempSync(path.join(os.tmpdir(),'specagent-preferences-')),db=path.join(temp,'prefs.db'),config=path.join(temp,'specagent.yaml'),serverPort=await port(),base='http://127.0.0.1:'+serverPort,token='preferences-fake-test-token';
    fs.mkdirSync(path.join(temp,'specs'));fs.copyFileSync(path.join(root,'examples/fincare-agent/specs/behavior.yaml'),path.join(temp,'specs/behavior.yaml'));
    fs.writeFileSync(config,'project: preferences-demo\nadapter:\n  type: demo\n  variant: patched\nspec: specs/behavior.yaml\n');
    const server=spawn(process.env.SPECAGENT_PYTHON||'python',['-m','uvicorn','app.main:app','--host','127.0.0.1','--port',String(serverPort),'--log-level','error'],{cwd:root,env:{...process.env,SPECAGENT_SKIP_DOTENV:'1',SPECAGENT_DB:db,SPECAGENT_PROJECT_CONFIG:config,SPECAGENT_API_TOKEN:token,OPENAI_API_KEY:'',OPENAI_BASE_URL:'',OPENAI_MODEL:'behavior-demo-model',SPECAGENT_AGENT_MODEL:'Idle',SPECAGENT_AGENT_API_INSECURE:'',TARGET_AGENT_URL:''},stdio:['ignore','pipe','pipe']});
    server.stdout.on('data',()=>{});server.stderr.on('data',()=>{});
    let browser;
    try{
        let ready=false;for(let i=0;i<120;i++){try{if((await fetch(base+'/api/health')).ok){ready=true;break}}catch{}await wait(250)}if(!ready)throw new Error('temporary server startup timeout');
        const seed=await fetch(base+'/api/projects',{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify({id:'preferences-demo',name:'Preferences demo'})});if(!seed.ok)throw new Error('temporary project creation failed');
        browser=await launch();const context=await browser.newContext({viewport:{width:1400,height:1100},permissions:['clipboard-read','clipboard-write']}),page=await context.newPage(),errors=[];
        page.on('pageerror',e=>{errors.push(e.message);console.error('page error:',e.message)});
        await page.goto(base);await page.fill('#tokenInput',token);await page.click('#tokenSet');
        await page.waitForFunction(()=>!document.getElementById('toolsControls').disabled).catch(async error=>{console.error(await page.textContent('#projectBarText'));throw error});
        await page.waitForFunction(()=>document.querySelector('#projectsBody button[data-project-id]'));
        check('preferences preserve the original initial labels and dark theme',await page.textContent('#runBtn')==='Run behavior audit'&&await page.getAttribute('html','data-theme')==='dark'&&await page.inputValue('#languageSelect')==='original');
        await page.click('#runBtn');await page.waitForFunction(()=>/^audit complete/.test(document.getElementById('status').textContent),{timeout:30000});
        const source=await page.inputValue('#specText'),trace=await page.locator('#tests .trace').first().textContent(),rule=await page.textContent('#rules'),runId=await page.evaluate(()=>window.currentRunSummary.id);
        // An arbitrary project name deliberately collides with a translated UI label.
        await page.evaluate(()=>apiJson('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:'named-ui-text',name:'设置与关于',description:'项目配置无效。'})}).then(()=>loadProjects('named-ui-text')));
        await page.click('#settingsOpen');await page.waitForFunction(()=>/读取于/.test(document.getElementById('settingsStatus').textContent));
        const model=await page.locator('#settingsContent [data-verbatim]').filter({hasText:'Idle'}).textContent();
        await page.click('#settingsClose');
        let requests=0;page.on('request',r=>{if(new URL(r.url()).pathname.startsWith('/api/'))requests++});
        await page.selectOption('#languageSelect','en');await page.waitForFunction(()=>document.documentElement.lang==='en'&&document.getElementById('settingsOpen').textContent==='Settings and About');
        check('English changes static navigation forms and accessible names',await page.textContent('#runBtn')==='Run behavior audit'&&await page.textContent('#specCurrent')==='View current YAML'&&await page.getAttribute('#projectSel','aria-label')==='View project history'&&await page.getAttribute('#draftText','placeholder')==='Describe the rules the Agent must follow');
        check('English preserves editable requirements execution evidence and rules',await page.inputValue('#specText')===source&&await page.locator('#tests .trace').first().textContent()===trace&&await page.textContent('#rules')===rule);
        check('project names and descriptions are never translated as UI',await page.locator('#projectsBody button[data-project-id="named-ui-text"]').textContent()==='设置与关于'&&await page.locator('#projectsBody .project-description').filter({hasText:'项目配置无效。'}).count()===1);
        check('mixed project history prose translates while retaining its project name',/Viewing history for 设置与关于/.test(await page.textContent('#projectSelectionInfo'))&&/server target preferences-demo/.test(await page.textContent('#projectSelectionInfo')));
        await page.selectOption('#themeSelect','light');await page.waitForFunction(()=>document.documentElement.dataset.theme==='light');
        check('language and theme changes make no API requests',requests===0,requests);
        check('light theme declares light native controls and light surfaces',await page.evaluate(()=>getComputedStyle(document.documentElement).colorScheme==='light'&&getComputedStyle(document.body).backgroundColor==='rgb(244, 246, 250)'));
        await page.click('#settingsOpen');await page.waitForFunction(()=>/Read at/.test(document.getElementById('settingsStatus').textContent));
        check('English translates asynchronously loaded settings purposes and state',/Models and purposes/.test(await page.textContent('#settingsContent'))&&/No LLM key is configured/.test(await page.textContent('#settingsContent'))&&/configuration snapshot; connection not checked/.test(await page.textContent('#settingsStatus')));
        check('model names remain exact even when they match a UI label',await page.locator('#settingsContent [data-verbatim]').filter({hasText:'Idle'}).textContent()===model);
        const contrast=await themeContrast(page);
        check('light theme body muted and semantic text meet 4.5 contrast',contrast.every(x=>x.ratio>=4.5),JSON.stringify(contrast));
        if(process.env.SPECAGENT_PREFERENCES_SCREENSHOT){await page.setViewportSize({width:1400,height:1550});await page.locator('#settingsDialog').screenshot({path:process.env.SPECAGENT_PREFERENCES_SCREENSHOT,animations:'disabled'})}
        await page.keyboard.press('Escape');await page.waitForFunction(()=>document.activeElement.id==='settingsOpen');
        if(process.env.SPECAGENT_PREFERENCES_DASHBOARD_SCREENSHOT){await page.setViewportSize({width:1400,height:1100});await page.screenshot({path:process.env.SPECAGENT_PREFERENCES_DASHBOARD_SCREENSHOT,animations:'disabled'})}
        await page.selectOption('#languageSelect','zh-CN');await page.waitForFunction(()=>document.documentElement.lang==='zh-CN'&&document.getElementById('runBtn').textContent==='运行行为审查');
        check('Chinese translates original English action labels',await page.textContent('#validateBtn')==='校验'&&await page.textContent('#agentSend')==='发送'&&await page.textContent('#projectRunBtn')==='运行项目测试');
        check('language switching preserves auth and editable state',await page.evaluate(token=>sessionStorage.getItem('specagent_token')===token,token)&&await page.inputValue('#specText')===source);
        await page.reload();await page.waitForFunction(()=>document.getElementById('runBtn').textContent==='运行行为审查'&&!document.getElementById('toolsControls').disabled);
        check('language and theme survive a reload in the same tab',await page.inputValue('#languageSelect')==='zh-CN'&&await page.inputValue('#themeSelect')==='light'&&await page.getAttribute('html','lang')==='zh-CN');
        await page.selectOption('#themeSelect','system');await page.emulateMedia({colorScheme:'dark'});await page.waitForFunction(()=>document.documentElement.dataset.theme==='dark');
        await page.emulateMedia({colorScheme:'light'});await page.waitForFunction(()=>document.documentElement.dataset.theme==='light');
        check('system theme follows OS color scheme changes',await page.inputValue('#themeSelect')==='system');
        await page.selectOption('#themeSelect','dark');await page.emulateMedia({colorScheme:'light'});
        check('explicit theme overrides the system color scheme',await page.getAttribute('html','data-theme')==='dark');
        const darkContrast=await themeContrast(page);check('dark theme body muted and semantic text meet 4.5 contrast',darkContrast.every(x=>x.ratio>=4.5),JSON.stringify(darkContrast));
        await page.emulateMedia({reducedMotion:'reduce'});
        check('reduced motion removes decorative animations',await page.evaluate(()=>{const badge=document.createElement('span');badge.className='badge busy';document.body.append(badge);const name=getComputedStyle(badge,'::before').animationName;badge.remove();return name==='none'}));
        await page.locator('.skip-link').focus();await page.keyboard.press('Enter');
        check('skip link moves keyboard focus to the main landmark',await page.evaluate(()=>document.activeElement.id==='mainContent'&&document.querySelectorAll('main').length===1));
        await page.click('#settingsOpen');await page.waitForFunction(()=>document.getElementById('settingsContent').children.length>0);
        await page.locator('#settingsClose').focus();await page.keyboard.press('Shift+Tab');const last=await page.evaluate(()=>document.activeElement.id==='settingsRefresh');await page.keyboard.press('Tab');
        check('modal focus cycles in the selected language',last&&await page.evaluate(()=>document.activeElement.id==='settingsClose'));
        await page.keyboard.press('Escape');await page.waitForFunction(()=>document.activeElement.id==='settingsOpen');
        check('focused controls have a visible focus outline',await page.locator('#settingsOpen').evaluate(node=>{node.focus();const s=getComputedStyle(node);return s.outlineStyle!=='none'&&parseFloat(s.outlineWidth)>=2}));
        const unlabeled=await page.evaluate(()=>[...document.querySelectorAll('button,input:not([type="hidden"]),select,textarea')].filter(n=>n.getClientRects().length&&!n.disabled&&!n.getAttribute('aria-label')&&!n.getAttribute('aria-labelledby')&&!n.labels?.length&&!n.textContent.trim()).map(n=>n.id||n.tagName));
        check('visible enabled controls have accessible labels',unlabeled.length===0,JSON.stringify(unlabeled));
        check('status regions and table column headers expose semantics',await page.getAttribute('#status','role')==='status'&&await page.getAttribute('#status','aria-live')==='polite'&&await page.locator('thead th:not([scope="col"])').count()===0);
        await page.evaluate(()=>{document.getElementById('status').textContent='audit complete · run locale-fixture'});
        await page.waitForFunction(()=>document.getElementById('status').textContent==='审查完成 · 运行 locale-fixture');
        check('new asynchronous status text follows the selected language',await page.textContent('#status')==='审查完成 · 运行 locale-fixture');
        await page.evaluate(()=>{const pre=document.createElement('pre');pre.id='preferencesEvidence';pre.textContent='设置与关于\nRefresh\n<not markup>';document.getElementById('projectTools').append(pre)});
        await page.waitForFunction(()=>document.getElementById('preferencesEvidence').tabIndex===0);
        check('scrollable evidence has keyboard focus and an accessible label',await page.getAttribute('#preferencesEvidence','aria-label')==='文本内容');
        await page.selectOption('#languageSelect','en');await page.waitForFunction(()=>document.documentElement.lang==='en');
        check('source evidence remains byte-identical across repeated switches',await page.textContent('#preferencesEvidence')==='设置与关于\nRefresh\n<not markup>');
        await page.locator('#preferencesEvidence').evaluate(node=>node.remove());
        await page.setViewportSize({width:390,height:844});await page.click('#settingsOpen');await page.waitForFunction(()=>document.getElementById('settingsContent').children.length>0);
        check('narrow settings and preference controls remain reachable',await page.locator('#settingsDialog').evaluate(d=>d.scrollWidth<=d.clientWidth&&d.getBoundingClientRect().right<=innerWidth));
        await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.getElementById('settingsDialog').open);
        check('narrow dashboard keeps top controls within the viewport',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&['languageSelect','themeSelect','settingsOpen','tokenInput'].every(id=>{const r=document.getElementById(id).getBoundingClientRect();return r.left>=0&&r.right<=innerWidth})),await page.evaluate(()=>JSON.stringify([...document.querySelectorAll('.top,.top-controls,.card,.section,.metrics,.metric')].map(n=>({id:n.id,cls:n.className,right:n.getBoundingClientRect().right,width:n.getBoundingClientRect().width})).filter(n=>n.right>innerWidth))));
        await page.selectOption('#languageSelect','en');await page.waitForFunction(()=>document.documentElement.lang==='en');
        // Inventory untranslated static UI, excluding data and identifiers by design.
        const untranslated=await page.evaluate(()=>{
            const skip='script,style,pre,code,textarea,input,[data-verbatim],.trace,.violation,.bubble,.tc-v,.project-description,button[data-project-id],button[data-run-id],#projectSel option,#toolsRun option,#verifyPreRun option,#agentHistorySelect option,#specVersion option,#specBaseline option,#rules,#tests,#diffBody,#triageResult,#projectSummary,#validateResult,#verifyResult,#projectGate,#runsBody td.mono,.approval-summary,#languageSelect,#projectSelectionInfo';
            const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT),left=[];let n;while((n=walker.nextNode()))if(n.data.trim()&&/[\u4e00-\u9fff]/.test(n.data)&&!n.parentElement.closest(skip))left.push(n.data.trim());return [...new Set(left)];
        });
        check('English covers static and loaded UI prose',untranslated.length===0,JSON.stringify(untranslated));
        // Only the private fixture is modified. Names that match UI keys must stay raw.
        await page.evaluate(()=>{
            window.testFrames=[];window.testRAF=window.requestAnimationFrame;
            window.requestAnimationFrame=callback=>{window.testFrames.push(callback);return 0};
        });
        await page.evaluate(async id=>{await apiJson('/api/runs/'+id+'/label',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({label:'设置与关于'})});await apiJson('/api/runs/'+id+'/baseline',{method:'POST'});await loadProjects('preferences-demo')},runId);
        await page.waitForFunction(()=>document.getElementById('baselineCurrent').textContent.includes('设置与关于'));
        check('baseline label can render before delayed identity translation',await page.evaluate(()=>window.testFrames.length>0&&!/Shared-token user \/ Web/.test(document.getElementById('baselineHistoryBody').textContent)));
        await page.evaluate(()=>{window.requestAnimationFrame=window.testRAF;for(const callback of window.testFrames)window.testRAF(callback);delete window.testFrames;delete window.testRAF});
        // Labels are verbatim; translated identities update in the next animation frame.
        await page.waitForFunction(()=>/Shared-token user \/ Web/.test(document.getElementById('baselineHistoryBody').textContent));
        check('English baseline history preserves supplied labels and translates known identities',await page.locator('#baselineCurrent [data-verbatim]').last().textContent()===' 设置与关于'&&/Shared-token user \/ Web/.test(await page.textContent('#baselineHistoryBody')));
        await page.evaluate(id=>editRunLabel(id),runId);await page.waitForFunction(()=>document.getElementById('runLabelSave').textContent==='Save label');
        check('English dynamic edit labels retain the saved input exactly',await page.inputValue('#runNewLabel')==='设置与关于'&&/Run label \(can be cleared/.test(await page.textContent('#runManagementPanel')));await page.evaluate(()=>closeRunManagement());
        await page.selectOption('#languageSelect','original');await page.waitForFunction(()=>document.getElementById('settingsOpen').textContent==='设置与关于');
        check('original language restores mixed authored UI without changing data',await page.textContent('#runBtn')==='Run behavior audit'&&await page.locator('#baselineCurrent [data-verbatim]').last().textContent()===' 设置与关于');
        await page.selectOption('#languageSelect','en');await page.waitForFunction(()=>document.documentElement.lang==='en');
        await page.setViewportSize({width:1400,height:1100});await page.click('#agentCard > summary');await page.waitForFunction(()=>agent.sid!==null);
        await page.waitForFunction(()=>/fixed deterministic workflow/.test(document.getElementById('agentNotice').textContent));
        check('English applies to dynamically loaded Agent notices',/Offline · deterministic/.test(await page.textContent('#agentMode')));
        await page.evaluate(()=>renderApproval({action_id:'presentation-only',tool:'set_baseline',human_only:true,summary:'设置与关于'}));
        await page.waitForFunction(()=>document.querySelector('#agentLog .approval-check').textContent.includes('I have reviewed'));
        check('approval controls translate while the server summary stays original',await page.locator('#agentLog .approval-summary').textContent()==='设置与关于'&&await page.locator('#agentLog .approval-actions').textContent()==='DeclineApprove'&&await page.locator('#agentLog .approval .primary').isDisabled());
        check('preference browser flow has no uncaught errors',errors.length===0,errors.join(' | '));
        await context.close();
        const fresh=await browser.newContext(),freshPage=await fresh.newPage();await freshPage.goto(base);await freshPage.waitForFunction(()=>document.documentElement.dataset.language);
        check('a separate browsing context does not inherit preferences',await freshPage.inputValue('#languageSelect')==='original'&&await freshPage.inputValue('#themeSelect')==='dark');await fresh.close();
        const corrupt=await browser.newContext();await corrupt.addInitScript(()=>{sessionStorage.setItem('specagent_ui_language','invalid');sessionStorage.setItem('specagent_ui_theme','invalid')});
        const corruptPage=await corrupt.newPage();await corruptPage.goto(base);await corruptPage.waitForFunction(()=>document.documentElement.dataset.language);
        check('invalid saved preferences fall back safely',await corruptPage.inputValue('#languageSelect')==='original'&&await corruptPage.inputValue('#themeSelect')==='dark');await corrupt.close();
        const blocked=await browser.newContext();await blocked.addInitScript(()=>{Storage.prototype.getItem=()=>{throw new Error('storage blocked')};Storage.prototype.setItem=()=>{throw new Error('storage blocked')}});
        const blockedPage=await blocked.newPage();await blockedPage.goto(base);await blockedPage.selectOption('#languageSelect','en');await blockedPage.waitForFunction(()=>document.documentElement.lang==='en');
        check('blocked storage still permits switching and announces lack of persistence',/storage unavailable/.test(await blockedPage.textContent('#preferencesStatus')));await blocked.close();
    }catch(e){check('preferences test run completed',false,String(e.message))}
    finally{
        if(browser)await browser.close().catch(()=>{});server.kill();await new Promise(resolve=>{if(server.exitCode!==null)return resolve();const timer=setTimeout(resolve,5000);server.once('exit',()=>{clearTimeout(timer);resolve()})});
        const resolved=path.resolve(temp);if(resolved.startsWith(path.resolve(os.tmpdir())+path.sep)&&path.basename(resolved).startsWith('specagent-preferences-')){try{fs.rmSync(resolved,{recursive:true,force:true,maxRetries:3,retryDelay:200})}catch{}}
    }
    const failures=results.filter(r=>!r.ok).length;console.log('\n'+(results.length-failures)+' passed, '+failures+' failed  (preferences: '+results.length+' checks)');process.exit(failures?1:0);
}
main().catch(e=>{console.error(e.message);process.exit(3)});
