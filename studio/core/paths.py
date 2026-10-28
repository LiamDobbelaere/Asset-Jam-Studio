"""Where everything lives. The studio sits next to ai-3d/, ai-voc/, ai-mus/ and ai-vid/ in the repository."""
import os
import sys

STUDIO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(STUDIO)
AI3D = os.path.join(REPO, "ai-3d")
AIVOC = os.path.join(REPO, "ai-voc")
AIMUS = os.path.join(REPO, "ai-mus")
AIVID = os.path.join(REPO, "ai-vid")
AIANIM = os.path.join(REPO, "ai-anim")
COMFY = os.path.join(AI3D, "ComfyUI")
COMFY_PY = os.path.join(COMFY, ".venv", "Scripts", "python.exe")
VOC_PY = os.path.join(AIVOC, ".venv", "Scripts", "python.exe")
MUS_PY = os.path.join(AIMUS, ".venv", "Scripts", "python.exe")
ANIM_PY = os.path.join(AIANIM, ".venv", "Scripts", "python.exe")

ASSETS = os.path.join(AI3D, "assets")
TEXTURES = os.path.join(ASSETS, "textures")
SPRITES = os.path.join(ASSETS, "sprites")
COMFY_OUTPUT = os.path.join(COMFY, "output")
COMFY_TEMP = os.path.join(COMFY, "temp")
COMFY_INPUT = os.path.join(COMFY, "input")
VOC_OUTPUT = os.path.join(AIVOC, "output")
VOICES = os.path.join(AIVOC, "voices")
MUS_OUTPUT = os.path.join(AIMUS, "output")
VID_OUTPUT = os.path.join(AIVID, "output")
VID_SHOTS = os.path.join(AIVID, "shots")
CHARACTERS = os.path.join(AIANIM, "assets")
JOBS = os.path.join(STUDIO, "jobs")
UPLOADS = os.path.join(STUDIO, "uploads")
THUMBS = os.path.join(STUDIO, "thumbs")
INTERMEDIATES = os.path.join(STUDIO, "intermediates")
WEB = os.path.join(STUDIO, "web")

# roots the HTTP server may serve files from, as /files/<key>/<relative path>
FILE_ROOTS = {
    "assets": ASSETS,
    "comfy-output": COMFY_OUTPUT,
    "comfy-temp": COMFY_TEMP,
    "comfy-input": COMFY_INPUT,
    "voc-output": VOC_OUTPUT,
    "voices": VOICES,
    "mus-output": MUS_OUTPUT,
    "vid-output": VID_OUTPUT,
    "vid-assets": os.path.join(AIVID, "assets"),
    "characters": CHARACTERS,
    "uploads": UPLOADS,
    "thumbs": THUMBS,
    "intermediates": INTERMEDIATES,
}

for d in (JOBS, UPLOADS, THUMBS, INTERMEDIATES, TEXTURES, SPRITES, VOC_OUTPUT, MUS_OUTPUT, VID_OUTPUT, CHARACTERS):
    os.makedirs(d, exist_ok=True)

# make the ai-3d CLIs importable (generate.py, texture.py, sprite.py) and the ai-vid ones (shot.py, intro.py)
for _p in (os.path.join(AI3D, "scripts"), os.path.join(AIVID, "scripts"), os.path.join(AIANIM, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def file_url(path):
    """URL under /files/ for an absolute path inside one of the served roots, else None."""
    if not path:
        return None
    p = os.path.abspath(path)
    for key, root in FILE_ROOTS.items():
        r = os.path.abspath(root)
        if p.startswith(r + os.sep):
            rel = p[len(r) + 1:].replace("\\", "/")
            return f"/files/{key}/{rel}"
    return None


def comfy_output_path(item):
    """Absolute path of an item from a ComfyUI history/executed output ({filename, subfolder, type})."""
    base = {"output": COMFY_OUTPUT, "temp": COMFY_TEMP, "input": COMFY_INPUT}.get(item.get("type", "output"), COMFY_OUTPUT)
    return os.path.join(base, item.get("subfolder", "") or "", item["filename"])
