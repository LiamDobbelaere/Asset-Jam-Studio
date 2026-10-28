"""The ai-sfx side: the persistent MOSS-SoundEffect v2.0 worker process and what the UI shows about writing prompts."""
import os

from .paths import AISFX, SFX_PY
from .procworker import LineWorker

WORKER_SCRIPT = os.path.join(AISFX, "scripts", "sfx_worker.py")
SFX_MODEL = os.path.join(AISFX, "models", "MOSS-SoundEffect-v2.0")

PROMPT_TIPS = {
    "documented": [
        {"mark": "source + action", "effect": "name what makes the sound and what it does: “a heavy wooden door creaks open slowly”, not just “door”"},
        {"mark": "space and distance", "effect": "“in a small tiled room”, “far away across a valley”, “close up” shape the reverb and level"},
        {"mark": "single / repeated", "effect": "“a single pistol gunshot” for one hit; “rapid machine-gun fire, several bursts” for a sequence"},
        {"mark": "Duration", "effect": "the prompt is told the length; one-shots 0.5–3 s, impacts with a tail 3–6 s, ambiences up to 30 s"},
        {"mark": "Takes", "effect": "same prompt, consecutive seeds; sound effects vary a lot per seed, so make 3–4 and keep the best"},
        {"mark": "Loop", "effect": "for ambiences (rain, wind, engine hum): the end is blended into the start so the file repeats without a click"},
    ],
    "untested": [
        {"mark": "Negative prompt", "effect": "“music, speech, voices” when a melody or talking creeps into an effect"},
        {"mark": "Guidance", "effect": "higher (5–7) follows the words more literally, lower (2–3) sounds more natural"},
        {"mark": "Chinese prompts", "effect": "the model was trained on English and Chinese captions"},
    ],
    "note": "MOSS-SoundEffect v2.0 is a 1.3B text-to-audio DiT (Apache-2.0). Output is 48 kHz mono, trimmed, faded and "
            "peak-normalized to -1 dBFS: mono suits positional audio in a game engine. Every sound costs the same "
            "compute (the model always denoises a 30 s canvas and the length is cropped from it).",
}


class SfxWorker(LineWorker):
    def __init__(self):
        super().__init__("sfx", SFX_PY, WORKER_SCRIPT, AISFX, "MOSS-SoundEffect v2.0")

    def request(self, req, on_event, should_cancel=lambda: False, timeout=1200):
        """on_event(kind, **data) with kind in log | loading | progress | tick; returns the done message."""
        def on_message(msg):
            ev = msg.get("event")
            if ev == "log":
                on_event("log", message=msg.get("message", ""))
            elif ev == "loading":
                on_event("loading", what=msg.get("what") or "")
            elif ev == "progress":
                self.loaded = True
                on_event("progress", what=msg.get("what") or "", ratio=msg.get("ratio"), seconds=msg.get("seconds"))
            elif ev == "done":
                self.loaded = True
                return msg
            return None
        return super().request(req, on_message, should_cancel, timeout, on_tick=lambda s: on_event("tick", seconds=s))


def readiness():
    return {"ready": os.path.isfile(SFX_PY) and os.path.isfile(os.path.join(SFX_MODEL, "model_index.json")),
            "venv": os.path.isfile(SFX_PY), "model": os.path.isdir(SFX_MODEL)}


sfx = SfxWorker()
