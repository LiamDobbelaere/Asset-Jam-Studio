"""Modelling vocabulary for props written as Blender scripts (by hand or by an agent), the way a person box-models:
cube + bevel, cylinder, lathe/spin, tube along a path, inset/extrude via bmesh, mirror; then join + export one GLB.

    blender -b --python MyProp.py          (MyProp.py: sys.path.insert(0, <ai-3d/scripts>); import prop_kit as k)

Conventions that paint.py relies on:
- Blender units are metres, Z is up, the prop stands on z = 0 and its front faces -Y (glTF +Z), the side the
  reference picture shows. The camera fit only tries views from the front half.
- One material per part, named after the part (Wood, Snow, Lid ...), with roughly the part's colour. The names are
  what paint --lock takes, and the colour is the fallback when the painted colour of a locked part strays.
- Materials named Decal* are painted marks, not geometry (decal()): the paint guides show them, so every view paints
  the keys or the label in the same place, and paint_project.py drops their faces before the bake.
"""
import math
import os

import bmesh
import bpy
from mathutils import Vector


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def mat(name, rgb):
    """A part material: name it after the part, rgb in linear 0-1 (roughly the part's colour)."""
    m = bpy.data.materials.get(name)
    if m is None:
        m = bpy.data.materials.new(name)
        m.diffuse_color = (*rgb, 1)
        m.use_nodes = True
        m.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (*rgb, 1)
    return m


def _obj(name, bm, material):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(o)
    me.materials.append(material)
    return o


def box(name, size, loc, material, bevel=0.0, segs=1, rot=(0, 0, 0)):
    """Box of size (x, y, z) centred on loc; bevel rounds every edge (segs > 1 for a rounded profile)."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1)
    bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    if bevel:
        bmesh.ops.bevel(bm, geom=bm.edges[:], offset=bevel, segments=segs, profile=0.5, affect="EDGES", clamp_overlap=True)
    o = _obj(name, bm, material)
    o.rotation_euler = rot
    o.location = loc
    return o


def lathe(name, profile, material, segs=16, loc=(0, 0, 0), cap_top=True, cap_bottom=True, rot=(0, 0, 0)):
    """Spin a [(radius, height), ...] profile around Z: cups, lids, bases, posts, dish shells. Radius 0 closes a pole."""
    bm = bmesh.new()
    rings = []
    for r, z in profile:
        ring = [bm.verts.new((r * math.cos(2 * math.pi * i / segs), r * math.sin(2 * math.pi * i / segs), z))
                for i in range(segs)] if r > 1e-6 else [bm.verts.new((0, 0, z))]
        rings.append(ring)
    for a, b in zip(rings, rings[1:]):
        for i in range(segs):
            if len(a) == 1:
                bm.faces.new((a[0], b[i], b[(i + 1) % segs]))
            elif len(b) == 1:
                bm.faces.new((a[i], a[(i + 1) % segs], b[0]))
            else:
                bm.faces.new((a[i], a[(i + 1) % segs], b[(i + 1) % segs], b[i]))
    if cap_bottom and len(rings[0]) > 1:
        bm.faces.new(list(reversed(rings[0])))
    if cap_top and len(rings[-1]) > 1:
        bm.faces.new(rings[-1])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    o = _obj(name, bm, material)
    o.location = loc
    o.rotation_euler = rot
    return o


def cyl(name, r, h, loc, material, segs=12, rot=(0, 0, 0), bevel=0.0):
    """Cylinder centred on loc along its local Z."""
    prof = [(r - bevel, -h / 2), (r, -h / 2 + bevel), (r, h / 2 - bevel), (r - bevel, h / 2)] if bevel else [(r, -h / 2), (r, h / 2)]
    return lathe(name, prof, material, segs=segs, loc=loc, rot=rot)


def tube(name, points, radius, material, segs=6, closed=False):
    """A round bar along a poly-line (wire handles, frame tubes, struts, tent poles)."""
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = radius
    cu.bevel_resolution = max(0, segs // 2 - 2)
    cu.use_fill_caps = True
    sp = cu.splines.new("POLY")
    sp.points.add(len(points) - 1)
    for p, c in zip(sp.points, points):
        p.co = (*c, 1)
    sp.use_cyclic_u = closed
    o = bpy.data.objects.new(name, cu)
    bpy.context.scene.collection.objects.link(o)
    cu.materials.append(material)
    bpy.context.view_layer.objects.active = o
    o.select_set(True)
    bpy.ops.object.convert(target="MESH")
    o.select_set(False)
    return bpy.context.active_object


def smooth_curve(points, n=24, closed=False):
    """Catmull-Rom through control points, for bent wires and arcs (feed the result to tube)."""
    P = [Vector(p) for p in points]
    out = []
    m = len(P) if closed else len(P) - 1
    for i in range(m):
        p0 = P[i - 1] if (i > 0 or closed) else P[i]
        p1, p2 = P[i], P[(i + 1) % len(P)]
        p3 = P[(i + 2) % len(P)] if (i + 2 < len(P) or closed) else p2
        for k in range(n // m + 1):
            t = k / (n // m + 1)
            out.append(tuple(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                                    + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3)))
    if not closed:
        out.append(tuple(P[-1]))
    return out


FACES = {"front": (math.pi / 2, 0, 0), "back": (math.pi / 2, 0, math.pi),"left": (math.pi / 2, 0, -math.pi / 2),
         "right": (math.pi / 2, 0, math.pi / 2), "top": (0, 0, 0)}


def decal(name, size, loc, color, face="front", rot=None, shape="rect", segs=16):
    """A painted mark on a surface (keys, a button panel, a label, a logo, a drawer outline): a flat rect or disc of
    size (w, h) centred on loc, which lies ON the surface; it is lifted 1 mm off it. face: front (-Y), back, left (-X),
    right, top: size is (x, z) on front/back, (y, z) on left/right, (x, y) on top. rot: euler angles instead of face,
    for slanted surfaces (the mark's normal is its local +Z). color: rgb or a material named Decal*. Decals show in
    the clay check and the paint guides but are not part of the painted mesh and cost no triangles."""
    m = color if isinstance(color, bpy.types.Material) else mat("Decal" + name, color)
    if not m.name.startswith("Decal"):
        raise ValueError(f"decal material {m.name!r}: name it Decal<Something>")
    bm = bmesh.new()
    w, h = size
    if shape == "disc":
        vs = [bm.verts.new((w / 2 * math.cos(2 * math.pi * i / segs), h / 2 * math.sin(2 * math.pi * i / segs), 0))
              for i in range(segs)]
    else:
        vs = [bm.verts.new(p) for p in ((-w / 2, -h / 2, 0), (w / 2, -h / 2, 0), (w / 2, h / 2, 0), (-w / 2, h / 2, 0))]
    bm.faces.new(vs)
    bmesh.ops.translate(bm, vec=(0, 0, 0.001), verts=bm.verts)
    o = _obj(name, bm, m)
    o.rotation_euler = rot if rot is not None else FACES[face]
    o.location = loc
    return o


def edit(o, fn):
    """Run fn(bm) on an object's mesh (inset, extrude, move verts ...)."""
    bm = bmesh.new()
    bm.from_mesh(o.data)
    fn(bm)
    bm.to_mesh(o.data)
    bm.free()
    o.data.update()


def mirror_x(o):
    m = o.modifiers.new("mirror", "MIRROR")
    m.use_axis = (True, False, False)
    bpy.context.view_layer.objects.active = o
    bpy.ops.object.modifier_apply(modifier=m.name)


def export(name, out_dir, sharp_deg=40):
    """Join every mesh into one object, weld, mark edges sharper than sharp_deg as hard, write <out_dir>/<name>.glb."""
    obs = [o for o in bpy.data.objects if o.type == "MESH"]
    bpy.ops.object.select_all(action="DESELECT")
    for o in obs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = obs[0]
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    bpy.ops.object.join()
    o = bpy.context.active_object
    o.name = name
    bm = bmesh.new()
    bm.from_mesh(o.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    decals = {i for i, m in enumerate(o.data.materials) if m.name.startswith("Decal")}
    tris = sum(len(f.verts) - 2 for f in bm.faces if f.material_index not in decals)
    bm.to_mesh(o.data)
    bm.free()
    for p in o.data.polygons:
        p.use_smooth = True
    o.data.set_sharp_from_angle(angle=math.radians(sharp_deg))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name + ".glb")
    bpy.ops.export_scene.gltf(filepath=path)
    parts = [m.name for i, m in enumerate(o.data.materials) if i not in decals]
    print(f"RESULT {path} tris={tris} parts={','.join(parts)}"
          + (f" decals={','.join(o.data.materials[i].name[5:] for i in sorted(decals))}" if decals else ""))
    return path
