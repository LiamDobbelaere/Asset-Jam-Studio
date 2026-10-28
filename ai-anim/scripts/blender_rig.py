"""Runs inside Blender (headless). Builds a rigged, game-ready character from a mesh and a rig .npz:

    blender -b --python blender_rig.py -- <mesh.glb> <rig.npz> <out_dir> <Name> [--height 1.8] [--keep-pose]
                                           [--no-fbx]

Reads the Mixamo joints / weights / to-rest pose that scripts/rig_infer.py (Make-It-Animatable) predicted,
creates an armature with the standard "mixamorig:" bone names, skins the ORIGINAL textured mesh to it,
moves the character into the Mixamo T-pose (unless --keep-pose), stands it on the ground at <height> metres
and writes:
    <out_dir>/<Name>.glb          rigged GLB (skin, no animation)
    <out_dir>/<Name>.fbx          Unity-ready FBX (Y-up, -Z forward) with <Name>Diffuse.png
    <out_dir>/<Name>.blend        the scene, used by blender_animate.py to add animation clips
"""
import os
import sys

import bpy
import mathutils
import numpy as np

argv = sys.argv[sys.argv.index("--") + 1:]
mesh_path, npz_path, out_dir, name = argv[:4]
mesh_path, npz_path, out_dir = (os.path.abspath(p) for p in (mesh_path, npz_path, out_dir))
height = 1.8
keep_pose = "--keep-pose" in argv
do_fbx = "--no-fbx" not in argv
if "--height" in argv:
    height = float(argv[argv.index("--height") + 1])
os.makedirs(out_dir, exist_ok=True)

# Blender (Z-up) <-> glTF / MIA (Y-up): G = C @ B
C = mathutils.Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))
C_INV = C.inverted()


def M(a):
    return mathutils.Matrix([[float(x) for x in row] for row in a])


rig = np.load(npz_path, allow_pickle=True)
bone_names = [str(n) for n in rig["bone_names"]]
parents = [int(p) for p in rig["parents"]]
joints_g, tails_g, bw, pose_g = rig["joints"], rig["joints_tail"], rig["bw"], rig["pose"]
T = M(rig["global_transform"])          # original mesh coords (Y-up) -> MIA normalised space (Y-up)

# ---------------------------------------------------------------------------------------------- mesh
bpy.ops.wm.read_factory_settings(use_empty=True)
ext = os.path.splitext(mesh_path)[1].lower()
if ext in (".glb", ".gltf"):
    bpy.ops.import_scene.gltf(filepath=mesh_path)
elif ext == ".fbx":
    bpy.ops.import_scene.fbx(filepath=mesh_path)
elif ext == ".obj":
    bpy.ops.wm.obj_import(filepath=mesh_path)
else:
    raise SystemExit(f"unsupported mesh format {ext}")
meshes = [o for o in bpy.data.objects if o.type == "MESH"]
if not meshes:
    raise SystemExit("no mesh in the input file")
bpy.ops.object.select_all(action="DESELECT")
for o in meshes:
    o.select_set(True)
bpy.context.view_layer.objects.active = meshes[0]
if len(meshes) > 1:
    bpy.ops.object.join()
mesh_obj = bpy.context.view_layer.objects.active
mesh_obj.name = mesh_obj.data.name = name
for o in list(bpy.data.objects):
    if o is not mesh_obj:
        bpy.data.objects.remove(o)
mesh_obj.parent = None
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
# into MIA's normalised, hips-aligned space (so the character faces -Y in Blender = +Z in glTF)
mesh_obj.matrix_world = C_INV @ T @ C
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
bpy.ops.object.shade_smooth()

# ---------------------------------------------------------------------------------------------- weights
# map MIA's vertex order onto Blender's (the importer may merge or split vertices) by nearest point
verts_b = np.array([C_INV @ mathutils.Vector(v) for v in rig["verts"]])
kd = mathutils.kdtree.KDTree(len(verts_b))
for i, v in enumerate(verts_b):
    kd.insert(mathutils.Vector(v), i)
kd.balance()
nearest = np.empty(len(mesh_obj.data.vertices), dtype=np.int64)
worst = 0.0
for v in mesh_obj.data.vertices:
    co, idx, dist = kd.find(v.co)
    nearest[v.index] = idx
    worst = max(worst, dist)
bw_b = bw[nearest]

# ---------------------------------------------------------------------------------------------- armature
arm_data = bpy.data.armatures.new(name + "Armature")
arm_obj = bpy.data.objects.new(name + "Armature", arm_data)
bpy.context.scene.collection.objects.link(arm_obj)
bpy.context.view_layer.objects.active = arm_obj
arm_obj.select_set(True)
bpy.ops.object.mode_set(mode="EDIT")
edit_bones = []
for i, bname in enumerate(bone_names):
    eb = arm_data.edit_bones.new(bname)
    head = C_INV @ mathutils.Vector(joints_g[i])
    tail = C_INV @ mathutils.Vector(tails_g[i])
    if (tail - head).length < 1e-4:
        tail = head + mathutils.Vector((0, 0, 0.02))
    eb.head, eb.tail = head, tail
    eb.use_connect = False
    if parents[i] >= 0:
        eb.parent = edit_bones[parents[i]]
    edit_bones.append(eb)
bpy.ops.object.mode_set(mode="OBJECT")

# skin: parent with an armature modifier and one vertex group per bone
mesh_obj.parent = arm_obj
mod = mesh_obj.modifiers.new("Armature", "ARMATURE")
mod.object = arm_obj
mesh_obj.vertex_groups.clear()
for i, bname in enumerate(bone_names):
    g = mesh_obj.vertex_groups.new(name=bname)
    col = bw_b[:, i]
    for vi in np.nonzero(col > 1e-3)[0]:
        g.add([int(vi)], float(col[vi]), "REPLACE")

# ---------------------------------------------------------------------------------------------- to T-pose
if not keep_pose:
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="POSE")
    for i, bname in enumerate(bone_names):
        pb = arm_obj.pose.bones[bname]
        if i == 0:
            continue                      # Hips stay where they are
        P = C_INV @ M(pose_g[i]) @ C
        pb.matrix = P @ pb.bone.matrix_local
        pb.location = (0.0, 0.0, 0.0)
        bpy.context.view_layer.update()
    bpy.ops.object.mode_set(mode="OBJECT")
    # bake the deformation into the mesh, then make this pose the new rest pose
    bpy.context.view_layer.objects.active = mesh_obj
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="POSE")
    bpy.ops.pose.armature_apply(selected=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    mod = mesh_obj.modifiers.new("Armature", "ARMATURE")
    mod.object = arm_obj

# ---------------------------------------------------------------------------------------------- size + ground
bpy.context.view_layer.update()
bbox = [mesh_obj.matrix_world @ mathutils.Vector(c) for c in mesh_obj.bound_box]
lo = mathutils.Vector((min(v.x for v in bbox), min(v.y for v in bbox), min(v.z for v in bbox)))
hi = mathutils.Vector((max(v.x for v in bbox), max(v.y for v in bbox), max(v.z for v in bbox)))
scale = height / max(hi.z - lo.z, 1e-6)
# unparent, scale + move both objects by the same transform, apply it to each, then re-parent with an identity
# parent-inverse: the mesh data itself is then in metres (no scale hidden in bind matrices for Unity)
mesh_obj.modifiers.remove(mod)
mesh_obj.parent = None
mesh_obj.matrix_world = mathutils.Matrix.Identity(4)
for o in (arm_obj, mesh_obj):
    o.scale = (scale,) * 3
    o.location = (-(lo.x + hi.x) / 2 * scale, -(lo.y + hi.y) / 2 * scale, -lo.z * scale)
bpy.ops.object.select_all(action="DESELECT")
arm_obj.select_set(True)
mesh_obj.select_set(True)
bpy.context.view_layer.objects.active = arm_obj
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
mesh_obj.parent = arm_obj
mesh_obj.matrix_parent_inverse = mathutils.Matrix.Identity(4)
mod = mesh_obj.modifiers.new("Armature", "ARMATURE")
mod.object = arm_obj

# ---------------------------------------------------------------------------------------------- material
mat = mesh_obj.material_slots[0].material if mesh_obj.material_slots else None
tex_img = None
if mat and mat.use_nodes:
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf and bsdf.inputs["Base Color"].is_linked:
        n = bsdf.inputs["Base Color"].links[0].from_node
        if n.type == "TEX_IMAGE":
            tex_img = n.image
tex_path = os.path.join(out_dir, name + "Diffuse.png")
if tex_img is not None:
    tex_img.filepath_raw = tex_path
    tex_img.file_format = "PNG"
    tex_img.save()
    new_mat = bpy.data.materials.new(name)
    new_mat.use_nodes = True
    nt = new_mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Roughness"].default_value = 1.0
    bsdf.inputs["Metallic"].default_value = 0.0
    tex_node = nt.nodes.new("ShaderNodeTexImage")
    tex_node.image = bpy.data.images.load(tex_path)
    nt.links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
    mesh_obj.data.materials.clear()
    mesh_obj.data.materials.append(new_mat)

# ---------------------------------------------------------------------------------------------- export
bpy.ops.object.select_all(action="SELECT")
glb_path = os.path.join(out_dir, name + ".glb")
bpy.ops.export_scene.gltf(filepath=glb_path, export_format="GLB", use_selection=False, export_animations=False,
                          export_skins=True, export_yup=True, export_apply=True)
fbx_path = os.path.join(out_dir, name + ".fbx")
if do_fbx:
    bpy.ops.export_scene.fbx(filepath=fbx_path, use_selection=False, apply_scale_options="FBX_SCALE_ALL",
                             axis_forward="-Z", axis_up="Y", path_mode="COPY", embed_textures=False,
                             mesh_smooth_type="FACE", use_tspace=True, add_leaf_bones=False, bake_anim=False,
                             use_armature_deform_only=True)
blend_path = os.path.join(out_dir, name + ".blend")
bpy.ops.file.pack_all()
bpy.ops.wm.save_as_mainfile(filepath=blend_path, compress=True)
mesh_obj.data.calc_loop_triangles()
print(f"RIG {glb_path} tris={len(mesh_obj.data.loop_triangles)} verts={len(mesh_obj.data.vertices)} "
      f"bones={len(bone_names)} height={height} weight_match_dist={worst:.4f} texture={'yes' if tex_img else 'no'} "
      f"fbx={'yes' if do_fbx else 'no'}")
