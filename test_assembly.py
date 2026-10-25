import asyncio
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import av
from aiohttp.test_utils import AioHTTPTestCase
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_server
from server import Workbench, DEFAULTS


@unittest.skipUnless(shutil.which('ffmpeg'), 'FFmpeg required for real assembly encoding tests')
class AssemblyTests(AioHTTPTestCase):
    get_application = test_server.ApiTests.get_application
    post = test_server.ApiTests.post

    async def asyncTearDown(self):
        for key in list(self.bench.assembly.tasks):
            self.bench.assembly.stop(key)
        await asyncio.gather(*list(self.bench.assembly.tasks.values()), return_exceptions=True)
        await super().asyncTearDown()
        self.temp.cleanup()

    async def clip(self, name, color, size, audio=True, seconds=0.5, patterned=False):
        path = self.bench.home / 'ComfyUI/output/video' / (name + '.mp4')
        path.parent.mkdir(parents=True, exist_ok=True)
        args = [shutil.which('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', f'color=c={color}:s={size}:r=24:d={seconds}']
        if audio:
            args += ['-f', 'lavfi', '-i', f'sine=frequency=440:sample_rate=48000:duration={seconds}', '-c:a', 'aac']
        if patterned:
            args += ['-vf', 'drawbox=x=0:y=0:w=iw:h=16:color=red:t=fill,drawbox=x=0:y=112:w=iw:h=16:color=red:t=fill']
        args += ['-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p', '-t', str(seconds), str(path)]
        await asyncio.to_thread(subprocess.run, args, check=True, capture_output=True)
        job = {'id':name, 'prompt_id':'prompt-'+name, 'name':name, 'created':time.time(), 'status':'completed',
               'config':{**DEFAULTS,'name':name}, 'outputs':[{'filename':path.name,'subfolder':'video','type':'output'}]}
        self.bench.jobs[name] = job
        self.bench.save_job(job)
        return path

    async def payload(self, clips, **kwargs):
        folder = str(Path(self.temp.name) / 'assembly output')
        response = await self.post('/api/assembly/folders', {'action':'add','path':folder})
        self.assertEqual(response.status,200)
        return {'name':'Joined fixture', 'clips':clips, 'output_folder':folder, **kwargs}

    async def finished(self, response):
        self.assertEqual(response.status,202,await response.text())
        job = await response.json()
        task = self.bench.assembly.tasks.get(job['id'])
        if task:
            await asyncio.wait_for(task,20)
        actual = self.bench.assembly.jobs[job['id']]
        self.assertEqual(actual['status'],'completed',actual)
        return actual, self.bench.assembly.output_path(actual)

    async def test_largest_canvas_crop_confirmation_order_trims_and_audio(self):
        blue = await self.clip('blue','blue','128x128',False,patterned=True)
        red = await self.clip('red','red','192x128')
        green = await self.clip('green','lime','96x64')
        hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (blue,red,green)]
        body = await self.payload([{'job_id':'blue','start':0,'end':0.25}, {'job_id':'red','start':0,'end':0.5}, {'job_id':'green','start':0.25,'end':0.5}])
        rejected = await self.post('/api/assembly/exports',body)
        self.assertEqual(rejected.status,400)
        self.assertIn('Aspect ratios differ',(await rejected.json())['error'])
        self.assertEqual(self.bench.assembly.jobs,{})
        body['confirm_crop']=True
        job,path = await self.finished(await self.post('/api/assembly/exports',body))
        self.assertEqual((job['width'],job['height']),(192,128))
        self.assertEqual(job['duration'],1)
        self.assertEqual(job['cropped_clips'],['blue'])
        with av.open(str(path)) as media:
            frames=list(media.decode(video=0))
            self.assertEqual(len(frames),24)
            colors=[frames[i].to_image().getpixel((96,64)) for i in [0,6,18]]
            self.assertGreater(colors[0][2],200)
            self.assertGreater(frames[0].to_image().getpixel((96,5))[2],200)
            self.assertGreater(colors[1][0],200)
            self.assertGreater(colors[2][1],200)
        with av.open(str(path)) as media:
            self.assertEqual(len(media.streams.audio),1)
            samples=[]
            for frame in media.decode(audio=0):
                samples.extend(frame.to_ndarray()[0].tolist())
            self.assertLess(sum(abs(v) for v in samples[:8000])/8000,0.001)
            self.assertGreater(sum(abs(v) for v in samples[18000:22000])/4000,0.01)
            self.assertAlmostEqual(len(samples)/48000,1,delta=0.06)
        self.assertEqual(hashes,[hashlib.sha256(p.read_bytes()).hexdigest() for p in (blue,red,green)])
        self.assertEqual(self.calls,[])
        response=await self.client.get(f'/api/assembly/exports/{job["id"]}/video',headers={'Host':'127.0.0.1:8788','Range':'bytes=0-99'})
        self.assertEqual(response.status,206)
        self.assertTrue((path.parent/(job['id']+'-assembly.json')).exists())
        response=await self.client.delete(f'/api/assembly/exports/{job["id"]}',headers={'Host':'127.0.0.1:8788'})
        self.assertEqual(response.status,200)
        self.assertFalse(path.exists())
        self.assertTrue(all(p.exists() for p in (blue,red,green)))

    async def test_repeated_source_muted_export_and_folder_isolation(self):
        original = json.dumps(self.bench.output_settings,sort_keys=True)
        source=await self.clip('repeat','red','96x64')
        body=await self.payload([{'job_id':'repeat','start':0,'end':0.5},{'job_id':'repeat','start':0,'end':0.5}],audio=False)
        await self.post('/api/assembly/folders',{'action':'default','path':body['output_folder']})
        self.assertEqual(json.dumps(self.bench.output_settings,sort_keys=True),original)
        job,path=await self.finished(await self.post('/api/assembly/exports',body))
        with av.open(str(path)) as media:
            self.assertFalse(media.streams.audio)
            self.assertEqual(len(list(media.decode(video=0))),24)
        restored=Workbench(self.bench.home,self.bench.data,8788,8188)
        self.assertEqual(restored.assembly.folders['default'],body['output_folder'])
        self.assertEqual(restored.assembly.jobs[job['id']]['status'],'completed')
        self.assertEqual(restored.output_settings,self.bench.output_settings)
        self.assertTrue(source.exists())

    async def test_invalid_inputs_and_origin_do_not_start_exports(self):
        source=await self.clip('valid','red','96x64')
        body=await self.payload([{'job_id':'valid'}])
        for bad in [{'clips':[]},{'clips':[{'job_id':'missing'}]},{'clips':[{'job_id':'valid','start':-1}]},
                    {'clips':[{'job_id':'valid','start':0.3,'end':0.2}]},{'clips':[{'job_id':'valid','end':2}]},
                    {'clips':[{'job_id':'valid','end':float('nan')}]},{'output_folder':str(Path(self.temp.name)/'unregistered')}]:
            with self.subTest(bad=bad):
                response=await self.post('/api/assembly/exports',{**body,**bad})
                self.assertEqual(response.status,400,await response.text())
        response=await self.post('/api/assembly/exports',body,Origin='https://example.com')
        self.assertEqual(response.status,403)
        self.assertEqual(self.bench.assembly.jobs,{})
        self.assertEqual(self.calls,[])

    async def test_paused_frame_clip_has_inclusive_exact_endpoint_and_survives_source_deletion(self):
        original = await self.clip('motion','red','96x64',seconds=1)
        await asyncio.to_thread(subprocess.run, [shutil.which('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'lavfi', '-i', 'testsrc2=s=96x64:r=24:d=1', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=1',
            '-c:v', 'libx264', '-threads', '2', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-t', '1', str(original)], check=True, capture_output=True)
        source_hash = hashlib.sha256(original.read_bytes()).hexdigest()
        with av.open(str(original)) as media:
            original_frames = [frame.to_image() for frame in media.decode(video=0)]
        destination = (await self.payload([{'job_id':'motion'}]))['output_folder']
        response = await self.post('/api/assembly/clip', {'job_id':'motion', 'name':'Good part', 'start':2/24, 'frame_index':8, 'output_folder':destination})
        job,path = await self.finished(response)
        saved = self.bench.jobs[job['id']]
        self.assertEqual(saved['kind'],'clip')
        self.assertEqual(saved['config']['name'],'Good part')
        self.assertEqual(saved['config']['frames'],7)
        self.assertEqual((saved['config']['width'],saved['config']['height']),(96,64))
        self.assertEqual(saved['source_clip']['frame_end'],8)
        with av.open(str(path)) as media:
            self.assertTrue(media.streams.audio)
            frames = [f.to_image() for f in media.decode(video=0)]
        self.assertEqual(len(frames),7)
        self.assertEqual(frames[0].tobytes(),original_frames[2].tobytes())
        self.assertEqual(frames[-1].tobytes(),original_frames[8].tobytes())
        self.assertEqual(source_hash,hashlib.sha256(original.read_bytes()).hexdigest())
        response = await self.client.get('/api/status',headers={'Host':'127.0.0.1:8788'})
        self.assertIn(saved['id'],[j['id'] for j in (await response.json())['jobs']])
        response = await self.client.get('/api/assembly/library',headers={'Host':'127.0.0.1:8788'})
        self.assertIn(saved['id'],[j['id'] for j in await response.json()])
        response = await self.client.get('/api/assembly/exports',headers={'Host':'127.0.0.1:8788'})
        self.assertNotIn(saved['id'],[j['id'] for j in (await response.json())['jobs']])
        response = await self.client.delete('/api/jobs/motion',headers={'Host':'127.0.0.1:8788'})
        self.assertEqual(response.status,200)
        self.assertTrue(path.exists())
        response = await self.post(f'/api/jobs/{saved["id"]}/last-frame',{})
        self.assertEqual(response.status,200,await response.text())
        ref = await response.json()
        image = self.bench.data/'refs'/(ref['id']+'.png')
        from PIL import Image
        with Image.open(image) as png:
            self.assertEqual(png.convert('RGB').tobytes(),original_frames[8].tobytes())
        renamed = await self.post(f'/api/jobs/{saved["id"]}/rename',{'name':'Useful endpoint'})
        self.assertEqual(renamed.status,200)
        restored = Workbench(self.bench.home,self.bench.data,8788,8188)
        self.assertEqual(restored.jobs[saved['id']]['config']['name'],'Useful endpoint')
        self.assertEqual(restored.refs[ref['id']]['name'],'Useful endpoint - last frame')
        record = json.loads((Path(destination)/(saved['id']+'-record.json')).read_text())
        self.assertEqual(record['config']['name'],'Useful endpoint')
        self.assertEqual(self.calls,[])
        response = await self.client.delete(f'/api/jobs/{saved["id"]}',headers={'Host':'127.0.0.1:8788'})
        self.assertEqual(response.status,200)
        self.assertFalse(path.exists())
        self.assertTrue(image.exists())

    async def test_saved_clip_collision_preserves_existing_file(self):
        await self.clip('collision','red','96x64')
        destination=(await self.payload([{'job_id':'collision'}]))['output_folder']
        job=self.bench.assembly.prepare_clip({'job_id':'collision','name':'Cut','start':0,'frame_index':3,'output_folder':destination})
        path=Path(destination)/job['filename']
        path.write_bytes(b'existing unrelated video')
        await self.bench.assembly.render_clip(job)
        self.assertEqual(job['status'],'failed')
        self.assertEqual(path.read_bytes(),b'existing unrelated video')
        self.assertNotIn(job['id'],self.bench.jobs)

    async def test_save_clip_first_and_final_frame_boundaries(self):
        await self.clip('bounds','red','96x64')
        destination=(await self.payload([{'job_id':'bounds'}]))['output_folder']
        for index,count in [(0,1),(11,12)]:
            job,path=await self.finished(await self.post('/api/assembly/clip',{'job_id':'bounds','name':f'Cut {index}', 'start':0,'frame_index':index,'output_folder':destination}))
            with av.open(str(path)) as media:
                self.assertEqual(len(list(media.decode(video=0))),count)
            self.assertEqual(self.bench.jobs[job['id']]['config']['frames'],count)

    async def test_save_clip_and_rename_validate_before_mutation(self):
        original=await self.clip('validation','red','96x64')
        original_hash=hashlib.sha256(original.read_bytes()).hexdigest()
        destination=(await self.payload([{'job_id':'validation'}]))['output_folder']
        body={'job_id':'validation','name':'Good','start':0,'frame_index':2,'output_folder':destination}
        for bad in [{'frame_index':-1},{'frame_index':12},{'frame_index':2.5},{'frame_index':'2'}, {'start':1}, {'name':' '}, {'name':'a'*101}, {'job_id':'missing'}]:
            response=await self.post('/api/assembly/clip',{**body,**bad})
            self.assertEqual(response.status,400,await response.text())
        response=await self.post('/api/assembly/clip',body,Origin='https://example.com')
        self.assertEqual(response.status,403)
        self.assertEqual(self.bench.assembly.jobs,{})
        for name in ['', '   ', 'a'*101, 4, 'bad\nname']:
            response=await self.post('/api/jobs/validation/rename',{'name':name})
            self.assertEqual(response.status,400)
        response=await self.post('/api/jobs/validation/rename',{'name':'New'},Origin='https://example.com')
        self.assertEqual(response.status,403)
        self.assertEqual(self.bench.jobs['validation']['config']['name'],'validation')
        response=await self.post('/api/jobs/validation/rename',{'name':'Descriptive old take'})
        self.assertEqual(response.status,200)
        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(),original_hash)
        self.assertEqual(self.bench.jobs['validation']['config']['name'],'Descriptive old take')
        self.assertEqual(self.bench.jobs['validation']['outputs'][0]['filename'],'validation.mp4')
        self.assertEqual(self.calls,[])

    async def test_cancel_terminates_export_and_protects_source_during_work(self):
        source=await self.clip('cancel','red','96x64',seconds=2)
        body=await self.payload([{'job_id':'cancel'}])
        original=self.bench.assembly.command
        async def slow(job,args,offset,length):
            return await original(job,['-re',*args],offset,length)
        self.bench.assembly.command=slow
        response=await self.post('/api/assembly/exports',body)
        self.assertEqual(response.status,202)
        job=await response.json()
        task=self.bench.assembly.tasks[job['id']]
        for _ in range(100):
            if self.bench.assembly.processes: break
            await asyncio.sleep(0.01)
        response=await self.client.delete('/api/jobs/cancel',headers={'Host':'127.0.0.1:8788'})
        self.assertEqual(response.status,400)
        self.assertIn('assembly export',(await response.json())['error'])
        response=await self.post(f'/api/assembly/exports/{job["id"]}/cancel',{})
        self.assertEqual(response.status,200)
        await asyncio.wait_for(task,10)
        self.assertEqual(self.bench.assembly.jobs[job['id']]['status'],'cancelled')
        self.assertFalse(self.bench.assembly.processes)
        self.assertTrue(source.exists())
        self.assertFalse(list(Path(body['output_folder']).glob('.h3-assembly-*')))
        self.assertEqual(self.calls,[])


if __name__=='__main__':
    unittest.main()
