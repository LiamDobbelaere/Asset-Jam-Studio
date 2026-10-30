"""Paint a modelled prop from its reference picture: SDXL repaints renders of the model itself from every side, the
views are projected onto the mesh and baked into one diffuse, exported as GLB + FBX.

    paint.bat Model.glb picture.png "shared prompt | front-only details | plain wording" [--name Desk] [--lock Snow,Floor] [--low]

The model comes from a Blender script (prop_kit.py: metres, Z up, front facing -Y, one named material per part with
roughly its colour); the picture is what it was modelled from, e.g. a 3D Models reference image. Needs ComfyUI running
and setup_paint.ps1.
Steps: background mask of the picture (BiRefNet) -> camera fit (paint_fit.py) -> guides: the model in its part colours
and its depth from each camera (paint_guides.py) -> one SDXL img2img per view in ComfyUI: RealVisXL + Lightning LoRA,
depth ControlNet, the picture as IP-Adapter, starting from the guide so every view keeps the same layout of parts and
colours -> projection + bake, part locks (paint_project.py) -> FBX + <Name>Diffuse.png. ~1-2 min.
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


def view_api(guide, depth, picture, text, seed, prefix, denoise=0.7, depth_strength=0.8, ipadapter=0.6):
    """ComfyUI prompt: one view, SDXL img2img from the guide render (uploaded names), held to the depth map, styled by
    the picture. 8 Lightning steps, the sampler settings StableGen's ARCHITECTURE preset uses."""
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "_meta": {"title": "Load RealVisXL"}, "inputs": {"ckpt_name": MODELS["checkpoint"][1]}},
        "2": {"class_type": "LoraLoader", "_meta": {"title": "Lightning LoRA"},
              "inputs": {"lora_name": MODELS["lightning_lora"][1], "strength_model": 1.0, "strength_clip": 1.0, "model": ["1", 0], "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "_meta": {"title": "Prompt"}, "inputs": {"clip": ["2", 1], "text": text}},
        "4": {"class_type": "CLIPTextEncode", "_meta": {"title": "Negative"}, "inputs": {"clip": ["2", 1], "text": NEGATIVE}},
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


def safe_name(s, fallback="Prop"):
    s = re.sub(r"[^A-Za-z0-9_\-]+", "", s or "")
    return s or fallback


def _blender(args, what):
    r = subprocess.run([BLENDER, "-b", "--python-exit-code", "1"] + args, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise RuntimeError(f"{what} failed:\n" + (r.stdout + r.stderr)[-1500:])
    return r.stdout


def paint(glb, picture, prompt, name=None, out_dir=None, lock=(), low=False, seed=None, ipadapter=0.6, denoise=None,
          depth=0.8, resolution=1024, size=None, fbx=True, upload=_cli_upload, run_api=_cli_run, log=print,
          on_progress=lambda pct, phase, detail="": None, on_file=lambda label, path: None, should_cancel=lambda: False):
    """Paint glb from picture; returns a dict with glb, diffuse, fbx (or None), views, guides, workdir, fit, bake.
    prompt: "shared | front-only details | plain wording for the other views"; lock: material names of plain parts.
    denoise: how far each view may move away from the guide (default 0.7, 0.85 for a model with a single material,
    whose guide is just grey). upload(path, name) -> ComfyUI input name, run_api(api) -> saved image paths: the studio
    passes its own; on_file(label, path) reports the mask, the guides, each painted view and the diffuse."""
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

    def check_cancel():
        if should_cancel():
            raise RuntimeError("cancelled")

    on_progress(2, "Camera fit", "background mask of the picture (BiRefNet)")
    tag = uuid.uuid4().hex[:6]
    pic_name = upload(picture, f"paint_{name}_{tag}.png")
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
    outp = _blender(["--python", os.path.join(SCRIPTS, "paint_guides.py"), "--", src, camf, gdir] + (["low"] if low else []),
                    "rendering the guides")
    nviews, nparts = map(int, next(ln for ln in outp.splitlines() if ln.startswith("GUIDES ")).split()[1:3])
    if denoise is None:
        denoise = 0.7 if nparts > 1 else 0.85
    log(f"{nviews} views, {nparts} part colour{'s' if nparts != 1 else ''} in the guide, denoise {denoise}, "
        f"depth {depth}, IP-Adapter {ipadapter}, seed {seed}")
    cams = json.load(open(os.path.join(gdir, "cams.json")))
    on_file("Guide (front)", os.path.join(gdir, "guide_0.png"))

    t0 = time.time()
    for i, c in enumerate(cams):
        check_cancel()
        on_progress(12 + 70 * i / nviews, "Painting views", f"{i + 1}/{nviews}: {c['what']}")
        detail = front if i == 0 else plain
        text = f"{c['what']}{', ' + detail if detail else ''}, {shared}, plain light grey background"
        g = upload(os.path.join(gdir, f"guide_{i}.png"), f"paint_{name}_{tag}_g{i}.png")
        d = upload(os.path.join(gdir, f"depth_{i}.png"), f"paint_{name}_{tag}_d{i}.png")
        files = run_api(view_api(g, d, pic_name, text, seed + i, f"paint/{os.path.basename(work)}/view{i}",
                                 denoise, depth, ipadapter))
        if not files:
            raise RuntimeError(f"ComfyUI returned no image for view {i + 1}")
        shutil.copyfile(files[0], os.path.join(vdir, f"view_{i}.png"))
        on_file(f"View {i + 1}", files[0])
    log(f"painted {nviews} views in {time.time() - t0:.0f} s")

    check_cancel()
    on_progress(84, "Baking", "projecting the views, unseen texels from the part colours" + (", parts locked" if lock else ""))
    outp = _blender(["--python", os.path.join(SCRIPTS, "paint_project.py"), "--", src, gdir, vdir, work, name,
                     ",".join(lock), str(resolution)], "projecting the views")
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
           "guides": [os.path.join(gdir, f"guide_{i}.png") for i in range(nviews)], "denoise": denoise}
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
    ap.add_argument("--depth", type=float, default=0.8, help="depth ControlNet strength")
    ap.add_argument("--ipadapter", type=float, default=0.6, help="IP-Adapter strength (how much the picture styles every view)")
    ap.add_argument("--res", type=int, default=1024, help="diffuse size")
    ap.add_argument("--size", type=float, help="longest dimension in metres for the FBX (default: as modelled)")
    ap.add_argument("--no-fbx", action="store_true")
    a = ap.parse_args()
    out = paint(a.glb, a.picture, a.prompt, a.name, a.out, [s for s in a.lock.split(",") if s], a.low, a.seed,
                a.ipadapter, a.denoise, a.depth, a.res, a.size, not a.no_fbx,
                on_progress=lambda pct, phase, detail="": print(f"{pct:5.1f}%  {phase}  {detail}", flush=True))
    print(json.dumps({k: v for k, v in out.items() if k not in ("views", "guides")}, indent=1))


if __name__ == "__main__":
    main()
