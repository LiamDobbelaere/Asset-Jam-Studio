"""Paint a modelled prop from its reference picture: SDXL repaints renders of the model itself from every side, the
views are projected onto the mesh and baked into one diffuse, exported as GLB + FBX.

    paint.bat Model.glb picture.png "shared prompt | front-only details | plain wording" [--name Desk] [--lock Snow,Floor] [--low] [--style inked]

The model comes from a Blender script (prop_kit.py: metres, Z up, front facing -Y, one named material per part with
roughly its colour); the picture is what it was modelled from, e.g. a 3D Models reference image. Needs ComfyUI running
and setup_paint.ps1.
Steps: background mask of the picture (BiRefNet) -> camera fit (paint_fit.py) -> guides: the model in its part colours
and its depth from each camera (paint_guides.py) -> one SDXL img2img per view in ComfyUI: RealVisXL + Lightning LoRA,
depth ControlNet, the picture as IP-Adapter, starting from the guide so every view keeps the same layout of parts and
colours -> projection + bake, part locks (paint_project.py) -> FBX + <Name>Diffuse.png. ~1-2 min.
--style <id> paints in a style guide (ai-3d/styles/<id>.json, prop_style.py): its prompt and negative words go to every
view, it sets the IP-Adapter / denoise / depth defaults, and its finish restyles the bake (flat colours, painted
shading, ink lines on the hard edges). A style can paint the views with FLUX.2 klein instead ("painter": "klein"):
img2img from the guide like SDXL, but klein follows "hand-painted, flat colours, outlines" where RealVisXL keeps
drifting to glossy photo materials. klein has no depth ControlNet, so the denoise holds the layout: at 0.75 the
views keep the guide's silhouette (overlap 0.95-0.99), at 0.85 a back view turned into a second front. The other
views take "denoise_other" (0.6): at 0.75 klein painted a vending machine's window and buttons on its plain back.
Generating the views from noise instead (StableGen, the first version) gave every view its own idea of the object, and
projected together they smeared: a snowmobile got a second seat in one view and lost its track in the bake.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import time
import uuid

import generate as gen3d
import prop_style

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
COMFY = os.path.join(ROOT, "ComfyUI")
COMFY_PY = os.path.join(COMFY, ".venv", "Scripts", "python.exe")
WORK = os.path.join(COMFY, "output", "paint")
BLENDER = gen3d.BLENDER
MODELS = {"checkpoint": ("checkpoints", "RealVisXL_V5.0_fp16.safetensors"),
          "lightning_lora": ("loras", "sdxl_lightning_8step_lora.safetensors"),
          "depth_controlnet": ("controlnet", "controlnet_depth_sdxl.safetensors"),
          "ipadapter": ("ipadapter", "ip-adapter-plus_sdxl_vit-h.safetensors"),
          "clip_vision": ("clip_vision", "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors")}
KLEIN = {"unet": ("diffusion_models", "flux-2-klein-4b-fp8.safetensors"), "clip": ("text_encoders", "qwen_3_4b_fp8_mixed.safetensors"),
         "vae": ("vae", "flux2-vae.safetensors")}
NEGATIVE = ("photo, photorealistic, text, watermark, harsh shadows, specular highlights, reflections, dirt, noise, blurry, "
            "background clutter")


def readiness():
    ok = {k: os.path.isfile(os.path.join(COMFY, "models", d, f)) for k, (d, f) in MODELS.items()}
    ok["ipadapter_nodes"] = os.path.isdir(os.path.join(COMFY, "custom_nodes", "ComfyUI_IPAdapter_plus"))
    ok["blender"] = os.path.isfile(BLENDER)
    ok["ready"] = all(ok.values())
    return ok


def mask_api(image_name, prefix):
    """ComfyUI prompt: the picture's object mask (BiRefNet), saved as a PNG."""
    return {
        "1": {"class_type": "LoadImage", "_meta": {"title": "Reference picture"}, "inputs": {"image": image_name}},
        "2": {"class_type": "LoadBackgroundRemovalModel", "_meta": {"title": "Load BiRefNet"},
              "inputs": {"bg_removal_name": "birefnet.safetensors"}},
        "3": {"class_type": "RemoveBackground", "_meta": {"title": "Remove background"},
              "inputs": {"bg_removal_model": ["2", 0], "image": ["1", 0]}},
        "4": {"class_type": "MaskToImage", "_meta": {"title": "Mask as image"}, "inputs": {"mask": ["3", 0]}},
        "5": {"class_type": "SaveImage", "_meta": {"title": "Picture mask"}, "inputs": {"images": ["4", 0], "filename_prefix": prefix}},
    }


def view_api(guide, depth, picture, text, seed, prefix, denoise=0.7, depth_strength=0.8, ipadapter=0.6, negative=NEGATIVE):
    """ComfyUI prompt: one view, SDXL img2img from the guide render (uploaded names), held to the depth map, styled by
    the picture. 8 Lightning steps, the sampler settings StableGen's ARCHITECTURE preset uses."""
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "_meta": {"title": "Load RealVisXL"}, "inputs": {"ckpt_name": MODELS["checkpoint"][1]}},
        "2": {"class_type": "LoraLoader", "_meta": {"title": "Lightning LoRA"},
              "inputs": {"lora_name": MODELS["lightning_lora"][1], "strength_model": 1.0, "strength_clip": 1.0, "model": ["1", 0], "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "_meta": {"title": "Prompt"}, "inputs": {"clip": ["2", 1], "text": text}},
        "4": {"class_type": "CLIPTextEncode", "_meta": {"title": "Negative"}, "inputs": {"clip": ["2", 1], "text": negative}},
        "5": {"class_type": "IPAdapterUnifiedLoader", "_meta": {"title": "IP-Adapter"}, "inputs": {"preset": "PLUS (high strength)", "model": ["2", 0]}},
        "6": {"class_type": "LoadImage", "_meta": {"title": "Reference picture"}, "inputs": {"image": picture}},
        "7": {"class_type": "IPAdapter", "_meta": {"title": "Style from the picture"},
              "inputs": {"weight": ipadapter, "start_at": 0.0, "end_at": 1.0, "weight_type": "style transfer",
                         "model": ["5", 0], "ipadapter": ["5", 1], "image": ["6", 0]}},
        "8": {"class_type": "LoadImage", "_meta": {"title": "Depth"}, "inputs": {"image": depth}},
        "9": {"class_type": "ControlNetLoader", "_meta": {"title": "Depth ControlNet"}, "inputs": {"control_net_name": MODELS["depth_controlnet"][1]}},
        "10": {"class_type": "ControlNetApplyAdvanced", "_meta": {"title": "Hold to the depth"},
               "inputs": {"strength": depth_strength, "start_percent": 0.0, "end_percent": 1.0, "positive": ["3", 0],
                          "negative": ["4", 0], "control_net": ["9", 0], "image": ["8", 0], "vae": ["1", 2]}},
        "11": {"class_type": "LoadImage", "_meta": {"title": "Guide (part colours)"}, "inputs": {"image": guide}},
        "12": {"class_type": "VAEEncode", "_meta": {"title": "Encode guide"}, "inputs": {"pixels": ["11", 0], "vae": ["1", 2]}},
        "13": {"class_type": "KSampler", "_meta": {"title": "Paint view"},
               "inputs": {"seed": seed, "steps": 8, "cfg": 1.5, "sampler_name": "dpmpp_2s_ancestral", "scheduler": "sgm_uniform",
                          "denoise": denoise, "model": ["7", 0], "positive": ["10", 0], "negative": ["10", 1], "latent_image": ["12", 0]}},
        "14": {"class_type": "VAEDecode", "_meta": {"title": "Decode"}, "inputs": {"samples": ["13", 0], "vae": ["1", 2]}},
        "15": {"class_type": "SaveImage", "_meta": {"title": "Painted view"}, "inputs": {"images": ["14", 0], "filename_prefix": prefix}},
    }


def _klein_lora(api, lora, guider="44"):
    """Put a LoRA (ComfyUI/models/loras file name, or {"file": ..., "strength": ...}) between the klein model and the
    guider, e.g. one trained on a style's own props (style_lora_data.py)."""
    if not lora:
        return
    lora = {"file": lora} if isinstance(lora, str) else lora
    api["80"] = {"class_type": "LoraLoaderModelOnly", "_meta": {"title": "Style LoRA"},
                 "inputs": {"lora_name": lora["file"], "strength_model": float(lora.get("strength", 1.0)), "model": ["1", 0]}}
    api[guider]["inputs"]["model"] = ["80", 0]


def klein_view_api(guide, picture, text, seed, prefix, denoise=0.75, steps=8, lora=None):
    """ComfyUI prompt: one view repainted by FLUX.2 klein 4B (the sprite flow's edit workflow) as img2img: the guide is
    image 1 and the starting latent, the picture (or None) image 2 for the style. The distilled model's 8-step
    schedule is cut to the last `denoise` part, so the guide's layout stays put."""
    api = json.load(open(os.path.join(ROOT, "workflows", "sprite_edit_api.json"), encoding="utf-8"))
    api["1"]["inputs"]["unet_name"] = KLEIN["unet"][1]
    api["5"]["inputs"]["text"] = text
    api["10"]["inputs"]["image"] = guide
    api["11"]["inputs"]["megapixels"] = 1.0
    api["43"]["inputs"]["noise_seed"] = seed
    api["41"]["inputs"]["steps"] = steps
    if picture:
        api["20"]["inputs"]["image"] = picture
        api["44"]["inputs"].update(positive=["23", 0], negative=["24", 0], cfg=1.0)
    else:
        api["44"]["inputs"].update(positive=["14", 0], negative=["15", 0], cfg=1.0)
    api["70"] = {"class_type": "SplitSigmasDenoise", "_meta": {"title": "img2img from the guide"},
                 "inputs": {"sigmas": ["41", 0], "denoise": denoise}}
    api["45"]["inputs"].update(latent_image=["13", 0], sigmas=["70", 1])
    api["47"]["inputs"]["filename_prefix"] = prefix
    _klein_lora(api, lora)
    return gen3d.prune_to(api, ["47"])


def klein_picture_api(refs, text, seed, prefix, size=1024, steps=4, lora=None):
    """ComfyUI prompt: a new picture drawn by FLUX.2 klein 4B from one or two reference pictures (uploaded names, the
    style's own props) as images 1 and 2, on an empty size x size canvas: a new object in their style."""
    api = json.load(open(os.path.join(ROOT, "workflows", "sprite_edit_api.json"), encoding="utf-8"))
    api["1"]["inputs"]["unet_name"] = KLEIN["unet"][1]
    api["5"]["inputs"]["text"] = text
    api["10"]["inputs"]["image"] = refs[0]
    api["43"]["inputs"]["noise_seed"] = seed
    api["40"]["inputs"].update(width=size, height=size)
    api["41"]["inputs"].update(steps=steps, width=size, height=size)
    if len(refs) > 1:
        api["20"]["inputs"]["image"] = refs[1]
        api["44"]["inputs"].update(positive=["23", 0], negative=["24", 0], cfg=1.0)
    else:
        api["44"]["inputs"].update(positive=["14", 0], negative=["15", 0], cfg=1.0)
    api["47"]["inputs"]["filename_prefix"] = prefix
    _klein_lora(api, lora)
    return gen3d.prune_to(api, ["47"])


def klein_t2i_api(text, seed, prefix, size=1024, steps=4, lora=None):
    """ComfyUI prompt: a picture drawn by FLUX.2 klein 4B from text alone (the sprite workflow's text branch), with an
    optional LoRA, e.g. one trained on a style's own props (style_lora_data.py --picture)."""
    api = json.load(open(os.path.join(ROOT, "workflows", "sprite_edit_api.json"), encoding="utf-8"))
    api["1"]["inputs"]["unet_name"] = KLEIN["unet"][1]
    api["5"]["inputs"]["text"] = text
    api["43"]["inputs"]["noise_seed"] = seed
    api["60"]["inputs"].update(width=size, height=size)
    api["61"]["inputs"].update(steps=steps, width=size, height=size)
    api["62"]["inputs"].update(negative=["6", 0], cfg=1.0)
    api["65"]["inputs"]["filename_prefix"] = prefix
    _klein_lora(api, lora, guider="62")
    return gen3d.prune_to(api, ["65"])


def _cli_upload(path, name):
    """Upload to ComfyUI's input folder (multipart, like the browser does). Returns the stored name."""
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        data = f.read()
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            f"Content-Type: image/png\r\n\r\n").encode() + data + \
        f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{boundary}--\r\n".encode()
    import urllib.request
    req = urllib.request.Request(gen3d.SERVER + "/upload/image", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["name"]


def _cli_run(api):
    """Queue a prompt and wait; returns the absolute paths of the images it saved."""
    pid = gen3d.post("/prompt", {"prompt": api, "client_id": str(uuid.uuid4())})["prompt_id"]
    t0 = time.time()
    while time.time() - t0 < 600:
        hist = gen3d.get(f"/history/{pid}")
        if pid in hist:
            break
        time.sleep(1)
    else:
        raise RuntimeError("ComfyUI did not finish within 10 minutes")
    return [os.path.join(COMFY, "output", it.get("subfolder", ""), it["filename"])
            for o in hist[pid].get("outputs", {}).values() for it in o.get("images", [])]


def _save_view(src, dst, res):
    """Copy a painted view, scaled to the guide's resolution if the model returned another size."""
    from PIL import Image
    im = Image.open(src)
    if im.size != (res, res):
        im.convert("RGB").resize((res, res), Image.LANCZOS).save(dst)
    else:
        shutil.copyfile(src, dst)


def safe_name(s, fallback="Prop"):
    s = re.sub(r"[^A-Za-z0-9_\-]+", "", s or "")
    return s or fallback


def _blender(args, what):
    r = subprocess.run([BLENDER, "-b", "--python-exit-code", "1"] + args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise RuntimeError(f"{what} failed:\n" + (r.stdout + r.stderr)[-1500:])
    return r.stdout


def paint(glb, picture, prompt, name=None, out_dir=None, lock=(), low=False, seed=None, ipadapter=None, denoise=None,
          depth=None, resolution=1024, size=None, fbx=True, upload=_cli_upload, run_api=_cli_run, log=print,
          on_progress=lambda pct, phase, detail="": None, on_file=lambda label, path: None, should_cancel=lambda: False,
          style=None, refs=()):
    """Paint glb from picture; returns a dict with glb, diffuse, fbx (or None), views, guides, workdir, fit, bake.
    prompt: "shared | front-only details | plain wording for the other views"; lock: material names of plain parts.
    denoise: how far each view may move away from the guide (default 0.7, 0.85 for a model with a single material,
    whose guide is just grey). upload(path, name) -> ComfyUI input name, run_api(api) -> saved image paths: the studio
    passes its own; on_file(label, path) reports the mask, the guides, each painted view and the diffuse.
    style: a style guide id (prop_style.py) or None; ipadapter / denoise / depth left None take its values.
    refs: pictures of the style's own props (prop_refs.front_render) for a klein style with "ref_views": "all": the
    first is image 2 of every view, "paint it like this", in place of the picture."""
    st = prop_style.load(style) if isinstance(style, str) or style is None else style
    sp = st["paint"] if st else {}
    painter = sp.get("painter", "sdxl")
    if painter == "klein":   # FLUX.2 klein (setup.ps1 installs it for the sprite flow) instead of the SDXL stack
        missing = [f for d, f in KLEIN.values() if not os.path.isfile(os.path.join(COMFY, "models", d, f))]
        missing += [] if os.path.isfile(BLENDER) else ["blender"]
        if missing:
            raise RuntimeError(f"the {st['id']} style paints with FLUX.2 klein, which is not installed ({', '.join(missing)}): "
                               "run ai-3d/setup.ps1")
    else:
        rd = readiness()
        missing = [k for k, v in rd.items() if k != "ready" and not v]
        if missing:
            raise RuntimeError(f"painting is not set up ({', '.join(missing)} missing): run ai-3d/setup_paint.ps1")
    name = safe_name(name or os.path.splitext(os.path.basename(glb))[0])
    out_dir = out_dir or os.path.join(ROOT, "assets")
    work = os.path.join(WORK, f"{name}_{time.strftime('%Y%m%d-%H%M%S')}")
    gdir, vdir = os.path.join(work, "guides"), os.path.join(work, "views")
    os.makedirs(vdir, exist_ok=True)
    src = os.path.join(work, name + "_model.glb")
    shutil.copyfile(glb, src)
    shared, front, plain = ([p.strip() for p in prompt.split("|")] + ["", ""])[:3]
    seed = 1000 if seed is None else int(seed)
    ipadapter = sp.get("ipadapter", 0.6) if ipadapter is None else ipadapter
    depth = sp.get("depth", 0.8) if depth is None else depth
    denoise = sp.get("denoise") if denoise is None else denoise
    negative = NEGATIVE + (", " + sp["negative"] if sp.get("negative") else "")
    finish = dict(sp.get("finish") or {})
    if sp.get("scale"):   # the style's unit scale (its props are modelled at real size), applied after the bake
        finish["scale"] = sp["scale"]

    def check_cancel():
        if should_cancel():
            raise RuntimeError("cancelled")

    on_progress(2, "Camera fit", "background mask of the picture (BiRefNet)")
    tag = uuid.uuid4().hex[:6]
    pic_name = upload(picture, f"paint_{name}_{tag}.png")
    ref_name = upload(refs[0], f"paint_{name}_{tag}_ref.png") if refs and painter == "klein" and sp.get("ref_views") == "all" else None
    masks = run_api(mask_api(pic_name, f"paint/{os.path.basename(work)}/mask"))
    if not masks:
        raise RuntimeError("ComfyUI returned no mask for the picture")
    on_file("Picture mask", masks[0])
    on_progress(6, "Camera fit", "matching the model's silhouette to the picture")
    camf = os.path.join(work, "camera.json")
    r = subprocess.run([COMFY_PY, "-I", os.path.join(SCRIPTS, "paint_fit.py"), src, masks[0], camf],
                       capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise RuntimeError("camera fit failed:\n" + r.stderr[-1500:])
    fit = json.load(open(camf))
    log(f"camera fit: silhouette overlap {fit['iou']:.2f}, yaw {fit['yaw']:.0f}, elevation {fit['elev']:.0f}"
        + ("  (low: does the model match the picture, front facing -Y?)" if fit["iou"] < 0.5 else ""))
    check_cancel()

    on_progress(9, "Guides", "the model in its part colours and its depth, from every camera")
    outp = _blender(["--python", os.path.join(SCRIPTS, "paint_guides.py"), "--", src, camf, gdir] + (["low"] if low else [])
                    + (["flat"] if sp.get("guide_light") == "flat" else []), "rendering the guides")
    nviews, nparts = map(int, next(ln for ln in outp.splitlines() if ln.startswith("GUIDES ")).split()[1:3])
    if denoise is None:
        denoise = 0.7 if nparts > 1 else 0.85
    log(f"{nviews} views, {nparts} part colour{'s' if nparts != 1 else ''} in the guide, denoise {denoise}"
        + (f" (other views {sp['denoise_other']})" if painter == "klein" and sp.get("denoise_other") else "") + ", "
        + (f"depth {depth}, IP-Adapter {ipadapter}" if painter == "sdxl" else "FLUX.2 klein") + f", seed {seed}"
        + (f", style {st['id']}" if st else ""))
    cams = json.load(open(os.path.join(gdir, "cams.json")))
    on_file("Guide (front)", os.path.join(gdir, "guide_0.png"))

    t0 = time.time()
    for i, c in enumerate(cams):
        check_cancel()
        on_progress(12 + 70 * i / nviews, "Painting views", f"{i + 1}/{nviews}: {c['what']}")
        detail = front if i == 0 else plain
        g = upload(os.path.join(gdir, f"guide_{i}.png"), f"paint_{name}_{tag}_g{i}.png")
        prefix = f"paint/{os.path.basename(work)}/view{i}"
        if painter == "klein":
            # an instruction; the picture only for its own view: with it on the back view, klein painted the
            # vending machine's window and buttons there too. A style reference (another prop of the style) is image 2
            # of every view instead: it carries the look, not this object's details
            if ref_name:
                text = (f"Repaint image 1 as a finished hand-painted 3D game prop in exactly the art style of image 2 (the "
                        f"same painting, colours, shading and outlines; image 2 is a different object): {shared}, seen in a "
                        f"{c['what']}{', ' + detail if detail else ''}. Paint only the details image 1 shows. ")
            elif i == 0:
                text = (f"Repaint image 1 as a finished hand-painted 3D game prop in the art style of image 2: {shared}, "
                        f"seen in a {c['what']}{', ' + detail if detail else ''}. ")
            else:
                text = (f"Repaint image 1 as a finished hand-painted 3D game prop: {shared}, seen in a {c['what']}"
                        f"{', ' + detail if detail else ''}. Paint only the details image 1 shows and keep every surface "
                        "that is plain in image 1 plain: no windows, displays, labels, screens or buttons that are not in image 1. ")
            text += ("Exactly the same shape, outline, camera angle, framing and part colours as image 1. "
                     + (sp["prompt"] + ", " if sp.get("prompt") else "") + "plain light grey background")
            files = run_api(klein_view_api(g, ref_name or (pic_name if i == 0 else None), text, seed + i, prefix,
                                           denoise if i == 0 else sp.get("denoise_other", denoise), lora=sp.get("lora")))
        else:
            text = f"{c['what']}{', ' + detail if detail else ''}, {shared}, {sp['prompt'] + ', ' if sp.get('prompt') else ''}plain light grey background"
            d = upload(os.path.join(gdir, f"depth_{i}.png"), f"paint_{name}_{tag}_d{i}.png")
            files = run_api(view_api(g, d, pic_name, text, seed + i, prefix, denoise, depth, ipadapter, negative))
        if not files:
            raise RuntimeError(f"ComfyUI returned no image for view {i + 1}")
        _save_view(files[0], os.path.join(vdir, f"view_{i}.png"), c["res"])
        on_file(f"View {i + 1}", files[0])
    log(f"painted {nviews} views in {time.time() - t0:.0f} s")

    check_cancel()
    on_progress(84, "Baking", "projecting the views, unseen texels from the part colours" + (", parts locked" if lock else "")
                + (f", {st['id']} finish" if finish else ""))
    outp = _blender(["--python", os.path.join(SCRIPTS, "paint_project.py"), "--", src, gdir, vdir, work, name,
                     ",".join(lock), str(resolution), json.dumps(finish) if finish else ""], "projecting the views")
    bake = json.loads(next(ln for ln in outp.splitlines() if ln.startswith("PAINTBAKE {"))[len("PAINTBAKE "):])
    log(f"baked: {bake['tris']} tris, views reached {bake['seen']:.0%} of the surface"
        + (f", locked {', '.join(f'{k} {v}' for k, v in bake['locked'].items())}" if bake["locked"] else ""))
    if bake["missing_locks"]:
        log(f"no part named {', '.join(bake['missing_locks'])} in the model (parts: {', '.join(bake['parts'])})")

    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, name + ".glb")
    shutil.copyfile(bake["glb"], dst)
    diffuse = os.path.join(out_dir, name + "Diffuse.png")
    shutil.copyfile(bake["diffuse"], diffuse)
    on_file("Diffuse", diffuse)
    out = {"glb": dst, "diffuse": diffuse, "fbx": None, "workdir": work, "fit": fit, "bake": bake, "mask": masks[0],
           "views": [os.path.join(vdir, f"view_{i}.png") for i in range(nviews)],
           "guides": [os.path.join(gdir, f"guide_{i}.png") for i in range(nviews)], "denoise": denoise,
           "style": st["id"] if st else None}
    if fbx:
        on_progress(95, "Converting to FBX", "Blender")
        out["fbx"], _, out["fbx_stats"] = gen3d.glb_to_fbx(dst, out_dir, name, size or bake["size"], level=False)
    on_progress(100, "Done", "")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("glb", help="the modelled prop (prop_kit.py export)")
    ap.add_argument("picture", help="the reference picture it was modelled from (PNG)")
    ap.add_argument("prompt", help='"shared | front-only details | plain wording for the other views"')
    ap.add_argument("--name", help="asset name (default: the GLB's)")
    ap.add_argument("--out", default=os.path.join(ROOT, "assets"))
    ap.add_argument("--lock", default="", help="comma-separated material names of plain parts (Snow,Floor)")
    ap.add_argument("--low", action="store_true", help="two extra low views for undersides (chairs, tables)")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--denoise", type=float, help="how far each view may move from the part-colour render (0.7; 0.85 single-material)")
    ap.add_argument("--depth", type=float, help="depth ControlNet strength (0.8)")
    ap.add_argument("--ipadapter", type=float, help="IP-Adapter strength (how much the picture styles every view, 0.6)")
    ap.add_argument("--style", help="style guide id (ai-3d/styles/<id>.json): " + ", ".join(s["id"] for s in prop_style.list_styles()))
    ap.add_argument("--res", type=int, default=1024, help="diffuse size")
    ap.add_argument("--size", type=float, help="longest dimension in metres for the FBX (default: as modelled)")
    ap.add_argument("--no-fbx", action="store_true")
    a = ap.parse_args()
    out = paint(a.glb, a.picture, a.prompt, a.name, a.out, [s for s in a.lock.split(",") if s], a.low, a.seed,
                a.ipadapter, a.denoise, a.depth, a.res, a.size, not a.no_fbx,
                on_progress=lambda pct, phase, detail="": print(f"{pct:5.1f}%  {phase}  {detail}", flush=True), style=a.style)
    print(json.dumps({k: v for k, v in out.items() if k not in ("views", "guides")}, indent=1))


if __name__ == "__main__":
    main()
