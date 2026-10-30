"""Runs inside Blender (headless): renders the guides paint.py generates the views from.

    blender -b --python paint_guides.py -- model.glb camera.json outdir [low]

For each camera, guide_<i>.png (the model in its part colours, studio light + cavity, on light grey) and depth_<i>.png
(16-bit, 1 = nearest, 0.1 = farthest, black background), plus cams.json (camera matrices, field of view, depth range,
the prompt word for the view). Cameras: the reference picture's own, from the silhouette fit (camera.json, glTF frame),
then a ring at eye level and one from above, 30 degree FOV, all aimed at the model's centre; "low" adds two views from
below for props with undersides. Prints "GUIDES <views> <parts>".
"""
import json
import math
import os
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
glb, camf, out = argv[:3]
LOW = len(argv) > 3 and argv[3] == "low"
RES = 1024
RING = [(60, 10, "side view"), (120, 15, "back side view"), (180, 15, "back view"), (240, 15, "back side view"),
        (300, 10, "side view"), (30, 60, "top view")]
if LOW:
    RING += [(90, -25, "low view from below"), (270, -25, "low view from below")]
os.makedirs(out, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=glb)
meshes = [o for o in bpy.data.objects if o.type == "MESH"]
sc = bpy.context.scene
sc.render.resolution_x = sc.render.resolution_y = RES
sc.view_settings.view_transform = "Standard"
pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
lo = Vector([min(p[i] for p in pts) for i in range(3)])
hi = Vector([max(p[i] for p in pts) for i in range(3)])
c, r = (lo + hi) / 2, (hi - lo).length / 2

# the picture's camera direction (glTF frame -> Blender: (x, y, z) -> (x, -z, y)), the prop filling a 30 degree view
fit = json.load(open(camf))
d0 = (Vector((fit["pos"][0], -fit["pos"][2], fit["pos"][1])) - c).normalized()
dist = r / math.sin(math.radians(15)) * 1.05
views = [("front view", c + d0 * dist)]
az0 = math.atan2(d0.y, d0.x)
for daz, el, what in RING:
    a, e = az0 + math.radians(daz), math.radians(el)
    views.append((what, c + Vector((math.cos(a) * math.cos(e), math.sin(a) * math.cos(e), math.sin(e))) * dist))

cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
cam.data.angle = math.radians(30)
sc.collection.objects.link(cam)
sc.camera = cam
sc.world = bpy.data.worlds.new("w")


def aim(pos):
    cam.location = pos
    cam.rotation_euler = (c - pos).to_track_quat("-Z", "Y").to_euler()
    bpy.context.view_layer.update()


meta = []
sc.render.engine = "BLENDER_WORKBENCH"
sc.display.shading.light = "STUDIO"
sc.display.shading.color_type = "MATERIAL"
sc.display.shading.show_cavity = True
sc.world.color = (0.85, 0.85, 0.87)
for i, (what, pos) in enumerate(views):
    aim(pos)
    d = (pos - c).length
    meta.append({"what": what, "matrix": [list(row) for row in cam.matrix_world], "fov": 30, "res": RES,
                 "near": d - r, "far": d + r})
    sc.render.filepath = os.path.join(out, f"guide_{i}.png")
    bpy.ops.render.render(write_still=True)

# depth as emission of the view distance, mapped near -> 1, far -> 0.1 (the background stays black)
mat = bpy.data.materials.new("depth")
mat.use_nodes = True
nt = mat.node_tree
nt.nodes.clear()
cd, mr = nt.nodes.new("ShaderNodeCameraData"), nt.nodes.new("ShaderNodeMapRange")
em, mo = nt.nodes.new("ShaderNodeEmission"), nt.nodes.new("ShaderNodeOutputMaterial")
nt.links.new(cd.outputs["View Distance"], mr.inputs["Value"])
mr.inputs["To Min"].default_value, mr.inputs["To Max"].default_value = 1.0, 0.1
nt.links.new(mr.outputs["Result"], em.inputs["Color"])
nt.links.new(em.outputs[0], mo.inputs[0])
sc.view_layers[0].material_override = mat
sc.render.engine = "CYCLES"
sc.cycles.samples = 4
sc.cycles.device = "CPU"
sc.world.color = (0, 0, 0)
sc.render.image_settings.color_depth = "16"
for i, (what, pos) in enumerate(views):
    aim(pos)
    mr.inputs["From Min"].default_value, mr.inputs["From Max"].default_value = meta[i]["near"], meta[i]["far"]
    sc.render.filepath = os.path.join(out, f"depth_{i}.png")
    bpy.ops.render.render(write_still=True)
json.dump(meta, open(os.path.join(out, "cams.json"), "w"), indent=1)
parts = {m.name for o in meshes for m in o.data.materials if m}
print(f"GUIDES {len(views)} {len(parts)}")
