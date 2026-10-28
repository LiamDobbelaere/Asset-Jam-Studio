r"""Joins clips into one video with cross-fades (ffmpeg xfade), optional music, optional slow-motion/frame rate.

Usage:
    assemble.bat out.mp4 clip1.mp4 clip2.mp4 clip3.mp4 --transition 0.3 --audio sting.wav

Every clip is scaled to the first clip's size (or --width/--height) and resampled to --fps first so xfade accepts
them. --transition is the cross-fade length between consecutive clips (0 = hard cut); a list gives one per cut.
Used by intro.py and the studio.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

FFMPEG = os.environ.get("FFMPEG", shutil.which("ffmpeg") or r"C:\ffmpeg\bin\ffmpeg.exe")
FFPROBE = os.environ.get("FFPROBE", shutil.which("ffprobe") or os.path.join(os.path.dirname(FFMPEG), "ffprobe.exe"))


def probe(path):
    """(duration seconds, width, height, fps) of a video file."""
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate,duration",
                        "-show_entries", "format=duration", "-of", "json", path], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffprobe failed on " + path + ": " + r.stderr[-500:])
    d = json.loads(r.stdout)
    st = d["streams"][0]
    num, den = st["r_frame_rate"].split("/")
    dur = float(st.get("duration") or d["format"]["duration"])
    return dur, int(st["width"]), int(st["height"]), float(num) / float(den or 1)


def assemble(clips, dst, transitions=0.3, audio=None, width=None, height=None, fps=24, crf=16, audio_fade=1.0,
             transition_kind="fade"):
    """clips: list of mp4 paths. transitions: seconds, or a list with one entry per cut. Returns (dst, total seconds)."""
    if not clips:
        raise ValueError("no clips")
    info = [probe(c) for c in clips]
    width = width or info[0][1]
    height = height or info[0][2]
    n = len(clips)
    tr = list(transitions) if isinstance(transitions, (list, tuple)) else [transitions] * (n - 1)
    tr = [min(max(float(t or 0), 0.0), min(info[i][0], info[i + 1][0]) / 2) for i, t in enumerate(tr[: n - 1])]
    tr += [0.0] * (n - 1 - len(tr))
    cmd = [FFMPEG, "-y", "-loglevel", "error"]
    for c in clips:
        cmd += ["-i", c]
    if audio:
        cmd += ["-i", audio]
    parts = []
    for i in range(n):
        parts.append(f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
                     f"fps={fps},format=yuv420p,setsar=1,settb=AVTB,setpts=PTS-STARTPTS[v{i}]")
    cur, total = "v0", info[0][0]
    for i in range(1, n):
        t = tr[i - 1]
        if t > 0:
            offset = total - t
            parts.append(f"[{cur}][v{i}]xfade=transition={transition_kind}:duration={t:.3f}:offset={offset:.3f}[x{i}]")
            total = offset + info[i][0]
        else:
            parts.append(f"[{cur}][v{i}]concat=n=2:v=1:a=0[x{i}]")
            total += info[i][0]
        cur = f"x{i}"
    out_v = cur
    if audio:
        fade = max(0.0, min(audio_fade, total / 2))
        parts.append(f"[{n}:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,afade=t=out:st={max(0.0, total - fade):.3f}:d={fade:.3f}[a]")
    cmd += ["-filter_complex", ";".join(parts), "-map", f"[{out_v}]"]
    if audio:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-movflags", "+faststart", "-t", f"{total:.3f}", dst]
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + r.stderr[-2000:])
    return dst, round(total, 2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out")
    ap.add_argument("clips", nargs="+")
    ap.add_argument("--transition", default="0.3", help="seconds per cut, or a comma list (0 = hard cut)")
    ap.add_argument("--kind", default="fade", help="xfade transition: fade, dissolve, wipeleft, slideup, circleopen, ...")
    ap.add_argument("--audio", help="music / sting; cut to the video length with a fade-out")
    ap.add_argument("--audio-fade", type=float, default=1.0)
    ap.add_argument("--width", type=int)
    ap.add_argument("--height", type=int)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--crf", type=int, default=16)
    a = ap.parse_args()
    tr = [float(x) for x in a.transition.split(",")]
    tr = tr[0] if len(tr) == 1 else tr
    try:
        dst, total = assemble(a.clips, a.out, tr, a.audio, a.width, a.height, a.fps, a.crf, a.audio_fade, a.kind)
    except (RuntimeError, ValueError) as e:
        sys.exit(str(e))
    print(f"wrote {dst} ({total} s)")


if __name__ == "__main__":
    main()
