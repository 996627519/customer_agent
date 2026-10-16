let csrf = '';
let currentChat = null;
const $ = (s) => document.querySelector(s);
const toast = (text) => { const el = $('#toast'); el.textContent = text; el.className = 'toast show'; setTimeout(() => el.className = 'toast', 2600); };
function setPagePath(path) { if (location.pathname !== path) history.replaceState(null, '', path); }

async function api(path, {method='GET', body}={}) {
  const response = await fetch('/api'+path, {method, credentials:'same-origin', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:body ? JSON.stringify(body) : undefined});
  const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) throw Error(data.detail || '请求失败，请稍后重试。');
  if (data.csrf_token) csrf = data.csrf_token;
  return data;
}

function showAuth(registrationEnabled) {
  const root = $('#auth'); root.hidden = false; $('#app').hidden = true;
  setPagePath('/login');
  let registering = false;
  const render = () => {
    root.innerHTML = `<section class="auth-card"><span class="eyebrow">CAREFLOW · SUPPORT</span><h1>${registering?'创建账户':'登录客服工作台'}</h1><p>${registering?'创建账号以保存和继续你的客服对话。':'登录后查看历史客服对话。'}</p><form id="auth-form"><label>用户名<input name="username" required minlength="3" maxlength="32" autocomplete="username" placeholder="3–32 位字母、数字或符号"></label><label>密码<input name="password" type="password" required minlength="${registering?10:1}" maxlength="128" autocomplete="${registering?'new-password':'current-password'}"></label><p class="auth-error"></p><button>${registering?'创建账户':'登录'}</button></form>${registrationEnabled?`<button class="auth-toggle">${registering?'已有账号？登录':'没有账号？创建账户'}</button>`:''}</section>`;
    root.querySelector('.auth-toggle')?.addEventListener('click',()=>{registering=!registering;render();});
    root.querySelector('form').addEventListener('submit',async e=>{
      e.preventDefault();
      let form=e.currentTarget;
      const body=Object.fromEntries(new FormData(form));
      form.querySelector('button').disabled=true;
      try {
        if(registering){
          await api('/auth/register',{method:'POST',body});
          // Registration succeeded: switch to the login form before attempting sign-in,
          // so a subsequent login/service error does not invite a duplicate registration.
          registering=false;
          render();
          form=root.querySelector('form');
          form.elements.username.value=body.username;
          form.querySelector('button').disabled=true;
        }
        await api('/auth/login',{method:'POST',body});
        await startApp();
      } catch(err) {
        const activeForm=root.querySelector('form');
        if(activeForm){
          activeForm.querySelector('.auth-error').textContent=err.message;
          activeForm.querySelector('button').disabled=false;
        } else {
          toast(err.message);
        }
      }
    });
  }; render();
}

function addMessage(role, content, sources=[]) {
  $('#welcome').hidden = true;
  const row = document.createElement('div'); row.className = `message-row ${role}`;
  const avatar = document.createElement('div'); avatar.className='avatar'; avatar.textContent=role==='user'?'我':'✦';
  const bubble = document.createElement('div'); bubble.className='bubble'; bubble.textContent=content;
  if (sources.length) { const sourceBox=document.createElement('div');sourceBox.className='sources';sourceBox.textContent='政策来源：'+sources.map(s=>`[${s.number}] ${s.title}（${s.source}）`).join('；');bubble.append(sourceBox); }
  row.append(avatar,bubble); $('#messages').append(row); $('#conversation').scrollTop=$('#conversation').scrollHeight;
}

async function loadChats() {
  const chats=await api('/chats');const list=$('#chat-list');list.replaceChildren();
  chats.forEach(chat=>{const b=document.createElement('button');b.className='chat-item'+(chat.id===currentChat?' active':'');b.textContent=chat.title;b.onclick=()=>openChat(chat.id);list.append(b);});
  return chats;
}
async function openChat(id) {
  currentChat=id;$('#messages').replaceChildren();$('#welcome').hidden=true;
  const chat=await api('/chats/'+id);chat.messages.forEach(m=>{if(m.role==='user')addMessage('user',m.content);else addMessage('assistant',m.content,m.sources||[]);});
  if(!chat.messages.length)$('#welcome').hidden=false;
  await loadChats();
}
async function startApp() {
  const session=await api('/session');setPagePath('/chat');$('#auth').hidden=true;$('#app').hidden=false;$('#username').textContent=session.user.username;
  $('#status').innerHTML=session.model_ready?'<i></i>DeepSeek 已连接':'<i style="background:#d99b37"></i>待配置模型密钥';
  const chats=await loadChats();if(chats.length)await openChat(chats[0].id);
}

$('#new-chat').onclick=async()=>{currentChat=null;$('#messages').replaceChildren();$('#welcome').hidden=false;await loadChats();$('#message').focus();};
$('#logout').onclick=async()=>{try{await api('/auth/logout',{method:'POST'});}catch{}location.replace('/login');};
document.querySelectorAll('.suggestions button').forEach(b=>b.onclick=()=>{$('#message').value=b.textContent;$('#message').focus();});
$('#message').addEventListener('input',e=>{e.target.style.height='auto';e.target.style.height=Math.min(e.target.scrollHeight,160)+'px';});
$('#composer').addEventListener('submit',async e=>{e.preventDefault();const input=$('#message'),send=$('#send'),message=input.value.trim();if(!message)return;addMessage('user',message);input.value='';input.style.height='auto';send.disabled=true;const wait=document.createElement('div');wait.className='message-row assistant thinking';wait.innerHTML='<div class="avatar">✦</div><div class="bubble">正在检索并整理答复…</div>';$('#messages').append(wait);try{const result=await api('/chat',{method:'POST',body:{message,chat_id:currentChat}});currentChat=result.chat_id;wait.remove();addMessage('assistant',result.answer,result.sources);await loadChats();}catch(err){wait.remove();addMessage('assistant',err.message);toast(err.message);}finally{send.disabled=false;input.focus();}});

let registrationEnabled = false;
try {
  const auth = await api('/auth/csrf');
  registrationEnabled = auth.registration_enabled;
  await startApp();
} catch (e) {
  if (e.message.includes('登录')) {
    showAuth(registrationEnabled);
  } else {
    setPagePath('/login');
    $('#app').hidden = true;
    $('#auth').hidden = false;
    $('#auth').innerHTML = `<section class="auth-card"><h1>暂时无法连接</h1><p>${e.message}</p><button onclick="location.reload()">重试</button></section>`;
  }
}
