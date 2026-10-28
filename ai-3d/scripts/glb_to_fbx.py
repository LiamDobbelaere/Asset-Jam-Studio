"""Runs inside Blender (headless). Converts a generated GLB into a Unity-ready prop:

    blender -b --python glb_to_fbx.py -- <in.glb> <out_dir> <AssetName> <longest_dimension_m> [level|nolevel] [dissolve=DEG] [crease=DEG]

Writes <out_dir>/<AssetName>.fbx and <out_dir>/<AssetName>Diffuse.png (plus <AssetName>Normal.png
when the GLB carries a baked normal map), with one material named <AssetName> that uses the diffuse
texture and, if present, the normal map (no metallic / roughness maps), the mesh centred on X/Y,
resting on Z=0 and scaled so its longest side is <longest_dimension_m> metres.

Image-to-3D returns the mesh in the reference camera's frame, i.e. tilted by the camera
elevation and turned by its azimuth. By default the mesh is first set level (see
level_mesh.py) and the input GLB is rewritten with the same corrected pose, so GLB and FBX
agree.

dissolve=DEG merges coplanar triangles (faces within DEG of each other, never across UV seams) and
re-triangulates, so the decimation budget is spent on small parts rather than flat panels; crease=DEG is
the hard-edge angle for the recomputed normals (60 for hard-surface props, 180 for round ones).
"""
import math
import os
import sys

import bpy
import mathutils
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import level_mesh  # noqa: E402

argv = sys.argv[sys.argv.index("--") + 1:]
src, out_dir, name, size = argv[0], argv[1], argv[2], float(argv[3])
do_level = (argv[4] if len(argv) > 4 else "level") != "nolevel"
opts = dict(a.split("=", 1) for a in argv[5:] if "=" in a)
dissolve = float(opts.get("dissolve", 0))
crease = float(opts.get("crease", 60))
os.makedirs(out_dir, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
meshes = [o for o in bpy.data.objects if o.type == "MESH"]
for o in meshes:
    o.select_set(True)
bpy.context.view_layer.objects.active = meshes[0]
if len(meshes) > 1:
    bpy.ops.object.join()
obj = bpy.context.view_layer.objects.active
obj.name = obj.data.name = name
for o in list(bpy.data.objects):
    if o is not obj:
        bpy.data.objects.remove(o)

bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

# planar dissolve: coplanar triangles become one n-gon (inside a UV island only, so the baked textures stay
# valid), then triangulate again; normals are recomputed with the crease angle
dissolve_info = ""
if dissolve > 0:
    n_before = len(obj.data.polygons)
    m = obj.modifiers.new("dissolve", "DECIMATE")
    m.decimate_type = "DISSOLVE"
    m.angle_limit = math.radians(dissolve)
    m.use_dissolve_boundaries = False
    m.delimit = {"UV"}
    bpy.ops.object.modifier_apply(modifier=m.name)
    m = obj.modifiers.new("triangulate", "TRIANGULATE")
    m.quad_method = m.ngon_method = "BEAUTY"
    bpy.ops.object.modifier_apply(modifier=m.name)
    bpy.ops.object.shade_smooth_by_angle(angle=math.radians(crease))
    dissolve_info = f" dissolve={dissolve:g}deg({n_before}->{len(obj.data.polygons)})"

# set the mesh level: the rotation whose bounding box is smallest, without turning it over
level_info = "level=off"
if do_level:
    verts = np.empty(len(obj.data.vertices) * 3)
    obj.data.vertices.foreach_get("co", verts)
    (yaw, pitch, roll), ratio = level_mesh.solve(level_mesh.hull_points(verts.reshape(-1, 3)))
    m = level_mesh.euler_to_matrix(yaw, pitch, roll)
    obj.matrix_world = mathutils.Matrix(m.tolist()).to_4x4() @ obj.matrix_world
    bpy.ops.object.transform_apply(rotation=True)
    level_info = f"level=yaw{yaw:+.1f}/pitch{pitch:+.1f}/roll{roll:+.1f} bbox_x{ratio:.2f}"

# scale to the requested size and put the base on the ground
longest = max(obj.dimensions)
obj.scale = (size / longest,) * 3
bpy.ops.object.transform_apply(scale=True)
bbox = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
lo = mathutils.Vector((min(v.x for v in bbox), min(v.y for v in bbox), min(v.z for v in bbox)))
hi = mathutils.Vector((max(v.x for v in bbox), max(v.y for v in bbox), max(v.z for v in bbox)))
obj.location -= mathutils.Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
bpy.ops.object.transform_apply(location=True)

# one material, diffuse texture only
mat = obj.material_slots[0].material if obj.material_slots else None
tex_img = None
normal_img = None
if mat and mat.use_nodes:
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf and bsdf.inputs["Base Color"].is_linked:
        n = bsdf.inputs["Base Color"].links[0].from_node
        if n.type == "TEX_IMAGE":
            tex_img = n.image
    if bsdf and bsdf.inputs["Normal"].is_linked:
        n = bsdf.inputs["Normal"].links[0].from_node
        if n.type == "NORMAL_MAP" and n.inputs["Color"].is_linked:
            n = n.inputs["Color"].links[0].from_node
        if n.type == "TEX_IMAGE":
            normal_img = n.image
new_mat = bpy.data.materials.new(name)
new_mat.use_nodes = True
nt = new_mat.node_tree
bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
bsdf.inputs["Roughness"].default_value = 1.0
bsdf.inputs["Metallic"].default_value = 0.0
tex_path = os.path.join(out_dir, name + "Diffuse.png")
if tex_img is not None:
    tex_img.filepath_raw = tex_path
    tex_img.file_format = "PNG"
    tex_img.save()
    tex_node = nt.nodes.new("ShaderNodeTexImage")
    tex_node.image = bpy.data.images.load(tex_path)
    nt.links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
normal_path = os.path.join(out_dir, name + "Normal.png")
if normal_img is not None:
    normal_img.filepath_raw = normal_path
    normal_img.file_format = "PNG"
    normal_img.save()
    n_tex = nt.nodes.new("ShaderNodeTexImage")
    n_tex.image = bpy.data.images.load(normal_path)
    n_tex.image.colorspace_settings.name = "Non-Color"
    n_map = nt.nodes.new("ShaderNodeNormalMap")
    nt.links.new(n_tex.outputs["Color"], n_map.inputs["Color"])
    nt.links.new(n_map.outputs["Normal"], bsdf.inputs["Normal"])
elif os.path.isfile(normal_path):
    os.remove(normal_path)
obj.data.materials.clear()
obj.data.materials.append(new_mat)

fbx_path = os.path.join(out_dir, name + ".fbx")
bpy.ops.export_scene.fbx(filepath=fbx_path, use_selection=False, apply_scale_options="FBX_SCALE_ALL",
                         axis_forward="-Z", axis_up="Y", path_mode="COPY", embed_textures=False,
                         mesh_smooth_type="FACE", use_tspace=True, add_leaf_bones=False)
if do_level:
    # the GLB callers keep is the one that was levelled, not the raw camera-frame mesh
    bpy.ops.export_scene.gltf(filepath=src, export_format="GLB", export_apply=True, export_yup=True,
                              export_image_format="AUTO", export_materials="EXPORT")

obj.data.calc_loop_triangles()
print(f"GLB2FBX {fbx_path} tris={len(obj.data.loop_triangles)} verts={len(obj.data.vertices)} "
      f"dims={[round(v, 3) for v in obj.dimensions]} texture={'yes' if tex_img else 'no'} "
      f"normalmap={'yes' if normal_img else 'no'} {level_info}{dissolve_info}")
