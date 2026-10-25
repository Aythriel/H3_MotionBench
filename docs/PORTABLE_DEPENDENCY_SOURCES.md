# Portable dependency sources

Verified 2026-09-20 for Windows x64 with NVIDIA. This records dependency provenance for the installer; it does not claim that a fresh machine has been tested.

## Pinned artifacts

Each model URL uses a fixed repository revision. GitHub release assets use versioned releases and the SHA-256 reported by the release API. Python uses the digest published in its official Sigstore bundle; pip uses PyPI metadata. Custom-node source ZIP hashes were computed from small downloads of fixed commits. No model or runtime archives were downloaded for this investigation.

| ID | Version/revision | Bytes | SHA-256 | Source and download |
| --- | --- | ---: | --- | --- |
| `comfy` | `v0.33.1` | 2,133,107,036 | `4a221588979b96b8244e0e50b2edca03af732acae1deba69d60aa3b4d60b9dba` | [Metadata/source](https://api.github.com/repos/Comfy-Org/ComfyUI/releases/tags/v0.33.1), [download](https://github.com/Comfy-Org/ComfyUI/releases/download/v0.33.1/ComfyUI_windows_portable_nvidia.7z) |
| `archive_tool` | `26.03` | 602,624 | `ad4c82fadcbdf93c03b4fc440f300509c7d60c5c2f4d183e35d9d70d6957037d` | [Metadata/source](https://api.github.com/repos/ip7z/7zip/releases/tags/26.03), [download](https://github.com/ip7z/7zip/releases/download/26.03/7zr.exe) |
| `ffmpeg` | `8.1.2` | 109,728,040 | `db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec` | [Metadata/source](https://api.github.com/repos/GyanD/codexffmpeg/releases/tags/8.1.2), [download](https://github.com/GyanD/codexffmpeg/releases/download/8.1.2/ffmpeg-8.1.2-essentials_build.zip) |
| `python` | `3.13.14` | 10,964,839 | `90b4e5b9898b72d744650524bff92377c367f44bd5fbd09e3148656c080ad907` | [Metadata/source](https://www.python.org/ftp/python/3.13.14/python-3.13.14-embed-amd64.zip.sigstore), [download](https://www.python.org/ftp/python/3.13.14/python-3.13.14-embed-amd64.zip) |
| `pip` | `26.2.1` | 1,816,632 | `71138adf1f4ca900cdb7d289c21b7494329f2332b6d85f0e1c42108c0384ed3e` | [Metadata/source](https://pypi.org/pypi/pip/26.2.1/json), [download](https://files.pythonhosted.org/packages/f3/6e/1736e5b4ae2b778ef2f81c47d797de9f891d4d8acb047a24ca37a60294dd/pip-26.2.1-py3-none-any.whl) |
| `encoder` | `7e75982b97cd5a41d2dcfa1904ee88d0686d6fd1` | 15,687,142,551 | `35a88d51044231fe332301d7a62aa81e3f2cba62febeb446e2c1e3e0ef76f2c6` | [Metadata/source](https://huggingface.co/api/models/Comfy-Org/MiniMax-H3/revision/7e75982b97cd5a41d2dcfa1904ee88d0686d6fd1?blobs=true), [download](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/7e75982b97cd5a41d2dcfa1904ee88d0686d6fd1/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors) |
| `video_vae` | `7e75982b97cd5a41d2dcfa1904ee88d0686d6fd1` | 5,207,808,496 | `7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522` | [Metadata/source](https://huggingface.co/api/models/Comfy-Org/MiniMax-H3/revision/7e75982b97cd5a41d2dcfa1904ee88d0686d6fd1?blobs=true), [download](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/7e75982b97cd5a41d2dcfa1904ee88d0686d6fd1/vae/minimax_h3_video_vae_fp16.safetensors) |
| `audio_vae` | `7e75982b97cd5a41d2dcfa1904ee88d0686d6fd1` | 605,254,808 | `8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48` | [Metadata/source](https://huggingface.co/api/models/Comfy-Org/MiniMax-H3/revision/7e75982b97cd5a41d2dcfa1904ee88d0686d6fd1?blobs=true), [download](https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/7e75982b97cd5a41d2dcfa1904ee88d0686d6fd1/vae/minimax_h3_audio_vae_fp32.safetensors) |
| `b25` | `69020fa0b7044c716b991c709e58562bcaaa4a7b` | 11,387,224,800 | `6b14de64d26a3e67316c31b11198eb01bee03cabddbd16cb6f55b65c7b70943c` | [Metadata/source](https://huggingface.co/api/models/hoidhxd/MiniMax-H3-Hybrid-b25-49-GGUF/revision/69020fa0b7044c716b991c709e58562bcaaa4a7b?blobs=true), [download](https://huggingface.co/hoidhxd/MiniMax-H3-Hybrid-b25-49-GGUF/resolve/69020fa0b7044c716b991c709e58562bcaaa4a7b/minimax_h3_hybrid_fl2va_ref2va_b25-49-Q4_0.gguf) |
| `b20` | `a36feb17fbd1f20ff4bdd509ccd07e2b7b585a38` | 20,970,379,632 | `8320246da06e5aae94988dc1ab336b19ebf2491b4393a330fb33bafca39f9d9a` | [Metadata/source](https://huggingface.co/api/models/smhfacct/Minimax-H3-fl2va-ref2va-hybrid-models/revision/a36feb17fbd1f20ff4bdd509ccd07e2b7b585a38?blobs=true), [download](https://huggingface.co/smhfacct/Minimax-H3-fl2va-ref2va-hybrid-models/resolve/a36feb17fbd1f20ff4bdd509ccd07e2b7b585a38/minimax_h3_hybrid_fl2va_ref2va_b20-49-int8.safetensors) |
| `fl2va` | `defd1c4e4e5617fae5ddff0f4668f9ac20ef5348` | 11,464,827,552 | `226c83f6b971077f61f4ccf4ffaaed5d7ccf914642b7c25359da1da2f13dcd1a` | [Metadata/source](https://huggingface.co/api/models/joeygambino/MiniMax-H3-curve-GGUF/revision/defd1c4e4e5617fae5ddff0f4668f9ac20ef5348?blobs=true), [download](https://huggingface.co/joeygambino/MiniMax-H3-curve-GGUF/resolve/defd1c4e4e5617fae5ddff0f4668f9ac20ef5348/fl2va/MiniMax-H3-fl2va-curve-Q4_0.gguf) |
| `gguf` | `6ea2651e7df66d7585f6ffee804b20e92fb38b8a` | 38,051 | `aad273a0b774684285b4496f6edff7e2293bd29d71ef89926d6861bfdf4947ac` | [Metadata/source](https://github.com/city96/ComfyUI-GGUF/tree/6ea2651e7df66d7585f6ffee804b20e92fb38b8a), [download](https://codeload.github.com/city96/ComfyUI-GGUF/zip/6ea2651e7df66d7585f6ffee804b20e92fb38b8a) |
| `h3_helpers` | `d7d197709fc93c1afb125e3db1dfd4ac0ae28c60` | 6,626,779 | `82e7358b02e71ef2dc1e03aba57754f74e611005d4fb0594545c713d4b9dadda` | [Metadata/source](https://github.com/jlucasmcrell/ComfyUI-H3-Multishot/tree/d7d197709fc93c1afb125e3db1dfd4ac0ae28c60), [download](https://codeload.github.com/jlucasmcrell/ComfyUI-H3-Multishot/zip/d7d197709fc93c1afb125e3db1dfd4ac0ae28c60) |
| `hybrid_node` | `f12a48fe9f1c8ee313a081a91327c31b5bfb9895` | 10,356 | `980074f59bfd37e0a56006d9e0d2f3dd73f6c79036adc91f34f45f4035c9d5fa` | [Metadata/source](https://github.com/BigStationW/ComfyUi-MiniMax-H3-Image-And-Reference-To-Video/tree/f12a48fe9f1c8ee313a081a91327c31b5bfb9895), [download](https://raw.githubusercontent.com/BigStationW/ComfyUi-MiniMax-H3-Image-And-Reference-To-Video/f12a48fe9f1c8ee313a081a91327c31b5bfb9895/__init__.py) |

## Installation layout and bootstrap

The pinned [ComfyUI packaging workflow](https://github.com/Comfy-Org/ComfyUI/blob/72865f4f27eaf5396f8f36370e0a2be3a9a090ee/.github/workflows/windows_release_package.yml) packages the root `ComfyUI_windows_portable`, containing `ComfyUI` and `python_embeded`. It creates a solid LZMA2 `.7z` archive with BCJ2 filtering and a 768 MB dictionary. The official standalone [7zr download](https://www.7-zip.org/download.html) can be kept inside the application dependency directory and used without installing a system archive tool.

Python 3.13.14 and pip 26.2.1 match the existing working runtime. The [Python embeddable distribution](https://docs.python.org/3.13/using/windows.html#the-embeddable-package) omits pip and isolates imports from system Python/environment paths. Treat the application wheels as pinned vendored dependencies, configure `python313._pth` explicitly, and bootstrap pip through a wheel in `sys.path` rather than assuming `PYTHONPATH` is honored. Python documents that the Microsoft C runtime is an installer prerequisite; on supported modern Windows installations, check the runtime before declaring bootstrap successful.

The [FFmpeg build provider](https://www.gyan.dev/ffmpeg/builds/) supplies both `ffmpeg.exe` and `ffprobe.exe`; the essentials build includes the codecs used here. Validate both executables after extraction. The ZIP needs no third-party extraction program.

The pinned [GGUF requirements](https://github.com/city96/ComfyUI-GGUF/blob/6ea2651e7df66d7585f6ffee804b20e92fb38b8a/requirements.txt) list `gguf>=0.13.0`, with `sentencepiece` and `protobuf` as tokenizer extras. MotionBench should resolve exact compatible package versions in its lock rather than installing this moving lower-bound requirement directly.

## Model combinations

All presets share the encoder and the two VAEs. Their combined size is 21,500,205,855 bytes. These are downloaded once. The file sizes below exclude ComfyUI, bootstrap packages, caches and extraction space.

| Selection | Model-weight download bytes | Decimal GB |
| --- | ---: | ---: |
| Balanced b25 Q4 (recommended default) | 32,887,430,655 | 32.89 |
| Stronger-reference b20 INT8 | 42,470,585,487 | 42.47 |
| Original FL2VA | 32,965,033,407 | 32.97 |
| Both hybrids | 53,857,810,287 | 53.86 |
| All three | 65,322,637,839 | 65.32 |

The recommendation follows the already-tested MotionBench default on its 16 GB NVIDIA machine; this is not a universal hardware benchmark. The b20 alternative is substantially larger. Original FL2VA does not provide independent character-image conditioning.

## Licenses and distribution boundaries

| Dependency | Recorded license |
| --- | --- |
| ComfyUI | [GPL-3.0 core license](https://github.com/Comfy-Org/ComfyUI/blob/72865f4f27eaf5396f8f36370e0a2be3a9a090ee/LICENSE); portable dependencies have their own terms |
| 7-Zip | [Upstream license](https://www.7-zip.org/license.txt), including LGPL and BSD portions |
| FFmpeg essentials build | [GPLv3, as stated by its build provider](https://www.gyan.dev/ffmpeg/builds/#about-these-builds) |
| Python / pip | [Python license](https://docs.python.org/3/license.html) / [pip MIT](https://github.com/pypa/pip/blob/26.2.1/LICENSE.txt) |
| ComfyUI-GGUF | [Apache 2.0](https://github.com/city96/ComfyUI-GGUF/blob/6ea2651e7df66d7585f6ffee804b20e92fb38b8a/LICENSE) |
| ComfyUI-H3-Multishot | [MIT](https://github.com/jlucasmcrell/ComfyUI-H3-Multishot/blob/d7d197709fc93c1afb125e3db1dfd4ac0ae28c60/LICENSE) |
| H3 models and derivatives | [MiniMax H3 Community License](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE), linked by the [Comfy-Org model card](https://huggingface.co/Comfy-Org/MiniMax-H3). The [hybrid model card](https://huggingface.co/smhfacct/Minimax-H3-fl2va-ref2va-hybrid-models) explicitly retains original model terms. |

The combined BigStationW node has no `LICENSE` file at pinned commit `f12a48fe9f1c8ee313a081a91327c31b5bfb9895`, although its [pyproject](https://github.com/BigStationW/ComfyUi-MiniMax-H3-Image-And-Reference-To-Video/blob/f12a48fe9f1c8ee313a081a91327c31b5bfb9895/pyproject.toml) points to one. No redistribution permission was established. Keep its source out of the share ZIP; the installer can retrieve the exact reviewed source from its origin and apply the local compatibility/picture-order changes. Record the source hash and resulting patched hash.

The upstream H3 agreement includes territorial and distribution/use conditions; linking a downloader is not a substitute for the recipient reviewing those terms. The encoder is based on Qwen3-VL, which the upstream H3 license separately identifies as Apache 2.0. Keep model license links visible and retain provenance without bundling weights, local paths, jobs or user media.

## Verification limits

- Release/model hashes were read from publisher APIs; only the two small source ZIPs and the raw hybrid node were downloaded and hashed in this research.
- The archive root is verified from the pinned packaging workflow, not by extracting the multi-gigabyte archive here.
- Source ZIPs from codeload are commit-pinned; if a provider changes compression while preserving source, fail checksum validation and deliberately update the manifest after review.
- Existing runtime execution checks are recorded in `HYBRID_SETUP.md`. Fresh download, extraction, bootstrapping, restart and missing-dependency behavior require installer validation.


## Installer verification follow-up

The implementation subsequently downloaded and checksum-verified the actual
ComfyUI archive and extracted its 4.44 GB portable installation, as well as 7-Zip,
FFmpeg, Python, locked dashboard packages and the custom-node sources. A clean
engine completed a first-frame plus two-reference b25 generation using existing
weights supplied through the Settings paths. The required H3 helper subset includes
h3_advanced.py, h3_gguf_arch.py and h3_avbank_probe.py; importing the last module
applies the keyframe/reference payload merge required for combined conditioning.
