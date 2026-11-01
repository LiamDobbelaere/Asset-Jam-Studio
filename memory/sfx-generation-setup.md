---
name: sfx-generation-setup
description: "ai-sfx: MOSS-SoundEffect v2.0 (Apache-2.0) for game SFX on the 2080; why not Stable Audio 3/Woosh; the VRAM-spill slowdown and the crop/trim findings"
metadata:
  type: project
---

ai-sfx/ (added 2026-10-28) wraps MOSS-SoundEffect v2.0 (OpenMOSS, Apache-2.0 code + weights): 1.3B Wan-style DiT,
Qwen3-1.7B text encoder, DAC VAE, 48 kHz mono, ≤30 s. Package `moss_soundeffect_v2` from the MOSS-TTS repo
(pinned 934d682), own venv (py 3.12, torch 2.9.0+cu128), `--no-deps` + curated requirements. Studio: SFX page,
`sfx` job kind, worker `ai-sfx/scripts/sfx_worker.py`.

Rejected: Stable Audio 3 small-sfx / Stable Audio Open (community license, $1M cap, gated), Sony Woosh (CC-BY-NC
weights), TangoFlux/AudioLDM2/AudioGen/MMAudio (NC), MOSS-SoundEffect v1 (8B AR, 17 GB).

**Why:** commercial EU game on an 8 GB Turing card; see [[hardware-and-license-constraints]].

**How to apply:**
- VRAM: only the fp16 DiT (2.8 GB) stays resident; text encoder (3.4 GB bf16) and VAE (1.5 GB fp32) swap in per
  stage + `empty_cache()`. With all three resident it peaked at 8.1 GB and Windows silently spilled into shared
  memory: 14 s/step instead of 0.4 s, no error. Slow job → check nvidia-smi near 8 GB first.
- Speed: ~25 s per sound at 50 steps regardless of length (always a 30 s canvas), ~20 s load, 6.7 GB peak.
- The model places the sound only roughly within the prompted duration (a 1.5 s gunshot ran 1.2–1.7 s), so the
  crop has a margin and trim decides the end; trim head -40 dB / tail -50 dB rel. peak; 30 Hz high-pass removes
  DAC sub-bass drift. Verified on spectrograms, not by ear.
- `Module.to(fp16)` on the DiT would drop the imaginary part of its complex RoPE buffers; common.py restores them.
- Auto mode blocked Claude from running setup.ps1/pip installs and killing processes (use TaskStop for its own
  background tasks); the user ran setup.ps1 themselves.

Related: [[music-generation-setup]], [[studio-web-ui]]
