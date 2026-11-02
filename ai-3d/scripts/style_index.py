"""Index a style's reference library (after style_refs.py export + render): index.json with every prop's stats,
one-line description and texture measurements, and catalog_<n>.png sheets of the props Claude may pick as references.

    python style_index.py <config.json>

The descriptions come from the file the config's "descriptions" names (relative to the config: {"Name": "a red
apple", ...}); write them once by looking at the renders. Props listed in "held_out" (kept for style_eval.py) and
"hidden" (near-copies of held-out ones) are indexed but left out of the catalog, so an evaluation never shows Claude
the prop it is asked to make.
"""
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


def texture_stats(path):
    """Median value (0-255, max channel) and saturation of the painted texels (away from UV island borders), and
    'flat': the share of texels within 12 levels of their 9x9 mean, i.e. smooth colour rather than noise."""
    im = Image.open(path).convert("RGB")
    a = np.asarray(im).astype(np.float32)
    m = a.sum(-1) > 15
    m = np.asarray(Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(11))) > 0
    if not m.any():
        return None
    v = a.max(-1)
    s = np.where(v > 0, (a.max(-1) - a.min(-1)) / np.maximum(v, 1), 0)
    blur = np.asarray(im.filter(ImageFilter.BoxBlur(4))).astype(np.float32)
    flat = np.abs(a - blur).max(-1) < 12
    return {"value": round(float(np.median(v[m]))), "sat": round(float(np.median(s[m])), 3),
            "flat": round(float(flat[m].mean()), 3)}


def load(config_path):
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    desc_path = os.path.join(os.path.dirname(os.path.abspath(config_path)), cfg.get("descriptions", "descriptions.json"))
    desc = json.load(open(desc_path, encoding="utf-8")) if os.path.isfile(desc_path) else {}
    return cfg, desc


def build(config_path):
    cfg, desc = load(config_path)
    out = cfg["out"]
    held, hidden = set(cfg.get("held_out", [])), set(cfg.get("hidden", []))
    index = []
    for name in sorted(os.listdir(out)):
        sp = os.path.join(out, name, "stats.json")
        if not os.path.isfile(sp):
            continue
        st = json.load(open(sp, encoding="utf-8"))
        tex = [t[1] for t in st.get("textures", [])]
        index.append({"name": name, "desc": desc.get(name, ""), "tris": st["tris"], "dims": st["dims"],
                      "parts": st["parts"], "uv_islands": st["uv_islands"],
                      "texture": texture_stats(os.path.join(out, name, tex[0])) if tex else None,
                      "held_out": name in held, "hidden": name in hidden})
    missing = [e["name"] for e in index if not e["desc"]]
    if missing:
        print("no description for: " + ", ".join(missing))
    json.dump(index, open(os.path.join(out, "index.json"), "w", encoding="utf-8"), indent=1)

    pool = [e for e in index if not e["held_out"] and not e["hidden"]]
    try:
        font = ImageFont.truetype("segoeui.ttf", 13)
    except OSError:
        font = ImageFont.load_default()
    S, cols, rows = 256, 6, 4
    sheets = []
    for k in range(0, len(pool), cols * rows):
        page = pool[k:k + cols * rows]
        W = Image.new("RGB", (S * cols, (S + 34) * ((len(page) + cols - 1) // cols)), (30, 30, 30))
        dr = ImageDraw.Draw(W)
        for i, e in enumerate(page):
            x, y = (i % cols) * S, (i // cols) * (S + 34)
            W.paste(Image.open(os.path.join(out, e["name"], "front_325.png")).convert("RGB").resize((S, S)), (x, y + 34))
            dr.text((x + 4, y + 1), f"{e['name']} ({e['tris']} tris)", fill=(255, 255, 255), font=font)
            dr.text((x + 4, y + 17), e["desc"][:40], fill=(180, 180, 180), font=font)
        p = os.path.join(out, f"catalog_{len(sheets) + 1}.png")
        W.save(p)
        sheets.append({"sheet": os.path.basename(p), "props": [e["name"] for e in page]})
    json.dump(sheets, open(os.path.join(out, "catalog.json"), "w", encoding="utf-8"), indent=1)
    print(f"INDEX {len(index)} props, {len(pool)} in {len(sheets)} catalog sheets, held out {len(held & {e['name'] for e in index})}")


if __name__ == "__main__":
    build(sys.argv[1])
