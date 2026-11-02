"""Build a style's reference library from a .blend of hand-made props: one normalised GLB, the diffuse textures,
stats and renders per prop, for Claude to look at while it models (prop_model.py), for evaluation (style_eval.py)
and as LoRA training data (style_lora_data.py).

    blender -b props.blend --python style_refs.py -- export <config.json>     one folder per prop: model.glb, tex_*.png, stats.json
    blender -b --python style_refs.py -- render <config.json> [Name ...]    renders from the exported GLBs

config.json (keep it in ai-3d/styles/private/: it names your files):
    {"blend": "...props.blend", "out": "...assets/style_refs/<style>", "front": "+X",
     "exclude": ["IsoFloorBase*", "Ext*", ...], "held_out": ["DeskChair", ...]}
"front" is the axis the props face in the .blend; the GLBs are turned so the front faces -Y and they stand on z = 0,
centred on x = y = 0, as prop_kit.py models. The exported mesh is the evaluated one (modifiers such as Smooth by
Angle applied) with only the UV map that renders: in hand-made files the active one is often a second, unpainted map.

Per prop (out/<Name>/): model.glb, tex_<n>.png, stats.json (tris, dims, loose parts, UV islands, materials, share of
smooth faces and sharp edges), and after "render": front_*.png (textured, Workbench studio light, the four views of
prop_check.py), flat_*.png (textured, flat light: the albedo), clay_*.png and wire_*.png (the same four views).
Prints "REFS <json>" with what it wrote.
"""
import fnmatch
import json
import math
import os
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector

VIEWS = (-35, 35, 90, 180)            # front-left, front-right, right side, back; as prop_check.py shows Claude
FRONT_ROT = {"-Y": 0.0, "+X": -90.0, "+Y": 180.0, "-X": 90.0}   # degrees about Z that turn that axis to -Y


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def wanted(name, cfg):
    return not any(fnmatch.fnmatchcase(name, pat) for pat in cfg.get("exclude", []))


def textured(o):
    return any(m and m.use_nodes and any(n.type == "TEX_IMAGE" and n.image for n in m.node_tree.nodes)
               for m in o.data.materials)


def uv_islands(bm, uvl):
    """Faces joined across an edge where both faces have the same UVs on it."""
    parent = list(range(len(bm.faces)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    bm.faces.index_update()
    for e in bm.edges:
        fs = e.link_faces
        if len(fs) != 2:
            continue
        uv = []
        for f in fs:
            uv.append({lp.vert.index: tuple(lp[uvl].uv) for lp in f.loops if lp.vert in e.verts})
        if all(abs(uv[0][k][j] - uv[1][k][j]) < 1e-5 for k in uv[0] if k in uv[1] for j in (0, 1)):
            a, b = find(fs[0].index), find(fs[1].index)
            parent[a] = b
    return len({find(i) for i in range(len(bm.faces))})


def export(cfg):
    out = cfg["out"]
    os.makedirs(out, exist_ok=True)
    rot = Matrix.Rotation(math.radians(FRONT_ROT[cfg.get("front", "-Y")]), 4, "Z")
    dg = bpy.context.evaluated_depsgraph_get()
    scene = bpy.context.scene   # headless: no window to switch scenes, so export the selection from this one
    done = []
    for o in [o for o in bpy.data.objects if o.type == "MESH"]:
        if not wanted(o.name, cfg) or not o.data.materials or not textured(o) or not o.visible_get():
            continue
        me = bpy.data.meshes.new_from_object(o.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
        me.transform(rot @ o.matrix_world)
        for uv in [u for u in me.uv_layers if not u.active_render]:
            me.uv_layers.remove(uv)
        if me.uv_layers:
            me.uv_layers.active = me.uv_layers[0]
        vs = [v.co for v in me.vertices]
        lo = Vector([min(v[i] for v in vs) for i in range(3)])
        hi = Vector([max(v[i] for v in vs) for i in range(3)])
        me.transform(Matrix.Translation(Vector((-(lo.x + hi.x) / 2, -(lo.y + hi.y) / 2, -lo.z))))
        ob = bpy.data.objects.new(o.name, me)
        scene.collection.objects.link(ob)
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.faces.index_update()
        parts = len(_components(bm))
        uvl = bm.loops.layers.uv.active
        islands = uv_islands(bm, uvl) if uvl else 0
        sharp = sum(1 for e in bm.edges if not e.smooth or len(e.link_faces) != 2)
        bm.free()
        me.calc_loop_triangles()
        d = os.path.join(out, o.name)
        os.makedirs(d, exist_ok=True)
        texs = []
        for m in me.materials:
            for n in (m.node_tree.nodes if m and m.use_nodes else []):
                if n.type == "TEX_IMAGE" and n.image and n.image.name not in [t[0] for t in texs]:
                    p = os.path.join(d, f"tex_{len(texs)}.png")
                    im = n.image.copy()
                    im.filepath_raw, im.file_format = p, "PNG"
                    try:
                        im.save()
                        texs.append((n.image.name, os.path.basename(p), list(n.image.size)))
                    except RuntimeError as err:
                        print(f"  {o.name}: could not save {n.image.name}: {err}")
                    bpy.data.images.remove(im)
        stats = {"name": o.name, "tris": len(me.loop_triangles), "dims": [round(hi[i] - lo[i], 4) for i in range(3)],
                 "parts": parts, "uv_islands": islands, "materials": [m.name for m in me.materials if m],
                 "smooth": round(sum(p.use_smooth for p in me.polygons) / max(len(me.polygons), 1), 3),
                 "sharp_edges": sharp, "edges": len(me.edges), "textures": texs,
                 "held_out": o.name in cfg.get("held_out", [])}
        with open(os.path.join(d, "stats.json"), "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=1)
        for x in scene.objects:
            x.select_set(False)
        ob.select_set(True)
        bpy.context.view_layer.objects.active = ob
        bpy.ops.export_scene.gltf(filepath=os.path.join(d, "model.glb"), use_selection=True, export_apply=False)
        bpy.data.objects.remove(ob)
        done.append(o.name)
        print(f"  {o.name}: {stats['tris']} tris, {parts} parts, {islands} UV islands", flush=True)
    print("REFS " + json.dumps({"exported": done}))


def _components(bm):
    seen, comps = set(), []
    for f in bm.faces:
        if f.index in seen:
            continue
        stack, comp = [f], []
        seen.add(f.index)
        while stack:
            g = stack.pop()
            comp.append(g)
            for e in g.edges:
                for h in e.link_faces:
                    if h.index not in seen:
                        seen.add(h.index)
                        stack.append(h)
        comps.append(comp)
    return comps


def render(cfg, names):
    out = cfg["out"]
    names = names or sorted(n for n in os.listdir(out) if os.path.isfile(os.path.join(out, n, "model.glb")))
    for name in names:
        d = os.path.join(out, name)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        sc = bpy.context.scene
        sc.render.engine = "BLENDER_WORKBENCH"
        sc.render.resolution_x = sc.render.resolution_y = 512
        sc.view_settings.view_transform = "Standard"
        sc.world = bpy.data.worlds.new("w")
        sc.world.color = (0.8, 0.8, 0.8)
        cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
        cam.data.angle = math.radians(30)
        sc.collection.objects.link(cam)
        sc.camera = cam
        bpy.ops.import_scene.gltf(filepath=os.path.join(d, "model.glb"))
        ms = [o for o in bpy.data.objects if o.type == "MESH"]
        pts = [o.matrix_world @ Vector(c) for o in ms for c in o.bound_box]
        lo = Vector([min(p[i] for p in pts) for i in range(3)])
        hi = Vector([max(p[i] for p in pts) for i in range(3)])
        c, r = (lo + hi) / 2, (hi - lo).length / 2
        clay = bpy.data.materials.new("clay")
        clay.diffuse_color = (0.72, 0.72, 0.72, 1)
        wire = bpy.data.materials.new("wire")
        wire.diffuse_color = (0.03, 0.03, 0.03, 1)
        wires = []
        for o in ms:
            w = o.copy()
            w.data = o.data.copy()
            w.data.materials.clear()
            w.data.materials.append(wire)
            mod = w.modifiers.new("w", "WIREFRAME")
            mod.thickness, mod.use_replace = (hi - lo).length * 0.0025, True
            sc.collection.objects.link(w)
            w.hide_render = True
            wires.append(w)

        def shoot(prefix, az):
            a, e = math.radians(az), math.radians(25)
            dv = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
            cam.location = c + dv * r / math.sin(math.radians(15)) * 1.05
            cam.rotation_euler = (-dv).to_track_quat("-Z", "Y").to_euler()
            sc.render.filepath = os.path.join(d, f"{prefix}_{az % 360:03d}.png")
            bpy.ops.render.render(write_still=True)

        sc.display.shading.color_type = "TEXTURE"
        for light, prefix in (("STUDIO", "front"), ("FLAT", "flat")):
            sc.display.shading.light = light
            for az in VIEWS:
                shoot(prefix, az)
        sc.display.shading.light = "STUDIO"
        sc.display.shading.color_type = "MATERIAL"
        sc.display.shading.show_cavity = True
        for o in ms:
            for i in range(len(o.data.materials)):
                o.data.materials[i] = clay
        for az in VIEWS:
            shoot("clay", az)
        for w in wires:
            w.hide_render = False
        for az in VIEWS:
            shoot("wire", az)
        print(f"  {name}: rendered", flush=True)
    print("REFS " + json.dumps({"rendered": names}))


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:]
    mode, cfg = argv[0], load_config(argv[1])
    if mode == "export":
        export(cfg)
    elif mode == "render":
        render(cfg, argv[2:])
    else:
        raise SystemExit("mode: export | render")
