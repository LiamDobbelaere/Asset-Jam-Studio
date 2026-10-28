"""The ai-mus side: the persistent ACE-Step 1.5 worker process and what the UI shows about writing prompts."""
import os

from .paths import AIMUS, MUS_PY
from .procworker import LineWorker

WORKER_SCRIPT = os.path.join(AIMUS, "scripts", "music_worker.py")
MUS_MODELS = os.path.join(AIMUS, "models")

PROMPT_TIPS = {
    "documented": [
        {"mark": "genre, instruments, mood", "effect": "the three things the caption must name: “lo-fi hip hop, warm Rhodes piano and vinyl crackle, relaxed”"},
        {"mark": "tempo words", "effect": "“slow”, “driving”, “frantic”; or pin a BPM in the field below"},
        {"mark": "no vocals", "effect": "Instrumental is the default; say “no vocals, no choir” in the caption too if voices creep in"},
        {"mark": "Loop", "effect": "blends the last seconds into the start and cuts them off, so the file loops without a click; use with a steady groove"},
        {"mark": "Plan with the LM", "effect": "the 0.6B planner writes bpm/key/structure and semantic codes before the DiT; off = faster, flatter"},
        {"mark": "Takes", "effect": "same caption, consecutive seeds; pick the best. A track takes ~0.5 s of compute per second of music"},
        {"mark": "[Verse] [Chorus]", "effect": "lyrics under Vocals are sung nearly word for word (Whisper round-trip checked); name the voice in the caption (“playful female vocal”)"},
        {"mark": "BPM", "effect": "pin it for sung tracks: the planner sometimes answers 30 bpm for an upbeat song"},
    ],
    "untested": [
        {"mark": "[Intro] [Build] [Drop]", "effect": "structure tags without lyrics to shape an instrumental: “[Instrumental]” plus section tags in the lyrics box"},
        {"mark": "Reference audio", "effect": "style/timbre transfer from an existing track (your own or a placeholder); strength is fixed in this version"},
        {"mark": "key / time signature", "effect": "“A minor”, 3/4 for waltzes, 6/8 for shanties"},
    ],
    "note": "ACE-Step 1.5 is a song model: it was trained on full tracks with structure, so game loops come out best as 30–90 s "
            "pieces with a clear mood. 48 kHz stereo; the file is written as 16-bit wav (or flac / ogg).",
}


class MusicWorker(LineWorker):
    def __init__(self):
        super().__init__("music", MUS_PY, WORKER_SCRIPT, AIMUS, "ACE-Step 1.5")

    def request(self, req, on_event, should_cancel=lambda: False, timeout=2400):
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


def available_models():
    """Planner LMs and DiTs present in ai-mus/models."""
    lms, dits = [], []
    if os.path.isdir(MUS_MODELS):
        for fn in sorted(os.listdir(MUS_MODELS)):
            if os.path.isdir(os.path.join(MUS_MODELS, fn)):
                if fn.startswith("acestep-5Hz-lm-"):
                    lms.append(fn)
                elif fn.startswith("acestep-v15-"):
                    dits.append(fn)
    return {"lms": lms, "dits": dits, "ready": bool(dits) and os.path.isfile(MUS_PY)}


music = MusicWorker()
