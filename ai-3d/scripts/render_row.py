"""Runs inside Blender (headless): renders several GLBs side by side, textured (Eevee) and as clay (Workbench).

    blender -b --python render_row.py -- <out_prefix> <a.glb> [<b.glb> ...]

Writes <out_prefix>_tex.png and <out_prefix>_clay.png with the models in one row, left to right in the order
given, each scaled to the same height. Used to A/B post-processing settings on the same seed.
"""
import math
import os
import sys

import bpy
import mathutils

argv = sys.argv[sys.argv.index("--") + 1:]
out_prefix, files = argv[0], argv[1:]

bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
objs = []
for f in files:
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=f)
    new = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
    for o in new:
        o.select_set(True)
    bpy.context.view_layer.objects.active = new[0]
    if len(new) > 1:
        bpy.ops.object.join()
    o = bpy.context.view_layer.objects.active
    mw = o.matrix_world.copy()   # keep the parent node's transform (ComfyUI GLBs carry one)
    o.parent = None
    o.matrix_world = mw
    for e in [x for x in bpy.data.objects if x not in before and x is not o]:
        bpy.data.objects.remove(e)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    o.name = os.path.splitext(os.path.basename(f))[0]
    objs.append(o)

H = 1.0
gap = 1.25
for i, o in enumerate(objs):
    s = H / max(o.dimensions)
    o.scale = (s,) * 3
    bpy.context.view_layer.update()
    bbox = [o.matrix_world @ mathutils.Vector(c) for c in o.bound_box]
    lo = mathutils.Vector((min(v.x for v in bbox), min(v.y for v in bbox), min(v.z for v in bbox)))
    hi = mathutils.Vector((max(v.x for v in bbox), max(v.y for v in bbox), max(v.z for v in bbox)))
    o.location = mathutils.Vector((i * gap - (lo.x + hi.x) / 2, -(lo.y + hi.y) / 2, -lo.z))
bpy.context.view_layer.update()

n = len(objs)
width = (n - 1) * gap
cam_d = bpy.data.cameras.new("cam")
cam = bpy.data.objects.new("cam", cam_d)
sc.collection.objects.link(cam)
sc.camera = cam
target = mathutils.Vector((width / 2, 0, H * 0.45))
dist = max(width, 1.5) * 0.9 + 2.4
cam.location = target + mathutils.Vector((0.25 * H, -dist, dist * 0.5))
cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
cam_d.lens = 40 if n <= 3 else 32

world = bpy.data.worlds.new("w")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.85, 0.85, 0.85, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 0.6
sc.world = world
sun_d = bpy.data.lights.new("sun", "SUN")
sun_d.energy = 3.5
sun = bpy.data.objects.new("sun", sun_d)
sc.collection.objects.link(sun)
sun.rotation_euler = (math.radians(50), math.radians(10), math.radians(35))

sc.render.resolution_x = min(4096, 700 * n)
sc.render.resolution_y = 800
sc.render.resolution_percentage = 100
sc.render.film_transparent = False

sc.render.engine = "BLENDER_EEVEE"
sc.render.filepath = out_prefix + "_tex.png"
bpy.ops.render.render(write_still=True)

sc.render.engine = "BLENDER_WORKBENCH"
sh = sc.display.shading
sh.light = "STUDIO"
sh.color_type = "SINGLE"
sh.single_color = (0.75, 0.75, 0.75)
sh.show_cavity = True
sh.cavity_type = "BOTH"
sh.show_object_outline = True
sc.render.filepath = out_prefix + "_clay.png"
bpy.ops.render.render(write_still=True)

for o in objs:
    o.data.calc_loop_triangles()
print("RENDERROW " + "  ".join(f"{o.name}:{len(o.data.loop_triangles)}tris/{len(o.data.vertices)}v" for o in objs))
