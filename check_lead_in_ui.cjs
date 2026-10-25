// Isolated browser integration check. No ComfyUI connection or GPU generation.
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const {spawn} = require('child_process');
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');

const fixture = String.raw`
import copy, io, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import av
from aiohttp import web
from PIL import Image
from server import Workbench, DEFAULTS, make_app
from test_server import node_catalogue
root = Path(sys.argv[2])
port = int(sys.argv[3])
bench = Workbench(root, root / 'data', port, 1)
refs = []
for name, color in [('Character artwork', 'blue'), ('Extra reference', 'yellow')]:
    blob = io.BytesIO()
    Image.new('RGB', (128, 96), color).save(blob, format='PNG')
    refs.append(bench.add_reference(blob.getvalue(), name))
output = root / 'ComfyUI/output'
output.mkdir(parents=True)
with av.open(str(output / 'fixture.mp4'), 'w') as container:
    stream = container.add_stream('libx264', rate=24)
    stream.width, stream.height, stream.pix_fmt = 128, 96, 'yuv420p'
    for index in range(24):
        frame = av.VideoFrame.from_image(Image.new('RGB', (128, 96), 'red' if index < 12 else 'green'))
        for packet in stream.encode(frame): container.mux(packet)
    for packet in stream.encode(): container.mux(packet)
now = time.time()
job = {'id':'lead-in-source', 'prompt_id':'fixture', 'kind':'take', 'status':'completed',
       'created':now-3800, 'started':now-3661, 'finished':now,
       'config':{**DEFAULTS, 'name':'Good take', 'width':128, 'height':96,
                 'frames':24, 'actual_seconds':1, 'identity_images':[refs[0]['id']]},
       'workflow':{}, 'references':{}, 'outputs':[{'filename':'fixture.mp4', 'type':'output'}]}
bench.jobs[job['id']] = job
bench.save_job(job)
for key, status in [('failed-source', 'failed'), ('discarded-one', 'discarded'),
                    ('discarded-two', 'discarded'), ('cancelled-source', 'cancelled')]:
    other = copy.deepcopy(job)
    other.update(id=key, prompt_id=key, status=status, outputs=[])
    other['config']['name'] = key
    if status == 'failed': other['error'] = 'Synthetic engine error.'
    bench.jobs[key] = other
    bench.save_job(other)
async def comfy(route, payload=None):
    if route == '/queue': return {'queue_running':[], 'queue_pending':[]}
    if route == '/system_stats': return {'system':{'comfyui_version':'test'}}
    if route == '/object_info': return node_catalogue()
    raise ValueError('This fixture cannot generate videos.')
bench.comfy = comfy
app = make_app(bench)
app.cleanup_ctx.clear()
web.run_app(app, host='127.0.0.1', port=port)
`;

(async () => {
  const evidence = path.join(__dirname, '.artifacts/lead-in-ui');
  fs.mkdirSync(evidence, {recursive:true});
  const state = fs.mkdtempSync(path.join(evidence, 'state-'));
  const port = Number(process.env.H3_LEAD_IN_TEST_PORT || 8793);
  const base = `http://127.0.0.1:${port}`;
  const server = spawn(process.env.H3_TEST_PYTHON || path.join(__dirname, '.runtime/python/python.exe'),
    ['-c', fixture, __dirname, state, String(port)], {windowsHide:true});
  let serverOutput = '';
  server.stdout.on('data', value => serverOutput += value);
  server.stderr.on('data', value => serverOutput += value);
  let browser;
  try {
    let ready = false;
    for (let attempt = 0; attempt < 100; attempt++) {
      if (server.exitCode !== null) throw new Error(serverOutput);
      try {
        const status = await (await fetch(base + '/api/status')).json();
        assert.equal(path.resolve(status.data_dir), path.join(state, 'data'));
        ready = true; break;
      } catch { await new Promise(resolve => setTimeout(resolve, 100)); }
    }
    assert.ok(ready, serverOutput);
    browser = await chromium.launch({headless:true, channel:'chrome'});
    const page = await browser.newPage({viewport:{width:1440, height:1080}});
    const errors = [], submissions = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/api/generate', async route => {
      submissions.push(route.request().postDataJSON());
      await route.fulfill({status:400, json:{error:'Generation captured by UI test.'}});
    });
    await page.goto(base);
    await page.waitForFunction(() => !document.querySelector('#generate').disabled);
    await page.locator('[data-action="play"][data-id="lead-in-source"]').waitFor();
    assert.match(await page.locator('#selectedInfo').textContent(), /Wall time 1h 1m 1s/);
    assert.match(await page.locator('#history').textContent(), /Wall time 1h 1m 1s/);
    assert.deepEqual(await page.evaluate(() => [
      wallTime({kind:'take', status:'completed', created:100, finished:165}),
      wallTime({kind:'take', status:'completed', created:100}),
      wallTime({kind:'clip', status:'completed', created:100, started:110, finished:165}),
      wallTime({kind:'take', status:'running', created:100, started:110})
    ]), ['Wall time 1m 5s (incl. queue)', '', '', '']);
    const refs = await (await page.request.get(base + '/api/references')).json();
    const sourceRef = refs.find(ref => ref.name === 'Character artwork').id;
    const extraRef = refs.find(ref => ref.name === 'Extra reference').id;
    const prompt = await page.locator('#prompt').inputValue();
    const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#saveFirstFrame').click()]);
    assert.equal(download.suggestedFilename(), 'lead-in-source-first-frame.png');
    await download.saveAs(path.join(evidence, download.suggestedFilename()));
    await page.waitForFunction(() => !document.querySelector('#saveFirstFrame').disabled);
    await page.locator('#useFirstFrame').click();
    await page.waitForFunction(() => document.querySelector('#notice').textContent.startsWith('First frame selected'));
    assert.equal(await page.locator('#mode').inputValue(), 'last_reference');
    assert.equal(await page.locator('#first').inputValue(), '');
    assert.equal(await page.locator('#firstBox').isVisible(), false);
    assert.equal(await page.locator('#lastBox').isVisible(), true);
    assert.equal(await page.locator('#identityBox').isVisible(), true);
    assert.equal(await page.locator('#width').inputValue(), '128');
    assert.equal(await page.locator('#height').inputValue(), '96');
    assert.equal(await page.locator('#prompt').inputValue(), prompt);
    assert.equal(await page.locator('.identity-card').count(), 1);
    const endpoint = await page.locator('#last').inputValue();
    assert.ok(endpoint);
    await page.locator('#identityPicker').selectOption(extraRef);
    await page.locator('#addIdentity').click();
    assert.deepEqual(await page.locator('.identity-card strong').allTextContents(), ['Picture 1', 'Picture 2']);
    assert.match(await page.locator('#lastTitle').textContent(), /Picture 3/);
    assert.match(await page.locator('#promptImageHint').textContent(), /End at <Picture 3>/);
    await page.locator('#useFirstFrame').click();
    await page.waitForFunction(() => !document.querySelector('#useFirstFrame').disabled);
    assert.equal(await page.locator('.identity-card').count(), 2);
    assert.equal(submissions.length, 0);
    await page.reload();
    await page.waitForFunction(() => !document.querySelector('#generate').disabled);
    await page.locator('[data-action="play"][data-id="lead-in-source"]').waitFor();
    assert.equal(await page.locator('#last').inputValue(), endpoint);
    assert.equal(await page.locator('#mode').inputValue(), 'last_reference');
    await page.locator('#generate').click();
    await page.waitForFunction(() => document.querySelector('#notice').textContent === 'Generation captured by UI test.');
    assert.equal(submissions[0].mode, 'last_reference');
    assert.equal(submissions[0].first, '');
    assert.equal(submissions[0].last, endpoint);
    assert.deepEqual(submissions[0].identity_images, [sourceRef, extraRef]);
    await page.locator('#mode').selectOption('both_reference');
    await page.locator('#first').selectOption(extraRef);
    assert.equal(await page.locator('#last').inputValue(), endpoint);
    assert.equal(await page.locator('#firstBox').isVisible(), true);
    await page.locator('#useFirstFrame').click();
    await page.waitForFunction(() => !document.querySelector('#useFirstFrame').disabled);
    assert.equal(await page.locator('#first').inputValue(), '');
    await page.locator('#takeHistory').screenshot({path:path.join(evidence, 'history-desktop.png')});
    await page.screenshot({path:path.join(evidence, 'desktop.png'), fullPage:true});
    await page.setViewportSize({width:390, height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await page.locator('#downloads').scrollIntoViewIfNeeded();
    await page.locator('#inactiveTakes summary').click();
    await page.locator('#takeHistory').screenshot({path:path.join(evidence, 'history-mobile.png')});
    await page.locator('#inactiveTakes summary').click();
    await page.screenshot({path:path.join(evidence, 'mobile.png'), fullPage:true});
    await page.locator('#pipelinePreset').selectOption('fl2va');
    await page.locator('#useFirstFrame').click();
    await page.waitForFunction(() => document.querySelector('#mode').value === 'last');
    assert.equal(await page.locator('#identityBox').isVisible(), false);
    await page.locator('#architecture').selectOption('ref2va');
    const before = await page.evaluate(() => localStorage.getItem('h3-workbench-settings'));
    await page.locator('#useFirstFrame').click();
    await page.waitForFunction(() => document.querySelector('#notice').textContent.includes('Select a Hybrid or FL2VA preset'));
    assert.equal(await page.evaluate(() => localStorage.getItem('h3-workbench-settings')), before);
    await page.locator('#pipelinePreset').selectOption('hybrid_balanced');
    await page.locator('#useLastFrame').click();
    await page.waitForFunction(() => document.querySelector('#notice').textContent.startsWith('Last frame selected'));
    assert.equal(await page.locator('#mode').inputValue(), 'first_reference');
    assert.equal(await page.locator('#last').inputValue(), '');
    assert.equal(await page.locator('.identity-card').count(), 2);
    assert.notEqual(await page.locator('#first').inputValue(), endpoint);
    const lastDownload = page.waitForEvent('download');
    await page.locator('#saveLastFrame').click();
    assert.equal((await lastDownload).suggestedFilename(), 'lead-in-source-last-frame.png');
    assert.match(await page.locator('#emptyTrash').textContent(), /Empty trash \(2\)/);
    const failedDelete = page.locator('[data-action="purge"][data-id="failed-source"]');
    assert.equal(await failedDelete.textContent(), 'Delete');
    await failedDelete.click();
    await failedDelete.waitFor({state:'detached'});
    assert.equal(await page.locator('#deleteDialog').evaluate(dialog => dialog.open), false);
    assert.equal(fs.existsSync(path.join(state, 'data/jobs/failed-source.json')), false);
    await page.reload();
    await page.locator('[data-action="play"][data-id="lead-in-source"]').waitFor();
    assert.equal(await page.locator('[data-id="failed-source"]').count(), 0);
    await page.locator('#inactiveTakes summary').click();
    const discardedDelete = page.locator('[data-action="purge"][data-id="discarded-one"]');
    await discardedDelete.click();
    await discardedDelete.waitFor({state:'detached'});
    assert.match(await page.locator('#emptyTrash').textContent(), /Empty trash \(1\)/);
    assert.equal(fs.existsSync(path.join(state, 'data/jobs/discarded-one.json')), false);
    await page.route('**/api/jobs/trash', route => route.fulfill({status:404, contentType:'text/plain', body:'404: Not Found'}));
    await Promise.all([
      page.waitForResponse(response => response.url().endsWith('/api/jobs/trash')),
      page.locator('#emptyTrash').click()
    ]);
    await page.waitForFunction(() => !document.querySelector('#emptyTrash').disabled);
    assert.match(await page.locator('#notice').textContent(), /Restart MotionBench/);
    assert.equal(fs.existsSync(path.join(state, 'data/jobs/discarded-two.json')), true);
    await page.unroute('**/api/jobs/trash');
    await page.locator('#emptyTrash').click();
    await page.locator('#emptyTrash').waitFor({state:'hidden'});
    assert.equal(fs.existsSync(path.join(state, 'data/jobs/discarded-two.json')), false);
    assert.equal(await page.locator('[data-id="cancelled-source"][data-action="reuse"]').count(), 1);
    assert.equal(await page.locator('[data-id="cancelled-source"][data-action="purge"]').count(), 0);
    await page.locator('[data-action="delete"][data-id="lead-in-source"]').click();
    assert.equal(await page.locator('#deleteDialog').evaluate(dialog => dialog.open), true);
    await page.locator('#confirmDelete').click();
    await page.locator('#deleteDialog').waitFor({state:'hidden'});
    await page.locator('#emptyTrash').waitFor({state:'visible'});
    assert.equal(fs.existsSync(path.join(state, 'data/jobs/lead-in-source.json')), true);
    assert.equal(fs.existsSync(path.join(state, 'ComfyUI/output/fixture.mp4')), false);
    await page.locator('#emptyTrash').click();
    await page.locator('#emptyTrash').waitFor({state:'hidden'});
    assert.equal(fs.existsSync(path.join(state, 'data/jobs/lead-in-source.json')), false);
    const remainingRefs = await (await page.request.get(base + '/api/references')).json();
    assert.ok(remainingRefs.some(ref => ref.id === endpoint));
    await page.reload();
    await page.locator('#inactiveTakes').waitFor();
    assert.equal(await page.locator('[data-id="lead-in-source"]').count(), 0);
    assert.deepEqual(errors, []);
    console.log('H3_LEAD_IN_UI_PASSED: timing, frame flow, references, persistence, inputs, architecture guards, desktop/mobile, direct failed deletion and permanent trash cleanup');
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
