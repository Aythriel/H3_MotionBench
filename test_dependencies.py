import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).parent))
from dependencies import Dependencies
from installers.install import download, unzip, Cancelled
from server import DEFAULTS
from test_server import node_catalogue


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'installers').mkdir()
        manifest = json.loads((Path(__file__).parent / 'installers/dependencies.json').read_text(encoding='utf-8'))
        for spec in manifest['dependencies'].values():
            if spec['kind'] == 'model':
                spec['size'] = 32
        (self.root / 'installers/dependencies.json').write_text(json.dumps(manifest), encoding='utf-8')
        with patch('dependencies.shutil.which', return_value=None), patch.dict('os.environ', {}, clear=True):
            self.dep = Dependencies(self.root)
        self.dep.hardware = {'valid': True, 'message': 'Synthetic NVIDIA'}

    def tearDown(self):
        self.temp.cleanup()

    def materialize(self):
        for key, spec in self.dep.specs.items():
            path = self.dep.managed_path(key)
            if spec['kind'] == 'directory':
                for name in spec.get('required_files', []):
                    file = path / name
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_text(spec.get('markers', {}).get(name, 'source'), encoding='utf-8')
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                if spec['kind'] == 'model':
                    header = b'GGUF1234' if path.suffix == '.gguf' else (2).to_bytes(8, 'little')
                    path.write_bytes(header + bytes(24))
                else:
                    path.write_bytes(b'MZ')
        self.dep.validate()

    def test_missing_and_moved_paths_do_not_prevent_settings_from_loading(self):
        report = self.dep.validate()
        self.assertFalse(report['generation_ready'])
        self.assertTrue(report['initialized'])
        self.materialize()
        self.assertTrue(self.dep.snapshot()['generation_ready'])
        self.dep.path('b25').unlink()
        self.dep.validate()
        self.assertFalse(self.dep.snapshot()['presets']['hybrid_balanced']['ready'])
        self.assertTrue(self.dep.snapshot()['presets']['hybrid_reference']['ready'])
        self.assertTrue(self.dep.generation_errors(DEFAULTS, node_catalogue()))

    def test_settings_roundtrip_retains_invalid_path_for_correction(self):
        bad = str(self.root / 'missing/model.gguf')
        self.dep.save({'b25': bad}, ['b25', 'b20'], 8199)
        loaded = Dependencies(self.root)
        self.assertEqual(loaded.paths['b25'], bad)
        self.assertEqual(loaded.port, 8199)
        self.assertEqual(loaded.selected_models, ['b25', 'b20'])
        self.assertFalse(loaded.validate()['generation_ready'])

    def test_corrupt_configuration_keeps_setup_accessible(self):
        self.dep.config_path.write_text('{broken', encoding='utf-8')
        loaded = Dependencies(self.root)
        self.assertIn('Could not read', loaded.config_error)
        self.assertFalse(loaded.validate()['generation_ready'])

    def test_malformed_saved_paths_keep_setup_accessible(self):
        for bad in ['bad\x00path', 'bad\npath', '//unreachable/server']:
            with self.subTest(path=bad):
                self.dep.config_path.write_text(json.dumps({'paths': {'comfy': bad}}), encoding='utf-8')
                loaded = Dependencies(self.root)
                self.assertTrue(loaded.path('comfy').is_absolute())
                loaded.validate()
                self.assertFalse(loaded.rows['comfy']['valid'])
                self.assertEqual(loaded.paths['comfy'], bad)

    def test_plan_selects_only_requested_models_and_skips_available_files(self):
        plan = self.dep.plan(['b25'])
        self.assertIn('b25', plan['ids'])
        self.assertNotIn('b20', plan['ids'])
        self.assertNotIn('fl2va', plan['ids'])
        self.assertLess(plan['ids'].index('hybrid_node'), plan['ids'].index('b25'))
        self.materialize()
        self.assertEqual(self.dep.plan(['b25'])['ids'], [])
        self.dep.path('comfy_python').unlink()
        plan = self.dep.plan(['b25'])
        self.assertIn('comfy', plan['ids'])
        self.assertIn('comfy_python', plan['ids'])

    def test_settings_reject_unknown_paths_and_invalid_models_without_writing(self):
        for paths, models, port in [({'unknown':'x'},['b25'],8188), ({'b25':'bad\npath'},['b25'],8188), ({},[],8188), ({},['x'],8188), ({},['b25'],80)]:
            with self.subTest(paths=paths, models=models), self.assertRaises(ValueError):
                self.dep.save(paths, models, port)
        self.assertFalse(self.dep.config_path.exists())

    def test_paths_file_registers_shared_models_without_copying(self):
        self.materialize()
        path = self.dep.extra_paths()
        config = json.loads(path.read_text(encoding='utf-8'))['motionbench']
        self.assertTrue(config['is_default'])
        self.assertIn(str(self.dep.path('b25').parent), config['diffusion_models'])
        self.assertEqual(config['vae'], str(self.dep.path('video_vae').parent))


class DownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = b'verified sample download' * 1000
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                offset = int(self.headers.get('Range', 'bytes=0-')[6:-1]) if self.path != '/ignore-range' else 0
                self.send_response(206 if offset else 200)
                if offset:
                    self.send_header('Content-Range', f'bytes {offset}-{len(cls.content)-1}/{len(cls.content)}')
                self.send_header('Content-Length', str(len(cls.content)-offset))
                self.end_headers()
                self.wfile.write(cls.content[offset:])
            def log_message(self, *args):
                pass
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def spec(self, route='/resume'):
        return {'url': f'http://127.0.0.1:{self.server.server_port}{route}', 'size':len(self.content), 'sha256':hashlib.sha256(self.content).hexdigest()}

    def test_resume_and_server_ignoring_range_both_produce_verified_file(self):
        with tempfile.TemporaryDirectory() as folder:
            for route in ['/resume','/ignore-range']:
                target=Path(folder)/route[1:]
                target.with_suffix('.partial').write_bytes(self.content[:31])
                download(self.spec(route), target, threading.Event(), lambda *args:None)
                self.assertEqual(target.read_bytes(), self.content)

    def test_bad_hash_and_cancellation_never_publish_a_file(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'artifact'
            spec={**self.spec(), 'sha256':'0'*64}
            with self.assertRaisesRegex(ValueError,'Checksum mismatch'):
                download(spec,target,threading.Event(),lambda *args:None)
            self.assertFalse(target.exists())
            cancelled=threading.Event();cancelled.set()
            with self.assertRaises(Cancelled):
                download(self.spec(),target,cancelled,lambda *args:None)
            self.assertFalse(target.exists())

    def test_zip_traversal_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as folder:
            archive=Path(folder)/'bad.zip'
            with zipfile.ZipFile(archive,'w') as package:
                package.writestr('../outside.txt','untrusted')
            with self.assertRaisesRegex(ValueError,'unsafe'):
                unzip(archive,Path(folder)/'extract')
            self.assertFalse((Path(folder)/'outside.txt').exists())


if __name__ == '__main__':
    unittest.main()
