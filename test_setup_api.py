import json
from pathlib import Path
import sys
from unittest.mock import Mock, AsyncMock

from aiohttp.test_utils import AioHTTPTestCase

sys.path.insert(0, str(Path(__file__).parent))
from server import Workbench, make_app, DEFAULTS
import test_dependencies as fixtures
from test_server import node_catalogue


class SetupApiTests(AioHTTPTestCase):
    async def get_application(self):
        self.fixture = fixtures.DependencyTests()
        self.fixture.setUp()
        self.fixture.materialize()
        self.dep = self.fixture.dep
        self.bench = Workbench(self.dep.path('comfy'), self.fixture.root/'data', 8794, 8199, self.dep)
        self.calls = []
        async def comfy(route, payload=None):
            self.calls.append(route)
            if route == '/system_stats': return {'system': {'comfyui_version':'test'}}
            if route == '/queue': return {'queue_running':[], 'queue_pending':[]}
            if route == '/object_info': return node_catalogue()
            if route == '/prompt': return {'prompt_id':'synthetic'}
            return {}
        self.bench.comfy = comfy
        self.bench.setup.apply()
        app=make_app(self.bench)
        app.cleanup_ctx.clear()
        return app

    async def asyncTearDown(self):
        await self.bench.setup.close()
        await super().asyncTearDown()
        self.fixture.tearDown()

    async def post(self, path, body, **headers):
        return await self.client.post(path,json=body,headers={'Host':'127.0.0.1:8794',**headers})

    async def test_generation_checks_disk_again_before_any_upload_or_queue(self):
        self.dep.path('b25').unlink()
        response=await self.post('/api/generate',{**DEFAULTS,'mode':'text'})
        self.assertEqual(response.status,400)
        self.assertIn('Settings', (await response.json())['error'])
        self.assertNotIn('/prompt',self.calls)
        self.assertNotIn('/upload/image',self.calls)

    async def test_save_missing_path_disables_export_but_keeps_generation_preset_ready(self):
        missing=str(self.fixture.root/'missing/ffmpeg.exe')
        response=await self.post('/api/setup',{'paths':{'ffmpeg':missing},'selected_models':['b25'],'comfy_port':8199})
        self.assertEqual(response.status,200)
        data=await response.json()
        self.assertTrue(data['presets']['hybrid_balanced']['ready'])
        self.assertFalse(next(row for row in data['dependencies'] if row['id']=='ffmpeg')['valid'])
        response=await self.client.get('/api/assembly/exports',headers={'Host':'127.0.0.1:8794'})
        self.assertFalse((await response.json())['available'])

    async def test_changing_engine_preserves_old_video_location(self):
        source=self.bench.home/'ComfyUI/output/video/old.mp4'
        source.parent.mkdir(parents=True,exist_ok=True);source.write_bytes(b'original video')
        job={'id':'old','status':'completed','config':dict(DEFAULTS),'outputs':[{'filename':'old.mp4','subfolder':'video','type':'output'}]}
        self.bench.jobs['old']=job
        response=await self.post('/api/setup',{'paths':{'comfy':str(self.fixture.root/'new-comfy')},'selected_models':['b25'],'comfy_port':8199})
        self.assertEqual(response.status,200)
        self.assertEqual(self.bench.video_path(job),source)
        persisted=json.loads((self.bench.data/'jobs/old.json').read_text(encoding='utf-8'))
        self.assertEqual(persisted['comfy_home'],str(source.parents[3]))

    async def test_active_job_rejects_path_changes_without_mutating_settings(self):
        self.bench.jobs['active']={'status':'running'}
        before=dict(self.dep.paths)
        response=await self.post('/api/setup',{'paths':{'b25':'wrong'},'selected_models':['b25'],'comfy_port':8199})
        self.assertEqual(response.status,400)
        self.assertEqual(self.dep.paths,before)

    async def test_cross_origin_install_and_unknown_component_are_rejected(self):
        response=await self.post('/api/setup/install',{'only':'comfy','models':['b25']},Origin='https://untrusted.example')
        self.assertEqual(response.status,403)
        response=await self.post('/api/setup/install',{'only':'unknown','models':['b25']})
        self.assertEqual(response.status,400)
        self.assertIsNone(self.bench.setup.task)

    async def test_restart_stops_owned_engine_even_after_port_change(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.return_value = 0
        self.bench.process = process
        self.bench.process_base = 'http://127.0.0.1:8188'
        self.bench.base = 'http://127.0.0.1:8199'
        self.bench.online = False
        response_context = Mock()
        response = Mock()
        response.json = AsyncMock(return_value={'queue_running': [], 'queue_pending': []})
        response_context.__aenter__ = AsyncMock(return_value=response)
        response_context.__aexit__ = AsyncMock(return_value=False)
        self.bench.session = Mock()
        self.bench.session.get.return_value = response_context
        from aiohttp import web
        self.bench.start_comfy = AsyncMock(return_value=web.json_response({'message': 'started'}))
        result = await self.post('/api/setup/restart-engine', {})
        self.assertEqual(result.status, 200)
        process.terminate.assert_called_once()
        self.bench.session.get.assert_called_once_with('http://127.0.0.1:8188/queue')
        self.bench.start_comfy.assert_awaited_once()

    async def test_external_engine_is_not_terminated_by_restart(self):
        self.bench.online=True
        response=await self.post('/api/setup/restart-engine',{})
        self.assertEqual(response.status,400)
        self.assertIn('outside', (await response.json())['error'])


if __name__=='__main__':
    import unittest
    unittest.main()
