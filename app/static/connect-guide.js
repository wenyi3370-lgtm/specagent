/* Static templates; validation remains an explicit guarded project operation. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  let generation=0,snapshot=null,validating=false;
  function canValidate(){return window.accountUI?.mode==='multiuser'?['admin','editor'].includes(window.accountUI.user?.server_project_role):true}
  function clear(){snapshot=null;$('connectConfig').textContent='';$('connectSpec').textContent='';$('connectNotes').textContent='';$('connectEnvironment').textContent='';$('connectOptional').textContent='';$('connectCommand').textContent='';$('connectSteps').replaceChildren();$('connectConfigCopy').disabled=true;$('connectSpecCopy').disabled=true}
  async function load(){
    const version=++generation,adapter=$('connectAdapter').value;clear();$('connectStatus').textContent='正在读取接入模板…';
    try{
      const data=await apiJson('/api/project/templates/'+encodeURIComponent(adapter));
      if(version!==generation||!$('connectDialog').open)return;
      snapshot=Object.freeze(data);$('connectConfig').textContent=data.config_yaml;$('connectSpec').textContent=data.spec_yaml;$('connectNotes').textContent=data.adapter_notes;
      $('connectEnvironment').textContent=data.environment_variables.join(' · ');$('connectOptional').textContent=data.optional_environment_variables.length?'可选环境变量 '+data.optional_environment_variables.join(' · '):'此适配器没有额外的可选环境变量。';
      $('connectCommand').textContent=data.cli_command;
      $('connectSteps').replaceChildren(...data.steps.map(step=>el('li',null,step)));
      $('connectConfigCopy').disabled=false;$('connectSpecCopy').disabled=false;$('connectStatus').textContent='模板已就绪。复制不会修改服务器文件。';
    }catch(e){if(version===generation)$('connectStatus').textContent=e.message}
  }
  async function copy(field){
    if(!snapshot)return;
    const version=generation,text=snapshot[field];$('connectStatus').textContent='正在复制模板…';
    try{await navigator.clipboard.writeText(text);if(version===generation&&$('connectDialog').open)$('connectStatus').textContent='模板已复制。请在服务器项目目录保存。'}catch{if(version===generation&&$('connectDialog').open)$('connectStatus').textContent='剪贴板不可用，请手动选择并复制模板。'}
  }
  document.addEventListener('DOMContentLoaded',()=>{
    $('connectOpen').onclick=()=>{
      $('connectDialog').showModal();$('connectValidate').disabled=validating||!canValidate();
      const previous=$('validateResult').textContent;$('connectValidation').textContent=previous||'尚未在本窗口校验。请先由部署者配置服务器。';
      load();
    };
    $('connectClose').onclick=()=>$('connectDialog').close();$('connectAdapter').onchange=load;
    $('connectConfigCopy').onclick=()=>copy('config_yaml');$('connectSpecCopy').onclick=()=>copy('spec_yaml');
    $('connectDialog').addEventListener('close',()=>{generation++;clear();$('connectValidation').textContent='';$('connectStatus').textContent='';$('connectOpen').focus()});
    $('connectValidate').onclick=async()=>{
      if(validating||!canValidate())return;
      validating=true;$('connectValidate').disabled=true;const version=generation;
      $('connectValidation').textContent='';$('connectStatus').textContent='正在校验服务器配置…';
      try{
        const data=await postProject('validate',{});
        if(version!==generation||!$('connectDialog').open)return;
        $('connectValidation').textContent=data.lines.join('\n');$('validateResult').hidden=false;$('validateResult').textContent=data.lines.join('\n');
        $('connectStatus').textContent=data.ok?'服务端校验通过。请核对顶部测试目标后再运行。':'服务端校验未通过，请部署者在本机修正配置。';
      }catch(e){if(version===generation&&$('connectDialog').open){$('connectValidation').textContent=e.message;$('connectStatus').textContent='服务端校验未完成。'}}
      finally{validating=false;$('connectValidate').disabled=!canValidate()}
    };
    $('connectDialog').addEventListener('keydown',e=>{
      if(e.key!=='Tab')return;
      const nodes=[...$('connectDialog').querySelectorAll('button,select,[tabindex="0"]')].filter(n=>!n.disabled&&n.getClientRects().length),first=nodes[0],last=nodes.at(-1);
      if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}
    });
  });
})();
