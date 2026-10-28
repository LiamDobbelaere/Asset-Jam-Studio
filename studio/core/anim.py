"""The ai-anim side: the persistent rig + motion worker (Make-It-Animatable, Kimodo) and what the UI needs to know."""
import os

from .paths import AIANIM, ANIM_PY
from .procworker import LineWorker

WORKER_SCRIPT = os.path.join(AIANIM, "scripts", "anim_worker.py")
MIA_WEIGHTS = os.path.join(AIANIM, "mia", "output", "best", "new", "pose.pth")
BLENDER = os.environ.get("BLENDER", r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")
HF_TOKEN = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "token")

PROMPT_TIPS = {
    "documented": [
        {"mark": "“A person …”", "effect": "Kimodo was trained on captions that start like this: “A person walks forward slowly and waves.”"},
        {"mark": "be explicit", "effect": "“waves hello with their right hand” gave a timid chest-height wave; “raises their right arm high above their head and waves enthusiastically” a real one. Say which limb and how far"},
        {"mark": "Seed", "effect": "takes differ a lot between seeds: generate the same clip with 2–3 seeds and keep the best"},
        {"mark": "one action per sentence", "effect": "several sentences separated by “ | ” are generated in sequence and blended into one clip"},
        {"mark": "Seconds", "effect": "length of the clip (per sentence); 2–8 s works best, long clips drift"},
        {"mark": "Idle / Walk / Run", "effect": "name the clip after the Unity state it will drive; the names become the glTF animations and FBX takes"},
        {"mark": "In place", "effect": "removes the horizontal root motion so the character animates on the spot (for your own movement code)"},
    ],
    "untested": [
        {"mark": "styles", "effect": "“… in a sneaky way”, “like a zombie”: the SOMA-RP model saw some stylised data"},
        {"mark": "looping", "effect": "Kimodo does not loop clips; trim the FBX clip in Unity or pick a clip whose last frame resembles the first"},
    ],
    "note": "Rigging (Make-It-Animatable) takes a few seconds and expects a humanoid: two arms, two legs, a head, ideally in an "
            "A- or T-pose. Motion (Kimodo-SOMA-RP) takes ~10–30 s per clip once its Llama-3 text encoder (16 GB, on the CPU) "
            "is loaded; the first load takes a minute or two.",
}


class AnimWorker(LineWorker):
    def __init__(self):
        super().__init__("anim", ANIM_PY, WORKER_SCRIPT, AIANIM, "Make-It-Animatable + Kimodo")

    def request(self, req, on_event, should_cancel=lambda: False, timeout=3600):
        """on_event(kind, **data) with kind in log | loading | progress | tick; returns the done message."""
        def on_message(msg):
            ev = msg.get("event")
            if ev == "log":
                on_event("log", message=msg.get("message", ""))
            elif ev == "loading":
                on_event("loading", what=msg.get("what") or "")
            elif ev == "progress":
                self.loaded = True
                on_event("progress", value=msg.get("value"), max=msg.get("max"))
            elif ev == "done":
                self.loaded = True
                return msg
            return None
        return super().request(req, on_message, should_cancel, timeout, on_tick=lambda s: on_event("tick", seconds=s))


def readiness():
    return {"venv": os.path.isfile(ANIM_PY), "mia_weights": os.path.isfile(MIA_WEIGHTS), "blender": os.path.isfile(BLENDER),
            "hf_token": os.path.isfile(HF_TOKEN),
            "ready": os.path.isfile(ANIM_PY) and os.path.isfile(MIA_WEIGHTS) and os.path.isfile(BLENDER)}


anim = AnimWorker()
