# H3 hybrid conditioning

Installed and verified on 2026-09-20.

## Use

The default Model preset is **Hybrid balanced / b25 Q4** and Conditioning is
**First frame + character references**. Use the last accepted video frame as
First frame, then add the original character artwork to Character references.
Leave the endpoint absent unless the action needs a prescribed final pose.
Use last frame as first retains the selected character references for the next
segment and clears the previous endpoint.

Model presets select architecture, checkpoint, conditioning and base sampling.
The separate Architecture control permits custom combinations of installed
checkpoints within that family. Conditioning exposes supported input roles:

| Architecture | Frame anchors | Independent character images |
| --- | --- | --- |
| Hybrid FL2VA/REF2VA | First, last, both, or none | Optional; can coexist with anchors |
| Original FL2VA | First, last, both, or none | Unsupported |
| REF2VA | None in this workbench | Supported |

REF2VA routing is implemented for a separately installed compatible checkpoint;
this installation adds two hybrids, not a pure REF2VA model.

With a first frame, it is always `<Picture 1>` and character images start at
`<Picture 2>`. Without a first frame, character images start at `<Picture 1>`.
An optional last frame follows all character references. Card labels show the
actual numbers. Version 2 saved prompts are renumbered on import/reuse, including
sound and music, while historical records remain unchanged. Character images
retain their source aspect ratio instead of undergoing the frame canvas crop/stretch. The
prompt maps them to Subject 1; use different views/details of the same character.
Generated audio and the existing soundscape/music controls remain available.
Video/audio reference uploads are not part of this image-conditioning interface.

The b20 preset prioritizes the stronger reference variant and uses a larger INT8
checkpoint. It can require more offloading than the b25 Q4 preset. Both start at
20 steps with no LoRA. The installed FL2VA turbo shortcut is confined to FL2VA.

Old takes and imported settings infer FL2VA from the recorded model and retain
their frame modes. On the first load after this upgrade, an old browser draft is
backed up under `h3-workbench-settings-pre-hybrid`; the active draft adopts the
hybrid default while keeping its prompt, frame selections, canvas and seed.
No identity artwork is guessed: the user selects the approved original.

## Installed model files

Under `ComfyUI/models/diffusion_models/`:

- `minimax_h3_hybrid_fl2va_ref2va_b25-49-Q4_0.gguf`, 11,387,224,800 bytes.
  [Source](https://huggingface.co/hoidhxd/MiniMax-H3-Hybrid-b25-49-GGUF), revision
  `69020fa0b7044c716b991c709e58562bcaaa4a7b`.
  SHA-256 `6b14de64d26a3e67316c31b11198eb01bee03cabddbd16cb6f55b65c7b70943c`.
- `minimax_h3_hybrid_fl2va_ref2va_b20-49-int8.safetensors`, 20,970,379,632 bytes.
  [Source](https://huggingface.co/smhfacct/Minimax-H3-fl2va-ref2va-hybrid-models),
  revision `a36feb17fbd1f20ff4bdd509ccd07e2b7b585a38`.
  SHA-256 `8320246da06e5aae94988dc1ab336b19ebf2491b4393a330fb33bafca39f9d9a`.

Both were downloaded to partial files and verified against the published LFS
size and SHA-256 before being renamed into place. Public-source provenance is
stored beside them in `h3-hybrid-provenance.json`. The existing H3 encoder,
video/audio VAEs and original FL2VA checkpoint were reused.

## ComfyUI compatibility changes

The combined node comes from
[BigStationW](https://github.com/BigStationW/ComfyUi-MiniMax-H3-Image-And-Reference-To-Video),
pinned revision `f12a48fe9f1c8ee313a081a91327c31b5bfb9895`.
Its entire source was reviewed before installation; no dependency install or
unreviewed repository code was executed. Original source SHA-256:
`980074f59bfd37e0a56006d9e0d2f3dd73f6c79036adc91f34f45f4035c9d5fa`.

The installed node has a compatibility fallback for `_encode_ref_audio`: newer
cores export the helper at module level; 0.33.1 exposes it as a static method on
`MiniMaxH3ReferenceToVideo`. Installed source SHA-256:
`8ec3ce75c72166f3d7e8c66ca06a435da83f84181c6da637c5f9a91564f5d629` before the
picture-order update. The node now also accepts `first_frame_first`, enabled by
MotionBench. It changes only the encoder image order, keeping separate keyframe
and identity latent roles. Omitted/false preserves older ComfyUI workflows.
Current source SHA-256 is recorded in the node directory provenance.json.
MotionBench rejects a node lacking this option rather than mismapping images.

A real b25 generation exposed a second compatibility issue in
`ComfyUI/comfy/ldm/minimax/model.py`: its timestep interpolation combined the
checkpoint's FP16 `adaln_t_table` with FP32 interpolation weights. A CPU probe
using that exact 1025-by-8 table reproduced the error. The table's `cast_to`
call now explicitly requests `dtype=torch.float32`. The original FL2VA table
already uses FP32. Both hybrid generation checks pass with this fix.

Core hashes before/after:

- Before: `7d244c9a1b37e2bc7cdb86d3f2f5ce83bda60e22036186a8d936392167f7f5b8`.
- After: `66d51b3e9d77dec306938140f0cf6331f1bcacd897646bb768eddd798dff232c`.

ComfyUI or node updates can overwrite these local compatibility changes. Check
whether the new version already handles both cases before reapplying them.
Backup sources, hashes and test evidence are kept in
`.artifacts/hybrid-install-20260920/` in MotionBench.

## Validation and limitations

38 backend tests cover architecture validation, ordered references, hidden-input
omission, distinct loaders, serialization, old settings, and existing media and
assembly behavior. `H3_HYBRID_UI_PASSED` covers default selection, visible inputs,
presets, original-reference retention through continuation/reload, exports,
legacy imports and one-time browser migration, and desktop/mobile layout.
The retained FL2VA UI suite ends `H3_DASHBOARD_UI_PASSED`.

Both presets completed real local 256-by-256, 22-frame, two-step generations
with a first frame and two independent synthetic character images. The graph,
reference roles and generated MP4s are retained as integration evidence. These
tiny samples establish execution and input routing; they do not establish
production animation quality or long-chain identity stability.

For a repeatable isolated review, run `prepare_hybrid_review.py`, then start the
server with `--data .artifacts/review-state --port 8792`. Browser checks use
`check_hybrid_ui.cjs` (requires Playwright and Chrome). `smoke_hybrid.py` and
`smoke_hybrid.py --preset hybrid_reference` submit actual local test generations
and refuse non-synthetic input shelves. The normal suite is
`python -m unittest discover -p "test_*.py"`.
