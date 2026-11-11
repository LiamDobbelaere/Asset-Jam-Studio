r"""Pixel art with FLUX.2 [klein] 4B + pixel-art LoRAs, snapped to a real pixel grid and palette, and animated pixel
art (sprite sheet + GIF): a still is animated by Wan 2.2 TI2V-5B and every frame is re-pixelized with the still's
own palette, so the frames share one grid and one set of colours.

Usage:
    pixel.bat "a knight with a red plume, idle pose, side view" --name Knight
    pixel.bat "the character from image 1" --image refs\hero.png --name HeroPixel          an existing sprite as pixel art
    pixel.bat "a cosy tavern interior with a fireplace" --kind scene --width 160 --height 120 --name Tavern
    pixel.bat --sheet "a medieval knight in blue armour with a red plume" --name Knight       4x4 walk-cycle sprite sheet
    pixel.bat --sheet --image assets\pixel\Knight.png --name KnightSheet                      ... of the character in a picture
    pixel.bat --animate assets\pixel\Knight.png "the knight walks in place" --name KnightWalk  Wan 2.2 14B + pixel LoRA (setup_pixel_anim.ps1)

Requires the ComfyUI server (start_comfyui.bat); --animate also needs setup_pixel_anim.ps1 (engine wan14) or ai-vid/setup.ps1 (wan5b).
Stills go to assets/pixel/<Name>.png (RGBA at its real pixel size, e.g. 64x64; view it with nearest-neighbour
scaling). Sheets go to assets/pixel/<Name>/: <Name>_sheet.png (128x128, 16 cells of 32 px: 3 walk frames in each of
4 directions + arms up, jump left/right, lying), frames/<Name>_<cell>.png, <Name>_walk_<dir>.gif, <Name>.json (cell
map + animations) and <Name>_raw.png (the 512 px picture). Wan animations go to assets/pixel/<Name>/ too:
<Name>_sheet.png (frames side by side), <Name>.gif, <Name>.json, frames/ and <Name>_wan.mp4.

build_still_api(), build_sheet_api(), pixelize(), build_anim_api() and finish_*() are also used by the studio (../../studio).
"""
import argparse
import glob
import io
import json
import os
import random
import shutil
import sys
import tempfile
import time
import uuid

import numpy as np
from PIL import Image, ImageColor, PngImagePlugin

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate import ROOT, OUT_DIR, get, post, asset_name, prune_to  # noqa: E402
import sprite as gensprite  # noqa: E402

PIXEL_DIR = os.path.join(ROOT, "assets", "pixel")
AIVID_SCRIPTS = os.path.join(os.path.dirname(ROOT), "ai-vid", "scripts")

# The two LoRAs (both Apache-2.0, downloaded by scripts/download_models.py into ComfyUI/models/loras):
#  sprite: Limbicnation/pixel-art-lora, trained on the distilled klein 4B at 512x512 on CC0 character sprites
#  scene:  adirik/pixel-art-lora-flux.2-klein-4B, trained on the base klein 4B at 512x512 on 64x64 scenes / objects
KINDS = {
    "sprite": {"lora": "pixel_sprite_klein4b.safetensors", "model": "distilled", "width": 64, "height": 64, "alpha": "auto",
               "t2i": "pixel art sprite, {prompt}, game asset, plain solid white background",
               "edit": "pixel art sprite, the exact same character as image 1 redrawn as pixel art: {prompt}, same design, "
                       "same colours, same pose, game asset, plain solid white background"},
    "scene": {"lora": "pixel_scene_klein4b.safetensors", "model": "base", "width": 128, "height": 128, "alpha": "none",
              "t2i": "PXART4. A pixel art of {prompt}.",
              "edit": "PXART4. A pixel art of image 1: {prompt}. Same layout, same colours."},
    "none": {"lora": None, "model": "distilled", "width": 64, "height": 64, "alpha": "auto",
             "t2i": "{prompt}, 16-bit pixel art, crisp square pixels, limited colour palette, no anti-aliasing, "
                    "2D game sprite, plain solid white background",
             "edit": "the exact same subject as image 1 redrawn as 16-bit pixel art: {prompt}, crisp square pixels, "
                     "limited colour palette, same design and colours, plain solid white background"},
}
NEGATIVE = "blurry, smooth gradients, photo, realistic, 3d render, anti-aliasing, text, watermark, drop shadow on the ground, cropped"

# style "illustration": the same flows without the pixel LoRAs and without the grid / palette step: klein draws a clean
# digitally painted sprite (or scene) at the requested size, Wan 14B animates it without the pixel-animate LoRA.
STYLES = ("pixel", "illustration")
ILLUSTRATION = {
    "sprite": {"lora": None, "model": "distilled", "width": 512, "height": 512, "alpha": "auto",
               "t2i": "{prompt}, 2D game character sprite, clean digital illustration, soft cel shading, clear shapes, full body "
                      "in view, centered, plain solid white background, no ground shadow",
               "edit": "the exact same character as image 1 redrawn as a clean digitally illustrated 2D game sprite: {prompt}, same "
                       "design, same colours, same pose, soft cel shading, plain solid white background, no ground shadow"},
    "scene": {"lora": None, "model": "distilled", "width": 1024, "height": 768, "alpha": "none",
              "t2i": "{prompt}, 2D game background illustration, clean digital painting, soft lighting, clear shapes",
              "edit": "image 1 redrawn as a clean 2D game background illustration: {prompt}, same layout, same colours"},
}
ILLUSTRATION["none"] = ILLUSTRATION["sprite"]
ILLUS_NEGATIVE = "blurry, photo, realistic, 3d render, pixel art, pixelated, text, watermark, drop shadow on the ground, cropped"


def style_mode(v):
    v = (v or "pixel").lower()
    if v not in STYLES:
        raise ValueError(f"style must be one of {STYLES}")
    return v
ANIM_STYLE = (", pixel art sprite animation for a 2D game, the subject stays in place in the centre of the frame, "
              "static camera, no zoom, plain solid white background, crisp pixels, limited palette, smooth loop")
ANIM_NEGATIVE = ("blurry, smooth gradients, photorealistic, 3d render, anti-aliasing, text, watermark, camera motion, zoom, "
                 "panning, the background changes, morphing into something else, extra limbs, deformed, cropped, "
                 "a different character, worst quality, low quality")
VIDEO_FPS = 24


# ---------------------------------------------------------------------------------------------------------------
# pixelization (pure Pillow + numpy; shared by stills and animation frames)

def _pal_image(pal):
    """A P-mode image carrying `pal` (list of RGB tuples) for Image.quantize(palette=...). The unused entries
    repeat the last colour so they can never win a nearest-colour match."""
    pal = list(pal) or [(0, 0, 0)]
    flat = []
    for i in range(256):
        flat.extend(pal[min(i, len(pal) - 1)])
    p = Image.new("P", (1, 1))
    p.putpalette(flat)
    return p


def _kmeans(points, centres, iters=12):
    """Refine a palette (k-means on the opaque pixels, initialised from Pillow's median cut)."""
    pts = points.astype(np.float32)
    c = np.array(centres, dtype=np.float32)
    for _ in range(iters):
        d = ((pts[:, None, :] - c[None, :, :]) ** 2).sum(-1)
        lab = d.argmin(1)
        new = c.copy()
        for k in range(len(c)):
            m = lab == k
            if m.any():
                new[k] = pts[m].mean(0)
        if np.allclose(new, c, atol=0.25):
            c = new
            break
        c = new
    return [tuple(int(round(v)) for v in row) for row in c]


def make_palette(rgb_opaque, colors):
    """A palette of at most `colors` RGB tuples for the opaque pixels (N x 3 uint8)."""
    if len(rgb_opaque) == 0:
        return [(0, 0, 0)]
    uniq = np.unique(rgb_opaque, axis=0)
    if len(uniq) <= colors:
        return [tuple(int(v) for v in row) for row in uniq]
    strip = Image.fromarray(rgb_opaque.reshape(1, -1, 3), "RGB")
    q = strip.quantize(colors=min(colors, 256), method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    used = sorted({idx for _, idx in q.getcolors(maxcolors=256) or []})
    raw = q.getpalette()
    init = [tuple(raw[3 * i:3 * i + 3]) for i in used]
    sample = rgb_opaque if len(rgb_opaque) <= 60000 else rgb_opaque[np.random.RandomState(0).choice(len(rgb_opaque), 60000, replace=False)]
    pal = _kmeans(sample, init)
    # drop duplicates that k-means may have merged
    seen, out = set(), []
    for c in pal:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def apply_palette(rgb, pal):
    """Map an RGB uint8 array (H x W x 3) to the nearest colours of `pal`. Returns (rgb, index array)."""
    h, w = rgb.shape[:2]
    q = Image.fromarray(rgb, "RGB").quantize(palette=_pal_image(pal), dither=Image.Dither.NONE)
    idx = np.asarray(q, dtype=np.int32).reshape(h, w)
    idx = np.minimum(idx, len(pal) - 1)
    table = np.array(pal, dtype=np.uint8)
    return table[idx], idx


def _sample_centers(arr, size, offset):
    """Pick one source pixel per output pixel: the point at `offset` (0-1 fractions of a block) inside each block."""
    h, w = arr.shape[:2]
    aw, ah = size
    xs = np.clip(np.floor((np.arange(aw) + offset[0]) * w / aw).astype(int), 0, w - 1)
    ys = np.clip(np.floor((np.arange(ah) + offset[1]) * h / ah).astype(int), 0, h - 1)
    return arr[ys[:, None], xs[None, :]]


def best_offset(arr, size, steps=4):
    """The sampling offset whose nearest-neighbour reconstruction explains the picture best: diffusion output has
    its own pixel grid, and it is rarely aligned with ours, so sampling at the block centres can cut through its
    pixel edges. Tries steps x steps offsets inside a block and keeps the one with the smallest error."""
    h, w = arr.shape[:2]
    a = arr[..., 3:4] / 255.0
    pre = np.concatenate([arr[..., :3] * a, arr[..., 3:4]], axis=-1)
    best, best_err = (0.5, 0.5), None
    for i in range(steps):
        for j in range(steps):
            off = ((i + 0.5) / steps, (j + 0.5) / steps)
            small = _sample_centers(pre, size, off)
            up = np.asarray(Image.fromarray(small.astype(np.uint8), "RGBA").resize((w, h), Image.NEAREST), dtype=np.float32)
            err = float(np.abs(up - pre).mean())
            if best_err is None or err < best_err:
                best, best_err = off, err
    return best


def pixelize(im, size, colors=32, palette=None, sample="center", alpha_threshold=128, offset=None):
    """RGBA picture -> (RGBA at `size`, palette). Each block becomes one pixel: sampled at the best-aligned point
    inside the block (`sample="center"`, default; `offset` fixes the point, else it is searched) or averaged
    (`sample="box"`, premultiplied so transparent neighbours do not darken edges). The alpha is thresholded, then
    the opaque pixels are quantized to `colors` colours (or to the given `palette`, for animation frames)."""
    im = im.convert("RGBA")
    arr = np.asarray(im, dtype=np.float32)
    if tuple(im.size) != tuple(size):
        a = arr[..., 3:4] / 255.0
        pre = np.concatenate([arr[..., :3] * a, arr[..., 3:4]], axis=-1)
        if sample == "box":
            small = np.asarray(Image.fromarray(pre.astype(np.uint8), "RGBA").resize(tuple(size), Image.BOX), dtype=np.float32)
        else:
            small = _sample_centers(pre, size, offset or best_offset(arr, size))
        sa = small[..., 3:4]
        rgb = np.where(sa > 0, small[..., :3] * 255.0 / np.maximum(sa, 1e-6), 0)
        alpha = small[..., 3] >= alpha_threshold
    else:
        rgb = arr[..., :3]
        alpha = arr[..., 3] >= alpha_threshold
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    if palette is None:
        palette = make_palette(rgb[alpha], colors) if colors and colors > 0 else None
    if palette:
        rgb, _ = apply_palette(rgb, palette)
    out = np.zeros(rgb.shape[:2] + (4,), dtype=np.uint8)
    out[..., :3] = rgb
    out[..., 3] = np.where(alpha, 255, 0)
    out[~alpha] = 0
    return Image.fromarray(out, "RGBA"), palette


def upscale(im, factor):
    """Nearest-neighbour upscale (what a game engine does with filtering off)."""
    return im.resize((im.width * factor, im.height * factor), Image.NEAREST)


def write_gif(frames, pal, dst, fps=8, scale=4):
    """Looping GIF of RGBA frames that share `pal`; index 0 is the transparent colour."""
    lut = {c: i + 1 for i, c in enumerate(pal)}
    flat = [0, 0, 0]
    for c in pal:
        flat.extend(c)
    while len(flat) < 768:
        flat.extend(pal[-1] if pal else (0, 0, 0))
    out = []
    for fr in frames:
        a = np.asarray(fr.convert("RGBA"))
        idx = np.zeros(a.shape[:2], dtype=np.uint8)
        rgb = a[..., :3]
        opaque = a[..., 3] > 0
        keys = rgb[opaque]
        if len(keys):
            idx[opaque] = [lut.get(tuple(int(v) for v in k), 1) for k in keys]
        p = Image.fromarray(idx, "P")
        p.putpalette(flat)
        if scale > 1:
            p = p.resize((p.width * scale, p.height * scale), Image.NEAREST)
        out.append(p)
    out[0].save(dst, save_all=True, append_images=out[1:], duration=max(20, round(1000 / fps)), loop=0, transparency=0,
                disposal=2, optimize=False)
    return dst


def write_gif_rgba(frames, dst, fps=8, scale=1):
    """Looping GIF of full-colour RGBA frames: one adaptive 255-colour palette from all frames, index 0 transparent."""
    sheet = Image.new("RGBA", (frames[0].width * len(frames), frames[0].height), (0, 0, 0, 0))
    for i, fr in enumerate(frames):
        sheet.paste(fr, (i * frames[0].width, 0))
    opaque = sheet.convert("RGB")
    q = opaque.quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    raw = q.getpalette()[:765]
    pal = [tuple(raw[3 * i:3 * i + 3]) for i in range(255)]
    out = []
    w = frames[0].width
    for i, fr in enumerate(frames):
        idx = np.asarray(q.crop((i * w, 0, (i + 1) * w, fr.height)), dtype=np.int32) + 1
        idx[np.asarray(fr)[..., 3] < 128] = 0
        p = Image.fromarray(idx.astype(np.uint8), "P")
        p.putpalette([0, 0, 0] + [v for c in pal for v in c])
        if scale > 1:
            p = p.resize((p.width * scale, p.height * scale), Image.NEAREST)
        out.append(p)
    out[0].save(dst, save_all=True, append_images=out[1:], duration=max(20, round(1000 / fps)), loop=0, transparency=0,
                disposal=2, optimize=False)
    return dst


def pick_scale(aw, ah, scale=None, target=512, lo=512, hi=1536):
    """Block size so that the diffusion canvas (art size x scale) is a multiple of 16 on both sides, near `target`
    px on the longest side (the LoRAs were trained at 512). Returns (scale, canvas_w, canvas_h)."""
    if scale:
        return scale, max(16, round(aw * scale / 16) * 16), max(16, round(ah * scale / 16) * 16)
    base = max(1, round(target / max(aw, ah)))
    cands = [base] + [base + d for d in range(1, 16)] + [base - d for d in range(1, base)]
    for s in cands:
        if s < 1:
            continue
        cw, ch = aw * s, ah * s
        if cw % 16 == 0 and ch % 16 == 0 and lo <= max(cw, ch) <= hi:
            return s, cw, ch
    s = base
    return s, max(16, round(aw * s / 16) * 16), max(16, round(ah * s / 16) * 16)


# ---------------------------------------------------------------------------------------------------------------
# stills

def build_still_api(prompt, image=None, refs=(), kind="sprite", width=None, height=None, scale=None, model=None,
                    steps=None, cfg=None, negative=NEGATIVE, seed=None, raw=False, lora_strength=None, alpha=None,
                    bg="#ffffff", dump=False, upload_prefix="pixel_in", style="pixel"):
    """The sprite workflow with the pixel-art LoRA, at a canvas that is a whole number of blocks (style "pixel"), or
    without a LoRA at the requested size with no snapping afterwards (style "illustration").

    Returns (api, final_node, state); state adds art_size, scale, kind, style, lora for finish_still()."""
    style = style_mode(style)
    k = (ILLUSTRATION if style == "illustration" else KINDS)[kind]
    aw = int(width or k["width"])
    if height:
        ah = int(height)
    elif image:                                  # follow the source picture's aspect
        sw, sh = Image.open(image).size
        ah = max(8, round(aw * sh / sw))
    else:
        ah = aw if width else int(k["height"])
    if style == "illustration":                  # klein draws at the output size: multiples of 16, at most ~1 MP
        f = min(1.0, (1.05e6 / (aw * ah)) ** 0.5)
        cw, ch = max(16, round(aw * f / 16) * 16), max(16, round(ah * f / 16) * 16)
        aw, ah, s = cw, ch, 1
        if negative == NEGATIVE:
            negative = ILLUS_NEGATIVE
    else:
        s, cw, ch = pick_scale(aw, ah, scale)
    model = model or k["model"]
    if model == "auto":
        model = k["model"]
    alpha = alpha or k["alpha"]
    text = prompt if raw else (k["edit"] if image else k["t2i"]).format(prompt=prompt)
    api, final_node, state = gensprite.build_api(
        text, image, refs, model, steps, cfg, negative, seed, raw=True, width=cw, height=ch, megapixels=1.0, bg=bg,
        alpha=alpha, dump=dump, upload_prefix=upload_prefix, lora=k["lora"],
        lora_strength=1.0 if lora_strength is None else lora_strength)
    state.update(art_size=(aw, ah), scale=s, kind=kind, prompt=prompt, style=style)
    return api, final_node, state


def finish_still(result_png, dst, state, colors=32, sample="center"):
    """ComfyUI result (RGBA when cut out) -> pixel-art PNG at the art size, or (style illustration) the picture as it
    is, as RGBA. Returns (dst, palette); the palette is None for illustrations."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    src = Image.open(result_png)
    info = PngImagePlugin.PngInfo()
    for key, v in (getattr(src, "text", None) or {}).items():   # ComfyUI's workflow (prompt, seed) travels with the file
        info.add_text(key, v)
    im = src.convert("RGBA")
    if state.get("alpha") == "keep" and state.get("src_alpha") is not None:
        im.putalpha(state["src_alpha"].resize(im.size, Image.LANCZOS))
    if state.get("style") == "illustration":
        info.add_text("pixel", json.dumps({"style": "illustration", "width": im.width, "height": im.height, "kind": state.get("kind")}))
        im.save(dst, pnginfo=info)
        return dst, None
    small, pal = pixelize(im, state["art_size"], colors=colors, sample=sample)
    info.add_text("pixel", json.dumps({"style": "pixel", "width": small.width, "height": small.height, "scale": state.get("scale"),
                                       "colors": len(pal or []), "palette": ["#%02x%02x%02x" % c for c in (pal or [])],
                                       "kind": state.get("kind")}))
    small.save(dst, pnginfo=info)
    return dst, pal


# ---------------------------------------------------------------------------------------------------------------
# walk-cycle sprite sheets: FLUX.2 klein base 4B + svntax-dev's 4-walk LoRA (Apache-2.0; 32 px characters in a 4x4 sheet)

SHEET_LORA = "pixel_4walk_klein4b_base.safetensors"
SHEET_LAYOUT = ("The spritesheet is a 4 by 4 grid of four rows of frames - first row is 3 walking frames facing down and 1 frame "
                "both arms raised, second row is 3 walking frames facing left and 1 frame jumping left, third row is 3 walking "
                "frames facing right and 1 frame jumping right, fourth row is 3 walking frames back view facing up and 1 frame "
                "lying on floor.")
SHEET_T2I = "A pixel art spritesheet of {prompt}. " + SHEET_LAYOUT
SHEET_EDIT = "Create a pixel art spritesheet of the character in image 1{extra}. " + SHEET_LAYOUT
SHEET_CANVAS, SHEET_ART, SHEET_CELL = 512, 128, 32      # the LoRA was trained on 512x512 sheets of 32 px sprites (x4 blocks)
SHEET_CELLS = ["down_0", "down_1", "down_2", "arms_up", "left_0", "left_1", "left_2", "jump_left",
               "right_0", "right_1", "right_2", "jump_right", "up_0", "up_1", "up_2", "lie_down"]
SHEET_ANIMS = {"walk_down": ["down_0", "down_1", "down_2", "down_1"], "walk_left": ["left_0", "left_1", "left_2", "left_1"],
               "walk_right": ["right_0", "right_1", "right_2", "right_1"], "walk_up": ["up_0", "up_1", "up_2", "up_1"]}


def build_sheet_api(prompt="", image=None, seed=None, steps=None, cfg=None, lora_strength=1.0, raw=False, negative=NEGATIVE,
                    bg="#ffffff", dump=False):
    """A 4x4 walk-cycle sheet of a character described by `prompt` and / or shown in `image` (a pixel still, a sprite,
    any picture). Always the base model at 512x512 (the LoRA's author: the distilled model ruins the pixels).
    Returns (api, final_node, state)."""
    if not prompt and not image:
        raise ValueError("describe the character or give a picture of it")
    if raw:
        text = prompt
    elif image:
        text = SHEET_EDIT.format(extra=(", " + prompt) if prompt else "")
    else:
        text = SHEET_T2I.format(prompt=prompt)
    tmp = None
    src = image
    if image:
        im = Image.open(image)
        if max(im.size) < SHEET_CANVAS:        # a 64 px still: enlarge it smoothly ourselves rather than let ComfyUI blur it
            s = SHEET_CANVAS / max(im.size)
            canvas = Image.new("RGB", (SHEET_CANVAS, SHEET_CANVAS), ImageColor.getrgb(bg))
            big = im.convert("RGBA").resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
            canvas.paste(big, ((SHEET_CANVAS - big.width) // 2, (SHEET_CANVAS - big.height) // 2), big)
            tmp = os.path.join(tempfile.gettempdir(), f"pixel_sheet_{uuid.uuid4().hex}.png")
            canvas.save(tmp)
            src = tmp
    try:
        api, final_node, state = gensprite.build_api(
            text, src, (), "base", steps, cfg, negative, seed, raw=True, width=SHEET_CANVAS, height=SHEET_CANVAS, megapixels=1.0,
            bg=bg, alpha="none", dump=dump, upload_prefix="pixel_sheet", lora=SHEET_LORA, lora_strength=lora_strength)
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass
    state.update(prompt=prompt, kind="sheet", art_size=(SHEET_ART, SHEET_ART), scale=SHEET_CANVAS // SHEET_ART, reference=image)
    return api, final_node, state


def key_background(small, tol=24):
    """Make the background of an opaque pixel picture transparent: the dominant border colour, flood-filled from the
    edges (enclosed pixels of that colour, like eyes, stay)."""
    from scipy import ndimage
    a = np.asarray(small.convert("RGBA")).copy()
    rgb = a[..., :3].astype(int)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    vals, counts = np.unique(border, axis=0, return_counts=True)
    bgc = vals[counts.argmax()]
    near = np.abs(rgb - bgc).sum(-1) <= tol
    labels, _ = ndimage.label(near)
    edge = set(np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))) - {0}
    mask = np.isin(labels, list(edge))
    a[mask] = 0
    return Image.fromarray(a, "RGBA"), "#%02x%02x%02x" % tuple(int(v) for v in bgc)


def finish_sheet(result_png, name, out_root=PIXEL_DIR, colors=32, fps=6, preview_scale=4, sample="center", seed=None, text=None,
                 prompt=None, reference=None):
    """The 512 px sheet -> 128 px pixel-exact RGBA sheet, 16 named 32 px cells, a GIF per walk cycle, a JSON layout and
    the raw picture. Returns the outputs dict (paths)."""
    im = Image.open(result_png).convert("RGBA")
    small, _ = pixelize(im, (SHEET_ART, SHEET_ART), colors=colors, sample=sample)
    small, bg = key_background(small)
    a = np.asarray(small)
    pal = [tuple(int(v) for v in c) for c in np.unique(a[a[..., 3] > 0][:, :3], axis=0)]
    d = os.path.join(out_root, name)
    fdir = os.path.join(d, "frames")
    os.makedirs(fdir, exist_ok=True)
    cells, cell_paths = {}, {}
    for i, cname in enumerate(SHEET_CELLS):
        r, c = divmod(i, 4)
        cell = small.crop((c * SHEET_CELL, r * SHEET_CELL, (c + 1) * SHEET_CELL, (r + 1) * SHEET_CELL))
        cells[cname] = cell
        p = os.path.join(fdir, f"{name}_{cname}.png")
        cell.save(p)
        cell_paths[cname] = p
    sheet_path = os.path.join(d, f"{name}_sheet.png")
    small.save(sheet_path)
    gifs = {}
    for aname, seq in SHEET_ANIMS.items():
        gifs[aname] = write_gif([cells[k] for k in seq], pal, os.path.join(d, f"{name}_{aname}.gif"), fps=fps, scale=preview_scale)
    raw_path = os.path.join(d, f"{name}_raw.png")
    shutil.copyfile(result_png, raw_path)
    meta = {"type": "sheet", "name": name, "cell_width": SHEET_CELL, "cell_height": SHEET_CELL, "columns": 4, "rows": 4,
            "cells": {cname: {"col": i % 4, "row": i // 4, "x": (i % 4) * SHEET_CELL, "y": (i // 4) * SHEET_CELL} for i, cname in enumerate(SHEET_CELLS)},
            "animations": SHEET_ANIMS, "fps": fps, "palette": ["#%02x%02x%02x" % c for c in pal], "background": bg,
            "seed": seed, "prompt": prompt, "text": text, "reference": reference, "sheet": os.path.basename(sheet_path)}
    json_path = os.path.join(d, f"{name}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    return {"sheet": sheet_path, "json": json_path, "raw": raw_path, "gifs": gifs, "frames": cell_paths, "dir": d, "palette": pal}


# ---------------------------------------------------------------------------------------------------------------
# animation: Wan 2.2 TI2V-5B from the still (first frame pinned, last frame pinned too for a loop), BiRefNet per frame

def _shot():
    if AIVID_SCRIPTS not in sys.path:
        sys.path.insert(0, AIVID_SCRIPTS)
    import shot  # noqa: E402  (ai-vid/scripts/shot.py: Wan 2.2 builder)
    return shot


def load_still(path, width=None, height=None, colors=32, sample="center", style="pixel"):
    """The still as pixel art: a small PNG is taken as is (its colours are its palette); a bigger picture is
    pixelized to width x height first. Style illustration: the picture as it is (longest side capped at 1024), no
    palette. Returns (RGBA at art size, palette or None, pixel meta from the PNG or None)."""
    im = Image.open(path)
    meta = None
    try:
        meta = json.loads((getattr(im, "text", None) or {}).get("pixel", ""))
    except ValueError:
        meta = None
    im = im.convert("RGBA")
    if style_mode(style) == "illustration":
        if max(im.size) > 1024:
            f = 1024 / max(im.size)
            im = im.resize((max(1, round(im.width * f)), max(1, round(im.height * f))), Image.LANCZOS)
        return im, None, meta
    if max(im.size) <= 256 and not (width and height):
        a = np.asarray(im)
        pal = [tuple(int(v) for v in c) for c in np.unique(a[a[..., 3] > 0][:, :3], axis=0)]
        if len(pal) > 256:
            small, pal = pixelize(im, im.size, colors=colors)
            return small, pal, meta
        return im, pal, meta
    aw = int(width or 64)
    ah = int(height or max(8, round(aw * im.height / im.width)))
    small, pal = pixelize(im, (aw, ah), colors=colors, sample=sample)
    return small, pal, meta


LOOPS = ("none", "pin", "pingpong")


def loop_mode(v):
    """none: the clip as it is (biggest motion). pin: the still is also the last frame (a true loop, but Wan then
    barely moves). pingpong: the clip played forward then backward (loops idles and bounces, not walks)."""
    if v is True:
        return "pin"
    if v in (None, False, ""):
        return "none"
    v = str(v).lower()
    if v not in LOOPS:
        raise ValueError(f"loop must be one of {LOOPS}")
    return v


WAN14_STYLE = ", pixel art sprite animation, 2D game, the subject stays in place in the centre, static camera, plain solid white background"
WAN14_NEGATIVE = ("blurry, smooth gradients, photorealistic, 3d render, text, watermark, camera motion, zoom, morphing into something else, "
                  "extra limbs, deformed, the background changes")
WAN14_FILES = {"unet": ["Wan2.2-I2V-A14B-HighNoise-Q4_K_S.gguf", "Wan2.2-I2V-A14B-LowNoise-Q4_K_S.gguf"],
               "loras": ["wan2.2_i2v_high_noise_lightx2v_4step.safetensors", "wan2.2_i2v_low_noise_lightx2v_4step.safetensors",
                         "wan2.2_pixel_animate.safetensors"], "vae": ["wan_2.1_vae.safetensors"],
               "text_encoders": ["umt5_xxl_fp8_e4m3fn_scaled.safetensors"]}


def wan14_present():
    """Are the Wan 2.2 14B GGUF experts, the distillation LoRAs, the pixel-animate LoRA and the ComfyUI-GGUF node there?"""
    models = os.path.join(ROOT, "ComfyUI", "models")
    files = all(os.path.isfile(os.path.join(models, d, f)) for d, fs in WAN14_FILES.items() for f in fs)
    return files and os.path.isdir(os.path.join(ROOT, "ComfyUI", "custom_nodes", "ComfyUI-GGUF"))


ILLUS_ANIM_STYLE = (", smooth 2D animation of a game character, clean digital illustration, the subject stays in place in the "
                    "centre, static camera, plain solid white background")
ILLUS_ANIM_NEGATIVE = ("blurry, photorealistic, 3d render, pixel art, text, watermark, camera motion, zoom, morphing into something else, "
                       "extra limbs, deformed, the background changes")


def build_anim14_api(still, prompt, frames=8, seconds=1.0, seed=None, negative=WAN14_NEGATIVE, raw=False, video_target=512,
                     bg="#ffffff", dump=False, run_id=None, upscale_mode="nearest", lora_strength=1.0, steps=4, cfg=1.0, style="pixel"):
    """Wan 2.2 I2V A14B (Q4_K_S GGUF, both experts) + lightx2v 4-step LoRAs + styly-agents' pixel-animate LoRA, from the
    still as the first frame (no end frame: that LoRA's workflow has none). Same frame handling as build_anim_api:
    frames saved as PNG + BiRefNet RGBA. The still is enlarged nearest-neighbour by default (the LoRA was trained on real
    pixel animations). Returns (api, info) compatible with finish_anim()."""
    shot = _shot()
    style = style_mode(style)
    aw, ah = still.size
    if style == "illustration":                  # fit the picture into video_target px (it is not a tiny pixel still)
        f = video_target / max(aw, ah)
        vw, vh = max(16, round(aw * f)), max(16, round(ah * f))
        s = round(f, 3)
        big = still.resize((vw, vh), Image.LANCZOS)
    else:
        s = max(1, round(video_target / max(aw, ah)))
        vw, vh = aw * s, ah * s
        big = upscale(still, s) if upscale_mode == "nearest" else still.resize((vw, vh), Image.LANCZOS)
    cw, ch = (vw + 15) // 16 * 16, (vh + 15) // 16 * 16
    canvas = Image.new("RGB", (cw, ch), ImageColor.getrgb(bg))
    canvas.paste(big, (0, 0), big)
    text = prompt if raw else prompt + (ILLUS_ANIM_STYLE if style == "illustration" else WAN14_STYLE)
    if style == "illustration" and negative in (None, "", WAN14_NEGATIVE):
        negative = ILLUS_ANIM_NEGATIVE
    n_clip = shot.frames_for(seconds)
    if seed is None:
        seed = random.randint(0, 2**31)
    rid = run_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    api = json.load(open(os.path.join(ROOT, "workflows", "pixel_wan14_api.json"), encoding="utf-8"))
    api["5"]["inputs"]["text"] = text
    api["6"]["inputs"]["text"] = negative or WAN14_NEGATIVE
    if style == "illustration":                  # no pixel-animate LoRA: the shift nodes read the lightx2v LoRAs directly
        api["12"]["inputs"]["model"] = ["10", 0]
        api["22"]["inputs"]["model"] = ["20", 0]
        api = prune_to(api, ["43", "55"])
    else:
        for nid in ("11", "21"):
            api[nid]["inputs"]["strength_model"] = float(lora_strength)
    api["31"]["inputs"].update(width=cw, height=ch, length=n_clip)
    half = max(1, steps // 2)
    api["40"]["inputs"].update(noise_seed=seed, steps=steps, cfg=cfg, end_at_step=half)
    api["41"]["inputs"].update(noise_seed=seed, steps=steps, cfg=cfg, start_at_step=half, end_at_step=steps)
    api["43"]["inputs"]["filename_prefix"] = f"aivid/{rid}/frames/f"
    api["55"]["inputs"]["filename_prefix"] = f"aivid/{rid}/rgba/f"
    name = f"pixel_{rid}_start.png"
    buf = io.BytesIO()
    canvas.save(buf, "PNG")
    api["30"]["inputs"]["image"] = gensprite.upload(name, buf.getvalue()) if not dump else name
    info = {"seed": seed, "length": n_clip, "seconds": round((n_clip - 1) / VIDEO_FPS, 2), "width": cw, "height": ch, "steps": steps,
            "cfg": cfg, "run_id": rid, "frames_node": "43", "rgba_node": "55", "art_size": (aw, ah), "scale": s, "crop": (vw, vh),
            "frames_out": frames, "loop": "none", "clip_frames": n_clip, "text": text, "prompt": prompt, "upscale": upscale_mode,
            "engine": "wan14", "style": style}
    return api, info


def build_anim_api(still, prompt, frames=8, seconds=1.0, loop="none", seed=None, steps=20, cfg=5.0, negative=ANIM_NEGATIVE,
                   raw=False, video_target=512, bg="#ffffff", dump=False, run_id=None, upscale_mode="smooth", style="pixel"):
    """Wan 2.2 TI2V-5B image-to-video from the pixel still (upscaled nearest to ~video_target px), with the still as
    the first frame and, for loop="pin", as the last frame too (aivid_wan's end-frame node); BiRefNet cuts out every
    decoded frame. `still` is an RGBA image at its art size. upscale_mode: how the still is enlarged for Wan: "smooth"
    (Lanczos; Wan is trained on natural video and keeps the colours of a smooth picture) or "nearest" (blocky; sharper,
    but Wan darkened a flat cartoon slime with it). Returns (api, info)."""
    loop = loop_mode(loop)
    shot = _shot()
    aw, ah = still.size
    s = max(1, round(video_target / max(aw, ah)))
    vw, vh = aw * s, ah * s
    cw, ch = (vw + 31) // 32 * 32, (vh + 31) // 32 * 32     # Wan wants multiples of 32: pad right / bottom
    canvas = Image.new("RGB", (cw, ch), ImageColor.getrgb(bg))
    big = upscale(still, s) if upscale_mode == "nearest" else still.resize((vw, vh), Image.LANCZOS)
    canvas.paste(big, (0, 0), big)
    tmp = os.path.join(tempfile.gettempdir(), f"pixel_anim_{uuid.uuid4().hex}.png")
    canvas.save(tmp)
    style = style_mode(style)
    text = prompt if raw else prompt + (ILLUS_ANIM_STYLE if style == "illustration" else ANIM_STYLE)
    n_clip = shot.frames_for(seconds)
    try:
        api, info = shot.build_api(text, start=tmp, end=tmp if loop == "pin" else None, width=cw, height=ch, seconds=seconds, seed=seed,
                                   steps=steps, cfg=cfg, negative=negative or ANIM_NEGATIVE, run_id=run_id, dump=dump,
                                   upload_prefix="pixel")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    rid = info["run_id"]
    api["50"] = {"inputs": {"bg_removal_name": "birefnet.safetensors"}, "class_type": "LoadBackgroundRemovalModel", "_meta": {"title": "BiRefNet"}}
    api["51"] = {"inputs": {"bg_removal_model": ["50", 0], "image": ["31", 0]}, "class_type": "RemoveBackground", "_meta": {"title": "Cut out every frame"}}
    api["53"] = {"inputs": {"mask": ["51", 0]}, "class_type": "InvertMask", "_meta": {"title": "Invert"}}
    api["54"] = {"inputs": {"image": ["31", 0], "alpha": ["53", 0]}, "class_type": "JoinImageWithAlpha", "_meta": {"title": "Add alpha"}}
    api["55"] = {"inputs": {"filename_prefix": f"aivid/{rid}/rgba/f", "images": ["54", 0]}, "class_type": "SaveImage", "_meta": {"title": "Save RGBA frames"}}
    info.update(rgba_node="55", art_size=(aw, ah), scale=s, crop=(vw, vh), frames_out=frames, loop=loop, clip_frames=n_clip,
                text=text, prompt=prompt, upscale=upscale_mode, engine="wan5b", style=style)
    return api, info


def select_frames(n_clip, n_out, loop):
    """Evenly spaced clip frame indices. With a pinned loop the last clip frame is the still again, so it is left out."""
    loop = loop_mode(loop) == "pin"
    pool = n_clip - 1 if loop and n_clip > 1 else n_clip
    n_out = max(1, min(n_out, pool))
    if n_out == 1:
        return [0]
    if loop:
        return [round(i * pool / n_out) for i in range(n_out)]
    return [round(i * (pool - 1) / (n_out - 1)) for i in range(n_out)]


def finish_anim(hist, info, still, pal, name, out_root=PIXEL_DIR, fps=8, preview_scale=4, keep_frames=False, mp4=True):
    """Pixelize the cut-out frames with the still's palette, write the sheet, GIF, frame PNGs, JSON (and the raw mp4).
    Returns the outputs dict (paths)."""
    shot = _shot()
    rgba, raw = [], []
    for nid, out in hist.get("outputs", {}).items():
        for img in out.get("images", []) or []:
            p = os.path.join(OUT_DIR, img.get("subfolder", ""), img["filename"])
            if nid == info["rgba_node"]:
                rgba.append(p)
            elif nid == info["frames_node"]:
                raw.append(p)
    run_dir = os.path.join(OUT_DIR, "aivid", info["run_id"])
    if not rgba:   # cached run: ComfyUI reports nothing, the files are still on disk
        rgba = sorted(glob.glob(os.path.join(run_dir, "rgba", "*.png")))
        raw = sorted(glob.glob(os.path.join(run_dir, "frames", "*.png")))
    if not rgba:
        raise RuntimeError("no frames in the ComfyUI output")
    rgba.sort()
    raw.sort()
    idx = select_frames(len(rgba), info["frames_out"], info["loop"])
    aw, ah = info["art_size"]
    vw, vh = info["crop"]
    frames = []
    illustration = info.get("style") == "illustration"
    if illustration:
        aw, ah = vw, vh                           # frames stay at the video size
    for i in idx:
        im = Image.open(rgba[i]).convert("RGBA").crop((0, 0, vw, vh))
        if illustration:
            frames.append(im)
            continue
        small, _ = pixelize(im, (aw, ah), palette=pal, offset=(0.5, 0.5))   # the clip's grid is the upscaled still's
        frames.append(small)
    if info["loop"] == "pingpong" and len(frames) > 2:
        frames = frames + frames[-2:0:-1]
    d = os.path.join(out_root, name)
    fdir = os.path.join(d, "frames")
    os.makedirs(fdir, exist_ok=True)
    n = len(frames)
    sheet = Image.new("RGBA", (aw * n, ah), (0, 0, 0, 0))
    frame_paths = []
    for i, fr in enumerate(frames):
        sheet.paste(fr, (i * aw, 0))
        fp = os.path.join(fdir, f"{name}_{i:02d}.png")
        fr.save(fp)
        frame_paths.append(fp)
    sheet_path = os.path.join(d, f"{name}_sheet.png")
    sheet.save(sheet_path)
    if illustration:
        gif_path = write_gif_rgba(frames, os.path.join(d, f"{name}.gif"), fps=fps, scale=1)
    else:
        gif_path = write_gif(frames, pal, os.path.join(d, f"{name}.gif"), fps=fps, scale=preview_scale)
    meta = {"type": "anim", "style": info.get("style", "pixel"), "name": name, "frame_width": aw, "frame_height": ah, "frames": n, "columns": n,
            "fps": fps, "loop": info["loop"], "picked": info["frames_out"], "engine": info.get("engine", "wan5b"),
            "palette": ["#%02x%02x%02x" % c for c in (pal or [])], "sheet": os.path.basename(sheet_path), "gif": os.path.basename(gif_path),
            "clip_frames": len(rgba), "clip_indices": idx, "seed": info["seed"], "video_size": [info["width"], info["height"]],
            "steps": info["steps"], "cfg": info["cfg"], "prompt": info.get("prompt"), "text": info.get("text"), "still": info.get("still")}
    json_path = os.path.join(d, f"{name}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    out = {"sheet": sheet_path, "gif": gif_path, "json": json_path, "frames": frame_paths, "dir": d}
    if mp4 and raw and os.path.isfile(shot.FFMPEG):
        try:
            out["mp4"] = shot.encode_frames(raw, os.path.join(d, f"{name}_wan.mp4"), fps=VIDEO_FPS)
        except RuntimeError as e:
            print("mp4 preview skipped:", e)
    if not keep_frames:
        shutil.rmtree(run_dir, ignore_errors=True)
    return out


# ---------------------------------------------------------------------------------------------------------------
# CLI

def _wait(pid, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        hist = get(f"/history/{pid}")
        if pid in hist:
            h = hist[pid]
            st = h.get("status", {})
            if st.get("status_str") == "error":
                for m in st.get("messages", []):
                    if m[0] == "execution_error":
                        sys.exit(f"\nERROR in node {m[1].get('node_type')}: {m[1].get('exception_message')}")
                sys.exit(2)
            print(f"\ndone in {time.time() - t0:.0f}s")
            return h
        print(f"  {time.time() - t0:.0f}s", end="\r", flush=True)
        time.sleep(2)
    sys.exit("timed out")


def _queue(api):
    res = post("/prompt", {"prompt": api, "client_id": str(uuid.uuid4())})
    if "prompt_id" not in res:
        print(json.dumps(res, indent=1))
        sys.exit(1)
    return res["prompt_id"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt", nargs="?", default="", help="what the pixel art shows (the kind's trigger words and style hints are added)")
    ap.add_argument("--sheet", action="store_true", help="draw a 4x4 walk-cycle sprite sheet (32 px character: 3 walk frames x 4 directions + 4 poses) "
                    "of the character in the prompt and / or --image; splits it into frames + walk GIFs")
    ap.add_argument("--animate", metavar="STILL", help="animate this pixel-art PNG with a video model instead (--engine); the prompt says what moves")
    ap.add_argument("--style", choices=STYLES, default="pixel", help="pixel: pixel art (LoRAs, grid + palette; default). illustration: clean digitally "
                    "illustrated sprites / scenes at the requested size, animated without the pixel LoRA")
    ap.add_argument("--kind", choices=KINDS, default="sprite", help="sprite: character LoRA, cut out (default); scene: scene / object LoRA on the base model, opaque; none: no LoRA")
    ap.add_argument("--image", help="source picture = image 1 (redrawn as pixel art)")
    ap.add_argument("--ref", action="append", default=[], help="extra reference = image 2, image 3 (needs --image)")
    ap.add_argument("--width", type=int, default=None, help="art width in pixels (default 64, scene 128)")
    ap.add_argument("--height", type=int, default=None, help="art height in pixels (default = width, or the source's aspect)")
    ap.add_argument("--scale", type=int, default=None, help="block size: diffusion canvas = art size x scale (default: auto, ~512 px)")
    ap.add_argument("--colors", type=int, default=32, help="palette size (default 32; 0 = keep every colour)")
    ap.add_argument("--sample", choices=["center", "box"], default="center", help="center: one sample per block at the best-aligned grid offset (default, crisp); box: average each block (softer)")
    ap.add_argument("--model", choices=["auto", "distilled", "base"], default="auto", help="auto: the kind's default (sprite: distilled, scene: base)")
    ap.add_argument("--lora-strength", type=float, default=None, help="LoRA weight (default 1.0)")
    ap.add_argument("--alpha", choices=["auto", "keep", "none"], default=None, help="auto: BiRefNet cut-out; keep: source alpha; none: opaque (default by kind)")
    ap.add_argument("--steps", type=int, default=None)
    ap.add_argument("--cfg", type=float, default=None, help="base model / animation CFG")
    ap.add_argument("--negative", default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--raw", action="store_true", help="use the prompt verbatim: no trigger words or style hints")
    ap.add_argument("--name", default=None)
    ap.add_argument("--frames", type=int, default=8, help="animation: frames in the sheet (default 8)")
    ap.add_argument("--fps", type=int, default=8, help="animation: playback speed of the GIF / JSON (default 8)")
    ap.add_argument("--seconds", type=float, default=1.0, help="animation: length of the Wan clip the frames are picked from (default 1.0)")
    ap.add_argument("--loop", choices=LOOPS, default="none", help="animation: none = the clip as it is (default, biggest motion); "
                    "pin = the still is also the last frame (true loop, but Wan then barely moves); pingpong = forward then backward (idles, bounces)")
    ap.add_argument("--preview-scale", type=int, default=4, help="animation: GIF upscale factor (default 4)")
    ap.add_argument("--upscale", choices=["auto", "smooth", "nearest"], default="auto", help="animation: how the still is enlarged for Wan (auto = smooth for wan5b, nearest for wan14)")
    ap.add_argument("--engine", choices=["wan14", "wan5b"], default="wan14", help="animation: wan14 = Wan 2.2 14B (GGUF) + the pixel-animate LoRA, 4 steps (default); "
                    "wan5b = Wan 2.2 TI2V-5B without a pixel LoRA (the old, weak route)")
    ap.add_argument("--keep-frames", action="store_true", help="animation: keep the raw frames in ComfyUI/output/aivid/<run>")
    ap.add_argument("--out", default=PIXEL_DIR)
    ap.add_argument("--dump", help="write the patched API prompt JSON here and exit")
    ap.add_argument("--timeout", type=int, default=1800)
    a = ap.parse_args()

    if a.sheet:
        try:
            api, final_node, state = build_sheet_api(a.prompt, a.image, a.seed, a.steps, a.cfg, 1.0 if a.lora_strength is None else a.lora_strength,
                                                     a.raw, a.negative or NEGATIVE, dump=bool(a.dump))
        except ValueError as e:
            sys.exit(str(e))
        if a.dump:
            json.dump(api, open(a.dump, "w", encoding="utf-8"), indent=1)
            print("wrote", a.dump)
            return
        pid = _queue(api)
        print(f"queued {pid}  sheet model=base steps={state['steps']} cfg={state['cfg']} seed={state['seed']} reference={'yes' if a.image else 'no'}")
        hist = _wait(pid, a.timeout)
        result = None
        for nid, out in hist.get("outputs", {}).items():
            for img in out.get("images", []):
                if nid == final_node:
                    result = os.path.join(OUT_DIR, img.get("subfolder", ""), img["filename"])
        if not result:
            sys.exit("no output image")
        name = a.name or asset_name(a.prompt or os.path.splitext(os.path.basename(a.image))[0], "Sheet")
        out = finish_sheet(result, name, a.out, a.colors, a.fps, a.preview_scale, a.sample, state["seed"], state["text"], a.prompt, a.image)
        print(f"  sheet: {out['sheet']}  ({SHEET_ART}x{SHEET_ART}, 16 cells of {SHEET_CELL} px, {len(out['palette'])} colours)")
        print(f"  gifs: {', '.join(os.path.basename(g) for g in out['gifs'].values())}")
        return

    if a.animate:
        still, pal, meta = load_still(a.animate, a.width, a.height, a.colors, a.sample, a.style)
        if a.engine == "wan14":
            if not wan14_present():
                sys.exit("engine wan14 needs the Wan 2.2 14B GGUF files, the lightx2v + pixel-animate LoRAs and ComfyUI-GGUF (see the README)")
            api, info = build_anim14_api(still, a.prompt, a.frames, a.seconds, a.seed, a.negative or WAN14_NEGATIVE, a.raw, dump=bool(a.dump),
                                         upscale_mode=a.upscale if a.upscale != "auto" else "nearest",
                                         lora_strength=1.0 if a.lora_strength is None else a.lora_strength, steps=a.steps or 4,
                                         cfg=a.cfg if a.cfg is not None else 1.0, style=a.style)
        else:
            api, info = build_anim_api(still, a.prompt, a.frames, a.seconds, a.loop, a.seed, a.steps or 20, a.cfg if a.cfg is not None else 5.0,
                                       a.negative or ANIM_NEGATIVE, a.raw, dump=bool(a.dump),
                                       upscale_mode=a.upscale if a.upscale != "auto" else "smooth", style=a.style)
        info["still"] = os.path.abspath(a.animate)
        if a.dump:
            json.dump(api, open(a.dump, "w", encoding="utf-8"), indent=1)
            print("wrote", a.dump)
            return
        pid = _queue(api)
        print(f"queued {pid}  animate {still.width}x{still.height} x{info['scale']} -> {info['width']}x{info['height']}, "
              f"{info['length']} clip frames -> {a.frames} frames, seed={info['seed']} loop={a.loop} style={a.style} palette={len(pal or [])}")
        hist = _wait(pid, a.timeout)
        name = a.name or (os.path.splitext(os.path.basename(a.animate))[0] + asset_name(a.prompt, "Anim")[:20])
        out = finish_anim(hist, info, still, pal, name, a.out, a.fps, a.preview_scale, a.keep_frames)
        for k in ("sheet", "gif", "json", "mp4"):
            if out.get(k):
                print(f"  {k}: {out[k]}")
        return

    if not a.prompt:
        sys.exit("give a prompt")
    try:
        api, final_node, state = build_still_api(a.prompt, a.image, a.ref, a.kind, a.width, a.height, a.scale, a.model, a.steps,
                                                 a.cfg, a.negative or NEGATIVE, a.seed, a.raw, a.lora_strength, a.alpha, dump=bool(a.dump),
                                                 style=a.style)
    except ValueError as e:
        sys.exit(str(e))
    if a.dump:
        json.dump(api, open(a.dump, "w", encoding="utf-8"), indent=1)
        print("wrote", a.dump)
        return
    pid = _queue(api)
    ws, (aw, ah) = state["work_size"], state["art_size"]
    print(f"queued {pid}  style={a.style} kind={a.kind} model={state['model']} steps={state['steps']} cfg={state['cfg']} seed={state['seed']} "
          f"canvas={ws[0]}x{ws[1]} art={aw}x{ah} (x{state['scale']}) lora={state['lora']} alpha={state['alpha']}")
    hist = _wait(pid, a.timeout)
    result = None
    for nid, out in hist.get("outputs", {}).items():
        for img in out.get("images", []):
            p = os.path.join(OUT_DIR, img.get("subfolder", ""), img["filename"])
            print("  image:", p)
            if nid == final_node:
                result = p
    if not result:
        sys.exit("no output image")
    name = a.name or asset_name(a.prompt, "Pixel")
    dst, pal = finish_still(result, os.path.join(a.out, name + ".png"), state, a.colors, a.sample)
    print(f"  pixel art: {dst}  ({aw}x{ah}, {len(pal or [])} colours)")


if __name__ == "__main__":
    main()
