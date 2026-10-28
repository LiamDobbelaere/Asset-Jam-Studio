"""Jobs: one record per generation, persisted as studio/jobs/<id>.json, run one at a time by the Worker."""
import builtins
import json
import os
import threading
import time
import traceback
import uuid

from .bus import bus
from .paths import JOBS
from .schema import KINDS as _KINDS, TERMINAL

KINDS = tuple(_KINDS)

# notified whenever a job reaches a terminal status; `wait_for` blocks on it (GET /api/jobs/<id>/wait)
_changed = threading.Condition()


class Job:
    def __init__(self, kind, params, title="", parent=None, id=None):
        self.id = id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.kind = kind
        self.params = params
        self.title = title
        self.parent = parent
        self.status = "queued"          # queued | running | done | error | cancelled
        self.created = time.time()
        self.started = None
        self.finished = None
        self.progress = {"pct": 0, "phase": "Queued", "detail": "", "value": 0, "max": 0}
        self.outputs = {}               # final results, by name: {path, url, ...}
        self.intermediates = []         # [{label, kind: image|model, url, path, node}]
        self.log = []
        self.error = None
        self.meta = {}                  # seed, text, timings ...
        self.cancel_requested = False
        # live preview (not persisted)
        self.preview = None
        self.preview_mime = "image/jpeg"
        self.preview_seq = 0
        self._dirty = False
        self._last_pub = 0.0
        self._last_preview_pub = 0.0

    # --- persistence ------------------------------------------------------
    def to_dict(self, full=True):
        d = {"id": self.id, "kind": self.kind, "params": self.params, "title": self.title, "parent": self.parent,
             "status": self.status, "created": self.created, "started": self.started, "finished": self.finished,
             "progress": self.progress, "outputs": self.outputs,
             "intermediates": [i for i in self.intermediates if not i.get("path") or os.path.isfile(i["path"])],
             "error": self.error, "meta": self.meta, "preview_seq": self.preview_seq,
             "cancel_requested": self.cancel_requested}
        d["log"] = self.log if full else self.log[-3:]
        return d

    @classmethod
    def from_dict(cls, d):
        j = cls(d["kind"], d.get("params", {}), d.get("title", ""), d.get("parent"), d["id"])
        for k in ("status", "created", "started", "finished", "progress", "outputs", "intermediates", "log", "error", "meta"):
            if k in d:
                setattr(j, k, d[k])
        return j

    def save(self):
        tmp = os.path.join(JOBS, self.id + ".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=1)
        os.replace(tmp, os.path.join(JOBS, self.id + ".json"))

    # --- updates ----------------------------------------------------------
    def publish(self, force=False):
        now = time.time()
        if force or now - self._last_pub > 0.15:
            self._last_pub = now
            self._dirty = False
            bus.publish({"type": "job", "job": self.to_dict(full=False)})
        else:
            self._dirty = True

    def set_status(self, status, error=None):
        self.status = status
        if status == "running":
            self.started = time.time()
        if status in ("done", "error", "cancelled"):
            self.finished = time.time()
            if status == "done":
                self.progress["pct"] = 100
        if error:
            self.error = error
        self.save()
        self.publish(force=True)
        if status in TERMINAL:
            with _changed:
                _changed.notify_all()

    def wait_for(self, timeout):
        """Block until this job is done / error / cancelled or `timeout` seconds pass. Returns True when terminal."""
        deadline = time.time() + max(0.0, float(timeout))
        with _changed:
            while self.status not in TERMINAL:
                left = deadline - time.time()
                if left <= 0:
                    break
                _changed.wait(min(left, 5.0))
        return self.status in TERMINAL

    def set_progress(self, pct=None, phase=None, detail=None, value=None, max=None, force=False):
        if pct is not None:
            self.progress["pct"] = builtins.max(0, builtins.min(99, int(pct))) if self.status == "running" else int(pct)
        if phase is not None:
            self.progress["phase"] = phase
        if detail is not None:
            self.progress["detail"] = detail
        if value is not None:
            self.progress["value"] = value
        if max is not None:
            self.progress["max"] = max
        self.publish(force=force)

    def add_log(self, line):
        self.log.append(f"{time.strftime('%H:%M:%S')}  {line}")
        if len(self.log) > 300:
            self.log = self.log[-300:]
        bus.publish({"type": "log", "id": self.id, "line": self.log[-1]})

    def add_intermediate(self, label, kind, path=None, url=None, node=None):
        item = {"label": label, "kind": kind, "path": path, "url": url, "node": node, "time": time.time()}
        self.intermediates.append(item)
        self.publish(force=True)

    def set_preview(self, data, mime):
        self.preview, self.preview_mime = data, mime
        self.preview_seq += 1
        now = time.time()
        if now - self._last_preview_pub > 0.2:
            self._last_preview_pub = now
            bus.publish({"type": "preview", "id": self.id, "seq": self.preview_seq})


class JobStore:
    def __init__(self):
        self.jobs = {}
        self.lock = threading.Lock()
        for fn in sorted(os.listdir(JOBS)):
            if fn.endswith(".json"):
                try:
                    j = Job.from_dict(json.load(open(os.path.join(JOBS, fn), encoding="utf-8")))
                except Exception:
                    continue
                if j.status in ("queued", "running"):
                    j.error = f"studio was restarted while this job was {j.status}"
                    j.status = "error"
                    j.finished = j.finished or time.time()
                    j.save()
                self.jobs[j.id] = j

    def add(self, job):
        with self.lock:
            self.jobs[job.id] = job
        job.save()
        job.publish(force=True)
        return job

    def get(self, id):
        return self.jobs.get(id)

    def list(self, kind=None, limit=None, status=None):
        with self.lock:
            js = [j for j in self.jobs.values() if (kind is None or j.kind == kind) and (status is None or j.status == status)]
        js.sort(key=lambda j: j.created, reverse=True)
        return js[:limit] if limit else js

    def delete(self, id):
        with self.lock:
            j = self.jobs.pop(id, None)
        if j:
            try:
                os.remove(os.path.join(JOBS, id + ".json"))
            except OSError:
                pass
            bus.publish({"type": "job_removed", "id": id})
        return j

    def by_output_path(self):
        """abs path -> job, for every output and intermediate file of every job."""
        idx = {}
        with self.lock:
            for j in self.jobs.values():
                for o in j.outputs.values():
                    if isinstance(o, dict) and o.get("path"):
                        idx[os.path.abspath(o["path"])] = j
                    elif isinstance(o, list):
                        for it in o:
                            if isinstance(it, dict) and it.get("path"):
                                idx[os.path.abspath(it["path"])] = j
        return idx


class Worker(threading.Thread):
    """Runs queued jobs one at a time so only one generator holds the GPU."""

    def __init__(self, store, flows):
        super().__init__(daemon=True, name="studio-worker")
        self.store = store
        self.flows = flows
        self.wake = threading.Event()
        self.current = None

    def enqueue(self, job):   # the job is already in the store; just wake the loop
        self.wake.set()

    def next_job(self):
        js = [j for j in self.store.list(status="queued")]
        js.sort(key=lambda j: j.created)
        return js[0] if js else None

    def run(self):
        while True:
            job = self.next_job()
            if job is None:
                self.wake.wait(timeout=2)
                self.wake.clear()
                continue
            if job.cancel_requested:
                job.set_status("cancelled")
                continue
            self.current = job
            job.set_status("running")
            job.set_progress(0, "Starting", "")
            try:
                self.flows.run(job)
                if job.status == "running":
                    job.set_status("done")
            except Exception as e:  # noqa: BLE001
                from .comfy import Cancelled
                if isinstance(e, Cancelled) or job.cancel_requested:
                    job.add_log("cancelled")
                    job.set_status("cancelled")
                else:
                    job.add_log("error: " + str(e))
                    job.set_status("error", str(e)[:2000])
                    traceback.print_exc()
            finally:
                self.current = None
            # flush a pending throttled update
            if job._dirty:
                job.publish(force=True)


store = JobStore()
