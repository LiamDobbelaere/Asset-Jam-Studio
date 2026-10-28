"""What the UI, the API and the CLI need to know about the installed back-ends: 3D presets, video presets, readiness."""
import json
import os
import shutil

from . import paths
from .anim import readiness as anim_readiness
from .music import available_models
from .sfx import readiness as sfx_readiness
from .paths import VID_SHOTS

VIDEO_WEIGHTS = (("diffusion_models", "wan2.2_ti2v_5B_fp16.safetensors"), ("vae", "wan2.2_vae.safetensors"),
                 ("text_encoders", "umt5_xxl_fp8_e4m3fn_scaled.safetensors"))


def presets_3d():
    """The ai-3d presets with what each one fixes, for the 3D form's explainer."""
    import generate as gen3d   # ai-3d/scripts (on sys.path via paths)
    notes = {"gameasset": "remesh 512 (256 for perforated parts), hard edges above 60°, decimated to the triangle budget (3000) and planar-dissolved 10° in Blender so flat panels give triangles back (box ~1200, chair ~2100), one 1024² diffuse (base color × AO) and a normal map baked, FBX + PNGs via Blender",
             "character": "same pipeline as Game asset with a 4000-triangle budget; the prompt asks for a front-view T-pose so the Animate page can rig it",
             "detailed": "no remesh or decimation (~700k tris), 4096² base color + metallic-roughness + normal + AO maps, GLB only"}
    out = {}
    for k, pr in gen3d.PRESETS.items():
        lowpoly = k != "detailed"
        out[k] = {"style": pr["style"], "workflow": pr["workflow"], "resolution": pr["resolution"], "texture": pr["texture"],
                  "tris": pr.get("tris") or (gen3d.TRIS["hard"] if lowpoly else None), "tris_round": gen3d.TRIS["round"] if lowpoly else None,
                  "dissolve": gen3d.DISSOLVE["hard"] if lowpoly else None,
                  "fbx": lowpoly, "decimate": lowpoly, "notes": notes.get(k, "")}
    out["_shape_round"] = "Round: crease angle 180° (fully smooth shading), remesh 384, default budget 1500 tris, no dissolve"
    out["_edges"] = {k: v for k, v in gen3d.EDGES.items()}
    return out


def ffmpeg_path():
    return os.environ.get("FFMPEG", shutil.which("ffmpeg") or r"C:\ffmpeg\bin\ffmpeg.exe")


def video_models_present():
    models = os.path.join(paths.COMFY, "models")
    return all(os.path.isfile(os.path.join(models, d, f)) for d, f in VIDEO_WEIGHTS)


def video_info():
    """Shot-list presets from ai-vid/shots plus whether ffmpeg and the Wan weights are there."""
    presets = []
    if os.path.isdir(VID_SHOTS):
        for fn in sorted(os.listdir(VID_SHOTS)):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(VID_SHOTS, fn), encoding="utf-8") as f:
                        presets.append({"file": fn, "spec": json.load(f)})
                except (OSError, ValueError):
                    pass
    return {"presets": presets, "ffmpeg": os.path.isfile(ffmpeg_path()), "models": video_models_present()}


def readiness(comfy_alive):
    """Per back-end: is it installed, and is it usable right now. Served by GET /api/health."""
    mus = available_models()
    an = anim_readiness()
    return {
        "comfy": {"installed": os.path.isfile(paths.COMFY_PY), "running": comfy_alive,
                  "note": "needed by model3d, texture, sprite, video; start it with POST /api/comfy/start (takes ~20 s)"},
        "voice": {"installed": os.path.isfile(paths.VOC_PY), "voices": os.path.isdir(paths.VOICES) and bool(os.listdir(paths.VOICES))},
        "music": {"installed": mus["ready"], "lms": mus["lms"], "dits": mus["dits"]},
        "sfx": {"installed": sfx_readiness()["ready"]},
        "video": {"installed": os.path.isfile(paths.COMFY_PY) and video_models_present(), "ffmpeg": os.path.isfile(ffmpeg_path())},
        "anim": {"installed": an["ready"], "hf_token": an["hf_token"],
                 "note": "clips need the Hugging Face token for Llama 3 (rigging works without it)"},
    }
