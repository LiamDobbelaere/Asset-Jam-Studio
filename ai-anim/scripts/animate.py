r"""Character pipeline from the command line: mesh -> rigged character -> text-described animation clips.

    animate.bat rig  D:\path\Hero.glb --name Hero [--height 1.8] [--no-fingers] [--keep-pose]
    animate.bat anim Hero --clip "Idle=a person stands still, breathing calmly and shifting weight" --seconds 4
                          --clip "Walk=a person walks forward at a relaxed pace" [--seed N] [--inplace] [--steps 100]
    animate.bat full D:\path\Hero.glb --name Hero --clip "Idle=..." --clip "Walk=..."
    animate.bat motion "a person waves hello" --seconds 3 --out output/clips/Wave      (BVH only, no character)

`rig` runs Make-It-Animatable on the mesh and builds the Mixamo-named skeleton in Blender; the result goes
to assets/<Name>/ (<Name>.glb, <Name>.fbx, <Name>Diffuse.png, <Name>.blend, rig.npz).
`anim` generates one Kimodo clip per --clip "Name=prompt" (several sentences separated by " | " play in
sequence inside one clip), retargets them onto the rig and re-exports the GLB (glTF animations) and FBX
(one take per clip) in place, keeping clips from earlier runs; the BVH files are kept in assets/<Name>/clips/.
`full` is rig + anim. Blender 5.x is needed (BLENDER env var, default C:\\Program Files\\Blender Foundation\\Blender 5.1).

The functions here (rig_character, animate_character) are also what the studio's Animate tab calls.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(ROOT, "assets")
BLENDER = os.environ.get("BLENDER", r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")


def safe_name(name, fallback="Character"):
    return re.sub(r"[^A-Za-z0-9_\-]+", "", name or "") or fallback


def blender(script, args, log=print):
    if not os.path.exists(BLENDER):
        raise RuntimeError(f"Blender not found at {BLENDER}; set the BLENDER env var")
    cmd = [BLENDER, "-b", "--python", os.path.join(HERE, script), "--", *map(str, args)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    marks = [l for l in r.stdout.splitlines() if l[:4] in ("RIG ", "ANIM", "CLIP", "PREV")]
    for l in marks:
        log(l)
    if r.returncode != 0 or not any(l.startswith(("RIG ", "ANIM ")) for l in marks):
        tail = "\n".join((r.stdout + r.stderr).splitlines()[-40:])
        raise RuntimeError(f"Blender {script} failed:\n{tail}")
    return marks


def meta_path(folder):
    return os.path.join(folder, "character.json")


def read_meta(folder):
    try:
        return json.load(open(meta_path(folder), encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_meta(folder, meta):
    json.dump(meta, open(meta_path(folder), "w", encoding="utf-8"), indent=1)


def rig_character(mesh, name, height=1.8, no_fingers=False, keep_pose=False, out_root=ASSETS, log=print,
                  rig_fn=None):
    """mesh -> assets/<name>/ with the rigged GLB/FBX/blend. rig_fn(mesh, npz) may replace the in-process MIA call
    (the studio passes the persistent worker). Returns the character folder."""
    name = safe_name(name)
    folder = os.path.join(out_root, name)
    os.makedirs(folder, exist_ok=True)
    npz = os.path.join(folder, "rig.npz")
    t0 = time.time()
    if rig_fn is None:
        import rig_infer
        rig_infer.rig(mesh, npz, no_fingers=no_fingers, log=log)
    else:
        rig_fn(mesh, npz)
    src_copy = os.path.join(folder, "source" + os.path.splitext(mesh)[1].lower())
    if os.path.abspath(mesh) != os.path.abspath(src_copy):
        shutil.copyfile(mesh, src_copy)
    args = [mesh, npz, folder, name, "--height", height]
    if keep_pose:
        args.append("--keep-pose")
    marks = blender("blender_rig.py", args, log)
    stats = next((l[4:] for l in marks if l.startswith("RIG ")), "")
    meta = read_meta(folder)
    meta.update(name=name, source=os.path.abspath(mesh), height=height, no_fingers=no_fingers, keep_pose=keep_pose,
                rigged=time.time(), rig_stats=stats, clips=[])
    write_meta(folder, meta)
    log(f"rigged {name} in {time.time() - t0:.0f}s -> {folder}")
    return folder


def animate_character(name, clips, seconds=4.0, seed=None, steps=100, inplace=False, out_root=ASSETS, log=print,
                      motion_fn=None, progress=None, on_retarget=None):
    """clips: [(ClipName, prompt)] or [{"name", "prompt", "seconds"?, "seed"?}] -> Kimodo BVHs in assets/<name>/clips/,
    retargeted into the character's GLB/FBX. motion_fn(prompts, seconds, out_stem, seed) may replace the in-process
    Kimodo call; on_retarget() is called before the Blender pass. Returns the folder."""
    name = safe_name(name)
    folder = os.path.join(out_root, name)
    blend = os.path.join(folder, name + ".blend")
    if not os.path.isfile(blend):
        raise RuntimeError(f"{blend} not found: rig the character first")
    clip_dir = os.path.join(folder, "clips")
    os.makedirs(clip_dir, exist_ok=True)
    meta = read_meta(folder)
    done = []
    for i, c in enumerate(clips):
        c = c if isinstance(c, dict) else {"name": c[0], "prompt": c[1]}
        cname = safe_name(c.get("name"), f"Clip{i + 1}")
        prompt = c.get("prompt") or ""
        prompts = [p.strip() for p in prompt.split("|") if p.strip()]
        stem = os.path.join(clip_dir, cname)
        clip_seconds = c.get("seconds") or seconds
        clip_seed = c.get("seed") if c.get("seed") is not None else ((seed + i) if seed is not None else None)
        t0 = time.time()
        if motion_fn is None:
            import motion
            motion.generate(prompts, clip_seconds, stem, clip_seed, steps, log=log, progress=progress)
        else:
            motion_fn(prompts, clip_seconds, stem, clip_seed)
        done.append((cname, stem + ".bvh"))
        entry = {"name": cname, "prompt": prompt, "seconds": clip_seconds, "seed": clip_seed, "steps": steps,
                 "bvh": stem + ".bvh", "created": time.time()}
        meta["clips"] = [c for c in meta.get("clips", []) if c.get("name") != cname] + [entry]
        write_meta(folder, meta)
        log(f"clip {cname} generated in {time.time() - t0:.0f}s")
    # retarget everything that exists on disk (earlier clips are rebuilt too, so the exports stay complete)
    all_clips = [(c["name"], c["bvh"]) for c in meta.get("clips", []) if os.path.isfile(c.get("bvh", ""))]
    args = [blend, folder, name]
    for cname, bvh in all_clips:
        args += ["--clip", f"{cname}={bvh}"]
    if inplace:
        args.append("--inplace")
    if on_retarget:
        on_retarget()
    blender("blender_animate.py", args, log)
    meta.update(animated=time.time(), inplace=inplace)
    write_meta(folder, meta)
    return folder


def parse_clips(items):
    clips = []
    for it in items or []:
        cname, sep, prompt = it.partition("=")
        if not sep:
            cname, prompt = f"Clip{len(clips) + 1}", it
        clips.append((cname.strip(), prompt.strip()))
    return clips


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rig", help="rig a mesh (GLB/FBX/OBJ) with Make-It-Animatable")
    r.add_argument("mesh")
    r.add_argument("--name", default=None)
    r.add_argument("--height", type=float, default=1.8, help="character height in metres (default 1.8)")
    r.add_argument("--no-fingers", action="store_true", help="hands only, no finger bones weights")
    r.add_argument("--keep-pose", action="store_true", help="do not move the mesh into the T-pose")
    a_ = sub.add_parser("anim", help="generate clips for a rigged character")
    a_.add_argument("name")
    f = sub.add_parser("full", help="rig + anim")
    f.add_argument("mesh")
    f.add_argument("--name", default=None)
    f.add_argument("--height", type=float, default=1.8)
    f.add_argument("--no-fingers", action="store_true")
    f.add_argument("--keep-pose", action="store_true")
    for p in (a_, f):
        p.add_argument("--clip", action="append", required=True, help='"Name=what the person does"; repeatable')
        p.add_argument("--seconds", type=float, default=4.0)
        p.add_argument("--seed", type=int, default=None)
        p.add_argument("--steps", type=int, default=100)
        p.add_argument("--inplace", action="store_true", help="drop the horizontal root motion")
    m = sub.add_parser("motion", help="text -> BVH only")
    m.add_argument("prompt")
    m.add_argument("--seconds", type=float, default=4.0)
    m.add_argument("--seed", type=int, default=None)
    m.add_argument("--steps", type=int, default=100)
    m.add_argument("--out", required=True)
    a = ap.parse_args()

    if a.cmd == "motion":
        import motion
        motion.generate(a.prompt.split("|"), a.seconds, a.out, a.seed, a.steps)
        return
    if a.cmd in ("rig", "full"):
        name = a.name or safe_name(os.path.splitext(os.path.basename(a.mesh))[0])
        folder = rig_character(a.mesh, name, a.height, a.no_fingers, a.keep_pose)
        print("character:", folder)
    if a.cmd in ("anim", "full"):
        name = a.name if a.cmd == "anim" else name
        folder = animate_character(name, parse_clips(a.clip), a.seconds, a.seed, a.steps, a.inplace)
        print("character:", folder)
        print("clips:", ", ".join(c["name"] for c in read_meta(folder).get("clips", [])))


if __name__ == "__main__":
    main()
