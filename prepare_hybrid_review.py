from pathlib import Path
import io
import sys
import time
sys.path.insert(0, str(Path(__file__).parent))
import av
from PIL import Image, ImageDraw
from server import Workbench, DEFAULTS, DEFAULT_COMFY
from storage import atomic_json

root = Path(__file__).parent
bench = Workbench(DEFAULT_COMFY, root / '.artifacts/review-state', 8792, 8188)
folder = root / '.artifacts/review-output'
folder.mkdir(parents=True, exist_ok=True)
atomic_json(bench.data / 'output-folders.json', {'default':str(folder), 'folders':[str(folder)]})
refs = []
for name, size in [('Starting pose', (256,256)), ('Canonical robot', (192,256)), ('Face detail', (128,128))]:
    image = Image.new('RGB',size,'#c5d1e0')
    draw = ImageDraw.Draw(image)
    w,h = size
    draw.rounded_rectangle((w*.32,h*.1,w*.68,h*.42),radius=8,fill='#d25135')
    draw.rectangle((w*.26,h*.45,w*.74,h*.78),fill='#356f97')
    draw.rectangle((w*.30,h*.78,w*.43,h*.96),fill='#284559')
    draw.rectangle((w*.57,h*.78,w*.7,h*.96),fill='#284559')
    draw.ellipse((w*.38,h*.22,w*.45,h*.29),fill='white')
    draw.ellipse((w*.55,h*.22,w*.62,h*.29),fill='white')
    blob=io.BytesIO(); image.save(blob,format='PNG')
    refs.append(bench.add_reference(blob.getvalue(),name,'synthetic hybrid integration fixture'))
video=folder/'synthetic-review.mp4'
with av.open(str(video),'w') as container:
    stream=container.add_stream('libx264',rate=24)
    stream.width=stream.height=256; stream.pix_fmt='yuv420p'
    source=Image.open(bench.data/'refs'/(refs[0]['id']+'.png')).convert('RGB')
    for index in range(22):
        frame=av.VideoFrame.from_image(source)
        for packet in stream.encode(frame): container.mux(packet)
    for packet in stream.encode(): container.mux(packet)
config={**DEFAULTS,'mode':'text','name':'Synthetic review clip','width':256,'height':256,
        'frames':22,'actual_seconds':22/24,'output_folder':str(folder)}
bench.save_job({'id':'synthetic-review','prompt_id':'synthetic-review','created':time.time(),'status':'completed',
                'kind':'take','config':config,'workflow':{'mux':{'inputs':{'audio':['decode_audio',0]}}},
                'references':{},'outputs':[{'filename':video.name,'subfolder':'hybrid-test','type':'output'}],
                'saved_outputs':[str(video)]})
print('H3_HYBRID_REVIEW_FIXTURES_READY')
