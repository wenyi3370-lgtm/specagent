/* Cookies stay HttpOnly. CSRF and account metadata live only in this page. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const state={mode:'shared',user:null,csrf:'',canEdit:pid=>state.user?.admin||state.user?.projects.some(p=>p.id===pid&&p.role==='editor')};
  window.accountUI=state;
  const permissionDisabled=new Map();
  const roleName=role=>({admin:'管理员',editor:'编辑用户',viewer:'查看用户'})[role]||'未授权';
  async function request(path,options={}){
    const headers={...options.headers};
    if(options.method&&options.method!=='GET')headers['X-SpecAgent-CSRF']=state.csrf;
    const r=await fetch(path,{...options,headers}),data=await r.json().catch(()=>({}));
    if(r.status===401&&path!=='/api/auth/login')state.expire();
    if(!r.ok)throw new Error(r.status===401?'用户名或密码错误。':r.status===429?'登录尝试过多，请稍后再试。':r.status===403?'没有此操作的权限。':'请求失败，请稍后重试。');
    return data;
  }
  let channel;
  state.expire=()=>{state.csrf='';state.user=null;location.reload()};
  state.apply=()=>{
    if(state.mode!=='multiuser'||!state.user)return;
    const current=typeof currentProject==='function'?currentProject():'';
    $('accountRole').textContent=state.user.admin?'管理员':roleName(state.user.projects.find(p=>p.id===current)?.role);
    $('projectCreate').hidden=!state.user.admin;$('settingsOpen').disabled=!state.user.admin;$('accessOpen').hidden=!state.user.admin;
    const serverEdit=['admin','editor'].includes(state.user.server_project_role);
    $('agentSection').classList.toggle('hidden',!serverEdit);
    const denied=[];
    if(!serverEdit)denied.push($('toolsControls'),$('projectRunBtn'),$('projectCancelBtn'));
    if(!state.canEdit(current))denied.push($('runBtn'),$('baselineClear'),...document.querySelectorAll('#runManagementPanel button[type="submit"],#baselineClearPanel button[type="submit"]'));
    document.querySelectorAll('[data-project-write]').forEach(node=>{if(!state.canEdit(node.dataset.projectWrite))denied.push(node)});
    if(!state.user.admin)denied.push($('specCompile'));
    const blocked=new Set(denied.filter(Boolean));
    for(const [node,previous] of permissionDisabled){if(!blocked.has(node)){permissionDisabled.delete(node);node.disabled=previous}}
    for(const node of blocked){if(!permissionDisabled.has(node))permissionDisabled.set(node,node.disabled);if(!node.disabled)node.disabled=true}
  };
  state.init=async()=>{
    state.mode='multiuser';$('mainContent').hidden=true;$('settingsOpen').disabled=true;
    $('tokenBox').classList.add('hidden');
    try{channel=new BroadcastChannel('specagent-account');channel.onmessage=()=>state.expire()}catch{}
    wireDialogs();
    let me;
    try{
      const r=await fetch('/api/auth/me');
      if(r.status===401){$('loginDialog').showModal();$('loginUsername').focus();return}
      if(!r.ok)throw new Error();me=await r.json();
    }catch{$('loginStatus').textContent='登录服务暂时不可用。';$('loginDialog').showModal();return}
    state.user=me;state.csrf=me.csrf;$('accountBox').hidden=false;$('accountName').textContent=me.username;
    $('mainContent').hidden=false;state.apply();
    new MutationObserver(()=>state.apply()).observe($('mainContent'),{childList:true,subtree:true,attributes:true,attributeFilter:['disabled']});
    $('projectSel').addEventListener('change',state.apply);
    $('logoutButton').onclick=async()=>{try{await request('/api/auth/logout',{method:'POST'});channel?.postMessage('changed');state.expire()}catch(e){$('accountStatus').textContent=e.message}};
    $('accessOpen').onclick=()=>{$('accessDialog').showModal();loadAccess().catch(accessError)};
    $('accessRefresh').onclick=()=>loadAccess().catch(accessError);
    $('accessProject').onchange=()=>loadMemberships().catch(accessError);
    $('accessForm').onsubmit=async e=>{
      e.preventDefault();$('accessSubmit').disabled=true;
      try{await request('/api/accounts/memberships',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({project_id:$('accessProject').value,user_id:$('accessUser').value,role:$('accessRole').value})});await loadMemberships();$('accessStatus').textContent='权限已保存。';}
      catch(e){accessError(e)}finally{$('accessSubmit').disabled=!$('accessUser').value||!$('accessProject').value}
    };
    await loadProjects();await loadProjectBar();state.apply();
    if(!me.projects.length)$('projectSelectionInfo').textContent='没有可访问的项目，请联系管理员。';
  };
  let users=[];
  const accessError=e=>{$('accessStatus').textContent=e.message};
  async function loadAccess(){
    $('accessStatus').textContent='正在读取权限…';
    const [all,projects]=await Promise.all([request('/api/accounts/users'),request('/api/projects')]);users=all;
    const previous=$('accessProject').value;$('accessProject').replaceChildren();$('accessUser').replaceChildren();
    for(const p of projects)$('accessProject').append(new Option(p.name+' ('+p.id+')',p.id));
    if(projects.some(p=>p.id===previous))$('accessProject').value=previous;
    for(const u of users.filter(u=>u.enabled&&!u.admin))$('accessUser').append(new Option(u.username,u.id));
    $('accessSubmit').disabled=!$('accessUser').value||!$('accessProject').value;
    await loadMemberships();
  }
  async function loadMemberships(){
    const project=$('accessProject').value;$('accessBody').replaceChildren();
    if(!project){$('accessStatus').textContent='暂无项目。';return}
    const items=await request('/api/accounts/memberships?project_id='+encodeURIComponent(project));
    for(const item of items){
      const row=document.createElement('tr'),name=document.createElement('td'),role=document.createElement('td'),cell=document.createElement('td'),button=document.createElement('button');
      name.dataset.verbatim='';name.textContent=users.find(u=>u.id===item.user_id)?.username||item.user_id;role.textContent=roleName(item.role);
      button.type='button';button.className='btn sm secondary';button.textContent='撤销权限';
      button.onclick=async()=>{button.disabled=true;try{await request('/api/accounts/memberships/'+encodeURIComponent(item.user_id)+'?project_id='+encodeURIComponent(project),{method:'DELETE'});await loadMemberships();$('accessStatus').textContent='权限已撤销。';}catch(e){accessError(e);button.disabled=false}};
      cell.append(button);row.append(name,role,cell);$('accessBody').append(row);
    }
    $('accessStatus').textContent=items.length?'权限按项目生效，管理员可访问全部项目。':'此项目尚未授权普通用户。';
  }
  function wireDialogs(){
    $('loginDialog').addEventListener('cancel',e=>e.preventDefault());
    $('loginLanguage').onclick=()=>{const select=$('languageSelect');select.value=select.value==='en'?'zh-CN':'en';select.dispatchEvent(new Event('change'))};
    $('loginForm').onsubmit=async e=>{
      e.preventDefault();$('loginSubmit').disabled=true;$('loginStatus').textContent='正在登录…';
      const password=$('loginPassword').value;$('loginPassword').value='';
      try{await request('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:$('loginUsername').value,password})});channel?.postMessage('changed');location.reload()}
      catch(e){$('loginStatus').textContent=e.message;$('loginPassword').focus()}finally{$('loginSubmit').disabled=false}
    };
    $('accessClose').onclick=()=>$('accessDialog').close();$('accessDialog').addEventListener('close',()=>$('accessOpen').focus());
    for(const id of ['loginDialog','accessDialog'])$(id).addEventListener('keydown',e=>{
      if(e.key!=='Tab')return;
      const nodes=[...$(id).querySelectorAll('button,input,select,[tabindex="0"]')].filter(n=>!n.disabled&&!n.hidden&&n.getClientRects().length),first=nodes[0],last=nodes.at(-1);
      if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus()}
    });
  }
})();
