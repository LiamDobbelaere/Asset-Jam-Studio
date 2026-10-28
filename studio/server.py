"""Asset Jam Studio: a local web front-end and HTTP API for ai-3d (3D props, world textures, 2D sprites),
ai-voc (voices), ai-mus (music), ai-vid (videos from a shot list) and ai-anim (rigged, animated characters).

    studio.bat            then open http://127.0.0.1:8765

Standard library only; runs with the ai-3d ComfyUI venv (Pillow is needed for sprite inputs).
The API is documented in API.md next to this file; studio_cli.py wraps it for scripts and for Claude.
"""
import argparse
import json
import mimetypes
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import gpu, info, library, schema  # noqa: E402
from core.anim import PROMPT_TIPS as ANIM_TIPS, anim, readiness as anim_readiness  # noqa: E402
from core.bus import bus  # noqa: E402
from core.comfy import comfy  # noqa: E402
from core.flows import Flows  # noqa: E402
from core.jobs import Job, Worker, store  # noqa: E402
from core.music import PROMPT_TIPS as MUSIC_TIPS, available_models, music  # noqa: E402
from core.paths import FILE_ROOTS, UPLOADS, WEB, file_url  # noqa: E402
from core.voice import TEXT_TIPS, list_voices, save_clip_texts, save_ref_text, tts, voice_clips  # noqa: E402

VERSION = "1.1"
MAX_WAIT = 600          # longest a single GET /api/jobs/<id>/wait may block, in seconds
WORKERS = {"tts": tts, "music": music, "anim": anim}

mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("audio/wav", ".wav")
mimetypes.add_type("audio/flac", ".flac")
mimetypes.add_type("audio/ogg", ".ogg")
mimetypes.add_type("video/mp4", ".mp4")

worker = Worker(store, Flows())


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def system_state():
    cur = worker.current
    return {
        "comfy": {"alive": comfy.alive(), "process": comfy.process_state(), "queue": comfy.queue_info(), "url": comfy.url},
        "tts": tts.state(),
        "music": music.state(),
        "anim": anim.state(),
        "gpu": gpu.state(),
        "running": cur.id if cur else None,
        "queued": [j.id for j in sorted(store.list(status="queued"), key=lambda j: j.created)],
        "time": time.time(),
    }


def _system_broadcast():
    last = None
    while True:
        s = system_state()
        key = json.dumps({k: s[k] for k in ("comfy", "tts", "music", "anim", "gpu", "running", "queued")}, sort_keys=True)
        if key != last:
            last = key
            bus.publish({"type": "system", "system": s})
        time.sleep(2)


def _job_or_404(job_id):
    j = store.get(job_id)
    if not j:
        raise ApiError("no such job", 404)
    return j


def _safe_path(root, rel):
    """Absolute path of `rel` inside `root`, or None when it escapes the root."""
    full = os.path.abspath(os.path.join(root, rel))
    return full if full.startswith(os.path.abspath(root) + os.sep) else None


# ------------------------------------------------------------------------------------------------
# API handlers: (handler, query, body) -> JSON-serialisable object; raise ApiError for a client error

def api_state(h, q, body):
    return {"system": system_state(), "jobs": [j.to_dict(full=False) for j in store.list(limit=200)]}


def api_health(h, q, body):
    alive = comfy.alive()
    cur = worker.current
    return {"ok": True, "version": VERSION, "time": time.time(), "backends": info.readiness(alive),
            "running": cur.to_dict(full=False) if cur else None, "queued": len(store.list(status="queued")),
            "gpu": gpu.state()}


def api_schema(h, q, body):
    return schema.public()


def api_jobs_list(h, q, body):
    kind = q.get("kind", [None])[0]
    status = q.get("status", [None])[0]
    limit = int(q.get("limit", [200])[0])
    return [j.to_dict(full=False) for j in store.list(kind=kind, status=status, limit=limit)]


def api_jobs_create(h, q, body):
    kind = body.get("kind")
    try:
        params = schema.validate(kind, body.get("params") or {})
    except schema.ParamError as e:
        raise ApiError(str(e))
    job = Job(kind, params, schema.job_title(kind, params), parent=body.get("parent"))
    store.add(job)
    worker.enqueue(job)
    return job.to_dict()


def api_job(h, q, body, job_id):
    return _job_or_404(job_id).to_dict()


def api_job_wait(h, q, body, job_id):
    """Long poll: returns when the job is done / error / cancelled, or after `timeout` seconds (default 60, max 600)."""
    j = _job_or_404(job_id)
    timeout = min(float(q.get("timeout", [60])[0]), MAX_WAIT)
    j.wait_for(timeout)
    d = j.to_dict()
    d["finished_waiting"] = j.status in schema.TERMINAL
    return d


def api_job_cancel(h, q, body, job_id):
    j = _job_or_404(job_id)
    j.cancel_requested = True
    if j.status == "queued":
        j.set_status("cancelled")
    else:
        j.publish(force=True)
        if schema.KINDS[j.kind]["backend"] == "comfy":
            comfy.interrupt()
    return j.to_dict(full=False)


def api_job_delete(h, q, body, job_id):
    j = store.get(job_id)
    if j and j.status == "running":
        raise ApiError("job is running")
    store.delete(job_id)
    return {"ok": True}


def api_job_preview(h, q, body, job_id):
    j = _job_or_404(job_id)
    if not j.preview:
        raise ApiError("no preview", 404)
    h.send_bytes(j.preview, j.preview_mime)
    return None


LIBRARIES = {"models": library.models, "textures": library.textures, "sprites": library.sprites, "voice": library.voice_takes,
             "music": library.music_tracks, "videos": library.videos, "characters": library.characters}


def api_library(h, q, body, name):
    fn = LIBRARIES.get(name)
    if not fn:
        raise ApiError(f"library must be one of {list(LIBRARIES)}", 404)
    return fn(store)


def api_presets3d(h, q, body):
    return info.presets_3d()


def api_music(h, q, body):
    return {"models": available_models(), "tips": MUSIC_TIPS}


def api_video(h, q, body):
    return info.video_info()


def api_anim(h, q, body):
    return {"ready": anim_readiness(), "tips": ANIM_TIPS, "state": anim.state()}


def api_voices(h, q, body):
    return {"voices": list_voices(), "tips": TEXT_TIPS}


def api_voice_clips(h, q, body, name):
    try:
        return voice_clips(name)
    except (FileNotFoundError, ValueError) as e:
        raise ApiError(str(e), 404)


def api_voice_clips_save(h, q, body, name):
    try:
        n = save_clip_texts(name, body.get("texts") or {})
        for stem, text in (body.get("refs") or {}).items():
            save_ref_text(name, stem, text)
    except (FileNotFoundError, ValueError) as e:
        raise ApiError(str(e))
    return {"ok": True, "changed": n}


def api_comfy_log(h, q, body):
    return {"lines": comfy.log_lines(), "state": comfy.process_state()}


def api_worker_log(h, q, body, name):
    w = WORKERS[name]
    return {"lines": w.stderr_tail[-100:], "state": w.state()}


def api_worker_stop(h, q, body, name):
    if worker.current and schema.KINDS[worker.current.kind]["backend"] == {"tts": "voice"}.get(name, name):
        raise ApiError(f"a {name} job is running; cancel it first", 409)
    WORKERS[name].stop()
    return {"ok": True}


def api_worker_unload(h, q, body, name):
    if worker.current and schema.KINDS[worker.current.kind]["backend"] == {"tts": "voice"}.get(name, name):
        raise ApiError(f"a {name} job is running; cancel it first", 409)
    WORKERS[name].unload()
    return {"ok": True}


def api_comfy_start(h, q, body):
    return {"result": comfy.start()}


def api_comfy_stop(h, q, body):
    if worker.current and schema.KINDS[worker.current.kind]["backend"] == "comfy":
        raise ApiError("a ComfyUI job is running; cancel it first", 409)
    return {"result": comfy.stop()}


def api_comfy_free(h, q, body):
    if worker.current and schema.KINDS[worker.current.kind]["backend"] == "comfy":
        raise ApiError("a ComfyUI job is running", 409)
    return {"result": comfy.free()}


def api_comfy_interrupt(h, q, body):
    comfy.interrupt()
    return {"ok": True}


def api_thumb(h, q, body):
    name = q.get("name", [""])[0]
    data = body
    if not name or not data.startswith(b"\x89PNG"):
        raise ApiError("name and a PNG body are required")
    with open(library.thumb_path(name), "wb") as f:
        f.write(data)
    return {"url": library.thumb_url(name)}


def api_upload(h, q, body):
    """Raw body -> studio/uploads/<timestamp>-<name>; with ?folder=x several files keep their names in one folder."""
    name = q.get("name", ["upload.png"])[0]
    safe = re.sub(r"[^A-Za-z0-9_.\-]+", "_", os.path.basename(name))[-80:] or "upload.png"
    if not body:
        raise ApiError("empty upload")
    folder = re.sub(r"[^A-Za-z0-9_\-]+", "_", q.get("folder", [""])[0])
    if folder:
        os.makedirs(os.path.join(UPLOADS, folder), exist_ok=True)
        full = os.path.join(UPLOADS, folder, safe)
    else:
        full = os.path.join(UPLOADS, time.strftime("%Y%m%d-%H%M%S-") + safe)
    with open(full, "wb") as f:
        f.write(body)
    out = {"path": full, "url": file_url(full), "name": safe}
    if folder:
        out["folder"] = os.path.join(UPLOADS, folder)
    return out


def api_reveal(h, q, body):
    p = body.get("path", "")
    if not any(os.path.abspath(p).startswith(os.path.abspath(r) + os.sep) for r in FILE_ROOTS.values()):
        raise ApiError("not allowed", 403)
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(p)])
    return {"ok": True}


# method -> [(compiled path regex, handler, reads_json_body)]
ROUTES = {
    "GET": [
        (r"/api/state", api_state), (r"/api/health", api_health), (r"/api/schema", api_schema),
        (r"/api/jobs", api_jobs_list), (r"/api/jobs/(?P<job_id>[\w\-]+)", api_job),
        (r"/api/jobs/(?P<job_id>[\w\-]+)/wait", api_job_wait), (r"/api/jobs/(?P<job_id>[\w\-]+)/preview", api_job_preview),
        (r"/api/library/(?P<name>\w+)", api_library), (r"/api/presets3d", api_presets3d),
        (r"/api/music", api_music), (r"/api/video", api_video), (r"/api/anim", api_anim), (r"/api/voices", api_voices),
        (r"/api/voices/(?P<name>[\w\-]+)/clips", api_voice_clips),
        (r"/api/comfy/log", api_comfy_log), (r"/api/(?P<name>tts|music|anim)/log", api_worker_log),
    ],
    "POST": [
        (r"/api/jobs", api_jobs_create), (r"/api/jobs/(?P<job_id>[\w\-]+)/cancel", api_job_cancel),
        (r"/api/jobs/(?P<job_id>[\w\-]+)/delete", api_job_delete),
        (r"/api/voices/(?P<name>[\w\-]+)/clips", api_voice_clips_save),
        (r"/api/comfy/start", api_comfy_start), (r"/api/comfy/stop", api_comfy_stop), (r"/api/comfy/free", api_comfy_free),
        (r"/api/comfy/interrupt", api_comfy_interrupt),
        (r"/api/(?P<name>tts|music|anim)/stop", api_worker_stop), (r"/api/(?P<name>tts|music|anim)/unload", api_worker_unload),
        (r"/api/thumb", api_thumb), (r"/api/upload", api_upload), (r"/api/reveal", api_reveal),
    ],
}
ROUTES = {m: [(re.compile(p + r"\Z"), fn) for p, fn in rs] for m, rs in ROUTES.items()}
RAW_BODY = {api_thumb, api_upload}     # handlers that take the request body as bytes, not JSON


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = f"AssetJamStudio/{VERSION}"

    def log_message(self, fmt, *args):   # quiet unless STUDIO_VERBOSE is set
        if os.environ.get("STUDIO_VERBOSE"):
            super().log_message(fmt, *args)

    def log_error(self, fmt, *args):     # idle keep-alive connections time out all the time; not worth a line
        pass

    # --- helpers ------------------------------------------------------------
    def send_json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_bytes(self, data, ctype, status=200, cache=False):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
        self.end_headers()
        self.wfile.write(data)

    def read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def send_file(self, path, cache=False):
        if not os.path.isfile(path):
            return self.send_json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        size = os.path.getsize(path)
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        status = 200
        if rng and rng.startswith("bytes="):
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), size - 1)
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "max-age=31536000" if cache else "no-cache")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1 << 16, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    # --- routing --------------------------------------------------------------
    def dispatch(self, method):
        u = urllib.parse.urlparse(self.path)
        path, q = u.path, urllib.parse.parse_qs(u.query)
        try:
            if method == "GET" and path == "/api/events":
                return self.sse()
            for rx, fn in ROUTES[method]:
                m = rx.match(path)
                if not m:
                    continue
                if method == "POST":
                    raw = self.read_body()
                    body = raw if fn in RAW_BODY else (json.loads(raw) if raw else {})
                else:
                    body = None
                out = fn(self, q, body, **m.groupdict())
                if out is not None:
                    self.send_json(out)
                return
            if method == "GET":
                return self.serve_file(path)
            self.send_json({"error": "not found"}, 404)
        except ApiError as e:
            self.send_json({"error": str(e)}, e.status)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            try:
                self.send_json({"error": str(e)}, 500)
            except Exception:
                pass

    def serve_file(self, path):
        """/files/<root>/<relative path> from the served roots, else a static file from web/."""
        if path.startswith("/files/"):
            parts = path[len("/files/"):].split("/", 1)
            root = FILE_ROOTS.get(parts[0])
            if not root or len(parts) < 2:
                return self.send_json({"error": "not found"}, 404)
            full = _safe_path(root, urllib.parse.unquote(parts[1]))
            if not full:
                return self.send_json({"error": "forbidden"}, 403)
            return self.send_file(full)
        if path == "/":
            path = "/index.html"
        full = _safe_path(WEB, path.lstrip("/"))
        if full and os.path.isfile(full):
            return self.send_file(full)
        return self.send_json({"error": "not found"}, 404)

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    # --- server-sent events ------------------------------------------------------
    def sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        q = bus.subscribe()
        try:
            self.wfile.write(("data: " + json.dumps({"type": "hello", "system": system_state()}) + "\n\n").encode())
            self.wfile.flush()
            while True:
                try:
                    data = q.get(timeout=15)
                    self.wfile.write(("data: " + data + "\n\n").encode())
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            pass
        finally:
            bus.unsubscribe(q)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        et = sys.exc_info()[0]
        if et in (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, TimeoutError):
            return   # a browser dropped a keep-alive connection; routine
        super().handle_error(request, client_address)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--start-comfy", action="store_true", help="launch ComfyUI if it is not running")
    a = ap.parse_args()

    gpu.start()
    worker.start()
    threading.Thread(target=_system_broadcast, daemon=True, name="system-broadcast").start()
    if a.start_comfy and not comfy.alive():
        print("starting ComfyUI ...")
        comfy.start()

    srv = Server((a.host, a.port), Handler)
    url = f"http://{a.host}:{a.port}"
    print(f"Asset Jam Studio at {url}   (ComfyUI: {'running' if comfy.alive() else 'not running'})")
    if not a.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        tts.stop()
        music.stop()
        anim.stop()
        if comfy.started_by_us:
            comfy.stop()


if __name__ == "__main__":
    main()
