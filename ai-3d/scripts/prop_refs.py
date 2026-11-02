"""A style's reference props (a style_refs.py library) for the modelled-prop chain: pick the ones most like the
prop being made, and stage them for Claude as one sheet each.

    python prop_refs.py <style> pick "a green wheelie garbage bin" [n]
    python prop_refs.py <style> stage <workdir> Name [Name ...]

pick asks Claude (text only, the library's index of one-line descriptions and triangle counts) for the n props most
like the new one in kind and construction, the way an artist would look at their own similar props first; without
the Claude CLI it falls back to word overlap. Held-out and hidden props are never offered.
stage writes <workdir>/refs/<Name>.png (textured from three sides, clay, wireframe) and refs/refs.md (stats).
"""
import json
import os
import re
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

import prop_style


def library(style):
    """(config, {name: index entry}) of the style's reference library, or (None, {})."""
    st = prop_style.load(style) if isinstance(style, str) else style
    if not st or not st.get("refs"):
        return None, {}
    with open(st["refs"], encoding="utf-8") as f:
        cfg = json.load(f)
    path = os.path.join(cfg["out"], "index.json")
    if not os.path.isfile(path):
        raise RuntimeError(f"no reference index at {path}: run style_refs.py export + render, then style_index.py")
    with open(path, encoding="utf-8") as f:
        return cfg, {e["name"]: e for e in json.load(f)}


def pool(index):
    return {k: e for k, e in index.items() if not e.get("held_out") and not e.get("hidden")}


def _overlap(description, entries, n):
    words = set(re.findall(r"[a-z]+", description.lower())) - {"a", "an", "the", "with", "and", "of", "on", "in"}
    score = {k: len(words & set(re.findall(r"[a-z]+", (e["desc"] + " " + k).lower()))) for k, e in entries.items()}
    return [k for k, s in sorted(score.items(), key=lambda kv: -kv[1])[:n]]


def pick(style, description, n=3, model="sonnet", timeout=180):
    """Names of the n reference props most like the description."""
    import prop_model
    _, index = library(style)
    entries = pool(index)
    if not entries:
        return []
    cli = prop_model.claude_cli()
    if not cli:
        return _overlap(description, entries, n)
    table = "\n".join(f"- {k}: {e['desc']} ({e['tris']} tris, {e['parts']} parts)" for k, e in sorted(entries.items()))
    ask = (f"A 3D artist is about to model this game prop: {description}\n\nThese are props they already made:\n{table}\n\n"
           f"Pick the {n} props most like the new one: the same kind of object first, then the same construction "
           "(a cabinet like a cabinet, a chair like a chair, a cylinder bin like a cylinder bin). Most similar first.")
    schema = {"type": "object", "properties": {"names": {"type": "array", "items": {"type": "string"}}},
              "required": ["names"], "additionalProperties": False}
    try:
        r = subprocess.run([cli, "-p", ask, "--output-format", "json", "--model", model, "--json-schema", json.dumps(schema),
                            "--permission-mode", "dontAsk", "--strict-mcp-config", "--disable-slash-commands",
                            "--no-session-persistence"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        names = (json.loads(r.stdout).get("structured_output") or {}).get("names", [])
    except (subprocess.TimeoutExpired, ValueError):
        names = []
    names = [k for k in names if k in entries][:n]
    return names or _overlap(description, entries, n)


def pick_picture(style, description, candidates, refs, model="sonnet", timeout=300):
    """Index of the candidate picture that best shows the description the way the artist would make it: Claude looks at
    the candidates and the textured renders of the reference props (Read only). Returns (index, reason); (0, "")
    when it cannot answer."""
    import prop_model
    cli = prop_model.claude_cli()
    cfg, _ = library(style)
    if not cli or not cfg or len(candidates) < 2:
        return 0, ""
    ref_paths = [os.path.join(cfg["out"], r, "front_325.png") for r in refs]
    ask = (f"A 3D artist is about to model this game prop from a reference picture: {description}\n\n"
           "Their own props most like it (read these images first):\n" + "\n".join(f"- {p}" for p in ref_paths) +
           "\n\nCandidate reference pictures:\n" + "\n".join(f"{i}: {p}" for i, p in enumerate(candidates)) +
           "\n\nRead every image. Pick the candidate that (1) clearly shows the requested object with every part the "
           "description names (a wheelie bin has wheels and a hinged lid, a caution sign reads as one), as one object on "
           "a plain background, and (2) of those, has the design, proportions, simplicity and look closest to the "
           "artist's props. Answer with its number.")
    schema = {"type": "object", "properties": {"best": {"type": "integer"}, "why": {"type": "string"}},
              "required": ["best", "why"], "additionalProperties": False}
    dirs = sorted({os.path.dirname(os.path.abspath(p)) for p in candidates + ref_paths})
    cmd = [cli, "-p", ask, "--output-format", "json", "--model", model, "--json-schema", json.dumps(schema),
           "--tools", "Read", "--allowedTools", "Read", "--permission-mode", "dontAsk", "--strict-mcp-config",
           "--disable-slash-commands", "--no-session-persistence"]
    for d in dirs:
        cmd += ["--add-dir", d]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = json.loads(r.stdout).get("structured_output") or {}
    except (subprocess.TimeoutExpired, ValueError):
        return 0, ""
    best = out.get("best", 0)
    return (best if isinstance(best, int) and 0 <= best < len(candidates) else 0), out.get("why", "")


def _font(size):
    try:
        return ImageFont.truetype("segoeui.ttf", size)
    except OSError:
        return ImageFont.load_default()


def sheet(cfg, entry, dst):
    """One image: textured front-left, front-right, back; clay and wireframe front-left and front-right."""
    d = os.path.join(cfg["out"], entry["name"])
    tiles = [("textured, front-left", "front_325"), ("textured, front-right", "front_035"), ("textured, back", "front_180"),
             ("clay", "clay_325"), ("wireframe, front-left", "wire_325"), ("wireframe, front-right", "wire_035")]
    T = 384
    W = Image.new("RGB", (3 * T, 2 * (T + 22) + 30), (34, 34, 38))
    dr = ImageDraw.Draw(W)
    dr.text((6, 5), f"{entry['name']}: {entry['desc']}  ({entry['tris']} triangles, {entry['parts']} separate parts)",
            fill=(240, 240, 240), font=_font(16))
    for i, (label, f) in enumerate(tiles):
        x, y = (i % 3) * T, 30 + (i // 3) * (T + 22)
        dr.text((x + 4, y + 2), label, fill=(190, 190, 190), font=_font(13))
        W.paste(Image.open(os.path.join(d, f + ".png")).convert("RGB").resize((T, T)), (x, y + 22))
    W.save(dst)
    return dst


def stage(style, names, workdir):
    """Copy the sheets of these reference props into <workdir>/refs; returns [(name, sheet path)]."""
    cfg, index = library(style)
    if not cfg:
        return []
    out = os.path.join(workdir, "refs")
    os.makedirs(out, exist_ok=True)
    staged, lines = [], []
    for k in names:
        e = index.get(k)
        if not e:
            continue
        staged.append((k, sheet(cfg, e, os.path.join(out, k + ".png"))))
        dims = " x ".join(f"{v:.2f}" for v in e["dims"])
        lines.append(f"- {k}: {e['desc']}. {e['tris']} triangles, {e['parts']} separate parts, size {dims} (x, y, z in the "
                     f"artist's units). Sheet: refs/{k}.png")
    with open(os.path.join(out, "refs.md"), "w", encoding="utf-8") as f:
        f.write("# The artist's own props most like this one\n\n" + "\n".join(lines) + "\n")
    return staged


def front_render(style, name):
    """The textured front-left render of a reference prop (for painting and pictures)."""
    cfg, _ = library(style)
    return os.path.join(cfg["out"], name, "front_325.png") if cfg else None


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[1] == "pick":
        print(json.dumps(pick(a[0], a[2], int(a[3]) if len(a) > 3 else 3)))
    elif a[1] == "stage":
        print(json.dumps(stage(a[0], a[3:], a[2])))
