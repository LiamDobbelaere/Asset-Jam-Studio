"""Runs inside Blender (headless): projects the generated views onto the model and bakes one diffuse.

    blender -b --python paint_project.py -- model.glb guides_dir views_dir out_dir Name [Part1,Part2] [resolution]

Writes out_dir/<Name>.glb and <Name>_diffuse.png and prints "PAINTBAKE {json}".
- Every texel's position and normal are baked on a fresh Smart UV unwrap; each view (views_dir/view_<i>.png, cameras
  from guides_dir/cams.json) colours the texels it sees: the texel's depth must match the view's depth map (so hidden
  surfaces are skipped) and it must not sit on a silhouette or depth edge (the picture bleeds background there).
  Views are weighted by how squarely they face the surface (facing^6), the picture's own view 1.5x.
- Texels no view saw keep the model's part colour with baked ambient occlusion.
- Locked parts (material names) are plain: they keep the painted shading but one colour, the part's median painted
  colour, or its modelled colour when that median strays far (a neighbour's colour bled onto it).
"""
import json
import math
import os
import sys

import bpy
import numpy as np

argv = sys.argv[sys.argv.index("--") + 1:]
glb, gdir, vdir, out_dir, name = argv[:5]
LOCK = [s for s in (argv[5] if len(argv) > 5 else "").split(",") if s]
T = int(argv[6]) if len(argv) > 6 else 1024
SHARP, FRONT_BOOST, DEPTH_TOL, EDGE = 6.0, 1.5, 0.011, 0.03

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb)
meshes = [o for o in bpy.data.objects if o.type == "MESH"]
for o in meshes:
    o.select_set(True)
bpy.context.view_layer.objects.active = meshes[0]
if len(meshes) > 1:
    bpy.ops.object.join()
obj = bpy.context.view_layer.objects.active
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
me = obj.data
if not me.materials:
    me.materials.append(bpy.data.materials.new("Body"))
for m in me.materials:
    m.use_nodes = True
PART_NAMES = [m.name.split(".")[0] for m in me.materials]
PART_COL = []
for m in me.materials:
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    PART_COL.append(list(bsdf.inputs["Base Color"].default_value[:3]) if bsdf else [0.8, 0.8, 0.8])

uv = me.uv_layers.new(name="Bake")
me.uv_layers.active = uv
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.select_all(action="SELECT")
bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.006)
bpy.ops.object.mode_set(mode="OBJECT")
sc = bpy.context.scene
sc.render.engine = "CYCLES"
sc.cycles.device = "CPU"
sc.render.bake.margin = 6
sc.world = bpy.data.worlds.new("w")
vs = np.empty(len(me.vertices) * 3, np.float32)
me.vertices.foreach_get("co", vs)
vs = vs.reshape(-1, 3)
lo, ext = vs.min(0), np.maximum(vs.max(0) - vs.min(0), 1e-6)


def bake(kind, emit=None, samples=1, **kw):
    """Bake into a float image. emit: "pos" | "nrm" | "id" makes every material emit that instead of its shading."""
    sc.cycles.samples = samples
    img = bpy.data.images.new(kind + (emit or ""), T, T, float_buffer=True, alpha=True)
    img.generated_color = (0, 0, 0, 0)
    undo = []
    for idx, m in enumerate(me.materials):
        nt = m.node_tree
        tex, uvn = nt.nodes.new("ShaderNodeTexImage"), nt.nodes.new("ShaderNodeUVMap")
        tex.image, uvn.uv_map = img, "Bake"
        nt.links.new(uvn.outputs["UV"], tex.inputs["Vector"])
        nt.nodes.active = tex
        added = [tex, uvn]
        if emit:
            outn = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputMaterial")
            prev = outn.inputs[0].links[0].from_socket if outn.inputs[0].links else None
            em = nt.nodes.new("ShaderNodeEmission")
            added.append(em)
            if emit == "id":
                em.inputs["Color"].default_value = ((idx + 1) / 255.0, 0, 0, 1)
            else:
                geo, vm = nt.nodes.new("ShaderNodeNewGeometry"), nt.nodes.new("ShaderNodeVectorMath")
                vm.operation = "MULTIPLY_ADD"   # into 0..1: the bake clamps nothing, but keeps it simple to read
                if emit == "pos":
                    nt.links.new(geo.outputs["Position"], vm.inputs[0])
                    vm.inputs[1].default_value = tuple(1 / ext)
                    vm.inputs[2].default_value = tuple(-lo / ext)
                else:
                    nt.links.new(geo.outputs["Normal"], vm.inputs[0])
                    vm.inputs[1].default_value = vm.inputs[2].default_value = (0.5, 0.5, 0.5)
                nt.links.new(vm.outputs[0], em.inputs["Color"])
                added += [geo, vm]
            nt.links.new(em.outputs[0], outn.inputs[0])
            undo.append((nt, outn, prev))
        m["_bake_nodes"] = [n.name for n in added]
    bpy.ops.object.bake(type=kind, uv_layer="Bake", **kw)
    px = np.empty(T * T * 4, np.float32)
    img.pixels.foreach_get(px)
    for nt, outn, prev in undo:
        if prev is not None:
            nt.links.new(prev, outn.inputs[0])
    for m in me.materials:
        for n in m["_bake_nodes"]:
            m.node_tree.nodes.remove(m.node_tree.nodes[n])
    bpy.data.images.remove(img)
    return px.reshape(T, T, 4)


def srgb(v):
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * np.power(np.clip(v, 0, None), 1 / 2.4) - 0.055)


def linear(v):
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


P = bake("EMIT", "pos")
valid = P[..., 3] > 0.5
pos = lo + P[..., :3] * ext
nrm = bake("EMIT", "nrm")[..., :3] * 2 - 1
nrm /= np.maximum(np.linalg.norm(nrm, axis=-1, keepdims=True), 1e-6)
part = np.where(valid, np.rint(bake("EMIT", "id")[..., 0] * 255).astype(np.int32) - 1, -1)
ao = bake("AO", samples=32)[..., :1]
flat = srgb(bake("DIFFUSE", pass_filter={"COLOR"})[..., :3] * (0.55 + 0.45 * ao))


def load(path):
    im = bpy.data.images.load(path)
    im.colorspace_settings.name = "Non-Color"   # the stored values, no conversion
    w, h = im.size
    a = np.empty(w * h * 4, np.float32)
    im.pixels.foreach_get(a)
    bpy.data.images.remove(im)
    return a.reshape(h, w, 4)[::-1]               # row 0 = top of the picture


cams = json.load(open(os.path.join(gdir, "cams.json")))
acc = np.zeros((T, T, 3), np.float32)
wsum = np.zeros((T, T), np.float32)
for i, c in enumerate(cams):
    vp = os.path.join(vdir, f"view_{i}.png")
    if not os.path.isfile(vp):
        continue
    view = load(vp)[..., :3]
    dep = linear(load(os.path.join(gdir, f"depth_{i}.png"))[..., 0])   # saved through the sRGB view transform
    R = view.shape[0]
    M = np.array(c["matrix"])
    Minv = np.linalg.inv(M)
    campos = M[:3, 3]
    q = pos @ Minv[:3, :3].T + Minv[:3, 3]       # camera space, looking down -Z
    z = np.maximum(-q[..., 2], 1e-6)
    t = math.tan(math.radians(c["fov"]) / 2)
    px = ((q[..., 0] / z / t + 1) / 2 * R).astype(np.int32)
    py = ((1 - q[..., 1] / z / t) / 2 * R).astype(np.int32)
    inside = valid & (-q[..., 2] > 0) & (px >= 3) & (px < R - 3) & (py >= 3) & (py < R - 3)
    px, py = np.clip(px, 0, R - 1), np.clip(py, 0, R - 1)
    d_img = dep[py, px]
    d_tex = 1 - 0.9 * (np.linalg.norm(pos - campos, axis=-1) - c["near"]) / (c["far"] - c["near"])
    vis = inside & (d_img > 0.05) & (np.abs(d_img - d_tex) < DEPTH_TOL)
    for dy, dx in ((-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, 2), (-2, 2), (2, -2)):
        vis &= np.abs(dep[np.clip(py + dy, 0, R - 1), np.clip(px + dx, 0, R - 1)] - d_img) < EDGE
    to_cam = campos - pos
    to_cam /= np.maximum(np.linalg.norm(to_cam, axis=-1, keepdims=True), 1e-6)
    w = np.where(vis, np.clip(np.sum(nrm * to_cam, axis=-1), 0, 1) ** SHARP, 0) * (FRONT_BOOST if i == 0 else 1.0)
    acc += view[py, px] * w[..., None]
    wsum += w

col = np.where((wsum > 1e-4)[..., None], acc / np.maximum(wsum, 1e-6)[..., None], flat)
fade = np.clip(wsum / 0.05, 0, 1)[..., None]   # ease into the part colour where the views barely reach
col = col * fade + flat * (1 - fade)
seen = float((wsum[valid] > 1e-4).mean())

locked = {}
lum = col @ np.array([0.2126, 0.7152, 0.0722], np.float32)
for idx, key in enumerate(PART_NAMES):
    if key not in LOCK:
        continue
    sel = part == idx
    if not sel.any():
        continue
    g = np.median(col[sel], axis=0)
    m = srgb(np.array(PART_COL[idx], np.float32))
    target = g if np.abs(g - m).max() < 0.25 else m
    rel = np.clip(lum[sel] / max(float(np.median(lum[sel])), 1e-3), 0.8, 1.12) ** 0.7   # shading, not the bleed's shape
    col[sel] = np.clip(target[None] * rel[:, None], 0, 1)
    locked[key] = [round(float(v), 3) for v in target]

os.makedirs(out_dir, exist_ok=True)
png = os.path.join(out_dir, name + "_diffuse.png")
outimg = bpy.data.images.new(name + "_diffuse", T, T)
o = np.ones((T, T, 4), np.float32)
o[..., :3] = np.clip(col, 0, 1)
outimg.pixels.foreach_set(o.ravel())   # same row order as the baked texel arrays
outimg.filepath_raw, outimg.file_format = png, "PNG"
outimg.save()
img = bpy.data.images.load(png)
mat = bpy.data.materials.new(name)
mat.use_nodes = True
nt = mat.node_tree
tex = nt.nodes.new("ShaderNodeTexImage")
tex.image = img
nt.links.new(tex.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
nt.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.8
me.materials.clear()
me.materials.append(mat)
for layer in [layer for layer in me.uv_layers if layer.name != "Bake"]:
    me.uv_layers.remove(layer)
obj.name = name
out_glb = os.path.join(out_dir, name + ".glb")
bpy.ops.export_scene.gltf(filepath=out_glb)
me.calc_loop_triangles()
print("PAINTBAKE " + json.dumps({"glb": out_glb, "diffuse": png, "seen": round(seen, 3), "locked": locked,
                                 "missing_locks": [k for k in LOCK if k not in locked], "parts": PART_NAMES,
                                 "tris": len(me.loop_triangles), "size": round(float(ext.max()), 4)}))
