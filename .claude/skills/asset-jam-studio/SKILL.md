---
name: asset-jam-studio
description: Generate game assets with the local Asset Jam Studio (3D models, seamless textures, 2D sprites, voice lines, music, sound effects, videos, rigged + animated characters) through its HTTP API / CLI. Use whenever a task needs an art, audio or 3D asset that does not exist yet, or when the user mentions the studio, Asset Jam Studio, ai-3d, ai-voc, ai-mus, ai-vid or ai-anim.
---

# Asset Jam Studio as a tool

The studio is a local server (`<repo>\studio\server.py`, default `http://127.0.0.1:8765`) with one
job queue in front of the GPU generators. `<repo>` is the folder this toolkit was cloned to (the folder that
holds `studio\` and this `.claude\`); substitute the real path in every command below. You talk to it with the
CLI (standard library, any Python 3):

```
python <repo>\studio\studio_cli.py <command>
```

Every command prints JSON on stdout. `STUDIO_URL` changes the address. Full API: `<repo>\studio\API.md`.

## 1. Before the first job

```
python <repo>\studio\studio_cli.py health
```

- `{"error": "studio not reachable ..."}` → the studio is not running. Ask the user to start `studio\studio.bat`
  (it needs the ComfyUI venv). Do not try to start it yourself.
- `backends.comfy.running: false` → 3D / texture / sprite / video jobs will fail. Start it:
  `studio_cli.py comfy start`, then poll `health` until `running` is true (~20-40 s).
- `backends.<x>.installed: false` → that flow is not set up on this machine; tell the user instead of queuing.
- `running` / `queued` tell you what is already in flight (the browser UI shares the queue).

`studio_cli.py schema` lists every kind with its required and optional parameters; `schema <kind>` gives the
help text, defaults and choices of each parameter. Prefer that over guessing a parameter name.

## 2. Submitting and waiting

```
studio_cli.py submit <kind> --<param> <value> ... [--wait] [--timeout 3600]
studio_cli.py wait <job id>
studio_cli.py jobs [--status queued|running|done|error|cancelled]
studio_cli.py cancel <job id>
```

- Scalars are plain flags (`--prompt "a barrel" --width 512 --loop true`). Lists/objects take JSON
  (`--clips '[{"name": "Idle", "prompt": "..."}]'`), or give everything as `--params '{...}'` / `--params-file x.json`.
- Git Bash on Windows rewrites arguments that start with `/` into Windows paths, so `--source_url /files/...` arrives
  mangled ("pick a model to rig"). Prefix such commands with `MSYS_NO_PATHCONV=1`.
- **Jobs run one at a time, oldest first.** Queuing several is safe; they just take turns. The GPU hand-over is
  automatic and verified: a voice / music / sfx / anim job asks ComfyUI to unload, checks nvidia-smi, stops ComfyUI if
  it will not let go (the next ComfyUI job restarts it, ~30 s), ends the other workers' processes if their memory
  stays allocated, and errors out rather than run on a full GPU. So
  any order works, but every switch costs 20-40 s of model loading: **group jobs by kind** — textures, sprites
  and 3D models (all ComfyUI) first, then voice lines, then music, then sound effects.
- Two usable patterns:
  1. `submit ... --wait` for one asset you need before continuing (blocks; progress on stderr).
  2. `submit` several without `--wait` (each returns its id at once), do other work, then `wait <id>` for each.
     `wait` is a long poll: it returns as soon as the job finishes. Do not poll `status` in a loop.
- Exit code 0 = `done`. 1 = the job ended `error` or `cancelled`: the JSON has `error` and `log_tail`; read them,
  fix the parameters, resubmit. 2 = the request was rejected (parameter message in `error`) or the studio is down.
- Typical durations: texture 20-50 s, sprite 30 s-2 min, voice line 5-20 s (+20 s first load), music ~1 s per
  second of audio (+40 s first load), sound effect ~25 s per take (+20 s first load), 3D model 3 min (25 min for `detailed`), rig 10 s + 10-30 s per clip,
  video ~40 s per second of footage. Set `--timeout` accordingly for `--wait`.

## 3. Results

The final JSON has `outputs` with **absolute file paths** (`path`) and studio URLs (`url`). Use the paths: copy
the files into the game project (never move them; the studio libraries scan these folders). The seed used is
in `seed`: re-submitting with the same parameters and `--seed <n>` reproduces the asset.

| kind | outputs | where |
|---|---|---|
| `model3d` | `glb`, `fbx`, `diffuse_fbx` (PNG), `reference` | `ai-3d/assets/<Name>.glb` / `.fbx` / `Diffuse.png` |
| `texture` | `texture` | `ai-3d/assets/textures/<Name>.png` |
| `sprite` | `sprite` (RGBA PNG) | `ai-3d/assets/sprites/<Name>.png` |
| `voice` | `files[]` (one wav per take) | `ai-voc/output/<voice>_<mood>_<slug>.wav` |
| `music` | `files[]` (wav/ogg/flac, `meta.lm_bpm`) | `ai-mus/output/<Name>.wav` |
| `sfx` | `files[]` (one wav/ogg/flac per take, 48 kHz mono) | `ai-sfx/output/<Name>.wav` (takes: `<Name>_1.wav`, ...) |
| `video` | `final` (mp4), `clips[]`, `keyframes[]` | `ai-vid/output/<name>/` |
| `anim` | `glb` (with all clips), `fbx`, `diffuse`, `clips[]` | `ai-anim/assets/<Name>/` |

`studio_cli.py library models|textures|sprites|voice|music|sfx|videos|characters` lists what already exists (with
the parameters that made it): check it before generating something the user may already have.

## 4. Per kind: what to send

Give `--name` (PascalCase: `CobbleStreet`, `VillageDusk`) to every kind except `voice`, whose files are named from the voice, mood and text. Names that are taken get `_2`.

**texture** (walls, floors, ground, fabric; seamless PNG). ONE material per job: `--prompt "grey cobblestone
street, rounded stones in irregular rows, thin dark gaps, slightly mossy"`. Name the material, colour and
layout; never say "outlines" or "seams" as general words (it draws a grid). `--width/--height` 256..2048
(1024 default; use 512 for a quick look, 2048 for hero surfaces). `--tile native` (default) keeps plank/tile
grids aligned; `--tile crossfade` for flat walls. `--lora 0.8` only for grass, fabric, glazed tiles.
`--reference_upload <png> --strength 0.4` makes a variant of an existing texture.

**model3d** (props and characters). ONE object, not a scene: `--prompt "a wooden barrel with iron bands"`.
`--preset gameasset` (default: remesh 512, 3000 tris decimated then planar-dissolved to ~1200-2400, FBX + diffuse + normal map for Unity/three.js; add `--remesh 256` for props with perforated parts such as a mesh chair back or a grille, where 512 makes the decimator stall), `--preset character` (a person
or creature in a T-pose, 4000 tris, ready for `anim`), `--preset detailed` (hero model, 700k tris, GLB only,
25 min). The game-asset diffuse is the reference picture itself projected onto the faces the camera saw (`--texture_from picture`, default; `voxels` = the old soft bake), so logos, labels and colours come out exactly as in the picture: judge the picture strictly. `--shape round` for bottles, pots, balls. `--size 1.2` = longest side in metres. Workflow for anything
that matters: `--stage image` first (20 s, returns `outputs.reference`), look at the picture (Read the PNG),
then resubmit the same parameters with `--stage model --seed <seed from the first job>` (ComfyUI reuses the
cached picture). `--stage full` for one-shot props.

**sprite** (2D characters, cut out to RGBA). Text only: `--prompt "a small round robot with one glowing eye,
front view, idle pose"`. Edit / re-pose an existing sprite: `--image_upload <png>` (= image 1) and describe
the result: `"the character from image 1 sitting on the chair from image 2, same character, same outfit and
colours"`, with `--ref_uploads '["chair.png"]'`. `--model base` for final frames (exact colours, 2 min),
`distilled` (default) for iteration. Give `--width` and `--height` (multiples of 16) together when a pose needs
more room. Sprite sheets: one job per pose, same seed and prompt phrasing, then assemble with Pillow.

**voice** (dialogue). `studio_cli.py voices` → names and moods. `--voice liam --mood upset --text "I missed
the last train home... Oh! Hey, Sam."`. Full sentences; `...` pauses, `!` energy, `?` rising; the mood clip is
the biggest lever (a mood that does not exist for that voice is an error). `--takes 3` gives three seeds to
choose from. Several lines = several jobs (queue them all, then wait on each). Default `language English`.

**music**. `--caption "lo-fi hip hop, warm Rhodes piano and vinyl crackle, relaxed, slow, no vocals"`: genre,
instruments, mood, tempo words; say "no vocals" for instrumentals. `--duration 45 --loop true` for game loops
(30-90 s loop best; `loop` cross-fades the end into the start). `--lyrics "[Verse]\n..."` for sung tracks (then
set `--bpm`). `--format ogg` when the target is a web game. `--takes 2` for alternatives.

**sfx** (sound effects). `--prompt "a single pistol gunshot, sharp and loud, short echo in a concrete room"`:
what makes the sound, what it does, where ("close up", "far away", "small tiled room"); "a single ..." for one
hit, "several bursts" for a sequence. `--duration` 0.5-3 for one-shots, 3-6 for impacts with a tail, 10-30 for
ambiences (`--loop true`). `--takes 4`: effects vary a lot per seed, so make several and pick (listen, or
check `duration` in the outputs: a take trimmed far below the asked length is usually a weak one). Each take is
trimmed, faded and peak-normalized to -1 dBFS, 48 kHz mono (right for positional audio). Every sound costs the
same (~25 s per take, +20 s first load) whatever its length. `--negative "music, speech"` if a melody or voice creeps in.

**anim** (rig + clips). Source: a character GLB from the 3D library: `studio_cli.py library models` → use the
item's `glb` URL as `--source_url /files/assets/Hero.glb`, or a file path with `--mesh_upload`. Generate the
model with `--preset character` first. `--clips '[{"name": "Idle", "prompt": "A person stands still, breathing
calmly, shifting their weight."}, {"name": "Walk", "prompt": "A person walks forward at a relaxed pace.", "seconds": 4}]'`
Prompts start with "A person ..."; one action per sentence; 2-8 s per clip. Be explicit about the limb and how
far: "waves hello with their right hand" gave a timid chest-height wave, "raises their right arm high above
their head and waves hello enthusiastically" a proper one; seeds differ a lot, so generate 2-3 takes of a clip
(`"seed"` per clip) and keep the best. `--inplace true` for your own movement code. `--stage rig` first to check
the T-pose (returns the GLB), then `--stage anim --name Hero` adds clips. Needs the Hugging Face Llama-3 token
(`health` → `backends.anim.hf_token`); rigging works without it. Timing: ~30 s model
load, ~10 s per 4 s clip, ~15 s Blender export. Output GLB plays in three.js (`gltf.animations`), the FBX in
Unity (Mixamo bone names). Verified on a clean humanoid mesh; a Pixal3D-generated character with fused limbs
rigs worse (check the T-pose after `--stage rig`).

**video** (experimental, slow). `--shots '[...]'` as in `ai-vid/README.md`; `--mode keyframes` first to check
the stills, then `--mode full`. `--audio_upload <wav>` adds a track from `music`.

**voice_prep** (new actor voice from a folder of recordings) is interactive by nature (transcripts need
checking); point the user at the Voice page instead unless asked.

Uploads: a file that is not already under the repo's served folders goes in with
`studio_cli.py upload <file>` → use the returned `path` for any `*_upload` parameter (absolute paths inside the
repo also work directly).

## 5. Building a game with it

1. `health`; start ComfyUI if needed. `library ...` for reusable assets.
2. Plan the asset list, then queue by kind: textures and sprites and 3D models (ComfyUI), then voice lines,
   then music, then sound effects. Submit without `--wait`, keep the ids, keep coding, `wait` on each id when you need the file.
3. Copy `outputs.*.path` files into the game folder (e.g. `assets/textures/CobbleStreet.png`); load GLBs with
   three.js `GLTFLoader`, sprites as textures with alpha, music/voice/sound effects as `<audio>` or Web Audio buffers (ogg or wav).
4. Check results: Read a PNG to look at it; for a GLB trust `outputs` plus the triangle count in the job log.
   A bad result: change the prompt or the seed and resubmit; never loop blindly more than two or three times.
5. If a job errors with "ComfyUI is not running" → `comfy start`, wait, resubmit. "venv not found" → that flow
   is not installed; tell the user. "Invalid argument" from a ComfyUI node → ComfyUI's console is dead (it was
   started by a studio that has since exited): ask the user to restart ComfyUI from the studio header.

Never call `comfy stop|free` or `<worker> stop|unload` while a job is running (the server answers 409 anyway);
never delete or move files inside the asset folders; never start a second studio or ComfyUI instance.
