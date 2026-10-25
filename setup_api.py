"""Settings and installation endpoints for the local workbench."""
import asyncio
from pathlib import Path

from aiohttp import web
from installers.install import Installer


class SetupAPI:
    def __init__(self, bench, dependencies):
        self.bench, self.dependencies = bench, dependencies
        self.installer = Installer(dependencies)
        self.task = None

    def install(self, app):
        app.router.add_get('/api/setup', self.get)
        app.router.add_post('/api/setup', self.save)
        app.router.add_post('/api/setup/validate', self.validate)
        app.router.add_post('/api/setup/discover', self.discover)
        app.router.add_post('/api/setup/plan', self.plan)
        app.router.add_post('/api/setup/install', self.start_install)
        app.router.add_post('/api/setup/cancel', self.cancel)
        app.router.add_get('/api/setup/log', self.log)
        app.router.add_post('/api/setup/restart-engine', self.restart)

    def snapshot(self):
        return {**self.dependencies.snapshot(), 'installation': dict(self.installer.state)}

    def installing(self):
        return self.task is not None and not self.task.done()

    async def idle(self):
        if self.installing():
            raise ValueError('Wait for the current dependency installation or cancel it first.')
        if any(j['status'] in ('queued', 'running', 'cancelling', 'recovering') for j in self.bench.jobs.values()):
            raise ValueError('Wait for active generations before changing dependency settings.')
        if any(j['status'] in ('exporting', 'cancelling') for j in self.bench.assembly.jobs.values()):
            raise ValueError('Wait for active exports before changing dependency settings.')
        if self.bench.online:
            queue = await self.bench.comfy('/queue')
            if queue.get('queue_running') or queue.get('queue_pending'):
                raise ValueError('ComfyUI has active or queued work. Wait before changing dependencies.')

    async def get(self, request):
        return web.json_response(self.snapshot())

    async def validate(self, request):
        if self.installing():
            return web.json_response(self.snapshot())
        await asyncio.to_thread(self.dependencies.validate, True)
        self.apply()
        return web.json_response(self.snapshot())

    async def discover(self, request):
        body = await request.json()
        value = body.get('path')
        if not isinstance(value, str) or not value.strip():
            raise ValueError('Enter the ComfyUI portable directory.')
        home = self.dependencies.resolve(value)
        if not (home / 'ComfyUI/main.py').is_file():
            raise ValueError('Select the portable root containing ComfyUI/main.py and python_embeded.')
        return web.json_response({'paths': self.dependencies.discover(home)})

    def apply(self):
        dep, bench = self.dependencies, self.bench
        if bench.home != dep.path('comfy'):
            for job in bench.jobs.values():
                if 'comfy_home' not in job:
                    job['comfy_home'] = str(bench.home)
                    bench.save_job(job)
        bench.home = dep.path('comfy')
        bench.comfy_port = dep.port
        old_base = bench.base
        bench.base = f'http://127.0.0.1:{dep.port}'
        if old_base != bench.base and hasattr(bench, 'tasks'):
            bench.tasks[1].cancel()
            bench.tasks[1] = asyncio.create_task(bench.events())
        bench.info = {}
        bench.assembly.ffmpeg = str(dep.path('ffmpeg')) if dep.rows.get('ffmpeg', {}).get('valid') else None

    async def save(self, request):
        body = await request.json()
        async with self.bench.lock:
            await self.idle()
            old_paths = dict(self.dependencies.paths)
            old_port = self.dependencies.port
            self.dependencies.save(body.get('paths', {}), body.get('selected_models', ['b25']), body.get('comfy_port', 8188))
            changed = any(value != self.dependencies.paths[key] for key, value in old_paths.items() if key not in ('ffmpeg', 'archive_tool')) or old_port != self.dependencies.port
            self.dependencies.restart_required |= changed and self.bench.online
            await asyncio.to_thread(self.dependencies.validate)
            self.apply()
        return web.json_response(self.snapshot())

    async def plan(self, request):
        body = await request.json()
        return web.json_response(await asyncio.to_thread(self.dependencies.plan, body.get('models'), body.get('only')))

    async def start_install(self, request):
        body = await request.json()
        async with self.bench.lock:
            await self.idle()
            if not self.dependencies.hardware['valid']:
                raise ValueError(self.dependencies.hardware['message'])
            plan = await asyncio.to_thread(self.dependencies.plan, body.get('models'), body.get('only'))
            if plan['required_free_bytes'] > plan['free_bytes']:
                raise ValueError('Not enough free disk space for the selected downloads and extraction.')
            self.installer.cancel.clear()
            self.installer.state = {'status': 'running', 'message': 'Preparing installation', 'done': 0, 'total': 0}
            self.task = asyncio.create_task(self.run_install(plan))
        return web.json_response(self.snapshot())

    async def run_install(self, plan):
        old = dict(self.dependencies.paths)
        await asyncio.to_thread(self.installer.run, plan)
        changed = any(self.dependencies.paths[key] != value for key, value in old.items() if key not in ('ffmpeg', 'archive_tool'))
        self.dependencies.restart_required |= changed and self.bench.online
        self.apply()

    async def cancel(self, request):
        self.installer.cancel.set()
        return web.json_response({'message': 'Cancellation requested. Completed files and resumable downloads are kept.'})

    async def log(self, request):
        path = self.installer.log_path
        return web.json_response({'log': path.read_text(encoding='utf-8', errors='replace')[-30000:] if path.exists() else 'No installation log yet.'})

    async def restart(self, request):
        async with self.bench.lock:
            await self.idle()
            owned = self.bench.process and self.bench.process.poll() is None
            if self.bench.online and (not owned or self.bench.process_base != self.bench.base):
                raise ValueError('This ComfyUI was started outside this MotionBench session. Close its process, then click Start ComfyUI here to apply the configured paths.')
            if owned:
                if self.bench.process_base != self.bench.base:
                    async with self.bench.session.get(self.bench.process_base + '/queue') as response:
                        queue = await response.json()
                    if queue.get('queue_running') or queue.get('queue_pending'):
                        raise ValueError('The previous ComfyUI port has active work. Wait before restarting it.')
                self.bench.process.terminate()
                await asyncio.to_thread(self.bench.process.wait, 20)
                self.bench.online = False
            self.dependencies.restart_required = False
            return await self.bench.start_comfy(request)

    async def close(self):
        if self.installing():
            self.installer.cancel.set()
            await self.task
