r"""Logo reveal shot rendered from the real logo PNG (no AI): the characters pop up from below with an overshoot,
then the wordmark jumps out. Keeps the artwork and typography pixel-exact, which a video model cannot.

Usage:
    logo_reveal.bat assets\logo.png --out output\clips\Reveal.mp4 --seconds 3 --split 0.69

--split is the row (as a fraction of the logo height) where the picture ends and the wordmark starts; the two
parts are animated as separate layers. The background colour and a lead-in that fades up from --from-frame
(the last frame of the previous shot) are optional. Pillow + ffmpeg; used by intro.py and the studio.
"""
import argparse
import math
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageColor

FFMPEG = os.environ.get("FFMPEG", shutil.which("ffmpeg") or r"C:\ffmpeg\bin\ffmpeg.exe")


def ease_out_back(t, s=1.70158):
    t = min(max(t, 0.0), 1.0) - 1.0
    return t * t * ((s + 1) * t + s) + 1.0


def ease_out_cubic(t):
    t = min(max(t, 0.0), 1.0)
    return 1 - (1 - t) ** 3


def smoothstep(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def render_frames(logo, width, height, fps, seconds, split, background, from_frame=None, scale=0.8, pop=0.9,
                  text_start=0.45, text_dur=0.8, lead_in=0.5, hold_dark=0.0):
    """Yields RGB PIL frames. Timeline: [0, lead_in] fade from from_frame/background; characters pop in over `pop`
    seconds; the wordmark jumps out from text_start over text_dur; hold till the end."""
    im = Image.open(logo).convert("RGBA")
    lw, lh = im.size
    target_w = int(width * scale)
    s = min(target_w / lw, (height * 0.9) / lh)
    im = im.resize((max(1, int(lw * s)), max(1, int(lh * s))), Image.LANCZOS)
    lw, lh = im.size
    cut = int(lh * split)
    pic = im.crop((0, 0, lw, cut))
    txt = im.crop((0, cut, lw, lh))
    x0 = (width - lw) // 2
    y0 = (height - lh) // 2
    bg = Image.new("RGB", (width, height), ImageColor.getrgb(background))
    prev = None
    if from_frame and os.path.isfile(from_frame):
        prev = Image.open(from_frame).convert("RGB").resize((width, height), Image.LANCZOS)
    n = max(1, round(seconds * fps))
    for i in range(n):
        t = i / fps
        frame = bg.copy()
        if prev is not None and t < lead_in:
            frame = Image.blend(prev, bg, smoothstep(t / lead_in))
        # characters: rise from below the canvas with overshoot
        k = ease_out_back((t - lead_in * 0.3) / pop)
        if t >= lead_in * 0.3:
            off = int((1 - k) * (height - y0))
            frame.paste(pic, (x0, y0 + off), pic)
        # wordmark: scale 0 -> 1 with overshoot around its own centre, plus a little hop
        tt = (t - text_start - lead_in * 0.3) / text_dur
        if tt >= 0:
            sc = ease_out_back(tt, 2.4)
            sc = max(sc, 0.02)
            w2, h2 = max(1, int(txt.width * sc)), max(1, int(txt.height * sc))
            layer = txt.resize((w2, h2), Image.LANCZOS)
            hop = int(-abs(math.sin(min(tt, 1.0) * math.pi)) * height * 0.03)
            cx, cy = x0 + txt.width // 2, y0 + cut + txt.height // 2
            frame.paste(layer, (cx - w2 // 2, cy - h2 // 2 + hop), layer)
        yield frame


def write_video(frames, dst, fps, crf=16):
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="logo_reveal_")
    try:
        last = None
        for i, fr in enumerate(frames):
            fr.save(os.path.join(tmp, f"f_{i:05d}.png"))
            last = fr
        cmd = [FFMPEG, "-y", "-loglevel", "error", "-framerate", str(fps), "-i", os.path.join(tmp, "f_%05d.png"),
               "-vf", "format=yuv420p", "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-movflags", "+faststart", dst]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("ffmpeg failed: " + r.stderr[-1500:])
        stem = os.path.splitext(dst)[0]
        if last is not None:
            last.save(stem + "_last.png")
        shutil.copyfile(os.path.join(tmp, "f_00000.png"), stem + "_first.png")
        return dst, stem + "_last.png", stem + "_first.png"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def render(logo, dst, width=832, height=480, fps=24, seconds=3.0, split=0.69, background="#ffffff", from_frame=None,
           scale=0.8, pop=0.9, text_start=0.45, text_dur=0.8, lead_in=0.5):
    return write_video(render_frames(logo, width, height, fps, seconds, split, background, from_frame, scale, pop,
                                     text_start, text_dur, lead_in), dst, fps)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logo")
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=832)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--split", type=float, default=0.69, help="row where the wordmark starts, fraction of the logo height")
    ap.add_argument("--background", default="#ffffff")
    ap.add_argument("--from-frame", help="fade up from this picture (the previous shot's last frame)")
    ap.add_argument("--scale", type=float, default=0.8, help="logo width as a fraction of the frame width")
    ap.add_argument("--pop", type=float, default=0.9, help="seconds the characters take to pop in")
    ap.add_argument("--text-start", type=float, default=0.45)
    ap.add_argument("--text-dur", type=float, default=0.8)
    ap.add_argument("--lead-in", type=float, default=0.5)
    a = ap.parse_args()
    if not os.path.exists(FFMPEG):
        sys.exit(f"ffmpeg not found ({FFMPEG}); set FFMPEG")
    mp4, last, first = render(a.logo, a.out, a.width, a.height, a.fps, a.seconds, a.split, a.background, a.from_frame,
                              a.scale, a.pop, a.text_start, a.text_dur, a.lead_in)
    print("clip:", mp4, "\nlast frame:", last)


if __name__ == "__main__":
    main()
