"""GPU memory / utilisation via nvidia-smi, polled in the background."""
import subprocess
import threading
import time

_state = {"available": False, "name": "", "used_mb": 0, "total_mb": 0, "util": 0, "updated": 0}
_lock = threading.Lock()


def read_once():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        line = out.stdout.strip().splitlines()[0]
        name, used, total, util = [x.strip() for x in line.split(",")]
        return {"available": True, "name": name, "used_mb": int(float(used)), "total_mb": int(float(total)),
                "util": int(float(util)), "updated": time.time()}
    except Exception:
        return {"available": False, "name": "", "used_mb": 0, "total_mb": 0, "util": 0, "updated": time.time()}


def _loop(interval):
    while True:
        s = read_once()
        with _lock:
            _state.update(s)
        time.sleep(interval)


def start(interval=3.0):
    threading.Thread(target=_loop, args=(interval,), daemon=True, name="gpu-poll").start()


def state():
    with _lock:
        return dict(_state)
