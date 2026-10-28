# Setup

Written for a person or an LLM coding agent installing the toolkit on a fresh machine. Follow the steps in
order; each one ends with a check. Every module except `ai-3d` is optional, so install only what you need.

## 1. Requirements

| | Needed | Notes |
|---|---|---|
| OS | Windows 10 or 11, x64 | developed on Windows 11; the setup is PowerShell, the CLIs are `.bat` files |
| GPU | NVIDIA, RTX 20-series (Turing) or newer, 8 GB VRAM or more | every setting was tuned on an RTX 2080 8 GB; GTX 10-series and older are not supported by the CUDA 13 PyTorch builds |
| Driver | NVIDIA driver with CUDA 13 support (R580 or newer) | `nvidia-smi` must print `CUDA Version: 13.0` or higher in its header |
| RAM | developed with 64 GB | ai-anim's text encoder holds ~16 GB of CPU RAM while loaded and Wan 2.2 streams its weights from RAM; less than 32 GB is untested |
| Disk | ~105 GB for all modules (measured), plan ~130 GB free with the uv cache and your outputs | per module below; NVMe recommended, models load from disk on every job |
| git | on PATH | `winget install --id Git.Git --exact` |
| uv | on PATH | `winget install --id astral-sh.uv --exact`; uv downloads Python 3.12 itself, no system Python needed |
| ffmpeg | on PATH (or `C:\ffmpeg\bin\ffmpeg.exe`, or the `FFMPEG` env var) | `winget install --id Gyan.FFmpeg --exact`; needed by ai-vid, by ai-mus for `.ogg` output and by ai-voc for `.mp3` voice recordings |
| Blender 5.1 | `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`, or the `BLENDER` env var | `winget install --id BlenderFoundation.Blender --exact --version 5.1.2` (the tested version; installs to the default path). Needed by ai-3d's `gameasset` / `character` presets (FBX export + planar dissolve) and by ai-anim |
| Hugging Face account | only for ai-anim's text-to-motion clips | access to the gated [meta-llama/Meta-Llama-3-8B-Instruct](https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct) repo (step 6) |

Open a new terminal after installing git / uv / ffmpeg so PATH is refreshed. Check:

```bash
nvidia-smi
git --version
uv --version
ffmpeg -version
"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" --version
```

## 2. Install the modules

Run each script from anywhere (they resolve paths from their own folder). Each one creates its own
git-ignored venv, so the modules never share Python packages, then downloads its weights. `-SkipModels` skips
the download if you want to fetch weights later by re-running the script.

| Order | Command | What it installs | Disk |
|---|---|---|---|
| 1 (required) | `powershell -ExecutionPolicy Bypass -File ai-3d\setup.ps1` | ComfyUI at a pinned commit + its venv (the studio also runs on this venv), the Turing fp16 patch, workflows, custom nodes; 3D, texture and sprite models | 33 GB models + 4.3 GB venv |
| 2 (optional, needs 1) | `powershell -ExecutionPolicy Bypass -File ai-vid\setup.ps1` | the Wan 2.2 node + workflow inside ai-3d's ComfyUI; Wan 2.2 TI2V-5B weights | 17 GB |
| 3 (optional) | `powershell -ExecutionPolicy Bypass -File ai-voc\setup.ps1` | Qwen3-TTS + Whisper in `ai-voc\.venv` | 6.4 GB models + 3.7 GB venv |
| 4 (optional) | `powershell -ExecutionPolicy Bypass -File ai-mus\setup.ps1` | ACE-Step 1.5 at a pinned commit in `ai-mus\.venv` (torch 2.7.1+cu128, the build ACE-Step pins) | 11 GB models + 6 GB venv |
| 5 (optional) | `powershell -ExecutionPolicy Bypass -File ai-anim\setup.ps1` | Make-It-Animatable + NVIDIA Kimodo at pinned commits in `ai-anim\.venv` | 2.3 GB weights + 3.4 GB venv now, 16.5 GB more in the Hugging Face cache on the first animation (step 3) |

The venv sizes above are already counted in the total. uv also keeps a cache of downloaded wheels in `%LOCALAPPDATA%\uv\cache` (`uv cache clean` frees it after setup).

Messages that are expected and harmless:
- ai-3d: "patch already applied or does not apply cleanly; check manually" on a re-run (the patch is in place).
- ai-3d: "CIVITAI_TOKEN not set, skipping the optional StylizedTexture_ZIT LoRA". The LoRA is opt-in for
  `texture.bat --lora`; set `CIVITAI_TOKEN` (civitai.com → account settings → API keys) and re-run
  `ai-3d\ComfyUI\.venv\Scripts\python.exe -I ai-3d\scripts\download_models.py` to get it.
- ai-voc: "SoX could not be found" when speaking (an unused tokenizer in the `qwen-tts` package).

Each script ends with "Setup done." and a command to try.

## 3. ai-anim: Hugging Face access (only for animation clips)

Rigging works without this; generating clips from text needs Kimodo's Llama-3 text encoder, which is gated.

1. Request access on [meta-llama/Meta-Llama-3-8B-Instruct](https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct)
   (usually granted within minutes) and create a read token under Settings → Access Tokens.
2. Log in with the CLI from ai-anim's venv: `ai-anim\.venv\Scripts\hf.exe auth login` and paste the token.
   This is the user's own account and token; an agent should ask the user to run this step.
3. Optional: `ai-anim\.venv\Scripts\python.exe -I ai-anim\scripts\download_models.py --kimodo` downloads
   Kimodo and the text encoder (16.5 GB, into the Hugging Face cache, `%USERPROFILE%\.cache\huggingface` unless
   `HF_HOME` is set) now instead of during the first clip.

## 4. Start and check

```bash
studio\studio.bat
```

Opens `http://127.0.0.1:8765`. ComfyUI (needed for 3D, textures, sprites and video) can be started from the
studio header, or with `ai-3d\start_comfyui.bat` (`http://127.0.0.1:8188`). Both servers listen on localhost only.

With the studio running, from another terminal:

```bash
ai-3d\ComfyUI\.venv\Scripts\python.exe studio\studio_cli.py health
```

Every installed module shows `"installed": true` under `backends`; `anim.hf_token` is `true` after step 3, and
`comfy.running` is `true` once ComfyUI is up. Then run one small job per installed module; each module's
README has examples.

## 5. Environment variables

| Variable | Default | Used for |
|---|---|---|
| `BLENDER` | `C:\Program Files\Blender Foundation\Blender 5.1\blender.exe` | FBX export (ai-3d) and rigging / retargeting (ai-anim) |
| `FFMPEG` | `ffmpeg` on PATH, else `C:\ffmpeg\bin\ffmpeg.exe` | video assembly, logo reveals, `.ogg` music |
| `STUDIO_URL` | `http://127.0.0.1:8765` | where `studio_cli.py` (and the Claude skill) find the studio |
| `CIVITAI_TOKEN` | unset | optional texture LoRA download |
| `HF_HOME` | `%USERPROFILE%\.cache\huggingface` | where ai-anim's Hugging Face downloads and token live |

## 6. GPUs with more than 8 GB

Nothing needs changing: the 8 GB settings (fp16 instead of bf16, int8 / fp8 weights, CPU offloading, small
batch sizes) also run on bigger cards, and the Turing fp16 patch is harmless on GPUs with bf16 (ComfyUI uses
bf16 there and the patched code path is not taken). To trade the
headroom for speed or quality, ask your LLM to adapt the module you care about to your GPU; each module's
README explains which choices were made for 8 GB and what was rejected because it needed more VRAM.

## 7. Using it from Claude Code

`.claude/skills/asset-jam-studio/` teaches Claude Code to queue jobs through `studio\studio_cli.py`. It works in
sessions opened in this folder; copy it to `%USERPROFILE%\.claude\skills\` to use it from other projects, and
replace `<repo>` in it with this folder's path.
