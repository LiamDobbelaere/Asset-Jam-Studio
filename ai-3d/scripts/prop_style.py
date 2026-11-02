"""Style guides for modelled props: ai-3d/styles/<id>.json, plus the modelling rules Claude gets on top of
prop_brief.md (<id>.md, named by "brief"). A style sets how the reference picture is drawn ("picture": the style words
after the prompt, in place of the gameasset preset's), how Claude models ("brief") and how paint.py paints ("paint":
prompt words for every view, negative words, ipadapter / denoise / depth defaults, and the "finish" paint_project.py
applies to the bake: flatten, shade, ink). No style ("" or "default") is the pipeline as it is without a file.

Private styles, built from your own props, live in ai-3d/styles/private/<id>/style.json (gitignored), with their brief
next to it. They may name a reference library ("refs": the style_refs.py config, relative to the style's folder): the
modelled-prop chain then picks the most similar of your props (prop_refs.py), shows them to Claude while it models,
and can draw the picture and paint the views from them ("picture_from": "refs", "paint": {"ref_views": ...}).

    python prop_style.py            lists the styles
"""
import json
import os

STYLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "styles")
PRIVATE = os.path.join(STYLES, "private")


def _paths():
    """{id: style json path} for the public styles and the private ones."""
    out = {}
    if os.path.isdir(STYLES):
        for fn in sorted(os.listdir(STYLES)):
            if fn.endswith(".json"):
                out[fn[:-5]] = os.path.join(STYLES, fn)
    if os.path.isdir(PRIVATE):
        for d in sorted(os.listdir(PRIVATE)):
            p = os.path.join(PRIVATE, d, "style.json")
            if os.path.isfile(p):
                out[d] = p
    return out


def list_styles():
    """[{id, label, summary, private}] for every style, sorted by id."""
    out = []
    for sid, path in _paths().items():
        s = load(sid)
        out.append({"id": s["id"], "label": s.get("label", s["id"]), "summary": s.get("summary", ""),
                    "private": path.startswith(PRIVATE)})
    return out


def load(style):
    """The style as a dict with "id", "dir", "picture", "brief" (the rules' text, "" if none), "paint" (with "finish")
    and "refs" (absolute path of the reference library config, or None); None for no style. Raises ValueError for an
    unknown id."""
    if not style or style == "default":
        return None
    path = _paths().get(os.path.basename(style))
    if not path:
        known = ", ".join(_paths()) or "none"
        raise ValueError(f"unknown style {style!r} (styles: {known})")
    with open(path, encoding="utf-8") as f:
        s = json.load(f)
    s["id"] = os.path.basename(style)
    s["dir"] = os.path.dirname(path)
    brief = s.get("brief")
    if brief:
        with open(os.path.join(s["dir"], brief), encoding="utf-8") as f:
            s["brief"] = f.read()
    else:
        s["brief"] = ""
    s["refs"] = os.path.join(s["dir"], s["refs"]) if s.get("refs") else None
    s.setdefault("picture", "")
    s.setdefault("paint", {})
    s["paint"].setdefault("finish", {})
    return s


def picture_prompt(prompt, style, raw=False):
    """(text, raw) for generate.build_api(..., "gameasset", raw, ...): the style's picture words replace the preset's."""
    if style and style.get("picture") and not raw:
        return prompt + style["picture"], True
    return prompt, raw


if __name__ == "__main__":
    print(json.dumps(list_styles(), indent=1))
