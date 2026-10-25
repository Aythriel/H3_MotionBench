'use strict';
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state, dirty=false, busy=false, polling=false, lastInstallStatus='', planSequence=0, planReady=false;
const bytes = n => n >= 1e9 ? `${(n/1e9).toFixed(2)} GB` : `${(n/1e6).toFixed(1)} MB`;
const models = () => [...document.querySelectorAll('[data-model]:checked')].map(el=>el.dataset.model);
async function api(path, body) {
  const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result=await response.json();if(!response.ok)throw new Error(result.error||response.statusText);return result;
}
function tell(message,error=false){$('notice').hidden=false;$('notice').textContent=message;$('notice').classList.toggle('error',error);}
async function action(fn){if(busy)return;busy=true;try{await fn();}catch(error){tell(error.message,true);}finally{busy=false;controls();}}
function controls(){const installing=state?.installation?.status==='running';$('downloadDependencies').disabled=!planReady||busy||installing||dirty;$('savePaths').disabled=busy||installing;$('detect').disabled=busy||installing;$('validate').disabled=busy||installing;document.querySelectorAll('[data-install]').forEach(b=>b.disabled=busy||installing||dirty||state.dependencies.find(d=>d.id===b.dataset.install)?.valid);$('cancelInstall').disabled=!installing;$('startEngine').disabled=busy||installing;$('restartEngine').disabled=busy||installing;$('dirtyNote').textContent=dirty?'Save path changes before installing.':'';}
function render(next,paths=false){
  state=next;
  const ready=Object.values(state.presets).filter(p=>p.ready).length;
  $('readiness').textContent=ready?`${ready} model preset${ready===1?'':'s'} ready for generation`:'Generation dependencies need attention';
  $('hardware').textContent=state.hardware.message;$('dashboardRuntime').value=state.runtime;
  $('configError').textContent=state.config_error;$('configError').hidden=!state.config_error;$('restartNotice').hidden=!state.restart_required;
  $('managedPath').textContent=`New installations: ${state.managed_directory}`;
  if(paths){
    $('dependencyRows').innerHTML=state.dependencies.map(row=>`<div class="dependency-row"><label>${esc(row.label)}<input data-path="${esc(row.id)}" value="${esc(row.path)}" class="${row.valid?'':'invalid'}" aria-invalid="${!row.valid}" aria-describedby="message-${esc(row.id)}"></label><div class="dependency-meta"><p id="message-${esc(row.id)}" class="${row.valid?'':'invalid'}">${esc(row.message)}</p><button type="button" class="small quiet" data-install="${esc(row.id)}">Install</button></div><div class="dependency-links">${row.source?`<a href="${esc(row.source)}" target="_blank" rel="noreferrer">Download source</a>`:''}<span>installers/Install-${esc(row.id)}.ps1</span></div></div>`).join('');
    $('comfyPort').value=state.comfy_port;$('existingComfy').value=state.paths.comfy;
    document.querySelectorAll('[data-model]').forEach(el=>el.checked=state.selected_models.includes(el.dataset.model));
    dirty=false;combinationLabel();
  }
  const installation=state.installation;$('installStatus').hidden=installation.status==='idle';$('installMessage').textContent=installation.message;
  if(installation.total){$('installProgress').value=installation.done/installation.total;$('installBytes').textContent=installation.total>1?`${bytes(installation.done)} / ${bytes(installation.total)}`:'';}else{$('installProgress').removeAttribute('value');$('installBytes').textContent='';}
  controls();
}
function combinationLabel(){const selected=models().join(',');$('combination').value=[...$('combination').options].some(o=>o.value===selected)?selected:'custom';}
async function updatePlan(){const sequence=++planSequence;const selected=models();$('downloadDependencies').disabled=true;if(!selected.length){planReady=false;$('planSummary').textContent='Choose at least one model.';return;}try{const plan=await api('/api/setup/plan',{models:selected});if(sequence!==planSequence)return;$('planSummary').textContent=plan.ids.length?`${bytes(plan.download_bytes)} to download`:'Selected dependencies are already available';$('planSpace').textContent=plan.ids.length?`${bytes(plan.required_free_bytes)} free space required for downloads and extraction · ${bytes(plan.free_bytes)} available`:'';$('planItems').textContent=plan.items.map(item=>item.label).join('\n');planReady=Boolean(plan.ids.length&&state.hardware.valid&&plan.required_free_bytes<=plan.free_bytes);controls();}catch(error){if(sequence===planSequence)tell(error.message,true);}}
async function reload(paths=false){render(await api('/api/setup'),paths);await updatePlan();}
async function install(only){if(dirty)throw new Error('Save paths before installing.');render(await api('/api/setup/install',{models:models(),only}),false);lastInstallStatus='running';await updatePlan();}
$('pathsForm').addEventListener('input',()=>{dirty=true;controls();$('downloadDependencies').disabled=true;});
$('pathsForm').addEventListener('submit',event=>{event.preventDefault();action(async()=>{const paths=Object.fromEntries([...document.querySelectorAll('[data-path]')].map(el=>[el.dataset.path,el.value]));render(await api('/api/setup',{paths,selected_models:models(),comfy_port:Number($('comfyPort').value)}),true);tell('Settings saved and paths checked.');await updatePlan();});});
$('dependencyRows').addEventListener('click',event=>{const button=event.target.closest('[data-install]');if(button)action(()=>install(button.dataset.install));});
$('validate').onclick=()=>action(async()=>{render(await api('/api/setup/validate',{}),!dirty);await updatePlan();});
$('detect').onclick=()=>action(async()=>{const result=await api('/api/setup/discover',{path:$('existingComfy').value});for(const [key,value] of Object.entries(result.paths)){const field=document.querySelector(`[data-path="${key}"]`);if(field)field.value=value;}dirty=true;tell('Paths found. Save paths to validate and use them.');await updatePlan();});
$('combination').onchange=()=>{if($('combination').value==='custom')return;const selected=$('combination').value.split(',');document.querySelectorAll('[data-model]').forEach(el=>el.checked=selected.includes(el.dataset.model));updatePlan();};
document.querySelectorAll('[data-model]').forEach(el=>el.onchange=()=>{combinationLabel();updatePlan();});
$('downloadDependencies').onclick=()=>action(()=>install());
$('cancelInstall').onclick=()=>action(async()=>tell((await api('/api/setup/cancel',{})).message));
$('showLog').onclick=()=>action(async()=>{$('logText').textContent=(await api('/api/setup/log')).log;$('logDialog').showModal();});
$('closeLog').onclick=()=>$('logDialog').close();
$('startEngine').onclick=()=>action(async()=>tell((await api('/api/start',{})).message));
$('restartEngine').onclick=()=>action(async()=>{tell((await api('/api/setup/restart-engine',{})).message);await reload(true);});
(async()=>{await reload(true);setInterval(async()=>{if(polling||busy)return;polling=true;try{const next=await api('/api/setup');const status=next.installation.status;render(next,lastInstallStatus==='running'&&status!=='running'&&!dirty);if(status!==lastInstallStatus)await updatePlan();lastInstallStatus=status;}catch(error){tell(error.message,true);}finally{polling=false;}},1500);})().catch(error=>tell(error.message,true));
