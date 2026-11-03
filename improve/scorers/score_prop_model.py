"""Scorer for the prop-model track: how close Claude's prop_kit models come to the artist's own props when the
picture is the prop's own reference render.

    python improve/scorers/score_prop_model.py --config <refs.json> --style <id> --out score.json --workdir <dir>
                                         [--props A,B] [--model opus] [--jobs 3] [--refs-cache refs.json]

For every held-out prop of the style's reference library (ai-3d/scripts/style_refs.py, style_index.py): copies the
original's three-quarter textured render as the picture, has Claude model it with prop_model.py (the same call the
studio's prop flow makes, with the style's brief and reference props), renders the four clay views of the result
like the library's, and compares them with the original's clay views on CLIP ViT-H embeddings (style_clip.py).
No picture generation and no painting, so no ComfyUI server; Claude (modelling, 3 at a time), Blender (renders)
and a few seconds of GPU for CLIP. Writes to --out:

  pair_clay           mean cosine over the 4 x 4 view pairs, averaged over props (the metric; higher is closer)
  pair_clay_sameview  the same over matching views only
  tris_ratio_median, size_ratio_median   generated / original
  failed              props Claude could not build (each one is listed in per_prop with "error")
  cost_usd, seconds, per_prop, sheet (original clay | generated clay per prop, for a person or the reflect step)

The reference props a held-out prop is shown are picked once (prop_chain.refs_for, a Claude call) and cached in
--refs-cache, so every iteration shows the same references. Run with ai-3d's ComfyUI venv python.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SCRIPTS = os.path.join(ROOT, "ai-3d", "scripts")
sys.path.insert(0, SCRIPTS)

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

import prop_chain  # noqa: E402
import prop_model  # noqa: E402
import prop_style  # noqa: E402
import style_eval  # noqa: E402
from style_clip import VIEWS, Clip  # noqa: E402


def _log(msg):
    print(msg, flush=True)


def _refs(cache_path, st, props, index):
    cache = {}
    if cache_path and os.path.isfile(cache_path):
        cache = json.load(open(cache_path, encoding="utf-8"))
    for p in props:
        if p not in cache:
            cache[p] = prop_chain.refs_for(st, index[p]["desc"])
            _log(f"refs {p}: {', '.join(cache[p]) or '(none)'}")
    if cache_path:
        os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
        json.dump(cache, open(cache_path, "w", encoding="utf-8"), indent=1)
    return cache


STRIP = ("import bpy,bmesh,sys;p=sys.argv[-1];bpy.ops.wm.read_factory_settings(use_empty=True);bpy.ops.import_scene.gltf(filepath=p)\n"
         "for o in [o for o in bpy.data.objects if o.type=='MESH']:\n"
         "    idx={i for i,m in enumerate(o.data.materials) if m and m.name.startswith('Decal')}\n"
         "    if not idx: continue\n"
         "    bm=bmesh.new();bm.from_mesh(o.data);bmesh.ops.delete(bm,geom=[f for f in bm.faces if f.material_index in idx],context='FACES')\n"
         "    bm.to_mesh(o.data);bm.free()\n"
         "bpy.ops.export_scene.gltf(filepath=p,export_format='GLB')\n")


def _strip_decals(glb):
    """Drops the faces with Decal* materials (prop_kit.decal: painted marks that the paint step removes too), so the
    clay views and the triangle count are those of the final mesh, like the artist's originals."""
    import paint
    r = subprocess.run([paint.BLENDER, "-b", "--python-exit-code", "1", "--python-expr", STRIP, "--", glb],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("decal strip failed:\n" + (r.stdout + r.stderr)[-800:])


def _sheet(ref, workdir, props, rows, out):
    cell, pad = 256, 6
    img = Image.new("RGB", (2 * cell + 3 * pad, len(props) * (cell + pad) + pad), (255, 255, 255))
    dr = ImageDraw.Draw(img)
    for i, p in enumerate(props):
        y = pad + i * (cell + pad)
        for j, folder in enumerate((os.path.join(ref, p), os.path.join(workdir, p))):
            f = os.path.join(folder, "clay_325.png")
            x = pad + j * (cell + pad)
            if os.path.isfile(f):
                img.paste(Image.open(f).convert("RGB").resize((cell, cell)), (x, y))
            else:
                dr.rectangle([x, y, x + cell, y + cell], fill=(235, 235, 235))
        r = rows.get(p, {})
        label = p if "error" in r else f"{p}  pair {r.get('pair_clay', 0):.3f}  tris {r.get('tris')}/{r.get('tris_orig')}"
        dr.text((pad + 4, y + 4), label, fill=(0, 0, 0))
    img.save(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True, help="the style's refs.json (reference library config)")
    ap.add_argument("--style", required=True, help="style id (ai-3d/styles/<id>.json or styles/private/<id>/style.json)")
    ap.add_argument("--out", required=True, help="score JSON to write")
    ap.add_argument("--workdir", required=True, help="folder for the models and renders of this scoring run")
    ap.add_argument("--props", default="", help="subset: Name,Name (default: every held-out prop)")
    ap.add_argument("--model", default="opus", help="Claude model for the modelling sessions")
    ap.add_argument("--jobs", type=int, default=3, help="modelling sessions at a time")
    ap.add_argument("--refs-cache", default="", help="JSON cache of the reference props shown per held-out prop")
    ap.add_argument("--rounds", type=int, default=6)
    a = ap.parse_args()

    t0 = time.time()
    # Blender renders from the config's "out": it must be absolute, so make every path absolute up front
    a.out, a.workdir = os.path.abspath(a.out), os.path.abspath(a.workdir)
    a.refs_cache = os.path.abspath(a.refs_cache) if a.refs_cache else ""
    cfg, index = style_eval._config(os.path.join(ROOT, a.config) if not os.path.isabs(a.config) else a.config)
    st = prop_style.load(a.style)
    if not st:
        raise SystemExit(f"unknown style {a.style}")
    ref = cfg["out"]
    props = [p for p in a.props.split(",") if p] or [p for p in cfg["held_out"] if p in index]
    missing = [p for p in props if not os.path.isfile(os.path.join(ref, p, "front_325.png"))]
    if missing:
        raise SystemExit(f"no reference renders for {', '.join(missing)}: run style_refs.py render first")
    os.makedirs(a.workdir, exist_ok=True)
    refs = _refs(a.refs_cache, st, props, index)

    rows, lock = {}, threading.Lock()
    sem = threading.Semaphore(max(1, a.jobs))

    def model_one(p):
        with sem:
            d = os.path.join(a.workdir, p)
            os.makedirs(d, exist_ok=True)
            pic = os.path.join(d, "picture.png")
            shutil.copyfile(os.path.join(ref, p, "front_325.png"), pic)
            if os.path.isfile(os.path.join(d, "model.glb")) and os.path.isfile(os.path.join(d, "model.json")):
                with lock:   # a resumed scoring run: the model is there, keep its cost
                    rows[p] = json.load(open(os.path.join(d, "model.json"), encoding="utf-8"))
                return
            try:
                m = prop_model.model_prop(pic, index[p]["desc"], os.path.join(d, "model"), p, model=a.model,
                                          rounds=a.rounds, style=a.style, refs=refs.get(p, []))
                shutil.copyfile(m["glb"], os.path.join(d, "model.glb"))
                _strip_decals(os.path.join(d, "model.glb"))
                row = {"cost_usd": m.get("cost_usd") or 0, "seconds": m["seconds"], "turns": m.get("turns")}
                json.dump(row, open(os.path.join(d, "model.json"), "w", encoding="utf-8"), indent=1)
                with lock:
                    rows[p] = row
                _log(f"modelled {p} in {m['seconds']} s, ${m.get('cost_usd') or 0:.2f}")
            except Exception as e:  # noqa: BLE001
                with lock:
                    rows[p] = {"error": str(e)[-400:], "cost_usd": 0}
                _log(f"MODEL FAILED {p}: {traceback.format_exc()[-600:]}")

    ts = [threading.Thread(target=model_one, args=(p,)) for p in props]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    built = [p for p in props if "error" not in rows[p]]
    if built:
        _log("rendering clay views")
        style_eval._render(a.workdir)
    clip = Clip()
    for p in built:
        try:
            g = style_eval._glb_stats(os.path.join(a.workdir, p, "model.glb"))
            o = index[p]
            gen = [clip.embed(os.path.join(a.workdir, p, f"clay_{v}.png")) for v in VIEWS]
            orig = [clip.embed(os.path.join(ref, p, f"clay_{v}.png")) for v in VIEWS]
            rows[p].update({
                "pair_clay": round(float(np.mean([x @ y for x in gen for y in orig])), 4),
                "pair_clay_sameview": round(float(np.mean([x @ y for x, y in zip(gen, orig)])), 4),
                "tris": g["tris"], "tris_orig": o["tris"], "tris_ratio": round(g["tris"] / max(o["tris"], 1), 2),
                "size_ratio": round(max(g["dims"]) / max(max(o["dims"]), 1e-6), 3)})
        except Exception as e:  # noqa: BLE001
            rows[p]["error"] = f"scoring: {str(e)[-300:]}"
            _log(f"SCORE FAILED {p}: {traceback.format_exc()[-600:]}")
    ok = [p for p in props if "error" not in rows[p]]
    sheet = os.path.join(a.workdir, "sheet.png")
    _sheet(ref, a.workdir, props, rows, sheet)
    rep = {"props": props, "scored": len(ok), "failed": len(props) - len(ok),
           "pair_clay": round(float(np.mean([rows[p]["pair_clay"] for p in ok])), 4) if ok else 0.0,
           "pair_clay_sameview": round(float(np.mean([rows[p]["pair_clay_sameview"] for p in ok])), 4) if ok else 0.0,
           "tris_ratio_median": round(float(np.median([rows[p]["tris_ratio"] for p in ok])), 3) if ok else 0.0,
           "size_ratio_median": round(float(np.median([rows[p]["size_ratio"] for p in ok])), 3) if ok else 0.0,
           "cost_usd": round(sum(r.get("cost_usd") or 0 for r in rows.values()), 2),
           "seconds": round(time.time() - t0), "model": a.model, "sheet": sheet, "per_prop": rows}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    json.dump(rep, open(a.out, "w", encoding="utf-8"), indent=1)
    _log(f"pair_clay {rep['pair_clay']:.4f} (same view {rep['pair_clay_sameview']:.4f}), tris x{rep['tris_ratio_median']:.2f}, "
         f"size x{rep['size_ratio_median']:.2f}, failed {rep['failed']}, ${rep['cost_usd']:.2f}, {rep['seconds']} s")


if __name__ == "__main__":
    main()
