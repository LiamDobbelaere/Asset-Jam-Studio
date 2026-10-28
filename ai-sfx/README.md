# ai-sfx: game sound effects from text (local, RTX 2080 / 8 GB)

Text → 48 kHz mono sound effects (explosions, gunshots, footsteps, impacts, UI blips, ambience loops) with
**MOSS-SoundEffect v2.0**, fully local on the 2080, with weights you may ship with a commercial game.

| Stage | Model | License | Notes |
|---|---|---|---|
| Text encoder | Qwen3-1.7B (fine-tuned, last hidden state) | Apache-2.0 | emulated bf16; visits the GPU only to encode the prompt |
| Text → latent | **MOSS-SoundEffect v2.0 DiT** (1.3B, Wan-style, flow matching) | Apache-2.0 | fp16 on Turing, CFG 4, 50 steps |
| Latent → audio | DAC VAE (128-d latents) | Apache-2.0 | fp32, 48 kHz mono |

Why this model (October 2026): it is the only current open-weight sound-effect model that is (a) commercially
usable without a revenue cap or territory clause (Apache-2.0 code and weights), (b) fits 8 GB (a 1.3B DiT plus a
1.7B text encoder that can be offloaded) and (c) a dedicated SFX model rather than a music or speech model with
side effects. Rejected: **Stable Audio 3 small-sfx** and **Stable Audio Open** (Stability community license,
US$1M revenue cap, gated, Gemma terms on the text encoder; the same reason ai-mus rejected it),
**Sony Woosh** (weights CC-BY-NC), **TangoFlux**, **AudioLDM 2**, **AudioGen**, **MMAudio** (non-commercial
weights), **MOSS-SoundEffect v1** (8B autoregressive, 17 GB in bf16), **HunyuanVideo-Foley** (Tencent license
excludes the EU), **Bark** (a speech model; its non-speech sounds are incidental).

Measured on the 2080: ~25 s per sound whatever its length, ~20 s model load, 6.7 GB peak VRAM (details below).

## Setup on a fresh machine

```bash
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Clones `MOSS-TTS/` (OpenMOSS's repository; only its `moss_soundeffect_v2` package is used) at a pinned commit,
creates `.venv` with uv (Python 3.12, torch 2.9.0+cu128: the pin upstream ships; it runs on sm_75), installs the
inference subset of its requirements (no Gradio demo, no fine-tuning stack) and the package itself with
`--no-deps`, then downloads ~11 GB into `models/`. `-SkipModels` skips the download.

## Layout

```
setup.ps1                 one-time install (clone, venv, packages, model)
requirements.txt          pinned Python packages (torch comes from the cu128 index)
sfx.bat "prompt" [...]    text -> wav/flac/ogg in output/
scripts/sfx.py            the CLI
scripts/sfx_worker.py     persistent JSON-lines worker used by studio/ (the model stays loaded between sounds)
scripts/common.py         paths, the Engine (8 GB loading: fp16 DiT, offloaded text encoder), trim/loop/normalize/write helpers
scripts/download_models.py
MOSS-TTS/                 upstream checkout (ignored; recreated by setup.ps1)
models/                   weights (~11 GB, ignored): MOSS-SoundEffect-v2.0/{transformer,text_encoder,tokenizer,vae,scheduler}
output/                   generated sounds (ignored)
```

## Usage

```bash
sfx.bat "a huge explosion with a deep boom and debris falling, outdoors" --duration 5 --takes 4
sfx.bat "a single pistol gunshot, sharp and loud, short echo in a concrete room" --duration 1.5 --takes 4 --name PistolShot
sfx.bat "steady rain falling on a tin roof, no thunder" --duration 20 --loop
sfx.bat "a soft short user-interface click, clean and bright" --duration 0.5 --format ogg
```

Prints the written path(s). Output is 48 kHz mono, 16-bit wav by default (`--format ogg|flac`); mono is what a
game engine wants for positional (3D) sounds.

| Flag | Effect |
|---|---|
| `--duration S` | 0.5–30 s; the prompt is told the length (`… duration: 1.5s`, the training convention) |
| `--takes N` | N sounds with seeds `seed, seed+1, …` (random seeds without `--seed`); effects vary a lot per seed |
| `--loop` | seamless loop for ambiences: the last `--crossfade` seconds (default 1) are equal-power blended into the start and cut off |
| `--negative TEXT` | steer away from something ("music, speech, voices") |
| `--steps N`, `--cfg X` | solver steps (default 50; upstream 100) and guidance (default 4) |
| `--no-trim` | keep leading/trailing silence (default: cut below -50 dBFS, keep 20 ms of lead-in) |
| `--peak DB` / `--peak none` | peak-normalize level (default -1 dBFS) or keep the model's raw level |
| `--highpass HZ` | low-cut against sub-bass drift (default 30; 0 = off) |
| `--no-offload` | keep the text encoder on the GPU too (~6.5 GB, saves the transfer) |

Prompts work like a sound-library description: what makes the sound, what it does, where it happens
("footsteps of boots walking on gravel, steady pace"; "a metal sword swung fast through the air and clashing
against another sword"). "single" vs "several", "close up" vs "far away" and the room ("small tiled room",
"open field") all matter.

## Speed and VRAM

The DiT always denoises a fixed 30 s latent (1500 tokens: the DAC hop is 960 samples) and the requested length is
cropped from it, so a 1 s click costs the same as a 30 s ambience. Measured on the 2080: **~25 s per sound** at 50
steps (0.40 s per step, two DiT passes each for guidance; text encode + decode + transfers ~5 s), plus ~20 s to
load the model the first time. Peak VRAM 6.7 GB (during the prompt encode, DiT + text encoder), 2.9 GB while
the studio worker sits idle with the model loaded.

## Post-processing (every take)

Checked on spectrograms of explosion, gunshot, footsteps, sword and rain takes:

- **Crop with a margin.** The model keeps to the length in the prompt only roughly: a "1.5 s" gunshot (seed 1)
  started at 1.2 s with its echo running to 1.7 s, and the rest of the 30 s canvas was silent. Cropping at
  exactly 1.5 s cut the tail off, so the crop is `duration + max(1 s, duration / 2)` and the silence trim decides
  where the file ends (loops crop at exactly `duration`).
- **Trim relative to the peak:** the start at -40 dB (a one-shot must sound on the frame it is triggered; the
  model often leaves a low rumble before the hit), the tail at -50 dB (keep the reverb).
- **30 Hz high-pass** (`--highpass`, zero-phase): the DAC decoder leaves a slow sub-bass wander in the waveform
  (clearly visible on footsteps) that eats headroom.
- 50 ms fade-out, then peak-normalize to -1 dBFS.

## 8 GB / Turing notes

- Upstream loads the whole pipeline in one dtype (bf16 via autocast, with the DiT weights left in fp32).
  `common.py` instead casts the DiT to fp16 (Turing has fp16 tensor cores, bf16 is emulated) and keeps only it
  resident (2.8 GB). The 3.4 GB Qwen3 text encoder (bf16: Qwen activations can overflow fp16; it runs once per
  sound) and the 1.5 GB fp32 DAC VAE take turns next to it, each moved to the GPU only for its stage, with
  `torch.cuda.empty_cache()` after every move. The first version kept the VAE resident too: DiT + VAE + text
  encoder peaked at 8.1 GB, the caching allocator kept that reserved, and every DiT step then ran out of
  Windows' shared system memory: **14 s per step instead of 0.4 s** (a 50-step sound took 12 minutes, with no
  error). If a sound ever takes minutes, check `nvidia-smi` for memory at the 8 GB ceiling first.
- `Module.to(float16)` would also cast the DiT's complex RoPE tables and silently drop their imaginary part;
  `common.py` restores them after the cast.
- The DiT forward is wrapped in `torch.compile` + Triton CUDA graphs upstream. There is no Triton for Windows /
  sm_75 here, so `TORCHDYNAMO_DISABLE=1` is set (upstream's own scripts do the same).
- If a prompt ever produces NaNs in fp16 the engine says so; `AISFX_DIT_DTYPE=float32` (5.2 GB, still fits with
  the offloaded text encoder) is the fallback.
- torchaudio 2.9 no longer writes files without torchcodec; output goes through soundfile (and ffmpeg for ogg,
  as in ai-mus, because libsndfile's Vorbis encoder crashes on Windows).
