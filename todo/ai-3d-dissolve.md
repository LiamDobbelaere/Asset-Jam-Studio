# TODO: revisit the planar-dissolve step

Written 2026-10-27. The Blender planar dissolve that `glb_to_fbx.py` applies to game assets
(3000-triangle budget, then `Decimate (Planar)` + re-triangulation, normals recomputed by angle) visibly mangles the
model compared with the raw ComfyUI output ("Save 3D (Advanced)" in the studio job view).

## What was seen

Filing cabinet, seed 399127219, same ComfyUI GLB (2978 tris) exported three ways and rendered with
`scripts/render_row.py`:

| export | tris | look |
|---|---|---|
| raw ComfyUI GLB (camera frame) | 2978 | clean |
| `glb_to_fbx.py ... dissolve=0` (levelled, scaled, material reduced) | 2978 | clean, identical shading |
| `glb_to_fbx.py` with the default dissolve | 1378 | shading gradients across the drawer fronts, faceted top, dark streaks along the edges |

So the levelling / material rewrite is fine; the dissolve is what hurts.

## Why

The normal map is baked in ComfyUI against the decimated low-poly: its vertex normals (`MeshSmoothNormals`, crease
60°) and the tangents `ApplyTextureToMesh` writes. Dissolving the flat faces into big fans afterwards changes the
interpolated normals over those faces and drops the TANGENT attribute, so the map is decoded against a basis it was
not baked for. Any change to the low-poly after the bake does this; the dissolve is just the first one we added.
The FBX has the same problem in principle (Unity regenerates tangents from the new triangles).

## Options

1. Make the dissolve opt-in again (`dissolve=0` default) and keep the 3000-triangle budget: ~2x the triangles of the
   old preset but clean shading. This is what the OVERTIME game does now (`tools/gen_assets.py` re-exports the ComfyUI
   source with `dissolve=0`).
2. Dissolve **before** the bake: run the decimated mesh through Blender (dissolve + triangulate), feed it back into the
   ComfyUI graph for `UnwrapMesh` → bakes. Needs a round trip out of ComfyUI mid-workflow (Blender step between two
   ComfyUI prompts, or a custom node that shells out to Blender), but it is the correct order.
3. Re-bake after the dissolve in Blender itself: Blender can bake normal + AO from the high-poly (`--save-highpoly`)
   onto the dissolved mesh with its own cage; replaces the ComfyUI bakes for game assets. More work, more control.
4. Keep the dissolve but preserve what the bake needs: keep custom split normals (`mesh.normals_split_custom_set`
   from the imported loop normals is lost when the dissolve rebuilds topology) and export tangents. Probably still
   wrong wherever a dissolved face spans vertices whose baked normals disagreed.

Until one of these is done, prefer the raw mesh for anything that will be looked at up close.
