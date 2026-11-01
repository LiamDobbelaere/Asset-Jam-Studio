---
name: box-rebuild-geometry
description: "--geometry boxes (custom_nodes/ai3d_boxes): rebuild dense mesh as union of axis-aligned boxes for box-modelled props; what failed first and open ideas"
metadata:
  type: project
---

Built 2026-10-29 because the user box-models their game's props (extrudes/insets/cubes) and found generated ones blobby, high-tri, and the planar-dissolve step mangles normals (they used the pre-dissolve version). `BoxRebuildMesh` levels + Manhattan-aligns the closed RemeshMesh output, voxelizes at 128, greedily takes the largest >=90%-solid box (full-extent growth per axis in all 6 orders from ~24 seeds), prunes <1% boxes, snaps faces within 2 voxels, merges equal cross-sections, unions with manifold3d (added to setup.ps1). Results same seed: desk 1510->84 tris, chair 1761->243, vending 1477->12, shelf 204. Opt-in, default stays remesh.

**Why:** CoACD convex parts cut furniture along the wrong planes (strips, wedges); raw VAE mesh is open so the solid won't fill (hollow sheets); bbox leveller leaves 1.5-5 deg lean.

**How to apply:** Next ideas: slanted parts (oriented boxes / plane fit per box), lathe for round props, auto mode by fit error, keep tiny parts. Related: [[game-asset-small-parts]], [[game-art-style]], [[text-to-3d-setup]].

2026-10-30: the user rejected this approach; all of its changes (incl. custom_nodes/ai3d_boxes) are in git stash@{0}, not on main. The installed copy in ComfyUI/custom_nodes was removed too.
