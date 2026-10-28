"""What is on disk: generated models, textures, sprites, voice takes and music, joined with the jobs that made them."""
import os
import time

import json

from .paths import ASSETS, MUS_OUTPUT, SPRITES, TEXTURES, THUMBS, VOC_OUTPUT, COMFY_OUTPUT, file_url
from .voice import wav_duration

import texture as gentex   # ai-3d/scripts: the style suffixes the CLIs append
import sprite as gensprite

STYLES = [gentex.STYLE, gensprite.STYLE_EDIT, gensprite.STYLE_T2I]


def png_prompt(path):
    """Prompt text and seed from the ComfyUI workflow that SaveImage embeds in its PNGs, or None."""
    try:
        from PIL import Image
        im = Image.open(path)
        raw = (getattr(im, "text", None) or {}).get("prompt")
        if not raw:
            return None
        api = json.loads(raw)
    except Exception:
        return None
    nodes = [v for v in api.values() if isinstance(v, dict) and v.get("class_type") == "CLIPTextEncode"]
    nodes.sort(key=lambda v: "prompt" not in v.get("_meta", {}).get("title", "").lower())
    if not nodes:
        return None
    full = nodes[0]["inputs"].get("text", "")
    prompt = full
    for st in STYLES:
        if full.endswith(st):
            prompt = full[:-len(st)]
            break
    seed = None
    for v in api.values():
        if isinstance(v, dict) and v.get("class_type") in ("KSampler", "RandomNoise"):
            seed = v["inputs"].get("seed", v["inputs"].get("noise_seed"))
            break
    return {"prompt": prompt, "text": full, "seed": seed}


def _entry(path, job=None):
    st = os.stat(path)
    e = {"path": path, "url": file_url(path), "name": os.path.splitext(os.path.basename(path))[0],
         "file": os.path.basename(path), "mtime": st.st_mtime, "size": st.st_size, "job": None}
    if job:
        e["job"] = {"id": job.id, "params": job.params, "meta": job.meta, "outputs": job.outputs, "status": job.status,
                    "created": job.created, "parent": job.parent, "intermediates": job.intermediates}
    elif path.lower().endswith(".png"):
        e["meta"] = png_prompt(path)
    return e


def thumb_path(name):
    safe = "".join(c for c in name if c.isalnum() or c in "_-.")
    return os.path.join(THUMBS, safe + ".png")


def thumb_url(name):
    p = thumb_path(name)
    return file_url(p) + f"?t={int(os.stat(p).st_mtime)}" if os.path.isfile(p) else None


def models(store):
    idx = store.by_output_path()
    items = {}
    if os.path.isdir(ASSETS):
        for fn in os.listdir(ASSETS):
            p = os.path.join(ASSETS, fn)
            if not os.path.isfile(p):
                continue
            name, ext = os.path.splitext(fn)
            if ext.lower() not in (".glb", ".fbx"):
                continue
            key = name
            it = items.setdefault(key, {"name": name, "glb": None, "fbx": None, "diffuse": None, "reference": None,
                                        "mtime": 0, "job": None})
            it[ext[1:].lower()] = file_url(p)
            it["mtime"] = max(it["mtime"], os.stat(p).st_mtime)
            job = idx.get(os.path.abspath(p))
            if job and not it["job"]:
                it["job"] = {"id": job.id, "params": job.params, "meta": job.meta, "outputs": job.outputs,
                             "intermediates": [i for i in job.intermediates if not i.get("path") or os.path.isfile(i["path"])],
                             "created": job.created, "parent": job.parent}
                for k in ("diffuse", "reference"):
                    if job.outputs.get(k):
                        it[k] = job.outputs[k].get("url")
            dp = os.path.join(ASSETS, name + "Diffuse.png")
            if os.path.isfile(dp):
                it["diffuse"] = file_url(dp)
            cp = os.path.join(ASSETS, name + "_compare.png")
            if os.path.isfile(cp):
                it["compare"] = file_url(cp)
            it["thumb"] = thumb_url(name)
    # raw ComfyUI GLBs that were never copied to assets (CLI runs with --no-fbx, UI runs)
    d3 = os.path.join(COMFY_OUTPUT, "3d")
    if os.path.isdir(d3):
        known = {os.path.abspath(j.outputs["glb"]["source"]) for j in store.list(kind="model3d")
                 if j.outputs.get("glb", {}).get("source")}
        for fn in os.listdir(d3):
            p = os.path.join(d3, fn)
            if fn.lower().endswith(".glb") and os.path.abspath(p) not in known:
                items["comfy:" + fn] = {"name": fn[:-4], "glb": file_url(p), "fbx": None, "diffuse": None, "reference": None,
                                        "mtime": os.stat(p).st_mtime, "job": None, "untracked": True,
                                        "thumb": thumb_url(fn[:-4])}
    out = list(items.values())
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return out


def _pngs(folder, store, kind):
    idx = store.by_output_path()
    out = []
    if os.path.isdir(folder):
        for fn in os.listdir(folder):
            p = os.path.join(folder, fn)
            if fn.lower().endswith(".png") and os.path.isfile(p):
                out.append(_entry(p, idx.get(os.path.abspath(p))))
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return out


def textures(store):
    return _pngs(TEXTURES, store, "texture")


def sprites(store):
    return _pngs(SPRITES, store, "sprite")


def voice_takes(store):
    idx = store.by_output_path()
    out = []
    if os.path.isdir(VOC_OUTPUT):
        for root, dirs, files in os.walk(VOC_OUTPUT):
            # skip fine-tune checkpoints
            dirs[:] = [d for d in dirs if not os.path.isfile(os.path.join(root, d, "config.json"))]
            for fn in files:
                if fn.lower().endswith(".wav"):
                    p = os.path.join(root, fn)
                    e = _entry(p, idx.get(os.path.abspath(p)))
                    e["duration"] = wav_duration(p)
                    e["rel"] = os.path.relpath(p, VOC_OUTPUT).replace("\\", "/")
                    if e["job"]:
                        e["text"] = e["job"]["params"].get("text")
                        e["voice"] = e["job"]["params"].get("voice")
                        e["mood"] = e["job"]["params"].get("mood")
                        for f in e["job"]["outputs"].get("files", []):
                            if os.path.abspath(f["path"]) == os.path.abspath(p):
                                e["seed"] = f.get("seed")
                    else:
                        e["voice"] = fn.split("_")[0]
                    out.append(e)
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return out


def audio_duration(path):
    if path.lower().endswith(".wav"):
        return wav_duration(path)
    try:
        import soundfile as sf
        info = sf.info(path)
        return round(info.frames / float(info.samplerate), 2)
    except Exception:
        return None


def music_tracks(store):
    idx = store.by_output_path()
    out = []
    if os.path.isdir(MUS_OUTPUT):
        for fn in os.listdir(MUS_OUTPUT):
            if fn.lower().endswith((".wav", ".flac", ".ogg")):
                p = os.path.join(MUS_OUTPUT, fn)
                e = _entry(p, idx.get(os.path.abspath(p)))
                e["duration"] = audio_duration(p)
                if e["job"]:
                    pr = e["job"]["params"]
                    e["caption"] = pr.get("caption")
                    e["loop"] = bool(pr.get("loop"))
                    e["vocal"] = bool((pr.get("lyrics") or "").strip())
                    for f in e["job"]["outputs"].get("files", []):
                        if os.path.abspath(f["path"]) == os.path.abspath(p):
                            e["seed"] = f.get("seed")
                            e["meta"] = f.get("meta") or {}
                out.append(e)
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return out


def characters(store):
    """ai-anim/assets/<Name>/: rigged characters (GLB with clips, FBX, blend, BVH clips) joined with the job that made them."""
    from .paths import CHARACTERS
    idx = store.by_output_path()
    out = []
    if os.path.isdir(CHARACTERS):
        for name in os.listdir(CHARACTERS):
            folder = os.path.join(CHARACTERS, name)
            mp = os.path.join(folder, "character.json")
            if not os.path.isfile(mp):
                continue
            try:
                meta = json.load(open(mp, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            files = {k: os.path.join(folder, name + ext) for k, ext in (("glb", ".glb"), ("fbx", ".fbx"), ("diffuse", "Diffuse.png"), ("blend", ".blend"))}
            e = {"name": name, "path": folder, "mtime": os.stat(mp).st_mtime, "thumbName": "char_" + name,
                 "source": meta.get("source"), "height": meta.get("height"), "rig_stats": meta.get("rig_stats"),
                 "inplace": meta.get("inplace"), "job": None, "thumb": thumb_url("char_" + name)}
            for k, fp in files.items():
                e[k] = file_url(fp) if os.path.isfile(fp) else None
                if os.path.isfile(fp):
                    e["mtime"] = max(e["mtime"], os.stat(fp).st_mtime)
            e["clips"] = [{"name": c.get("name"), "prompt": c.get("prompt"), "seconds": c.get("seconds"), "seed": c.get("seed"),
                           "bvh": file_url(c["bvh"]) if c.get("bvh") and os.path.isfile(c["bvh"]) else None} for c in meta.get("clips", [])]
            job = idx.get(os.path.abspath(files["glb"]))
            if job:
                e["job"] = {"id": job.id, "params": job.params, "meta": job.meta, "outputs": job.outputs, "status": job.status,
                            "created": job.created, "parent": job.parent}
            out.append(e)
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return out


def videos(store):
    """ai-vid/output/<project>/: the finished video, its clips and shot list, joined with the job that made it."""
    from .paths import VID_OUTPUT
    idx = store.by_output_path()
    out = []
    if os.path.isdir(VID_OUTPUT):
        for name in os.listdir(VID_OUTPUT):
            d = os.path.join(VID_OUTPUT, name)
            if not os.path.isdir(d):
                continue
            final = os.path.join(d, name + ".mp4")
            spec = None
            sp = os.path.join(d, "shots.json")
            if os.path.isfile(sp):
                try:
                    spec = json.load(open(sp, encoding="utf-8"))
                except Exception:
                    spec = None
            clips = []
            for s in (spec or {}).get("shots") or []:
                mp4 = os.path.join(d, f"{s.get('id')}.mp4")
                if os.path.isfile(mp4):
                    stem = mp4[:-4]
                    clips.append({"id": s.get("id"), "url": file_url(mp4), "path": mp4, "type": s.get("type", "video"),
                                  "first": file_url(stem + "_first.png") if os.path.isfile(stem + "_first.png") else None,
                                  "last": file_url(stem + "_last.png") if os.path.isfile(stem + "_last.png") else None,
                                  "seed": s.get("seed"), "seconds": s.get("seconds"), "mtime": os.stat(mp4).st_mtime})
            keyframes = [{"file": fn, "url": file_url(os.path.join(d, fn))} for fn in sorted(os.listdir(d)) if fn.startswith("key_") and fn.endswith(".png")]
            if not clips and not os.path.isfile(final):
                continue
            mtimes = [os.stat(final).st_mtime] if os.path.isfile(final) else []
            mtimes += [c["mtime"] for c in clips]
            job = idx.get(os.path.abspath(final))
            e = {"name": name, "path": d, "final": file_url(final) if os.path.isfile(final) else None, "final_path": final if os.path.isfile(final) else None,
                 "clips": clips, "keyframes": keyframes, "spec": spec, "mtime": max(mtimes) if mtimes else os.stat(d).st_mtime, "job": None,
                 "poster": (clips[-1]["last"] if clips else None)}
            if job:
                e["job"] = {"id": job.id, "params": job.params, "meta": job.meta, "outputs": job.outputs, "status": job.status, "created": job.created}
            out.append(e)
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return out
