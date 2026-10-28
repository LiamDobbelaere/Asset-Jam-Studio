"""Runs inside Blender (headless): renders a generated FBX next to a reference prop from props.blend.

    blender -b <props.blend> --python compare_render.py -- <generated.fbx> <RefObjectName> <out.png>
"""
import math
import sys

import bpy
import mathutils

argv = sys.argv[sys.argv.index("--") + 1:]
fbx, ref_name, out = argv
ref = bpy.data.objects[ref_name]
for o in bpy.data.objects:
    o.hide_render = True
before = set(bpy.data.objects)
bpy.ops.import_scene.fbx(filepath=fbx)
gen = [o for o in bpy.data.objects if o not in before and o.type == "MESH"]
ref.hide_render = False
ref.parent = None
ref.matrix_world = mathutils.Matrix.Identity(4)
# scale generated to the reference height and place it beside it
gh = max(o.dimensions.z for o in gen)
for o in gen:
    o.hide_render = False
    o.scale = (ref.dimensions.z / gh,) * 3
bpy.context.view_layer.update()
gap = ref.dimensions.x * 1.3
for o in gen:
    o.location.x += gap
sc = bpy.context.scene
cam_d = bpy.data.cameras.new("cmp_cam")
cam = bpy.data.objects.new("cmp_cam", cam_d)
sc.collection.objects.link(cam)
sc.camera = cam
h = ref.dimensions.z
target = mathutils.Vector((gap / 2, 0, h / 2))
cam.location = target + mathutils.Vector((gap * 1.0, -gap * 4.2, h * 1.1))
cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
cam_d.lens = 35
world = bpy.data.worlds.new("cmp_world")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.92, 0.92, 0.92, 1)
sc.world = world
sun_d = bpy.data.lights.new("cmp_sun", "SUN")
sun_d.energy = 3
sun = bpy.data.objects.new("cmp_sun", sun_d)
sc.collection.objects.link(sun)
sun.rotation_euler = (math.radians(50), 0, math.radians(30))
sc.render.engine = "BLENDER_EEVEE"
sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = 1600, 900, 100
sc.render.filepath = out
bpy.ops.render.render(write_still=True)
ref.data.calc_loop_triangles()
print(f"COMPARE ref_tris={len(ref.data.loop_triangles)} gen_tris={sum(len(o.data.loop_triangles) for o in gen if o.data.calc_loop_triangles() is None)} out={out}")
