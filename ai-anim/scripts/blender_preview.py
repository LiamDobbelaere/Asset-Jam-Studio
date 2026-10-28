"""Runs inside Blender (headless). Renders a GLB (optionally one frame of one of its animations) to a PNG:

    blender -b --python blender_preview.py -- <model.glb> <out.png> [--clip NAME] [--frame N] [--size 512]
                                               [--views front,side,quarter] [--bones]

Used by the ai-anim CLI / tests to eyeball a rig or a retargeted clip without opening Blender.
"""
import math
import os
import sys

import bpy
import mathutils

argv = sys.argv[sys.argv.index("--") + 1:]
src, out = os.path.abspath(argv[0]), os.path.abspath(argv[1])


def opt(flag, default=None):
    return argv[argv.index(flag) + 1] if flag in argv else default


clip, frame, size = opt("--clip"), int(opt("--frame", 0)), int(opt("--size", 512))
rest = "--rest" in argv        # show the rest pose even when the GLB has clips (the importer activates the first one)
views = opt("--views", "front,quarter").split(",")
show_bones = "--bones" in argv

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.fps = int(opt("--fps", 30))     # the glTF importer maps animation time to frames at the scene fps
bpy.ops.import_scene.gltf(filepath=src)
arm = next((o for o in bpy.data.objects if o.type == "ARMATURE"), None)
if rest and arm is not None:
    arm.animation_data_clear()
    for pb in arm.pose.bones:
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.location = (0, 0, 0)
if clip and arm is not None:
    act = bpy.data.actions.get(clip) or next((a for a in bpy.data.actions if clip.lower() in a.name.lower()), None)
    if act is None:
        raise SystemExit(f"clip {clip!r} not found; actions: {[a.name for a in bpy.data.actions]}")
    arm.animation_data_create()
    arm.animation_data.action = act
    try:
        arm.animation_data.action_slot = act.slots[0]
    except Exception:
        pass
    scene.frame_set(frame)
if show_bones and arm is not None:
    arm.show_in_front = True
    arm.data.display_type = "STICK"

# bounds: the posed bones (plus a margin for hats and the like), or the mesh when there is no armature
bpy.context.view_layer.update()
pts = []
if arm is not None:
    for pb in arm.pose.bones:
        pts += [arm.matrix_world @ pb.head, arm.matrix_world @ pb.tail]
else:
    for o in bpy.data.objects:
        if o.type == "MESH":
            pts += [o.matrix_world @ v.co for v in o.data.vertices]
lo = mathutils.Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
hi = mathutils.Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
center, extent = (lo + hi) / 2, max(hi - lo) * (1.3 if arm is not None else 1.0)

scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.light = "STUDIO"
scene.display.shading.color_type = "TEXTURE"
scene.display.shading.show_object_outline = True
scene.render.resolution_x = scene.render.resolution_y = size
scene.render.film_transparent = False
scene.world = bpy.data.worlds.new("W")
scene.world.color = (0.18, 0.2, 0.26)
cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
scene.collection.objects.link(cam)
scene.camera = cam
cam.data.type = "ORTHO"
cam.data.ortho_scale = extent * 1.15

if show_bones and arm is not None:
    scene.display.shading.show_xray = True
    scene.display.shading.xray_alpha = 0.5

frames = []
for i, v in enumerate(views):
    ang = {"front": 0.0, "side": math.pi / 2, "quarter": math.pi / 4, "back": math.pi}.get(v, 0.0)
    # character faces -Y: the front camera sits at -Y looking towards +Y
    d = extent * 3
    cam.location = center + mathutils.Vector((-math.sin(ang) * d, -math.cos(ang) * d, 0))
    cam.rotation_euler = (math.pi / 2, 0, -ang)
    scene.render.filepath = out if len(views) == 1 else out[:-4] + f"_{v}.png"
    bpy.ops.render.render(write_still=True)
    frames.append(scene.render.filepath)

if len(views) > 1:   # stitch side by side
    imgs = [bpy.data.images.load(f) for f in frames]
    w, h = imgs[0].size
    sheet = bpy.data.images.new("sheet", w * len(imgs), h)
    px = [0.0] * (w * len(imgs) * h * 4)
    for k, im in enumerate(imgs):
        src_px = list(im.pixels)
        for y in range(h):
            row = src_px[y * w * 4:(y + 1) * w * 4]
            start = (y * w * len(imgs) + k * w) * 4
            px[start:start + w * 4] = row
    sheet.pixels = px
    sheet.filepath_raw = out
    sheet.file_format = "PNG"
    sheet.save()
    for f in frames:
        os.remove(f)
print(f"PREVIEW {out} views={views} frame={frame} clip={clip}")
