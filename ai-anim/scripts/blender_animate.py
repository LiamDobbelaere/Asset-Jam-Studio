"""Runs inside Blender (headless). Retargets BVH motion clips onto a rigged character and exports them:

    blender -b --python blender_animate.py -- <Rig.blend> <out_dir> <Name> --clip Idle=path/idle.bvh
                                               [--clip Walk=path/walk.bvh ...] [--fps 30] [--inplace]
                                               [--no-fbx] [--keep-existing]

<Rig.blend> is what blender_rig.py saved (a "mixamorig:" armature + skinned mesh in Mixamo T-pose).
Each BVH is a Kimodo SOMA-77 clip exported with --bvh_standard_tpose (T-pose rest, centimetres, Y-up).
Retargeting copies each source bone's world-space rotation change (pose x rest^-1) onto the mapped target
bone, so it only needs both rests to be T-poses; the hips translation is scaled by the leg-length ratio.
--inplace drops the horizontal root motion (the character walks on the spot; use it with Unity root motion off).

Writes <out_dir>/<Name>.glb (all clips as glTF animations), <Name>.fbx (one take per clip) and updates
<Name>.blend. With --keep-existing, clips already in the .blend are kept and the new ones added.
"""
import math
import os
import sys

import bpy
import mathutils

argv = sys.argv[sys.argv.index("--") + 1:]
blend_path, out_dir, name = os.path.abspath(argv[0]), os.path.abspath(argv[1]), argv[2]
clips = []
i = 3
fps, inplace, do_fbx, keep_existing = 30, False, True, False
while i < len(argv):
    a = argv[i]
    if a == "--clip":
        cname, _, cpath = argv[i + 1].partition("=")
        clips.append((cname, os.path.abspath(cpath)))
        i += 2
    elif a == "--fps":
        fps = int(argv[i + 1]); i += 2
    elif a == "--inplace":
        inplace = True; i += 1
    elif a == "--no-fbx":
        do_fbx = False; i += 1
    elif a == "--keep-existing":
        keep_existing = True; i += 1
    else:
        raise SystemExit(f"unknown argument {a}")
os.makedirs(out_dir, exist_ok=True)

# Kimodo SOMA-77 joint -> Mixamo bone (without the "mixamorig:" prefix). SOMA's finger chains have one
# extra root joint inside the palm (Index1 = metacarpal), so Mixamo IndexN maps to SOMA Index(N+1).
MAP = {"Hips": "Hips", "Spine": "Spine1", "Spine1": "Spine2", "Spine2": "Chest", "Neck": "Neck1", "Head": "Head"}
for s in ("Left", "Right"):
    MAP.update({f"{s}Shoulder": f"{s}Shoulder", f"{s}Arm": f"{s}Arm", f"{s}ForeArm": f"{s}ForeArm", f"{s}Hand": f"{s}Hand",
                f"{s}UpLeg": f"{s}Leg", f"{s}Leg": f"{s}Shin", f"{s}Foot": f"{s}Foot", f"{s}ToeBase": f"{s}ToeBase"})
    for n in (1, 2, 3):
        MAP[f"{s}HandThumb{n}"] = f"{s}HandThumb{n}"
        for f in ("Index", "Middle", "Ring", "Pinky"):
            MAP[f"{s}Hand{f}{n}"] = f"{s}Hand{f}{n + 1}"

bpy.ops.wm.open_mainfile(filepath=blend_path)
bpy.context.preferences.filepaths.save_version = 0
scene = bpy.context.scene
scene.render.fps = fps
target = next(o for o in bpy.data.objects if o.type == "ARMATURE")
mesh_obj = next(o for o in bpy.data.objects if o.type == "MESH")
prefix = "mixamorig:" if "mixamorig:Hips" in target.pose.bones else ""
if not keep_existing:
    for a in list(bpy.data.actions):
        bpy.data.actions.remove(a)
    if target.animation_data:
        target.animation_data_clear()
target.animation_data_create()
# the NLA holds one track per clip; the exporters turn each into a glTF animation / FBX take
for t in list(target.animation_data.nla_tracks):
    if not keep_existing:
        target.animation_data.nla_tracks.remove(t)


def bone_rest_world(arm, bname):
    return (arm.matrix_world @ arm.data.bones[bname].matrix_local).to_3x3()


tgt_rest = {b.name: target.data.bones[b.name].matrix_local.copy() for b in target.data.bones}
tgt_parent = {b.name: (b.parent.name if b.parent else None) for b in target.data.bones}
tgt_order = [b.name for b in target.data.bones]   # bpy lists parents before children
hips_t = prefix + "Hips"
# rest-pose hips height: from the mesh data, not bound_box (which is the evaluated, possibly posed, mesh once
# NLA tracks from an earlier run exist)
tgt_hips_height = (target.matrix_world @ tgt_rest[hips_t].translation).z - min(
    (mesh_obj.matrix_world @ v.co).z for v in mesh_obj.data.vertices)

results = []
for cname, bvh in clips:
    before = set(bpy.data.objects)
    bpy.ops.import_anim.bvh(filepath=bvh, target="ARMATURE", global_scale=0.01, frame_start=1, use_fps_scale=False,
                            update_scene_fps=False, update_scene_duration=False, use_cyclic=False,
                            rotate_mode="QUATERNION", axis_forward="-Z", axis_up="Y")
    src = next(o for o in set(bpy.data.objects) - before if o.type == "ARMATURE")
    src_action = src.animation_data.action
    frame_end = int(src_action.frame_range[1])
    src_names = {b.name for b in src.data.bones}
    pairs = [(t, prefix and t[len(prefix):] or t) for t in tgt_order]
    pairs = [(t, MAP.get(short)) for t, short in pairs]
    pairs = [(t, s) for t, s in pairs if s in src_names]
    src_rest_rot = {s: bone_rest_world(src, s) for _, s in pairs}
    src_hips = "Hips"
    src_hips_rest_pos = src.matrix_world @ src.data.bones[src_hips].matrix_local.translation
    src_feet = [n for n in src_names if n.endswith("Foot") or n.endswith("ToeBase")]
    src_hips_height = src_hips_rest_pos.z - min((src.matrix_world @ src.data.bones[n].matrix_local.translation).z
                                                for n in src_feet) if src_feet else src_hips_rest_pos.z
    ratio = tgt_hips_height / max(src_hips_height, 1e-6)

    action = bpy.data.actions.new(cname)
    target.animation_data.action = action
    try:
        target.animation_data.action_slot = action.slots.new(id_type="OBJECT", name=cname)
    except Exception:
        pass
    for pb in target.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.location = (0, 0, 0)
    W_t_inv = target.matrix_world.inverted()
    for f in range(1, frame_end + 1):
        scene.frame_set(f)
        pose_mats = {}            # desired pose-space (armature) 4x4 per target bone
        for t, s in pairs:
            spb = src.pose.bones[s]
            rot_world = (src.matrix_world @ spb.matrix).to_3x3()
            delta = rot_world @ src_rest_rot[s].inverted()
            rot_t_world = delta @ (target.matrix_world @ tgt_rest[t]).to_3x3()
            rest = tgt_rest[t]
            if tgt_parent[t] is None:
                pos_world = (src.matrix_world @ spb.matrix).translation - src_hips_rest_pos
                if inplace:
                    pos_world.x = pos_world.y = 0.0
                pos = (target.matrix_world @ rest.translation) + pos_world * ratio
                pos = W_t_inv @ pos
            else:
                p = tgt_parent[t]
                pos = (pose_mats[p] @ tgt_rest[p].inverted() @ rest).translation
            m = (W_t_inv.to_3x3() @ rot_t_world).to_4x4()
            m.translation = pos
            pose_mats[t] = m
        for t in tgt_order:
            if t not in pose_mats:          # unmapped: follow the parent (identity basis)
                p = tgt_parent[t]
                pose_mats[t] = (pose_mats[p] @ tgt_rest[p].inverted() @ tgt_rest[t]) if p else tgt_rest[t].copy()
        for t in tgt_order:
            p = tgt_parent[t]
            if p is None:
                basis = tgt_rest[t].inverted() @ pose_mats[t]
            else:
                basis = tgt_rest[t].inverted() @ tgt_rest[p] @ pose_mats[p].inverted() @ pose_mats[t]
            pb = target.pose.bones[t]
            pb.rotation_quaternion = basis.to_quaternion()
            pb.keyframe_insert("rotation_quaternion", frame=f)
            if p is None:
                pb.location = basis.translation
                pb.keyframe_insert("location", frame=f)
    # stash the clip as its own NLA track
    track = target.animation_data.nla_tracks.new()
    track.name = cname
    strip = track.strips.new(cname, 1, action)
    strip.name = cname
    target.animation_data.action = None
    bpy.data.objects.remove(src, do_unlink=True)
    bpy.data.actions.remove(src_action, do_unlink=True)
    results.append((cname, frame_end))
    print(f"CLIP {cname} frames={frame_end} ratio={ratio:.3f}")

scene.frame_start, scene.frame_end = 1, max([fe for _, fe in results] + [1])
for pb in target.pose.bones:
    pb.rotation_quaternion = (1, 0, 0, 0)
    pb.location = (0, 0, 0)
bpy.ops.object.select_all(action="SELECT")
glb_path = os.path.join(out_dir, name + ".glb")
bpy.ops.export_scene.gltf(filepath=glb_path, export_format="GLB", use_selection=False, export_animations=True,
                          export_animation_mode="NLA_TRACKS", export_skins=True, export_yup=True, export_apply=True,
                          export_force_sampling=True, export_frame_range=False, export_rest_position_armature=True)
if do_fbx:
    fbx_path = os.path.join(out_dir, name + ".fbx")
    bpy.ops.export_scene.fbx(filepath=fbx_path, use_selection=False, apply_scale_options="FBX_SCALE_ALL",
                             axis_forward="-Z", axis_up="Y", path_mode="COPY", embed_textures=False,
                             mesh_smooth_type="FACE", use_tspace=True, add_leaf_bones=False, bake_anim=True,
                             bake_anim_use_all_bones=True, bake_anim_use_nla_strips=True, bake_anim_use_all_actions=False,
                             bake_anim_force_startend_keying=True, bake_anim_simplify_factor=0.0,
                             use_armature_deform_only=True)
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out_dir, name + ".blend"), compress=True)
print(f"ANIM {glb_path} clips={','.join(f'{c}:{n}' for c, n in results)} fps={fps} inplace={inplace}")
