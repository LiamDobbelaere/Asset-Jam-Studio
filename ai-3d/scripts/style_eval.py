"""Measure how close a style gets to a set of hand-made props: generate the held-out props of a reference library
(style_refs.py, style_index.py) from their one-line descriptions, render them like the references, and score them.

    python style_eval.py run <config.json> <style> <run> [--props A,B] [--seed 7] [--model opus] [--from <run>]
    python style_eval.py score <config.json> <run> [<run> ...]
    python style_eval.py fit <config.json> <run> '{"flatten": 0.85, ...}' ['{...}' ...]
    python style_eval.py rebake <config.json> <run> <new run> '{"flatten": 0.85, ...}'
    python style_eval.py floor <config.json> <run>

run: the modelled-prop chain as the studio's prop flow runs it (picture -> Claude models it -> paint), for every
"held_out" prop of the config; three models at a time (Claude, no GPU), pictures and paint one at a time (ComfyUI
must be running). Results: assets/style_eval/<run>/<Prop>/ (picture.png, model/, out/, model.glb). --from <run> reuses
that run's references, pictures and models and only paints again: to measure a paint change on its own.
score: renders each run's props with style_refs.py (same cameras and light as the references) and reports, per run:
- probe: a "theirs or generated?" logistic regression on CLIP ViT-H embeddings of the renders, leave-one-prop-out
  over the held-out originals and the generated props. 0.5 = cannot tell them apart, 1.0 = always can. With it its
  90 % interval (bootstrap over props) and "chance", the highest probe random labels reach at this many props
  (style_clip.probe_stats): a probe below it cannot be told from 0.5.
- style: how much the renders look like the library (style_clip.style_score), generated vs the originals; "gap" is
  the originals' minus the generated: 0 = as close to the artist's other props as the artist's own held-out ones.
- pair: mean cosine similarity between a generated prop's renders and its original's.
- tris and size ratios (generated / original; the size ratio is the unit scale between the two) and texture stats.
Writes report.json, compare.png (original | generated, two views and the textures) and blind.png + blind_key.json
(the originals and the generated props shuffled, for a person to pick from). Prints one summary line per run.
fit: re-bakes a run's painted views (no new painting, ~10 s per prop) with each candidate finish (paint_project.py's
JSON) and compares the medians of the texture stats with the reference library's: value, saturation, flatness.
rebake: the same with one finish, saved as a new run (same models and views) for score.
floor: the probe and style gap between each held-out prop and the artist's own most similar other prop (the first
reference the run picked for it): how far apart two of the artist's own props already are, a realistic target.
"""
import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import threading
import traceback

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPTS)
EVAL = os.path.join(ROOT, "assets", "style_eval")
VIEWS = ("325", "035", "090", "180")
sys.path.insert(0, SCRIPTS)


def _config(path):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    index = json.load(open(os.path.join(cfg["out"], "index.json"), encoding="utf-8"))
    return cfg, {e["name"]: e for e in index}


def run(cfg_path, style, name, props=None, seed=7, model="opus", source=None):
    import paint
    import prop_chain
    import prop_model
    import prop_refs
    import prop_style
    cfg, index = _config(cfg_path)
    st = prop_style.load(style)
    props = props or cfg["held_out"]
    root = os.path.join(EVAL, name)
    os.makedirs(root, exist_ok=True)
    json.dump({"style": style, "config": os.path.abspath(cfg_path), "props": props, "seed": seed, "from": source},
              open(os.path.join(root, "run.json"), "w"), indent=1)
    if source:
        for p in props:
            d, src = os.path.join(root, p), os.path.join(EVAL, source, p)
            os.makedirs(d, exist_ok=True)
            for f in ("refs.json", "picture.png", "model.json"):
                if os.path.isfile(os.path.join(src, f)) and not os.path.isfile(os.path.join(d, f)):
                    shutil.copyfile(os.path.join(src, f), os.path.join(d, f))

    refs = {}
    for i, p in enumerate(props):   # reference props and pictures first, while the GPU is free
        d = os.path.join(root, p)
        os.makedirs(d, exist_ok=True)
        rp = os.path.join(d, "refs.json")
        if not os.path.isfile(rp):
            json.dump(prop_chain.refs_for(st, index[p]["desc"]), open(rp, "w"))
        refs[p] = json.load(open(rp))
        if os.path.isfile(os.path.join(d, "picture.png")):
            continue
        shutil.copyfile(prop_chain.picture(index[p]["desc"], st, seed + i, refs[p], log=lambda m: print("  " + m, flush=True)),
                        os.path.join(d, "picture.png"))
        print(f"picture {p}" + (f" (refs {', '.join(refs[p])})" if refs[p] else ""), flush=True)

    sem = threading.Semaphore(3)

    def model_one(p):
        with sem:
            d = os.path.join(root, p)
            try:
                m = prop_model.model_prop(os.path.join(d, "picture.png"), index[p]["desc"], os.path.join(d, "model"), p,
                                          model=model, style=style, refs=refs[p])
                json.dump({k: m[k] for k in ("glb", "paint", "cost_usd", "seconds", "turns")},
                          open(os.path.join(d, "model.json"), "w"), indent=1)
                print(f"modelled {p} in {m['seconds']} s, ${m['cost_usd'] or 0:.2f}", flush=True)
            except Exception:
                print(f"MODEL FAILED {p}\n{traceback.format_exc()[-800:]}", flush=True)
    ts = [threading.Thread(target=model_one, args=(p,)) for p in props
          if not os.path.isfile(os.path.join(root, p, "model.json"))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    for i, p in enumerate(props):
        d = os.path.join(root, p)
        if not os.path.isfile(os.path.join(d, "model.json")) or os.path.isfile(os.path.join(d, "model.glb")):
            continue
        m = json.load(open(os.path.join(d, "model.json")))
        pp = m["paint"]
        prompt = " | ".join(s.strip() for s in (pp.get("prompt") or index[p]["desc"], pp.get("front") or "", pp.get("plain") or ""))
        try:
            out = paint.paint(m["glb"], os.path.join(d, "picture.png"), prompt, p, os.path.join(d, "out"), pp.get("lock") or [],
                              low=bool(pp.get("low")), seed=seed + i, style=st, fbx=False, log=lambda s: None,
                              refs=[prop_refs.front_render(st, r) for r in refs[p][:1]])
            shutil.copyfile(out["glb"], os.path.join(d, "model.glb"))
            json.dump({"workdir": out["workdir"], "diffuse": out["diffuse"], "tris": out["bake"]["tris"]},
                      open(os.path.join(d, "paint.json"), "w"), indent=1)
            print(f"painted {p}", flush=True)
        except Exception:
            print(f"PAINT FAILED {p}\n{traceback.format_exc()[-800:]}", flush=True)


from style_clip import Clip, library, probe, probe_stats, style_score   # noqa: E402


def _render(folder):
    import paint
    cfg = os.path.join(folder, "_render.json")
    json.dump({"out": folder}, open(cfg, "w"))
    r = subprocess.run([paint.BLENDER, "-b", "--python-exit-code", "1", "--python", os.path.join(SCRIPTS, "style_refs.py"),
                        "--", "render", cfg], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError("rendering failed:\n" + (r.stdout + r.stderr)[-1500:])


def _glb_stats(glb):
    import paint
    code = ("import bpy,json,sys;bpy.ops.wm.read_factory_settings(use_empty=True);bpy.ops.import_scene.gltf(filepath=sys.argv[-1]);"
            "ms=[o for o in bpy.data.objects if o.type=='MESH'];[m.data.calc_loop_triangles() for m in ms];"
            "from mathutils import Vector;pts=[o.matrix_world@Vector(c) for o in ms for c in o.bound_box];"
            "print('GLBSTATS '+json.dumps({'tris':sum(len(o.data.loop_triangles) for o in ms),"
            "'dims':[max(p[i] for p in pts)-min(p[i] for p in pts) for i in range(3)]}))")
    r = subprocess.run([paint.BLENDER, "-b", "--python-expr", code, "--", glb], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return json.loads(next(ln for ln in r.stdout.splitlines() if ln.startswith("GLBSTATS "))[9:])


def score(cfg_path, runs):
    from style_index import texture_stats
    cfg, index = _config(cfg_path)
    held = [p for p in cfg["held_out"] if p in index]
    clip = Clip()
    ref = cfg["out"]
    orig = {p: [clip.embed(os.path.join(ref, p, f"front_{v}.png")) for v in VIEWS] for p in held}
    _, lib = library(cfg, clip)
    style_orig = float(np.mean([style_score(e, lib) for p in held for e in orig[p]]))
    for name in runs:
        root = os.path.join(EVAL, name)
        _render(root)
        props = [p for p in held if os.path.isfile(os.path.join(root, p, "front_325.png"))]
        gen = {p: [clip.embed(os.path.join(root, p, f"front_{v}.png")) for v in VIEWS] for p in props}
        rows = {}
        for p in props:
            g = _glb_stats(os.path.join(root, p, "model.glb"))
            o = index[p]
            dims_o = sorted(o["dims"])[-1]
            pj = json.load(open(os.path.join(root, p, "paint.json")))
            tex = texture_stats(pj["diffuse"])
            rows[p] = {"pair": round(float(np.mean([a @ b for a in gen[p] for b in orig[p]])), 3),
                       "tris": g["tris"], "tris_orig": o["tris"], "tris_ratio": round(g["tris"] / max(o["tris"], 1), 2),
                       "size_ratio": round(max(g["dims"]) / max(dims_o, 1e-6), 3), "texture": tex, "texture_orig": o["texture"]}
        ps = probe_stats({p: orig[p] for p in props}, gen)
        pairs = np.array([rows[p]["pair"] for p in props])
        rng = np.random.default_rng(0)
        pb = [pairs[rng.choice(len(pairs), len(pairs))].mean() for _ in range(1000)]   # bootstrap over props
        style_gen = float(np.mean([style_score(e, lib) for p in props for e in gen[p]]))
        rep = {"run": name, "props": len(props), "probe": ps["probe"], "probe_ci": ps["ci"], "chance": ps["chance"],
               "style": round(style_gen, 4), "style_orig": round(style_orig, 4), "style_gap": round(style_orig - style_gen, 4),
               "pair": round(float(np.mean([r["pair"] for r in rows.values()])), 3),
               "pair_ci": [round(float(np.percentile(pb, 5)), 3), round(float(np.percentile(pb, 95)), 3)],
               "tris_ratio_median": float(np.median([r["tris_ratio"] for r in rows.values()])),
               "size_ratio_median": float(np.median([r["size_ratio"] for r in rows.values()])),
               "texture_value_diff": float(np.median([r["texture"]["value"] - r["texture_orig"]["value"] for r in rows.values()
                                                      if r["texture"] and r["texture_orig"]])),
               "texture_flat_diff": float(np.median([r["texture"]["flat"] - r["texture_orig"]["flat"] for r in rows.values()
                                                     if r["texture"] and r["texture_orig"]])),
               "per_prop": rows}
        json.dump(rep, open(os.path.join(root, "report.json"), "w"), indent=1)
        _sheets(root, ref, props, index)
        print(f"{name}: probe {rep['probe']:.2f} (90% {rep['probe_ci'][0]:.2f}-{rep['probe_ci'][1]:.2f}, chance up to "
              f"{rep['chance']:.2f}), style gap {rep['style_gap']:+.3f}, pair {rep['pair']:.3f} ({rep['pair_ci'][0]:.3f}-"
              f"{rep['pair_ci'][1]:.3f}), tris x{rep['tris_ratio_median']:.2f}, "
              f"size x{rep['size_ratio_median']:.2f}, texture value {rep['texture_value_diff']:+.0f}, flat {rep['texture_flat_diff']:+.2f}")


def _font(size):
    try:
        return ImageFont.truetype("segoeui.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _sheets(root, ref, props, index):
    T = 220
    cols = [("yours", ref, "front_325.png"), ("yours, back", ref, "front_180.png"), ("generated", root, "front_325.png"),
            ("generated, back", root, "front_180.png")]
    W = Image.new("RGB", (130 + T * (len(cols) + 2), 30 + len(props) * T), (34, 34, 38))
    dr = ImageDraw.Draw(W)
    for j, (h, _, _) in enumerate(cols + [("your texture", 0, 0), ("generated texture", 0, 0)]):
        dr.text((130 + j * T + 4, 6), h, fill=(230, 230, 230), font=_font(15))
    for i, p in enumerate(props):
        y = 30 + i * T
        dr.text((6, y + 6), p, fill=(230, 230, 230), font=_font(14))
        dr.text((6, y + 26), f"{index[p]['tris']} -> {json.load(open(os.path.join(root, 'report.json')))['per_prop'][p]['tris'] if os.path.isfile(os.path.join(root, 'report.json')) else '?'} tris", fill=(170, 170, 170), font=_font(12))
        ims = [os.path.join(base, p, f) for _, base, f in cols]
        st = json.load(open(os.path.join(ref, p, "stats.json")))
        ims += [os.path.join(ref, p, st["textures"][0][1]) if st["textures"] else None,
                json.load(open(os.path.join(root, p, "paint.json")))["diffuse"]]
        for j, f in enumerate(ims):
            if f and os.path.isfile(f):
                W.paste(Image.open(f).convert("RGB").resize((T - 4, T - 4)), (130 + j * T, y))
    W.save(os.path.join(root, "compare.png"))

    tiles = [("yours", os.path.join(ref, p, "front_325.png")) for p in props] + \
            [("generated", os.path.join(root, p, "front_325.png")) for p in props]
    random.Random(len(props)).shuffle(tiles)
    n, c = len(tiles), 6
    B = Image.new("RGB", (c * T, ((n + c - 1) // c) * (T + 22)), (34, 34, 38))
    db = ImageDraw.Draw(B)
    for k, (_, f) in enumerate(tiles):
        x, y = (k % c) * T, (k // c) * (T + 22)
        B.paste(Image.open(f).convert("RGB").resize((T - 4, T - 4)), (x, y + 22))
        db.text((x + 4, y + 2), str(k + 1), fill=(230, 230, 230), font=_font(15))
    B.save(os.path.join(root, "blind.png"))
    json.dump({str(k + 1): who for k, (who, _) in enumerate(tiles)}, open(os.path.join(root, "blind_key.json"), "w"), indent=1)


def fit(cfg_path, name, finishes):
    import paint
    from style_index import texture_stats
    cfg, index = _config(cfg_path)
    pool = [e["texture"] for e in index.values() if e["texture"] and not e["held_out"] and not e["hidden"]]
    want = {k: float(np.median([t[k] for t in pool])) for k in ("value", "sat", "flat")}
    root = os.path.join(EVAL, name)
    props = [p for p in sorted(os.listdir(root)) if os.path.isfile(os.path.join(root, p, "paint.json"))]
    print(f"references: value {want['value']:.0f}, sat {want['sat']:.2f}, flat {want['flat']:.2f}")
    for k, fin in enumerate(finishes):
        stats = []
        for p in props:
            w = json.load(open(os.path.join(root, p, "paint.json")))["workdir"]
            out = os.path.join(root, p, f"fit_{k}")
            r = subprocess.run([paint.BLENDER, "-b", "--python-exit-code", "1", "--python", os.path.join(SCRIPTS, "paint_project.py"),
                                "--", os.path.join(w, p + "_model.glb"), os.path.join(w, "guides"), os.path.join(w, "views"), out, p,
                                "", "1024", json.dumps(fin)], capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode == 0:
                t = texture_stats(os.path.join(out, p + "_diffuse.png"))
                if t:
                    stats.append(t)
        got = {key: float(np.median([t[key] for t in stats])) for key in want}
        dist = abs(got["value"] - want["value"]) / 255 + abs(got["sat"] - want["sat"]) + abs(got["flat"] - want["flat"])
        print(f"{k}: value {got['value']:.0f}, sat {got['sat']:.2f}, flat {got['flat']:.2f}, distance {dist:.3f}  {json.dumps(fin)}",
              flush=True)


def floor(cfg_path, name):
    cfg, index = _config(cfg_path)
    clip = Clip()
    ref, root = cfg["out"], os.path.join(EVAL, name)
    held = [p for p in cfg["held_out"] if os.path.isfile(os.path.join(root, p, "refs.json"))]
    orig = {p: [clip.embed(os.path.join(ref, p, f"front_{v}.png")) for v in VIEWS] for p in held}
    other = {p: [clip.embed(os.path.join(ref, json.load(open(os.path.join(root, p, "refs.json")))[0], f"front_{v}.png"))
                 for v in VIEWS] for p in held}
    keys, lib = library(cfg, clip)
    ps = probe_stats(orig, other)
    first = {p: json.load(open(os.path.join(root, p, "refs.json")))[0] for p in held}

    def without(name):   # the library minus the prop itself, or it matches its own renders
        return lib[[i for i, k in enumerate(keys) if k.split("/")[0] != name]]
    gap = float(np.mean([style_score(e, lib) for p in held for e in orig[p]])
                - np.mean([style_score(e, without(first[p])) for p in held for e in other[p]]))
    print(f"floor (your held-out props vs your most similar other prop): probe {ps['probe']:.2f} (90% {ps['ci'][0]:.2f}-"
          f"{ps['ci'][1]:.2f}, chance up to {ps['chance']:.2f}), style gap {gap:+.3f}")


def rebake(cfg_path, name, new, fin):
    import paint
    root, dst = os.path.join(EVAL, name), os.path.join(EVAL, new)
    for p in sorted(os.listdir(root)):
        if not os.path.isfile(os.path.join(root, p, "paint.json")):
            continue
        pj = json.load(open(os.path.join(root, p, "paint.json")))
        w, d = pj["workdir"], os.path.join(dst, p)
        os.makedirs(d, exist_ok=True)
        for f in ("model.json", "refs.json", "picture.png"):
            if os.path.isfile(os.path.join(root, p, f)):
                shutil.copyfile(os.path.join(root, p, f), os.path.join(d, f))
        out = os.path.join(d, "out")
        r = subprocess.run([paint.BLENDER, "-b", "--python-exit-code", "1", "--python", os.path.join(SCRIPTS, "paint_project.py"),
                            "--", os.path.join(w, p + "_model.glb"), os.path.join(w, "guides"), os.path.join(w, "views"), out, p,
                            "", "1024", json.dumps(fin)], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print(f"{p}: bake failed", flush=True)
            continue
        shutil.copyfile(os.path.join(out, p + ".glb"), os.path.join(d, "model.glb"))
        json.dump(dict(pj, diffuse=os.path.join(out, p + "_diffuse.png")), open(os.path.join(d, "paint.json"), "w"), indent=1)
        print(f"rebaked {p}", flush=True)
    json.dump({"from": name, "finish": fin}, open(os.path.join(dst, "run.json"), "w"), indent=1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["run", "score", "fit", "rebake", "floor"])
    ap.add_argument("config")
    ap.add_argument("names", nargs="+", help="run: <style> <run>; score: <run> ...; fit: <run> <finish json> ...")
    ap.add_argument("--props")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--model", default="opus")
    ap.add_argument("--from", dest="source", help="run: reuse this run's pictures and models, paint only")
    a = ap.parse_args()
    if a.mode == "run":
        run(a.config, a.names[0], a.names[1], a.props.split(",") if a.props else None, a.seed, a.model, a.source)
    elif a.mode == "fit":
        fit(a.config, a.names[0], [json.loads(f) for f in a.names[1:]])
    elif a.mode == "floor":
        floor(a.config, a.names[0])
    elif a.mode == "rebake":
        rebake(a.config, a.names[0], a.names[1], json.loads(a.names[2]))
    else:
        score(a.config, a.names)
