"""Have Claude model a prop from its reference picture: runs Claude Code headless (`claude -p`) in the prop's own
folder with prop_brief.md as its brief. Claude writes <Name>.py with prop_kit.py, builds it with prop_check.py,
looks at the clay views next to the picture and revises, then answers with the paint settings (prompt, front,
plain, lock, low). Claude may only write inside that folder, read files, and run that one check command.

    python prop_model.py reference.png "what the object is" <workdir> <Name> [--model sonnet]

Uses the Claude Code login of this machine (`claude` on PATH or ~/.local/bin/claude.exe); CLAUDE_CLI overrides.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPTS)
PY = os.path.join(ROOT, "ComfyUI", ".venv", "Scripts", "python.exe")
SCHEMA = {"type": "object", "additionalProperties": False,
          "properties": {"prompt": {"type": "string"}, "front": {"type": "string"}, "plain": {"type": "string"},
                         "lock": {"type": "array", "items": {"type": "string"}}, "low": {"type": "boolean"},
                         "notes": {"type": "string"}},
          "required": ["prompt", "front", "plain", "lock", "low", "notes"]}


def claude_cli():
    for c in (os.environ.get("CLAUDE_CLI"), shutil.which("claude"),
              os.path.join(os.path.expanduser("~"), ".local", "bin", "claude.exe")):
        if c and os.path.isfile(c):
            return c
    return None


def _fwd(p):
    return os.path.abspath(p).replace("\\", "/")


def model_prop(picture, description, workdir, name, model=None, rounds=6, effort=None,
               on_event=lambda kind, **d: None, should_cancel=lambda: False, timeout=1800):
    """Returns {glb, script, sheets: [check.png per round], paint: {prompt, front, plain, lock, low, notes}, cost_usd}.
    on_event(kind, ...): "text" (text=), "write" (path=), "check" (round=), "sheet" (round=, path=), "done"."""
    cli = claude_cli()
    if not cli:
        raise RuntimeError("Claude Code CLI not found: install it (claude.ai/code) and log in, or set CLAUDE_CLI")
    os.makedirs(workdir, exist_ok=True)
    shutil.copyfile(picture, os.path.join(workdir, "reference.png"))
    # the Bash tool is Git Bash on Windows: forward slashes, no spaces in these paths
    check = f"{_fwd(PY)} {_fwd(os.path.join(SCRIPTS, 'prop_check.py'))}"
    with open(os.path.join(SCRIPTS, "prop_brief.md"), encoding="utf-8") as f:
        brief = (f.read().replace("{SCRIPTS}", os.path.abspath(SCRIPTS)).replace("{CHECK}", check)
                 .replace("{NAME}", name).replace("{ROUNDS}", str(rounds)))
    task = (f"Model this prop: {description}\nThe reference picture is reference.png in the current folder. "
            f"Write {name}.py here and follow the loop in your instructions.")
    cmd = [cli, "-p", task, "--output-format", "stream-json", "--verbose", "--append-system-prompt", brief,
           "--tools", "Read,Write,Edit,Bash", "--permission-mode", "dontAsk",
           "--allowedTools", "Read", "Write(./**)", "Edit(./**)", f"Bash({check} *)",
           "--add-dir", os.path.abspath(SCRIPTS), "--strict-mcp-config", "--disable-slash-commands",
           "--no-session-persistence", "--json-schema", json.dumps(SCHEMA)]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    log_path = os.path.join(workdir, "claude.jsonl")
    proc = subprocess.Popen(cmd, cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    import threading
    lines = []
    threading.Thread(target=lambda: lines.extend(proc.stdout), daemon=True).start()
    t0, seen, checks, sheets, result = time.time(), 0, 0, [], None
    sheet_path = os.path.join(workdir, "check.png")
    last_sheet = 0.0
    with open(log_path, "w", encoding="utf-8") as log:
        while True:
            alive = proc.poll() is None
            while seen < len(lines):
                ln = lines[seen]
                seen += 1
                log.write(ln)
                try:
                    ev = json.loads(ln)
                except ValueError:
                    on_event("text", text=ln.strip()[:300])
                    continue
                if ev.get("type") == "assistant":
                    for c in ev.get("message", {}).get("content", []):
                        if c.get("type") == "text" and c.get("text", "").strip():
                            on_event("text", text=c["text"].strip()[:400])
                        elif c.get("type") == "tool_use":
                            inp = c.get("input") or {}
                            if c["name"] in ("Write", "Edit"):
                                on_event("write", path=inp.get("file_path", ""))
                            elif c["name"] == "Bash":
                                checks += 1
                                on_event("check", round=checks)
                elif ev.get("type") == "result":
                    result = ev
            # a new check sheet: keep a copy per round so the studio can show the progression
            if os.path.isfile(sheet_path) and os.path.getmtime(sheet_path) > last_sheet:
                last_sheet = os.path.getmtime(sheet_path)
                keep = os.path.join(workdir, f"check_{len(sheets) + 1}.png")
                shutil.copyfile(sheet_path, keep)
                sheets.append(keep)
                on_event("sheet", round=len(sheets), path=keep)
            if not alive and seen >= len(lines):
                break
            if should_cancel():
                proc.kill()
                raise RuntimeError("cancelled")
            if time.time() - t0 > timeout:
                proc.kill()
                raise RuntimeError(f"Claude did not finish modelling within {timeout // 60} minutes")
            time.sleep(0.5)
    glb = os.path.join(workdir, name + ".glb")
    if result is None or result.get("is_error") or result.get("subtype") != "success":
        msg = (result or {}).get("result") or (result or {}).get("subtype") or "".join(lines[-5:])
        raise RuntimeError(f"Claude's modelling session failed: {str(msg)[-800:]}")
    if not os.path.isfile(glb):
        raise RuntimeError(f"Claude finished without building {name}.glb (see {log_path})")
    paint = result.get("structured_output")
    if not isinstance(paint, dict):
        try:
            paint = json.loads(result.get("result") or "")
        except ValueError:
            paint = {"prompt": description, "front": "", "plain": "", "lock": [], "low": False, "notes": result.get("result", "")}
    on_event("done")
    return {"glb": glb, "script": os.path.join(workdir, name + ".py"), "sheets": sheets, "paint": paint,
            "cost_usd": result.get("total_cost_usd"), "seconds": round(time.time() - t0), "turns": result.get("num_turns")}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("picture")
    ap.add_argument("description")
    ap.add_argument("workdir")
    ap.add_argument("name")
    ap.add_argument("--model")
    ap.add_argument("--rounds", type=int, default=6)
    a = ap.parse_args()
    out = model_prop(a.picture, a.description, a.workdir, a.name, a.model, a.rounds,
                     on_event=lambda kind, **d: print(f"[{kind}]", json.dumps(d)[:300], flush=True))
    print(json.dumps(out, indent=1))
