# Asset Jam Studio HTTP API

Everything the browser pages do is available over plain HTTP from `studio/server.py` (default
`http://127.0.0.1:8765`). `studio_cli.py` wraps it for scripts; `.claude/skills/asset-jam-studio/SKILL.md` teaches
Claude to use it. No authentication: the server listens on localhost only.

## The model

- A **job** is one generation request: `{"kind": ..., "params": {...}}`. Kinds: `model3d`, `texture`, `sprite`,
  `voice`, `voice_prep`, `music`, `sfx`, `video`, `anim`, `paint`, `prop`.
- Jobs are **queued and run one at a time**, oldest first, by one worker thread. Only one model set is on the
  GPU at any moment: before a ComfyUI job the voice / music / sfx / animation workers unload their models; before a
  voice, music, sfx or animation job the other workers unload and ComfyUI is asked to free its VRAM. Queuing ten jobs
  at once is safe; they just take turns.
- Status: `queued` → `running` → `done` | `error` | `cancelled`. `progress` has `pct`, `phase`, `detail`,
  `value/max`. Records are persisted in `studio/jobs/<id>.json` and survive restarts (a job that was running
  when the studio stopped is marked `error`).
- Outputs are absolute file paths on this machine (plus `/files/...` URLs served by the studio). Jobs never
  overwrite: a taken name gets `_2`, `_3`, ...

## Endpoints

| Method, path | What |
|---|---|
| `GET /api/health` | `{ok, version, backends: {comfy, voice, music, sfx, video, prop, paint, anim}, running, queued, gpu}`: which back-ends are installed and usable right now |
| `GET /api/schema` | every kind with its `summary`, typical `time`, `outputs` and `params` (type, help, default, choices, required) |
| `POST /api/jobs` `{"kind", "params", "parent"?}` | validate and queue; returns the job record (`400` with `{"error"}` when a parameter is wrong) |
| `GET /api/jobs?kind=&status=&limit=` | recent jobs, newest first (log trimmed to the last 3 lines) |
| `GET /api/jobs/<id>` | one job, full log |
| `GET /api/jobs/<id>/wait?timeout=60` | **long poll**: returns the job as soon as it is done / error / cancelled, or after `timeout` seconds (max 600) with `finished_waiting: false` |
| `POST /api/jobs/<id>/cancel` | cancel a queued job or interrupt a running one |
| `POST /api/jobs/<id>/delete` | forget the record (files stay) |
| `GET /api/jobs/<id>/preview` | the latest sampler preview image of a running ComfyUI job |
| `GET /api/events` | server-sent events: `job`, `log`, `preview`, `system`, `job_removed` |
| `GET /api/state` | system state + the last 200 jobs (what the browser loads at start) |
| `GET /api/library/models\|textures\|sprites\|voice\|music\|sfx\|videos\|characters` | what is on disk, joined with the job that made it |
| `GET /api/voices` | prepared voices with moods, reference transcripts, fine-tuned checkpoints; `GET\|POST /api/voices/<name>/clips` lists / edits the clip transcripts |
| `GET /api/presets3d`, `/api/music`, `/api/sfx`, `/api/video`, `/api/anim` | preset details, installed models, prompt tips, readiness per flow |
| `POST /api/upload?name=x.png` (raw body) | copies a file into `studio/uploads/`; returns `{path, url}` to use as `reference_upload`, `image_upload`, `ref_uploads[]`, `audio_upload`, `mesh_upload` |
| `POST /api/comfy/start\|stop\|free\|interrupt` | ComfyUI process control (`free` unloads its models); `stop`/`free` answer `409` while a ComfyUI job runs |
| `POST /api/tts\|music\|sfx\|anim/stop\|unload` | worker process control; `409` while that worker has a job |
| `GET /api/comfy/log`, `/api/tts\|music\|sfx\|anim/log` | process output tails |
| `GET /files/<root>/<path>` | the generated files (`assets`, `comfy-output`, `voc-output`, `mus-output`, `vid-output`, `characters`, `uploads`, ...) |

## Parameters

`GET /api/schema` is the source of truth (it is generated from `core/schema.py`, which the server validates
against). The short version:

| kind | required | the rest |
|---|---|---|
| `model3d` | `prompt` (or `reference_upload`) | `stage image\|model\|full`, `preset gameasset\|character\|detailed`, `shape hard\|round`, `edges sharp\|soft`, `normalmap`, `remesh`, `name`, `size`, `tris`, `model pixal3d\|trellis2`, `seed`, `resolution`, `texture`, `fbx`, `raw` |
| `texture` | `prompt` | `name`, `width`/`height` (256..2048), `tile native\|crossfade\|none`, `lora`, `seed`, `steps`, `band`, `denoise`, `raw`, `reference_upload` + `strength` |
| `sprite` | `prompt` | `name`, `image_upload` (image 1), `ref_uploads` (image 2, 3), `model distilled\|base`, `alpha auto\|keep\|none`, `width`+`height`, `seed`, `steps`, `cfg`, `negative`, `megapixels`, `bg`, `match_source`, `raw` |
| `voice` | `text`, `voice` | `mood`, `takes`, `seed`, `language`, `temperature`, `top_k`, `top_p`, `repetition_penalty`, `mood_timbre`, `model`, `speaker` |
| `voice_prep` | `name` + (`folder` or `mood` + `refs`) | `language`, `ref_seconds`, `max_ref_clips` |
| `music` | `caption` | `name`, `duration`, `loop`, `crossfade`, `takes`, `seed`, `format wav\|ogg\|flac`, `lyrics`, `instrumental`, `language`, `bpm`, `keyscale`, `timesignature`, `think`, `lm`, `steps`, `shift`, `lm_temperature`, `reference_upload` |
| `sfx` | `prompt` | `name`, `duration` (0.5-30), `takes`, `seed`, `format wav\|ogg\|flac`, `loop`, `crossfade`, `negative`, `trim`, `normalize`, `peak_db`, `highpass`, `steps`, `cfg`, `shift` |
| `video` | `shots[]` | `name`, `width`, `height`, `fps`, `transition`, `transition_kind`, `mode full\|keyframes\|assemble`, `only[]`, `audio_upload`, `negative`, `crf` (shot fields: see the schema or `ai-vid/README.md`) |
| `prop` | `prompt` | `name`, `seed`, `reference_upload` or `reference_url` (skip drawing), `raw`, `claude_model` (default `opus`), `effort`, `rounds`, `ipadapter`, `discard`, `resolution`, `size`, `fbx` |
| `paint` | `prompt`, `model_url` or `model_upload`, `picture_url` or `picture_upload` | `front`, `plain`, `lock`, `low`, `name`, `seed`, `ipadapter`, `discard`, `resolution`, `size`, `fbx` |
| `anim` | `source_url` or `mesh_upload` (rig); `clips[]` (anim) | `stage rig\|anim\|full`, `name`, `height`, `no_fingers`, `keep_pose`, `seconds`, `seed`, `steps`, `inplace` |

Outputs by kind (`job.outputs`):

- `model3d`: `glb`, `fbx`, `diffuse_fbx`, `normal_fbx`, `diffuse`, `normal`, `reference` → `{path, url}`
- `paint`: `glb`, `fbx`, `diffuse`, `reference` → `{path, url}`, `views: [{path, url}]`
- `prop`: as `paint`, plus `script` (Claude's Blender script) and `checks: [{path, url}]` (clay check per round); `meta.paint` holds Claude's paint settings and notes
- `texture`: `texture`, `preview2x2`; `sprite`: `sprite`, `raw`
- `voice`, `music`, `sfx`: `files: [{path, url, seed, duration, meta?}]` (one per take)
- `video`: `final`, `clips[]`, `keyframes[]`, `project`
- `anim`: `glb`, `fbx`, `diffuse`, `blend`, `clips: [{name, prompt, seconds, seed, bvh}]`
- `voice_prep`: `voice: {name, mood}`

## Examples

```bash
curl -s localhost:8765/api/health
curl -s -X POST localhost:8765/api/jobs -H "Content-Type: application/json" \
  -d "{\"kind\": \"music\", \"params\": {\"caption\": \"calm piano, quiet village at dusk, slow, no vocals\", \"duration\": 45, \"loop\": true, \"name\": \"VillageDusk\"}}"
curl -s "localhost:8765/api/jobs/<id>/wait?timeout=300"
```

With the CLI (standard library only, any Python 3.8+; `STUDIO_URL` overrides the address):

```bash
python studio/studio_cli.py health
python studio/studio_cli.py schema music
python studio/studio_cli.py submit music --caption "calm piano, quiet village at dusk, slow, no vocals" --duration 45 --loop true --name VillageDusk --wait
python studio/studio_cli.py submit model3d --prompt "a wooden barrel" --name Barrel --wait
python studio/studio_cli.py submit anim --source_url /files/assets/Hero.glb --name Hero --clips "[{\"name\": \"Idle\", \"prompt\": \"A person stands still, breathing calmly.\"}]" --wait
python studio/studio_cli.py jobs --status running
python studio/studio_cli.py wait <id>
```

`submit` without `--wait` returns at once with the job id; `wait` blocks (progress on stderr, result JSON on
stdout). Exit codes: 0 done, 1 the job failed or was cancelled, 2 the request was rejected or the studio is down.

## Memory and concurrency

- One worker thread: two jobs never share the GPU, however many are queued. The browser and the API share the
  same queue, so a job queued by Claude waits behind one started from a page and the other way round.
- Model hand-over is automatic and verified (see "The model" above). Before a voice, music or anim job loads,
  the studio asks ComfyUI to unload and then watches nvidia-smi until at least 5.5 GB is free; ComfyUI's `/free`
  does not reach models the 3D custom nodes keep in their own globals, so if the memory does not come back
  within ~12 s ComfyUI is stopped (the next ComfyUI job restarts it, ~30 s); an instance started outside the
  studio is found by its port and stopped the same way, after which the studio owns the restart. The other
  workers' unload is checked the same way: if their memory stays allocated, their processes are ended (the next
  request restarts them). Only if the memory still does not come back does the job fail with a clear message instead of running with its models
  pushed to the CPU (ACE-Step's VAE decode then takes 20+ minutes). The cost is load time: switching from a ComfyUI job to
  music costs ~40 s of model loading, back again ~20 s. Group jobs by kind when you can (all textures, then all
  voice lines) to avoid paying that repeatedly.
- Worker processes keep running (empty) between jobs and hold ~1–2 GB of CPU RAM each for the imported
  libraries; the animation worker's Llama-3 text encoder lives in CPU RAM (~16 GB) while loaded. With 64 GB RAM
  this is fine; `POST /api/<worker>/stop` ends a process if RAM is needed elsewhere.
- Control calls that would pull the GPU away from a running job (`comfy/free`, `comfy/stop`, `<worker>/unload`,
  `<worker>/stop`) answer `409` while that back-end has a job. Cancel the job first.
- Long polls (`/wait`) hold one server thread each; the server is threaded, dozens are fine.
