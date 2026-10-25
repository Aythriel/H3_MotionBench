"""Small compatibility changes applied only to managed, pinned downloads."""
from pathlib import Path


def patch_core(home):
    path = Path(home) / 'ComfyUI/comfy/ldm/minimax/model.py'
    source = path.read_text(encoding='utf-8')
    old = 'comfy.model_management.cast_to(self.adaln_t_table, device=device)'
    new = 'comfy.model_management.cast_to(self.adaln_t_table, dtype=torch.float32, device=device)'
    if new not in source:
        if source.count(old) != 1:
            raise ValueError('Pinned ComfyUI timestep code differs; compatibility patch was not applied.')
        path.write_text(source.replace(old, new), encoding='utf-8')


def patch_hybrid(path):
    source = Path(path).read_text(encoding='utf-8')
    source = source.replace('    _encode_ref_audio,\n', '    MiniMaxH3ReferenceToVideo,\n')
    marker = 'class MiniMaxH3ImageAndReferenceToVideo'
    if 'except ImportError:' not in source:
        source = source.replace(marker, 'try:\n    from comfy_extras.nodes_minimax_h3 import _encode_ref_audio\nexcept ImportError:\n    _encode_ref_audio = MiniMaxH3ReferenceToVideo._encode_ref_audio\n\n' + marker, 1)
    changes = [
        ('                io.Image.Input("last_frame", optional=True),',
         '                io.Image.Input("last_frame", optional=True),\n                io.Boolean.Input("first_frame_first", default=False, optional=True,\n                                 tooltip="Number the first frame as Picture 1, then references, then the last frame."),'),
        ('        ref_images=None, ref_videos=None, ref_video_audios=None, ref_audios=None,',
         '        ref_images=None, ref_videos=None, ref_video_audios=None, ref_audios=None,\n        first_frame_first=False,'),
        ('        # Keep reference numbering stable, then include keyframes in the vision context.\n        encoder_items = ref_items + [{"type": "image", "data": image} for image in images]',
         '        frame_items = [{"type": "image", "data": image} for image in images]\n        if first_frame_first and first_frame is not None:\n            encoder_items = frame_items[:1] + ref_items + frame_items[1:]\n        else:\n            encoder_items = ref_items + frame_items'),
    ]
    for old, new in changes:
        if source.count(old) != 1:
            raise ValueError('Pinned hybrid node differs; compatibility patch was not applied.')
        source = source.replace(old, new)
    compile(source, str(path), 'exec')
    Path(path).write_text(source, encoding='utf-8')
