"""The improvement loop: Claude proposes one change to a track's editable files, a fixed scorer measures it, git keeps
or reverts it, a short second Claude session writes the notes, the handoff for the next iteration and any memory.

    python improve/loop.py <track> [--iterations 3] [--budget-usd 30] [--hours 6] [--model opus] [--reflect-model sonnet]
                           [--props A,B] [--calibrate N] [--dry-run]

<track> is a folder improve/tracks/<track>/ with track.json (goal, metric, editable and read-only files, the score
command), NOTES.md, backlog.md and next.md (the agent's), results.tsv (one row per iteration) and, if the track
needs private settings, private.json (gitignored, merged over track.json). State lives in improve/runs/<track>/
(state.json, one folder per iteration with the score, the diff and Claude's logs); the branch improve/<track>
holds the kept changes and the notes.

--calibrate N scores the unchanged files N times and records the spread as the track's noise: a change is only
kept when it beats the best score by max(min_delta, spread). Do this once before a loop. --dry-run prints the
prompt of the next iteration and the score command without calling Claude or scoring. --props limits the scorer
to a few props (cheap smoke tests; results are not comparable with full runs, so state is kept apart under
runs/<track>-<props>).

Stops at --iterations, --hours, --budget-usd (Claude usage of this run, modelling included), when improve/STOP
exists, or on Ctrl-C after the current step. Needs: git, the Claude Code CLI logged in (claude on PATH or
~/.local/bin/claude.exe; CLAUDE_CLI overrides), ai-3d's venv (the scorer runs in it). Standard library only.
"""
import argparse
import datetime
import fnmatch
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = os.path.join(ROOT, "ai-3d", "ComfyUI", ".venv", "Scripts", "python.exe")
STUDIO = os.environ.get("STUDIO_URL", "http://127.0.0.1:8765")
sys.path.insert(0, os.path.join(ROOT, "ai-3d", "scripts"))

PROPOSE_SCHEMA = {"type": "object", "additionalProperties": False,
                  "properties": {"title": {"type": "string"}, "hypothesis": {"type": "string"},
                                 "change": {"type": "string"}, "expect": {"type": "string"},
                                 "files": {"type": "array", "items": {"type": "string"}}},
                  "required": ["title", "hypothesis", "change", "expect", "files"]}
REFLECT_SCHEMA = {"type": "object", "additionalProperties": False,
                  "properties": {"summary": {"type": "string"}, "memory_files": {"type": "array", "items": {"type": "string"}},
                                 "next": {"type": "string"}},
                  "required": ["summary", "memory_files", "next"]}


def log(msg):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)


def git(*args, check=True):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{r.stderr.strip()}")
    return r.stdout


def rel(p):
    return os.path.relpath(p, ROOT).replace("\\", "/")


def expand(patterns):
    """Repo-relative files matching the patterns (globs with ** allowed), sorted."""
    out = set()
    for pat in patterns:
        for p in glob.glob(os.path.join(ROOT, pat), recursive=True):
            if os.path.isfile(p) and "__pycache__" not in p:
                out.add(rel(p))
    return sorted(out)


def matches(path, patterns):
    return any(fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(path, pat.replace("**", "*")) for pat in patterns)


def digest(files):
    h = hashlib.sha256()
    for f in files:
        h.update(f.encode())
        with open(os.path.join(ROOT, f), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()


def read(path, default=""):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return default


class Track:
    def __init__(self, name, props=None):
        self.name = name
        self.dir = os.path.join(HERE, "tracks", name)
        if not os.path.isfile(os.path.join(self.dir, "track.json")):
            raise SystemExit(f"no track {name}: expected {rel(self.dir)}/track.json")
        self.cfg = json.load(open(os.path.join(self.dir, "track.json"), encoding="utf-8"))
        priv = self.cfg.get("private")
        if priv:
            pp = os.path.join(self.dir, priv)
            if not os.path.isfile(pp):
                raise SystemExit(f"{rel(pp)} is missing; it holds: {self.cfg.get('private_fields', '?')}")
            self.cfg.update(json.load(open(pp, encoding="utf-8")))
        self.props = props
        self.runs = os.path.join(HERE, "runs", name + (f"-{props.replace(',', '+')}" if props else ""))
        os.makedirs(self.runs, exist_ok=True)
        self.state_path = os.path.join(self.runs, "state.json")
        self.state = json.load(open(self.state_path, encoding="utf-8")) if os.path.isfile(self.state_path) else \
            {"iteration": 0, "best": None, "best_iter": None, "baseline": None, "noise": 0.0, "spent_usd": 0.0}
        m = self.cfg["metric"]
        self.key, self.sign = m["key"], (1 if m.get("direction", "max") == "max" else -1)
        self.branch = "improve/" + name

    def save(self):
        json.dump(self.state, open(self.state_path, "w", encoding="utf-8"), indent=1)

    def min_delta(self):
        return max(float(self.cfg["metric"].get("min_delta", 0)), float(self.state.get("noise") or 0))

    def file(self, n):
        return os.path.join(self.dir, n)

    def score_cmd(self, out, workdir):
        s = self.cfg["score"]
        fill = {"py": PY, "out": out, "workdir": workdir, "track_runs": self.runs, "model": s.get("model", "opus"),
                **{k: v for k, v in self.cfg.items() if isinstance(v, str)}}
        cmd = [c.format(**fill) for c in s["cmd"]]
        if self.props:
            cmd += ["--props", self.props]
        return cmd, int(s.get("timeout", 3600))

    def results_row(self, n, status, score, delta, cost, commit, title):
        p = self.file("results.tsv")
        if not os.path.isfile(p):
            with open(p, "w", encoding="utf-8") as f:
                f.write(f"iter\tdate\tstatus\t{self.key}\tdelta\tcost_usd\tcommit\ttitle\n")
        v = "" if score is None else f"{score:.4f}"
        d = "" if delta is None else f"{delta:+.4f}"
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{n}\t{datetime.date.today()}\t{status}\t{v}\t{d}\t{cost:.2f}\t{commit}\t{title}\n")


def claude_call(prompt, system, allowed, schema, model, budget, log_path, cwd=ROOT, timeout=1800, effort=None):
    """One headless Claude Code session; returns (structured_output or None, cost_usd, result event)."""
    import prop_model
    cli = prop_model.claude_cli()
    if not cli:
        raise RuntimeError("Claude Code CLI not found (claude on PATH or ~/.local/bin/claude.exe; CLAUDE_CLI overrides)")
    tools = sorted({a.split("(")[0] for a in allowed})
    cmd = [cli, "-p", prompt, "--output-format", "stream-json", "--verbose", "--append-system-prompt", system,
           "--tools", ",".join(tools), "--permission-mode", "dontAsk", "--allowedTools", *allowed,
           "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
           "--json-schema", json.dumps(schema), "--max-budget-usd", f"{budget:.2f}", "--model", model]
    if effort:
        cmd += ["--effort", effort]
    result, t0 = None, time.time()
    with open(log_path, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                text=True, encoding="utf-8", errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for ln in proc.stdout:
            lf.write(ln)
            try:
                ev = json.loads(ln)
            except ValueError:
                continue
            if ev.get("type") == "assistant":
                for c in ev.get("message", {}).get("content", []):
                    if c.get("type") == "text" and c.get("text", "").strip():
                        log("  claude: " + c["text"].strip().replace("\n", " ")[:160])
                    elif c.get("type") == "tool_use" and c["name"] in ("Write", "Edit"):
                        log(f"  claude {c['name'].lower()}s {(c.get('input') or {}).get('file_path', '')}")
            elif ev.get("type") == "result":
                result = ev
            if time.time() - t0 > timeout:
                proc.kill()
                raise RuntimeError(f"Claude session exceeded {timeout} s")
        proc.wait()
    if result is None or result.get("is_error") or result.get("subtype") != "success":
        raise RuntimeError("Claude session failed: " + str((result or {}).get("result") or (result or {}).get("subtype"))[-600:])
    out = result.get("structured_output")
    if not isinstance(out, dict):
        try:
            out = json.loads(result.get("result") or "")
        except ValueError:
            out = None
    return out, float(result.get("total_cost_usd") or 0), result


def changed_paths():
    """Repo-relative paths with uncommitted changes (tracked and untracked, ignored ones excluded)."""
    out = []
    for ln in git("status", "--porcelain", "--untracked-files=all").splitlines():
        p = ln[3:].strip().strip('"')
        if " -> " in p:
            p = p.split(" -> ")[1]
        out.append(p.replace("\\", "/"))
    return out


def revert(paths):
    for p in paths:
        tracked = git("ls-files", "--error-unmatch", p, check=False)
        if tracked.strip():
            git("checkout", "--", p)
        else:
            try:
                os.remove(os.path.join(ROOT, p))
            except OSError:
                pass


def ensure_branch(t):
    dirty = [p for p in changed_paths() if not p.startswith("improve/tracks/") and not p.startswith("memory/")]
    if dirty:
        raise SystemExit("the working tree has uncommitted changes outside improve/tracks and memory; commit or stash them first:\n  "
                         + "\n  ".join(dirty))
    cur = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    if cur != t.branch:
        exists = git("branch", "--list", t.branch).strip()
        git("checkout", t.branch) if exists else git("checkout", "-b", t.branch)
        log(f"on branch {t.branch}" + ("" if exists else f" (new, from {cur})"))


def wait_for_gpu(t):
    if not t.cfg.get("gpu"):
        return
    waited = False
    while True:
        try:
            with urllib.request.urlopen(STUDIO + "/api/health", timeout=3) as r:
                h = json.load(r)
        except Exception:  # noqa: BLE001 — studio not running: nothing of ours is using the GPU
            return
        if not h.get("running") and not h.get("queued"):
            if waited:
                log("studio idle, continuing")
            return
        if not waited:
            log("the studio is running a job; waiting for the GPU")
            waited = True
        time.sleep(30)


def run_scorer(t, n, tag=""):
    folder = os.path.join(t.runs, f"{n:03d}{tag}")
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, "score.json")
    cmd, timeout = t.score_cmd(out, os.path.join(folder, "work"))
    wait_for_gpu(t)
    log("scoring: " + " ".join(rel(c) if c.startswith(ROOT) else c for c in cmd))
    with open(os.path.join(folder, "score.log"), "w", encoding="utf-8") as lf:
        try:
            r = subprocess.run(cmd, cwd=ROOT, stdout=lf, stderr=subprocess.STDOUT, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None, folder, f"scorer exceeded {timeout} s"
    if r.returncode != 0 or not os.path.isfile(out):
        tail = read(os.path.join(folder, "score.log"))[-800:]
        return None, folder, f"scorer failed (exit {r.returncode}):\n{tail}"
    return json.load(open(out, encoding="utf-8")), folder, None


def guards_ok(t, score):
    bad = []
    for k, (lo, hi) in (t.cfg["metric"].get("guards") or {}).items():
        v = score.get(k)
        if v is None or not (lo <= v <= hi):
            bad.append(f"{k}={v} not in [{lo}, {hi}]")
    return bad


def also(t, score):
    keys = [t.key] + list(t.cfg["metric"].get("also") or [])
    return ", ".join(f"{k} {score.get(k)}" for k in keys if k in score)


def propose_prompt(t):
    m = t.cfg["metric"]
    best = t.state.get("best")
    results = read(t.file("results.tsv")).strip().splitlines()
    results = "\n".join(results[:1] + results[-15:]) if len(results) > 16 else "\n".join(results)
    return f"""# Track: {t.name} — {t.cfg.get('label', '')}

Goal: {t.cfg['goal']}

Metric: `{m['key']}` ({'higher' if t.sign > 0 else 'lower'} is better). A change is kept when it beats the best score by at
least {t.min_delta():.4f} (the track's min_delta or the measured noise, whichever is larger). Guards that must hold: {json.dumps(m.get('guards') or {})}.
Best so far: {best if best is not None else 'not scored yet (this iteration scores the baseline first)'} (iteration {t.state.get('best_iter')}); baseline {t.state.get('baseline')}; noise {t.state.get('noise')}.

You may edit ONLY these files: {', '.join(t.cfg['editable'])}
Read these first: {', '.join(t.cfg.get('context') or [])}
The scorer (do not run it, the runner does): {rel(t.cfg['score']['cmd'][1]) if len(t.cfg['score']['cmd']) > 1 else '?'}; read its docstring to understand exactly what is measured.

## Playbook (improve/tracks/{t.name}/NOTES.md)
{read(t.file('NOTES.md'))}

## Backlog (improve/tracks/{t.name}/backlog.md)
{read(t.file('backlog.md'))}

## Results so far (improve/tracks/{t.name}/results.tsv)
{results or '(none)'}

## Handoff from the previous iteration (improve/tracks/{t.name}/next.md)
{read(t.file('next.md'))}

## Memory
Read memory/MEMORY.md and open the memories that look relevant to this track before deciding.

Now pick ONE hypothesis, make the change in the editable files, and answer with the JSON: title (a few words),
hypothesis (what and why), change (what you edited, concretely), expect (what the metric should do and why), files."""


def reflect_prompt(t, n, prop, score, status, why, delta, folder):
    sheet = (score or {}).get("sheet")
    return f"""# Track: {t.name}, iteration {n}: write the notes

The runner scored the change this iteration proposed. Record the outcome and prepare the next iteration.

Hypothesis: {prop.get('title')} — {prop.get('hypothesis')}
Change: {prop.get('change')}
Expected: {prop.get('expect')}
Result: **{status}** — {why}
Score: {json.dumps({k: v for k, v in (score or {}).items() if k != 'per_prop'}, indent=None)}
Per prop: {json.dumps((score or {}).get('per_prop'), indent=None)}
Best before this iteration: {t.state.get('best')} (iteration {t.state.get('best_iter')}); min delta {t.min_delta():.4f}; delta of this change {delta}.
The diff of the change (already reverted from the working tree if discarded): improve/runs/{os.path.basename(t.runs)}/{os.path.basename(folder)}/change.patch
{f'Contact sheet, original clay | generated clay per prop (look at it with Read): {sheet}' if sheet else ''}

Do all of the following, editing only improve/tracks/{t.name}/ and memory/:
1. NOTES.md: add this iteration under "Tried" (one or two lines: what, result, what it tells us); update "What the
   metric rewards" if the per-prop numbers taught something; note any harness problem under "Harness problems".
2. backlog.md: mark the tried item with the iteration number and result, reorder or add items if the result
   suggests better ones, strike items the result rules out (keep them, crossed out).
3. next.md: the handoff. Which backlog item the next iteration should take and exactly how to approach it, in a
   few lines, written for a session that has none of your context.
4. memory/: only if this iteration taught something durable (a measured number, a lever that does nothing, a trap):
   one memory file following AGENTS.md's rules plus its index line in memory/MEMORY.md; otherwise nothing.
Then answer with the JSON: summary (one line for results.tsv), memory_files (paths written, or []), next (the title
of the hypothesis you recommend next)."""


def iterate(t, n, args, system):
    folder = os.path.join(t.runs, f"{n:03d}")
    os.makedirs(folder, exist_ok=True)
    cost = 0.0
    editable = t.cfg["editable"]
    readonly = expand(t.cfg.get("readonly") or [])
    h0 = digest(readonly)

    # 1. propose
    log(f"iteration {n}: proposing ({args.model})")
    allowed = ["Read", "Glob", "Grep"] + [f"{tool}(./{p})" for p in editable for tool in ("Edit", "Write")]
    prop, c, _ = claude_call(propose_prompt(t), system, allowed, PROPOSE_SCHEMA, args.model, args.step_budget,
                             os.path.join(folder, "propose.jsonl"), effort=args.effort)
    cost += c
    prop = prop or {"title": "(no structured answer)", "hypothesis": "", "change": "", "expect": "", "files": []}
    json.dump(prop, open(os.path.join(folder, "hypothesis.json"), "w", encoding="utf-8"), indent=1)
    log(f"  hypothesis: {prop['title']} (${c:.2f})")

    # 2. check the change stayed inside the lines
    changed = [p for p in changed_paths() if not p.startswith("improve/tracks/") and not p.startswith("memory/")]
    outside = [p for p in changed if not matches(p, editable)]
    with open(os.path.join(folder, "change.patch"), "w", encoding="utf-8") as f:
        f.write(git("diff", "--", *[p for p in changed if matches(p, editable)]) if changed else "")
    status, why, score, delta, commit = "discard", "", None, None, ""
    if outside or digest(readonly) != h0:
        status, why = "violation", "changed files outside the editable list: " + ", ".join(outside or ["(a read-only file)"])
        revert(changed)
    elif not changed:
        status, why = "noop", "no file was changed"
    else:
        # 3. score
        score, sfolder, err = run_scorer(t, n)
        if err:
            status, why = "crash", err
        else:
            v = float(score[t.key])
            cost += float(score.get("cost_usd") or 0)
            best = t.state.get("best")
            delta = round((v - best) * t.sign, 4) if best is not None else None
            bad = guards_ok(t, score)
            if bad:
                why = "guard: " + "; ".join(bad)
            elif best is None:
                status, why = "keep", "first score becomes the baseline"
            elif delta >= t.min_delta():
                status, why = "keep", f"{t.key} {best} -> {v} (+{delta} >= {t.min_delta():.4f})"
            else:
                why = f"{t.key} {best} -> {v} ({delta:+} < {t.min_delta():.4f})"
            log(f"  score: {also(t, score)} -> {status}: {why}")
        if status == "keep":
            git("add", "--", *[p for p in changed if matches(p, editable)])
            git("commit", "-q", "-m", f"improve({t.name}) #{n}: {prop['title']}\n\n{prop['hypothesis']}\n\n{why}\n\n"
                                      f"Co-Authored-By: Claude ({args.model}) <noreply@anthropic.com>")
            commit = git("rev-parse", "--short", "HEAD").strip()
            t.state.update(best=float(score[t.key]), best_iter=n)
            if t.state.get("baseline") is None:
                t.state["baseline"] = float(score[t.key])
        else:
            revert(changed)

    # 4. reflect
    log(f"  reflecting ({args.reflect_model})")
    allowed = ["Read", "Glob", "Grep", f"Edit(./improve/tracks/{t.name}/**)", f"Write(./improve/tracks/{t.name}/**)",
               "Edit(./memory/**)", "Write(./memory/**)"]
    try:
        refl, c, _ = claude_call(reflect_prompt(t, n, prop, score, status, why, delta, folder), system, allowed, REFLECT_SCHEMA,
                                 args.reflect_model, args.step_budget, os.path.join(folder, "reflect.jsonl"))
        cost += c
    except RuntimeError as e:
        log(f"  reflect failed: {e}")
        refl = None
    summary = (refl or {}).get("summary") or prop["title"]
    t.results_row(n, status, None if score is None else float(score[t.key]), delta, cost, commit, summary.replace("\t", " "))
    t.state["spent_usd"] = round(t.state.get("spent_usd", 0) + cost, 2)
    t.state["iteration"] = n
    t.save()
    stray = [p for p in changed_paths() if not (p.startswith("improve/tracks/") or p.startswith("memory/"))]
    if stray:
        log("  reflect touched files outside its lines, reverting: " + ", ".join(stray))
        revert(stray)
    notes = [p for p in changed_paths() if p.startswith("improve/tracks/") or p.startswith("memory/")]
    if notes:
        git("add", "--", *notes)
        git("commit", "-q", "-m", f"improve({t.name}) #{n}: notes ({status})\n\nCo-Authored-By: Claude ({args.reflect_model}) <noreply@anthropic.com>")
    log(f"  iteration {n} done: {status}, ${cost:.2f} this iteration, ${t.state['spent_usd']:.2f} this track")
    return status


def calibrate(t, times):
    vals = []
    for i in range(times):
        n = t.state["iteration"]
        score, folder, err = run_scorer(t, n, tag=f"-cal{i + 1}")
        if err:
            raise SystemExit(err)
        v = float(score[t.key])
        t.state["spent_usd"] = round(t.state.get("spent_usd", 0) + float(score.get("cost_usd") or 0), 2)
        vals.append(v)
        log(f"calibration {i + 1}/{times}: {also(t, score)}")
    mean = sum(vals) / len(vals)
    spread = max(vals) - min(vals) if len(vals) > 1 else 0.0
    t.state.update(baseline=round(mean, 4), best=round(mean, 4), best_iter=0, noise=round(spread, 4), calibration=vals)
    t.save()
    t.results_row(0, "baseline", mean, None, sum(float(json.load(open(os.path.join(t.runs, f"{t.state['iteration']:03d}-cal{i + 1}", "score.json"))).get("cost_usd") or 0) for i in range(times)),
                  "", f"calibration x{times}: {', '.join(f'{v:.4f}' for v in vals)}, spread {spread:.4f}")
    log(f"baseline {mean:.4f}, spread {spread:.4f} -> a change must beat the best by {t.min_delta():.4f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("track")
    ap.add_argument("--iterations", type=int, default=3)
    ap.add_argument("--budget-usd", type=float, default=30.0, help="stop when this run has spent this much on Claude")
    ap.add_argument("--step-budget", type=float, default=5.0, help="cap for each propose / reflect session")
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--model", default="opus", help="model that proposes the change")
    ap.add_argument("--reflect-model", default="sonnet", help="model that writes the notes")
    ap.add_argument("--effort")
    ap.add_argument("--props", default="", help="scorer subset Name,Name (smoke tests; separate state)")
    ap.add_argument("--calibrate", type=int, default=0, help="score the unchanged files N times, record the noise, exit")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    t = Track(args.track, args.props or None)
    system = read(os.path.join(HERE, "PROGRAM.md"))
    if args.dry_run:
        print(propose_prompt(t))
        cmd, _ = t.score_cmd("<score.json>", "<workdir>")
        print("\nscore command:\n  " + " ".join(cmd))
        print(f"\nread-only files hashed: {len(expand(t.cfg.get('readonly') or []))}; editable: {', '.join(t.cfg['editable'])}")
        print(f"state: {json.dumps(t.state)}")
        return
    if not os.path.isfile(PY):
        raise SystemExit(f"{rel(PY)} is missing: run ai-3d/setup.ps1")
    ensure_branch(t)
    if args.calibrate:
        calibrate(t, args.calibrate)
        return
    if t.state.get("best") is None:
        log("no baseline yet: scoring the unchanged files once first (use --calibrate 2 or more to measure the noise)")
        calibrate(t, 1)
    spent0, t0 = t.state.get("spent_usd", 0), time.time()
    for i in range(args.iterations):
        if os.path.isfile(os.path.join(HERE, "STOP")):
            log("improve/STOP exists, stopping")
            break
        if t.state.get("spent_usd", 0) - spent0 >= args.budget_usd:
            log(f"budget of ${args.budget_usd:.2f} spent, stopping")
            break
        if time.time() - t0 > args.hours * 3600:
            log("time limit reached, stopping")
            break
        n = t.state["iteration"] + 1
        try:
            iterate(t, n, args, system)
        except KeyboardInterrupt:
            log("interrupted; reverting any unscored change")
            revert([p for p in changed_paths() if matches(p, t.cfg["editable"])])
            break
        except Exception as e:  # noqa: BLE001
            log(f"iteration {n} failed: {e}")
            revert([p for p in changed_paths() if matches(p, t.cfg["editable"])])
            t.results_row(n, "error", None, None, 0.0, "", str(e).replace("\n", " ")[:200])
            t.state["iteration"] = n
            t.save()
    log(f"done: best {t.state.get('best')} (iteration {t.state.get('best_iter')}), ${t.state.get('spent_usd', 0):.2f} spent on this track; "
        f"review with: git log --oneline main..{t.branch}  and  improve/tracks/{t.name}/results.tsv")


if __name__ == "__main__":
    main()
