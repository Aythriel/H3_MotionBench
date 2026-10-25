import asyncio
import copy
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import av
from aiohttp import web
from storage import atomic_json, local_folder, writable_folder, clip_name


ACTIVE = {'exporting', 'cancelling'}


def media_info(path):
    try:
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise ValueError('The source has no video stream.')
            stream = container.streams.video[0]
            duration = float(stream.duration * stream.time_base) if stream.duration is not None else 0
            if not duration and stream.frames and stream.average_rate:
                duration = float(stream.frames / stream.average_rate)
            if not duration and container.duration:
                duration = container.duration / av.time_base
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError('Could not determine the video duration.')
            return {'width': stream.width, 'height': stream.height, 'duration': duration,
                    'audio': bool(container.streams.audio), 'fps': float(stream.average_rate or 24),
                    'frames': stream.frames or round(duration * float(stream.average_rate or 24))}
    except av.FFmpegError as exc:
        raise ValueError('This video is incomplete or unreadable.') from exc


class Assembly:
    def __init__(self, bench):
        self.bench = bench
        self.data = bench.data / 'assemblies'
        self.data.mkdir(exist_ok=True)
        self.folder_file = bench.data / 'assembly-folders.json'
        default = str((bench.data.parent / 'exports').resolve())
        self.folders = bench.read(self.folder_file, {'default': default, 'folders': [default]})
        self.jobs = {}
        for path in self.data.glob('*.json'):
            job = bench.read(path, None)
            if job:
                if job['status'] in ACTIVE:
                    job.update(status='failed', error='Export interrupted by a dashboard restart. Reuse the timeline to retry.')
                    atomic_json(path, job)
                self.jobs[job['id']] = job
        self.ffmpeg = os.environ.get('H3_FFMPEG') or shutil.which('ffmpeg')
        self.tasks, self.processes, self.cancelled = {}, {}, set()
        self.metadata = {}
        self.lock = asyncio.Lock()

    def install(self, app):
        app.cleanup_ctx.append(self.lifecycle)
        app.router.add_get('/api/assembly/library', self.library)
        app.router.add_get('/api/assembly/folders', self.output_folders)
        app.router.add_post('/api/assembly/folders', self.output_folders)
        app.router.add_get('/api/assembly/exports', self.exports)
        app.router.add_post('/api/assembly/exports', self.submit)
        app.router.add_post('/api/assembly/clip', self.save_clip)
        app.router.add_post('/api/assembly/exports/{id}/cancel', self.cancel)
        app.router.add_delete('/api/assembly/exports/{id}', self.delete)
        app.router.add_get('/api/assembly/exports/{id}/{kind}', self.file)

    async def lifecycle(self, app):
        yield
        for key in list(self.tasks):
            self.stop(key)
        await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)

    def save(self, job):
        atomic_json(self.data / (job['id'] + '.json'), job)

    def source_in_use(self, key):
        return any(j['status'] in ACTIVE and any(c['job_id'] == key for c in j['clips']) for j in self.jobs.values())

    def inspect(self, job):
        path = self.bench.video_path(job)
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        if key not in self.metadata:
            self.metadata[key] = media_info(path)
        return path, self.metadata[key]

    async def library(self, request):
        result = []
        for job in sorted(list(self.bench.jobs.values()), key=lambda j: j['created'], reverse=True):
            if job['status'] != 'completed' or not job.get('outputs'):
                continue
            row = {'id': job['id'], 'name': job['config']['name'], 'created': job['created']}
            try:
                _, info = await asyncio.to_thread(self.inspect, job)
                row.update(info)
            except (ValueError, OSError) as exc:
                row['error'] = str(exc)
            result.append(row)
        return web.json_response(result)

    async def output_folders(self, request):
        if request.method == 'GET':
            return web.json_response(self.folders)
        body = await request.json()
        folder = str(local_folder(body.get('path')))
        action = body.get('action', 'add')
        async with self.lock:
            if action not in ('add', 'default'):
                raise ValueError('Unknown folder action.')
            if action == 'default' and folder not in self.folders['folders']:
                raise ValueError('Add the assembly folder first.')
            await asyncio.to_thread(writable_folder, Path(folder))
            updated = {'default': self.folders['default'], 'folders': list(self.folders['folders'])}
            if folder not in updated['folders']:
                updated['folders'].append(folder)
            if action == 'default':
                updated['default'] = folder
            atomic_json(self.folder_file, updated)
            self.folders = updated
        return web.json_response({**updated, 'selected': folder})

    async def exports(self, request):
        return web.json_response({'available': bool(self.ffmpeg and Path(self.ffmpeg).is_file()),
                                 'jobs': sorted((j for j in self.jobs.values() if j.get('kind') != 'clip' or j['status'] != 'completed'), key=lambda j: j['created'], reverse=True)})

    def prepare(self, body):
        if not isinstance(body, dict):
            raise ValueError('An assembly request must contain timeline settings.')
        if not self.ffmpeg or not Path(self.ffmpeg).is_file():
            raise ValueError('FFmpeg was not found. Open Settings to select its path or install it.')
        incoming = body.get('clips')
        if not isinstance(incoming, list) or not 1 <= len(incoming) <= 100:
            raise ValueError('Add between 1 and 100 clips to the timeline.')
        clips = []
        for item in incoming:
            if not isinstance(item, dict):
                raise ValueError('Each timeline entry must describe a source clip.')
            job = self.bench.jobs.get(item.get('job_id'))
            if not job or job['status'] != 'completed' or not job.get('outputs'):
                raise ValueError('A timeline source is no longer a completed video. Remove or replace it.')
            path, info = self.inspect(job)
            start, end = float(item.get('start', 0)), float(item.get('end', info['duration']))
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end > info['duration'] + 0.001 or end <= start:
                raise ValueError(f'Invalid trim for {job["config"]["name"]}. Use 0 <= in < out <= source duration.')
            frames = math.floor((end - start) * 24 + 0.5)
            if frames < 1:
                raise ValueError('Each clip must contain at least one output frame (1/24 second).')
            if type(item.get('mute', False)) is not bool:
                raise ValueError('Mute must be a boolean.')
            clips.append({'job_id': job['id'], 'name': job['config']['name'], 'source': str(path),
                          'start': start, 'end': end, 'frames': frames, 'duration': frames / 24,
                          'mute': item.get('mute', False), **{k: info[k] for k in ('width', 'height', 'audio')}})
        largest = max(clips, key=lambda clip: clip['width'] * clip['height'])
        sizes = {'largest': (largest['width'], largest['height']), 'first': (clips[0]['width'], clips[0]['height']), '720p': (1280, 720), '1080p': (1920, 1080), 'square': (1024, 1024)}
        canvas = body.get('canvas', 'largest')
        if canvas not in sizes:
            raise ValueError('Choose a supported output canvas.')
        width, height = sizes[canvas]
        width, height = width + width % 2, height + height % 2
        if width < 2 or height < 2 or width * height > 3840 * 2160:
            raise ValueError('The selected canvas is too large. Choose 720p or 1080p.')
        mismatches = [c['name'] for c in clips if abs((c['width'] / c['height']) / (width / height) - 1) > 0.005]
        if mismatches and body.get('confirm_crop') is not True:
            raise ValueError('Aspect ratios differ. Confirm cropping before exporting: ' + ', '.join(dict.fromkeys(mismatches)))
        audio = body.get('audio', True)
        if type(audio) is not bool:
            raise ValueError('Include audio must be a boolean.')
        folder = local_folder(body.get('output_folder'))
        if str(folder) not in self.folders['folders']:
            raise ValueError('Add the assembly output folder before exporting.')
        writable_folder(folder)
        name = str(body.get('name', 'Assembly')).strip()[:100] or 'Assembly'
        key = time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8]
        slug = re.sub(r'[^\w-]+', '-', name, flags=re.ASCII).strip('-')[:60] or 'assembly'
        filename = f'{slug}-{key}.mp4'
        return {'id': key, 'name': name, 'created': time.time(), 'status': 'exporting', 'progress': 0,
                'phase': 'Preparing clips', 'clips': clips, 'canvas': canvas, 'width': width, 'height': height,
                'crop_confirmed': bool(mismatches), 'cropped_clips': mismatches, 'audio': audio, 'fps': 24, 'duration': sum(c['duration'] for c in clips),
                'output_folder': str(folder), 'filename': filename}

    async def submit(self, request):
        body = await request.json()
        async with self.bench.lock:
            if any(j['status'] in ACTIVE for j in self.jobs.values()):
                raise ValueError('An assembly is already exporting. Wait or cancel it before starting another.')
            job = await asyncio.to_thread(self.prepare, body)
            self.save(job)
            self.jobs[job['id']] = job
            task = asyncio.create_task(self.render(job))
            self.tasks[job['id']] = task
            task.add_done_callback(lambda completed: self.tasks.pop(job['id'], None))
            return web.json_response(job, status=202)

    def prepare_clip(self, body):
        if not isinstance(body, dict):
            raise ValueError('Provide a source clip, frame and name.')
        name = clip_name(body.get('name'))
        source = self.bench.jobs.get(body.get('job_id'))
        if not source or source['status'] != 'completed' or not source.get('outputs'):
            raise ValueError('Choose a completed source clip.')
        index = body.get('frame_index')
        if type(index) is not int or index < 0:
            raise ValueError('Choose a valid paused frame.')
        path, info = self.inspect(source)
        start = float(body.get('start', 0))
        if not math.isfinite(start) or start < 0:
            raise ValueError('Choose a valid clip In point.')
        first_index = math.ceil(start * info['fps'] - 0.00001)
        if index < first_index or index >= info['frames']:
            raise ValueError('The paused frame must be within the source and at or after the In point.')
        start_time = end_time = None
        with av.open(str(path)) as container:
            container.streams.video[0].thread_count = 2
            for i, frame in enumerate(container.decode(video=0)):
                frame_time = float(frame.time) if frame.time is not None else i / info['fps']
                if i == first_index:
                    start_time = frame_time
                if i == index:
                    end_time = frame_time + 1 / info['fps']
                if i == index + 1:
                    end_time = frame_time
                    break
        if start_time is None or end_time is None:
            raise ValueError('The requested frame could not be decoded from this video.')
        prepared = self.prepare({'name': name, 'clips': [{'job_id': source['id'], 'start': start_time, 'end': min(end_time, info['duration'])}],
                                 'canvas': 'first', 'output_folder': body.get('output_folder'), 'audio': info['audio']})
        prepared.update(kind='clip', frame_start=first_index, frame_end=index, frames=index-first_index+1,
                        duration=end_time-start_time, fps=info['fps'], source_snapshot=copy.deepcopy(source))
        prepared['clips'][0].update(start=start_time, end=end_time, duration=end_time-start_time)
        return prepared

    async def save_clip(self, request):
        body = await request.json()
        async with self.bench.lock:
            if any(j['status'] in ACTIVE for j in self.jobs.values()):
                raise ValueError('Wait for the current export or cancel it before saving a clip.')
            job = await asyncio.to_thread(self.prepare_clip, body)
            self.save(job)
            self.jobs[job['id']] = job
            task = asyncio.create_task(self.render_clip(job))
            self.tasks[job['id']] = task
            task.add_done_callback(lambda completed: self.tasks.pop(job['id'], None))
            return web.json_response(job, status=202)

    async def render_clip(self, job):
        final = None
        published = False
        try:
            folder = Path(job['output_folder'])
            clip = job['clips'][0]
            with tempfile.TemporaryDirectory(prefix='.h3-clip-', dir=folder) as scratch:
                target = Path(scratch) / 'clip.mp4'
                job['phase'] = 'Saving through the selected frame'
                args = ['-threads', '2', '-i', clip['source'], '-map', '0:v:0', '-filter_threads', '1',
                        '-vf', f"trim=start_frame={job['frame_start']}:end_frame={job['frame_end']+1},setpts=PTS-STARTPTS",
                        '-fps_mode', 'passthrough', '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast', '-crf', '0', '-bf', '0']
                if job['audio']:
                    args += ['-map', '0:a:0', '-af', f"atrim=start={clip['start']}:end={clip['end']},asetpts=PTS-STARTPTS,apad,atrim=duration={job['duration']}",
                             '-c:a', 'aac', '-b:a', '192k']
                else:
                    args += ['-an']
                args += ['-t', str(job['duration']), '-movflags', '+faststart', str(target)]
                await self.command(job, args, 0, job['duration'])
                info = await asyncio.to_thread(media_info, target)
                if info['frames'] != job['frames']:
                    raise ValueError('The saved clip did not retain the expected frame count.')
                if job['id'] in self.cancelled:
                    raise asyncio.CancelledError()
                final = folder / job['filename']
                if final.exists():
                    raise ValueError('The clip filename already exists. Save again to use a new filename.')
                target.rename(final)
                published = True
                parent = job['source_snapshot']
                config = copy.deepcopy(parent['config'])
                config.update(name=job['name'], width=info['width'], height=info['height'], frames=job['frames'],
                              actual_seconds=info['duration'], output_folder=str(folder))
                saved = {'id': job['id'], 'prompt_id': 'local-clip-' + job['id'], 'kind': 'clip', 'created': time.time(),
                         'finished': time.time(), 'status': 'completed', 'config': config,
                         'workflow': copy.deepcopy(parent.get('workflow', {})), 'references': copy.deepcopy(parent.get('references', {})),
                         'outputs': [{'filename': final.name, 'type': 'saved'}], 'saved_outputs': [str(final)],
                         'source_clip': {'job_id': parent['id'], 'name': parent['config']['name'],
                                         'frame_start': job['frame_start'], 'frame_end': job['frame_end'],
                                         'start': clip['start'], 'end': clip['end'], 'fps': job['fps']}}
                self.bench.save_job(saved)
                self.bench.jobs[saved['id']] = saved
                job.update(status='completed', progress=1, phase='Saved to generation inventory', saved_clip_id=saved['id'], finished=time.time())
                try:
                    atomic_json(folder / (saved['id'] + '-record.json'), saved)
                except OSError:
                    job['warning'] = 'Clip saved in the inventory; the destination record could not be written.'
        except asyncio.CancelledError:
            job.update(status='cancelled', phase='Cancelled', finished=time.time())
        except Exception as exc:
            job.update(status='failed', error=str(exc), phase='Failed', finished=time.time())
            if published and job['id'] not in self.bench.jobs:
                final.unlink(missing_ok=True)
        finally:
            self.cancelled.discard(job['id'])
            self.save(job)

    async def command(self, job, args, offset, length):
        key = job['id']
        if key in self.cancelled:
            raise asyncio.CancelledError()
        with (self.data / (key + '.log')).open('ab') as log:
            proc = await asyncio.create_subprocess_exec(self.ffmpeg, '-hide_banner', '-nostdin', '-loglevel', 'error',
                '-y', '-progress', 'pipe:1', '-nostats', *args, stdout=asyncio.subprocess.PIPE, stderr=log,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.processes[key] = proc
            try:
                if key in self.cancelled:
                    proc.terminate()
                last_save = 0
                while line := await proc.stdout.readline():
                    if line.startswith(b'out_time_us='):
                        try:
                            seconds = int(line.split(b'=', 1)[1]) / 1_000_000
                        except ValueError:
                            continue
                        job['progress'] = min(0.99, offset + min(1, seconds / max(length, 0.01)) * length / job['duration'] * 0.9)
                        if time.monotonic() - last_save > 1:
                            self.save(job)
                            last_save = time.monotonic()
                code = await proc.wait()
                if key in self.cancelled:
                    raise asyncio.CancelledError()
                if code:
                    raise ValueError('Video export failed. A source may be missing, unreadable, or the destination may be full. See the export log.')
            finally:
                if proc.returncode is None:
                    proc.terminate()
                    await proc.wait()
                self.processes.pop(key, None)

    async def render(self, job):
        try:
            folder = Path(job['output_folder'])
            with tempfile.TemporaryDirectory(prefix='.h3-assembly-', dir=folder) as scratch:
                temp = Path(scratch)
                elapsed = 0
                entries = []
                for i, clip in enumerate(job['clips']):
                    job['phase'] = f'Encoding clip {i + 1} of {len(job["clips"])}'
                    duration = clip['duration']
                    args = ['-threads', '2', '-ss', str(clip['start']), '-i', clip['source']]
                    if job['audio'] and (clip['mute'] or not clip['audio']):
                        args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
                    args += ['-map', '0:v:0', '-filter_threads', '1', '-vf',
                        f"setpts=PTS-STARTPTS,fps=24,scale={job['width']}:{job['height']}:force_original_aspect_ratio=increase:force_divisible_by=2:flags=lanczos,crop={job['width']}:{job['height']},setsar=1,tpad=stop_mode=clone:stop_duration=1",
                        '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast', '-crf', '18', '-pix_fmt', 'yuv420p', '-bf', '0']
                    if job['audio']:
                        audio_input = '1:a:0' if clip['mute'] or not clip['audio'] else '0:a:0'
                        args += ['-map', audio_input, '-af', f'aresample=48000,apad,atrim=duration={duration},asetpts=PTS-STARTPTS',
                                 '-c:a', 'pcm_s16le', '-ar', '48000', '-ac', '2']
                    else:
                        args += ['-an']
                    part = temp / f'clip-{i:03d}.mkv'
                    args += ['-t', str(duration), str(part)]
                    await self.command(job, args, elapsed / job['duration'] * 0.9, duration)
                    elapsed += duration
                    entries += [f"file '{part.name}'", f'duration {duration:.9f}']
                listing = temp / 'clips.txt'
                listing.write_text('\n'.join(entries) + '\n', encoding='utf-8')
                job['phase'] = 'Joining clips and saving MP4'
                target = temp / 'joined.mp4'
                args = ['-threads', '2', '-f', 'concat', '-safe', '1', '-i', str(listing), '-map', '0:v:0', '-c:v', 'copy']
                args += ['-map', '0:a:0', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2'] if job['audio'] else ['-an']
                args += ['-t', str(job['duration']), '-movflags', '+faststart', str(target)]
                await self.command(job, args, 0.9, job['duration'])
                if job['id'] in self.cancelled:
                    raise asyncio.CancelledError()
                await asyncio.to_thread(media_info, target)
                final = folder / job['filename']
                if final.exists():
                    raise ValueError('The export file already exists. Export again to use a new filename.')
                target.rename(final)
                job.update(status='completed', progress=1, phase='Complete', finished=time.time())
                try:
                    atomic_json(folder / (job['id'] + '-assembly.json'), job)
                except OSError:
                    job['warning'] = 'Video saved; destination timeline record could not be written. Download the record from export history.'
        except asyncio.CancelledError:
            job.update(status='cancelled', phase='Cancelled', finished=time.time())
        except Exception as exc:
            job.update(status='failed', error=str(exc), phase='Failed', finished=time.time())
        finally:
            self.cancelled.discard(job['id'])
            self.save(job)

    def stop(self, key):
        self.cancelled.add(key)
        proc = self.processes.get(key)
        if proc and proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass

    async def cancel(self, request):
        job = self.jobs.get(request.match_info['id'])
        if not job:
            raise web.HTTPNotFound()
        if job['status'] in ACTIVE:
            job['status'] = 'cancelling'
            self.save(job)
            self.stop(job['id'])
        return web.json_response({'message': 'Assembly cancellation requested.'})

    def output_path(self, job):
        folder = local_folder(job['output_folder'])
        path = (folder / job['filename']).resolve()
        if path.parent != folder or path.suffix != '.mp4':
            raise ValueError('Invalid assembly output path.')
        return path

    async def file(self, request):
        job = self.jobs.get(request.match_info['id'])
        if not job:
            raise web.HTTPNotFound()
        kind = request.match_info['kind']
        if kind == 'record':
            return web.json_response(job, headers={'Content-Disposition': f'attachment; filename="{job["id"]}-assembly.json"'})
        if kind == 'log':
            path = self.data / (job['id'] + '.log')
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size - 12000))
                return web.json_response({'log': stream.read().decode('utf-8', 'replace')})
        if job.get('kind') == 'clip':
            raise web.HTTPNotFound()
        if kind != 'video' or job['status'] != 'completed':
            raise web.HTTPNotFound()
        path = self.output_path(job)
        if not path.is_file():
            raise ValueError('This assembly output file is missing or has been moved.')
        headers = {'Content-Disposition': f'attachment; filename="{path.name}"'} if 'download' in request.query else {}
        return web.FileResponse(path, headers=headers)

    async def delete(self, request):
        async with self.lock:
            job = self.jobs.get(request.match_info['id'])
            if job and job.get('kind') == 'clip':
                raise ValueError('Manage this saved clip from the generation inventory.')
            if not job or job['status'] != 'completed':
                raise ValueError('Only completed assembly videos can be deleted.')
            path = self.output_path(job)
            record = path.parent / (job['id'] + '-assembly.json')
            if record.exists() and self.bench.read(record, {}).get('id') != job['id']:
                raise ValueError('The destination record does not belong to this assembly.')
            path.unlink(missing_ok=True)
            record.unlink(missing_ok=True)
            job.update(status='discarded', discarded_at=time.time())
            self.save(job)
            return web.json_response({'message': 'Assembly output deleted. Source generations and timeline settings are retained.'})
