import argparse
import json
from pathlib import Path
import time
import urllib.request

BASE='http://127.0.0.1:8792'
def request(path, data=None, base=BASE):
    req=urllib.request.Request(base+path, data=json.dumps(data).encode() if data is not None else None,
                               headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=90) as response:
        return json.load(response)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--preset',default='hybrid_balanced');args=parser.parse_args()
    queue=request('/queue',base=request('/api/status')['comfy_url'])
    if queue['queue_running'] or queue['queue_pending']:
        raise RuntimeError('ComfyUI is busy; not submitting the integration test.')
    caps=request('/api/capabilities?refresh=1')
    preset=next(item for item in caps['presets'] if item['id']==args.preset)
    assert preset['available'],preset
    refs=request('/api/references')
    if len(refs)<3 or any(ref.get('source')!='synthetic hybrid integration fixture' for ref in refs[:3]):
        raise RuntimeError('The integration test requires its isolated synthetic reference shelf.')
    c={**caps['defaults'],**preset['settings'],'name':'Hybrid integration smoke '+args.preset,
       'first':refs[0]['id'],'identity_images':[refs[1]['id'],refs[2]['id']],
       'width':256,'height':256,'seconds':0.9,'steps':2,'seed':617,
       'prompt':'A painted toy robot stands still, then raises one arm slightly. Begin from <Picture 1>. Keep the red head, blue body and white eyes of <Subject 1> from <Picture 2> and <Picture 3>. Locked camera.',
       'sound':'Quiet room tone.','music':'No music.'}
    job=request('/api/generate',c)
    print('H3_SMOKE_QUEUED',job['id'],job['prompt_id'],flush=True)
    output=Path(__file__).parent/'.artifacts'/('smoke-'+args.preset+'.json')
    output.write_text(json.dumps(job,indent=2),encoding='utf-8')
    started=time.monotonic(); last=''
    while time.monotonic()-started < 1800:
        state=request('/api/status'); current=next(item for item in state['jobs'] if item['id']==job['id'])
        status=json.dumps({'status':current['status'],'progress':current.get('progress')})
        if status!=last:
            print('H3_SMOKE_PROGRESS',status,flush=True);last=status
        if current['status'] not in ('queued','running','recovering'):
            if current['status']!='completed':raise RuntimeError(current.get('error',current['status']))
            record=request('/api/jobs/'+job['id']+'/record')
            output.write_text(json.dumps(record,indent=2),encoding='utf-8')
            print('H3_HYBRID_SMOKE_PASSED',args.preset,'seconds',round(time.monotonic()-started,1),flush=True)
            print(json.dumps(record.get('saved_outputs')),flush=True)
            break
        time.sleep(5)
    else:
        raise RuntimeError('Still running after 30 minutes; job was left intact.')
