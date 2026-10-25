"""Verified, resumable installation into this tool's managed directories."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dependencies import Dependencies, NO_WINDOW, sha256
from storage import atomic_json
from installers.patches import patch_core, patch_hybrid


class Cancelled(Exception):
    pass


def download(spec, target, cancel, progress):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size == spec['size'] and sha256(target) == spec['sha256']:
        progress('Using verified download', spec['size'], spec['size'])
        return target
    partial = target.with_suffix(target.suffix + '.partial')
    for attempt in range(3):
        if cancel.is_set():
            raise Cancelled('Installation cancelled. Partial downloads are kept for Resume.')
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > spec['size']:
            partial.unlink()
            offset = 0
        if offset == spec['size']:
            break
        headers = {'User-Agent': 'H3-MotionBench-Installer/1', 'Accept-Encoding': 'identity'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
        try:
            with urllib.request.urlopen(urllib.request.Request(spec['url'], headers=headers), timeout=30) as response:
                if offset and response.status == 206:
                    expected = f'bytes {offset}-'
                    if not response.headers.get('Content-Range', '').startswith(expected):
                        raise ValueError('Download server returned an invalid resume range.')
                elif response.status == 200:
                    offset = 0
                else:
                    raise ValueError(f'Unexpected download status {response.status}.')
                with partial.open('ab' if offset else 'wb') as stream:
                    while chunk := response.read(1024 * 1024):
                        if cancel.is_set():
                            raise Cancelled('Installation cancelled. Partial downloads are kept for Resume.')
                        offset += len(chunk)
                        if offset > spec['size']:
                            raise ValueError('Download exceeds its pinned size.')
                        stream.write(chunk)
                        progress('Downloading', offset, spec['size'])
            if offset == spec['size']:
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == 2:
                raise ValueError('Download interrupted. Retry to resume the partial file.') from exc
            if cancel.wait(attempt + 1):
                raise Cancelled('Installation cancelled.')
    progress('Verifying SHA-256', offset, spec['size'])
    if not partial.exists() or partial.stat().st_size != spec['size']:
        raise ValueError('Download is incomplete. Retry to resume.')
    if sha256(partial) != spec['sha256']:
        partial.rename(partial.with_name(partial.name + f'.invalid-{time.time_ns()}'))
        raise ValueError('Checksum mismatch. The file was rejected; retry downloads it again.')
    os.replace(partial, target)
    return target


def unzip(archive, destination):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as package:
        for entry in package.infolist():
            target = (destination / entry.filename).resolve()
            if not target.is_relative_to(destination) or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Archive contains an unsafe path or symbolic link.')
        package.extractall(destination)


class Installer:
    def __init__(self, dependencies):
        self.dependencies = dependencies
        self.cancel = threading.Event()
        self.state = {'status': 'idle', 'message': 'Ready to install missing dependencies.', 'done': 0, 'total': 0}
        self.log_path = dependencies.root / 'data/install.log'
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def progress(self, message, done=0, total=0):
        self.state.update(message=message, done=done, total=total)

    def run_process(self, command, cwd=None):
        with self.log_path.open('ab') as log:
            process = subprocess.Popen([str(x) for x in command], cwd=cwd, stdout=log, stderr=log, creationflags=NO_WINDOW)
            while process.poll() is None:
                if self.cancel.wait(0.2):
                    process.terminate()
                    process.wait(timeout=20)
                    raise Cancelled('Installation cancelled.')
            if process.returncode:
                raise ValueError(f'Installation command failed with exit code {process.returncode}. See the installation log.')

    def run(self, plan):
        dep = self.dependencies
        self.state.update(status='running', message='Preparing installation', ids=plan['ids'], completed=[])
        lock_path = dep.root / 'dependencies/.install.lock'
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = None
        try:
            # Windows byte-range locks are released automatically after an interrupted process.
            import msvcrt
            lock = lock_path.open('a+b')
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError('Another dependency installer is already running.') from exc
            if shutil.disk_usage(dep.root).free < plan['required_free_bytes']:
                raise ValueError('Not enough free disk space for downloads and extraction.')
            for key in plan['ids']:
                if self.cancel.is_set():
                    raise Cancelled('Installation cancelled.')
                self.state['current'] = key
                self.progress(f'Preparing {dep.specs[key]["label"]}')
                self.install_one(key)
                dep.paths[key] = dep.specs[key]['destination']
                if key == 'comfy':
                    dep.paths['comfy_python'] = dep.specs['comfy_python']['destination']
                dep.save({}, plan['models'], dep.port)
                dep.validate()
                if not dep.rows[key]['valid']:
                    raise ValueError(f'{dep.specs[key]["label"]}: {dep.rows[key]["message"]}')
                self.state['completed'].append(key)
            self.state.update(status='completed', message='Dependencies installed and verified. Start ComfyUI to load them.', done=1, total=1)
        except Cancelled as exc:
            self.state.update(status='cancelled', message=str(exc))
        except Exception as exc:
            self.state.update(status='failed', message=str(exc))
            with self.log_path.open('a', encoding='utf-8') as log:
                log.write(f'\n{type(exc).__name__}: {exc}\n')
        finally:
            if lock:
                lock.close()
            dep.validate()
        return self.state

    def install_one(self, key):
        dep, spec = self.dependencies, self.dependencies.specs[key]
        target = dep.managed_path(key)
        if key == 'comfy_python':
            if not target.is_file():
                raise ValueError('Managed ComfyUI Python is missing. Reinstall ComfyUI.')
            return
        cache = dep.root / 'dependencies/downloads' / f'{key}-{spec["filename"]}'
        if spec['kind'] == 'model':
            download(spec, target, self.cancel, self.progress)
            return
        download(spec, cache, self.cancel, self.progress)
        if spec['kind'] == 'executable' and key != 'ffmpeg':
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cache, target)
            return
        staging = dep.root / 'dependencies/staging' / f'{key}-{time.time_ns()}'
        staging.mkdir(parents=True)
        self.progress(f'Installing {spec["label"]}')
        if key == 'comfy':
            self.run_process([dep.path('archive_tool'), 'x', cache, f'-o{staging}', '-y'])
            source = staging / 'ComfyUI_windows_portable'
            patch_core(source)
        elif key == 'hybrid_node':
            source = staging / 'node'
            source.mkdir()
            shutil.copy2(cache, source / '__init__.py')
            patch_hybrid(source / '__init__.py')
        else:
            unzip(cache, staging)
            source = next(staging.iterdir())
            if key == 'h3_helpers':
                subset = staging / 'helpers'
                subset.mkdir()
                for name in ('h3_advanced.py', 'h3_gguf_arch.py', 'h3_avbank_probe.py', 'LICENSE'):
                    shutil.copy2(source / name, subset / name)
                (subset / '__init__.py').write_text('from .h3_advanced import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS\nfrom . import h3_gguf_arch, h3_avbank_probe\n', encoding='utf-8')
                source = subset
            if key == 'gguf':
                self.run_process([dep.path('comfy_python'), '-s', '-m', 'pip', 'install', '--require-hashes', '--no-deps',
                                  '-r', dep.root / 'installers/gguf-requirements.lock'])
        install_target = target.parents[1] if key == 'ffmpeg' else target
        install_target.parent.mkdir(parents=True, exist_ok=True)
        if install_target.exists():
            install_target.rename(install_target.with_name(install_target.name + f'.previous-{time.time_ns()}'))
        source.rename(install_target)
        atomic_json(install_target / 'motionbench-provenance.json', {name: spec.get(name) for name in ('url', 'revision', 'sha256', 'license')})
        if key == 'hybrid_node':
            atomic_json(install_target / 'motionbench-patch.json', {'installed_sha256': sha256(target / '__init__.py'), 'first_frame_first': True})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--only')
    parser.add_argument('--models', nargs='+', choices=['b25', 'b20', 'fl2va'])
    args = parser.parse_args()
    dependencies = Dependencies(args.root)
    dependencies.validate(probe=True)
    if not dependencies.hardware['valid']:
        raise SystemExit(dependencies.hardware['message'])
    installer = Installer(dependencies)
    plan = dependencies.plan(args.models, args.only)
    print(json.dumps(plan, indent=2), flush=True)
    worker = threading.Thread(target=installer.run, args=(plan,))
    worker.start()
    try:
        while worker.is_alive():
            print(json.dumps(installer.state), flush=True)
            worker.join(1)
    except KeyboardInterrupt:
        installer.cancel.set()
        worker.join()
    print(json.dumps(installer.state), flush=True)
    if installer.state['status'] != 'completed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
