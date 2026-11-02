"""LoRA training pairs from a style's reference library: for every prop that is not held out, the same cameras render
(a) a guide like paint_guides.py makes (the mesh in flat colour regions, flat light + cavity) and (b) the target, the
prop with its own hand-painted texture. An edit LoRA trained on guide -> target learns exactly the step paint.py asks
FLUX.2 klein for: "repaint this guide the way this artist paints".

    blender -b --python style_lora_data.py -- <config.json> <out_dir> [--res 512] [--views 10] [--picture]

The guide's colours come from the prop's own texture, reduced to a few flat colours (box blur, then k-means per prop),
standing in for the part colours and decals Claude gives a modelled prop. Writes <out_dir>/control/<Prop>_<k>.png,
<out_dir>/target/<Prop>_<k>.png and <out_dir>/target/<Prop>_<k>.txt (the caption: the instruction paint.py sends,
with the prop's description), and prints "LORADATA <pairs>".

--picture renders a text-to-image set instead, for a LoRA that draws the reference picture (prop_chain.py,
"picture_from": "lora"): the props textured under studio light from three-quarter views at several angles, on light
grey, captioned with the picture prompt prop_chain.PICTURE_CAPTION fills in. No guides.
"""
import json
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
cfg = json.load(open(argv[0], encoding="utf-8"))
OUT = os.path.abspath(argv[1])
RES = int(argv[argv.index("--res") + 1]) if "--res" in argv else 512
NVIEWS = int(argv[argv.index("--views") + 1]) if "--views" in argv else 10
CAMS = [(-35, 18, "front view"), (35, 18, "front view"), (0, 8, "front view"), (60, 10, "side view"),
        (120, 15, "back side view"), (180, 15, "back view"), (240, 15, "back side view"), (300, 10, "side view"),
        (30, 60, "top view"), (-20, 35, "front view")][:NVIEWS]
PICTURE = "--picture" in argv
PICTURE_CAMS = [(-40, 25), (40, 25), (-25, 18), (25, 18), (-55, 32), (55, 32)]
PICTURE_CAPTION = ("{desc}, a 3D game prop with a hand-painted texture, three-quarter view from slightly above, plain light "
                   "grey background")   # = prop_chain.PICTURE_CAPTION
CAPTION = ("Repaint image 1 as a finished hand-painted 3D game prop: {desc}, seen in a {what}. Exactly the same shape, "
           "outline, camera angle, framing and part colours as image 1. clean hand-painted game texture, flat colours "
           "with soft airbrushed shading, simple painted details, plain light grey background")


def posterize(img, k=5, blur=None):
    """The image reduced to k flat colours (box blur first, ~1/60 of its width, so painted seams, outlines and fine print
    merge into their region the way a modelled prop's guide only has flat part colours)."""
    w, h = img.size
    blur = blur or max(4, w // 60)
    a = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(a)
    a = a.reshape(h, w, 4)
    rgb = a[..., :3]
    c = np.cumsum(np.cumsum(np.pad(rgb, ((blur + 1, blur), (blur + 1, blur), (0, 0)), mode="edge"), 0), 1)
    n = 2 * blur + 1
    b = (c[n:, n:] - c[:-n, n:] - c[n:, :-n] + c[:-n, :-n]) / (n * n)
    flat = b.reshape(-1, 3)
    rng = np.random.default_rng(0)
    cent = flat[rng.choice(len(flat), k, replace=False)]
    for _ in range(12):
        lab = np.argmin(((flat[::7, None] - cent[None]) ** 2).sum(-1), 1)
        for j in range(k):
            sel = flat[::7][lab == j]
            if len(sel):
                cent[j] = sel.mean(0)
    lab = np.argmin(((flat[:, None] - cent[None]) ** 2).sum(-1), 1)
    a[..., :3] = cent[lab].reshape(h, w, 3)
    out = bpy.data.images.new(img.name + "_poster", w, h, alpha=True)
    out.pixels.foreach_set(a.ravel())
    return out


def main():
    index = {e["name"]: e for e in json.load(open(os.path.join(cfg["out"], "index.json"), encoding="utf-8"))}
    names = [n for n, e in sorted(index.items()) if not e["held_out"] and not e["hidden"]]
    if PICTURE:
        pictures(index, names)
        return
    for sub in ("control", "target"):
        os.makedirs(os.path.join(OUT, sub), exist_ok=True)
    pairs = 0
    for name in names:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        sc = bpy.context.scene
        sc.render.engine = "BLENDER_WORKBENCH"
        sc.render.resolution_x = sc.render.resolution_y = RES
        sc.view_settings.view_transform = "Standard"
        sc.world = bpy.data.worlds.new("w")
        sc.world.color = (0.85, 0.85, 0.87)
        sc.display.shading.light = "FLAT"
        cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
        cam.data.angle = math.radians(30)
        sc.collection.objects.link(cam)
        sc.camera = cam
        bpy.ops.import_scene.gltf(filepath=os.path.join(cfg["out"], name, "model.glb"))
        ms = [o for o in bpy.data.objects if o.type == "MESH"]
        pts = [o.matrix_world @ Vector(c) for o in ms for c in o.bound_box]
        lo = Vector([min(p[i] for p in pts) for i in range(3)])
        hi = Vector([max(p[i] for p in pts) for i in range(3)])
        c, r = (lo + hi) / 2, (hi - lo).length / 2
        texnodes = [n for o in ms for m in o.data.materials if m and m.use_nodes for n in m.node_tree.nodes
                    if n.type == "TEX_IMAGE" and n.image]
        originals = {n: n.image for n in texnodes}
        posters = {}
        for n in texnodes:
            if n.image.name not in posters:
                posters[n.image.name] = posterize(n.image)

        def shoot(kind, k, az, el):
            a, e = math.radians(az), math.radians(el)
            d = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
            cam.location = c + d * r / math.sin(math.radians(15)) * 1.05
            cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
            sc.render.filepath = os.path.join(OUT, kind, f"{name}_{k}.png")
            bpy.ops.render.render(write_still=True)

        sc.display.shading.color_type = "TEXTURE"
        for k, (az, el, what) in enumerate(CAMS):
            sc.display.shading.show_cavity = False
            for n in texnodes:
                n.image = originals[n]
            shoot("target", k, az, el)
            sc.display.shading.show_cavity = True   # the guides have cavity, as paint_guides.py renders them
            for n in texnodes:
                n.image = posters[originals[n].name]
            shoot("control", k, az, el)
            with open(os.path.join(OUT, "target", f"{name}_{k}.txt"), "w", encoding="utf-8") as f:
                f.write(CAPTION.format(desc=index[name]["desc"], what=what))
            pairs += 1
        print(f"  {name}: {len(CAMS)} pairs", flush=True)
    print(f"LORADATA {pairs}")


def pictures(index, names):
    os.makedirs(os.path.join(OUT, "target"), exist_ok=True)
    n = 0
    for name in names:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        sc = bpy.context.scene
        sc.render.engine = "BLENDER_WORKBENCH"
        sc.render.resolution_x = sc.render.resolution_y = RES
        sc.view_settings.view_transform = "Standard"
        sc.world = bpy.data.worlds.new("w")
        sc.world.color = (0.82, 0.82, 0.84)
        sc.display.shading.light = "STUDIO"
        sc.display.shading.color_type = "TEXTURE"
        cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
        cam.data.angle = math.radians(30)
        sc.collection.objects.link(cam)
        sc.camera = cam
        bpy.ops.import_scene.gltf(filepath=os.path.join(cfg["out"], name, "model.glb"))
        ms = [o for o in bpy.data.objects if o.type == "MESH"]
        pts = [o.matrix_world @ Vector(c) for o in ms for c in o.bound_box]
        lo = Vector([min(p[i] for p in pts) for i in range(3)])
        hi = Vector([max(p[i] for p in pts) for i in range(3)])
        c, r = (lo + hi) / 2, (hi - lo).length / 2
        for k, (az, el) in enumerate(PICTURE_CAMS[:NVIEWS]):
            a, e = math.radians(az), math.radians(el)
            d = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
            cam.location = c + d * r / math.sin(math.radians(15)) * 1.15
            cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
            sc.render.filepath = os.path.join(OUT, "target", f"{name}_{k}.png")
            bpy.ops.render.render(write_still=True)
            with open(os.path.join(OUT, "target", f"{name}_{k}.txt"), "w", encoding="utf-8") as f:
                f.write(PICTURE_CAPTION.format(desc=index[name]["desc"]))
            n += 1
        print(f"  {name}: {min(NVIEWS, len(PICTURE_CAMS))} pictures", flush=True)
    print(f"LORADATA {n}")


main()
