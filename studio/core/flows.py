"""The generation flows, built on the ai-3d CLIs' prompt builders and the ai-voc / ai-mus / ai-sfx worker processes."""
import os
import random
import re
import shutil
import time

from . import gpu, paths
from .comfy import comfy, ComfyError
from .paths import AIVID, ASSETS, MUS_OUTPUT, SFX_OUTPUT, SPRITES, TEXTURES, VID_OUTPUT, VOC_OUTPUT, comfy_output_path, file_url
from .anim import anim
from .music import music
from .sfx import sfx
from .voice import tts

import generate as gen3d   # ai-3d/scripts (on sys.path via paths)
import texture as gentex
import sprite as gensprite
import intro as genintro   # ai-vid/scripts

# ---------------------------------------------------------------------------------------------
# progress bookkeeping shared by the ComfyUI flows

WEIGHTS = {"KSampler": 6, "Trellis2ShapeStage": 10, "Trellis2UpsampleStage": 12, "Trellis2TextureStage": 10,
           "BakeTextureFromVoxel": 6, "ProjectReferenceTexture": 2, "PaintMesh": 4, "BakeAmbientOcclusion": 4, "BakeNormalMapFromMesh": 3,
           "RemeshMesh": 3, "UnwrapMesh": 3, "DecimateMesh": 2, "RemoveBackground": 2, "MoGeInference": 2,
           "UNETLoader": 2, "CLIPLoader": 2, "RenderUVAtlas": 2, "VoxelToMesh": 2,
           "AIVidWan22FirstLastFrameToVideoLatent": 2, "Wan22ImageToVideoLatent": 2}

PHASE_BY_CLASS = {
    # 3D
    "ModelSamplingAuraFlow": "Reference image", "CLIPTextEncode": "Reference image", "ConditioningZeroOut": "Reference image",
    "EmptySD3LatentImage": "Reference image", "VAEDecode": "Reference image", "LoadImage": "Reference image",
    "LoadBackgroundRemovalModel": "Background removal", "RemoveBackground": "Background removal",
    "ComfySwitchNode": "Background removal", "SolidMask": "Background removal", "ImageCropToMask": "Background removal",
    "MaskPreview": "Background removal",
    "LoadMoGeModel": "Camera estimation", "MoGeInference": "Camera estimation", "MoGeGeometryToFOV": "Camera estimation",
    "CLIPVisionLoader": "Shape", "Pixal3DConditioning": "Shape", "Trellis2Conditioning": "Shape",
    "EmptyTrellis2LatentStructure": "Shape", "Trellis2ShapeStage": "Shape", "VaeDecodeStructureTrellis2": "Shape",
    "VaeDecodeShapeTrellis": "Shape", "Trellis2UpsampleStage": "Shape (upsample)", "ModelSamplingSD3": "Shape",
    "RescaleCFG": "Shape", "CFGOverride": "Shape", "VoxelToMesh": "Shape", "GetMeshInfo": "Shape",
    "PrimitiveBoolean": "Shape", "PrimitiveInt": "Shape",
    "Trellis2TextureStage": "Texture", "VaeDecodeTextureTrellis": "Texture", "BakeTextureFromVoxel": "Texture",
    "PaintMesh": "Texture", "ApplyTextureToMesh": "Texture", "ProjectReferenceTexture": "Texture", "MaskToImage": "Texture",
    "RemeshMesh": "Mesh clean-up", "DecimateMesh": "Mesh clean-up", "MeshSmoothNormals": "Mesh clean-up",
    "UnwrapMesh": "Mesh clean-up", "RenderUVAtlas": "Mesh clean-up", "BakeAmbientOcclusion": "Mesh clean-up",
    "BakeNormalMapFromMesh": "Mesh clean-up",
    "MeshToFile3D": "Export", "Save3DAdvanced": "Export", "Preview3DAdvanced": "Export", "ImageBlend": "Export",
    "ImageBlur": "Export",
    # texture / sprite
    "SeamlessTileModel": "Sampling", "LoraLoaderModelOnly": "Loading models", "CircularVAEDecode": "Decoding",
    "ImageStitch": "Tiling preview", "ImageCrop": "Cross-fade", "FeatherMask": "Cross-fade", "MaskComposite": "Cross-fade",
    "ImageCompositeMasked": "Cross-fade", "VAEEncode": "Encoding reference", "SetLatentNoiseMask": "Cross-fade",
    "ImageScale": "Encoding reference",
    "ReferenceLatent": "Encoding references", "FluxGuidance": "Sampling", "CFGGuider": "Sampling",
    "BasicScheduler": "Sampling", "KSamplerSelect": "Sampling", "RandomNoise": "Sampling", "SamplerCustomAdvanced": "Sampling",
    "EmptyFlux2LatentImage": "Sampling", "Flux2Scheduler": "Sampling",
    # video (Wan 2.2)
    "ModelSamplingSD3": "Sampling video", "Wan22ImageToVideoLatent": "Encoding frames",
    "AIVidWan22FirstLastFrameToVideoLatent": "Encoding frames", "ImageFromBatch": "Saving frames",
}


def phase_for(node, is_3d=True):
    ct, title = node.get("class_type", ""), node.get("_meta", {}).get("title", "")
    if ct == "KSampler":
        if "uni_pc" in title.lower() or "video" in title.lower():
            return "Sampling video"
        return "Reference image" if is_3d and "text to image" in title.lower() else ("Repainting seams" if "repaint" in title.lower() else "Painting")
    if ct in ("UNETLoader", "CLIPLoader", "VAELoader"):
        return "Loading models"
    if ct == "SaveImage":
        return "Reference image" if is_3d and "reference" in title.lower() else "Saving"
    if not is_3d and ct in ("VAEDecode", "CLIPTextEncode", "ConditioningZeroOut", "EmptySD3LatentImage", "LoadImage"):
        return {"VAEDecode": "Decoding", "LoadImage": "Loading inputs"}.get(ct, "Encoding prompt")
    if ct == "PreviewImage":
        t = title.lower()
        if "base color" in t:
            return "Texture"
        if "occlusion" in t or "normal" in t or "metallic" in t or "roughness" in t:
            return "Mesh clean-up"
        return "Background removal"
    return PHASE_BY_CLASS.get(ct, "Processing")


class Runner:
    """Feeds ComfyUI events into a job: phase, weighted percentage, live preview, intermediate outputs."""

    def __init__(self, job, api, pct_range=(0, 100)):
        self.job, self.api = job, api
        self.is_3d = job.kind in ("model3d", "prop")
        self.lo, self.hi = pct_range
        self.total = sum(WEIGHTS.get(n["class_type"], 1) for n in api.values())
        self.finished = set()
        self.current = None
        self.frac = 0.0
        self.seen_outputs = set()
        self.t0 = time.time()

    def title(self, nid):
        n = self.api.get(str(nid))
        return n["_meta"].get("title", n["class_type"]) if n else str(nid)

    def _pct(self):
        done = sum(WEIGHTS.get(self.api[n]["class_type"], 1) for n in self.finished if n in self.api)
        cur = WEIGHTS.get(self.api[self.current]["class_type"], 1) * self.frac if self.current in self.api else 0
        return self.lo + (self.hi - self.lo) * (done + cur) / max(self.total, 1)

    def _push(self, detail=None, force=False):
        n = self.api.get(self.current)
        phase = phase_for(n, self.is_3d) if n else self.job.progress.get("phase")
        self.job.set_progress(self._pct(), phase, detail if detail is not None else (self.title(self.current) if n else ""),
                              force=force)

    def __call__(self, kind, **d):
        job = self.job
        if kind == "queued":
            job.meta["prompt_id"] = d["prompt_id"]
            job.set_progress(self.lo, "Queued in ComfyUI", "", force=True)
        elif kind == "start":
            job.add_log("ComfyUI started executing")
        elif kind == "cached":
            self.finished.update(str(n) for n in d["nodes"])
            self._push(force=True)
        elif kind == "node":
            if self.current and self.current != d["node"]:
                self.finished.add(self.current)
            self.current, self.frac = (str(d["node"]) if d["node"] is not None else None), 0.0
            if self.current:
                job.set_progress(value=0, max=0)
                self._push(force=True)
        elif kind == "progress":
            v, m = d.get("value", 0), max(d.get("max", 1), 1)
            node = str(d.get("node") or self.current)
            self.current = node
            self.frac = v / m
            job.set_progress(value=v, max=m)
            self._push(f"{self.title(node)}  {v}/{m}")
        elif kind == "state":
            for nid, st in d["nodes"].items():
                if st.get("state") == "finished":
                    self.finished.add(str(nid))
        elif kind == "executed":
            nid = str(d["node"])
            self.finished.add(nid)
            out = d.get("output") or {}
            for key in ("images", "3d", "files"):
                items = [it for it in (out.get(key) or []) if isinstance(it, dict) and "filename" in it]
                if len(items) > 8:   # a frame dump (video): show the first frame only
                    items = items[:1]
                for item in items:
                    p = comfy_output_path(item)
                    if p in self.seen_outputs:
                        continue
                    self.seen_outputs.add(p)
                    if item.get("type") == "temp" and os.path.isfile(p):
                        # ComfyUI wipes temp/ on every start; keep a copy next to the job record
                        keep = os.path.join(paths.INTERMEDIATES, job.id)
                        os.makedirs(keep, exist_ok=True)
                        dst = os.path.join(keep, item["filename"])
                        shutil.copyfile(p, dst)
                        p = dst
                    ext = os.path.splitext(item["filename"])[1].lower()
                    k = "model" if ext in (".glb", ".gltf", ".obj") else ("image" if ext in (".png", ".jpg", ".jpeg", ".webp") else "file")
                    job.add_intermediate(self.title(nid), k, p, file_url(p), nid)
            job.add_log(f"{self.title(nid)} done ({time.time() - self.t0:.0f}s)")
        elif kind == "preview":
            job.set_preview(d["data"], d["mime"])


GPU_HEADROOM_MB = 5500   # free VRAM the voice / music / sfx / anim workers need before they load


def _gpu_free_within(seconds, need_mb):
    t0 = time.time()
    while True:
        g = gpu.read_once()
        if not g["available"] or g["total_mb"] - g["used_mb"] >= need_mb:
            return True
        if time.time() - t0 > seconds:
            return False
        time.sleep(1)


def take_gpu(job, who):
    """Hand the GPU from ComfyUI to one of the process workers, and check that it actually let go.

    ComfyUI's /free only unloads models its own manager knows about; the 3D custom nodes keep theirs in
    module globals, so VRAM can sit at 7 GB after a "successful" free. A worker loading on top of that
    quietly falls back to the CPU (ACE-Step's VAE decode then takes 20+ minutes instead of seconds), so
    poll nvidia-smi, stop ComfyUI if the studio owns it (run_comfy restarts it), and fail loudly if the
    memory still is not there. The other workers' unload is checked the same way: a worker can keep its
    VRAM after "unloaded" (CUDA context, tensors still referenced), so if the memory does not come back
    their processes are ended; the next request starts them again."""
    others = [w for w in (tts, music, sfx, anim) if w is not who]
    for w in others:
        w.unload()
    # A worker whose models are already resident (the second voice line of a batch) only needs the others'
    # share back, not the full headroom: its own footprint, measured after its last job, counts as free.
    held = int(getattr(who, "vram_mb", 0) or 0) if getattr(who, "loaded", False) else 0
    need = max(1200, GPU_HEADROOM_MB - held)
    if _gpu_free_within(3, need):
        return
    if comfy.alive():
        job.set_progress(1, "Freeing the GPU", "asking ComfyUI to unload its models", force=True)
        comfy.free()
        if _gpu_free_within(12, need):
            return
        g = gpu.read_once()
        job.add_log(f"ComfyUI still holds {g['used_mb']} MB after /free; stopping it (it restarts for the next ComfyUI job)")
        job.set_progress(2, "Freeing the GPU", "stopping ComfyUI", force=True)
        if comfy.stop() and _gpu_free_within(15, need):
            return
    running = [w for w in others if w.alive()]
    if running:
        g = gpu.read_once()
        job.add_log(f"{g['used_mb']} MB still in use; stopping the {', '.join(w.name for w in running)} worker process(es)")
        job.set_progress(3, "Freeing the GPU", "stopping the other workers", force=True)
        for w in running:
            w.stop()
        if _gpu_free_within(15, need):
            return
    g = gpu.read_once()
    raise RuntimeError(f"the GPU is not free: {g['used_mb']} of {g['total_mb']} MB in use after unloading ComfyUI and the "
                       "other workers. Close other programs that use the GPU and resubmit; running now would push the "
                       f"{who.name} models to the CPU and take 20+ minutes.")


def ensure_comfy(job, pct=0):
    if comfy.alive():
        return
    if not comfy.started_by_us:
        raise ComfyError("ComfyUI is not running. Start it from the header (or ai-3d/start_comfyui.bat) and retry.")
    # take_gpu() stopped it to give another worker the GPU; bring it back for this job
    job.set_progress(pct, "Starting ComfyUI", "it was stopped to free the GPU for another job", force=True)
    comfy.start()
    t0 = time.time()
    while not comfy.alive():
        if job.cancel_requested:
            raise RuntimeError("cancelled")
        if time.time() - t0 > 180:
            raise ComfyError("ComfyUI did not come back within 3 minutes; see its log in the studio header")
        time.sleep(2)


def run_comfy(job, api, pct_range=(0, 100)):
    ensure_comfy(job, pct_range[0])
    tts.unload()   # the GPU belongs to ComfyUI now
    music.unload()
    sfx.unload()
    anim.unload()
    runner = Runner(job, api, pct_range)
    hist = comfy.run(api, runner, should_cancel=lambda: job.cancel_requested)
    return hist, runner


def history_files(hist, node_id=None, kinds=("images", "3d", "files")):
    """Absolute paths of the files a history entry reports (optionally only one node's)."""
    out = []
    for nid, o in hist.get("outputs", {}).items():
        if node_id is not None and str(nid) != str(node_id):
            continue
        for k in kinds:
            for item in o.get(k, []) or []:
                if isinstance(item, dict) and "filename" in item:
                    out.append((str(nid), comfy_output_path(item)))
    return out


def unique_name(folder, name, exts):
    """name, name_2, name_3 ... so that none of name+ext exists in folder."""
    name = re.sub(r"[^A-Za-z0-9_\-]+", "", name) or "Asset"
    cand, i = name, 1
    while any(os.path.exists(os.path.join(folder, cand + e)) for e in exts):
        i += 1
        cand = f"{name}_{i}"
    return cand


def upload_to_comfy(path, name):
    with open(path, "rb") as f:
        data = f.read()
    return comfy.upload_image(name, data)


def _int(v, default=None):
    try:
        return int(v) if v not in (None, "", "random") else default
    except (TypeError, ValueError):
        return default


def _float(v, default=None):
    try:
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------------------------
# 3D models

def flow_model3d(job):
    p = job.params
    stage = p.get("stage", "full")          # image | model | full
    seed = _int(p.get("seed"))
    if seed is None:
        seed = random.randint(0, 2**31)
        p["seed"] = seed
    reference = None
    if p.get("reference_upload"):
        job.set_progress(1, "Uploading reference image", "", force=True)
        reference = upload_to_comfy(p["reference_upload"], f"ref3d_{job.id}.png")
    api, info = gen3d.build_api(
        p.get("prompt", ""), p.get("preset", "gameasset"), bool(p.get("raw")), seed, p.get("model", "pixal3d"),
        _int(p.get("resolution")), _int(p.get("texture")), _int(p.get("tris")), p.get("shape", "hard"),
        image_only=(stage == "image"), reference_image=reference, edges=p.get("edges") or "sharp",
        normalmap=p.get("normalmap", True) not in (False, "false", "0", 0), remesh=_int(p.get("remesh")),
        normal_from=p.get("normal_from") or "remesh", blur=_int(p.get("blur")), ao=_float(p.get("ao")),
        texture_from=p.get("texture_from") or "picture")
    job.meta.update(info)
    job.add_log(f"seed {seed}, preset {info['preset']}, model {info['model']}, shape res {info['resolution']}, texture {info['texture']}"
                + (f", {info['tris']} tris, {info['shape']} shape, {info['edges']} edges, remesh {info['remesh']}, "
                   f"normal map {'on (from ' + info['normal_from'] + ')' if info['normalmap'] else 'off'}, texture from {info['texture_from']}" if "tris" in info else ""))
    convert = p.get("preset", "gameasset") in ("gameasset", "character") and p.get("fbx", True) and stage != "image"
    hist, runner = run_comfy(job, api, (0, 92 if convert else 100))

    # reference image
    for nid, path in history_files(hist, kinds=("images",)):
        if api.get(nid, {}).get("class_type") == "SaveImage" and "reference" in api[nid]["_meta"]["title"].lower():
            job.outputs["reference"] = {"path": path, "url": file_url(path)}
    if stage == "image":
        job.set_progress(100, "Reference image ready", "Check it, then continue to 3D or retry with another seed")
        return

    glb = None
    for nid, path in history_files(hist, kinds=("3d",)):
        if path.lower().endswith(".glb") and os.sep + "3d" + os.sep in path:
            glb = path
    if glb is None:
        cands = [os.path.join(paths.COMFY_OUTPUT, "3d", f) for f in os.listdir(os.path.join(paths.COMFY_OUTPUT, "3d"))
                 if f.endswith(".glb")]
        glb = max(cands, key=os.path.getmtime) if cands else None
    if glb is None:
        raise ComfyError("no GLB in the ComfyUI output")
    for nid, path in history_files(hist, kinds=("images",)):
        if api.get(nid, {}).get("class_type") == "SaveImage" and "diffuse" in api[nid]["_meta"]["title"].lower():
            job.outputs["diffuse"] = {"path": path, "url": file_url(path)}
        if api.get(nid, {}).get("class_type") == "SaveImage" and "normal map" in api[nid]["_meta"]["title"].lower():
            job.outputs["normal"] = {"path": path, "url": file_url(path)}

    name = unique_name(ASSETS, p.get("name") or gen3d.asset_name(p.get("prompt", "")), [".glb", ".fbx"])
    job.meta["name"] = name
    dst = os.path.join(ASSETS, name + ".glb")
    shutil.copyfile(glb, dst)
    job.add_log(f"GLB: {dst}")
    if convert:
        # the GLB is published only after Blender has rewritten it (levelled, dissolved), or the viewer loads the
        # intermediate file and shows the wrong triangle count
        job.set_progress(93, "Converting to FBX", "Blender", force=True)
        try:
            fbx, png, stats = gen3d.glb_to_fbx(dst, ASSETS, name, _float(p.get("size"), 1.0),
                                               level=p.get("level", True) not in (False, "false", "0", 0),
                                               dissolve=_float(p.get("dissolve"), gen3d.DISSOLVE[p.get("shape") or "hard"]),
                                               crease=180.0 if p.get("shape") == "round" else 60.0)
            job.outputs["fbx"] = {"path": fbx, "url": file_url(fbx)}
            job.outputs["diffuse_fbx"] = {"path": png, "url": file_url(png)}
            normal_png = os.path.join(ASSETS, name + "Normal.png")
            if os.path.isfile(normal_png):
                job.outputs["normal_fbx"] = {"path": normal_png, "url": file_url(normal_png)}
            job.meta["fbx_stats"] = stats
            job.add_log(f"FBX: {fbx} {stats}")
        except RuntimeError as e:
            job.add_log(str(e))
            job.meta["fbx_error"] = str(e)[:500]
    job.outputs["glb"] = {"path": dst, "url": file_url(dst), "source": glb}
    job.set_progress(100, "Done", "")


# ---------------------------------------------------------------------------------------------
# world textures

def flow_texture(job):
    p = job.params
    seed = _int(p.get("seed"))
    if seed is None:
        seed = random.randint(0, 2**31)
        p["seed"] = seed
    reference = None
    if p.get("reference_upload"):
        job.set_progress(1, "Uploading reference texture", "", force=True)
        reference = upload_to_comfy(p["reference_upload"], f"texref_{job.id}.png")
    api, final_node, info = gentex.build_api(
        p.get("prompt", ""), bool(p.get("raw")), _int(p.get("width"), 1024), _int(p.get("height"), 1024), seed,
        _int(p.get("steps")), p.get("tile", "native"), _float(p.get("lora"), 0.0), _int(p.get("band"), 192),
        _float(p.get("denoise"), 0.0), reference, _float(p.get("strength"), 0.6))
    job.meta.update(info)
    job.add_log(f"seed {seed}, {info['width']}x{info['height']}, {info['steps']} steps, tile {info['tile']}, lora {info['lora']}"
                + (f", reference strength {info['strength']}" if reference else ""))
    hist, runner = run_comfy(job, api)
    result = None
    for nid, path in history_files(hist, kinds=("images",)):
        if nid == final_node:
            result = path
        elif "preview" in api.get(nid, {}).get("_meta", {}).get("title", "").lower():
            job.outputs["preview2x2"] = {"path": path, "url": file_url(path)}
    if not result:
        raise ComfyError("no texture in the ComfyUI output")
    name = unique_name(TEXTURES, p.get("name") or gen3d.asset_name(p.get("prompt", ""), "Texture"), [".png"])
    dst = os.path.join(TEXTURES, name + ".png")
    shutil.copyfile(result, dst)
    job.meta["name"] = name
    job.outputs["texture"] = {"path": dst, "url": file_url(dst), "source": result}
    job.add_log(f"texture: {dst}")


# ---------------------------------------------------------------------------------------------
# 2D sprites

def flow_sprite(job):
    p = job.params
    seed = _int(p.get("seed"))
    if seed is None:
        seed = random.randint(0, 2**31)
        p["seed"] = seed
    ensure_comfy(job)
    job.set_progress(1, "Preparing images", "", force=True)
    w, h = _int(p.get("width")), _int(p.get("height"))
    api, final_node, state = gensprite.build_api(
        p.get("prompt", ""), p.get("image_upload") or None, p.get("ref_uploads") or [], p.get("model", "distilled"),
        _int(p.get("steps")), _float(p.get("cfg")), p.get("negative") or gensprite.NEGATIVE, seed, bool(p.get("raw")),
        w, h, _float(p.get("megapixels"), 1.0), p.get("bg") or "#ffffff", p.get("alpha", "auto"))
    ws = state["work_size"]
    job.meta.update({k: state[k] for k in ("images", "model", "steps", "cfg", "seed", "alpha", "text")})
    job.meta["size"] = f"{ws[0]}x{ws[1]}"
    job.add_log(f"seed {seed}, {state['model']} model, {state['steps']} steps, cfg {state['cfg']}, {state['images']} input image(s), {ws[0]}x{ws[1]}")
    hist, runner = run_comfy(job, api)
    result = None
    for nid, path in history_files(hist, kinds=("images",)):
        if nid == final_node:
            result = path
        else:
            job.outputs["raw"] = {"path": path, "url": file_url(path)}
    if not result:
        raise ComfyError("no sprite in the ComfyUI output")
    name = unique_name(SPRITES, p.get("name") or gen3d.asset_name(p.get("prompt", ""), "Sprite"), [".png"])
    dst = os.path.join(SPRITES, name + ".png")
    gensprite.finish_sprite(result, dst, state, bool(p.get("match_source")))
    job.meta["name"] = name
    job.outputs["sprite"] = {"path": dst, "url": file_url(dst), "source": result}
    job.add_log(f"sprite: {dst}")


# ---------------------------------------------------------------------------------------------
# voice lines

def slug(text, n=40):
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:n] or "line"


def flow_voice(job):
    p = job.params
    text = (p.get("text") or "").strip()
    if not text:
        raise ValueError("no text")
    voice = p.get("voice")
    if not voice:
        raise ValueError("no voice selected")
    takes = max(1, min(_int(p.get("takes"), 1), 8))
    seed = _int(p.get("seed"))
    mood = p.get("mood") or None
    take_gpu(job, tts)
    job.outputs["files"] = []
    for i in range(takes):
        if job.cancel_requested:
            raise RuntimeError("cancelled")
        take_seed = (seed + i) if seed is not None else random.randint(0, 2**31)
        base = f"{voice}{'_' + mood if mood else ''}_{slug(text)}"
        name = unique_name(VOC_OUTPUT, base, [".wav"])
        out = os.path.join(VOC_OUTPUT, name + ".wav")
        req = {"id": f"{job.id}-{i}", "text": text, "voice": voice, "mood": mood, "mood_timbre": bool(p.get("mood_timbre")),
               "language": p.get("language") or "English", "seed": take_seed, "temperature": _float(p.get("temperature")),
               "top_k": _int(p.get("top_k")), "top_p": _float(p.get("top_p")),
               "repetition_penalty": _float(p.get("repetition_penalty")), "out": out,
               "model": p.get("model") or None, "speaker": p.get("speaker") or None}
        lo, hi = 2 + 98 * i / takes, 2 + 98 * (i + 1) / takes
        label = f"take {i + 1}/{takes}" if takes > 1 else ""

        def on_event(kind, **d):
            if kind == "log":
                job.add_log(d["message"])
                job.set_progress(lo, "Starting voice worker", d["message"], force=True)
            elif kind == "loading":
                job.set_progress(lo + 2, "Loading Qwen3-TTS", d.get("model") or "", force=True)
            elif kind == "generating":
                job.add_log(f"reference {os.path.basename(d.get('reference') or '')}"
                            + (f", timbre from {os.path.basename(d['timbre'])}" if d.get("timbre") else ""))
                job.set_progress(lo + 5, "Generating", label, force=True)
            elif kind == "tick":
                job.set_progress(min(hi - 1, lo + 5 + d["seconds"] * 2), "Generating", f"{label} {d['seconds']:.0f}s".strip())

        msg = tts.request(req, on_event, should_cancel=lambda: job.cancel_requested)
        job.outputs["files"].append({"path": msg["path"], "url": file_url(msg["path"]), "seed": take_seed,
                                     "duration": msg.get("duration"), "seconds": msg.get("seconds")})
        job.add_log(f"take {i + 1}: {msg.get('duration')} s of audio in {msg.get('seconds')} s ({msg.get('realtime')}x realtime), "
                    f"peak VRAM {msg.get('peak_vram_gb')} GB -> {msg['path']}")
        job.publish(force=True)
    job.meta["takes"] = takes
    job.set_progress(100, "Done", f"{takes} take{'s' if takes > 1 else ''}")


def flow_voice_prep(job):
    """Prepare a voice (transcribe clips, build ref.wav) or add a mood reference, via ai-voc/scripts/prep_voice.py."""
    import subprocess
    p = job.params
    name = (p.get("name") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_]+", name):
        raise ValueError("voice name must be lowercase letters, digits or _")
    mood = (p.get("mood") or "").strip().lower() or None
    if mood and not re.fullmatch(r"[a-z0-9]+", mood):
        raise ValueError("mood must be lowercase letters/digits, e.g. upset, happy")
    folder = (p.get("folder") or "").strip() or None
    if not mood and not folder:
        raise ValueError("give the folder with the actor's audio files")
    if folder and not os.path.isdir(folder):
        raise ValueError(f"folder not found: {folder}")
    cmd = [paths.VOC_PY, "-I", os.path.join(paths.AIVOC, "scripts", "prep_voice.py")]
    if folder:
        cmd.append(folder)
    cmd += ["--name", name, "--language", p.get("language") or "en"]
    if mood:
        cmd += ["--mood", mood]
    else:
        cmd += ["--ref-seconds", str(_float(p.get("ref_seconds"), 12.0)), "--max-ref-clips", str(_int(p.get("max_ref_clips"), 3))]
    refs = [str(r).strip() for r in (p.get("refs") or []) if str(r).strip()]
    if refs:
        cmd += ["--ref"] + refs
    elif mood:
        raise ValueError("pick the clips spoken in that mood")
    if not os.path.exists(paths.VOC_PY):
        raise RuntimeError(f"ai-voc venv not found at {paths.VOC_PY}; run ai-voc/setup.ps1 first")
    if not mood:   # Whisper needs the GPU
        tts.unload()
        music.unload()
        sfx.unload()
        if comfy.alive():
            comfy.free()
    job.add_log("prep_voice " + " ".join(f'"{c}"' if " " in c else c for c in cmd[3:]))
    job.set_progress(2, "Adding mood reference" if mood else "Transcribing clips", "", force=True)
    proc = subprocess.Popen(cmd, cwd=paths.AIVOC, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", bufsize=1,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    total, done = None, 0
    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        if job.cancel_requested:
            proc.kill()
            raise RuntimeError("cancelled")
        job.add_log(line)
        m = re.match(r"transcribing (\d+) clips", line)
        if m:
            total = int(m.group(1))
            job.set_progress(5, "Transcribing clips", f"0/{total} (Whisper)", force=True)
        elif re.match(r"\s*\[\d{3}\] ", line) and total:
            done += 1
            job.set_progress(5 + 85 * done / total, "Transcribing clips", f"{done}/{total}: {line.strip()[6:60]}", force=True)
        elif line.startswith("loading") and "clips from" in line:
            job.set_progress(3, "Converting clips", line, force=True)
        elif line.startswith("ref"):
            job.set_progress(95, "Building reference", line[:80], force=True)
    code = proc.wait()
    if code != 0:
        raise RuntimeError("prep_voice failed (exit %d): %s" % (code, " / ".join(job.log[-3:])))
    job.outputs["voice"] = {"name": name, "mood": mood}
    job.set_progress(100, "Done", f"mood '{mood}' added to {name}" if mood else f"voice '{name}' ready")


# ---------------------------------------------------------------------------------------------
# music (ACE-Step 1.5 via ai-mus/scripts/music_worker.py)

def flow_music(job):
    p = job.params
    caption = (p.get("caption") or "").strip()
    if not caption:
        raise ValueError("describe the music")
    lyrics = (p.get("lyrics") or "").strip()
    instrumental = not lyrics if p.get("instrumental") is None else bool(p.get("instrumental"))
    takes = max(1, min(_int(p.get("takes"), 1), 8))
    seed = _int(p.get("seed"))
    fmt = p.get("format") or "wav"
    if fmt not in ("wav", "flac", "ogg"):
        raise ValueError("format must be wav, flac or ogg")
    if not os.path.exists(paths.MUS_PY):
        raise RuntimeError(f"ai-mus venv not found at {paths.MUS_PY}; run ai-mus/setup.ps1 first")
    take_gpu(job, music)
    stem = p.get("name") or slug(caption)
    job.meta["name"] = stem
    job.outputs["files"] = []
    for i in range(takes):
        if job.cancel_requested:
            raise RuntimeError("cancelled")
        take_seed = (seed + i) if seed is not None else random.randint(0, 2**31)
        name = unique_name(MUS_OUTPUT, stem if takes == 1 else f"{stem}_{i + 1}", [".wav", ".flac", ".ogg"])
        req = {"id": f"{job.id}-{i}", "caption": caption, "lyrics": lyrics, "instrumental": instrumental,
               "duration": _float(p.get("duration"), 60.0), "seed": take_seed, "bpm": _int(p.get("bpm")),
               "keyscale": p.get("keyscale") or "", "timesignature": p.get("timesignature") or "",
               "think": p.get("think", True) is not False, "steps": _int(p.get("steps"), 8),
               "shift": _float(p.get("shift"), 3.0), "lm_temperature": _float(p.get("lm_temperature"), 0.85),
               "reference_audio": p.get("reference_upload") or None, "language": p.get("language") or "en",
               "loop": bool(p.get("loop")), "crossfade": _float(p.get("crossfade"), 2.0), "format": fmt,
               "lm": p.get("lm") if p.get("lm") is not None else "acestep-5Hz-lm-0.6B",
               "out": os.path.join(MUS_OUTPUT, name)}
        lo, hi = 2 + 98 * i / takes, 2 + 98 * (i + 1) / takes
        label = f"take {i + 1}/{takes}" if takes > 1 else ""

        def on_event(kind, **d):
            if kind == "log":
                job.add_log(d["message"])
                job.set_progress(lo, "Starting music worker", d["message"], force=True)
            elif kind == "loading":
                job.set_progress(lo + 2, "Loading ACE-Step 1.5", d.get("what") or "", force=True)
            elif kind == "progress":
                r = d.get("ratio") or 0
                what = d.get("what") or ""
                job.add_log(f"{int(r * 100):3d}%  {what}")
                job.set_progress(lo + 5 + (hi - lo - 6) * r, "Composing", f"{label} {what}".strip(), force=True)
            elif kind == "tick":
                job.set_progress(None, None, f"{label} {d['seconds']:.0f}s".strip())

        msg = music.request(req, on_event, should_cancel=lambda: job.cancel_requested)
        f = {"path": msg["path"], "url": file_url(msg["path"]), "seed": take_seed, "duration": msg.get("duration"),
             "seconds": msg.get("seconds"), "meta": msg.get("meta") or {}}
        job.outputs["files"].append(f)
        m = f["meta"]
        job.add_log(f"take {i + 1}: {msg.get('duration')} s of music in {msg.get('seconds')} s, seed {take_seed}, "
                    f"peak VRAM {msg.get('peak_vram_gb')} GB"
                    + (f", LM: {m.get('lm_bpm')} bpm {m.get('lm_keyscale') or ''}" if m.get("lm_bpm") else "")
                    + f" -> {msg['path']}")
        job.publish(force=True)
    job.meta["takes"] = takes
    job.set_progress(100, "Done", f"{takes} take{'s' if takes > 1 else ''}")



# ---------------------------------------------------------------------------------------------
# sound effects (MOSS-SoundEffect v2.0 via ai-sfx/scripts/sfx_worker.py)

def flow_sfx(job):
    p = job.params
    prompt = (p.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("describe the sound")
    takes = max(1, min(_int(p.get("takes"), 1), 8))
    seed = _int(p.get("seed"))
    fmt = p.get("format") or "wav"
    if fmt not in ("wav", "flac", "ogg"):
        raise ValueError("format must be wav, flac or ogg")
    if not os.path.exists(paths.SFX_PY):
        raise RuntimeError(f"ai-sfx venv not found at {paths.SFX_PY}; run ai-sfx/setup.ps1 first")
    take_gpu(job, sfx)
    stem = p.get("name") or slug(prompt)
    job.meta["name"] = stem
    job.outputs["files"] = []
    peak = _float(p.get("peak_db"), -1.0) if p.get("normalize", True) is not False else None
    for i in range(takes):
        if job.cancel_requested:
            raise RuntimeError("cancelled")
        take_seed = (seed + i) if seed is not None else random.randint(0, 2**31)
        name = unique_name(SFX_OUTPUT, stem if takes == 1 else f"{stem}_{i + 1}", [".wav", ".flac", ".ogg"])
        req = {"id": f"{job.id}-{i}", "prompt": prompt, "duration": _float(p.get("duration"), 4.0), "seed": take_seed,
               "steps": _int(p.get("steps"), 50), "cfg": _float(p.get("cfg"), 4.0), "shift": _float(p.get("shift"), 5.0),
               "negative": p.get("negative") or "", "loop": bool(p.get("loop")), "crossfade": _float(p.get("crossfade"), 1.0),
               "trim": p.get("trim", True) is not False, "peak_db": peak, "highpass": _float(p.get("highpass"), 30.0),
               "format": fmt, "out": os.path.join(SFX_OUTPUT, name)}
        lo, hi = 2 + 98 * i / takes, 2 + 98 * (i + 1) / takes
        label = f"take {i + 1}/{takes}" if takes > 1 else ""

        def on_event(kind, **d):
            if kind == "log":
                job.add_log(d["message"])
                job.set_progress(lo, "Starting sfx worker", d["message"], force=True)
            elif kind == "loading":
                job.set_progress(lo + 2, "Loading MOSS-SoundEffect", d.get("what") or "", force=True)
            elif kind == "progress":
                r = d.get("ratio") or 0
                job.set_progress(lo + 5 + (hi - lo - 6) * r, "Generating", f"{label} {d.get('what') or ''}".strip(), force=True)
            elif kind == "tick":
                job.set_progress(None, None, f"{label} {d['seconds']:.0f}s".strip())

        msg = sfx.request(req, on_event, should_cancel=lambda: job.cancel_requested)
        f = {"path": msg["path"], "url": file_url(msg["path"]), "seed": take_seed, "duration": msg.get("duration"),
             "seconds": msg.get("seconds")}
        job.outputs["files"].append(f)
        job.add_log(f"take {i + 1}: {msg.get('duration')} s of sound in {msg.get('seconds')} s, seed {take_seed}, "
                    f"peak VRAM {msg.get('peak_vram_gb')} GB -> {msg['path']}")
        job.publish(force=True)
    job.meta["takes"] = takes
    job.set_progress(100, "Done", f"{takes} take{'s' if takes > 1 else ''}")


# ---------------------------------------------------------------------------------------------
# video: shot list -> Wan 2.2 clips + logo reveal -> ffmpeg (ai-vid/scripts/intro.py)

def flow_video(job):
    p = job.params
    shots = p.get("shots") or []
    if not shots:
        raise ValueError("add at least one shot")
    name = re.sub(r"[^A-Za-z0-9_\-]+", "_", (p.get("name") or "intro").strip()) or "intro"
    spec = {"name": name, "width": _int(p.get("width"), 832), "height": _int(p.get("height"), 480), "fps": _int(p.get("fps"), 24),
            "transition": _float(p.get("transition"), 0.0), "transition_kind": p.get("transition_kind") or "fade",
            "audio": p.get("audio_upload") or p.get("audio") or None, "negative": p.get("negative") or None,
            "crf": _int(p.get("crf"), 16), "shots": [dict(s) for s in shots]}
    only = [s for s in (p.get("only") or []) if s]
    mode = p.get("mode") or "full"            # full | keyframes | assemble
    job.meta.update(name=name, mode=mode, only=only, shots=len(shots))
    if mode != "assemble":
        ensure_comfy(job)
    n = len(shots)
    rng = {"lo": 0.0, "hi": 100.0}
    job.outputs["clips"] = []
    job.outputs["keyframes"] = []

    def remember_keyframe(sid, which, path):
        url = file_url(path)
        if url and not any(k["path"] == path for k in job.outputs["keyframes"]):
            job.outputs["keyframes"].append({"shot": sid, "which": which, "path": path, "url": url})
            job.add_intermediate(f"{sid} {which} frame", "image", path, url)

    def on_shot(i, s, stage, **d):
        lo, hi = 2 + 90 * i / n, 2 + 90 * (i + 1) / n
        label = f"shot {i + 1}/{n}: {s['id']}"
        if stage == "keyframes":
            rng.update(lo=lo, hi=lo + (hi - lo) * 0.35)
            job.set_progress(lo, "Keyframes", label, force=True)
        elif stage == "start":
            rng.update(lo=lo + (hi - lo) * 0.35, hi=hi)
            for which in ("start", "end"):
                if d.get(which):
                    remember_keyframe(s["id"], which, d[which])
            job.set_progress(rng["lo"], "Rendering logo reveal" if d.get("kind") == "logo" else "Sampling video", label, force=True)
        elif stage == "done":
            mp4 = d.get("mp4")
            if mp4:
                stem = os.path.splitext(mp4)[0]
                c = {"id": s["id"], "path": mp4, "url": file_url(mp4), "reused": bool(d.get("reused")),
                     "first": file_url(stem + "_first.png") if os.path.isfile(stem + "_first.png") else None,
                     "last": file_url(stem + "_last.png") if os.path.isfile(stem + "_last.png") else None,
                     "seconds": (d.get("info") or {}).get("seconds"), "seed": (d.get("info") or {}).get("seed")}
                job.outputs["clips"].append(c)
                if c["last"] and not d.get("reused"):
                    job.add_intermediate(f"{s['id']} last frame", "image", stem + "_last.png", c["last"])
            for k in d.get("keyframes") or []:
                remember_keyframe(s["id"], "start" if k.endswith("_start.png") else "end", k)
            job.set_progress(hi, "Shot done", label, force=True)
            job.publish(force=True)

    def executor(api, label=""):
        hist, runner = run_comfy(job, api, (rng["lo"], rng["hi"]))
        return hist

    r = genintro.run_project(spec, AIVID, only or None, mode == "assemble", mode == "keyframes", executor, job.add_log, on_shot,
                             out_root=VID_OUTPUT)
    job.outputs["project"] = {"path": r["project"]}
    if r["final"]:
        job.outputs["final"] = {"path": r["final"], "url": file_url(r["final"])}
        job.add_log(f"final: {r['final']}")
    job.set_progress(100, "Done", "keyframes ready: check them, then generate the shots" if mode == "keyframes" else "")


# ---------------------------------------------------------------------------------------------
# rigged + animated characters (ai-anim: Make-It-Animatable + Kimodo + Blender)

def url_to_path(url):
    """/files/<root>/<rel> (what the library hands the browser) -> absolute path, or None."""
    if not url or not url.startswith("/files/"):
        return None
    import urllib.parse
    parts = url[len("/files/"):].split("/", 1)
    root = paths.FILE_ROOTS.get(parts[0])
    if not root or len(parts) < 2:
        return None
    full = os.path.abspath(os.path.join(root, urllib.parse.unquote(parts[1])))
    return full if full.startswith(os.path.abspath(root) + os.sep) else None


def flow_anim(job):
    import animate as anim_cli   # ai-anim/scripts (on sys.path via paths)
    p = job.params
    stage = p.get("stage", "full")          # rig | anim | full
    name = anim_cli.safe_name(p.get("name") or "Character")
    if not os.path.exists(paths.ANIM_PY):
        raise RuntimeError(f"ai-anim venv not found at {paths.ANIM_PY}; run ai-anim/setup.ps1 first")
    clips = []
    for i, c in enumerate(p.get("clips") or []):
        if (c.get("prompt") or "").strip():
            clips.append({"name": anim_cli.safe_name(c.get("name"), f"Clip{i + 1}"), "prompt": c["prompt"].strip(),
                          "seconds": _float(c.get("seconds")), "seed": _int(c.get("seed"))})
    if stage == "anim" and not clips:
        raise ValueError("add at least one clip (name + what the person does)")
    take_gpu(job, anim)

    def events(lo, hi, label):
        def on_event(kind, **d):
            if kind == "log":
                job.add_log(d["message"])
            elif kind == "loading":
                job.set_progress(lo, "Loading " + d["what"], "first use takes a while", force=True)
            elif kind == "progress":
                v, m = d.get("value") or 0, max(d.get("max") or 1, 1)
                job.set_progress(lo + (hi - lo) * v / m, label, f"denoising {v}/{m}", value=v, max=m)
        return on_event

    cancel = lambda: job.cancel_requested
    folder = os.path.join(paths.CHARACTERS, name)
    do_rig = stage in ("rig", "full")
    do_anim = stage in ("anim", "full") and bool(clips)
    rig_hi = 40 if do_anim else 95
    if do_rig:
        mesh = p.get("mesh_upload") or url_to_path(p.get("source_url")) or p.get("source")
        if not mesh or not os.path.isfile(mesh):
            raise ValueError("pick a model to rig: a GLB from the 3D library or an uploaded file")
        job.set_progress(3, "Rigging", "Make-It-Animatable predicts joints, weights and the T-pose", force=True)

        def rig_fn(mesh_path, npz):
            anim.request({"cmd": "rig", "id": job.id, "mesh": mesh_path, "out_npz": npz, "no_fingers": bool(p.get("no_fingers"))},
                         events(3, rig_hi * 0.4, "Rigging"), should_cancel=cancel)
            job.set_progress(rig_hi * 0.4, "Building the rig", "Blender: armature, skin, T-pose, GLB + FBX", force=True)
        folder = anim_cli.rig_character(mesh, name, _float(p.get("height"), 1.8), bool(p.get("no_fingers")),
                                        bool(p.get("keep_pose")), out_root=paths.CHARACTERS, log=job.add_log, rig_fn=rig_fn)
        job.set_progress(rig_hi, "Rigged", "", force=True)
    if do_anim:
        if not os.path.isfile(os.path.join(folder, name + ".blend")):
            raise RuntimeError(f"{name} is not rigged yet: rig it first")
        n, done = len(clips), [0]
        lo0 = rig_hi if do_rig else 2
        span = (92 - lo0) / n

        def motion_fn(prompts, seconds, out_stem, seed):
            lo = lo0 + span * done[0]
            label = f"Clip {os.path.basename(out_stem)} ({done[0] + 1}/{n})"
            job.set_progress(lo, label, "Kimodo", force=True)
            anim.request({"cmd": "motion", "id": job.id, "prompts": prompts, "seconds": seconds, "out_stem": out_stem,
                          "seed": seed, "steps": _int(p.get("steps"), 100)}, events(lo, lo + span * 0.95, label), should_cancel=cancel)
            done[0] += 1
        anim_cli.animate_character(name, clips, _float(p.get("seconds"), 4.0), _int(p.get("seed")), _int(p.get("steps"), 100),
                                   bool(p.get("inplace")), out_root=paths.CHARACTERS, log=job.add_log, motion_fn=motion_fn,
                                   on_retarget=lambda: job.set_progress(92, "Retargeting", "Blender: clips onto the rig, GLB + FBX", force=True))
    meta = anim_cli.read_meta(folder)
    job.meta.update(name=name, folder=folder, rig_stats=meta.get("rig_stats"), clip_names=[c["name"] for c in meta.get("clips", [])])
    for key, fn in (("glb", name + ".glb"), ("fbx", name + ".fbx"), ("diffuse", name + "Diffuse.png"), ("blend", name + ".blend")):
        fp = os.path.join(folder, fn)
        if os.path.isfile(fp):
            job.outputs[key] = {"path": fp, "url": file_url(fp)}
    job.outputs["clips"] = [{"name": c["name"], "prompt": c.get("prompt"), "seconds": c.get("seconds"), "seed": c.get("seed"),
                             "bvh": file_url(c["bvh"]) if c.get("bvh") else None} for c in meta.get("clips", [])]
    job.set_progress(100, "Done", "", force=True)


# ---------------------------------------------------------------------------------------------
# painting a modelled prop (ai-3d/scripts/paint.py: StableGen in Blender, through ComfyUI)

def flow_paint(job):
    import paint as genpaint   # ai-3d/scripts
    p = job.params
    glb = p.get("model_upload") or url_to_path(p.get("model_url"))
    picture = p.get("picture_upload") or url_to_path(p.get("picture_url"))
    if not glb or not os.path.isfile(glb):
        raise ValueError("pick the modelled GLB to paint: from the 3D library or a file")
    if not picture or not os.path.isfile(picture):
        raise ValueError("pick the reference picture the prop was modelled from")
    seed = _int(p.get("seed"))
    if seed is None:
        seed = random.randint(0, 2**31)
        p["seed"] = seed
    name = unique_name(ASSETS, p.get("name") or os.path.splitext(os.path.basename(glb))[0], [".glb", ".fbx"])
    job.meta["name"] = name
    lock = [s.strip() for s in (p.get("lock") or "").split(",") if s.strip()]
    prompt = " | ".join(s.strip() for s in (p.get("prompt") or "", p.get("front") or "", p.get("plain") or ""))
    ensure_comfy(job)
    for w in (tts, music, sfx, anim):   # StableGen's views run in ComfyUI: the GPU belongs to it
        w.unload()
    job.outputs["reference"] = {"path": picture, "url": file_url(picture)}

    def run_api(api):
        hist, runner = run_comfy(job, api, (2, 6))
        return [path for nid, path in history_files(hist, kinds=("images",))]

    def on_file(label, path):
        url = file_url(path)
        # the mask also comes through the ComfyUI runner
        if url and not any(os.path.abspath(i.get("path") or "") == os.path.abspath(path) for i in job.intermediates):
            job.add_intermediate(label, "image", path, url)

    out = genpaint.paint(glb, picture, prompt, name, ASSETS, lock, bool(p.get("low")), seed, _float(p.get("ipadapter"), 0.8),
                         _float(p.get("discard"), 65.0), _int(p.get("resolution"), 1024), _float(p.get("size")),
                         p.get("fbx", True) is not False, upload=upload_to_comfy, run_api=run_api, log=job.add_log,
                         on_progress=lambda pct, phase, detail="": job.set_progress(pct, phase, detail, force=True),
                         on_file=on_file, should_cancel=lambda: job.cancel_requested)
    job.meta.update(fit={k: round(out["fit"][k], 3) for k in ("iou", "yaw", "elev", "fov")}, tris=out["bake"]["tris"],
                    unseen_texels=out["bake"]["unseen_texels"], locked=out["bake"]["locked"], workdir=out["workdir"])
    job.outputs["views"] = [{"path": v, "url": file_url(v)} for v in out["views"]]
    job.outputs["diffuse"] = {"path": out["diffuse"], "url": file_url(out["diffuse"])}
    if out.get("fbx"):
        job.outputs["fbx"] = {"path": out["fbx"], "url": file_url(out["fbx"])}
        job.meta["fbx_stats"] = out.get("fbx_stats")
    job.outputs["glb"] = {"path": out["glb"], "url": file_url(out["glb"])}
    job.add_log(f"GLB: {out['glb']}")
    job.set_progress(100, "Done", "")


# ---------------------------------------------------------------------------------------------
# text -> modelled + painted prop: reference picture (Z-Image), Claude models it in Blender (prop_model.py), paint

def flow_prop(job):
    import paint as genpaint     # ai-3d/scripts
    import prop_model
    p = job.params
    prompt = (p.get("prompt") or "").strip()
    seed = _int(p.get("seed"))
    if seed is None:
        seed = random.randint(0, 2**31)
        p["seed"] = seed
    if not prop_model.claude_cli():
        raise RuntimeError("Claude Code CLI not found: install Claude Code and log in (or set CLAUDE_CLI)")
    modelled = os.path.join(ASSETS, "modelled")
    name = unique_name(ASSETS, p.get("name") or gen3d.asset_name(prompt), [".glb", ".fbx"])
    while os.path.exists(os.path.join(modelled, name)):
        name = unique_name(ASSETS, name + "_2", [".glb", ".fbx"])
    job.meta["name"] = name
    workdir = os.path.join(modelled, name)
    rounds = _int(p.get("rounds"), 6)

    # 1. the reference picture: given, or drawn like the 3D Models image stage
    picture = p.get("reference_upload") or url_to_path(p.get("reference_url"))
    if picture and not os.path.isfile(picture):
        raise ValueError(f"reference picture not found: {picture}")
    if not picture:
        api, info = gen3d.build_api(prompt, "gameasset", bool(p.get("raw")), seed, image_only=True)
        hist, runner = run_comfy(job, api, (0, 8))
        for nid, path in history_files(hist, kinds=("images",)):
            if api.get(nid, {}).get("class_type") == "SaveImage" and "reference" in api[nid]["_meta"]["title"].lower():
                picture = path
        if not picture:
            raise ComfyError("no reference picture in the ComfyUI output")
    job.outputs["reference"] = {"path": picture, "url": file_url(picture)}
    job.publish(force=True)

    # 2. Claude models it: a prop_kit script, built and compared with the picture round by round
    job.set_progress(10, "Claude is modelling", "reading the picture", force=True)
    seen = {"round": 0}

    def on_event(kind, **d):
        if kind == "text":
            job.add_log("claude: " + d["text"].replace("\n", " ")[:300])
        elif kind == "write":
            job.set_progress(None, "Claude is modelling", f"writing {os.path.basename(d['path'])}", force=True)
        elif kind == "check":
            seen["round"] = d["round"]
            job.set_progress(10 + 45 * min(d["round"], rounds) / rounds, "Claude is modelling",
                             f"building + checking, round {d['round']}", force=True)
        elif kind == "sheet":
            job.add_intermediate(f"Clay check {d['round']}", "image", d["path"], file_url(d["path"]))

    m = prop_model.model_prop(picture, prompt, workdir, name, model=p.get("claude_model", "opus") or None, rounds=rounds,
                              effort=p.get("effort") or None, on_event=on_event, should_cancel=lambda: job.cancel_requested)
    paint = m["paint"]
    job.meta.update(paint=paint, model_rounds=len(m["sheets"]), model_seconds=m["seconds"], claude_cost_usd=m["cost_usd"],
                    script=m["script"])
    job.add_intermediate("Modelled (clay)", "model", m["glb"], file_url(m["glb"]))
    job.add_log(f"Claude modelled it in {m['seconds']} s, {len(m['sheets'])} check rounds"
                + (f", ${m['cost_usd']:.2f} of usage" if m.get("cost_usd") else "") + f": {paint.get('notes', '')}")

    # 3. paint it from the same picture with the settings Claude chose
    def on_file(label, path):
        url = file_url(path)
        if url and not any(os.path.abspath(i.get("path") or "") == os.path.abspath(path) for i in job.intermediates):
            job.add_intermediate(label, "image", path, url)

    def run_api(api):
        hist, runner = run_comfy(job, api, (56, 58))
        return [path for nid, path in history_files(hist, kinds=("images",))]

    paint_prompt = " | ".join(s.strip() for s in (paint.get("prompt") or prompt, paint.get("front") or "", paint.get("plain") or ""))
    out = genpaint.paint(m["glb"], picture, paint_prompt, name, ASSETS, paint.get("lock") or [], bool(paint.get("low")), seed,
                         _float(p.get("ipadapter"), 0.8), _float(p.get("discard"), 65.0), _int(p.get("resolution"), 1024),
                         _float(p.get("size")), p.get("fbx", True) is not False, upload=upload_to_comfy, run_api=run_api,
                         log=job.add_log, on_file=on_file, should_cancel=lambda: job.cancel_requested,
                         on_progress=lambda pct, phase, detail="": job.set_progress(55 + 0.45 * pct, phase, detail, force=True))
    job.meta.update(fit={k: round(out["fit"][k], 3) for k in ("iou", "yaw", "elev", "fov")}, tris=out["bake"]["tris"],
                    unseen_texels=out["bake"]["unseen_texels"], locked=out["bake"]["locked"])
    job.outputs["views"] = [{"path": v, "url": file_url(v)} for v in out["views"]]
    job.outputs["checks"] = [{"path": s, "url": file_url(s)} for s in m["sheets"]]
    job.outputs["script"] = {"path": m["script"], "url": file_url(m["script"])}
    job.outputs["diffuse"] = {"path": out["diffuse"], "url": file_url(out["diffuse"])}
    if out.get("fbx"):
        job.outputs["fbx"] = {"path": out["fbx"], "url": file_url(out["fbx"])}
    job.outputs["glb"] = {"path": out["glb"], "url": file_url(out["glb"])}
    job.add_log(f"GLB: {out['glb']}")
    job.set_progress(100, "Done", "")


FLOWS = {"model3d": flow_model3d, "paint": flow_paint, "prop": flow_prop, "texture": flow_texture, "sprite": flow_sprite, "voice": flow_voice, "voice_prep": flow_voice_prep,
         "music": flow_music, "sfx": flow_sfx, "video": flow_video, "anim": flow_anim}


WORKER_OF = {"voice": tts, "voice_prep": tts, "music": music, "sfx": sfx, "anim": anim}


class Flows:
    def run(self, job):
        try:
            FLOWS[job.kind](job)
        finally:
            # remember how much VRAM the worker keeps while idle, so the next hand-over check can discount it
            w = WORKER_OF.get(job.kind)
            if w is not None and getattr(w, "loaded", False):
                g = gpu.read_once()
                if g.get("available"):
                    w.vram_mb = g["used_mb"]
