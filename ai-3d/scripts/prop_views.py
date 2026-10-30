"""Runs inside Blender (headless): renders a GLB as clay (Workbench, studio light, cavity) from four sides.

    blender -b --python prop_views.py -- <out_prefix> <model.glb>

Writes <out_prefix>_<az>.png for az -35 (front, from the left), 35 (front, from the right), 90 (side) and 180
(back). The front is -Y, as prop_kit.py models it; the camera looks down 25 degrees.
"""
import math
import os
import sys

import bpy
import mathutils

argv = sys.argv[sys.argv.index("--") + 1:]
out, glb = argv[0], argv[1]
VIEWS = (-35, 35, 90, 180)

bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
sc.render.engine = "BLENDER_WORKBENCH"
sc.display.shading.light = "STUDIO"
sc.display.shading.color_type = "MATERIAL"   # the part colours: shows which parts are which
sc.display.shading.show_cavity = True
sc.render.resolution_x = sc.render.resolution_y = 512
sc.world = bpy.data.worlds.new("w")
sc.world.color = (0.8, 0.8, 0.8)
sc.view_settings.view_transform = "Standard"
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
cam.data.angle = math.radians(30)
sc.collection.objects.link(cam)
sc.camera = cam
bpy.ops.import_scene.gltf(filepath=glb)
ms = [o for o in bpy.data.objects if o.type == "MESH"]
pts = [o.matrix_world @ mathutils.Vector(c) for o in ms for c in o.bound_box]
lo = mathutils.Vector([min(p[i] for p in pts) for i in range(3)])
hi = mathutils.Vector([max(p[i] for p in pts) for i in range(3)])
c, r = (lo + hi) / 2, (hi - lo).length / 2
for az in VIEWS:
    a, e = math.radians(az), math.radians(25)
    d = mathutils.Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
    cam.location = c + d * r / math.sin(math.radians(15)) * 1.05
    cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
    sc.render.filepath = f"{out}_{az}.png"
    bpy.ops.render.render(write_still=True)
print("VIEWS " + " ".join(os.path.abspath(f"{out}_{az}.png") for az in VIEWS))
