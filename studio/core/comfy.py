"""ComfyUI client: HTTP helpers, a stdlib WebSocket listener for live progress, and process control."""
import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .paths import COMFY, COMFY_PY, STUDIO

COMFY_LOG = os.path.join(STUDIO, "comfy.log")   # ComfyUI's output when the studio starts it (a file, so ComfyUI survives a studio crash)


class ComfyError(Exception):
    pass


class Cancelled(Exception):
    pass


class WSClient:
    """Minimal RFC 6455 client: enough to read ComfyUI's text and binary frames."""

    def __init__(self, url, timeout=5.0):
        u = urllib.parse.urlparse(url)
        self.host, self.port = u.hostname, u.port or 80
        self.path = u.path + ("?" + u.query if u.query else "")
        self.sock = None
        self.timeout = timeout
        self._buf = b""

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET {self.path} HTTP/1.1\r\nHost: {self.host}:{self.port}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ComfyError("websocket handshake failed")
            resp += chunk
        head, _, rest = resp.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise ComfyError("websocket handshake refused: " + head.decode(errors="replace")[:200])
        expect = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        if expect.encode() not in head:
            raise ComfyError("websocket accept key mismatch")
        self._buf = rest
        self.sock.settimeout(self.timeout)

    def _read(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                raise ComfyError("websocket closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send_text(self, text):
        data = text.encode()
        mask = os.urandom(4)
        header = bytes([0x81])
        n = len(data)
        if n < 126:
            header += bytes([0x80 | n])
        elif n < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(header + mask + masked)

    def _pong(self, payload):
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes([0x8A, 0x80 | len(payload)]) + mask + masked)

    def recv(self):
        """Returns ("text", str) | ("binary", bytes) | None on timeout. Raises ComfyError when closed."""
        message, opcode = b"", None
        while True:
            try:
                b0, b1 = self._read(2)
            except socket.timeout:
                return None
            fin, op = b0 & 0x80, b0 & 0x0F
            masked, n = b1 & 0x80, b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if masked else None
            payload = self._read(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if op == 8:
                raise ComfyError("websocket closed by server")
            if op == 9:
                self._pong(payload)
                continue
            if op == 10:
                continue
            if op in (1, 2):
                opcode = op
                message = payload
            elif op == 0:
                message += payload
            if fin:
                if opcode == 1:
                    return "text", message.decode(errors="replace")
                if opcode == 2:
                    return "binary", message
                message, opcode = b"", None

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass


class Comfy:
    def __init__(self, url="http://127.0.0.1:8188"):
        self.url = url.rstrip("/")
        self.proc = None
        self._logfile = None
        self.started_by_us = False

    # --- HTTP -------------------------------------------------------------
    def get(self, path, timeout=30):
        with urllib.request.urlopen(self.url + path, timeout=timeout) as r:
            return json.load(r)

    def post(self, path, body=None, timeout=30):
        data = json.dumps(body or {}).encode()
        req = urllib.request.Request(self.url + path, data, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw.strip() else {}

    def alive(self):
        try:
            self.get("/system_stats", timeout=2)
            return True
        except Exception:
            return False

    def queue_info(self):
        try:
            q = self.get("/queue", timeout=3)
            return {"running": len(q.get("queue_running", [])), "pending": len(q.get("queue_pending", []))}
        except Exception:
            return {"running": 0, "pending": 0}

    def upload_image(self, name, data):
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
                f"Content-Type: image/png\r\n\r\n").encode() + data + \
               f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(self.url + "/upload/image", body,
                                     {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)["name"]

    def free(self):
        """Ask ComfyUI to unload its models and release VRAM (processed immediately when idle)."""
        try:
            self.post("/free", {"unload_models": True, "free_memory": True}, timeout=5)
            return True
        except Exception:
            return False

    def interrupt(self):
        try:
            self.post("/interrupt", {}, timeout=5)
        except Exception:
            pass

    def delete_queued(self, prompt_id):
        try:
            self.post("/queue", {"delete": [prompt_id]}, timeout=5)
        except Exception:
            pass

    def view_url(self, item):
        q = urllib.parse.urlencode({"filename": item["filename"], "subfolder": item.get("subfolder", "") or "",
                                    "type": item.get("type", "output")})
        return self.url + "/view?" + q

    # --- process ----------------------------------------------------------
    def log_lines(self, n=400):
        """The last lines of ComfyUI's output (only when it was started from the studio, now or earlier)."""
        try:
            with open(COMFY_LOG, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 200_000))
                lines = f.read().decode("utf-8", errors="replace").splitlines()
            return [ln.rstrip() for ln in lines[-n:]]
        except OSError:
            return []

    def start(self):
        if self.alive():
            return "already running"
        if self.proc and self.proc.poll() is None:
            return "starting"
        if not os.path.exists(COMFY_PY):
            raise ComfyError(f"ComfyUI venv not found at {COMFY_PY}; run ai-3d/setup.ps1 first")
        env = dict(os.environ, PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True", PYTHONIOENCODING="utf-8")
        # output goes to a file, not a pipe: a pipe dies with the studio process and every later print in
        # ComfyUI (progress bars, node logs) then fails with "[Errno 22] Invalid argument" inside the nodes
        if self._logfile:
            self._logfile.close()
        self._logfile = open(COMFY_LOG, "wb")
        self.proc = subprocess.Popen(
            [COMFY_PY, "main.py", "--listen", "127.0.0.1", "--port", "8188", "--preview-method", "auto"],
            cwd=COMFY, env=env, stdout=self._logfile, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.started_by_us = True
        return "starting"

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            return True
        # An instance started outside the studio (start_comfyui.bat, or a studio that has since exited): find it by
        # the port it listens on and stop it; from then on the studio owns the restart, so the GPU hand-over to the
        # voice / music / anim workers works the same as for an instance it started itself.
        pid = self._listening_pid()
        if pid:
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=20)
            except (OSError, subprocess.TimeoutExpired):
                return False
            t0 = time.time()
            while self.alive() and time.time() - t0 < 10:
                time.sleep(0.5)
            self.proc = None
            self.started_by_us = True
            return not self.alive()
        return False

    def _listening_pid(self):
        """PID of the process listening on ComfyUI's port (Windows netstat), or None."""
        port = urllib.parse.urlparse(self.url).port or 8188
        try:
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3] == "LISTENING":
                try:
                    return int(parts[4])
                except ValueError:
                    pass
        return None

    def process_state(self):
        if self.proc is None:
            return "external" if self.alive() else "stopped"
        if self.proc.poll() is None:
            return "running" if self.alive() else "starting"
        return "exited"

    # --- running a prompt --------------------------------------------------
    def run(self, api, on_event, should_cancel=lambda: False, timeout=7200):
        """Queue an API prompt and stream progress to on_event(kind, **data) until it finishes.

        kinds: queued, start, cached(nodes), node(node), progress(node,value,max), state(nodes),
               executed(node, output), preview(data, mime). Returns the history entry.
        """
        client_id = str(uuid.uuid4())
        ws = WSClient(self.url.replace("http", "ws", 1) + "/ws?clientId=" + client_id)
        ws.connect()
        try:
            res = self.post("/prompt", {"prompt": api, "client_id": client_id}, timeout=120)
            if "prompt_id" not in res:
                raise ComfyError("ComfyUI rejected the prompt: " + json.dumps(res.get("node_errors") or res)[:1500])
            pid = res["prompt_id"]
            on_event("queued", prompt_id=pid, number=res.get("number"))
            t0 = time.time()
            running = False
            last_poll = time.time()
            unresponsive_since = None   # long VAE decodes (video) block ComfyUI's HTTP server for a while
            while True:
                if should_cancel():
                    if running:
                        self.interrupt()
                    else:
                        self.delete_queued(pid)
                        raise Cancelled()
                if time.time() - t0 > timeout:
                    self.interrupt()
                    raise ComfyError("timed out")
                frame = ws.recv()
                if frame is None:
                    # fallback: the history settles even if a websocket message was missed
                    if time.time() - last_poll > 5:
                        last_poll = time.time()
                        try:
                            hist = self.get(f"/history/{pid}", timeout=5)
                        except Exception:
                            hist = {}
                        if pid in hist:
                            unresponsive_since = None
                            return self._finish(hist[pid])
                        if not self.alive():
                            unresponsive_since = unresponsive_since or time.time()
                            if time.time() - unresponsive_since > 180:
                                raise ComfyError("ComfyUI stopped responding")
                        else:
                            unresponsive_since = None
                    continue
                kind, payload = frame
                if kind == "binary":
                    if len(payload) < 8:
                        continue
                    event = struct.unpack(">I", payload[:4])[0]
                    if event == 1:   # PREVIEW_IMAGE: image type, then bytes
                        itype = struct.unpack(">I", payload[4:8])[0]
                        on_event("preview", data=payload[8:], mime="image/png" if itype == 2 else "image/jpeg")
                    elif event == 4:  # PREVIEW_IMAGE_WITH_METADATA: json length, json, bytes
                        n = struct.unpack(">I", payload[4:8])[0]
                        try:
                            meta = json.loads(payload[8:8 + n])
                        except Exception:
                            meta = {}
                        if meta.get("prompt_id") in (None, pid):
                            on_event("preview", data=payload[8 + n:], mime=meta.get("image_type", "image/jpeg"),
                                     node=meta.get("display_node_id") or meta.get("node_id"))
                    continue
                try:
                    msg = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                t, d = msg.get("type"), msg.get("data") or {}
                if d.get("prompt_id") not in (None, pid) and t != "status":
                    continue
                if t == "execution_start":
                    running = True
                    on_event("start")
                elif t == "execution_cached":
                    on_event("cached", nodes=d.get("nodes", []))
                elif t == "executing":
                    on_event("node", node=d.get("display_node") or d.get("node"))
                elif t == "progress":
                    on_event("progress", node=d.get("node"), value=d.get("value", 0), max=d.get("max", 1))
                elif t == "progress_state":
                    on_event("state", nodes=d.get("nodes", {}))
                elif t == "executed":
                    on_event("executed", node=d.get("display_node") or d.get("node"), output=d.get("output") or {})
                elif t == "execution_success":
                    break
                elif t == "execution_interrupted":
                    raise Cancelled()
                elif t == "execution_error":
                    raise ComfyError(f"{d.get('node_type')}: {d.get('exception_message')}")
            for _ in range(20):
                hist = self.get(f"/history/{pid}", timeout=10)
                if pid in hist:
                    return self._finish(hist[pid])
                time.sleep(0.5)
            raise ComfyError("finished but no history entry")
        finally:
            ws.close()

    @staticmethod
    def _finish(h):
        st = h.get("status", {})
        if st.get("status_str") == "error":
            for m in st.get("messages", []):
                if m[0] == "execution_error":
                    raise ComfyError(f"{m[1].get('node_type')}: {m[1].get('exception_message')}")
            raise ComfyError("execution failed")
        if any(m[0] == "execution_interrupted" for m in st.get("messages", [])):
            raise Cancelled()
        return h


comfy = Comfy(os.environ.get("COMFY_SERVER", "http://127.0.0.1:8188"))
