r"""Shot list -> finished video. Each shot is a Wan 2.2 clip (from text, from a start frame, or pinned between a
start and an end frame), or a logo reveal rendered from a PNG; clips are chained (a shot can start on the previous
shot's last frame), then joined with cross-fades and optional music.

Usage:
    intro.bat shots\example_intro.json                generate every shot, assemble output\<name>\<name>.mp4
    intro.bat shots\example_intro.json --only reflection,drop   regenerate just these shots (others reused), re-assemble
    intro.bat shots\example_intro.json --assemble-only just join the existing clips (new transitions / audio)
    intro.bat shots\example_intro.json --keyframes-only generate the still keyframes and stop (check them first)

Shot list (JSON):
{
  "name": "example_intro", "width": 832, "height": 480, "fps": 24, "transition": 0, "audio": null,
  "shots": [
    {"id": "drop", "prompt": "...", "seconds": 3, "seed": 1,
     "start": {"generate": "still keyframe prompt", "refs": ["assets/logo.png"], "model": "base"},   # or {"image": "x.png"}, "previous", null
     "end": null},                                                                                    # or {"generate"...} / {"image"...}
    {"id": "reveal", "type": "logo", "logo": "assets/logo.png", "seconds": 3, "split": 0.69, "background": "#ffffff", "from": "previous"}
  ]
}
Keyframes are drawn with FLUX.2 klein (ai-3d/sprite.py, raw prompt, opaque): refs[0] is "image 1" in the prompt.
Relative paths are resolved against the shot-list file, then ai-vid/. Everything lands in output/<name>/.

run_project() is also used by the studio web UI (../../studio) with its own ComfyUI executor for live progress.
"""
import argparse
import json
import os
import shutil
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(ROOT)
sys.path.insert(0, os.path.join(REPO, "ai-3d", "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shot as shotmod  # noqa: E402
import logo_reveal  # noqa: E402
import assemble as asm  # noqa: E402
import sprite as gensprite  # noqa: E402
from generate import post  # noqa: E402


def resolve(path, base):
    if not path:
        return path
    if os.path.isabs(path):
        return path
    for b in (base, ROOT, REPO):
        p = os.path.join(b, path)
        if os.path.exists(p):
            return os.path.abspath(p)
    return os.path.abspath(os.path.join(base, path))


def default_executor(api, label=""):
    """CLI executor: queue, poll, return the history entry."""
    res = post("/prompt", {"prompt": api, "client_id": str(uuid.uuid4())})
    if "prompt_id" not in res:
        raise RuntimeError(json.dumps(res, indent=1)[:2000])
    print(f"  queued {label} ({res['prompt_id']})")
    return shotmod.wait(res["prompt_id"])


def result_image(hist, node_id):
    for nid, out in hist.get("outputs", {}).items():
        if str(nid) == str(node_id):
            for img in out.get("images", []) or []:
                return os.path.join(shotmod.COMFY_OUTPUT, img.get("subfolder", ""), img["filename"])
    return None


def gen_keyframe(spec, width, height, dst, executor, base, log=print):
    """{"generate": prompt, "refs": [...], "model": "base"|"distilled", "seed": n, "steps": n} -> dst PNG."""
    refs = [resolve(r, base) for r in spec.get("refs") or []]
    for r in refs:
        if not os.path.isfile(r):
            raise FileNotFoundError(f"reference image not found: {r}")
    api, final, state = gensprite.build_api(
        spec["generate"], refs[0] if refs else None, refs[1:], spec.get("model", "base"), spec.get("steps"), spec.get("cfg"),
        spec.get("negative") or gensprite.NEGATIVE, spec.get("seed"), raw=True, width=width, height=height, alpha="none",
        upload_prefix=f"aivid_key_{uuid.uuid4().hex[:6]}")
    log(f"  keyframe: {spec['model'] if 'model' in spec else 'base'} model, seed {state['seed']}, {len(refs)} ref(s)")
    hist = executor(api, "keyframe")
    src = result_image(hist, final)
    if not src:
        raise RuntimeError("keyframe: no image in the ComfyUI output")
    shutil.copyfile(src, dst)
    return dst, state["seed"]


def frame_source(spec, which, s, project, prev_last, width, height, executor, base, log, force):
    """Resolve a shot's start/end spec to a PNG path (or None)."""
    v = s.get(which)
    if not v:
        return None, None
    if v == "previous":
        if prev_last is False:    # keyframes-only pass: no clips are rendered, so there is no previous frame yet
            return None, None
        if not prev_last:
            raise ValueError(f"shot {s['id']}: '{which}: previous' but there is no previous shot")
        return prev_last, None
    if isinstance(v, dict) and v.get("image"):
        p = resolve(v["image"], base)
        if not os.path.isfile(p):
            raise FileNotFoundError(f"shot {s['id']}: {which} image not found: {p}")
        return p, None
    if isinstance(v, dict) and v.get("generate"):
        dst = os.path.join(project, f"key_{s['id']}_{which}.png")
        if os.path.isfile(dst) and not force:
            log(f"  {which} keyframe reused: {os.path.basename(dst)}")
            return dst, None
        return gen_keyframe(v, width, height, dst, executor, base, log)
    raise ValueError(f"shot {s['id']}: {which} must be 'previous', {{'image': path}} or {{'generate': prompt}}")


def run_project(spec, base, only=None, assemble_only=False, keyframes_only=False, executor=default_executor,
                log=print, on_shot=lambda i, s, stage, **d: None, out_root=None):
    """Runs the shot list. Returns {"final": mp4, "clips": [...], "project": dir}.

    only: list of shot ids to (re)generate; others must already have a clip in the project folder.
    on_shot(index, shot, stage, **info) is called at 'keyframes', 'start', 'done' for progress displays.
    """
    name = spec.get("name") or "intro"
    width, height, fps = int(spec.get("width", 832)), int(spec.get("height", 480)), int(spec.get("fps", 24))
    project = os.path.join(out_root or os.path.join(ROOT, "output"), name)
    os.makedirs(project, exist_ok=True)
    with open(os.path.join(project, "shots.json"), "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=1)
    shots = spec.get("shots") or []
    if not shots:
        raise ValueError("no shots")
    ids = [s.get("id") or f"shot{i + 1}" for i, s in enumerate(shots)]
    for s, sid in zip(shots, ids):
        s["id"] = sid
    only = set(only or [])
    unknown = only - set(ids)
    if unknown:
        raise ValueError(f"unknown shot id(s): {', '.join(sorted(unknown))}")
    clips, prev_last = [], None
    t_all = time.time()
    for i, s in enumerate(shots):
        sid = s["id"]
        mp4 = os.path.join(project, f"{sid}.mp4")
        last = os.path.join(project, f"{sid}_last.png")
        regenerate = not assemble_only and not keyframes_only and (not only or sid in only or not os.path.isfile(mp4))
        if assemble_only or (only and sid not in only and os.path.isfile(mp4)):
            if not os.path.isfile(mp4):
                raise FileNotFoundError(f"shot {sid}: no clip yet ({mp4}); generate it first")
            log(f"[{i + 1}/{len(shots)}] {sid}: reusing {os.path.basename(mp4)}")
            chained = s.get("start") == "previous" or (s.get("type") == "logo" and s.get("from", "previous") == "previous")
            clips.append({"id": sid, "mp4": mp4, "last": last if os.path.isfile(last) else None, "reused": True,
                          "transition": 0 if chained else s.get("transition", spec.get("transition", 0))})
            prev_last = last if os.path.isfile(last) else None
            on_shot(i, s, "done", mp4=mp4, reused=True)
            continue
        log(f"[{i + 1}/{len(shots)}] {sid}: {s.get('type', 'video')}")
        on_shot(i, s, "keyframes")
        t0 = time.time()
        if s.get("type") == "logo":
            logo = resolve(s.get("logo"), base)
            if not logo or not os.path.isfile(logo):
                raise FileNotFoundError(f"shot {sid}: logo not found: {logo}")
            if keyframes_only:
                continue
            on_shot(i, s, "start", kind="logo")
            frm = prev_last if s.get("from", "previous") == "previous" else resolve(s.get("from"), base) if s.get("from") else None
            logo_reveal.render(logo, mp4, width, height, fps, float(s.get("seconds", 3.0)), float(s.get("split", 0.69)),
                               s.get("background", "#ffffff"), frm, float(s.get("scale", 0.8)), float(s.get("pop", 0.9)),
                               float(s.get("text_start", 0.45)), float(s.get("text_dur", 0.8)), float(s.get("lead_in", 0.5)))
            info = {"kind": "logo", "seconds": s.get("seconds", 3.0)}
        else:
            force = bool(only and sid in only) and regenerate
            prev = False if keyframes_only else prev_last
            start, start_seed = frame_source(s, "start", s, project, prev, width, height, executor, base, log, force and s.get("regen_keyframes", False))
            end, end_seed = frame_source(s, "end", s, project, prev, width, height, executor, base, log, force and s.get("regen_keyframes", False))
            if keyframes_only:
                on_shot(i, s, "done", keyframes=[p for p in (start, end) if p])
                continue
            on_shot(i, s, "start", kind="video", start=start, end=end)
            api, info = shotmod.build_api(s["prompt"], start, end, width, height, float(s.get("seconds", 3.0)), s.get("seed"),
                                          int(s.get("steps", 20)), float(s.get("cfg", 5.0)), s.get("negative") or spec.get("negative") or shotmod.NEGATIVE,
                                          float(s.get("shift", 8.0)))
            log(f"  {info['width']}x{info['height']}, {info['length']} frames, seed {info['seed']}, start={'yes' if start else 'no'} end={'yes' if end else 'no'}")
            hist = executor(api, sid)
            shotmod.finish_clip(hist, info, mp4, drop_first=(s.get("start") == "previous"))
            s["seed"] = info["seed"]   # remembered in the project's shots.json
        # continuous shots (start on the previous last frame, or a reveal fading up from it) always cut hard
        chained = s.get("start") == "previous" or (s.get("type") == "logo" and s.get("from", "previous") == "previous")
        clips.append({"id": sid, "mp4": mp4, "last": last, "transition": 0 if chained else s.get("transition", spec.get("transition", 0)), "info": info})
        prev_last = last
        log(f"  clip: {mp4} ({time.time() - t0:.0f}s)")
        on_shot(i, s, "done", mp4=mp4, info=info)
        with open(os.path.join(project, "shots.json"), "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=1)
    if keyframes_only:
        return {"final": None, "clips": clips, "project": project}
    final = os.path.join(project, f"{name}.mp4")
    audio = resolve(spec.get("audio"), base) if spec.get("audio") else None
    if audio and not os.path.isfile(audio):
        raise FileNotFoundError(f"audio not found: {audio}")
    transitions = [float(c["transition"] or 0) for c in clips[1:]]
    log(f"assembling {len(clips)} clips -> {final}" + (f" with {os.path.basename(audio)}" if audio else ""))
    asm.assemble([c["mp4"] for c in clips], final, transitions, audio, width, height, fps, int(spec.get("crf", 16)),
                 float(spec.get("audio_fade", 1.0)), spec.get("transition_kind", "fade"))
    log(f"done: {final} ({time.time() - t_all:.0f}s)")
    return {"final": final, "clips": clips, "project": project}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("shots", help="shot list JSON")
    ap.add_argument("--only", help="comma-separated shot ids to regenerate (others are reused)")
    ap.add_argument("--assemble-only", action="store_true")
    ap.add_argument("--keyframes-only", action="store_true")
    ap.add_argument("--out", help="output root (default ai-vid/output)")
    a = ap.parse_args()
    spec = json.load(open(a.shots, encoding="utf-8"))
    base = os.path.dirname(os.path.abspath(a.shots))
    try:
        r = run_project(spec, base, [x.strip() for x in a.only.split(",")] if a.only else None, a.assemble_only, a.keyframes_only,
                        out_root=a.out)
    except (RuntimeError, ValueError, FileNotFoundError, TimeoutError) as e:
        sys.exit(str(e))
    if r["final"]:
        print("final:", r["final"])


if __name__ == "__main__":
    main()
