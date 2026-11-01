"""Style guides for modelled props: ai-3d/styles/<id>.json, plus the modelling rules Claude gets on top of
prop_brief.md (<id>.md, named by "brief"). A style sets how the reference picture is drawn ("picture": the style words
after the prompt, in place of the gameasset preset's), how Claude models ("brief") and how paint.py paints ("paint":
prompt words for every view, negative words, ipadapter / denoise / depth defaults, and the "finish" paint_project.py
applies to the bake: flatten, shade, ink). No style ("" or "default") is the pipeline as it is without a file.

    python prop_style.py            lists the styles
"""
import json
import os

STYLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "styles")


def list_styles():
    """[{id, label, summary}] for every styles/<id>.json, sorted by id."""
    out = []
    if os.path.isdir(STYLES):
        for fn in sorted(os.listdir(STYLES)):
            if fn.endswith(".json"):
                s = load(fn[:-5])
                out.append({"id": s["id"], "label": s.get("label", s["id"]), "summary": s.get("summary", "")})
    return out


def load(style):
    """The style as a dict with "id", "picture", "brief" (the rules' text, "" if none) and "paint" (with "finish");
    None for no style. Raises ValueError for an unknown id."""
    if not style or style == "default":
        return None
    path = os.path.join(STYLES, os.path.basename(style) + ".json")
    if not os.path.isfile(path):
        known = ", ".join(s["id"] for s in list_styles()) or "none"
        raise ValueError(f"unknown style {style!r} (styles: {known})")
    with open(path, encoding="utf-8") as f:
        s = json.load(f)
    s["id"] = os.path.basename(style)
    brief = s.get("brief")
    if brief:
        with open(os.path.join(STYLES, brief), encoding="utf-8") as f:
            s["brief"] = f.read()
    else:
        s["brief"] = ""
    s.setdefault("picture", "")
    s.setdefault("paint", {})
    s["paint"].setdefault("finish", {})
    return s


if __name__ == "__main__":
    print(json.dumps(list_styles(), indent=1))
