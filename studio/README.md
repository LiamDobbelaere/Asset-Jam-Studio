# studio: the Asset Jam Studio web front-end and API

One page per flow, a single job queue, live progress, and the same queue over HTTP for scripts and for Claude.
Wraps the command-line tools in `ai-3d/` (3D props, world textures, 2D sprites), `ai-voc/` (voice lines),
`ai-mus/` (music), `ai-sfx/` (sound effects), `ai-vid/` (videos) and `ai-anim/` (rigged, animated characters) without replacing them: the
studio imports the same prompt builders the `.bat` files use, so a run from the browser is the same run as
from the terminal.

```bash
studio\studio.bat            # then http://127.0.0.1:8765 opens in the browser
studio\studio.bat --start-comfy --port 8765 --no-browser
```

Runs with the ComfyUI venv Python (`ai-3d/ComfyUI/.venv`), standard library only; the 3D viewer
loads three.js from a CDN. ComfyUI can be started and stopped from the header (its output goes to
`studio/comfy.log`, so it keeps running if the studio stops). The voice flow starts `ai-voc/scripts/tts_worker.py`
on demand, the music flow `ai-mus/scripts/music_worker.py`, the SFX flow `ai-sfx/scripts/sfx_worker.py`, the Animate flow `ai-anim/scripts/anim_worker.py`.

The header shows every flow as an icon with its label; on narrower windows the labels, then the chip texts,
fold away (hover for the detail) so nothing is cut off. The palette button picks the accent colour, saved in
the browser's localStorage.

## What each page does

| Page | Flow | Notes |
|---|---|---|
| Overview | queue, system state, ComfyUI log | cancel or forget any job |
| 3D Models | text → reference image → Pixal3D/TRELLIS.2 → GLB (+ FBX via Blender) | **Reference image only** first, check it, then **Build the 3D model from this image** (same seed, so ComfyUI serves the cached picture); or a full run; or upload your own reference picture (skips text-to-image). Intermediate images and meshes appear as nodes finish; sampler previews stream live. Library: every GLB/FBX in `ai-3d/assets` with a three.js viewer (wireframe, tri count), reference, diffuse, downloads, and **Reuse settings**. |
| Textures | text → seamless hand-painted texture | native / cross-fade / none tiling, LoRA strength, optional **reference texture** with a strength slider (img2img from an uploaded or library texture), 1×/2×2/4×4 repeat preview. |
| Sprites | FLUX.2 klein edit / pose / text-to-sprite | source sprite + up to two references (upload, or pick from the library), distilled or base model, alpha mode, canvas size. Results on a checkerboard with **Use as source** / **Add as reference** for quick iteration. |
| Voice | Qwen3-TTS zero-shot clone | voices and moods are read from `ai-voc/voices/` (play the reference, see its transcript), several takes per line, seed, sampling knobs, fine-tuned checkpoints if present, a cheat sheet of what punctuation does. Library: every wav in `ai-voc/output` with date, length, line and seed. **Add voice** runs `prep_voice.py` on a folder of an actor's lines (or on dropped audio files) with the Whisper transcripts streaming into the log; the ⚙ manager on each voice lists every clip with a play button and an editable transcript, lets you fix `ref.txt`, and builds a mood reference from ticked clips. |
| Video | shot list → Wan 2.2 clips + logo reveal → mp4 | presets from `ai-vid/shots/`; per shot: prompt, seconds, seed, **start/end frame** (none / previous shot's last frame / picture / generated keyframe with a reference), or a **logo reveal** (PNG, wordmark split, background). **Generate all**, **Keyframes only** (check the stills first), **Assemble existing clips** (new transitions / music), **Regenerate only this shot**. Live sampler preview; keyframes and last frames appear as they finish. Result: the final video, every clip (hover to play, ↻ redo with a new seed), keyframes. Library: every project in `ai-vid/output`. |
| Animate | humanoid mesh → rig (Make-It-Animatable) → text clips (Kimodo) → GLB + FBX | pick a GLB from the 3D library (generate one with the **Character** preset) or upload one; name, height, finger bones, T-pose reset; a list of clips (name + what the person does, seconds, seed) with presets (Idle, Walk, Run, …); **Rig only** first to check the T-pose, then **Rig + generate the clips**; **in place** drops the root motion. Result: three.js viewer that plays each clip (bones overlay), GLB with all clips, Unity FBX with one take per clip, BVH per clip; **Add the clips from the form** to an existing character keeps the earlier clips. Library: every character in `ai-anim/assets`. Needs Hugging Face access to Llama 3 for the text encoder (the form says so until the token is there). |
| Music | ACE-Step 1.5 text → music | caption, length, **seamless loop** (crossfade), takes, seed, optional lyrics + language, bpm / key / time signature, reference audio (upload or ♪ on any library track), planner LM on/off, wav/ogg/flac. Results play in a loop when the track is one; the planner's bpm/key are shown. Library: every track in `ai-mus/output` with caption, length and seed. |
| SFX | MOSS-SoundEffect v2.0 text → sound effect | prompt with one-click examples (explosion, gunshot, footsteps, sword, UI click, pickup, rain loop), length 0.5–30 s, takes (default 4: effects vary a lot per seed), **seamless loop** for ambiences, name, seed, wav/ogg/flac; advanced: negative prompt, steps, guidance, trim, peak level. Every take is trimmed, faded and peak-normalized to -1 dBFS, 48 kHz mono. Library: every sound in `ai-sfx/output` with prompt, length and seed. |

## Using it from scripts and from Claude

`API.md` documents the HTTP API; `studio_cli.py` (standard library, any Python 3) wraps it:

```bash
python studio\studio_cli.py health
python studio\studio_cli.py submit texture --prompt "grey cobblestone street, thin dark gaps" --name Cobble --wait
python studio\studio_cli.py submit music --caption "calm piano, slow, no vocals" --duration 45 --loop true --name Dusk
python studio\studio_cli.py wait <job id>
```

`GET /api/schema` lists every kind and parameter (the server validates jobs against the same table,
`core/schema.py`); `GET /api/jobs/<id>/wait` is a long poll that returns when the job finishes.

Claude: the skill in `.claude/skills/asset-jam-studio/SKILL.md` teaches a Claude Code session to check the studio,
queue jobs, wait for them and pick up the output files. It is picked up automatically when this repository
is the working directory; to use the studio from another project (a game you are building), copy or link the
folder to `%USERPROFILE%\.claude\skills\asset-jam-studio\` (the paths in it are absolute).

## Resources: one thing on the GPU at a time

Jobs run one after another in a single worker thread, whoever queued them (a page or the API). Before a
ComfyUI job the voice, music, sfx and animation workers are asked to drop their models; before a voice, music, sfx
or animation job the other workers are unloaded and ComfyUI is asked to unload its models (`POST /free`). The
worker processes themselves stay alive between jobs so the model load (~20 s voice, ~40 s music, a minute or
two for the Llama-3 encoder) happens once. The header shows ComfyUI / worker state and VRAM from `nvidia-smi`;
the Overview tiles have the unload / stop buttons. Control calls that would pull the GPU away from a running
job are refused (HTTP 409) until that job is cancelled.

## Layout

```
studio.bat            launcher
server.py             HTTP + server-sent events, static files, /api/* (route table) and /files/*
studio_cli.py         command-line client for the API (any Python 3, no dependencies)
API.md                the HTTP API
core/schema.py        every job kind with its parameters: validation, GET /api/schema, the CLI's flags, job titles
core/jobs.py          Job records (studio/jobs/<id>.json), queue, worker thread, wait_for()
core/comfy.py         ComfyUI client: prompt queueing, stdlib WebSocket for progress/previews, start/stop (log in comfy.log)
core/flows.py         model3d / texture / sprite / voice / music / sfx / video / anim flows on top of the ai-* builders
core/procworker.py    the JSON-lines worker-process protocol shared by voice, music, sfx and anim
core/voice.py         voices + moods discovery, TTS worker process
core/music.py         ACE-Step worker process, installed models, caption tips
core/sfx.py           MOSS-SoundEffect worker process, readiness, prompt tips
core/anim.py          rig + motion worker process (ai-anim/scripts/anim_worker.py), readiness checks, prompt tips
core/info.py          3D presets, video presets, per-back-end readiness (GET /api/health)
core/library.py       scans assets, textures, sprites, voice takes, music, sound effects, video projects and characters and joins them with job records
web/                  index.html, app.js (vanilla), viewer.js (three.js GLB viewer), style.css
jobs/, uploads/, comfy.log   job records, uploaded inputs, ComfyUI output (git-ignored)
```

## API

See `API.md`. In short: `POST /api/jobs {"kind", "params"}` queues a job (validated against `GET /api/schema`),
`GET /api/jobs/<id>/wait` blocks until it is done, `GET /api/health` says which back-ends are ready,
`GET /api/library/<name>` lists what exists, `POST /api/upload` takes input files, `/api/events` streams
progress. Job params are the CLI flags by name.
