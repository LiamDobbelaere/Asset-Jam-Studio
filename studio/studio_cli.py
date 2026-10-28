"""Command-line client for the Asset Jam Studio API (standard library only; any Python 3.8+).

    python studio_cli.py health                              is the studio up, which back-ends are ready, what is running
    python studio_cli.py schema [kind]                       every job kind with its parameters (or one kind in detail)
    python studio_cli.py submit <kind> --<param> value ...   queue a job; --wait blocks until it is done and prints the outputs
    python studio_cli.py wait <job id> [--timeout 1800]      block until a job is done / error / cancelled
    python studio_cli.py status <job id>                     one job (full record, log included)
    python studio_cli.py jobs [--status queued|running|done|error|cancelled] [--kind music] [--limit 20]
    python studio_cli.py cancel <job id>
    python studio_cli.py voices                              prepared voices and their moods
    python studio_cli.py library models|textures|sprites|voice|music|sfx|videos|characters
    python studio_cli.py upload <file>                       copy a file into the studio (for *_upload parameters)
    python studio_cli.py comfy start|stop|free
    python studio_cli.py worker tts|music|sfx|anim stop|unload

Flags for `submit` come from the schema: scalar parameters are plain flags (--prompt "x" --width 512 --loop true),
list/object parameters take JSON (--shots '[{"id": ...}]', --clips '[...]'). `--params '{"prompt": ...}'` or
`--params-file spec.json` give the whole parameter object at once; explicit flags override it.
Output is JSON on stdout; `submit --wait` and `wait` print progress lines on stderr. Exit code 0 = done,
1 = the job failed or was cancelled, 2 = the request was rejected or the studio is unreachable.

The studio's address is STUDIO_URL (default http://127.0.0.1:8765).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("STUDIO_URL", "http://127.0.0.1:8765").rstrip("/")
TERMINAL = ("done", "error", "cancelled")


class Unreachable(Exception):
    pass


def call(method, path, body=None, raw=None, timeout=60):
    url = BASE + path
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    headers = {"Content-Type": "application/octet-stream" if raw is not None else "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers if data is not None else {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e)
        except Exception:
            msg = {"error": e.reason}
        raise SystemExit(out({"error": msg.get("error") or str(e), "status": e.code}, code=2))
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise Unreachable(f"studio not reachable at {BASE} ({e}). Start it with studio\\studio.bat")


def out(obj, code=0):
    print(json.dumps(obj, indent=1, ensure_ascii=False))
    return code


def progress_line(j):
    p = j.get("progress") or {}
    detail = (p.get("detail") or "").strip()
    return f"[{j['id']}] {j['status']:9s} {p.get('pct', 0):3.0f}%  {p.get('phase') or ''}{(' - ' + detail) if detail else ''}"


def wait_for(job_id, timeout):
    """Long-poll until the job is terminal; progress on stderr. Returns the final job record."""
    t0 = time.time()
    last = None
    while True:
        left = timeout - (time.time() - t0)
        if left <= 0:
            j = call("GET", f"/api/jobs/{job_id}")
            print(f"[{job_id}] still {j['status']} after {timeout:.0f} s; keep waiting with: studio_cli.py wait {job_id}", file=sys.stderr)
            return j
        # short polls so a progress line shows every ~20 s while a long job runs
        j = call("GET", f"/api/jobs/{job_id}/wait?timeout={min(left, 20):.0f}", timeout=min(left, 20) + 30)
        line = progress_line(j)
        if line != last:
            print(line, file=sys.stderr)
            last = line
        if j["status"] in TERMINAL:
            return j


def summary(j):
    """What a caller needs after a job: status, outputs (paths + urls), the error, the seed that was used."""
    s = {"id": j["id"], "kind": j["kind"], "status": j["status"], "title": j.get("title"), "outputs": j.get("outputs") or {}}
    if j.get("error"):
        s["error"] = j["error"]
        s["log_tail"] = (j.get("log") or [])[-5:]
    if (j.get("params") or {}).get("seed") is not None:
        s["seed"] = j["params"]["seed"]
    if j.get("started") and j.get("finished"):
        s["seconds"] = round(j["finished"] - j["started"], 1)
    return s


def add_schema_flags(ap, kind, schema):
    fields = schema["kinds"][kind]["params"]
    for name, spec in fields.items():
        if spec.get("ui"):
            continue
        help_ = spec.get("help") or ""
        if spec.get("choices"):
            help_ += f" [{'|'.join(str(c) for c in spec['choices'])}]"
        if spec.get("default") is not None:
            help_ += f" (default {spec['default']})"
        if spec["type"] in ("list", "object"):
            help_ += " (JSON)"
        ap.add_argument("--" + name, dest="p_" + name, help=help_, metavar=spec["type"].upper())


def coerce_flag(spec, v):
    t = spec["type"]
    if t == "bool":
        return v.lower() in ("1", "true", "yes", "on")
    if t == "int":
        return int(v)
    if t == "float":
        return float(v)
    if t in ("list", "object"):
        return json.loads(v)
    if t == "path":
        return os.path.abspath(v)
    return v


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("health")
    s = sub.add_parser("schema"); s.add_argument("kind", nargs="?")
    s = sub.add_parser("submit"); s.add_argument("kind"); s.add_argument("--wait", action="store_true", help="block until the job finishes")
    s.add_argument("--timeout", type=float, default=3600, help="seconds to wait with --wait (default 3600)")
    s.add_argument("--params", help="JSON object with the parameters"); s.add_argument("--params-file", help="file with that JSON")
    s.add_argument("--parent", help="id of the job this one continues (e.g. the image stage of a 3D model)")
    s = sub.add_parser("wait"); s.add_argument("id"); s.add_argument("--timeout", type=float, default=3600)
    s = sub.add_parser("status"); s.add_argument("id")
    s = sub.add_parser("jobs"); s.add_argument("--status"); s.add_argument("--kind"); s.add_argument("--limit", type=int, default=20)
    s = sub.add_parser("cancel"); s.add_argument("id")
    sub.add_parser("voices")
    s = sub.add_parser("library"); s.add_argument("name", choices=["models", "textures", "sprites", "voice", "music", "sfx", "videos", "characters"])
    s = sub.add_parser("upload"); s.add_argument("file")
    s = sub.add_parser("comfy"); s.add_argument("action", choices=["start", "stop", "free"])
    s = sub.add_parser("worker", help="end a worker process (stop) or just drop its models (unload)")
    s.add_argument("name", choices=["tts", "music", "sfx", "anim"]); s.add_argument("action", choices=["stop", "unload"])

    # `submit <kind>` gets its flags from the live schema, so parse in two steps
    if argv[:1] == ["submit"] and len(argv) > 1 and not argv[1].startswith("-"):
        try:
            schema = call("GET", "/api/schema")
        except Unreachable as e:
            return out({"error": str(e)}, 2)
        kind = argv[1]
        if kind not in schema["kinds"]:
            return out({"error": f"kind must be one of {list(schema['kinds'])}"}, 2)
        add_schema_flags(sub.choices["submit"], kind, schema)
    else:
        schema = None
    a = ap.parse_args(argv)

    try:
        if a.cmd == "health":
            return out(call("GET", "/api/health"))
        if a.cmd == "schema":
            sc = call("GET", "/api/schema")
            if a.kind:
                if a.kind not in sc["kinds"]:
                    return out({"error": f"kind must be one of {list(sc['kinds'])}"}, 2)
                return out(sc["kinds"][a.kind])
            brief = {k: {"label": v["label"], "backend": v["backend"], "summary": v["summary"], "time": v["time"],
                         "required": [n for n, p in v["params"].items() if p.get("required")],
                         "params": [n for n, p in v["params"].items() if not p.get("ui")]} for k, v in sc["kinds"].items()}
            return out({"kinds": brief, "statuses": sc["statuses"]})
        if a.cmd == "submit":
            params = {}
            if a.params_file:
                with open(a.params_file, encoding="utf-8") as f:
                    params.update(json.load(f))
            if a.params:
                params.update(json.loads(a.params))
            fields = schema["kinds"][a.kind]["params"]
            for name, spec in fields.items():
                v = getattr(a, "p_" + name, None)
                if v is not None:
                    params[name] = coerce_flag(spec, v)
            job = call("POST", "/api/jobs", {"kind": a.kind, "params": params, "parent": a.parent})
            print(f"[{job['id']}] queued: {job['title']}", file=sys.stderr)
            if not a.wait:
                return out({"id": job["id"], "status": job["status"], "title": job["title"],
                            "hint": f"studio_cli.py wait {job['id']}"})
            j = wait_for(job["id"], a.timeout)
            return out(summary(j), 0 if j["status"] == "done" else 1)
        if a.cmd == "wait":
            j = wait_for(a.id, a.timeout)
            return out(summary(j), 0 if j["status"] == "done" else 1)
        if a.cmd == "status":
            return out(call("GET", f"/api/jobs/{a.id}"))
        if a.cmd == "jobs":
            q = {k: v for k, v in (("status", a.status), ("kind", a.kind), ("limit", a.limit)) if v}
            js = call("GET", "/api/jobs?" + urllib.parse.urlencode(q))
            return out([{"id": j["id"], "kind": j["kind"], "status": j["status"], "title": j["title"],
                         "pct": (j.get("progress") or {}).get("pct"), "phase": (j.get("progress") or {}).get("phase"),
                         "error": j.get("error")} for j in js])
        if a.cmd == "cancel":
            return out(call("POST", f"/api/jobs/{a.id}/cancel"))
        if a.cmd == "voices":
            v = call("GET", "/api/voices")
            return out([{"name": x["name"], "moods": [m["name"] for m in x["moods"]], "ref_text": x["ref_text"],
                         "finetuned": [f["path"] for f in x.get("finetuned", [])]} for x in v["voices"]])
        if a.cmd == "library":
            return out(call("GET", f"/api/library/{a.name}"))
        if a.cmd == "upload":
            with open(a.file, "rb") as f:
                data = f.read()
            return out(call("POST", "/api/upload?name=" + urllib.parse.quote(os.path.basename(a.file)), raw=data, timeout=300))
        if a.cmd == "comfy":
            return out(call("POST", f"/api/comfy/{a.action}"))
        if a.cmd == "worker":
            return out(call("POST", f"/api/{a.name}/{a.action}"))
    except Unreachable as e:
        return out({"error": str(e)}, 2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
