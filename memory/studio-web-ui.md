---
name: studio-web-ui
description: studio/ is the browser front-end + HTTP API/CLI (stdlib Python server + vanilla JS) for all ai-* flows; how to run and test it, how Claude uses it
metadata:
  type: project
---

`studio/studio.bat` (ComfyUI venv Python, port 8765) is the web UI over ai-3d, ai-voc, ai-mus, ai-vid and ai-anim.
Flows import the CLIs' `build_api()` builders, so CLI and UI stay identical; voice/music/anim run through persistent
worker processes (JSON lines on stdin/stdout, `core/procworker.py`). Since 2026-10-26 the same queue is an API:
`core/schema.py` (one table per kind: params, validation, `GET /api/schema`), `GET /api/jobs/<id>/wait` (long poll),
`GET /api/health`, `studio/studio_cli.py` (stdlib client) and the Claude skill `.claude/skills/asset-jam-studio/SKILL.md`
(copy to `~/.claude/skills/` to use from a game project). `studio/API.md` documents it.

**Why:** one job at a time on the 8 GB card: the worker unloads the other model sets before each job. The user wants
Claude to generate assets for games (three.js etc.) through the studio, so the skill + CLI route was chosen over an
MCP server: no extra config, transparent commands, and `--wait` keeps progress visible in the tool output.

**How to apply:** when testing in the Claude app, run the server with a background Bash command
(`python studio/server.py --no-browser`), not `preview_start`. Reload the page with `navigate`. ComfyUI's output
goes to `studio/comfy.log` (a file, not a pipe) since 2026-10-26: a ComfyUI left over from a studio that died
with a *piped* stdout fails every job with `[Errno 22] Invalid argument` in KSampler; the only fix is restarting
ComfyUI (killing processes was denied to Claude in auto mode, so ask the user). Keep `core/schema.py` in step with
`core/flows.py` whenever a flow gains a parameter. See [[text-to-3d-setup]], [[voice-generation-setup]].
