"""Paint a modelled prop from its reference picture: StableGen (SDXL + depth ControlNet + IP-Adapter in ComfyUI,
driven from Blender) projects generated views onto the mesh, then one diffuse is baked and exported as GLB + FBX.

    paint.bat Model.glb picture.png "shared prompt | front-only details | plain wording" [--name Desk] [--lock Snow,Floor] [--low]

The model comes from a Blender script (prop_kit.py: metres, Z up, front facing -Y, one named material per part); the
picture is what it was modelled from, e.g. a 3D Models reference image. Needs ComfyUI running and setup_paint.ps1.
Steps: background mask of the picture (BiRefNet in ComfyUI) -> camera fit (paint_fit.py) -> StableGen in a Blender
window, ~2-3 min (paint_drive.py) -> bake, fill, part locks (paint_bake.py, headless) -> FBX + <Name>Diffuse.png.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid

import generate as gen3d

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
COMFY = os.path.join(ROOT, "ComfyUI")
COMFY_PY = os.path.join(COMFY, ".venv", "Scripts", "python.exe")
PROFILE = os.path.join(ROOT, "blender")   # BLENDER_USER_RESOURCES holding the StableGen add-on (setup_paint.ps1)
ADDON_DIR = os.path.join(PROFILE, "extensions", "user_default", "stablegen")
CHECKPOINT = "RealVisXL_V5.0_fp16.safetensors"
WORK = os.path.join(COMFY, "output", "paint")
BLENDER = gen3d.BLENDER


def readiness():
    ok = {"blender": os.path.isfile(BLENDER), "stablegen": os.path.isdir(ADDON_DIR),
          "checkpoint": os.path.isfile(os.path.join(COMFY, "models", "checkpoints", CHECKPOINT)),
          "ipadapter_nodes": os.path.isdir(os.path.join(COMFY, "custom_nodes", "ComfyUI_IPAdapter_plus"))}
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
        raise RuntimeError("ComfyUI did not finish the mask in 10 minutes")
    return [os.path.join(COMFY, "output", it.get("subfolder", ""), it["filename"])
            for o in hist[pid].get("outputs", {}).values() for it in o.get("images", [])]


def safe_name(s, fallback="Prop"):
    s = re.sub(r"[^A-Za-z0-9_\-]+", "", s or "")
    return s or fallback


def paint(glb, picture, prompt, name=None, out_dir=None, lock=(), low=False, seed=None, ipadapter=0.8, discard=65.0,
          resolution=1024, size=None, fbx=True, upload=_cli_upload, run_api=_cli_run, log=print,
          on_progress=lambda pct, phase, detail="": None, on_file=lambda label, path: None, should_cancel=lambda: False):
    """Paint glb from picture; returns a dict with glb, diffuse, fbx (or None), views, workdir, fit, bake.
    prompt: "shared | front-only details | plain wording for the other views"; lock: material names of plain parts.
    upload(path, name) -> ComfyUI input name, run_api(api) -> saved image paths: the studio passes its own;
    on_file(label, path) reports the mask, each painted view and the diffuse as they appear."""
    rd = readiness()
    missing = [k for k, v in rd.items() if k != "ready" and not v]
    if missing:
        raise RuntimeError(f"painting is not set up ({', '.join(missing)} missing): run ai-3d/setup_paint.ps1")
    name = safe_name(name or os.path.splitext(os.path.basename(glb))[0])
    out_dir = out_dir or os.path.join(ROOT, "assets")
    work = os.path.join(WORK, f"{name}_{time.strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(work, exist_ok=True)
    src = os.path.join(work, name + ".glb")   # paint_drive.py names everything after the GLB
    shutil.copyfile(glb, src)

    on_progress(2, "Camera fit", "background mask of the picture (BiRefNet)")
    pic_name = upload(picture, f"paint_{name}_{uuid.uuid4().hex[:6]}.png")
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

    env = dict(os.environ, BLENDER_USER_RESOURCES=PROFILE, PAINT_IPA=str(ipadapter), PAINT_DISCARD=str(discard),
               PAINT_SERVER=gen3d.SERVER.split("://", 1)[-1].rstrip("/"))
    env.pop("PAINT_LOW", None)
    if low:
        env["PAINT_LOW"] = "1"
    if seed is not None:
        env["PAINT_SEED"] = str(seed)
    views = 9 if low else 7
    on_progress(10, "Painting views", f"StableGen in a Blender window, {views} views")
    t0 = time.time()
    # a small window behind the others: StableGen's generate operator is modal and needs one
    proc = subprocess.Popen([BLENDER, "--window-geometry", "40", "40", "960", "640", "--no-window-focus",
                             "--python", os.path.join(SCRIPTS, "paint_drive.py"), "--", src, picture, camf, prompt, work],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace")
    import threading
    lines = []
    threading.Thread(target=lambda: lines.extend(proc.stdout), daemon=True).start()
    done = 0
    while proc.poll() is None:
        if should_cancel():
            proc.kill()
            raise RuntimeError("cancelled")
        if time.time() - t0 > 1800:
            proc.kill()
            raise RuntimeError("StableGen did not finish within 30 minutes")
        shots = sorted(glob.glob(os.path.join(work, "*", "*", "generated", "*.png")))
        n = len(shots)
        if n != done:
            for f in shots[done:]:
                on_file(f"View {shots.index(f) + 1}", f)
            done = n
            on_progress(10 + 75 * min(n, views) / views, "Painting views", f"{n}/{views} views painted")
        time.sleep(2)
    for ln in lines:
        if ln.startswith("[paint]"):
            log(ln.rstrip())
    res_path = os.path.join(work, name + "_drive.json")
    res = json.load(open(res_path)) if os.path.isfile(res_path) else {"status": "error", "error": "".join(lines[-20:])}
    if res["status"] != "done":
        raise RuntimeError("StableGen failed: " + (res.get("error") or "see the log")[-1500:])
    log(f"StableGen painted {views} views in {res['seconds']} s")

    on_progress(87, "Baking", "one diffuse on a fresh UV unwrap, unseen texels filled" + (", parts locked" if lock else ""))
    r = subprocess.run([BLENDER, "-b", os.path.join(work, name + "_sg.blend"), "--python", os.path.join(SCRIPTS, "paint_bake.py"),
                        "--", work, name, ",".join(lock), str(resolution)], env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("PAINTBAKE {")), None)
    if not line:
        raise RuntimeError("bake failed:\n" + r.stdout[-1500:] + r.stderr[-1500:])
    bake = json.loads(line[len("PAINTBAKE "):])
    log(f"baked: {bake['tris']} tris, {bake['unseen_texels']} unseen texels filled"
        + (f", locked {', '.join(f'{k} {v}' for k, v in bake['locked'].items())}" if bake["locked"] else ""))
    missing_locks = [p for p in lock if p not in bake["locked"]]
    if missing_locks:
        log(f"no part named {', '.join(missing_locks)} in the model; lock takes material names")

    os.makedirs(out_dir, exist_ok=True)
    dst = os.path.join(out_dir, name + ".glb")
    shutil.copyfile(bake["glb"], dst)
    diffuse = os.path.join(out_dir, name + "Diffuse.png")
    shutil.copyfile(bake["diffuse"], diffuse)
    on_file("Diffuse", diffuse)
    out = {"glb": dst, "diffuse": diffuse, "fbx": None, "workdir": work, "fit": fit, "bake": bake,
           "mask": masks[0], "views": sorted(glob.glob(os.path.join(work, "*", "*", "generated", "*.png")))}
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
    ap.add_argument("--ipadapter", type=float, default=0.8, help="IP-Adapter strength (how much the picture steers every view)")
    ap.add_argument("--discard", type=float, default=65.0, help="views paint faces turned less than this many degrees away")
    ap.add_argument("--res", type=int, default=1024, help="diffuse size")
    ap.add_argument("--size", type=float, help="longest dimension in metres for the FBX (default: as modelled)")
    ap.add_argument("--no-fbx", action="store_true")
    a = ap.parse_args()
    out = paint(a.glb, a.picture, a.prompt, a.name, a.out, [s for s in a.lock.split(",") if s], a.low, a.seed,
                a.ipadapter, a.discard, a.res, a.size, not a.no_fbx,
                on_progress=lambda pct, phase, detail="": print(f"{pct:5.1f}%  {phase}  {detail}", flush=True))
    print(json.dumps({k: v for k, v in out.items() if k != "views"}, indent=1))


if __name__ == "__main__":
    main()
