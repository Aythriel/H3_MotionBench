# Third-party dependency notices

Software and models are retrieved from their publishers during installation.
The share archive contains installer code and source references, not these artifacts.
Retain upstream license files supplied with installed components.

| Component | License stated by source | Source |
| --- | --- | --- |
| comfy | GPL-3.0 (core; bundled packages retain their licenses) | [Pinned source](https://api.github.com/repos/Comfy-Org/ComfyUI/releases/tags/v0.33.1) |
| archive_tool | 7-Zip license: LGPL-2.1-or-later and BSD-3-Clause; see upstream license | [Pinned source](https://api.github.com/repos/ip7z/7zip/releases/tags/26.03) |
| ffmpeg | GPL-3.0 | [Pinned source](https://api.github.com/repos/GyanD/codexffmpeg/releases/tags/8.1.2) |
| python | PSF-2.0 | [Pinned source](https://www.python.org/ftp/python/3.13.14/python-3.13.14-embed-amd64.zip.sigstore) |
| pip | MIT | [Pinned source](https://pypi.org/pypi/pip/26.2.1/json) |
| encoder | MiniMax H3 Community License (upstream terms apply to derivative checkpoints) | [Pinned source](https://huggingface.co/api/models/Comfy-Org/MiniMax-H3/revision/7e75982b97cd5a41d2dcfa1904ee88d0686d6fd1?blobs=true) |
| video_vae | MiniMax H3 Community License (upstream terms apply to derivative checkpoints) | [Pinned source](https://huggingface.co/api/models/Comfy-Org/MiniMax-H3/revision/7e75982b97cd5a41d2dcfa1904ee88d0686d6fd1?blobs=true) |
| audio_vae | MiniMax H3 Community License (upstream terms apply to derivative checkpoints) | [Pinned source](https://huggingface.co/api/models/Comfy-Org/MiniMax-H3/revision/7e75982b97cd5a41d2dcfa1904ee88d0686d6fd1?blobs=true) |
| b25 | MiniMax H3 Community License (upstream terms apply to derivative checkpoints) | [Pinned source](https://huggingface.co/api/models/hoidhxd/MiniMax-H3-Hybrid-b25-49-GGUF/revision/69020fa0b7044c716b991c709e58562bcaaa4a7b?blobs=true) |
| b20 | MiniMax H3 Community License (upstream terms apply to derivative checkpoints) | [Pinned source](https://huggingface.co/api/models/smhfacct/Minimax-H3-fl2va-ref2va-hybrid-models/revision/a36feb17fbd1f20ff4bdd509ccd07e2b7b585a38?blobs=true) |
| fl2va | MiniMax H3 Community License (upstream terms apply to derivative checkpoints) | [Pinned source](https://huggingface.co/api/models/joeygambino/MiniMax-H3-curve-GGUF/revision/defd1c4e4e5617fae5ddff0f4668f9ac20ef5348?blobs=true) |
| gguf | Apache-2.0 | [Pinned source](https://github.com/city96/ComfyUI-GGUF/tree/6ea2651e7df66d7585f6ffee804b20e92fb38b8a) |
| h3_helpers | MIT | [Pinned source](https://github.com/jlucasmcrell/ComfyUI-H3-Multishot/tree/d7d197709fc93c1afb125e3db1dfd4ac0ae28c60) |
| hybrid_node | Unspecified: pyproject points to missing LICENSE; download from upstream, do not bundle source | [Pinned source](https://github.com/BigStationW/ComfyUi-MiniMax-H3-Image-And-Reference-To-Video/tree/f12a48fe9f1c8ee313a081a91327c31b5bfb9895) |

The hybrid node references a missing LICENSE file at the pinned revision.
Its source is fetched directly from upstream on the recipient PC and is not bundled
in the share package. No redistribution license is inferred.
The managed H3 helper package keeps only the needed conditioning/GGUF modules and MIT notice.
Compatibility changes are in installers/patches.py.
