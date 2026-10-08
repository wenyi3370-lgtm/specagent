/* Only explicit previews and confirmations can request delivery. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const types={email:'邮件',webhook:'Webhook',pr_comment:'PR 评论'};
  const states={prepared:'待确认',sending:'发送中，结果尚未确定',sent:'已发送',failed:'发送失败，可能已送达',expired:'预览已过期'};
  let generation=0,offset=0,project='',preview=null,permissions={};
  function invalidate(){preview=null;$('notificationPreview').hidden=true;$('notificationContent').textContent='';$('notificationConfirm').checked=false;$('notificationSend').disabled=true;$('notificationPrepare').disabled=!permissions.send||!$('notificationChannel').value||!$('notificationRun').value}
  function error(e){$('notificationStatus').textContent=({channel_not_enabled:'渠道尚未启用。',channel_not_ready:'服务端凭据尚未配置。',notification_preview_changed:'预览已过期或配置发生变化，请重新预览。',notification_already_attempted:'这次通知已尝试发送，请查看历史。',notifications_require_authenticated_user:'发送通知需要登录或配置共享令牌。',project_access_denied:'没有此项目的权限。'})[e.message]||e.message}
  async function load(nextOffset=0){
    const version=++generation,pid=currentProject();project=pid;offset=nextOffset;invalidate();$('notificationPrepare').disabled=true;$('notificationStatus').textContent='正在读取通知…';
    const [channels,history,runs]=await Promise.all([apiJson('/api/notifications/channels?project_id='+encodeURIComponent(pid)),apiJson('/api/notifications/history?project_id='+encodeURIComponent(pid)+'&offset='+offset),apiJson('/api/runs?project_id='+encodeURIComponent(pid)+'&limit=200')]);
    if(version!==generation||pid!==currentProject())return;
    permissions=channels.permissions;$('notificationChannels').replaceChildren();$('notificationChannel').replaceChildren();
    for(const channel of channels.channels){
      const row=el('div','notification-channel'),name=el('strong',null,channel.name);name.dataset.verbatim='';
      const kind=el('span','tag',types[channel.kind]),status=el('span','status',!channel.ready?'凭据未配置':channel.enabled?'渠道已启用':'渠道已关闭');
      const toggle=el('button','btn sm secondary',channel.enabled?'关闭渠道':'启用渠道');toggle.type='button';toggle.disabled=!permissions.configure||(!channel.ready&&!channel.enabled);
      toggle.onclick=async()=>{toggle.disabled=true;try{await apiJson('/api/notifications/channels/'+channel.id,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!channel.enabled})});await load()}catch(e){error(e);toggle.disabled=false}};
      row.append(name,kind,status,toggle);$('notificationChannels').append(row);
      if(channel.ready&&channel.enabled)$('notificationChannel').append(new Option(channel.name,channel.id));
    }
    $('notificationRun').replaceChildren();
    for(const run of runs.filter(r=>['completed','canceled'].includes(r.status)))$('notificationRun').append(new Option(run.id+' · '+run.status,run.id));
    $('notificationPrepare').disabled=!permissions.send||!$('notificationChannel').value||!$('notificationRun').value;
    $('notificationHistory').replaceChildren();
    for(const delivery of history.items){
      const row=el('tr');row.dataset.deliveryId=delivery.id;
      for(const [i,value] of [delivery.created_at.replace('T',' '),delivery.run_id,delivery.channel_id,states[delivery.state]||delivery.state,delivery.actor].entries()){
        const cell=el('td',i===1?'mono':null,value);if(i!==3)cell.dataset.verbatim='';row.append(cell);
      }
      $('notificationHistory').append(row);
    }
    if(!history.items.length){const row=el('tr'),cell=el('td',null,'暂无通知记录。');cell.colSpan=5;row.append(cell);$('notificationHistory').append(row)}
    $('notificationPrev').disabled=offset===0;$('notificationNext').disabled=!history.has_more;
    $('notificationPage').textContent=String(history.total)+' 条记录';
    $('notificationStatus').textContent=channels.state==='invalid'?'服务端通知配置无效，请联系部署者。':channels.state==='missing'?'尚未配置通知接收目标，请联系部署者。':!permissions.send?'当前账号可查看历史，不能发送通知。':'渠道开关不会自动发送。每次发送都需要预览并明确确认。';
  }
  document.addEventListener('DOMContentLoaded',()=>{
    $('notificationsOpen').onclick=()=>{$('notificationDialog').showModal();load().catch(error)};
    $('notificationClose').onclick=()=>$('notificationDialog').close();
    $('notificationDialog').addEventListener('close',()=>{generation++;invalidate();$('notificationsOpen').focus()});
    $('notificationRefresh').onclick=()=>load(offset).catch(error);
    $('notificationPrev').onclick=()=>load(Math.max(0,offset-20)).catch(error);$('notificationNext').onclick=()=>load(offset+20).catch(error);
    for(const id of ['notificationRun','notificationChannel'])$(id).onchange=()=>{generation++;invalidate()};
    $('projectSel').addEventListener('change',()=>{invalidate();if($('notificationDialog').open)load().catch(error)});
    $('notificationPrepare').onclick=async()=>{
      const version=++generation;invalidate();$('notificationPrepare').disabled=true;
      try{
        const data=await apiJson('/api/notifications/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({run_id:$('notificationRun').value,channel_id:$('notificationChannel').value})});
        if(version!==generation||project!==currentProject()||!$('notificationDialog').open)return;
        preview=Object.freeze(data);$('notificationContent').textContent=JSON.stringify(data.summary,null,2);$('notificationExpires').textContent='确认有效至 '+data.expires_at;
        $('notificationPreview').hidden=false;$('notificationStatus').textContent='预览尚未发送。确认后将发送到所选渠道。';
      }catch(e){if(version===generation)error(e)}finally{if(version===generation)$('notificationPrepare').disabled=!permissions.send}
    };
    $('notificationConfirm').onchange=()=>{$('notificationSend').disabled=!preview||!$('notificationConfirm').checked};
    $('notificationSend').onclick=async()=>{
      if(!preview||!$('notificationConfirm').checked)return;
      const snapshot=preview,pid=currentProject(),version=++generation;invalidate();$('notificationPrepare').disabled=true;$('notificationStatus').textContent='正在发送通知…';
      try{
        const data=await apiJson('/api/notifications/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({delivery_id:snapshot.id,confirm_run_id:snapshot.run_id,confirm:true})});
        if(version!==generation||pid!==currentProject()||!$('notificationDialog').open)return;
        await load();if(pid===currentProject()&&$('notificationDialog').open)$('notificationStatus').textContent=data.state==='sent'?'通知已发送。':'发送失败，可能已送达。不会自动重发，请先核对接收端。';
      }catch(e){
        if(version!==generation||pid!==currentProject()||!$('notificationDialog').open)return;
        try{await load()}catch(_){}if(pid===currentProject()&&$('notificationDialog').open)error(e);
      }
    };
    $('notificationDialog').addEventListener('keydown',e=>{
      if(e.key!=='Tab')return;
      const nodes=[...$('notificationDialog').querySelectorAll('button,input,select,[tabindex="0"]')].filter(n=>!n.disabled&&n.getClientRects().length),first=nodes[0],last=nodes.at(-1);
      if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}
    });
  });
})();
