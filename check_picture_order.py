"""Check the installed combined node with distinct synthetic image tensors."""
from pathlib import Path
import importlib.util
import os
import sys
from unittest.mock import patch

core = Path(sys.argv[1] if len(sys.argv) > 1 else os.environ['H3_COMFY_HOME']) / 'ComfyUI'
sys.path.insert(0, str(core))
sys.argv = [sys.argv[0], '--cpu']
import torch

spec = importlib.util.spec_from_file_location('h3_combined', core / 'custom_nodes/ComfyUi-MiniMax-H3-Image-And-Reference-To-Video/__init__.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
node = module.MiniMaxH3ImageAndReferenceToVideo
assert any(item.id == 'first_frame_first' for item in node.define_schema().inputs)

class Clip:
    def tokenize(self, prompt, minimax_ref_items):
        self.order = [round(item['data'].mean().item() * 255) for item in minimax_ref_items]
        return prompt

    def encode_from_tokens_scheduled(self, tokens):
        return [[torch.zeros(1), {}]]

class Vae:
    def encode(self, image):
        return round(image.mean().item() * 255)

def picture(value):
    return torch.full((1, 64, 64, 3), float(value) / 255)

cases = 0
for count in [0, 1, 2, 9]:
    refs = {f'ref_image_{i}': picture(i * 10) for i in range(1, count + 1)}
    for first, last in [(True, False), (True, True), (False, True), (False, False)]:
        for enabled in [False, True]:
            clip = Clip()
            kwargs = {'first_frame_first': True} if enabled else {}
            with patch.object(module.node_helpers, 'conditioning_set_values', wraps=module.node_helpers.conditioning_set_values) as capture:
                node.execute(clip, Vae(), None, 'Prompt', 64, 64, 22,
                             first_frame=picture(100) if first else None,
                             last_frame=picture(200) if last else None,
                             ref_images=refs, **kwargs)
            reference_order = [i * 10 for i in range(1, count + 1)]
            expected = ([100] if first and enabled else []) + reference_order + ([100] if first and not enabled else []) + ([200] if last else [])
            assert clip.order == expected, (count, first, last, enabled, clip.order, expected)
            if first or last or count:
                payload = capture.call_args.args[1]
                assert [item['latent'] for item in payload.get('minimax_refs', [])] == reference_order
                assert [(item['resolved_frame_index'], item['latent']) for item in payload.get('minimax_keyframes', [])] == ([(0, 100)] if first else []) + ([(21, 200)] if last else [])
            cases += 1
print('H3_PICTURE_ORDER_NODE_PASSED', cases, 'cases: encoder order, latent roles, old workflow default')
