"""Persistent ai-anim worker for the studio: rigs characters (Make-It-Animatable) and generates motion (Kimodo).

Protocol: one JSON object per line on stdin, one JSON object per line on stdout (same as ai-voc/tts_worker.py).
    {"cmd": "rig", "id", "mesh", "out_npz", "no_fingers"?, "normals"?}
        -> {"event": "done", "npz"}
    {"cmd": "motion", "id", "prompts": [..], "seconds": float | [..], "out_stem", "seed"?, "steps"?}
        -> {"event": "progress", "value", "max"} ... {"event": "done", "npz", "bvh"}
    {"cmd": "unload"}  frees the GPU (and the CPU text encoder) but keeps the process
    {"cmd": "quit"}
Progress / log events: {"id", "event": "log", "message"} and {"id", "event": "loading", "what"}.
Blender steps (rig build, retargeting, export) are run by the caller with blender_rig.py / blender_animate.py.
"""
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
_real_stdout = sys.stdout
sys.stdout = sys.stderr      # library prints must not corrupt the protocol


def send(obj):
    _real_stdout.write(json.dumps(obj) + "\n")
    _real_stdout.flush()


class Worker:
    def __init__(self):
        self.rig_loaded = False
        self.motion_loaded = False

    def log(self, rid):
        return lambda msg: send({"id": rid, "event": "log", "message": str(msg)})

    def rig(self, req):
        import rig_infer
        rid = req.get("id")
        if not self.rig_loaded:
            send({"id": rid, "event": "loading", "what": "Make-It-Animatable"})
        out = rig_infer.rig(req["mesh"], req["out_npz"], bool(req.get("no_fingers")), bool(req.get("normals")),
                            log=self.log(rid))
        self.rig_loaded = True
        send({"id": rid, "event": "done", "npz": out})

    def motion(self, req):
        import motion
        rid = req.get("id")
        if not self.motion_loaded:
            send({"id": rid, "event": "loading", "what": "Kimodo + Llama-3-8B text encoder"})

        def progress(v, m):
            send({"id": rid, "event": "progress", "value": v, "max": m})

        npz, bvh = motion.generate(req["prompts"], req.get("seconds", 4.0), req["out_stem"], req.get("seed"),
                                   int(req.get("steps") or 100), log=self.log(rid), progress=progress)
        self.motion_loaded = True
        send({"id": rid, "event": "done", "npz": npz, "bvh": bvh})

    def unload(self):
        # free whatever was imported: a request that failed after loading must not leave the models resident
        if "rig_infer" in sys.modules:
            sys.modules["rig_infer"].unload_models()
        if "motion" in sys.modules:
            sys.modules["motion"].unload()
        self.rig_loaded = self.motion_loaded = False

    def handle(self, req):
        cmd = req.get("cmd")
        if cmd == "rig":
            self.rig(req)
        elif cmd == "motion":
            self.motion(req)
        elif cmd == "unload":
            self.unload()
            send({"event": "unloaded"})
        elif cmd == "quit":
            raise SystemExit(0)
        else:
            send({"id": req.get("id"), "event": "error", "error": f"unknown cmd {cmd!r}"})


def main():
    w = Worker()
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
        try:
            w.handle(req)
        except SystemExit:
            break
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            send({"id": req.get("id"), "event": "error", "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
