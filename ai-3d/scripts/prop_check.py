"""Build a prop_kit modelling script and show the result next to its reference picture: the one command Claude
runs while it models a prop for the studio's "prop" flow (see prop_brief.md).

    ComfyUI/.venv/Scripts/python.exe prop_check.py <Name>.py

Runs `blender -b --python <Name>.py` in the script's folder (the script exports <Name>.glb there with prop_kit),
then renders the GLB as clay from four sides and writes check.png: the reference picture (reference.png in the
same folder) followed by the four views. Prints the triangle count, the parts and the sheet's path, or the end of
Blender's error output.
"""
import os
import re
import subprocess
import sys

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER = os.environ.get("BLENDER", r"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe")


def main():
    script = os.path.abspath(sys.argv[1])
    folder = os.path.dirname(script)
    r = subprocess.run([BLENDER, "-b", "--python-exit-code", "1", "--python", script], cwd=folder,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = re.search(r"^RESULT (.+\.glb) tris=(\d+) parts=(.*)$", r.stdout, re.M)
    if r.returncode != 0 or not m:
        err = (r.stdout + r.stderr).strip().splitlines()
        tb = [i for i, l in enumerate(err) if l.startswith("Traceback")]
        print("BUILD FAILED\n" + "\n".join(err[tb[-1]:] if tb else err[-25:]))
        sys.exit(1)
    glb, tris, parts = m.group(1), int(m.group(2)), m.group(3)
    prefix = os.path.join(folder, "_view")
    v = subprocess.run([BLENDER, "-b", "--python", os.path.join(HERE, "prop_views.py"), "--", prefix, glb],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    views = next((l.split()[1:] for l in v.stdout.splitlines() if l.startswith("VIEWS ")), None)
    if not views:
        print("RENDER FAILED\n" + (v.stdout + v.stderr)[-2000:])
        sys.exit(1)
    S = 512
    sheet = Image.new("RGB", (S * 5, S + 28), (40, 40, 40))
    ref = os.path.join(folder, "reference.png")
    if os.path.isfile(ref):
        sheet.paste(Image.open(ref).convert("RGB").resize((S, S)), (0, 28))
    dr = ImageDraw.Draw(sheet)
    for i, (label, path) in enumerate(zip(("reference", "front, from the left", "front, from the right", "right side", "back"),
                                          [ref] + views)):
        if i:
            sheet.paste(Image.open(path).convert("RGB"), (S * i, 28))
        dr.text((S * i + 8, 8), label, fill=(230, 230, 230))
    out = os.path.join(folder, "check.png")
    sheet.save(out)
    for p in views:
        os.remove(p)
    print(f"OK {glb}\ntris={tris} parts={parts}\nsheet: {out}")


if __name__ == "__main__":
    main()
