"""Persistent MOSS-SoundEffect v2.0 worker for the studio web UI: keeps the model loaded between sounds.

    python -I scripts/sfx_worker.py

Protocol: one JSON object per line on stdin, one JSON object per line on stdout. Everything the
libraries print goes to stderr, so stdout stays a clean channel.

Requests:
    {"id": "...", "prompt": "...", "duration": 4, "seed": 7|null, "steps": 50, "cfg": 4.0, "shift": 5.0,
     "negative": "", "loop": false, "crossfade": 1.0, "trim": true, "peak_db": -1.0|null, "highpass": 30,
     "format": "wav", "out": "D:/.../file" (extension is added)}
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
from common import DEFAULT_CFG, DEFAULT_SHIFT, DEFAULT_STEPS, Engine, finish, log, write_audio  # noqa: E402

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
        self.engine.load(offload=not req.get("no_offload"),
                         progress=lambda kind, what, *a: send({"id": rid, "event": "loading", "what": what}))
        t0 = time.time()
        last = {"what": None, "t": 0.0}

        def progress(kind, what, ratio=None):
            now = time.time()
            if kind == "progress" and (what != last["what"] and now - last["t"] > 0.3 or ratio == 1.0):
                last["what"], last["t"] = what, now
                send({"id": rid, "event": "progress", "what": what, "ratio": ratio, "seconds": round(now - t0, 1)})

        import torch
        torch.cuda.reset_peak_memory_stats()
        r = self.engine.generate(req.get("prompt") or "", duration=float(req.get("duration") or 4.0), seed=req.get("seed"),
                                 steps=int(req.get("steps") or DEFAULT_STEPS), cfg=float(req.get("cfg") or DEFAULT_CFG),
                                 shift=float(req.get("shift") or DEFAULT_SHIFT), negative=req.get("negative") or "",
                                 exact=bool(req.get("loop")), progress=progress)
        peak = req.get("peak_db", -1.0)
        audio = finish(r["audio"], r["sr"], loop=bool(req.get("loop")), crossfade=float(req.get("crossfade") or 1.0),
                       trim=req.get("trim", True) is not False, peak_db=None if peak is None else float(peak),
                       hp_hz=float(req.get("highpass", 30.0) or 0))
        path = write_audio(req["out"], audio, r["sr"], req.get("format") or "wav")
        dur = audio.shape[1] / r["sr"]
        send({"id": rid, "event": "done", "path": str(path), "seed": r["seed"], "duration": round(dur, 2),
              "seconds": round(time.time() - t0, 1), "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2)})

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
                log("sfx worker: model unloaded")
                send({"event": "unloaded"})
                continue
            if cmd == "ping":
                send({"event": "pong", "loaded": self.engine.pipe is not None})
                continue
            try:
                self.handle(req)
            except (SystemExit, Exception) as e:
                traceback.print_exc()
                send({"id": req.get("id"), "event": "error", "error": str(e) or e.__class__.__name__})


if __name__ == "__main__":
    Worker().loop()
