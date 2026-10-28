r"""One video clip with Wan 2.2 TI2V-5B: from text, from a start frame, or between a start and an end frame.

Usage:
    shot.bat "a black droplet falls into dark water, ripples spread" --seconds 3 --name Drop
    shot.bat "the ripples settle and the emblem appears in the reflection" --image start.png --end emblem.png --name Reflection
    shot.bat "..." --width 1280 --height 704 --seconds 5 --steps 20 --cfg 5 --seed 7

Requires the ComfyUI server (ai-3d/start_comfyui.bat) with the aivid_wan custom node installed (setup.ps1).
Queues workflows/wan_i2v_api.json, waits, encodes the saved PNG frames to <name>.mp4 with ffmpeg and keeps the
exact last frame as <name>_last.png so the next shot can start from it. Outputs go to output/clips/.

build_api() and finish_clip() are also used by intro.py and the studio web UI (../../studio).
"""
import argparse
import glob
import json
import os
import random
import shutil
import subprocess
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(ROOT)
AI3D = os.path.join(REPO, "ai-3d")
sys.path.insert(0, os.path.join(AI3D, "scripts"))
from generate import SERVER, get, post  # noqa: E402
from sprite import upload  # noqa: E402

COMFY_OUTPUT = os.path.join(AI3D, "ComfyUI", "output")
FFMPEG = os.environ.get("FFMPEG", shutil.which("ffmpeg") or r"C:\ffmpeg\bin\ffmpeg.exe")
FPS = 24
NEGATIVE = ("bright colors, overexposed, static, blurred details, subtitles, style, artwork, painting, picture, still, "
            "overall gray, worst quality, low quality, JPEG compression residue, ugly, incomplete, extra fingers, "
            "poorly drawn hands, poorly drawn faces, deformed, disfigured, malformed limbs, fused fingers, still picture, "
            "cluttered background, three legs, many people in the background, walking backwards")
SIZES = {"480p": (832, 480), "480p-portrait": (480, 832), "576p": (1024, 576), "720p": (1280, 704), "square": (640, 640)}


def frames_for(seconds, fps=FPS):
    """Wan 2.2 wants 4k+1 frames."""
    n = max(1, round(seconds * fps)) + 1      # seconds * fps intervals -> +1 frames
    return max(5, ((n - 1) // 4) * 4 + 1)


def load_png_bytes(path):
    """Any image -> PNG bytes, RGB (ComfyUI composites alpha on black otherwise)."""
    import io
    from PIL import Image
    im = Image.open(path)
    if im.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").getchannel("A"))
        im = bg
    else:
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def build_api(prompt, start=None, end=None, width=832, height=480, seconds=3.0, seed=None, steps=20, cfg=5.0,
              negative=NEGATIVE, shift=8.0, run_id=None, dump=False, upload_prefix="aivid"):
    """Patch the Wan API workflow; uploads the start/end pictures to ComfyUI (unless dump).

    Returns (api, info). info: seed, length, size, run_id (the ComfyUI output subfolder for this run), node ids.
    """
    if width % 32 or height % 32:
        raise ValueError("width and height must be multiples of 32")
    api = json.load(open(os.path.join(ROOT, "workflows", "wan_i2v_api.json"), encoding="utf-8"))
    if seed is None:
        seed = random.randint(0, 2**31)
    run_id = run_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    length = frames_for(seconds)
    api["5"]["inputs"]["text"] = prompt
    api["6"]["inputs"]["text"] = negative or NEGATIVE
    api["4"]["inputs"]["shift"] = shift
    lat = api["20"]["inputs"]
    lat.update(width=width, height=height, length=length)
    if start:
        name = f"{upload_prefix}_{run_id}_start.png"
        lat["start_image"] = ["10", 0]
        api["10"]["inputs"]["image"] = upload(name, load_png_bytes(start)) if not dump else name
    else:
        lat.pop("start_image", None)
        api.pop("10")
    if end:
        name = f"{upload_prefix}_{run_id}_end.png"
        lat["end_image"] = ["11", 0]
        api["11"]["inputs"]["image"] = upload(name, load_png_bytes(end)) if not dump else name
    else:
        lat.pop("end_image", None)
        api.pop("11")
        api["20"]["class_type"] = "Wan22ImageToVideoLatent"   # built-in node suffices; works without aivid_wan
    api["30"]["inputs"].update(seed=seed, steps=steps, cfg=cfg)
    api["40"]["inputs"]["filename_prefix"] = f"aivid/{run_id}/frames/f"
    api["41"]["inputs"]["batch_index"] = length - 1
    api["42"]["inputs"]["filename_prefix"] = f"aivid/{run_id}/last/f"
    info = {"seed": seed, "length": length, "seconds": round((length - 1) / FPS, 2), "width": width, "height": height,
            "steps": steps, "cfg": cfg, "run_id": run_id, "frames_node": "40", "last_node": "42",
            "start": bool(start), "end": bool(end), "prompt": prompt}
    return api, info


def encode_frames(frames, dst, fps=FPS, crf=16):
    """PNG frames (in order) -> H.264 mp4 at dst."""
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    lst = dst + ".frames.txt"
    with open(lst, "w", encoding="utf-8") as f:
        for p in frames:
            f.write("file '" + os.path.abspath(p).replace("\\", "/").replace("'", "'\\''") + "'\n")
            f.write(f"duration {1 / fps:.6f}\n")
        f.write("file '" + os.path.abspath(frames[-1]).replace("\\", "/").replace("'", "'\\''") + "'\n")   # concat demuxer needs the last file twice
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-vf", f"fps={fps},format=yuv420p",
           "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-movflags", "+faststart", dst]
    r = subprocess.run(cmd, capture_output=True, text=True)
    os.remove(lst)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + r.stderr[-1500:])
    return dst


def finish_clip(hist, info, dst_mp4, keep_frames=False, drop_first=False):
    """Take the frames of a finished ComfyUI run from its history, encode the mp4, copy the last frame next to it.

    drop_first: leave out frame 0 (a chained shot starts on the previous shot's last frame, which is already in
    the previous clip). Returns (mp4, last_png, first_png)."""
    frames, last = [], None
    for nid, out in hist.get("outputs", {}).items():
        for img in out.get("images", []) or []:
            p = os.path.join(COMFY_OUTPUT, img.get("subfolder", ""), img["filename"])
            if nid == info["frames_node"]:
                frames.append(p)
            elif nid == info["last_node"]:
                last = p
    if not frames:   # cached run: ComfyUI reports nothing, the files are still on disk
        frames = sorted(glob.glob(os.path.join(COMFY_OUTPUT, "aivid", info["run_id"], "frames", "*.png")))
        lasts = sorted(glob.glob(os.path.join(COMFY_OUTPUT, "aivid", info["run_id"], "last", "*.png")))
        last = lasts[-1] if lasts else None
    if not frames:
        raise RuntimeError("no frames in the ComfyUI output")
    frames.sort()
    encode_frames(frames[1:] if drop_first and len(frames) > 1 else frames, dst_mp4)
    stem = os.path.splitext(dst_mp4)[0]
    first_png, last_png = stem + "_first.png", stem + "_last.png"
    shutil.copyfile(frames[0], first_png)
    shutil.copyfile(last or frames[-1], last_png)
    if not keep_frames:
        shutil.rmtree(os.path.join(COMFY_OUTPUT, "aivid", info["run_id"]), ignore_errors=True)
    return dst_mp4, last_png, first_png


def wait(pid, timeout=3600, quiet=False):
    t0 = time.time()
    while time.time() - t0 < timeout:
        hist = get(f"/history/{pid}")
        if pid in hist:
            h = hist[pid]
            st = h.get("status", {})
            if st.get("status_str") == "error":
                for m in st.get("messages", []):
                    if m[0] == "execution_error":
                        raise RuntimeError(f"ComfyUI error in {m[1].get('node_type')}: {m[1].get('exception_message')}")
            return h
        if not quiet:
            print(f"  {time.time() - t0:.0f}s", end="\r", flush=True)
        time.sleep(2)
    raise TimeoutError("ComfyUI run timed out")


def run_shot(prompt, dst_mp4, **kw):
    """CLI/intro helper: build, queue, wait, finish. Returns (mp4, last_png, first_png, info)."""
    api, info = build_api(prompt, **kw)
    res = post("/prompt", {"prompt": api, "client_id": str(uuid.uuid4())})
    if "prompt_id" not in res:
        raise RuntimeError(json.dumps(res, indent=1)[:2000])
    print(f"queued {res['prompt_id']}  {info['width']}x{info['height']} {info['length']} frames ({info['seconds']} s) "
          f"seed={info['seed']} steps={info['steps']} cfg={info['cfg']} start={info['start']} end={info['end']}")
    t0 = time.time()
    hist = wait(res["prompt_id"])
    print(f"\nsampled in {time.time() - t0:.0f}s, encoding ...")
    mp4, last, first = finish_clip(hist, info, dst_mp4)
    info["wall"] = round(time.time() - t0, 1)
    return mp4, last, first, info


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt", help="what happens in the shot (subject, motion, camera, lighting)")
    ap.add_argument("--image", help="start frame (the clip begins exactly on this picture)")
    ap.add_argument("--end", help="end frame (the clip lands exactly on this picture; needs the aivid_wan node)")
    ap.add_argument("--size", choices=SIZES, default="480p", help="480p = 832x480 (default, fits 8 GB), 720p = 1280x704")
    ap.add_argument("--width", type=int, help="override: multiple of 32")
    ap.add_argument("--height", type=int, help="override: multiple of 32")
    ap.add_argument("--seconds", type=float, default=3.0, help="clip length (rounded to 4k+1 frames at 24 fps)")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--cfg", type=float, default=5.0)
    ap.add_argument("--shift", type=float, default=8.0)
    ap.add_argument("--negative", default=NEGATIVE)
    ap.add_argument("--name", default=None, help="output file name without extension")
    ap.add_argument("--out", default=os.path.join(ROOT, "output", "clips"))
    ap.add_argument("--keep-frames", action="store_true", help="leave the PNG frames in ComfyUI/output/aivid/<run>")
    ap.add_argument("--dump", help="write the patched API prompt JSON here and exit")
    a = ap.parse_args()

    w, h = SIZES[a.size]
    w, h = a.width or w, a.height or h
    if a.dump:
        api, info = build_api(a.prompt, a.image, a.end, w, h, a.seconds, a.seed, a.steps, a.cfg, a.negative, a.shift, dump=True)
        json.dump(api, open(a.dump, "w", encoding="utf-8"), indent=1)
        print("wrote", a.dump, info)
        return
    name = a.name or time.strftime("shot_%Y%m%d_%H%M%S")
    dst = os.path.join(a.out, name + ".mp4")
    try:
        mp4, last, first, info = run_shot(a.prompt, dst, start=a.image, end=a.end, width=w, height=h, seconds=a.seconds,
                                          seed=a.seed, steps=a.steps, cfg=a.cfg, negative=a.negative, shift=a.shift)
    except (RuntimeError, TimeoutError, ValueError) as e:
        sys.exit(str(e))
    print(f"done in {info['wall']}s\n  clip: {mp4}\n  last frame: {last}")


if __name__ == "__main__":
    main()
