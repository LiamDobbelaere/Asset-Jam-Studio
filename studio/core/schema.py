"""What a job of each kind takes: the parameter schema shared by the web form, the HTTP API, the CLI and the Claude skill.

One table per kind. The server validates `POST /api/jobs` against it (400 with a message instead of a job that
dies in the worker), serves it as `GET /api/schema`, and `studio_cli.py` builds its command-line flags from it.
Keep this in step with the flows in flows.py: every parameter a flow reads is listed here.
"""
import os
import re

# --- parameter descriptors ----------------------------------------------------------------------

def P(type, help="", default=None, choices=None, required=False, min=None, max=None, items=None, ui=False):
    """type: str | int | float | bool | path | list | object. `path` is an absolute file path on this machine
    (what /api/upload returns). `ui` marks fields only the browser sends (display URLs); they are accepted but not
    offered on the command line."""
    d = {"type": type, "help": help}
    if default is not None:
        d["default"] = default
    if choices:
        d["choices"] = list(choices)
    if required:
        d["required"] = True
    if min is not None:
        d["min"] = min
    if max is not None:
        d["max"] = max
    if items:
        d["items"] = items
    if ui:
        d["ui"] = True
    return d


SEED = P("int", "Same inputs + same seed = same result. Empty = random; the seed used is written back into the job params.")
NAME = lambda example: P("str", f"Output file name without extension (default: made from the prompt, e.g. {example}). "  # noqa: E731
                                "A suffix _2, _3 ... is added when the name is taken.")

SHOT = {
    "id": P("str", "Shot id, becomes the clip file name (letters, digits, _ -)", required=True),
    "type": P("str", "video: a Wan 2.2 clip; logo: animate a PNG logo", default="video", choices=["video", "logo"]),
    "prompt": P("str", "video shots: subject, motion, camera, style ('flat 2D cartoon with thick black ink outlines, static camera')"),
    "seconds": P("float", "Clip length; rounded to 4k+1 frames at 24 fps", default=3, min=1, max=8),
    "seed": SEED,
    "start": P("object", "null (free) | \"previous\" (the previous shot's exact last frame, hard cut) | {\"image\": path} | "
                         "{\"generate\": prompt, \"refs\": [path], \"model\": \"base\"|\"distilled\", \"seed\": n} (FLUX.2 klein keyframe)"),
    "end": P("object", "Same forms as start, for the last frame"),
    "transition": P("float", "Cross-fade into this shot in seconds (ignored when start is \"previous\")"),
    "steps": P("int", "Wan sampling steps (default from the workflow)"),
    "cfg": P("float", "Wan CFG (default from the workflow)"),
    "logo": P("path", "logo shots: transparent PNG"),
    "split": P("float", "logo shots: row (0-1 of the height) where the wordmark starts", default=0.69),
    "background": P("str", "logo shots: background colour", default="#ffffff"),
    "from": P("str", "logo shots: \"previous\" fades up from the previous shot's last frame, \"\" from the background", default="previous"),
    "scale": P("float", "logo shots: logo width as a fraction of the frame", default=0.8),
}

CLIP = {
    "name": P("str", "Clip name: becomes the glTF animation and the FBX take (Idle, Walk, Run ...)", required=True),
    "prompt": P("str", "What the person does, Kimodo style: 'A person walks forward at a relaxed, steady pace.' "
                       "Several sentences separated by ' | ' play in sequence inside one clip.", required=True),
    "seconds": P("float", "Clip length per sentence; 2-8 s works best", default=4, min=1, max=20),
    "seed": SEED,
}

# --- the kinds -------------------------------------------------------------------------------

KINDS = {
    "model3d": {
        "label": "3D model", "page": "model3d", "backend": "comfy",
        "summary": "Text (or your own picture) -> low-poly textured 3D prop or character: GLB, plus Unity FBX + diffuse PNG for the "
                   "gameasset/character presets. Z-Image-Turbo draws a reference image, Pixal3D (or TRELLIS.2) builds the mesh.",
        "time": "reference image ~20 s; full run ~3 min (gameasset/character), ~25 min (detailed)",
        "outputs": {"glb": "the model (ai-3d/assets/<name>.glb)", "fbx": "Unity FBX (gameasset/character)", "diffuse_fbx": "1024 diffuse PNG for the FBX",
                    "diffuse": "diffuse PNG from ComfyUI", "reference": "the reference image that was generated"},
        "params": {
            "prompt": P("str", "ONE object (or one character), not a scene. The preset appends its style hints (low-poly, hand-painted, plain background).", required=True),
            "stage": P("str", "image: only draw the reference image (check it first); model: build the 3D model from the image of the given seed "
                              "(ComfyUI serves the cached picture); full: both in one job", default="full", choices=["image", "model", "full"]),
            "preset": P("str", "gameasset: decimated to 3000 tris then planar-dissolved to ~1200-2200, one 1024 diffuse + normal map, FBX. character: a person/creature in a front-view T-pose, 4000 tris, "
                               "ready for the Animate flow. detailed: ~700k tris, 4K PBR maps, GLB only.", default="gameasset", choices=["gameasset", "character", "detailed"]),
            "shape": P("str", "hard: crease angle 60 deg, remesh 512, 3000 tris then 10 deg dissolve (furniture, boxes, machines). round: fully smooth shading, remesh 384, 1500 tris, no dissolve (bottles, pots, balls)",
                       default="hard", choices=["hard", "round"]),
            "edges": P("str", "sharp: corners and creases survive the remesh (QEF vertices, 2 smoothing passes) and the decimation "
                              "(QEM-optimal placement, feature edges pinned). soft: the old pipeline (20 smoothing passes, midpoint "
                              "collapses), which rounds everything off like melted wax", default="sharp", choices=["sharp", "soft"]),
            "normalmap": P("bool", "Bake a tangent-space normal map from the remeshed high-poly into the low-poly UVs and put it in the "
                                   "GLB (and a <name>Normal.png next to the FBX): bevels, panel lines and small details shade as if "
                                   "they were modelled (gameasset/character)", default=True),
            "normal_from": P("str", "Which high-poly the normal map is baked from: remesh (the remeshed shell, smooth) or raw (the "
                                    "shape VAE's own mesh: finest detail, but its voxel staircase shows as faint stripes on flat areas)",
                             default="remesh", choices=["remesh", "raw"]),
            "dissolve": P("float", "After decimation, merge coplanar triangles within this many degrees in Blender (inside UV islands, "
                                   "textures stay valid) and re-triangulate; 0 = off. Lets flat panels give their triangles back so the "
                                   "budget goes to small parts (needs fbx). Default 10 (hard), 0 (round)", min=0.0, max=30.0),
            "remesh": P("int", "Remesh voxel resolution before decimation (default 512, round 384). 512 keeps taps, ribs and collars "
                               "separate where 256 melts them; use 256 on perforated parts such as a mesh chair back, where 512 resolves "
                               "the holes and the decimator stalls on them", min=64, max=1024),
            "blur": P("int", "Blur radius on the diffuse texture in px (default 0 for hard, 2 for round: the painted look)", min=0, max=8),
            "ao": P("float", "How much the baked ambient occlusion darkens the diffuse, 0..1 (default 0.3 for hard, 0.45 for round)", min=0.0, max=1.0),
            "name": NAME("DeskChair"),
            "size": P("float", "Longest dimension in metres for the FBX", default=1.0, min=0.05),
            "tris": P("int", "Triangle budget of the decimation (gameasset/character; default 3000, round 1500, character 4000). The planar "
                             "dissolve then brings flat props back down (a box lands near 1200, a chair near 2100)", min=100),
            "model": P("str", "Image-to-3D model", default="pixal3d", choices=["pixal3d", "trellis2"]),
            "seed": SEED,
            "resolution": P("int", "Shape voxel resolution 1024..2048 (preset default)"),
            "texture": P("int", "Texture atlas size (preset default)"),
            "fbx": P("bool", "Convert to FBX + diffuse PNG with Blender (gameasset/character)", default=True),
            "level": P("bool", "Set the mesh flat on the ground before export (undoes the reference camera's tilt and turn; "
                               "needs fbx). Off for things that should stay tilted.", default=True),
            "raw": P("bool", "Use the prompt verbatim, no style hints", default=False),
            "reference_upload": P("path", "Your own reference picture (PNG): skips text-to-image, Pixal3D works straight from it. Only with stage full."),
            "reference_url": P("str", "", ui=True),
        },
    },
    "texture": {
        "label": "Texture", "page": "texture", "backend": "comfy",
        "summary": "Text -> seamless hand-painted world texture (walls, floors, ground, fabric) as PNG, Z-Image-Turbo with native latent-roll tiling.",
        "time": "~20 s for 1024x1024, ~50 s for 1024x2048",
        "outputs": {"texture": "the PNG (ai-3d/assets/textures/<name>.png)", "preview2x2": "a 2x2 repeat preview PNG"},
        "params": {
            "prompt": P("str", "ONE material: name it, its colour and its layout ('dark oak parquet floor, long horizontal planks in staggered rows with thin dark seams'). "
                               "Avoid 'outlines'/'seams' as general words (draws a grid).", required=True),
            "name": NAME("ParquetFloorDark"),
            "width": P("int", "Width in px, multiple of 64", default=1024, choices=[256, 512, 768, 1024, 1536, 2048]),
            "height": P("int", "Height in px, multiple of 64", default=1024, choices=[256, 512, 768, 1024, 1536, 2048]),
            "tile": P("str", "native: seamless by construction (plank rows and tile grids line up). crossfade: for flat walls, no faint roll lines. none: plain picture",
                      default="native", choices=["native", "crossfade", "none"]),
            "lora": P("float", "Hand-painted LoRA strength 0..1. Off by default; 0.8 helps grass, fabric, glazed tiles; hurts plain walls and parquet layout",
                      default=0.0, min=0, max=1),
            "seed": SEED,
            "steps": P("int", "Sampling steps (default 14 native, 8 crossfade)"),
            "band": P("int", "crossfade: seam strip width in px", default=192),
            "denoise": P("float", "crossfade: masked repaint of the seam band 0.3..0.5 (0 = cross-fade only)", default=0.0, min=0, max=1),
            "raw": P("bool", "Use the prompt verbatim, no style hints", default=False),
            "reference_upload": P("path", "Start from this picture instead of noise (img2img), e.g. an earlier texture"),
            "strength": P("float", "With reference_upload: how far to move away from it; 0.3 keeps the layout, 0.8 keeps only the palette", default=0.6, min=0.05, max=1),
            "reference_url": P("str", "", ui=True),
        },
    },
    "sprite": {
        "label": "Sprite", "page": "sprite", "backend": "comfy",
        "summary": "FLUX.2 klein: draw a 2D character sprite from text, or edit / re-pose a source sprite with up to two reference pictures; "
                   "the result is cut out (BiRefNet) to a transparent PNG.",
        "time": "~30 s (distilled), ~2 min (base)",
        "outputs": {"sprite": "the RGBA PNG (ai-3d/assets/sprites/<name>.png)", "raw": "the picture before the cut-out"},
        "params": {
            "prompt": P("str", "What the finished sprite shows and what must stay ('same character, same outfit and colours'). "
                               "Refer to the inputs as image 1 (source), image 2, image 3 (references).", required=True),
            "name": NAME("HeroSitFront"),
            "image_upload": P("path", "Source sprite = image 1 (PNG). Omit to draw from text only."),
            "ref_uploads": P("list", "Up to two reference pictures = image 2, image 3 (need a source sprite)", items="path"),
            "model": P("str", "distilled: 4 steps, fast, colours may drift. base: 20 steps with CFG, keeps exact colours, follows long prompts",
                       default="distilled", choices=["distilled", "base"]),
            "alpha": P("str", "auto: cut out with BiRefNet. keep: keep the source alpha (exact for shading/colour passes). none: opaque",
                       default="auto", choices=["auto", "keep", "none"]),
            "width": P("int", "Canvas width, multiple of 16 (default: source size or 768). Give both or neither."),
            "height": P("int", "Canvas height, multiple of 16 (default: source size or 1024)"),
            "seed": SEED,
            "steps": P("int", "Sampling steps (default 4 distilled / 20 base)"),
            "cfg": P("float", "CFG, base model only (default 5)"),
            "negative": P("str", "Negative prompt, base model only", default="blurry, photo, realistic, 3d render, extra limbs, text, watermark, drop shadow on the ground, cropped"),
            "megapixels": P("float", "Working resolution for the inputs", default=1.0, min=0.25, max=2),
            "bg": P("str", "Colour the transparent inputs are flattened on", default="#ffffff"),
            "match_source": P("bool", "Resize the result back to the source sprite's pixel size", default=False),
            "raw": P("bool", "Use the prompt verbatim, no style hints", default=False),
            "image_url": P("str", "", ui=True), "ref_urls": P("list", "", items="str", ui=True),
        },
    },
    "voice": {
        "label": "Voice line", "page": "voice", "backend": "voice",
        "summary": "Text -> a spoken line in one of the prepared actors' voices (Qwen3-TTS zero-shot clone), optionally in a mood. "
                   "GET /api/voices lists the voices and their moods.",
        "time": "~3 s of compute per second of audio, plus ~20 s model load on the first line",
        "outputs": {"files": "list of {path, url, seed, duration} wavs, one per take (ai-voc/output/<voice>_<mood>_<slug>.wav)"},
        "params": {
            "text": P("str", "The line, as full sentences. '...' pauses, '!' energy, '?' rising; the mood clip is the biggest lever.", required=True),
            "voice": P("str", "A prepared voice name (folder in ai-voc/voices)", required=True),
            "mood": P("str", "A mood of that voice (ref_<mood>.wav), e.g. upset, happy; empty = neutral"),
            "takes": P("int", "Takes with consecutive seeds; keep the best", default=1, min=1, max=8),
            "seed": SEED,
            "language": P("str", "", default="English", choices=["English", "German", "French", "Spanish", "Italian", "Portuguese", "Russian", "Japanese", "Korean", "Chinese", "Auto"]),
            "temperature": P("float", "Lower = steadier, flatter read (default 0.9)", min=0.1, max=1.5),
            "top_k": P("int", "(default 50)"), "top_p": P("float", "(default 1.0)"), "repetition_penalty": P("float", "(default 1.05)"),
            "mood_timbre": P("bool", "Take the timbre from the mood clip too (default: timbre from ref.wav, delivery from the mood clip)", default=False),
            "model": P("path", "A fine-tuned checkpoint folder (ai-voc/output/<voice>-ft*) instead of zero-shot cloning"),
            "speaker": P("str", "With a fine-tuned model: the trained speaker name"),
        },
    },
    "voice_prep": {
        "label": "Voice prep", "page": "voice", "backend": "voice",
        "summary": "Prepare a new voice from a folder of an actor's audio files (Whisper transcribes, ref.wav is cut), or add a mood reference to an existing voice from chosen clips.",
        "time": "~1 s per clip (Whisper on the GPU)",
        "outputs": {"voice": "{name, mood}"},
        "params": {
            "name": P("str", "Voice name: lowercase letters, digits, _", required=True),
            "folder": P("path", "Folder with one audio file per line (new voice)"),
            "mood": P("str", "Add this mood to an existing voice instead (lowercase letters/digits); needs refs"),
            "refs": P("list", "Clip numbers / file-name substrings to build the reference from (required for a mood; optional for a new voice)", items="str"),
            "language": P("str", "Clip language code for Whisper", default="en"),
            "ref_seconds": P("float", "Target reference length in seconds (new voice)", default=12.0),
            "max_ref_clips": P("int", "Clips joined into the reference (new voice)", default=3),
        },
    },
    "music": {
        "label": "Music", "page": "music", "backend": "music",
        "summary": "Caption -> music (ACE-Step 1.5): instrumental loops or sung tracks with lyrics, 48 kHz stereo wav/ogg/flac.",
        "time": "~1 s of compute per second of music, plus ~40 s model load on the first track",
        "outputs": {"files": "list of {path, url, seed, duration, meta: {lm_bpm, lm_keyscale, ...}} (ai-mus/output/<name>.<format>)"},
        "params": {
            "caption": P("str", "Genre, instruments, mood, tempo words: 'lo-fi hip hop, warm Rhodes piano and vinyl crackle, relaxed, slow, no vocals' (max 512 chars)", required=True),
            "name": NAME("VillageDusk"),
            "duration": P("float", "Seconds, 10-600; loops 30-90", default=60, min=10, max=600),
            "loop": P("bool", "Seamless loop: the end is cross-faded into the start and cut off", default=True),
            "crossfade": P("float", "Loop crossfade in seconds", default=2.0, min=0.5, max=8),
            "takes": P("int", "Takes with consecutive seeds", default=1, min=1, max=8),
            "seed": SEED,
            "format": P("str", "", default="wav", choices=["wav", "ogg", "flac"]),
            "lyrics": P("str", "Lyrics with [Verse] / [Chorus] / [Bridge] tags; empty = instrumental. Name the voice in the caption ('playful female vocal')."),
            "instrumental": P("bool", "Force instrumental (default: true when lyrics are empty)"),
            "language": P("str", "Vocal language code", default="en", choices=["en", "de", "fr", "nl", "es", "it", "pt", "ja", "ko", "zh"]),
            "bpm": P("int", "Pin the tempo (recommended for sung tracks)", min=30, max=300),
            "keyscale": P("str", "'A minor', 'F# major'"),
            "timesignature": P("str", "2, 3, 4 or 6", choices=["", "2", "3", "4", "6"]),
            "think": P("bool", "Plan bpm/key/structure with the 0.6B LM before the DiT (false = faster, flatter)", default=True),
            "lm": P("str", "Planner LM folder in ai-mus/models ('' = none)", default="acestep-5Hz-lm-0.6B"),
            "steps": P("int", "Turbo DiT steps", default=8, min=1, max=20),
            "shift": P("float", "", default=3.0),
            "lm_temperature": P("float", "", default=0.85, min=0.1, max=2),
            "reference_upload": P("path", "Reference track (wav/mp3) for style/timbre transfer"),
            "reference_url": P("str", "", ui=True),
        },
    },
    "sfx": {
        "label": "Sound effect", "page": "sfx", "backend": "sfx",
        "summary": "Prompt -> sound effect (MOSS-SoundEffect v2.0): explosions, gunshots, footsteps, UI blips, ambience loops; 48 kHz mono wav/ogg/flac, up to 30 s.",
        "time": "~25 s per take whatever its length, plus ~20 s model load on the first one",
        "outputs": {"files": "list of {path, url, seed, duration, seconds} (ai-sfx/output/<name>.<format>)"},
        "params": {
            "prompt": P("str", "Source + action + space: 'a single pistol gunshot in a small concrete room', 'a huge explosion with debris falling, distant'", required=True),
            "name": NAME("PistolShot"),
            "duration": P("float", "Seconds, 0.5-30; one-shots 0.5-3, impacts with a tail 3-6, ambiences 10-30", default=4, min=0.5, max=30),
            "takes": P("int", "Takes with consecutive seeds; effects vary a lot per seed, so 3-4 is a good habit", default=1, min=1, max=8),
            "seed": SEED,
            "format": P("str", "", default="wav", choices=["wav", "ogg", "flac"]),
            "loop": P("bool", "Seamless loop for ambiences: the end is cross-faded into the start and cut off", default=False),
            "crossfade": P("float", "Loop crossfade in seconds", default=1.0, min=0.1, max=5),
            "negative": P("str", "What to steer away from: 'music, speech, voices'"),
            "trim": P("bool", "Cut leading/trailing silence (below -50 dBFS)", default=True),
            "normalize": P("bool", "Peak-normalize to peak_db", default=True),
            "peak_db": P("float", "Peak level in dBFS when normalizing", default=-1.0, min=-24, max=0),
            "highpass": P("float", "Low-cut in Hz against the sub-bass drift the decoder leaves in (0 = off)", default=30.0, min=0, max=500),
            "steps": P("int", "Flow-matching steps (upstream uses 100; 50 sounds the same and takes half the time)", default=50, min=10, max=200),
            "cfg": P("float", "Guidance: higher follows the words more literally", default=4.0, min=1, max=10),
            "shift": P("float", "", default=5.0),
        },
    },
    "video": {
        "label": "Video", "page": "video", "backend": "comfy",
        "summary": "Shot list -> mp4: Wan 2.2 clips chained through keyframes (FLUX.2 klein), optional logo reveal, ffmpeg transitions + music. Experimental.",
        "time": "~40 s of sampling per second of 480p video, ~2 min per generated keyframe",
        "outputs": {"final": "the mp4 (ai-vid/output/<name>/<name>.mp4)", "clips": "list of {id, path, url, first, last, seconds, seed}", "keyframes": "list of {shot, which, path, url}", "project": "{path}"},
        "params": {
            "name": P("str", "Project folder name under ai-vid/output; re-running with the same name reuses existing keyframes and clips", default="intro"),
            "shots": P("list", "The shot list, in order", items=SHOT, required=True),
            "width": P("int", "", default=832), "height": P("int", "", default=480),
            "fps": P("int", "", default=24, min=8, max=60),
            "transition": P("float", "Default cross-fade in seconds for shots that do not chain", default=0.0, min=0, max=3),
            "transition_kind": P("str", "", default="fade", choices=["fade", "dissolve", "fadeblack", "fadewhite", "wipeup", "slideup", "circleopen", "pixelize"]),
            "mode": P("str", "full: generate everything and assemble. keyframes: only draw the keyframes (check them first). assemble: join the existing clips (new transitions / music)",
                      default="full", choices=["full", "keyframes", "assemble"]),
            "only": P("list", "Regenerate only these shot ids (the others are reused from the project folder)", items="str"),
            "audio_upload": P("path", "Music or sting (wav/mp3), cut to the video with a fade-out; e.g. a track from the music flow"),
            "negative": P("str", "Negative prompt for Wan (default: the Wan negative)"),
            "crf": P("int", "x264 quality", default=16),
            "audio_url": P("str", "", ui=True),
        },
    },
    "anim": {
        "label": "Character", "page": "anim", "backend": "anim",
        "summary": "Humanoid GLB -> rigged character (Make-It-Animatable, Mixamo skeleton) + animation clips from text (Kimodo): GLB with glTF animations, Unity FBX with one take per clip, BVH per clip.",
        "time": "rig ~10 s; each clip ~10-30 s after the first load of the Llama-3 text encoder (a minute or two, 16 GB RAM)",
        "outputs": {"glb": "ai-anim/assets/<Name>/<Name>.glb with every clip", "fbx": "Unity FBX", "diffuse": "diffuse PNG", "blend": "the Blender file", "clips": "list of {name, prompt, seconds, seed, bvh}"},
        "params": {
            "stage": P("str", "rig: rig only (check the T-pose). anim: add clips to an already rigged character (by name). full: both", default="full", choices=["rig", "anim", "full"]),
            "name": P("str", "Character name = folder under ai-anim/assets (default: from the source file)"),
            "source_url": P("str", "A library GLB as its /files/... URL (from GET /api/library/models: the 'glb' field)"),
            "mesh_upload": P("path", "Or a GLB/FBX/OBJ file path on this machine (e.g. an ai-3d/assets/<Name>.glb, or /api/upload)"),
            "clips": P("list", "Animation clips to generate", items=CLIP),
            "height": P("float", "Character height in metres", default=1.8, min=0.2),
            "no_fingers": P("bool", "Hands only, no finger bones (mitten-like low-poly hands)", default=False),
            "keep_pose": P("bool", "Keep the input pose as rest pose (default: move into the Mixamo T-pose)", default=False),
            "seconds": P("float", "Default clip length", default=4.0, min=1, max=20),
            "seed": SEED,
            "steps": P("int", "Kimodo denoising steps (50 is ~2x faster)", default=100, min=10, max=200),
            "inplace": P("bool", "Remove the horizontal root motion (animate on the spot)", default=False),
            "source": P("path", "", ui=True), "source_name": P("str", "", ui=True), "mesh_url": P("str", "", ui=True),
        },
    },
}

STATUSES = {
    "queued": "waiting for the worker (jobs run one at a time, oldest first)",
    "running": "the worker is on it; progress.pct / phase / detail update live",
    "done": "finished; outputs holds the files",
    "error": "failed; error holds the message",
    "cancelled": "cancelled by a client or interrupted",
}
TERMINAL = ("done", "error", "cancelled")


# --- validation ---------------------------------------------------------------------------------

class ParamError(ValueError):
    pass


def _coerce(name, spec, v):
    t = spec["type"]
    if v is None or v == "":
        return None if t != "str" else v
    if t == "bool":
        if isinstance(v, str):
            return v.lower() in ("1", "true", "yes", "on")
        return bool(v)
    if t in ("int", "float"):
        try:
            n = float(v)
        except (TypeError, ValueError):
            raise ParamError(f"{name}: expected a number, got {v!r}")
        if t == "int":
            if n != int(n):
                raise ParamError(f"{name}: expected a whole number, got {v!r}")
            n = int(n)
        if spec.get("min") is not None and n < spec["min"]:
            raise ParamError(f"{name}: must be >= {spec['min']}")
        if spec.get("max") is not None and n > spec["max"]:
            raise ParamError(f"{name}: must be <= {spec['max']}")
        if spec.get("choices") and n not in spec["choices"]:
            raise ParamError(f"{name}: must be one of {spec['choices']}")
        return n
    if t == "str":
        s = str(v)
        if spec.get("choices") and s not in spec["choices"]:
            raise ParamError(f"{name}: must be one of {spec['choices']}")
        return s
    if t == "path":
        s = str(v)
        if not os.path.exists(s):
            raise ParamError(f"{name}: file not found: {s}")
        return s
    if t == "list":
        if not isinstance(v, (list, tuple)):
            raise ParamError(f"{name}: expected a list")
        items = spec.get("items")
        if isinstance(items, dict):
            return [validate_fields(items, it, f"{name}[{i}]") if isinstance(it, dict) else it for i, it in enumerate(v)]
        if isinstance(items, str):
            return [_coerce(f"{name}[{i}]", {"type": items}, it) for i, it in enumerate(v)]
        return list(v)
    return v


def validate_fields(fields, params, where=""):
    """Coerce and check `params` against a field table. Returns a new dict; unknown keys are kept as they are."""
    if not isinstance(params, dict):
        raise ParamError(f"{where or 'params'}: expected an object")
    out = dict(params)
    for name, spec in fields.items():
        v = params.get(name)
        if v is None or v == "" or (isinstance(v, str) and not v.strip() and spec["type"] != "str"):
            if spec.get("required"):
                raise ParamError(f"{(where + '.') if where else ''}{name} is required")
            continue
        out[name] = _coerce((where + "." if where else "") + name, spec, v)
    return out


def _check_model3d(p):
    if not (p.get("prompt") or "").strip() and not p.get("reference_upload"):
        raise ParamError("prompt is required (or a reference_upload)")
    if p.get("reference_upload") and p.get("stage") == "image":
        raise ParamError("with reference_upload there is no image stage: use stage full")


def _check_sprite(p):
    if p.get("ref_uploads") and not p.get("image_upload"):
        raise ParamError("ref_uploads need a source sprite (image_upload)")
    if len(p.get("ref_uploads") or []) > 2:
        raise ParamError("at most two ref_uploads")
    if bool(p.get("width")) != bool(p.get("height")):
        raise ParamError("give both width and height, or neither")


def _check_voice_prep(p):
    if not re.fullmatch(r"[a-z0-9_]+", p.get("name") or ""):
        raise ParamError("name must be lowercase letters, digits or _")
    if p.get("mood"):
        if not re.fullmatch(r"[a-z0-9]+", p["mood"]):
            raise ParamError("mood must be lowercase letters/digits")
        if not p.get("refs"):
            raise ParamError("a mood needs refs: the clips spoken in that mood")
    elif not p.get("folder"):
        raise ParamError("folder is required for a new voice")


def _check_video(p):
    ids = [s.get("id") for s in p.get("shots") or []]
    if len(set(ids)) != len(ids):
        raise ParamError("shot ids must be unique")
    for s in p.get("shots") or []:
        if s.get("type", "video") != "logo" and not (s.get("prompt") or "").strip():
            raise ParamError(f"shot {s.get('id')}: prompt is required")


def _check_anim(p):
    stage = p.get("stage") or "full"
    if stage in ("rig", "full") and not (p.get("source_url") or p.get("mesh_upload") or p.get("source")):
        raise ParamError("source_url (a library GLB) or mesh_upload (a file path) is required to rig")
    if stage == "anim" and not p.get("name"):
        raise ParamError("name of the rigged character is required for stage anim")
    if stage in ("anim", "full") and not [c for c in p.get("clips") or [] if (c.get("prompt") or "").strip()]:
        raise ParamError("clips: at least one clip with a prompt (or use stage rig)")


CHECKS = {"model3d": _check_model3d, "sprite": _check_sprite, "voice_prep": _check_voice_prep, "video": _check_video, "anim": _check_anim}


def validate(kind, params):
    """Validated, coerced params for a job of `kind`, or ParamError."""
    if kind not in KINDS:
        raise ParamError(f"kind must be one of {list(KINDS)}")
    out = validate_fields(KINDS[kind]["params"], params or {})
    if kind in CHECKS:
        CHECKS[kind](out)
    return out


def job_title(kind, params):
    p = params
    if kind == "voice":
        return f"{p.get('voice', '?')}: {(p.get('text') or '')[:60]}"
    if kind == "music":
        return p.get("name") or (p.get("caption") or "")[:60]
    if kind == "video":
        tag = {"keyframes": " (keyframes)", "assemble": " (assemble)"}.get(p.get("mode"), "")
        only = f" · only {', '.join(p['only'])}" if p.get("only") else ""
        return (p.get("name") or "intro") + tag + only
    if kind == "voice_prep":
        return f"Add mood '{p.get('mood')}' to {p.get('name')}" if p.get("mood") else f"Prepare voice {p.get('name')}"
    if kind == "anim":
        clips = [] if p.get("stage") == "rig" else [c.get("name") or "?" for c in (p.get("clips") or []) if (c.get("prompt") or "").strip()]
        verb = {"rig": "Rig ", "anim": "Animate "}.get(p.get("stage"), "Rig + animate ")
        return verb + (p.get("name") or "Character") + (f": {', '.join(clips)}" if clips else "")
    t = (p.get("name") or p.get("prompt") or "")[:60]
    if kind == "model3d":
        return {"image": "Reference image: ", "model": "3D from image: "}.get(p.get("stage"), "") + t
    return t


def public():
    """The schema as served by GET /api/schema."""
    return {"kinds": {k: dict(v, params={n: s for n, s in v["params"].items()}) for k, v in KINDS.items()},
            "statuses": STATUSES, "terminal": list(TERMINAL)}
