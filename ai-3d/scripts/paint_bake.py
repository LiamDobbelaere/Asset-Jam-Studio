"""Runs inside Blender (headless) with the StableGen add-on: bakes the painted scene paint_drive.py saved into one
diffuse on a fresh Smart UV unwrap, fills unseen texels, locks plain parts, exports one textured GLB. paint.py starts it.

    blender -b workdir/<name>_sg.blend --python paint_bake.py -- workdir name [Part1,Part2] [resolution]

Writes workdir/<name>.glb and workdir/<name>_diffuse.png and prints "PAINTBAKE {json}".
- StableGen's own "smart" unwrap left a third of a chair's faces at UV (0, 0), so they baked black; Blender's
  Smart UV Project on every face goes into the "BakeUV" layer its bake_texture() picks up instead.
- Faces no camera saw come out in the magenta fallback colour and get the neighbouring colours grown into them.
- Locked parts (material names) are plain: snow, a wooden post, a lid. Prompt bleed paints them in the colour of
  their neighbour (an orange tent rim on the snow, lantern glow on the post); they keep the painted shading but take
  one colour: the part's median painted colour, or the colour it was modelled with when that median strays far.
"""
import json
import math
import os
import sys

import bpy
import numpy as np

argv = sys.argv[sys.argv.index("--") + 1:]
workdir, name = argv[:2]
LOCK = [s for s in (argv[2] if len(argv) > 2 else "").split(",") if s]
RES = int(argv[3]) if len(argv) > 3 else 1024
from bl_ext.user_default.stablegen.texturing.rendering import bake_texture, prepare_baking  # noqa: E402

obj = [o for o in bpy.data.objects if o.type == "MESH"][0]
me = obj.data
PART_COL = json.loads(obj.get("paint_part_colors", "{}"))
# StableGen's bake resets the faces' material slots, so remember which part each face belongs to first
PART_IDX = [p.material_index for p in me.polygons]
PART_NAMES = [m.name.split(".")[0] for m in me.materials]


def uv_parts(w, h):
    """Part index of the face each texel belongs to on the active UV map, -1 off the islands."""
    me.calc_loop_triangles()
    uv = np.empty(len(me.loops) * 2, np.float32)
    me.uv_layers.active.data.foreach_get("uv", uv)
    uv = uv.reshape(-1, 2) * (w, h) - 0.5
    tri = np.empty(len(me.loop_triangles) * 3, np.int32)
    me.loop_triangles.foreach_get("loops", tri)
    parts = np.full((h, w), -1, np.int16)
    for t, part in zip(uv[tri.reshape(-1, 3)], [PART_IDX[lt.polygon_index] for lt in me.loop_triangles]):
        x0, y0 = np.floor(t.min(0)).astype(int)
        x1, y1 = np.ceil(t.max(0)).astype(int)
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, w - 1), min(y1, h - 1)
        (ax, ay), (bx, by), (cx, cy) = t
        d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if x1 < x0 or y1 < y0 or abs(d) < 1e-9:
            continue
        X, Y = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
        l1 = ((by - cy) * (X - cx) + (cx - bx) * (Y - cy)) / d
        l2 = ((cy - ay) * (X - cx) + (ax - cx) * (Y - cy)) / d
        e = 0.75 / max(abs(d) ** 0.5, 1)   # a little slack so thin islands get their edge texels
        parts[y0:y1 + 1, x0:x1 + 1][(l1 >= -e) & (l2 >= -e) & (1 - l1 - l2 >= -e)] = part
    return parts


def fill_fallback(px, parts):
    """Grow the neighbouring colours into the magenta texels no camera saw. Returns how many there were."""
    h, w = parts.shape
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    hole = (r > 0.55) & (b > 0.55) & (g < 0.45 * np.minimum(r, b))
    if not hole.any():
        return 0
    known = (parts >= 0) & ~hole
    col = px[..., :3].copy()
    col[~known] = 0
    for _ in range(400):
        if not (hole & ~known).any():
            break
        acc = np.zeros_like(col)
        n = np.zeros((h, w), np.float32)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
            k = np.roll(np.roll(known, dy, 0), dx, 1)
            acc += np.roll(np.roll(col, dy, 0), dx, 1) * k[..., None]
            n += k
        grow = hole & ~known & (n > 0)
        col[grow] = acc[grow] / n[grow][:, None]
        known |= grow
    left = hole & ~known   # whole islands nobody saw: the texture's median colour
    if left.any():
        col[left] = np.median(px[..., :3][(parts >= 0) & ~hole], axis=0)
    px[..., :3] = np.where(hole[..., None], col, px[..., :3])
    return int(((parts >= 0) & hole).sum())


def lock_parts(px, parts):
    locked = {}
    # dilate the part map a few texels so the bake margin around locked islands follows too
    for _ in range(4):
        free = parts < 0
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nb = np.roll(np.roll(parts, dy, 0), dx, 1)
            parts = np.where(free & (nb >= 0), nb, parts)
    lum = px[..., :3] @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    for i, key in enumerate(PART_NAMES):
        if key not in LOCK:
            continue
        sel = parts == i
        if not sel.any():
            continue
        g = np.median(px[..., :3][sel], axis=0)
        c = g
        if key in PART_COL:
            m = np.array(PART_COL[key], np.float32)
            m = np.where(m <= 0.0031308, m * 12.92, 1.055 * np.power(m, 1 / 2.4) - 0.055)   # linear -> sRGB like the PNG
            c = g if np.abs(g - m).max() < 0.25 else m
        # keep grain and shading, clamped so the shape of the bleed (a logo, a glow) does not survive
        rel = np.clip(lum[sel] / max(np.median(lum[sel]), 1e-3), 0.8, 1.12) ** 0.7
        px[..., :3][sel] = np.clip(c[None] * rel[:, None], 0, 1)
        locked[key] = [round(float(v), 3) for v in c]
    return locked


ctx = bpy.context
prepare_baking(ctx)
for coll in (me.vertices, me.edges, me.polygons):
    coll.foreach_set("hide", [False] * len(coll))
if "BakeUV" in me.uv_layers:
    me.uv_layers.remove(me.uv_layers["BakeUV"])
me.uv_layers.active = me.uv_layers.new(name="BakeUV")
ctx.view_layer.objects.active = obj
obj.select_set(True)
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.004)
bpy.ops.object.mode_set(mode="OBJECT")
if not bake_texture(ctx, obj, RES, output_dir=workdir):
    raise SystemExit("PAINTBAKE failed: StableGen's bake_texture returned False")
png = os.path.join(workdir, name + "_diffuse.png")
os.replace(os.path.join(workdir, obj.name + ".png"), png)

img = bpy.data.images.load(png)
w, h = img.size
px = np.empty(w * h * 4, np.float32)
img.pixels.foreach_get(px)
px = px.reshape(h, w, 4)
parts = uv_parts(w, h)
unseen = fill_fallback(px, parts)
locked = lock_parts(px, parts) if LOCK else {}
img.pixels.foreach_set(px.ravel())
img.update()
img.save()

uv = me.uv_layers.active
mat = bpy.data.materials.new(name)
mat.use_nodes = True
nt = mat.node_tree
t = nt.nodes.new("ShaderNodeTexImage")
t.image = img
u = nt.nodes.new("ShaderNodeUVMap")
u.uv_map = uv.name
nt.links.new(u.outputs["UV"], t.inputs["Vector"])
nt.links.new(t.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
nt.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.8
me.materials.clear()
me.materials.append(mat)
for layer in [layer for layer in me.uv_layers if layer.name != uv.name]:
    me.uv_layers.remove(layer)
for o in [o for o in bpy.data.objects if o.type != "MESH"]:
    bpy.data.objects.remove(o, do_unlink=True)
glb = os.path.join(workdir, name + ".glb")
bpy.ops.export_scene.gltf(filepath=glb)
me.calc_loop_triangles()
size = max(obj.dimensions)
print("PAINTBAKE " + json.dumps({"glb": glb, "diffuse": png, "unseen_texels": unseen, "locked": locked,
                                 "tris": len(me.loop_triangles), "size": round(size, 4)}))
