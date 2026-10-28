"""Persistent ACE-Step 1.5 worker for the studio web UI: keeps the models loaded between tracks.

    python -I scripts/music_worker.py

Protocol: one JSON object per line on stdin, one JSON object per line on stdout. Everything the
libraries print goes to stderr, so stdout stays a clean channel.

Requests:
    {"id": "...", "caption": "...", "lyrics": "", "instrumental": true, "duration": 60, "seed": 7|null,
     "bpm": null, "keyscale": "", "timesignature": "", "think": true, "steps": 8, "shift": 3.0,
     "lm_temperature": 0.85, "reference_audio": null, "language": "en",
     "loop": false, "crossfade": 2.0, "format": "wav", "out": "D:/.../file" (extension is added),
     "lm": "acestep-5Hz-lm-0.6B"|"" }
    {"cmd": "unload"}      free the GPU, keep the process
    {"cmd": "quit"}

Responses: {"id", "event": "loading"|"progress"|"done"|"error", ...}; {"event": "ready"} at start.
"""
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts run with python -I
from common import DEFAULT_SHIFT, DEFAULT_STEPS, DIT_MODEL, LM_MODEL, Engine, log, make_loop, trim_silence, write_audio  # noqa: E402

_real_stdout = sys.stdout
sys.stdout = sys.stderr   # keep library chatter off the protocol channel


def send(obj):
    _real_stdout.write(json.dumps(obj) + "\n")
    _real_stdout.flush()


class Worker:
    def __init__(self):
        self.engine = Engine()

    def handle(self, req):
        rid = req.get("id")
        lm = req.get("lm", LM_MODEL)
        lm = lm if lm else None
        think = bool(req.get("think", True)) and bool(lm)
        self.engine.load(req.get("dit") or DIT_MODEL, lm if think else self.engine.lm_name,   # no planner: keep one if loaded, load none
                         offload=not req.get("no_offload"),
                         progress=lambda kind, what, *a: send({"id": rid, "event": "loading", "what": what}))
        lyrics = req.get("lyrics") or ""
        instrumental = req.get("instrumental", not lyrics.strip())
        t0 = time.time()
        last = {"what": None, "t": 0.0}

        def progress(kind, what, ratio=None):
            now = time.time()
            if kind == "progress" and (what != last["what"] or now - last["t"] > 0.5):
                last["what"], last["t"] = what, now
                send({"id": rid, "event": "progress", "what": what, "ratio": ratio, "seconds": round(now - t0, 1)})

        import torch
        torch.cuda.reset_peak_memory_stats()
        r = self.engine.generate(req.get("caption") or "", lyrics=lyrics, instrumental=instrumental,
                                 duration=float(req.get("duration") or 60), seed=req.get("seed"), bpm=req.get("bpm"),
                                 keyscale=req.get("keyscale") or "", timesignature=req.get("timesignature") or "",
                                 think=think, steps=int(req.get("steps") or DEFAULT_STEPS),
                                 shift=float(req.get("shift") or DEFAULT_SHIFT), reference_audio=req.get("reference_audio"),
                                 vocal_language=req.get("language") or "en",
                                 lm_temperature=float(req.get("lm_temperature") or 0.85), progress=progress)
        audio = trim_silence(r["audio"], r["sr"])
        if req.get("loop"):
            audio = make_loop(audio, r["sr"], float(req.get("crossfade") or 2.0))
        path = write_audio(req["out"], audio, r["sr"], req.get("format") or "wav")
        dur = audio.shape[1] / r["sr"]
        send({"id": rid, "event": "done", "path": str(path), "seed": r["seed"], "duration": round(dur, 2),
              "seconds": round(time.time() - t0, 1), "meta": r["meta"],
              "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2)})

    def loop(self):
        send({"event": "ready"})
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                send({"event": "error", "error": "bad json"})
                continue
            cmd = req.get("cmd")
            if cmd == "quit":
                break
            if cmd == "unload":
                self.engine.unload()
                log("music worker: models unloaded")
                send({"event": "unloaded"})
                continue
            if cmd == "ping":
                send({"event": "pong", "loaded": self.engine.dit is not None})
                continue
            try:
                self.handle(req)
            except (SystemExit, Exception) as e:
                traceback.print_exc()
                send({"id": req.get("id"), "event": "error", "error": str(e) or e.__class__.__name__})


if __name__ == "__main__":
    Worker().loop()
