# ai-vid: logo intros and short videos from a shot list (local, RTX 2080 / 8 GB)

Shot list → **Wan 2.2 TI2V-5B** clips (text-to-video, image-to-video, or pinned between a first and a last
frame) → logo reveal rendered from the real PNG → ffmpeg cross-fades + music → one mp4. Runs in the
ComfyUI that `ai-3d/` installs (same venv, same models folder), fp16 on the 2080, weights you may ship.

| Stage | Model / tool | License | Notes |
|---|---|---|---|
| Still keyframes | FLUX.2 [klein] 4B via `ai-3d/scripts/sprite.py` (raw prompt, opaque) | Apache-2.0 | the logo goes in as "image 1" so its artwork stays on-model |
| Keyframe → clip | **Wan 2.2 TI2V-5B** (fp16) + umT5-XXL (fp8) + Wan 2.2 VAE | Apache-2.0 | 20 steps, CFG 5, uni_pc; 4k+1 frames at 24 fps |
| First + last frame | `custom_nodes/aivid_wan` (our node) | MIT | pins the end frame by latent in-painting; the built-in node only takes a start frame |
| Logo reveal | `scripts/logo_reveal.py` (Pillow) | — | characters pop in with an overshoot, wordmark jumps out; pixel-exact |
| Assembly | ffmpeg `xfade` + optional audio (`scripts/assemble.py`) | — | ffmpeg from PATH or `C:\ffmpeg\bin` |

Why this model: Wan 2.2 5B is the only current open video model that is Apache-2.0 without a revenue cap,
runs in 8 GB with ComfyUI's offloading, and lists fp16 as supported (Turing has no bf16). Rejected:
**LTX-2.3 / 2.5** (22B + Gemma 12B encoder, 16 GB floor even quantised, bf16-only in ComfyUI, community license
capped at US$10M revenue), **HunyuanVideo 1.5** (EU-excluded license), **Wan 2.2 14B / FLF2V-14B** (~16 GB at
Q4), **CogVideoX-5B** (custom license), **Wan 2.5–2.7** (API only, no weights).

Why the hybrid: a video model will not reproduce exact artwork or typography. So the organic shots (droplet,
ripples, reflection, camera tilt) are generated, each one lands on a keyframe drawn from the logo, and the
final reveal animates the actual logo file.

Measured on the 2080 (832×480, 20 steps): **~40 s of sampling per second of video** (2 s clip in 85 s including
the first model load), ~1.3 GB VRAM at rest, the 5B model streams from RAM. A keyframe with the base klein
model takes ~2 min. A four-shot intro (3 keyframes + 9 s of video + reveal) takes about 12 minutes.

## Setup

```bash
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Needs `ai-3d/setup.ps1` done first (ComfyUI + venv) and ffmpeg. Copies `custom_nodes/aivid_wan` and the
workflow into ComfyUI and downloads ~18 GB into `ai-3d/ComfyUI/models/`. Restart ComfyUI afterwards so it
loads the node. `-SkipModels` skips the download.

## Usage

```bash
intro.bat shots\example_intro.json                     every shot, then output\example_intro\example_intro.mp4
intro.bat shots\example_intro.json --keyframes-only    draw the still keyframes first and look at them
intro.bat shots\example_intro.json --only reflection    redo one shot (its keyframes and the other clips are reused), re-assemble
intro.bat shots\example_intro.json --assemble-only     only re-join the clips (new transitions / audio)

shot.bat "a black droplet falls into still water, ripples spread" --seconds 3 --name Drop
shot.bat "the ripples settle and the emblem appears" --image Drop_last.png --end emblem.png --name Reflection
logo_reveal.bat assets\logo.png --out reveal.mp4 --split 0.69
assemble.bat intro.mp4 a.mp4 b.mp4 c.mp4 --transition 0.3 --audio ..\ai-mus\output\sting.wav
```

The ComfyUI server must be running (`ai-3d\start_comfyui.bat`) for `intro.bat` and `shot.bat`. The studio
(`studio\studio.bat`, Video tab) edits the same shot lists, runs them with live progress and regenerates
single shots.

### Shot list

```json
{
 "name": "example_intro", "width": 832, "height": 480, "fps": 24, "transition": 0, "audio": null,
 "shots": [
  {"id": "drop", "prompt": "A single black ink droplet falls ...", "seconds": 3, "seed": 1,
   "start": {"generate": "A still pool of pale water, a black droplet hanging above ...", "model": "base", "seed": 11},
   "end": null},
  {"id": "reflection", "prompt": "The ripples settle and the emblem's reflection appears ...", "seconds": 3,
   "start": "previous",
   "end": {"generate": "The emblem from image 1 reflected in rippling water ...", "refs": ["assets/logo.png"]}},
  {"id": "reveal", "type": "logo", "logo": "assets/logo.png", "seconds": 3, "split": 0.69,
   "background": "#ffffff", "from": "previous"}
 ]
}
```

- `start` / `end`: `null` (free), `"previous"` (the previous shot's exact last frame), `{"image": path}`, or
  `{"generate": prompt, "refs": [...], "model": "base"|"distilled", "seed": n}` (klein; `refs[0]` is "image 1").
- `seconds` is rounded to 4k+1 frames; `seed`, `steps`, `cfg`, `shift`, `negative` per shot are optional.
- Shots that start on `"previous"` are continuous: the clip begins on the previous shot's last frame, that duplicate
  frame is dropped, and the cut is hard. `transition` (seconds, cross-fade *into* a shot) only applies to shots that
  do not chain; the top-level value is the default (0). `transition_kind` picks the ffmpeg xfade (`fade`, `dissolve`, ...).
- Logo shots: `split` is the row (fraction of the height) where the wordmark starts, `scale` the logo width as a
  fraction of the frame, `from` = `"previous"` fades up from the last frame of the shot before.
- `audio`: a wav/mp3 (e.g. from `ai-mus/`), cut to the video with a fade-out.

Relative paths resolve against the shot-list file, then `ai-vid/`. Everything lands in `output/<name>/`:
`key_<shot>_start|end.png`, `<shot>.mp4`, `<shot>_first|last.png`, `shots.json` (with the seeds that were used)
and `<name>.mp4`.

## Layout

```
setup.ps1                     installs the node + workflow into ai-3d/ComfyUI and downloads the Wan 2.2 weights
intro.bat shots.json [...]    shot list -> clips -> assembled mp4 (scripts/intro.py)
shot.bat "prompt" [...]       one Wan 2.2 clip (scripts/shot.py)
logo_reveal.bat logo.png      logo reveal clip from a PNG (scripts/logo_reveal.py)
assemble.bat out.mp4 a b c    join clips with cross-fades and audio (scripts/assemble.py)
scripts/build_video_workflow.py   regenerates workflows/wan_i2v_api.json (+ the UI workflow when ComfyUI is up)
scripts/download_models.py
custom_nodes/aivid_wan/       AIVidWan22FirstLastFrameToVideoLatent (installed into ComfyUI by setup.ps1)
workflows/wan_i2v_api.json    API graph used by shot.py;  wan_i2v.json: the same graph for ComfyUI's sidebar
shots/example_intro.json      example intro: droplet -> ripples -> emblem reflection -> logo reveal
assets/logo.png               your logo (not in git): the reveal animates it and the keyframes reference it
output/                       projects (ignored)
```

## Tips

- Check the keyframes first (`--keyframes-only` / the studio button): the clip can only be as on-model as the
  picture it lands on. The base klein model keeps the logo's colours; the distilled one drifts.
- Prompts that work for Wan: say what moves and how the camera moves, name the style ("flat 2D cartoon with
  thick black ink outlines"), add "static camera" when the camera must not drift, "no text" always.
- Pinning both frames of a 3 s clip gives Wan little room; if the motion looks forced, give it 4–5 s or drop the
  end frame and let the next shot start from `"previous"`.
- 720p (1280×704) roughly quadruples the time and may not fit; stay at 480p and upscale in the editor.
