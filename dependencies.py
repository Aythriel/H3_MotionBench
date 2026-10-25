"""Local dependency settings, validation and managed download plans."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from storage import atomic_json

MODEL_IDS = ('b25', 'b20', 'fl2va')
COMMON = ('comfy', 'comfy_python', 'h3_helpers', 'encoder', 'video_vae', 'audio_vae')
PRESET_DEPENDENCIES = {
    'hybrid_balanced': (*COMMON, 'gguf', 'hybrid_node', 'b25'),
    'hybrid_reference': (*COMMON, 'hybrid_node', 'b20'),
    'fl2va': (*COMMON, 'gguf', 'fl2va'),
}
NODE_NAMES = {'gguf': ('UnetLoaderGGUF',), 'hybrid_node': ('MiniMaxH3ImageAndReferenceToVideo',),
              'h3_helpers': ('H3FreeTextEncoder', 'H3ConditionStrength')}
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_config(path):
    if not path.exists():
        return {}, ''
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(value, dict):
            raise ValueError('Settings must be an object.')
        return value, ''
    except (OSError, ValueError) as exc:
        return {}, f'Could not read {path.name}: {exc}. Save corrected settings to repair it.'


class Dependencies:
    def __init__(self, root, config_path=None, comfy_home=None, comfy_port=None):
        self.root = Path(root).resolve()
        self.config_path = Path(config_path) if config_path else self.root / 'config.local.json'
        self.manifest = json.loads((self.root / 'installers/dependencies.json').read_text(encoding='utf-8'))
        self.specs = self.manifest['dependencies']
        config, self.config_error = read_config(self.config_path)
        paths = config.get('paths', {})
        if not isinstance(paths, dict):
            paths = {}
            self.config_error = 'Dependency paths must be an object. Save corrected settings.'
        legacy_home = comfy_home or os.environ.get('H3_COMFY_HOME') or config.get('comfy_home')
        self.paths = {key: str(paths.get(key, spec['destination'])) for key, spec in self.specs.items()}
        if legacy_home and 'comfy' not in paths:
            try:
                self.paths.update(self.discover(self.resolve(str(legacy_home))))
            except (OSError, ValueError, TypeError):
                self.paths['comfy'] = str(legacy_home)
        if comfy_home:
            self.paths['comfy'] = str(comfy_home)
        ffmpeg = os.environ.get('H3_FFMPEG') or shutil.which('ffmpeg')
        if ffmpeg and 'ffmpeg' not in paths:
            self.paths['ffmpeg'] = ffmpeg
        self.port = comfy_port or config.get('comfy_port', 8188)
        if not isinstance(self.port, int) or not 1024 <= self.port <= 65535:
            self.port = 8188
            self.config_error = 'Invalid engine port; using 8188 until settings are corrected.'
        self.selected_models = config.get('selected_models', ['b25'])
        if not isinstance(self.selected_models, list) or not self.selected_models or any(x not in MODEL_IDS for x in self.selected_models):
            self.selected_models = ['b25']
        self.hardware = {'valid': False, 'message': 'Checking Windows and NVIDIA support.'}
        self.rows = {}
        self.restart_required = False

    def resolve(self, value):
        if any(c in value for c in ('\n', '\r', '\x00')) or value.startswith(('\\\\', '//')):
            raise ValueError('Use a local path without line breaks or null characters.')
        path = Path(value).expanduser()
        return (path if path.is_absolute() else self.root / path).resolve()

    def path(self, key):
        try:
            return self.resolve(self.paths[key])
        except (OSError, ValueError):
            return self.root / 'dependencies' / ('.invalid-' + key)

    def managed_path(self, key):
        path = self.resolve(self.specs[key]['destination'])
        if not path.is_relative_to(self.root / 'dependencies') and not path.is_relative_to(self.root / '.runtime'):
            raise ValueError('Managed dependencies must stay inside the tool directory.')
        return path

    def discover(self, home):
        result = {'comfy': str(home), 'comfy_python': str(home / 'python_embeded/python.exe')}
        for key, spec in self.specs.items():
            if 'existing' in spec:
                candidates = [home / candidate for candidate in spec['existing']]
                found = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
                result[key] = str(found)
        return result

    def save(self, paths, models, port):
        if not isinstance(paths, dict) or set(paths) - self.specs.keys():
            raise ValueError('Unknown dependency path.')
        if not isinstance(models, list) or not models or any(x not in MODEL_IDS for x in models):
            raise ValueError('Select at least one supported model.')
        if not isinstance(port, int) or not 1024 <= port <= 65535:
            raise ValueError('Engine port must be between 1024 and 65535.')
        for value in paths.values():
            if not isinstance(value, str) or len(value) > 4096 or any(c in value for c in ('\n', '\r', '\x00')) or value.startswith(('\\\\', '//')):
                raise ValueError('Use a local path without line breaks.')
        self.paths.update(paths)
        self.selected_models = list(dict.fromkeys(models))
        self.port = port
        atomic_json(self.config_path, {'settings_version': 1, 'paths': self.paths,
                                      'selected_models': self.selected_models, 'comfy_port': self.port})
        self.config_error = ''

    def validate(self, probe=False):
        if probe:
            self.hardware = self.probe_hardware()
        rows = {}
        for key, spec in self.specs.items():
            path = self.path(key)
            valid, reason = self.check_path(key, path)
            rows[key] = {'id': key, 'label': spec['label'], 'path': self.paths[key], 'resolved_path': str(path),
                         'valid': valid, 'message': reason, 'model': key in MODEL_IDS,
                         'bytes': spec.get('size', 0), 'source': spec.get('source', spec.get('url', '')),
                         'managed': path.is_relative_to(self.root / 'dependencies')}
        self.rows = rows
        return self.snapshot()

    def check_path(self, key, path):
        if not self.paths[key].strip():
            return False, 'Choose a path or install this dependency.'
        spec = self.specs[key]
        try:
            self.resolve(self.paths[key])
            if spec['kind'] == 'model':
                if not path.is_file():
                    return False, 'File not found on this PC.'
                if path.name != spec['filename']:
                    return False, f'Expected {spec["filename"]}.'
                if path.stat().st_size != spec['size']:
                    return False, 'File size differs from the supported model; the download may be incomplete.'
                with path.open('rb') as stream:
                    header = stream.read(8)
                if path.suffix == '.gguf' and header[:4] != b'GGUF':
                    return False, 'This is not a GGUF model.'
                if path.suffix == '.safetensors' and not 2 <= int.from_bytes(header, 'little') <= min(path.stat().st_size - 8, 100_000_000):
                    return False, 'Invalid safetensors header.'
                return True, 'Available · expected filename, size and format.'
            if spec['kind'] == 'directory':
                for file in spec.get('required_files', []):
                    if not (path / file).is_file():
                        return False, f'Missing {file}.'
                for file, marker in spec.get('markers', {}).items():
                    if marker not in (path / file).read_text(encoding='utf-8'):
                        return False, 'The installed version needs the MotionBench compatibility update.'
                return True, 'Available · required files and compatibility checks passed.'
            if not path.is_file():
                return False, 'Executable not found on this PC.'
            if path.suffix.lower() != '.exe':
                return False, 'Select a Windows executable.'
            return True, 'Available.'
        except (OSError, ValueError, UnicodeError) as exc:
            return False, f'Cannot read this dependency: {exc}'

    def probe_hardware(self):
        if sys.platform != 'win32':
            return {'valid': False, 'message': 'Automatic setup supports Windows with NVIDIA only.'}
        try:
            result = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total,driver_version', '--format=csv,noheader'],
                                    capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW)
            if result.returncode or not result.stdout.strip():
                return {'valid': False, 'message': 'NVIDIA GPU/driver unavailable. Install a working NVIDIA driver first.'}
            return {'valid': True, 'message': result.stdout.strip()}
        except (OSError, subprocess.TimeoutExpired):
            return {'valid': False, 'message': 'NVIDIA driver tools were not found. Install the NVIDIA driver first.'}

    def snapshot(self):
        presets = {}
        for key, required in PRESET_DEPENDENCIES.items():
            missing = [item for item in required if not self.rows.get(item, {}).get('valid')]
            presets[key] = {'ready': not missing and self.hardware['valid'] and not self.restart_required and not self.config_error,
                            'missing': missing}
        return {'paths': self.paths, 'dependencies': list(self.rows.values()), 'presets': presets,
                'selected_models': self.selected_models, 'comfy_port': self.port, 'hardware': self.hardware,
                'runtime': str(Path(sys.executable)), 'config_error': self.config_error,
                'restart_required': self.restart_required, 'managed_directory': str(self.root / 'dependencies'),
                'initialized': bool(self.rows), 'generation_ready': any(p['ready'] for p in presets.values())}

    def generation_errors(self, config, info):
        # Refresh paths at submission too, so a deleted file cannot pass a stale startup check.
        self.validate()
        model = next((key for key in MODEL_IDS if self.specs[key]['filename'] == config['model']), None)
        required = list(COMMON)
        if config['architecture'] == 'hybrid':
            required.append('hybrid_node')
        if config['model'].lower().endswith('.gguf'):
            required.append('gguf')
        if model:
            required.append(model)
        problems = [f'{self.specs[key]["label"]}: {self.rows[key]["message"]}' for key in required if not self.rows[key]['valid']]
        if not self.hardware['valid']:
            problems.append(self.hardware['message'])
        if self.config_error:
            problems.append(self.config_error)
        if self.restart_required:
            problems.append('Restart ComfyUI from Settings to apply the changed paths.')
        for key in required:
            for name in NODE_NAMES.get(key, ()):
                if name not in info:
                    problems.append(f'ComfyUI has not loaded {name}. Restart it after installation.')
        for field, key in [('encoder', 'encoder'), ('video_vae', 'video_vae'), ('audio_vae', 'audio_vae')]:
            if config[field] != self.path(key).name:
                problems.append(f'The selected {field} differs from its Settings path.')
        return problems

    def extra_paths(self):
        mapping = {}
        for category, ids in [('diffusion_models', MODEL_IDS), ('text_encoders', ('encoder',)),
                              ('vae', ('video_vae', 'audio_vae')), ('custom_nodes', ('gguf', 'hybrid_node', 'h3_helpers'))]:
            mapping[category] = '\n'.join(dict.fromkeys(str(self.path(key).parent) for key in ids if self.rows.get(key, {}).get('valid')))
        target = self.root / 'data/comfy-paths.yaml'
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(target, {'motionbench': {'is_default': True, **mapping}})
        return target

    def plan(self, models=None, only=None):
        self.validate()
        models = self.selected_models if models is None else models
        if not isinstance(models, list) or not models or any(x not in MODEL_IDS for x in models):
            raise ValueError('Select at least one model.')
        if only is not None and only not in self.specs:
            raise ValueError('Unknown dependency.')
        wanted = [only] if only else [*COMMON, 'ffmpeg', *models]
        if not only:
            if any(key in models for key in ('b25', 'fl2va')):
                wanted.append('gguf')
            if any(key in models for key in ('b25', 'b20')):
                wanted.append('hybrid_node')
        ordered = []
        force_comfy = not self.rows['comfy_python']['valid'] and 'comfy_python' in wanted
        if only == 'comfy_python':
            force_comfy = True
        def add(key):
            if key in ordered or (self.rows[key]['valid'] and not (key == 'comfy' and force_comfy)):
                return
            for dependency in self.specs[key].get('requires', []):
                add(dependency)
            ordered.append(key)
        for key in wanted:
            add(key)
        ordered.sort(key=lambda key: self.specs[key]['kind'] == 'model')
        needed = sum(self.specs[key].get('size', 0) for key in ordered)
        required_space = needed + sum(self.specs[key].get('expanded_bytes', 0) for key in ordered) + (512 * 1024**2 if ordered else 0)
        return {'ids': ordered, 'models': models, 'download_bytes': needed, 'required_free_bytes': required_space,
                'free_bytes': shutil.disk_usage(self.root).free,
                'items': [{'id': key, 'label': self.specs[key]['label'], 'bytes': self.specs[key].get('size', 0)} for key in ordered]}
