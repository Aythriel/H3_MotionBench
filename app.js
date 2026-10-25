'use strict';
const $ = id => document.getElementById(id);
let defaults = {}, options = {}, references = [], jobs = [], selected = null, online = false, capsLoaded = false;
let historySignature = '', polling = false, noticeTimer, savedSettings = null;
const imageCache = new Map();
let architectures = {}, modes = {}, models = {}, pipelinePresets = [], identityImages = [];
let weightBytes = {}, liveDevice = null, dependencyStatus = null;
let outputFolders = null, outputFoldersLoaded = false, folderParent = "";
const busyActions = new Set();
const activeStates = new Set(['queued', 'running', 'cancelling', 'recovering']);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
try { savedSettings = JSON.parse(localStorage.getItem('h3-workbench-settings')); } catch {}

async function api(path, body, method) {
  const requestMethod = method || (body === undefined ? 'GET' : 'POST');
  const options = body === undefined ? {method: requestMethod} : body instanceof FormData ? {method: requestMethod, body} : {method: requestMethod, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)};
  const response = await fetch(path, options);
  let data;
  try { data = await response.json(); }
  catch {
    if ([404, 405].includes(response.status)) throw new Error(`The requested action or take is unavailable (HTTP ${response.status}). Restart MotionBench if it was updated, then refresh this page.`);
    throw new Error(`The dashboard returned an unreadable response (HTTP ${response.status}). Refresh the page and try again.`);
  }
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
function renderOutputFolders(chosen) {
  const selector = $('output_folder'); selector.replaceChildren();
  for (const path of outputFolders.folders) selector.add(new Option(path + (path === outputFolders.default ? ' · default' : ''), path));
  selector.value = outputFolders.folders.includes(chosen) ? chosen : outputFolders.default;
  outputFoldersLoaded = true; updateOutputDefault();
}
function updateOutputDefault() {
  $('outputDefault').textContent = `Default for new sessions: ${outputFolders?.default || 'Not loaded'}`;
  $('defaultOutputFolder').disabled = !outputFoldersLoaded || $('output_folder').value === outputFolders.default;
}
async function loadOutputFolders() {
  outputFolders = await api('/api/output-folders'); renderOutputFolders(outputFolders.default);
}
async function browseFolders(path) {
  try {
    const result = await api('/api/browse-folders?path='+encodeURIComponent(path));
    $('folderPath').value = result.path; folderParent = result.parent;
    $('folderList').replaceChildren();
    for (const folder of result.folders) {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'quiet';
      button.textContent = folder.split(/[\\/]/).filter(Boolean).pop() || folder;
      button.title = folder; button.addEventListener('click', () => browseFolders(folder)); $('folderList').append(button);
    }
    $('folderMessage').textContent = result.folders.length ? 'Open a subfolder, or add the current path.' : 'No subfolders. You can add this path.';
  } catch(error) { $('folderMessage').textContent = error.message; }
}
$('addOutputFolder').addEventListener('click', () => { $('folderPath').value = $('output_folder').value; $('folderDialog').showModal(); browseFolders($('folderPath').value); });
$('closeFolder').addEventListener('click', () => $('folderDialog').close());
$('browseFolder').addEventListener('click', () => browseFolders($('folderPath').value));
$('folderParent').addEventListener('click', () => browseFolders(folderParent));
$('folderDrives').addEventListener('click', () => browseFolders(''));
$('useFolder').addEventListener('click', () => act($('useFolder'), async () => {
  try {
    const path = $('folderPath').value; outputFolders = await api('/api/output-folders', {action:'add',path});
    const selected = outputFolders.selected;
    renderOutputFolders(selected); $('folderDialog').close(); saveDraft(); notice('Output folder selected for new generations.');
  } catch(error) { $('folderMessage').textContent = error.message; }
}));
$('output_folder').addEventListener('change', () => { updateOutputDefault(); saveDraft(); });
$('defaultOutputFolder').addEventListener('click', () => act($('defaultOutputFolder'), async () => {
  const path = $('output_folder').value; outputFolders = await api('/api/output-folders', {action:'default',path});
  renderOutputFolders(path); notice('Default output folder saved for future sessions and generations.');
}));
function notice(message, error = false) {
  clearTimeout(noticeTimer);
  $('notice').hidden = false;
  $('notice').classList.toggle('error', error);
  $('notice').textContent = message;
  if (!error) noticeTimer = setTimeout(() => {$('notice').hidden = true;}, 11000);
}
async function act(button, fn) {
  busyActions.add(button.id); button.disabled = true;
  try { await fn(); } catch(error) { notice(error.message, true); }
  finally { busyActions.delete(button.id); button.disabled = false; updateGenerationAvailability(); }
}
function config() {
  const result = {};
  for (const [key, value] of Object.entries(defaults)) {
    const field = $(key);
    if (field) result[key] = typeof value === 'boolean' ? field.checked : typeof value === 'number' ? Number(field.value) : field.value;
  }
  result.identity_images = [...identityImages];
  result.settings_version = 3;
  return result;
}
function fillSelect(field, values, selected, preserveMissing = true) {
  field.replaceChildren();
  for (const item of values) field.add(new Option(item.label || item.value, item.value));
  if (preserveMissing && selected && !values.some(item => item.value === selected)) field.add(new Option(`${selected} (unavailable)`, selected));
  field.value = selected || values[0]?.value || '';
}
function architectureControls(model, mode, preserveMissing = true) {
  const architecture = $('architecture').value;
  const matching = Object.entries(models).filter(([, data]) => data.architecture === architecture).map(([value]) => ({value}));
  const targetModel = preserveMissing ? model : matching.some(item => item.value === model) ? model : matching[0]?.value;
  fillSelect($('model'), matching, targetModel, preserveMissing);
  const allowed = (architectures[architecture]?.modes || []).map(value => ({value, label:modes[value].label}));
  const targetMode = preserveMissing ? mode : allowed.some(item => item.value === mode) ? mode : architectures[architecture]?.default_mode;
  fillSelect($('mode'), allowed, targetMode, preserveMissing);
  $('turbo').disabled = architecture !== 'fl2va';
  $('turboHint').textContent = architecture === 'fl2va' ? 'Optional FL2VA turbo experiment: eight steps, Euler / beta. Compare against base sampling.' : 'Base sampling is the default for references. The installed FL2VA turbo preset is unavailable for this architecture.';
  $('conditioningHint').textContent = architectures[architecture]?.description || 'Choose an available architecture.';
}
function applySettings(values) {
  const settings = {...defaults, ...values, output_folder:$('output_folder').value};
  if (!values.architecture && values.model) settings.architecture = models[values.model]?.architecture || defaults.architecture;
  const spec = modes[settings.mode];
  if (values.settings_version === 2 && spec?.references && spec.frames.includes('first')) {
    const count = settings.identity_images.length;
    for (const field of ['prompt','sound','music']) {
      settings[field] = settings[field].replace(/<Picture ([1-9][0-9]*)>/g, (token, value) => {
        const number = Number(value);
        return number <= count + 1 ? `<Picture ${number === count + 1 ? 1 : number + 1}>` : token;
      });
    }
  }
  $('architecture').value = settings.architecture;
  architectureControls(settings.model, settings.mode);
  identityImages = Array.isArray(settings.identity_images) ? [...settings.identity_images] : [];
  for (const [key, value] of Object.entries(settings)) {
    const field = $(key);
    if (!field || !(key in defaults) || ['architecture','model','mode'].includes(key)) continue;
    if (field.type === 'checkbox') field.checked = Boolean(value);
    else {
      if (field.tagName === 'SELECT' && !Array.from(field.options).some(option => option.value === String(value))) field.add(new Option(`${value} (unavailable)`, value));
      field.value = value;
    }
  }
  const canvasPreset = Object.entries({draft:[608,352,2],square:[512,512,2],native:[1344,768,5]}).find(([,size]) => size[0]===Number(settings.width) && size[1]===Number(settings.height) && size[2]===Number(settings.seconds));
  $('quality').value = canvasPreset?.[0] || 'custom';
  renderIdentity(); updateCanvas(); saveDraft();
}
function updatePresetLabel() {
  const preset = pipelinePresets.find(p => Object.entries(p.settings).every(([key,value]) => config()[key] === value));
  $('pipelinePreset').value = preset?.id || 'custom';
}
function renderIdentity() {
  const spec = modes[$('mode').value] || {frames:[], references:false};
  const offset = Number(spec.frames.includes('first'));
  $('firstTitle').textContent = 'First frame · Picture 1';
  $('lastTitle').textContent = `Last frame · Picture ${offset + (spec.references ? identityImages.length : 0) + 1}`;
  $('promptImageHint').textContent = offset
    ? 'Use <Picture 1> for the starting frame.' + (spec.references ? ' Character references start at <Picture 2>; use <Subject 1> for the character.' : '')
    : spec.references ? 'Character references start at <Picture 1>; use <Subject 1> for the character.' + (spec.frames.includes('last') ? ` End at <Picture ${identityImages.length + 1}>.` : '')
    : spec.frames.includes('last') ? 'Use <Picture 1> for the last frame.' : '';
  $('promptImageHint').hidden = !spec.frames.length && !spec.references;
  $('identityList').innerHTML = identityImages.length ? identityImages.map((id,index) => {
    const ref = references.find(item => item.id === id);
    return `<div class="identity-card">${ref ? `<img src="/api/references/${encodeURIComponent(id)}/image" alt="Character reference ${index+1}">` : ''}<strong>Picture ${index+1+offset}</strong><small>${escapeHtml(ref?.name || 'Unavailable image — upload or replace it')}</small><button type="button" class="quiet small" data-remove-identity="${escapeHtml(id)}">Remove</button></div>`;
  }).join('') : '<p class="hint">Add the original character artwork to keep identity consistent.</p>';
}
function addIdentity(id) {
  if (!id || identityImages.includes(id)) return;
  if (identityImages.length >= 9) throw new Error('Use up to nine character references.');
  identityImages.push(id); renderIdentity(); saveDraft();
}
function frameMode(frames) {
  const useRefs = modes[$('mode').value]?.references && $('architecture').value === 'hybrid';
  return frames + (useRefs ? '_reference' : '');
}
function saveDraft() {
  renderBudget(); updatePresetLabel(); updateGenerationAvailability();
  try { localStorage.setItem('h3-workbench-settings', JSON.stringify(config())); $('saved').textContent = 'Settings saved in this browser'; }
  catch { $('saved').textContent = 'Browser storage unavailable · export settings to keep them'; }
}
function downloadJson(value, filename) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], {type:'application/json'}));
  const link = document.createElement('a'); link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function loadCapabilities(refresh = false) {
  let current = capsLoaded ? config() : savedSettings;
  const caps = await api('/api/capabilities' + (refresh ? '?refresh=1' : ''));
  dependencyStatus = caps.dependencies || null;
  defaults = caps.defaults; options = caps.options; weightBytes = caps.weight_bytes || {};
  architectures = caps.architectures; modes = caps.modes; models = caps.models; pipelinePresets = caps.presets;
  fillSelect($('architecture'), Object.entries(architectures).map(([value,data]) => ({value,label:data.label})), defaults.architecture);
  fillSelect($('pipelinePreset'), [...pipelinePresets.map(p => ({value:p.id,label:p.label+(p.available ? '' : ' · unavailable')})), {value:'custom',label:'Custom settings'}], 'hybrid_balanced');
  if (current && !current.settings_version) {
    try { localStorage.setItem('h3-workbench-settings-pre-hybrid', JSON.stringify(current)); } catch {}
    const mode = {first:'first_reference',both:'both_reference',last:'last_reference',text:'first_reference'}[current.mode] || defaults.mode;
    current = {...current, ...pipelinePresets[0].settings, mode, settings_version:3, identity_images:[]};
    notice('Hybrid is now the default. Your prompt and frame selections were kept; add the original artwork under Character references.');
  }
  for (const [key, values] of Object.entries(options)) {
    if (key === 'model') continue;
    $(key).replaceChildren();
    if (key === 'lora') $(key).add(new Option('None · base model', ''));
    for (const value of values) $(key).add(new Option(value, value));
  }
  $('free_encoder').disabled = !caps.free_encoder;
  $('visual_strength').disabled = !caps.strength;
  capsLoaded = true;
  const settings = {...defaults, ...current};
  if (!current && references.length) settings.first = references[0].id;
  if (!caps.free_encoder) settings.free_encoder = false;
  applySettings(settings);
}
async function loadReferences() {
  references = await api('/api/references');
  for (const slot of ['first','last','identityPicker']) {
    const value = $(slot).value;
    $(slot).replaceChildren(new Option('Choose a reference…', ''));
    for (const ref of references) $(slot).add(new Option(`${ref.name} · ${ref.width}×${ref.height}`, ref.id));
    $(slot).value = value;
  }
  renderIdentity();
}
async function upload(slot, file) {
  if (!file) return;
  const data = new FormData(); data.append('image', file);
  const ref = await api('/api/references', data);
  await loadReferences();
  if (slot === 'identity') { addIdentity(ref.id); return; }
  $(slot).value = ref.id;
  if (slot === 'first' && !modes[$('mode').value]?.frames.includes('first')) $('mode').value = frameMode(modes[$('mode').value]?.frames.includes('last') ? 'both' : 'first');
  if (slot === 'last' && !modes[$('mode').value]?.frames.includes('last')) $('mode').value = frameMode('both');
  await updateCanvas(); saveDraft(); notice(`${file.name} added to the reference shelf.`);
}
async function drawFrame(slot) {
  const id = $(slot).value, canvas = $(slot+'Canvas'), context = canvas.getContext('2d');
  const width = Number($('width').value) || 608, height = Number($('height').value) || 352;
  canvas.width = Math.max(64, Math.min(1536, width)); canvas.height = Math.max(64, Math.min(1536, height));
  context.fillStyle = id ? $('background').value : '#151b15'; context.fillRect(0,0,canvas.width,canvas.height);
  $(slot+'Empty').style.display = id ? 'none' : 'flex';
  if (!id) return;
  let image = imageCache.get(id);
  if (!image) {
    image = new Image(); image.src = `/api/references/${encodeURIComponent(id)}/image`;
    await image.decode(); imageCache.set(id, image);
  }
  if ($(slot).value !== id) return;
  const fit = $('fit').value;
  let w = canvas.width, h = canvas.height;
  if (fit !== 'stretch') {
    const ratio = (fit === 'cover' ? Math.max : Math.min)(w/image.width, h/image.height);
    w = image.width*ratio; h = image.height*ratio;
  }
  context.drawImage(image, (canvas.width-w)/2, (canvas.height-h)/2, w,h);
  if (slot === 'first') $('firstSize').textContent = `${image.width} × ${image.height} source → ${canvas.width} × ${canvas.height} canvas`;
}
async function updateCanvas() {
  const mode = $('mode').value;
  renderIdentity();
  for (const slot of ['first','last']) {
    const enabled = modes[mode]?.frames.includes(slot) || false;
    $(slot+'Box').hidden = !enabled;
    $(slot).disabled = !enabled;
    try { await drawFrame(slot); } catch { $(slot+'Empty').textContent = 'Reference unavailable. Upload the file again.'; $(slot+'Empty').style.display = 'flex'; }
  }
  const spec = modes[mode] || {frames:[], references:false};
  document.querySelector('.references').style.gridTemplateColumns = spec.frames.length > 1 ? '1fr 1fr' : '1fr';
  $('identityBox').hidden = !spec.references;
  $('fitLabel').hidden = !spec.frames.length;
  $('imagePreparation').hidden = !spec.frames.length && !spec.references;
  $('visual_strength').closest('.help-field').hidden = !spec.frames.length && !spec.references;
  const n = Math.max(5, Math.round((Number($('seconds').value) || 0)*24));
  const frames = n + ((5 - n%17 + 17)%17);
  $('timing').textContent = `${frames} frames / 24 fps / ${(frames/24).toFixed(2)} s actual`;
  $('draftNote').textContent = Number($('seconds').value) < 4 || Number($('width').value)*Number($('height').value) < 768*768 ? 'Draft outside native training settings' : 'Native-canvas candidate';
}
function showTake(id) {
  const job = jobs.find(item => item.id === id);
  if (!job || !job.outputs.length) return;
  selected = id;
  $('video').src = `/api/jobs/${encodeURIComponent(id)}/video`;
  $('video').style.display = 'block'; $('videoEmpty').hidden = true;
  $('video').loop = $('loopPlayback').checked; $('video').playbackRate = Number($('speed').value);
  describeTake(job);
  $('download').href = `/api/jobs/${id}/video?download=1`;
  $('record').href = `/api/jobs/${id}/record`;
  $('workflow').href = `/api/jobs/${id}/workflow`;
  $('downloads').hidden = false; historySignature = ''; renderHistory();
}
function wallTime(job) {
  if (job.kind === 'clip' || activeStates.has(job.status)) return '';
  const start = Number.isFinite(job.started) ? job.started : job.created;
  if (!Number.isFinite(start) || !Number.isFinite(job.finished) || job.finished < start) return '';
  const seconds = Math.round(job.finished - start);
  const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60);
  return `Wall time ${hours ? hours + 'h ' : ''}${minutes || hours ? minutes + 'm ' : ''}${seconds % 60}s${Number.isFinite(job.started) ? '' : ' (incl. queue)'}`;
}
function describeTake(job) {
  const c = job.config;
  $('selectedInfo').textContent = `${c.output_folder ? 'Output folder: ' + c.output_folder + ' · ' : ''}${c.name} · ${c.width} × ${c.height} · ${c.frames} frames · ${c.actual_seconds.toFixed(2)} s · ${job.kind === 'clip' ? 'Saved clip · settings and graph belong to its source generation' : `seed ${c.seed} · ${c.steps} steps`}${job.kind === 'warmup' ? ' · warm-up only, not a quality sample' : ''}`;
  if (wallTime(job)) $('selectedInfo').textContent += ` · ${wallTime(job)}`;
  $('workflow').textContent = job.kind === 'clip' ? 'Source ComfyUI graph' : 'ComfyUI graph';
}
let renameJobId = null;
function requestRename(job) {
  renameJobId = job.id; $('renameName').value = job.config.name; $('renameError').textContent = '';
  $('renameDialog').showModal(); $('renameName').focus(); $('renameName').select();
}
$('cancelRename').onclick = () => { renameJobId = null; $('renameDialog').close(); };
$('renameForm').onsubmit = event => {
  event.preventDefault(); act($('confirmRename'), async () => {
    if (!renameJobId) return;
    try {
      const result = await api(`/api/jobs/${encodeURIComponent(renameJobId)}/rename`, {name:$('renameName').value});
      await poll(); await loadReferences(); renameJobId = null; $('renameDialog').close(); notice(result.message);
    } catch (error) { $('renameError').textContent = error.message; }
  });
};
function renderHistory() {
  const signature = JSON.stringify(jobs.map(j => [j.id,j.status,j.config.name,j.error,j.export_error,j.outputs.length,j.started,j.finished])) + selected;
  if (signature === historySignature) return;
  historySignature = signature;
  const current = jobs.find(job => job.id === selected);
  if (current) describeTake(current);
  const inactiveJobs = jobs.filter(job => ['cancelled','discarded'].includes(job.status));
  const visibleJobs = jobs.filter(job => !['cancelled','discarded'].includes(job.status));
  $('takeCount').textContent = `${visibleJobs.filter(j => ['take','clip'].includes(j.kind)).length} takes / clips`;
  $('inactiveCount').textContent = String(inactiveJobs.length);
  $('inactiveTakes').hidden = !inactiveJobs.length;
  const discardedCount = jobs.filter(job => job.status === 'discarded').length;
  $('emptyTrash').hidden = !discardedCount;
  $('trashCount').textContent = `Empty trash (${discardedCount})`;
  const renderTake = job => {
    const c = job.config;
    const deletion = job.status === 'completed'
      ? `<button class="small danger" data-action="delete" data-id="${job.id}">Delete video</button>`
      : ['failed', 'discarded'].includes(job.status)
        ? `<button class="small danger" data-action="purge" data-id="${job.id}" title="Permanently delete this take and its saved settings" aria-label="${job.status === 'failed' ? 'Delete failed take' : 'Permanently delete discarded take'}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/></svg>${job.status === 'failed' ? 'Delete' : ''}</button>`
        : '';
    return `<article class="take ${selected === job.id ? 'selected' : ''}"><div><div class="take-title">${escapeHtml(c.name)}</div><div class="take-meta">${new Date(job.created*1000).toLocaleString()}<br>${c.width}×${c.height} · ${c.actual_seconds.toFixed(2)}s · ${job.kind==='clip'?'Saved clip':`${c.steps} steps · seed ${c.seed}`}${wallTime(job) ? `<br>${escapeHtml(wallTime(job))}` : ''}</div></div><div><div class="take-status">${escapeHtml(job.status)}</div><div class="take-actions">${job.outputs.length ? `<button class="small" data-action="play" data-id="${job.id}">Review</button>` : ''}${deletion}<button class="small quiet" data-action="reuse" data-id="${job.id}">${job.kind==='clip'?'Reuse source settings':'Reuse'}</button>${!activeStates.has(job.status)?`<button class="small quiet" data-action="rename" data-id="${job.id}">Rename</button>`:''}${activeStates.has(job.status) ? `<button class="small quiet" data-action="cancel" data-id="${job.id}">Cancel</button>` : ''}</div></div>${job.error ? `<div class="take-error">${escapeHtml(job.error)}</div>` : ''}${job.export_error ? `<div class="take-error">${escapeHtml(job.export_error)} <button class="small" data-action="export" data-id="${job.id}">Retry saving</button></div>` : ''}</article>`;
  };
  $('history').innerHTML = visibleJobs.map(renderTake).join('') || '<div class="empty-history">No takes to show here yet. Generated takes keep their settings and references for reuse.</div>';
  $('inactiveHistory').innerHTML = inactiveJobs.map(renderTake).join('');
}
function updateGenerationAvailability() {
  let ready = true;
  if (dependencyStatus) {
    const model = $('model').value;
    const preset = model.includes('b25-49') ? 'hybrid_balanced' : model.includes('b20-49') ? 'hybrid_reference' : 'fl2va';
    ready = Boolean(dependencyStatus.presets[preset]?.ready);
  }
  $('dependencyNotice').hidden = ready;
  $('generate').disabled = !ready || !online || !capsLoaded || !outputFoldersLoaded || busyActions.has('generate');
  $('warmup').disabled = !ready || !online || !capsLoaded || !outputFoldersLoaded || busyActions.has('warmup');
}
function renderStatus(data) {
  online = data.online;
  dependencyStatus = data.dependencies || dependencyStatus;
  $('led').classList.toggle('online', online);
  $('connection').textContent = online ? 'Connected locally' : 'ComfyUI offline';
  $('comfyLink').href = data.comfy_url;
  const device = data.stats?.devices?.[0];
  liveDevice = online ? device : null; renderBudget();
  $('gpu').textContent = device?.name?.replace('cuda:0 ', '') || 'MiniMax H3 · FL2VA';
  $('memory').textContent = online ? device ? `${(device.vram_free/2**30).toFixed(1)} / ${(device.vram_total/2**30).toFixed(1)} GiB available · ${data.websocket ? 'Live progress connected' : 'History polling'}${data.evict_pending ? ' · eviction after queue drains' : ''}` : 'ComfyUI connected' : data.error || 'Start ComfyUI to load the model catalogue.';
  updateGenerationAvailability();
  $('evict').disabled = !online || busyActions.has('evict') || Boolean(data.queue.queue_running || data.queue.queue_pending);
  const queueSize = (data.queue.queue_running || 0)+(data.queue.queue_pending || 0);
  $('queueCount').textContent = queueSize ? `${queueSize} IN COMFY QUEUE` : 'IDLE';
  jobs = data.jobs;
  const current = jobs.find(j => ['running','cancelling'].includes(j.status)) || jobs.slice().reverse().find(j => j.status === 'queued');
  if (current) {
    $('currentTitle').textContent = current.config.name;
    $('elapsed').textContent = `${Math.floor((Date.now()/1000-(current.started || current.created))/60)}m ${Math.floor(Date.now()/1000-(current.started || current.created))%60}s`;
    const stages = {condition:'Encoding prompt and reference frames', model:'Loading H3 model', encoder:'Loading visual / text encoder', sample:'Sampling video and audio', decode:'Decoding video', decode_audio:'Decoding native audio', mux:'Combining video and audio', save:'Writing MP4', free_encoder:'Evicting encoder from VRAM'};
    const p = current.progress;
    $('stage').textContent = !online ? 'Connection lost. The job may still be running; reconnecting…' : current.status === 'queued' ? 'Queued in ComfyUI; waiting for the GPU' : current.status === 'cancelling' ? 'Cancellation requested' : stages[p.node] || 'Loading or executing graph';
    if (p.max && p.value !== undefined) { $('progress').value = 100*p.value/p.max; $('progressText').textContent = `${p.value} / ${p.max} · progress for the current stage`; }
    else { $('progress').removeAttribute('value'); $('progressText').textContent = 'This stage has no percentage estimate. First load can take several minutes.'; }
  } else {
    $('currentTitle').textContent = queueSize ? 'Other ComfyUI work is active' : 'Ready for a take';
    $('stage').textContent = queueSize ? 'This dashboard leaves other clients’ jobs alone.' : data.last_eviction ? 'Models were requested to unload. The next generation loads them again.' : 'Select frames, tune the motion, then generate.';
    $('elapsed').textContent = ''; $('progress').value = 0; $('progressText').textContent = 'Video and audio are generated together. No render timeout is imposed.';
  }
  renderHistory();
  if (!selected) { const recent = jobs.find(j => j.outputs.length && j.kind === 'take') || jobs.find(j => j.outputs.length); if (recent) showTake(recent.id); }
}
async function poll() {
  if (polling) return; polling = true;
  try { const state = await api('/api/status'); renderStatus(state); if (state.online && !capsLoaded) await loadCapabilities(); }
  catch(error) { liveDevice = null; renderBudget(); $('connection').textContent = 'Dashboard disconnected'; $('led').classList.remove('online'); $('generate').disabled = true; $('warmup').disabled = true; $('evict').disabled = true; }
  finally { polling = false; }
}
const settingHelp = {
  model: ['H3 checkpoint', 'The main denoising model generates motion and audio together. Hybrid supports frames plus independent references, FL2VA supports frames, and REF2VA supports references. GGUF quantization reduces weight storage. This is usually the main sampling-stage GPU load. Smaller quants can trade fidelity for memory savings.'],
  encoder: ['Visual / text encoder', 'Turns your prompt and reference images into conditioning that guides H3. It runs before sampling. This large encoder can compete with the generator for VRAM; CPU placement or eviction after encoding reduces overlap.'],
  video_vae: ['Video VAE', 'Compresses reference frames into the model’s latent representation, then decodes generated video latents back into pixels. It needs weights plus working buffers; tiled decode reduces the latter. Use the matching H3 video VAE.'],
  audio_vae: ['Audio VAE', 'Decodes the audio generated by H3 into a waveform, which is saved with the video. It does not create an independent soundtrack. Use the matching H3 audio VAE; native audio stays enabled.'],
  lora: ['Model LoRA', 'An optional set of weight changes applied to the main H3 model. The installed turbo LoRA supports fewer sampling steps; it can change visual and sound quality. LoRA loading and patching can also add memory beyond the base checkpoint. None uses the base model.'],
  lora_strength: ['LoRA strength', 'Scales the selected LoRA’s effect. 1 uses its full authored strength; 0 removes its effect. Intermediate values blend the change but do not reduce memory proportionally. This field has no effect when no LoRA is selected.'],
  turbo: ['Turbo preset', 'Selects the installed FL2V turbo LoRA at strength 1, eight steps, Euler sampler and beta schedule. This saves sampling time; it does not shrink the base model or guarantee a lower peak VRAM requirement.'],
  base: ['Base-model preset', 'Disables the LoRA and restores 20 steps, res_multistep and simple scheduling. The prompt, seed, canvas and references remain as selected, so you can compare the two sampling setups.'],
  sampler: ['Sampler', 'The numerical method that updates the noisy video/audio latents at each sampling step. It affects detail, stability and motion. Keep the sampler paired with the chosen base or turbo preset when starting out. It is not a model-size control.'],
  scheduler: ['Scheduler', 'Chooses the noise levels used across the sampling steps. A different schedule can change the result even with the same seed and sampler. Simple is the base preset; beta is the turbo experiment. It does not reduce weight memory.'],
  denoise: ['Denoise', 'Controls how much of the denoising schedule is used. Leave at 1 for generation from this workflow’s empty video/audio latents. Lower values are not a reference-image strength slider and may leave poor or noisy output.'],
  visual_strength: ['Visual adherence', 'Controls how cleanly keyframe and character-reference latents are supplied to H3. 1 gives the cleanest anchor; lower values blend noise into that conditioning and allow more drift. Default 0.999. This is not a guarantee of exact identity and has no effect in text-only mode.'],
  encoder_device: ['Encoder device', 'Auto lets ComfyUI use the GPU and offload as needed. CPU moves encoder computation to system RAM, reducing its GPU pressure but usually taking longer. The main generator and video/audio decoders still need GPU memory.'],
  free_encoder: ['Evict encoder before sampling', 'Unloads the encoder from VRAM after the prompt and frame conditioning are computed. That conditioning is retained for H3. This frees space for the generator; a later uncached encoding pass has to load the encoder again.'],
  tiled: ['Tiled video VAE decode', 'Decodes the video in overlapping spatial and temporal chunks. This lowers decode working-memory demand, usually at a speed cost; tile boundaries can affect the result. It does not shrink the VAE weights, main sampler or encoder.'],
  evict_after: ['Evict models when queue drains', 'Requests release of all ComfyUI models and cached memory after this take finishes and the entire queue is idle. It also affects models loaded by other ComfyUI clients. A later job reloads what it needs.']
};
function installHelp() {
  const tooltip = document.createElement('div');
  tooltip.id = 'settingTooltip'; tooltip.className = 'setting-tooltip'; tooltip.role = 'tooltip'; tooltip.hidden = true;
  document.body.append(tooltip);
  let current = null, hideTimer;
  const hide = () => { tooltip.hidden = true; current?.removeAttribute('aria-describedby'); current = null; };
  const position = () => {
    if (!current) return;
    const rect = current.getBoundingClientRect();
    tooltip.style.left = `${Math.max(12, Math.min(rect.left, innerWidth-tooltip.offsetWidth-12))}px`;
    const below = rect.bottom+9;
    tooltip.style.top = `${below+tooltip.offsetHeight < innerHeight-12 ? below : Math.max(12, rect.top-tooltip.offsetHeight-9)}px`;
  };
  tooltip.addEventListener('mouseenter', () => clearTimeout(hideTimer));
  tooltip.addEventListener('mouseleave', () => { hideTimer = setTimeout(hide, 100); });
  for (const [id, [title, text]] of Object.entries(settingHelp)) {
    const control = $(id), target = control.closest('label') || control;
    const wrap = document.createElement('div'); wrap.className = target.tagName === 'BUTTON' ? 'help-action' : 'help-field';
    target.replaceWith(wrap); wrap.append(target);
    if (target.tagName === 'LABEL' && !target.classList.contains('check')) {
      const caption = document.createElement('span'); caption.className = 'field-caption';
      caption.textContent = target.firstChild.textContent; target.firstChild.replaceWith(caption);
    }
    const button = document.createElement('button'); button.type = 'button'; button.className = 'info-button';
    button.id = 'help-'+id; button.textContent = 'i'; button.setAttribute('aria-label', `About ${title}`);
    wrap.append(button);
    const show = () => {
      clearTimeout(hideTimer); current?.removeAttribute('aria-describedby'); current = button;
      tooltip.replaceChildren(); const heading = document.createElement('strong'); heading.textContent = title;
      const body = document.createElement('p'); body.textContent = text; tooltip.append(heading, body);
      tooltip.hidden = false; button.setAttribute('aria-describedby', tooltip.id); position();
    };
    button.addEventListener('mouseenter', show); button.addEventListener('focus', show);
    button.addEventListener('click', show);
    button.addEventListener('mouseleave', () => { hideTimer = setTimeout(hide, 120); });
    button.addEventListener('blur', hide);
  }
  document.addEventListener('keydown', event => { if (event.key === 'Escape') hide(); });
  document.addEventListener('pointerdown', event => { if (!tooltip.contains(event.target) && !event.target.closest('.info-button')) hide(); });
  document.addEventListener('scroll', position, true);
  window.addEventListener('resize', hide);
}
function renderBudget() {
  const total = Number(liveDevice?.vram_total) || 0, free = Number(liveDevice?.vram_free) || 0;
  const gib = bytes => `${(bytes/2**30).toFixed(2)} GiB`;
  $('budgetLive').textContent = total ? `Device now: ${gib(Math.max(0,total-free))} used / ${gib(total)} total · ${gib(free)} available` : 'Live VRAM unavailable while disconnected.';
  $('budgetUsed').style.width = `${total ? Math.max(0,Math.min(100,100*(total-free)/total)) : 0}%`;
  let sum = 0, gpuSum = 0, unknown = false;
  const components = [['model','H3 generator','Sampling'],['encoder','Visual / text encoder','Prompt + references'],['video_vae','Video VAE','References + video decode'],['audio_vae','Audio VAE','Audio decode'],['lora','Model LoRA','Sampling patches']];
  $('budgetComponents').innerHTML = components.map(([key,title,stage]) => {
    const name = $(key).value, cpu = key === 'encoder' && $('encoder_device').value === 'cpu';
    const inactive = key === 'lora' && (!name || Number($('lora_strength').value) === 0);
    const bytes = inactive ? 0 : weightBytes[key]?.[name];
    const known = typeof bytes === 'number';
    if (known) {sum += bytes; if(!cpu) gpuSum += bytes;} else unknown = true;
    const pct = known && total ? bytes/total*100 : null;
    return `<div class="budget-component" data-component="${key}"><div><strong>${title}</strong><span>${known ? gib(bytes) : 'Unknown size'}</span></div><small>${stage}${cpu ? ' · CPU / system RAM' : inactive ? ' · disabled' : ''}${pct !== null && !cpu ? ` · ${Math.round(pct)}% of GPU capacity (weights only)` : ''}</small><div class="budget-track ${cpu ? 'cpu' : ''}" aria-hidden="true"><span style="width:${pct === null || cpu ? 0 : Math.min(100,pct)}%"></span></div></div>`;
  }).join('');
  $('budgetTotal').textContent = `Selected weights: ${gib(sum)}${unknown ? ' + unknown sizes' : ''}. GPU-assigned baseline: ${gib(gpuSum)}${unknown ? ' + unknown sizes' : ''}${total ? ` vs ${gib(total)} capacity` : ''}.`;
  $('budgetStages').textContent = ($('encoder_device').value === 'cpu' ? 'The encoder is assigned to CPU. ' : $('free_encoder').checked ? 'The encoder is evicted before sampling. ' : 'The encoder can remain cached; ComfyUI may still offload it. ') + ($('tiled').checked ? 'Tiled decode limits video decode buffers. ' : '') + 'File sizes are read from the local installation; externally configured model paths may show unknown sizes.';
}
installHelp();
$('settings').addEventListener('submit', event => {event.preventDefault(); act($('generate'), async () => {const job = await api('/api/generate', config()); notice(`Queued ${job.config.name} · seed ${job.config.seed}`); await poll();});});
$('settings').addEventListener('input', event => {if (['pipelinePreset','architecture'].includes(event.target.id)) return; updateCanvas(); saveDraft();});
$('settings').addEventListener('change', saveDraft);
$('pipelinePreset').addEventListener('change', () => {
  const preset = pipelinePresets.find(p => p.id === $('pipelinePreset').value);
  if (!preset) return;
  applySettings({...config(), ...preset.settings});
  if (!preset.available) notice('This preset needs its checkpoint and conditioning node installed. Refresh the connection after installation.', true);
});
$('architecture').addEventListener('change', () => {
  architectureControls($('model').value, $('mode').value, false);
  if ($('architecture').value !== 'fl2va' && /fl2v/i.test($('lora').value)) $('lora').value = '';
  updateCanvas(); saveDraft();
});
$('mode').addEventListener('change', () => { updateCanvas(); saveDraft(); });
$('addIdentity').addEventListener('click', () => {try {addIdentity($('identityPicker').value);} catch(error) {notice(error.message,true);}});
$('identityList').addEventListener('click', event => {
  const button = event.target.closest('[data-remove-identity]'); if (!button) return;
  identityImages = identityImages.filter(id => id !== button.dataset.removeIdentity); renderIdentity(); saveDraft();
});
$('uploadIdentity').addEventListener('change', async event => {
  try { for (const file of event.target.files) await upload('identity', file); }
  catch(error) {notice(error.message,true);} finally {event.target.value='';}
});
$('identityBox').addEventListener('dragover', event => event.preventDefault());
$('identityBox').addEventListener('drop', async event => {
  event.preventDefault(); try { for (const file of event.dataTransfer.files) await upload('identity',file); } catch(error) {notice(error.message,true);}
});
$('quality').addEventListener('change', () => {const preset = {draft:[608,352,2],square:[512,512,2],native:[1344,768,5]}[$('quality').value]; if(preset){[$('width').value,$('height').value,$('seconds').value] = preset; updateCanvas(); saveDraft();}});
for (const id of ['width','height']) $(id).addEventListener('input', () => {$('quality').value='custom';});
function clearLastFrame() { $('last').value = ''; $('mode').value = frameMode('first'); updateCanvas(); saveDraft(); }
$('clearLast').addEventListener('click', clearLastFrame);
$('last').addEventListener('change', () => { if (!$('last').value) clearLastFrame(); });
$('copyFirst').addEventListener('click', () => {$('last').value=$('first').value;$('mode').value=frameMode('both');updateCanvas();saveDraft();});
for (const slot of ['first','last']) {
  $('upload'+slot[0].toUpperCase()+slot.slice(1)).addEventListener('change', async event => {try {await upload(slot,event.target.files[0]);}catch(error){notice(error.message,true);}finally{event.target.value='';}});
  $(slot+'Box').addEventListener('dragover',event=>event.preventDefault());
  $(slot+'Box').addEventListener('drop',async event=>{event.preventDefault();try{await upload(slot,event.dataTransfer.files[0]);}catch(error){notice(error.message,true);}});
}
$('randomize').addEventListener('click', () => {const value = new Uint32Array(1);crypto.getRandomValues(value);$('seed').value=value[0];saveDraft();});
$('turbo').addEventListener('click', () => {const lora = (options.lora || []).find(name => /h3.*fl2v.*turbo/i.test(name)); if(!lora){notice('No compatible H3 FL2V turbo LoRA is installed.',true);return;}$('lora').value=lora;$('lora_strength').value=1;$('steps').value=8;$('sampler').value='euler';$('scheduler').value='beta';saveDraft();notice('Turbo experiment selected: installed LoRA, 8 steps, Euler / beta.');});
$('base').addEventListener('click',()=>{$('lora').value='';$('steps').value=20;$('sampler').value='res_multistep';$('scheduler').value='simple';saveDraft();});
$('start').addEventListener('click',()=>act($('start'),async()=>notice((await api('/api/start',{})).message)));
$('connect').addEventListener('click',()=>act($('connect'),async()=>{await loadCapabilities(true);await poll();notice('Model catalogue refreshed.');}));
$('warmup').addEventListener('click',()=>act($('warmup'),async()=>{await api('/api/warmup',config());notice('Warm-up queued: one sampling step with native audio. This is a pipeline test, not an animation-quality sample.');await poll();}));
$('evict').addEventListener('click',()=>act($('evict'),async()=>{notice((await api('/api/evict',{})).message);await poll();}));
$('savePreset').addEventListener('click',()=>downloadJson(config(),'h3-settings.json'));
$('loadPreset').addEventListener('change',async event=>{try{const file=event.target.files[0];if(!file)return;const parsed=JSON.parse(await file.text());const c=parsed.config||parsed;if(!c||typeof c!=='object'||!('prompt' in c))throw new Error('Choose an exported settings or provenance JSON.');applySettings(c);notice('Settings loaded. Missing reference files must be uploaded again.');}catch(error){notice(error.message,true);}finally{event.target.value='';}});
async function saveVideoFrame(endpoint, useAsAnchor) {
  const id = selected;
  if (!id) throw new Error('Select a completed take first.');
  if (useAsAnchor && $('architecture').value === 'ref2va') throw new Error('Select a Hybrid or FL2VA preset to use an exact frame anchor. Hybrid also supports character references.');
  const source = jobs.find(job => job.id === id);
  notice(`Extracting the ${endpoint} video frame…`);
  const ref = await api(`/api/jobs/${encodeURIComponent(id)}/${endpoint}-frame`, {});
  await loadReferences();
  if (useAsAnchor) {
    const target = endpoint === 'first' ? 'last' : 'first';
    const hybrid = $('architecture').value === 'hybrid';
    if (endpoint === 'first' && hybrid && !identityImages.length) identityImages = [...(source?.config.identity_images || [])];
    $(target).value = ref.id; $(endpoint).value = '';
    $('mode').value = target + (hybrid ? '_reference' : '');
    $('width').value = ref.width; $('height').value = ref.height;
    $('quality').value = 'custom';
    await updateCanvas(); saveDraft();
    if (endpoint === 'first') {
      notice('First frame selected as the last frame of a lead-in. Canvas size matched and old starting frame cleared. ' + (hybrid ? 'Keep or add character references. ' : 'Choose a Hybrid preset for character references. ') + 'Review Picture numbers and describe the action leading into this pose, then Generate when ready.');
      $('lastBox').scrollIntoView({behavior:'smooth', block:'center'});
    } else {
      notice('Last frame selected as the next first frame. Canvas size matched and old endpoint cleared. Edit the prompt, then Generate when ready.');
    }
  } else {
    const link = document.createElement('a');
    link.href = `/api/references/${encodeURIComponent(ref.id)}/image`;
    link.download = `${id}-${endpoint}-frame.png`; link.click();
    notice(`${endpoint === 'first' ? 'First' : 'Last'} frame saved as PNG and added to the reference shelf.`);
  }
}
for (const endpoint of ['first', 'last']) {
  const label = endpoint === 'first' ? 'First' : 'Last';
  for (const [action, useAsAnchor] of [['save', false], ['use', true]]) {
    const button = $(action + label + 'Frame');
    button.addEventListener('click', () => act(button, () => saveVideoFrame(endpoint, useAsAnchor)));
  }
}
let pendingDeletion = null;
async function permanentlyDelete(path) {
  const result = await api(path, undefined, 'DELETE');
  const removed = new Set(result.removed);
  if (removed.has(selected)) clearSelectedVideo();
  jobs = jobs.filter(job => !removed.has(job.id));
  renderHistory(); notice(result.message); await poll();
}
$('emptyTrash').addEventListener('click', () => act($('emptyTrash'), () => permanentlyDelete('/api/jobs/trash')));
function clearSelectedVideo() {
  selected = null; $('video').pause(); $('video').removeAttribute('src'); $('video').load();
  $('video').style.display = 'none'; $('videoEmpty').hidden = false; $('downloads').hidden = true;
  $('selectedInfo').textContent = 'Select a completed take to review it.';
}
function requestDelete(job) {
  pendingDeletion = job;
  const locations = job.config.output_folder
    ? `ComfyUI’s output and ${job.config.output_folder}`
    : 'ComfyUI’s output';
  $('deleteDescription').textContent = `Delete every recorded generated video copy for “${job.config.name}” from ${locations}? Its settings remain in history for reuse.`;
  $('deleteDialog').showModal();
}
$('closeDelete').addEventListener('click', () => { pendingDeletion = null; $('deleteDialog').close(); });
$('cancelDelete').addEventListener('click', () => { pendingDeletion = null; $('deleteDialog').close(); });
$('confirmDelete').addEventListener('click', () => act($('confirmDelete'), async () => {
  if (!pendingDeletion) return;
  const job = pendingDeletion;
  const result = await api(`/api/jobs/${job.id}`, undefined, 'DELETE');
  if (selected === job.id) clearSelectedVideo();
  $('deleteDialog').close(); pendingDeletion = null; notice(result.message); await poll();
}));
$('takeHistory').addEventListener('click',event=>{const button=event.target.closest('button[data-action]');if(!button)return;const job=jobs.find(j=>j.id===button.dataset.id);if(button.dataset.action==='purge')act(button,()=>permanentlyDelete(`/api/jobs/${encodeURIComponent(job.id)}`));if(button.dataset.action==='delete')requestDelete(job);if(button.dataset.action==='rename')requestRename(job);if(button.dataset.action==='export')act(button,async()=>{notice((await api(`/api/jobs/${job.id}/export`,{})).message);await poll();});if(button.dataset.action==='play')showTake(job.id);if(button.dataset.action==='reuse'){applySettings({...job.config,random_seed:false});$('quality').value='custom';notice('Settings restored with the original seed. Generation is not queued until you click Generate.');}if(button.dataset.action==='cancel')act(button,async()=>{const result=await api(`/api/jobs/${job.id}/cancel`,{});notice(result.cancelled?'Cancellation requested for this take.':'Take already finished or left the queue.');await poll();});});
$('loopPlayback').addEventListener('change',()=>{$('video').loop=$('loopPlayback').checked;});
$('speed').addEventListener('change',()=>{$('video').playbackRate=Number($('speed').value);});
for(const [id,delta] of [['prevFrame',-1],['nextFrame',1]]) $(id).addEventListener('click',()=>{const video=$('video');if(!Number.isFinite(video.duration))return;video.pause();video.currentTime=Math.max(0,Math.min(video.duration,video.currentTime+delta/24));});
$('logButton').addEventListener('click',()=>act($('logButton'),async()=>{$('logText').textContent=(await api('/api/log')).log;$('logDialog').showModal();}));
$('closeLog').addEventListener('click',()=> $('logDialog').close());
(async()=>{await loadOutputFolders();await loadReferences();try{await loadCapabilities();}catch(error){notice('ComfyUI is offline. Start it above, then refresh the connection.',true);}await poll();setInterval(poll,2000);})().catch(error=>notice(error.message,true));
