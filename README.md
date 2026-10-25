# H3 MotionBench

A local Windows + NVIDIA video workbench for ComfyUI and MiniMax H3. Continue a
starting frame while keeping separate character references, review takes, and
assemble accepted clips into a sequence.

Everything runs on your own PC. The dashboard is a small Python web app served on
`127.0.0.1`; generation runs in a local ComfyUI engine. Nothing is exposed beyond
loopback.

## Setting up on another PC

### 1. Check the machine

| Requirement | Details |
| --- | --- |
| Operating system | Windows 10 or 11, 64-bit. Automatic setup refuses other platforms. |
| GPU | NVIDIA GPU with a working driver. Setup runs `nvidia-smi` and stops if it fails. The default preset was developed and tested on a 16 GB NVIDIA card; that is a known-good reference, not a guaranteed minimum. |
| Driver | Recent enough for CUDA 13.0: the bundled engine is the ComfyUI 0.33.1 portable build for NVIDIA with its CUDA 13.0 runtime. `nvidia-smi` should report `CUDA Version: 13.0` or higher. Update from [nvidia.com/drivers](https://www.nvidia.com/drivers) if not. |
| RAM | Generous system RAM. The encoder alone is a 15.7 GB file and ComfyUI offloads between RAM and VRAM. Model file size is not an estimate of peak memory use. |
| Disk | About **50 GB free** for the default preset (32.9 GB of model weights + ComfyUI archive and extraction + FFmpeg). About **85 GB** to install all three presets. Use a local NTFS drive, not a network share. |
| Internet | Needed for the first-run downloads only. Generation is fully local afterwards. |
| Software | Nothing else. Windows PowerShell 5.1 (built into Windows) runs the launcher. No system Python, no administrator rights, no global packages. Git is only needed if you clone instead of downloading the ZIP. |

### 2. Get the code

Put the project in a short, writable local path such as `D:\H3-MotionBench`.
Avoid `Program Files`, OneDrive-synced folders and very deep paths (the ComfyUI
archive extracts long file paths).

Any of these work:

- **Clone the repository** (the repository is private, so sign in first with
  `gh auth login` or Git Credential Manager):

  ```bash
  gh repo clone Aythriel/H3_MotionBench D:\H3-MotionBench
  ```

- **Download a ZIP** from GitHub (*Code* > *Download ZIP*) and extract the whole
  folder.
- **Share package**: on a PC that already has the project, run
  `Build-SharePackage.ps1`. It writes `dist/H3-MotionBench.zip` plus a `.sha256`
  file containing only code and installer files (no settings, history, media or
  models). Copy that ZIP to the new PC and extract it.

### 3. First launch

Double-click **`Start-MotionBench.cmd`**.

On the first run the launcher:

1. Downloads a pinned, checksum-verified embeddable Python 3.13.14 and pip
   26.2.1 into `.runtime/`, then installs the dashboard's locked packages
   (`aiohttp`, `Pillow`, `av`) there. This takes a minute or two.
2. Starts the dashboard on `http://127.0.0.1:8788`.
3. Opens your browser. Because no generation dependencies exist yet, it opens
   **Settings**.

If it fails, the console window stays open with the error. The dashboard log is
`data/dashboard.stderr.log`.

### 4. Install the engine and models (Settings page)

Choose one route.

**A. Fresh managed install (recommended for a new PC)**

1. Tick the model presets you want. **Hybrid balanced / b25 Q4** is selected by
   default and is the recommended starting point.
2. Click **Download missing dependencies**. Settings shows the download size
   and the free space needed for extraction before it starts.
3. Wait. Most of the time goes to the models (tens of GB). Interrupted downloads
   keep a `.partial` file and resume when you retry. Every file is SHA-256
   verified before it is put in place. Progress and errors are in the
   installation log on the Settings page (`data/install.log`).

Everything goes under `dependencies/` inside the project folder:

```
dependencies/
  ComfyUI_windows_portable/   ComfyUI 0.33.1 + its own Python and CUDA runtime
  custom_nodes/               GGUF loader, H3 helpers, hybrid node
  models/                     diffusion_models/, text_encoders/, vae/
  ffmpeg/                     ffmpeg.exe + ffprobe.exe
  tools/7zr.exe               extracts the ComfyUI archive
```

**B. Reuse an existing ComfyUI portable installation**

1. Enter its folder and click **Find its dependencies**. MotionBench looks for
   matching model files and custom nodes inside it.
2. Click **Save paths**. Settings stores them in `config.local.json`.
3. Anything missing or incompatible is marked red. Click **Download missing
   dependencies** to fill only those gaps. MotionBench installs compatible
   copies under `dependencies/` and never overwrites files in your existing
   installation.

The existing installation must be ComfyUI with the MiniMax H3 nodes and the
fixes listed under [What gets installed](#what-gets-installed). Settings checks
file names, sizes, headers and code markers to decide compatibility.

**Command-line alternative.** Every dependency has its own installer that uses
the same pinned manifest, for example:

```bash
powershell -ExecutionPolicy Bypass -File installers\Install-b25.ps1
```

The available installers are `Install-comfy`, `Install-comfy_python`,
`Install-archive_tool`, `Install-gguf`, `Install-h3_helpers`,
`Install-hybrid_node`, `Install-encoder`, `Install-video_vae`,
`Install-audio_vae`, `Install-b25`, `Install-b20`, `Install-fl2va` and
`Install-ffmpeg`. `installers\Bootstrap-Runtime.ps1` prepares only the dashboard
runtime.

### 5. Start generating

1. In Settings, click **Start ComfyUI**. The first start loads the CUDA runtime
   and can take a while.
2. When it shows as connected, switch to **Generate**.
3. Pick a preset, add a starting frame and character references, write a prompt
   and click **Generate take**. The first take also loads the models, so it is
   much slower than later ones.

Next time, just double-click `Start-MotionBench.cmd` again. If the dashboard is
already running it only opens the browser.

### What gets installed

All versions are pinned in [installers/dependencies.json](installers/dependencies.json)
and never auto-update. License details are in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md); full hashes and provenance are in
[docs/PORTABLE_DEPENDENCY_SOURCES.md](docs/PORTABLE_DEPENDENCY_SOURCES.md).

**Software**

| Component | Version | Download | Used for |
| --- | --- | ---: | --- |
| [Python embeddable](https://www.python.org/downloads/release/python-31314/) + [pip](https://pypi.org/project/pip/26.2.1/) | 3.13.14 / 26.2.1 | 12.8 MB | Dashboard runtime (`.runtime/`) |
| [ComfyUI portable for NVIDIA](https://github.com/Comfy-Org/ComfyUI/releases/tag/v0.33.1) | v0.33.1 | 2.13 GB (≈4.4 GB extracted) | Generation engine, its own Python and CUDA 13.0 runtime |
| [7-Zip `7zr.exe`](https://github.com/ip7z/7zip/releases/tag/26.03) | 26.03 | 0.6 MB | Extracting the ComfyUI archive |
| [ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF/tree/6ea2651e7df66d7585f6ffee804b20e92fb38b8a) | `6ea2651` | 38 KB | Loading the `.gguf` models |
| [ComfyUI-H3-Multishot](https://github.com/jlucasmcrell/ComfyUI-H3-Multishot/tree/d7d197709fc93c1afb125e3db1dfd4ac0ae28c60) (subset) | `d7d1977` | 6.6 MB | H3 memory and conditioning helpers (`H3ConditionStrength`, `H3FreeTextEncoder`) |
| [MiniMax H3 Image + Reference to Video node](https://github.com/BigStationW/ComfyUi-MiniMax-H3-Image-And-Reference-To-Video/tree/f12a48fe9f1c8ee313a081a91327c31b5bfb9895) | `f12a48f` | 10 KB | Hybrid frames + references conditioning |
| [FFmpeg essentials (gyan.dev)](https://github.com/GyanD/codexffmpeg/releases/tag/8.1.2) | 8.1.2 | 110 MB | Assembly and trimmed clip export (not needed for generation) |

The installer applies small compatibility patches inside the managed copies only
([installers/patches.py](installers/patches.py)): the hybrid node's audio-helper
compatibility and first-frame ordering, the H3 timestep FP32 fix, GGUF support
and the required H3 memory/conditioning helpers.

**Models**

All presets share the encoder and the two VAEs, downloaded once.

| Model | File | Size | Needed by |
| --- | --- | ---: | --- |
| [H3 visual/text encoder (Qwen3-VL 32B, NVFP4 AWQ)](https://huggingface.co/Comfy-Org/MiniMax-H3) | `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | 15.69 GB | All presets |
| [H3 video VAE](https://huggingface.co/Comfy-Org/MiniMax-H3) | `minimax_h3_video_vae_fp16.safetensors` | 5.21 GB | All presets |
| [H3 audio VAE](https://huggingface.co/Comfy-Org/MiniMax-H3) | `minimax_h3_audio_vae_fp32.safetensors` | 0.61 GB | All presets |
| [Hybrid b25 Q4](https://huggingface.co/hoidhxd/MiniMax-H3-Hybrid-b25-49-GGUF) | `minimax_h3_hybrid_fl2va_ref2va_b25-49-Q4_0.gguf` | 11.39 GB | Hybrid balanced (default) |
| [Hybrid b20 INT8](https://huggingface.co/smhfacct/Minimax-H3-fl2va-ref2va-hybrid-models) | `minimax_h3_hybrid_fl2va_ref2va_b20-49-int8.safetensors` | 20.97 GB | Hybrid stronger references |
| [FL2VA curve Q4](https://huggingface.co/joeygambino/MiniMax-H3-curve-GGUF) | `MiniMax-H3-fl2va-curve-Q4_0.gguf` | 11.46 GB | Original FL2VA |

| Model selection | Total model download |
| --- | ---: |
| Balanced hybrid b25 | 32.89 GB |
| Stronger-reference hybrid b20 | 42.47 GB |
| Original FL2VA | 32.97 GB |
| Both hybrids | 53.86 GB |
| All three | 65.32 GB |

The model weights are under the
[MiniMax H3 Community License](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE);
review its terms on each machine you install them on. Weights and the hybrid
node source are always downloaded from their upstream sources and are not stored
in this repository.

### Launcher options

`Start-MotionBench.cmd` passes its arguments to `Start-H3Dashboard.ps1`:

| Option | Effect |
| --- | --- |
| `-Port 8790` | Use another dashboard port (default 8788). |
| `-NoBrowser` | Do not open the browser. |
| `-DataPath <folder>` | Use another data folder (history, references, logs). |
| `-PythonPath <python.exe>` | Use an already prepared dashboard runtime instead of `.runtime/`. |
| `-ComfyHome <folder>` / `-ComfyPort <port>` | Explicit engine overrides; normally set in Settings. |

### Moving existing work to the new PC

The repository does not contain your history, references or videos. To carry
them over, copy `data/` (take records, prompts, reference shelf, assemblies) and
your output video folders. Do not copy `config.local.json`: it holds absolute
paths for the old machine. Re-enter paths in Settings instead. Takes keep their
original engine location, so restore videos to the same paths or re-add the
output folders in the dashboard.

### Troubleshooting

- **"Port 8788 is occupied"**: another program or another MotionBench data folder
  is using it. Start with `-Port 8790`.
- **"NVIDIA GPU/driver unavailable"**: `nvidia-smi` failed. Install or repair the
  NVIDIA driver, then restart MotionBench.
- **Generate is disabled or a preset is greyed out**: open Settings; missing files
  and nodes are marked red. Presets you did not install do not block the others.
- **Changed engine, model or node paths**: restart ComfyUI from Settings. An engine
  started outside MotionBench must be closed externally first.
- **Logs**: `data/dashboard.stderr.log`, `data/install.log`, and the ComfyUI logs in
  `data/comfy*.log`.

## Generate and assemble

Select a model preset and Conditioning; only supported image roles appear.
With a starting frame it is always **`<Picture 1>`**. Character references start
at **`<Picture 2>`**; an optional last frame follows them. Without a starting
frame, references begin at Picture 1. Use **`<Subject 1>`** for the character.
All character references describe the same character. The UI displays the actual
numbers. Old version 2 prompts are remapped when imported or reused.

| Preset | Architecture | Input modes |
| --- | --- | --- |
| Hybrid balanced · b25 Q4 (default) | Hybrid frames + references | First / last / both frames, with or without 1–9 character references, references only, text only |
| Hybrid stronger references · b20 INT8 | Hybrid frames + references | Same as b25 |
| Original FL2VA · Q4 | Frame anchors | First frame, last frame, first + last, text only (no character references) |

Images can be PNG, JPEG or WebP up to 20 MB. Output is an MP4 at 24 fps with
audio generated by H3.

**Use last frame as first** keeps character references for the next segment,
matches the source canvas and clears the previous endpoint. Takes retain their
seed, prompt, references and graph. Cancelled/discarded takes remain reusable
under a separate heading, collapsed by default. Deleting a completed video
requires confirmation and retains its take record.
The trash button on a discarded take permanently removes its history and saved
settings; **Empty trash** removes all discarded takes at once. Failed takes have
a **Delete** button that removes them outright, including any recorded partial
video outputs. Reference shelf images remain available for other takes.

Completed generations show **Wall time** in history and review details, measured
from execution start through video completion (including model loading and
decoding). Existing takes use their saved timestamps. If only submission time
was recorded, the label says **incl. queue**. Saved clips do not claim a generation
time of their own.

**Save first frame** and **Save last frame** download a full-resolution PNG and
keep it on the reference shelf. **Use first frame as last** prepares a lead-in
that ends at the beginning of the selected take. It matches the canvas and
clears the old starting frame. Hybrid uses **Last frame + character references**:
current reference selections stay selected; if none are selected, the source
take's references are restored. Add or remove references, check the displayed
Picture numbers, and describe the preceding action before clicking **Generate**.
Choose **First + last frame + character references** to also specify a starting
pose. FL2VA supports the endpoint without independent character references;
REF2VA requires switching to a frame-capable preset first.

**Assemble videos** orders, trims and joins existing clips with optional audio.
The default canvas matches the largest source; smaller clips upscale. Aspect
ratio mismatches require crop confirmation. **Save clip through this frame**
creates a separate copy ending at the paused frame. Assembly uses CPU FFmpeg,
not H3, and has an independent output-folder preference.

Path changes are blocked while generations or exports are active.

## Data and sharing

Back up `data/` and your output videos. Local configuration is `config.local.json`.
Browser drafts remain local to the browser. Changing dependency paths does not
move prior videos; takes retain their original engine location when needed.

`.gitignore` keeps `data/`, `exports/`, `dependencies/`, `.runtime/`, `dist/`,
`migration/`, `.artifacts/` and `config.local.json` out of the repository.
`Build-SharePackage.ps1` uses an explicit allowlist for the same reason.

## Development

The Python suites run with `python -m unittest discover -p "test_*.py"`.
The assembly verification also needs NumPy and FFmpeg. Browser checks require
Chrome and Playwright and should use isolated synthetic data.
`node check_lead_in_ui.cjs` starts its own isolated fixture server and checks
timing, frame downloads and lead-in setup without GPU generation.
The same check covers failed-take deletion and permanent trash cleanup. Set
`PLAYWRIGHT_MODULE` if Playwright is outside the normal Node module path, and
`H3_TEST_PYTHON` to override the bundled dashboard Python.

Design notes and history are in [docs/](docs/).
