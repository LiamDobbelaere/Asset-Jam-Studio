r"""2D transparent game sprites with FLUX.2 [klein] 4B: edit an existing sprite, redraw a character in a new pose
from reference sprites, or draw a new sprite from text.

Usage:
    sprite.bat "improve the shading: soft cel shading with a second darker tone on the hair and clothes" --image refs\hero_front_idle.png --name HeroFrontIdleShaded
    sprite.bat "the character from image 1 sitting on the office chair from image 2, facing the camera, southeast isometric view" --image refs\hero_front_idle.png --ref refs\office_chair.png --name HeroSitFront
    sprite.bat "a short round office robot with one big eye" --width 768 --height 1024 --name OfficeBot
    sprite.bat "..." --image x.png --model base --steps 20 --cfg 4     slower, follows the prompt more closely

Requires the ComfyUI server (start_comfyui.bat). Queues workflows/sprite_edit_api.json: the source sprite is
image 1 and each --ref is image 2, image 3 in the prompt. Transparent inputs are composited on --bg (white) before
upload; the result is cut out again by BiRefNet (--alpha auto), or keeps the source's own alpha (--alpha keep, for
shading/colour edits that leave the silhouette alone), or is saved opaque (--alpha none). Copies the RGBA PNG to
assets/sprites/<Name>.png.

build_api() and finish_sprite() are also used by the studio web UI (../../studio).
"""
import argparse
import io
import json
import os
import random
import re
import shutil
import sys
import time
import urllib.request
import uuid

from PIL import Image, ImageColor, PngImagePlugin

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate import ROOT, OUT_DIR, SERVER, get, post, find, prune_to, asset_name  # noqa: E402

STYLE_EDIT = (", same character design, same art style, clean 2D game sprite, cel-shaded cartoon with dark ink outlines "
              "and flat colours, plain solid white background, no ground shadow")
STYLE_T2I = (", 2D game character sprite, full body in view, centered, clean cel-shaded cartoon style with dark ink "
             "outlines, flat colours with simple two-tone shading, plain solid white background, no ground shadow")
NEGATIVE = "blurry, photo, realistic, 3d render, extra limbs, text, watermark, drop shadow on the ground, cropped"
MODELS = {"distilled": ("flux-2-klein-4b-fp8.safetensors", 4, 1.0), "base": ("flux-2-klein-base-4b-fp8.safetensors", 20, 5.0)}
POS_CHAIN = ["14", "23", "33"]      # ReferenceLatent chain on the positive prompt, one per image
NEG_ZERO_CHAIN = ["15", "24", "34"]  # same on the zeroed negative (distilled)
NEG_BASE_CHAIN = ["16", "25", "35"]  # same on the real negative prompt (base)
LOADERS = ["10", "20", "30"]


def prep_image(path, bg, megapixels, name):
    """RGBA -> RGB on a solid colour, scaled to ~megapixels with sides a multiple of 16. Returns (PNG bytes, (w, h), alpha)."""
    im = Image.open(path)
    alpha = im.getchannel("A") if im.mode in ("RGBA", "LA") else None
    rgb = Image.new("RGB", im.size, ImageColor.getrgb(bg))
    rgb.paste(im.convert("RGBA"), mask=alpha)
    w, h = rgb.size
    s = (megapixels * 1e6 / (w * h)) ** 0.5
    nw, nh = max(16, round(w * s / 16) * 16), max(16, round(h * s / 16) * 16)
    rgb = rgb.resize((nw, nh), Image.LANCZOS)
    buf = io.BytesIO()
    rgb.save(buf, "PNG")
    return buf.getvalue(), (nw, nh), alpha


def upload(name, data):
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            f"Content-Type: image/png\r\n\r\n").encode() + data + f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(SERVER + "/upload/image", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["name"]


def build_api(prompt, image=None, refs=(), model="distilled", steps=None, cfg=None, negative=NEGATIVE, seed=None,
              raw=False, width=None, height=None, megapixels=1.0, bg="#ffffff", alpha="auto", dump=False,
              upload_prefix="sprite_in"):
    """Patch the sprite API workflow; uploads the source and reference pictures to ComfyUI (unless dump).

    Returns (api, final_node, state) where state holds src_alpha / src_size / work_size for finish_sprite().
    """
    refs = list(refs or [])
    if len(refs) > 2:
        raise ValueError("at most two reference images")
    if refs and not image:
        raise ValueError("reference images need a source image (the source is image 1)")
    if alpha == "keep" and not image:
        raise ValueError("alpha=keep needs a source image")
    if (width or 0) % 16 or (height or 0) % 16 or bool(width) != bool(height):
        raise ValueError("width and height must be given together, as multiples of 16")

    api = json.load(open(os.path.join(ROOT, "workflows", "sprite_edit_api.json"), encoding="utf-8"))
    model_file, dsteps, dcfg = MODELS[model]
    steps = steps or dsteps
    cfg = cfg if cfg is not None else dcfg
    if seed is None:
        seed = random.randint(0, 2**31)
    text = prompt if raw else prompt + (STYLE_EDIT if image else STYLE_T2I)

    api["1"]["inputs"]["unet_name"] = model_file
    api["5"]["inputs"]["text"] = text
    api["7"]["inputs"]["text"] = negative
    api["43"]["inputs"]["noise_seed"] = seed
    base = model == "base"

    images = [image] + refs if image else []
    src_alpha, src_size, work_size = None, None, None
    for i, path in enumerate(images):
        data, size, a_ch = prep_image(path, bg, megapixels, f"{upload_prefix}{i + 1}.png")
        if i == 0:
            src_alpha, src_size, work_size = a_ch, Image.open(path).size, size
        api[LOADERS[i]]["inputs"]["image"] = upload(f"{upload_prefix}{i + 1}.png", data) if not dump else f"{upload_prefix}{i + 1}.png"
    n = len(images)
    if n:
        guider = api["44"]["inputs"]
        guider["positive"] = [POS_CHAIN[n - 1], 0]
        guider["negative"] = [(NEG_BASE_CHAIN if base else NEG_ZERO_CHAIN)[n - 1], 0]
        guider["cfg"] = cfg if base else 1.0   # the distilled model has no negative branch
        api["41"]["inputs"]["steps"] = steps
        if width:   # explicit canvas instead of the source size
            api["40"]["inputs"].update(width=width, height=height)
            api["41"]["inputs"].update(width=width, height=height)
            work_size = (width, height)
        raw_node, rgba_node = "47", "52"
    else:
        guider = api["62"]["inputs"]
        guider["negative"] = ["7", 0] if base else ["6", 0]
        guider["cfg"] = cfg if base else 1.0   # the distilled model has no negative branch
        w, h = width or 768, height or 1024
        api["60"]["inputs"].update(width=w, height=h)
        api["61"]["inputs"].update(steps=steps, width=w, height=h)
        work_size = (w, h)
        raw_node, rgba_node = "65", "67"
    final_node = rgba_node if alpha == "auto" else raw_node
    api = prune_to(api, [final_node] + ([raw_node] if alpha == "auto" else []))
    state = {"src_alpha": src_alpha, "src_size": src_size, "work_size": work_size, "images": n, "model": model,
             "steps": steps, "cfg": cfg, "seed": seed, "alpha": alpha, "text": text}
    return api, final_node, state


def finish_sprite(result_png, dst, state, match_source=False):
    """Copy the ComfyUI result to dst as RGBA, re-applying the source alpha / size when asked."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    src = Image.open(result_png)
    info = PngImagePlugin.PngInfo()
    for k, v in (getattr(src, "text", None) or {}).items():   # ComfyUI stores the workflow (prompt, seed) here
        info.add_text(k, v)
    im = src.convert("RGBA")
    if state["alpha"] == "keep" and state["src_alpha"] is not None:
        im.putalpha(state["src_alpha"].resize(im.size, Image.LANCZOS))
    if match_source and state["src_size"]:
        im = im.resize(state["src_size"], Image.LANCZOS)
    im.save(dst, pnginfo=info)
    return dst


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt", help="describe the finished sprite (edit: what changes and what stays); style hints are appended")
    ap.add_argument("--image", help="source sprite = image 1 (omit to draw from text only)")
    ap.add_argument("--ref", action="append", default=[], help="extra reference = image 2, image 3 (repeatable, max 2)")
    ap.add_argument("--model", choices=MODELS, default="distilled", help="distilled: 4 steps (default); base: 20 steps with CFG, slower but more faithful to the prompt")
    ap.add_argument("--steps", type=int, default=None)
    ap.add_argument("--cfg", type=float, default=None, help="base model only (default 5); the distilled model ignores it")
    ap.add_argument("--negative", default=NEGATIVE, help="base model only")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--raw", action="store_true", help="use the prompt verbatim, no style hints")
    ap.add_argument("--name", default=None, help="output file name without extension (default from prompt)")
    ap.add_argument("--width", type=int, default=None, help="output canvas width, multiple of 16 (default: source size scaled to --megapixels, or 768)")
    ap.add_argument("--height", type=int, default=None, help="output canvas height, multiple of 16 (default: source size, or 1024); "
                    "set both wider than the source when a new pose needs more room (sitting, arms out)")
    ap.add_argument("--megapixels", type=float, default=1.0, help="working resolution for the source and refs (default 1.0)")
    ap.add_argument("--bg", default="#ffffff", help="colour the transparent inputs are composited on (default white)")
    ap.add_argument("--alpha", choices=["auto", "keep", "none"], default="auto",
                    help="auto: BiRefNet cut-out (default); keep: reuse the source alpha (silhouette unchanged); none: opaque")
    ap.add_argument("--match-source", action="store_true", help="resize the result back to the source sprite's pixel size")
    ap.add_argument("--out", default=os.path.join(ROOT, "assets", "sprites"), help="folder for the PNG")
    ap.add_argument("--dump", help="write the patched API prompt JSON here and exit")
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()

    try:
        api, final_node, state = build_api(a.prompt, a.image, a.ref, a.model, a.steps, a.cfg, a.negative, a.seed, a.raw,
                                           a.width, a.height, a.megapixels, a.bg, a.alpha, dump=bool(a.dump))
    except ValueError as e:
        sys.exit(str(e))
    if a.dump:
        json.dump(api, open(a.dump, "w", encoding="utf-8"), indent=1)
        print("wrote", a.dump)
        return

    res = post("/prompt", {"prompt": api, "client_id": str(uuid.uuid4())})
    if "prompt_id" not in res:
        print(json.dumps(res, indent=1))
        sys.exit(1)
    pid = res["prompt_id"]
    ws = state["work_size"]
    print(f"queued {pid}  model={a.model} steps={state['steps']} cfg={state['cfg']} seed={state['seed']} images={state['images']} "
          f"size={ws[0]}x{ws[1]} alpha={a.alpha}")
    t0 = time.time()
    while time.time() - t0 < a.timeout:
        hist = get(f"/history/{pid}")
        if pid in hist:
            break
        print(f"  {time.time() - t0:.0f}s", end="\r", flush=True)
        time.sleep(2)
    else:
        print("timed out")
        sys.exit(3)
    h = hist[pid]
    st = h.get("status", {})
    if st.get("status_str") == "error":
        for m in st.get("messages", []):
            if m[0] == "execution_error":
                print("\nERROR in node", m[1].get("node_type"), ":", m[1].get("exception_message"))
        sys.exit(2)
    print(f"\ndone in {time.time() - t0:.0f}s")
    result = None
    for nid, out in h.get("outputs", {}).items():
        for img in out.get("images", []):
            p = os.path.join(OUT_DIR, img.get("subfolder", ""), img["filename"])
            print("  image:", p)
            if nid == final_node:
                result = p
    if not result:
        sys.exit("no output image")
    name = a.name or asset_name(a.prompt, "Sprite")
    dst = finish_sprite(result, os.path.join(a.out, name + ".png"), state, a.match_source)
    print("  sprite:", dst)


if __name__ == "__main__":
    main()
