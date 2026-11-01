---
name: music-generation-setup
description: "ai-mus: ACE-Step 1.5 (2B turbo DiT + 0.6B planner LM) for game music on the 2080; why not the alternatives; Turing dtype gotchas"
metadata:
  type: project
---

ai-mus/ wraps ACE-Step 1.5 (MIT code + weights, commercial use of output allowed) chosen Oct 2026 for game
music: instrumental-by-default captions, lyrics optional, bpm/key control, seamless `--loop` post-process.
Upstream clone pinned in setup.ps1 (commit ca1e85f, 2026-08-29), own venv (py 3.12, torch 2.7.1+cu128, the
Windows pin upstream ships), installed `--no-deps` with a curated requirements.txt (no gradio/fastapi/nano-vllm).

Rejected: MusicGen & YuE (CC-BY-NC), Stable Audio Open (Stability community license, $1M cap, ≤47 s, SFX-ish),
HeartMuLa (10-12 GB), MiniMax Music 3 (20 GB+), DiffRhythm 2 (VAE inherits Stable Audio license).

**Why:** the user ships a commercial game from the EU on an 8 GB Turing card; ACE-Step's own 6-8 GB tier is
exactly 2B turbo + 0.6B LM + PyTorch backend.

**How to apply:**
- Turing: ACE-Step auto-picks fp16 + eager attention for the DiT (fine). Planner LM: fp16 overflows (multinomial
  NaN assert); fp32 halves decode speed and its 2.4 GB pushes the VAE decode onto the CPU (minutes); bf16 is
  emulated but fastest (memory-bound) and safe. So common.py loads the LM **bf16 with offload_to_cpu=True**
  (`AIMUS_LM_DTYPE` overrides). Peak VRAM 5.8 GB instrumental, 6.6 GB with lyrics; 30 s track ≈ 21 s.
- Sung lyrics: the DiT lyric encoder overflows fp16 → NaN latents; common.py keeps it fp32 (output cast back). Planner bpm can be nonsense (30) for vocal tracks; pin --bpm.
- `use_cot_caption` is off on purpose: the LM otherwise rewrites the user's caption into something else.
- nano-vllm/Triton/flash-attn not installed (no Windows sm_75 builds); `pt` backend only.
- Studio: music worker is `ai-mus/scripts/music_worker.py` via `studio/core/procworker.LineWorker` (shared
  with the voice worker). The user keeps their own studio running on port 8765; test on another port.

Related: [[hardware-and-license-constraints]], [[voice-generation-setup]], [[studio-web-ui]]
