"""Runs inside Blender (headless): projects the generated views onto the model and bakes one diffuse.

    blender -b --python paint_project.py -- model.glb guides_dir views_dir out_dir Name [Part1,Part2] [resolution] [finish]

Writes out_dir/<Name>.glb and <Name>_diffuse.png and prints "PAINTBAKE {json}".
- Every texel's position and normal are baked on a fresh Smart UV unwrap; each view (views_dir/view_<i>.png, cameras
  from guides_dir/cams.json) colours the texels it sees: the texel's depth must match the view's depth map (so hidden
  surfaces are skipped) and it must not sit on a silhouette or depth edge (the picture bleeds background there).
  Views are weighted by how squarely they face the surface (facing^6), the picture's own view 1.5x.
- Texels no view saw keep the model's part colour with baked ambient occlusion.
- Locked parts (material names) are plain: they keep the painted shading but one colour, the part's median painted
  colour, or its modelled colour when that median strays far (a neighbour's colour bled onto it).
- Faces with a Decal* material (prop_kit.decal: painted marks) are dropped first: the views painted them onto the
  surface underneath.
- finish (JSON, from a style guide, see prop_style.py) restyles the bake: "tone" (0-1) scales each part's painted
  brightness towards its modelled colour's (SDXL and the guides' light drift darker), "flatten" pulls each part's small colour
  differences (photo texture, view-to-view shading) to the part's median and keeps the big ones (labels, keys),
  "shade" puts back soft painted shading from the baked AO and the height, "ink" draws dark lines of that half-width
  in texels along the hard edges (sharper than "ink_angle" degrees, part borders, open edges) and round the decals,
  "ink_dark" times the local colour.
"""
import json
import math
import os
import sys

import bmesh
import bpy
import numpy as np

argv = sys.argv[sys.argv.index("--") + 1:]
glb, gdir, vdir, out_dir, name = argv[:5]
LOCK = [s for s in (argv[5] if len(argv) > 5 else "").split(",") if s]
T = int(argv[6]) if len(argv) > 6 else 1024
FINISH = json.loads(argv[7]) if len(argv) > 7 and argv[7] else {}
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
decal_idx = {i for i, m in enumerate(me.materials) if m and m.name.startswith("Decal")}
decal_edges = []   # the decals' outlines, for the ink
if decal_idx:   # painted marks (prop_kit.decal): in the guides and the views, not in the painted mesh
    bm = bmesh.new()
    bm.from_mesh(me)
    decal_edges = [(tuple(e.verts[0].co), tuple(e.verts[1].co)) for e in bm.edges
                   if e.is_boundary and e.link_faces[0].material_index in decal_idx]
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.material_index in decal_idx], context="FACES")
    bm.to_mesh(me)
    bm.free()
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
bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.012 if FINISH.get("ink") else 0.006)
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



def box_blur(a, r, mask):
    """Mean over a (2r+1)^2 box of the texels in mask (normalised, so empty texels do not darken island borders)."""
    def blur1(x, axis):
        c = np.cumsum(np.pad(x, [(r + 1, r) if i == axis else (0, 0) for i in range(x.ndim)]), axis=axis)
        return np.take(c, range(2 * r + 1, c.shape[axis]), axis=axis) - np.take(c, range(0, c.shape[axis] - 2 * r - 1), axis=axis)
    m = mask.astype(np.float32)
    num = blur1(blur1(a * (m[..., None] if a.ndim == 3 else m), 0), 1)
    den = blur1(blur1(m, 0), 1)
    return num / np.maximum(den if a.ndim == 2 else den[..., None], 1e-6)


def ink_mask(angle, width):
    """1 on the dark lines along the hard edges, drawn on the Bake UVs (each face's side of the edge), antialiased."""
    bm = bmesh.new()
    bm.from_mesh(me)
    uvl = bm.loops.layers.uv["Bake"]
    lim = math.radians(angle)
    ink = np.zeros((T, T), np.float32)
    yy, xx = np.mgrid[0:T, 0:T]
    for e in bm.edges:
        fs = e.link_faces
        if len(fs) == 2 and fs[0].material_index == fs[1].material_index and e.calc_face_angle(0) < lim:
            continue
        for f in fs:
            uv = [lp[uvl].uv for lp in f.loops if lp.vert in e.verts]
            if len(uv) != 2:
                continue
            a, b = np.array(uv[0]) * T - 0.5, np.array(uv[1]) * T - 0.5   # (col, row) of the texel centres
            x0, x1 = int(max(min(a[0], b[0]) - width - 2, 0)), int(min(max(a[0], b[0]) + width + 3, T))
            y0, y1 = int(max(min(a[1], b[1]) - width - 2, 0)), int(min(max(a[1], b[1]) + width + 3, T))
            if x0 >= x1 or y0 >= y1:
                continue
            px, py = xx[y0:y1, x0:x1] - a[0], yy[y0:y1, x0:x1] - a[1]
            d = b - a
            t = np.clip((px * d[0] + py * d[1]) / max(float(d @ d), 1e-9), 0, 1)
            dist = np.hypot(px - t * d[0], py - t * d[1])
            ink[y0:y1, x0:x1] = np.maximum(ink[y0:y1, x0:x1], np.clip(width + 0.5 - dist, 0, 1))
    bm.free()
    if decal_edges:   # round the painted marks: texels within the line width of a decal's outline, in 3D
        area = sum(p.area for p in me.polygons)
        w = width * math.sqrt(area / max(int(valid.sum()), 1))   # the line's half-width in metres
        idx = np.flatnonzero(valid)
        q = pos.reshape(-1, 3)[idx]
        flat_ink = ink.reshape(-1)
        for a, b in decal_edges:
            a, b = np.array(a, np.float32), np.array(b, np.float32)
            near = np.all((q > np.minimum(a, b) - 2 * w) & (q < np.maximum(a, b) + 2 * w), axis=1)
            if not near.any():
                continue
            p, d = q[near] - a, b - a
            t = np.clip(p @ d / max(float(d @ d), 1e-12), 0, 1)
            dist = np.linalg.norm(p - t[:, None] * d, axis=1) / w
            j = idx[near]
            flat_ink[j] = np.maximum(flat_ink[j], np.clip(1.5 - dist, 0, 1))
    return ink


if FINISH.get("tone"):
    lum_w = np.array([0.2126, 0.7152, 0.0722], np.float32)
    for idx in range(len(PART_NAMES)):
        sel = valid & (part == idx)
        if not sel.any() or idx in decal_idx:
            continue
        have = float(np.median(col[sel] @ lum_w))
        want = float(srgb(np.array(PART_COL[idx], np.float32)) @ lum_w)
        ratio = np.clip(want / max(have, 1e-3), 0.5, 2.5) ** FINISH["tone"]
        col[sel] = np.clip(col[sel] * ratio, 0, 1)
if FINISH.get("flatten"):
    for idx in range(len(PART_NAMES)):
        sel = valid & (part == idx)
        if not sel.any():
            continue
        g = np.median(col[sel], axis=0)
        d = col[sel] - g
        mag = np.linalg.norm(d, axis=-1, keepdims=True)
        keep = np.clip((mag - 0.06) / 0.19, 0, 1) ** 2 * (3 - 2 * np.clip((mag - 0.06) / 0.19, 0, 1))   # smoothstep 0.06..0.25
        col[sel] = g + d * (1 - FINISH["flatten"] * (1 - keep))
if FINISH.get("shade"):
    soft_ao = box_blur(ao[..., 0], 4, valid)
    height = np.clip((pos[..., 2] - lo[2]) / ext[2], 0, 1)
    s = (0.62 + 0.38 * soft_ao) * (0.88 + 0.12 * height) * (1 + 0.06 * nrm[..., 2])
    col = col * (1 - FINISH["shade"] + FINISH["shade"] * s[..., None])
if FINISH.get("ink"):
    k = ink_mask(FINISH.get("ink_angle", 40), FINISH["ink"])[..., None] * valid[..., None]
    col = col * (1 - k) + col * FINISH.get("ink_dark", 0.4) * k

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
