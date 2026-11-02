"""CLIP ViT-H image embeddings (the IP-Adapter's clip_vision model, through ComfyUI's loader) for judging how much a
render or picture looks like a style's reference library, and the statistics style_eval.py reports.

    python style_clip.py rank <config.json> <picture> [<picture> ...]     prints "RANK {json}": each picture's style score

The style score of an image is its mean cosine similarity to its k nearest renders in the library (the props that are
not held out or hidden): high when it looks like something the artist made. The library's embeddings are computed
once and cached in <library>/clip_library.npz. rank runs on the CPU (CUDA hidden), so it can run next to ComfyUI on
an 8 GB card; it is what prop_chain.py calls to keep the best of several pictures.
"""
import json
import os
import subprocess
import sys

import numpy as np
from PIL import Image

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPTS)
VIEWS = ("325", "035", "090", "180")


class Clip:
    def __init__(self, cpu=False):
        comfy_dir = os.path.join(ROOT, "ComfyUI")
        if comfy_dir not in sys.path:
            sys.path.insert(0, comfy_dir)
        if cpu:
            import comfy.cli_args
            comfy.cli_args.args.cpu = True
        import comfy.clip_vision as cv
        self.m = cv.load(os.path.join(comfy_dir, "models", "clip_vision", "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"))
        self.cache = {}

    def embed(self, path):
        if path not in self.cache:
            import torch
            a = np.asarray(Image.open(path).convert("RGB")).astype(np.float32) / 255
            with torch.no_grad():
                e = self.m.encode_image(torch.from_numpy(a)[None])["image_embeds"][0].float().cpu().numpy()
            self.cache[path] = e / np.linalg.norm(e)
        return self.cache[path]


def library(cfg, clip):
    """(names, embeddings [n, 1024]) of the library's retrievable props' textured renders, cached."""
    cache = os.path.join(cfg["out"], "clip_library.npz")
    index = json.load(open(os.path.join(cfg["out"], "index.json"), encoding="utf-8"))
    names = [e["name"] for e in index if not e["held_out"] and not e["hidden"]]
    if os.path.isfile(cache):
        z = np.load(cache)
        if list(z["names"]) == [f"{n}/{v}" for n in names for v in VIEWS]:
            return list(z["names"]), z["emb"]
    keys = [f"{n}/{v}" for n in names for v in VIEWS]
    emb = np.array([clip.embed(os.path.join(cfg["out"], n, f"front_{v}.png")) for n in names for v in VIEWS])
    np.savez(cache, names=np.array(keys), emb=emb)
    return keys, emb


def style_score(e, lib, k=5):
    """Mean cosine similarity of embedding e to its k nearest library embeddings."""
    s = lib @ e
    return float(np.sort(s)[-k:].mean())


def rank(config_path, pictures):
    """Style score per picture, on the CPU in a subprocess (no GPU memory taken from ComfyUI)."""
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="")
    py = os.path.join(ROOT, "ComfyUI", ".venv", "Scripts", "python.exe")
    r = subprocess.run([py, os.path.join(SCRIPTS, "style_clip.py"), "rank", config_path] + list(pictures),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("RANK ")), None)
    if not line:
        raise RuntimeError("style ranking failed:\n" + (r.stdout + r.stderr)[-1200:])
    return json.loads(line[5:])


# --- statistics for style_eval.py -------------------------------------------------------------------------------

def _logreg(X, y, l2=1.0, steps=400, lr=0.5):
    w, b = np.zeros(X.shape[1]), 0.0
    for _ in range(steps):
        p = 1 / (1 + np.exp(-(X @ w + b)))
        g = p - y
        w -= lr * (X.T @ g / len(y) + l2 * w / len(y))
        b -= lr * g.mean()
    return w, b


def probe(orig, gen):
    """Leave-one-prop-out accuracy of telling original from generated. orig/gen: {prop: [embedding per view]}."""
    props = sorted(set(orig) & set(gen))
    base = {p: p.split("#")[0] for p in props}   # bootstrap copies ("Name#k") of a prop are held out together
    hits = n = 0
    for p in props:
        tr = [q for q in props if base[q] != base[p]]
        X = np.array([e for q in tr for e in orig[q]] + [e for q in tr for e in gen[q]])
        y = np.array([0] * sum(len(orig[q]) for q in tr) + [1] * sum(len(gen[q]) for q in tr), np.float64)
        mu, sd = X.mean(0), X.std(0) + 1e-6
        w, b = _logreg((X - mu) / sd, y)
        for e, lab in [(e, 0) for e in orig[p]] + [(e, 1) for e in gen[p]]:
            hits += int((1 / (1 + np.exp(-(((e - mu) / sd) @ w + b))) > 0.5) == lab)
            n += 1
    return hits / max(n, 1)


def probe_stats(orig, gen, n=100, seed=0):
    """The probe with what it means at this many props: "ci" (bootstrap over props, 5-95 %) and "chance" (the 95th
    percentile of the probe when each prop's original/generated labels are swapped at random, i.e. the highest score
    noise alone reaches; a probe below it cannot be told from 0.5)."""
    rng = np.random.default_rng(seed)
    props = sorted(set(orig) & set(gen))
    acc = probe(orig, gen)
    boots = []
    for _ in range(n):
        pick = rng.choice(len(props), len(props), replace=True)
        o = {f"{props[i]}#{k}": orig[props[i]] for k, i in enumerate(pick)}
        g = {f"{props[i]}#{k}": gen[props[i]] for k, i in enumerate(pick)}
        boots.append(probe(o, g))
    null = []
    for _ in range(n):
        o, g = {}, {}
        for p in props:
            a, b = (orig[p], gen[p]) if rng.random() < 0.5 else (gen[p], orig[p])
            o[p], g[p] = a, b
        null.append(probe(o, g))
    return {"probe": round(acc, 3), "ci": [round(float(np.percentile(boots, 5)), 3), round(float(np.percentile(boots, 95)), 3)],
            "chance": round(float(np.percentile(null, 95)), 3)}


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[0] == "rank":
        cfg = json.load(open(a[1], encoding="utf-8"))
        clip = Clip(cpu=True)
        _, lib = library(cfg, clip)
        print("RANK " + json.dumps([round(style_score(clip.embed(p), lib), 4) for p in a[2:]]))
