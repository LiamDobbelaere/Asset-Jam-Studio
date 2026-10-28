# ai-mus: game music from text (local, RTX 2080 / 8 GB)

Text → 48 kHz stereo music with **ACE-Step 1.5**, instrumental by default, optionally as a seamless
loop. Runs fully local on the 2080 in fp16, no cloud, weights you may ship with a commercial game.

| Stage | Model | License | Notes |
|---|---|---|---|
| Planner ("thinking") | acestep-5Hz-lm-0.6B (Qwen3-0.6B) | MIT | writes bpm / key / structure and 5 Hz semantic codes from the caption; optional |
| Text + codes → latent | **acestep-v15-turbo** (2B DiT, 8 steps, no CFG) | MIT | the music model; fp16 + eager attention on Turing (picked by ACE-Step itself) |
| Text encoder | Qwen3-Embedding-0.6B | Apache-2.0 | offloaded to the CPU between uses |
| Latent → audio | ACE-Step VAE | MIT | tiled decode, 48 kHz stereo |

Why this model: it is the only current open-weight music model that is (a) commercially usable
without a revenue cap (MIT code and weights; the model card explicitly allows commercial use of the
output), (b) runs in 8 GB (upstream's own 6–8 GB tier is exactly this 2B turbo + 0.6B LM + PyTorch
backend setup) and (c) is a full music model with instrumental, lyric, bpm/key and structure control
rather than a 47 s sound-effect generator. Rejected: **MusicGen** and **YuE** (weights CC-BY-NC),
**Stable Audio Open 1.0 / Small** (Stability community license, US$1M revenue cap, ≤47 s, trained for
SFX/loops not songs), **HeartMuLa** (Suno-like quality but 3B LM + codec, 10–12 GB without 4-bit
quantisation), **MiniMax Music 3** (20 GB+ in full precision, attribution clause), **DiffRhythm 2**
(Apache-2.0 code but its VAE is a Stable Audio Open fine-tune and inherits that license). The 4B "XL"
ACE-Step checkpoints and the 1.7B/4B planners need 12–24 GB and are not downloaded (the 1.7B planner is fetched but needs CPU offload on 8 GB).

Measured on the 2080 (30 s track, planner on): 21 s wall, of which planner LM 14 s (3 s metadata +
10 s semantic codes, ~15 tokens/s), DiT 8 steps 1.3 s, VAE decode ~3 s, offload shuffling ~2 s; peak
VRAM 5.8 GB. Without the planner: 7 s. First call also loads the models (DiT ~12 s, LM ~26 s).

## Setup on a fresh machine

```bash
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Clones `ACE-Step-1.5/` at a pinned commit, creates `.venv` with uv (Python 3.12, torch 2.7.1+cu128:
the build ACE-Step pins for Windows; it runs on sm_75), installs the runtime subset of its
requirements (no Gradio, FastAPI, nano-vllm or training stack) and the package itself with
`--no-deps`, then downloads ~11 GB into `models/`. `-SkipModels` skips the download.

## Layout

```
setup.ps1                 one-time install (clone, venv, packages, models)
requirements.txt          pinned Python packages (torch comes from the cu128 index)
compose.bat "caption" [...]   text -> wav/flac/ogg in output/
scripts/compose.py        the CLI
scripts/music_worker.py   persistent JSON-lines worker used by studio/ (models stay loaded between tracks)
scripts/common.py         paths, the Engine (DiT + LM loading tuned for 8 GB), loop/trim/write helpers
scripts/download_models.py
ACE-Step-1.5/             upstream checkout (ignored; recreated by setup.ps1)
models/                   weights (~11 GB, ignored): acestep-v15-turbo, acestep-5Hz-lm-0.6B, acestep-5Hz-lm-1.7B, vae, Qwen3-Embedding-0.6B
output/                   generated tracks (ignored)
```

## Usage

```bash
compose.bat "calm piano and soft strings, a quiet village at dusk, gentle, no vocals" --duration 45 --loop
compose.bat "8-bit chiptune boss battle, fast, tense, square wave leads" --bpm 170 --takes 3 --seed 7
compose.bat "indie folk, acoustic guitar, warm" --lyrics lyrics.txt --language en --duration 120
```

Prints the written path(s). Output is 48 kHz stereo, 16-bit wav by default (`--format ogg|flac`).

| Flag | Effect |
|---|---|
| `--duration S` | target length, 10–600 s (the planner may round it); loops work best at 30–90 s |
| `--loop` | seamless loop: the last `--crossfade` seconds (default 2) are equal-power blended into the start and cut off |
| `--takes N` | N tracks with seeds `seed, seed+1, …` (random seeds without `--seed`) |
| `--lyrics FILE\|TEXT` | sung vocals; `[Verse]`, `[Chorus]`, `[Bridge]` tags shape the song; `--language en` |
| `--bpm`, `--key "A minor"`, `--timesig 3` | pin tempo, key, time signature instead of letting the planner choose |
| `--no-think` | skip the planner LM (faster; the DiT only sees the caption) |
| `--ref track.wav` | reference audio for style/timbre transfer |
| `--lm ""` / `--lm acestep-5Hz-lm-1.7B` | no planner / the bigger planner (does not fit next to the DiT in 8 GB without offload) |

The caption works like a music-library description: genre, instruments, mood, tempo words
("lo-fi hip hop, warm Rhodes piano and vinyl crackle, relaxed, slow"). Say "no vocals" too if voices
creep into an instrumental. With the planner on, the LM also fills in bpm/key/time signature (you can
read them back from `--json`); the caption itself is passed to the DiT verbatim, the LM is not allowed
to rewrite it.

## Notes

- `--format ogg` is encoded by ffmpeg (libvorbis, q6) from a temporary wav. libsndfile 1.2.2's own Vorbis
  encoder terminates the process on Windows with no exception, leaving a 4 KB file, even for 12 s of noise.
- The DiT is offloaded to the CPU between phases like the VAE and text encoder (`offload_dit_to_cpu`).
  Kept resident it leaves under 0.5 GB free on the 8 GB card and ACE-Step then quietly decodes the
  VAE on the CPU: 20+ minutes for a 64 s track, with the GPU idle. The move costs a few seconds.
- Turing has no bf16. ACE-Step detects compute capability < 8 and loads the DiT in fp16 with eager
  attention (its own fix for fp16 softmax overflow). The planner LM is a different story, measured here:
  fp16 overflows (NaN probabilities, CUDA assert in `multinomial`), fp32 decodes at half speed and its
  2.4 GB leaves no room for the VAE (which then decodes on the CPU, minutes instead of seconds), bf16 is
  emulated but memory-bound decoding is still the fastest and it is numerically safe. So the LM stays
  bf16 (`AIMUS_LM_DTYPE` overrides) and is offloaded to the CPU between its two phases.
- Sung lyrics overflow fp16 in the DiT's lyric encoder (layer 7 MLP activations ~9e3 → inf → every latent
  NaN; instrumentals only pass "[Instrumental]" and are fine). `common.py` keeps that ~0.1 GB encoder in
  fp32 and casts its output back. Verified: Whisper transcribes a 60 s generated pop song back to the
  lyrics file nearly word for word.
- The planner sometimes returns a nonsense bpm for sung tracks (30 for "upbeat indie pop"); pin `--bpm`
  when it matters. The caption and lyrics still come through either way.
- nano-vllm would make the planner faster but needs Triton + flash-attn builds for Windows/sm_75; not
  installed. `--no-think` is the quick path for iterating on captions; turn the planner on for the final take.
- VRAM: DiT fp16 ≈ 4.7 GB resident; VAE/text encoder/LM are offloaded to the CPU when idle
  (`offload_to_cpu`). The VAE decode is tiled automatically when free VRAM is low. INT8 quantisation
  (`torchao`) is not used; it needs `torch.compile`/Triton.
- Cover / repaint / extend / stem ("lego") tasks exist in ACE-Step (the base checkpoint for lego and
  extract) but are not wired into `compose.py`; `Engine.generate` only does text2music (+ reference).
