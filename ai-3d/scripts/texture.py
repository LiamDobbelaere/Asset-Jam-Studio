"""Text -> seamless hand-painted world texture (walls, floors, ground) from the command line.

Usage:
    texture.bat "dark oak parquet floor planks" --name ParquetFloorDark
    texture.bat "cream plaster wall" --name PlasterWall --width 1024 --height 2048 --tile crossfade
    texture.bat "red office carpet" --name CarpetRed --seed 7
    texture.bat "worn red brick wall" --name BrickWall --reference old_brick.png --strength 0.6

Requires the ComfyUI server (start_comfyui.bat). Queues workflows/text_to_texture_api.json: Z-Image-Turbo
paints the material. With --tile native (default) the latent is rolled by a different offset on every
sampling step and decoded with circular padding, so the picture tiles by construction and plank or tile
grids line up across the borders. --tile crossfade is the older post-process (roll the picture by half,
cross-fade the continuous centre over the seam cross, optional masked repaint with --denoise); it has no
faint roll lines on flat materials. --reference starts from an existing picture instead of noise (img2img);
--strength is how far it may move away from it (1.0 = ignore it). Copies the result to assets/textures/<Name>.png.

build_api() is also used by the studio web UI (../../studio).
"""
import argparse
import json
import os
import random
import re
import shutil
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate import ROOT, OUT_DIR, get, post, find, prune_to, asset_name  # noqa: E402

STYLE = (", seamless tileable game texture, flat top-down orthographic view, hand-painted in a simple stylized cartoon "
         "style, flat matte solid color fills with a faint grainy speckled mottling, a few soft brush smudges and small "
         "paint spatter dots, very low detail, no fine grain, no photo realism, low contrast, even flat lighting, "
         "no shadows, no perspective, no vignette, the material fills the entire frame edge to edge")
LORA = os.path.join(ROOT, "ComfyUI", "models", "loras", "StylizedTexture_ZIT.safetensors")


def build_api(prompt, raw=False, width=1024, height=1024, seed=None, steps=None, tile="native", lora=0.0,
              band=192, denoise=0.0, reference_image=None, strength=0.6):
    """Patch the texture API workflow. Returns (api, final_node, info).

    reference_image: name of a picture already uploaded to ComfyUI's input folder; sampling then starts from
    it (scaled to width x height) with the given strength instead of from noise.
    """
    if width % 64 or height % 64:
        raise ValueError("width and height must be multiples of 64")
    if tile not in ("native", "crossfade", "none"):
        raise ValueError("tile must be native, crossfade or none")
    api = json.load(open(os.path.join(ROOT, "workflows", "text_to_texture_api.json"), encoding="utf-8"))
    text = prompt if raw else prompt + STYLE
    api[find(api, "CLIPTextEncode", "PROMPT")]["inputs"]["text"] = text
    if seed is None:
        seed = random.randint(0, 2**31)
    steps = steps or (14 if tile == "native" else 8)

    lora_node = find(api, "LoraLoaderModelOnly")
    if lora > 0:
        if not os.path.exists(LORA):
            raise FileNotFoundError(f"--lora needs {LORA}; see README (Civitai model 503877)")
        api[lora_node]["inputs"]["strength_model"] = lora
    else:  # route every consumer of the LoRA node straight to the model sampling node
        upstream = api[lora_node]["inputs"]["model"]
        for v in api.values():
            for k, inp in v["inputs"].items():
                if inp == [lora_node, 0]:
                    v["inputs"][k] = upstream
        del api[lora_node]

    tile_inputs = api[find(api, "SeamlessTileModel")]["inputs"]
    tile_inputs["seed"] = seed
    ks = api[find(api, "KSampler", "seamless")]["inputs"]
    ks["seed"], ks["steps"] = seed, steps
    # the plain sampler of the crossfade/untiled branch; its title is a prefix of the seamless one, so match exactly
    ks1 = next(v for v in api.values() if v["class_type"] == "KSampler" and v["_meta"]["title"].strip().lower() == "text to image")["inputs"]
    ks1["seed"], ks1["steps"] = seed, steps
    ks2 = api[find(api, "KSampler", "Repaint")]["inputs"]
    ks2["seed"], ks2["steps"], ks2["denoise"] = seed + 1, steps, denoise

    W, H = width, height
    lat_node = find(api, "EmptySD3LatentImage")
    lat = api[lat_node]["inputs"]
    lat["width"], lat["height"] = W, H
    if reference_image:
        # img2img: encode the uploaded picture (scaled to the texture size) and start both samplers from it
        vae_id = find(api, "VAELoader")
        api["9001"] = {"class_type": "LoadImage", "_meta": {"title": "Reference texture (uploaded)"},
                       "inputs": {"image": reference_image}}
        api["9002"] = {"class_type": "ImageScale", "_meta": {"title": "Scale reference to texture size"},
                       "inputs": {"image": ["9001", 0], "upscale_method": "lanczos", "width": W, "height": H, "crop": "center"}}
        api["9003"] = {"class_type": "VAEEncode", "_meta": {"title": "Encode reference"},
                       "inputs": {"pixels": ["9002", 0], "vae": [vae_id, 0]}}
        for s in (ks, ks1):
            s["latent_image"] = ["9003", 0]
            s["denoise"] = max(0.05, min(1.0, float(strength)))
    hw, hh = W // 2, H // 2
    for k, v in api.items():
        if v["class_type"] == "ImageCrop":
            v["inputs"].update(width=hw, height=hh, x=hw if "right" in v["_meta"]["title"] else 0,
                               y=hh if "bottom" in v["_meta"]["title"] else 0)
    api[find(api, "SolidMask", "Empty")]["inputs"].update(width=W, height=H)
    api[find(api, "SolidMask", "Horizontal")]["inputs"].update(width=W, height=band)
    api[find(api, "SolidMask", "Vertical")]["inputs"].update(width=band, height=H)
    for k, v in api.items():
        if v["class_type"] == "MaskComposite":
            if "horizontal" in v["_meta"]["title"]:
                v["inputs"]["y"] = (H - band) // 2
            else:
                v["inputs"]["x"] = (W - band) // 2
    feather = band // 2
    for k, v in api.items():
        if v["class_type"] == "FeatherMask":
            for side in ("left", "top", "right", "bottom"):
                if v["inputs"][side]:
                    v["inputs"][side] = feather

    if tile == "none":
        final_node = find(api, "SaveImage", "untiled")
        keep = [final_node]
    elif tile == "native":
        final_node = find(api, "SaveImage", "Save tileable texture")
        keep = [final_node, find(api, "SaveImage", "Save 2x2 tiling preview")]
    elif denoise > 0:
        final_node = find(api, "SaveImage", "repainted")
        rp = find(api, "ImageCompositeMasked", "Blend repaint")
        api[find(api, "ImageStitch", "2x1 (cross-fade)")]["inputs"].update(image1=[rp, 0], image2=[rp, 0])
        keep = [final_node, find(api, "SaveImage", "preview (cross-fade)")]
    else:
        final_node = find(api, "SaveImage", "Save cross-faded texture")
        keep = [final_node, find(api, "SaveImage", "preview (cross-fade)")]
    api = prune_to(api, keep)
    info = {"seed": seed, "steps": steps, "tile": tile, "lora": lora, "width": W, "height": H, "text": text}
    if tile == "crossfade":
        info.update(band=band, denoise=denoise)
    if reference_image:
        info.update(reference=reference_image, strength=strength)
    return api, final_node, info


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt", help="describe ONE material, e.g. 'worn red brick wall'; style hints are appended")
    ap.add_argument("--raw", action="store_true", help="use the prompt verbatim, no style hints")
    ap.add_argument("--name", default=None, help="output file name without extension (default from prompt)")
    ap.add_argument("--width", type=int, default=1024, help="texture width in px, multiple of 64 (default 1024)")
    ap.add_argument("--height", type=int, default=1024, help="texture height in px, multiple of 64 (default 1024)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--steps", type=int, default=None, help="sampling steps (default 14 native, 8 crossfade)")
    ap.add_argument("--tile", choices=["native", "crossfade", "none"], default="native",
                    help="native: seamless by construction (default); crossfade: post-process roll + cross-fade; none: plain image")
    ap.add_argument("--lora", type=float, default=0.0,
                    help="strength of the StylizedTexture_ZIT LoRA, 0 = off (default). 0.8 helps grass, fabric and glazed tiles; "
                         "it hurts plain walls and makes parquet ignore the plank layout")
    ap.add_argument("--band", type=int, default=192, help="crossfade: width in px of the seam strip")
    ap.add_argument("--denoise", type=float, default=0.0, help="crossfade: masked repaint of the seam band, 0.3..0.5; default 0 = cross-fade only")
    ap.add_argument("--reference", help="start from this picture instead of noise (img2img)")
    ap.add_argument("--strength", type=float, default=0.6, help="with --reference: how far to move away from it, 0.05..1.0 (default 0.6)")
    ap.add_argument("--out", default=os.path.join(ROOT, "assets", "textures"), help="folder for the PNG")
    ap.add_argument("--dump", help="write the patched API prompt JSON here and exit")
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()

    reference = None
    if a.reference:
        from sprite import upload  # noqa: E402  (same ComfyUI upload helper)
        reference = upload(f"texture_ref_{uuid.uuid4().hex[:8]}.png", open(a.reference, "rb").read())
    try:
        api, final_node, info = build_api(a.prompt, a.raw, a.width, a.height, a.seed, a.steps, a.tile, a.lora,
                                          a.band, a.denoise, reference, a.strength)
    except (ValueError, FileNotFoundError) as e:
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
    print(f"queued {pid}  {a.width}x{a.height} seed={info['seed']} steps={info['steps']} tile={a.tile} lora={a.lora}"
          + (f" band={a.band} denoise={a.denoise}" if a.tile == "crossfade" else ""))
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
    if result:
        name = a.name or asset_name(a.prompt, "Texture")
        os.makedirs(a.out, exist_ok=True)
        dst = os.path.join(a.out, name + ".png")
        shutil.copyfile(result, dst)
        print("  texture:", dst)


if __name__ == "__main__":
    main()
