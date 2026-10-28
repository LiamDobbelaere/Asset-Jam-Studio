"""A persistent generator subprocess speaking one JSON object per line (ai-voc/tts_worker.py, ai-mus/music_worker.py,
ai-sfx/sfx_worker.py).

The process keeps its model loaded between requests; `unload` frees the GPU but keeps the process,
`stop` ends it. One request at a time (the studio worker thread is single-threaded).
"""
import json
import os
import queue
import subprocess
import threading
import time


class LineWorker:
    def __init__(self, name, python, script, cwd, label):
        self.name = name            # short id for logs ("voice", "music")
        self.python = python
        self.script = script
        self.cwd = cwd
        self.label = label          # what the user sees ("Qwen3-TTS", "ACE-Step 1.5")
        self.proc = None
        self.lock = threading.Lock()
        self.loaded = False
        self.last_used = 0
        self.stderr_tail = []
        self.lines = queue.Queue()

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def ensure(self, log=lambda s: None):
        if self.alive():
            return
        if not os.path.exists(self.python):
            raise RuntimeError(f"{self.name} venv not found at {self.python}; run its setup.ps1 first")
        log(f"starting the {self.name} worker ({self.label})")
        self.proc = subprocess.Popen([self.python, "-I", self.script], cwd=self.cwd, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                     errors="replace", bufsize=1,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.stderr_tail = []
        self.lines = queue.Queue()
        threading.Thread(target=self._pump_err, args=(self.proc,), daemon=True, name=f"{self.name}-stderr").start()
        threading.Thread(target=self._pump_out, args=(self.proc, self.lines), daemon=True, name=f"{self.name}-stdout").start()
        msg = self._read(timeout=120)
        if not msg or msg.get("event") != "ready":
            raise RuntimeError(f"{self.name} worker did not start: " + "\n".join(self.stderr_tail[-20:]))

    def _pump_err(self, proc):
        for line in proc.stderr:
            self.stderr_tail.append(line.rstrip())
            if len(self.stderr_tail) > 200:
                self.stderr_tail = self.stderr_tail[-200:]

    @staticmethod
    def _pump_out(proc, lines):
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)   # EOF

    def _read(self, timeout):
        """Next JSON message from the worker, or None on timeout / exit."""
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty:
            return None
        if line is None:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return {"event": "garbage", "line": line}

    def request(self, req, on_message, should_cancel=lambda: False, timeout=1800, on_tick=lambda s: None):
        """Send one request; on_message(msg) is called per reply and returns the final message to stop.

        Raises on cancel, worker death, timeout, or an "error" reply."""
        with self.lock:
            self.ensure(log=lambda s: on_message({"event": "log", "message": s}))
            self.proc.stdin.write(json.dumps(req) + "\n")
            self.proc.stdin.flush()
            t0 = time.time()
            while True:
                if should_cancel():
                    self.stop()
                    raise RuntimeError("cancelled")
                msg = self._read(timeout=2)
                if msg is None:
                    if not self.alive():
                        raise RuntimeError(f"{self.name} worker died:\n" + "\n".join(self.stderr_tail[-15:]))
                    if time.time() - t0 > timeout:
                        self.stop()
                        raise RuntimeError(f"{self.name} generation timed out")
                    on_tick(time.time() - t0)
                    continue
                if msg.get("event") == "error":
                    raise RuntimeError(msg.get("error") or f"{self.name} worker error")
                done = on_message(msg)
                if done is not None:
                    self.last_used = time.time()
                    return done

    def unload(self):
        """Free the GPU but keep the process (the next request reloads the model)."""
        if not self.alive() or not self.loaded:
            return
        with self.lock:
            try:
                self.proc.stdin.write(json.dumps({"cmd": "unload"}) + "\n")
                self.proc.stdin.flush()
                self._read(timeout=60)
            except Exception:
                self.stop()
            self.loaded = False

    def stop(self):
        if self.proc is None:
            return
        p = self.proc
        self.proc = None
        self.loaded = False
        try:
            if p.poll() is None:
                try:
                    p.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
                    p.stdin.flush()
                except Exception:
                    pass
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
        except Exception:
            pass

    def state(self):
        return {"process": "running" if self.alive() else "stopped", "model_loaded": self.loaded and self.alive()}
