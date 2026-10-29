# TODO: better geometry for game props

Written 2026-10-27. **Step 0 done the same night (see below); 1-5 remain as further options.**
Context: Pixal3D / TRELLIS.2 output looks "melted" at game poly
counts because both generate a voxel / SDF field and extract a dense triangle soup from it. Raising
the voxel resolution sharpens edges but the mesh is then unusable in-game, and decimating it to
~1500 tris keeps the silhouette with random topology. The shape model has the information; the
extraction + decimation step throws it away. Options below, roughly in order of expected payoff.

## 0. Done: stop the post-processing from melting it (`--edges sharp` + normal map)

The premise above was only half right: the shape VAE's mesh is crisp; it was our own ComfyUI post-processing
that melted it. `RemeshMesh` ran `smooth_iters=20` with `qef=false`, and `DecimateMesh` used `midpoint`
placement, which drags every corner inward. Same-seed A/B on the filing cabinet (seed 399127219, rendered with
`scripts/render_row.py`):

| variant | result |
|---|---|
| old (smooth 20, no QEF, midpoint) | pillow, wavy edges, grooves gone |
| smooth 2 + QEF, midpoint decimation | still wavy: the decimator is the main culprit |
| old remesh + QEM decimation (optimal placement, feature-edge quadrics 50 @ 30°) | clean box again |
| smooth 2 + QEF + QEM (now `--edges sharp`) | clean box, drawer grooves survive at 1494 tris |
| + normal map baked from the remeshed high-poly (now default) | reads like the 280k high-poly in the viewport |
| + remesh 512 | no visible gain, +30 s |

Implemented in `scripts/generate.py` (`EDGES`, `--edges`, `--no-normalmap`, `--remesh`, `--save-highpoly`),
`scripts/glb_to_fbx.py` (keeps the normal map as `<Name>Normal.png`), and the studio (schema, flow, 3D form).
Post-processing variants of the same seed re-run in ~60-80 s thanks to ComfyUI's RAM cache, so further
tuning is cheap. Still true: topology is random triangle soup, so 1 (retopo) and 2 (artist-topology models)
remain the way to get edge loops; 4 (script-built hard-surface props) is still the cheapest way to exact
dimensions and clean UVs.

## 1. High-poly → retopology → bake (standard game pipeline)

- Keep the 1536-res Pixal3D mesh as the high poly.
- Replace `DecimateMesh` with real quad retopology: **Quad Remesher** (paid Blender add-on, ~100 EUR,
  industry standard, seconds per mesh) or **Instant Meshes** (free, weaker on hard edges).
- Bake normal + diffuse from high to low (the workflow already has `BakeNormalMapFromMesh`,
  `BakeAmbientOcclusion`, `BakeTextureFromVoxel`; the normal bake is in the game-asset preset since step 0).
- Probably the biggest improvement for the least work. Needs a Blender step in `glb_to_fbx.py`
  or a ComfyUI retopo node if one exists by then.

## 2. Artist-topology mesh generators (point cloud → low-poly mesh with edge loops)

Feed the Pixal3D shape in, get a 500–2000 face mesh out, the way an artist would build it.

| Model | Year | Notes |
|---|---|---|
| MeshAnything V2, BPT, DeepMesh | 2025 | open weights, autoregressive, ~1B params (fits 8 GB), minutes per mesh on Turing |
| Meshtron (NVIDIA) | 2025 | higher face counts, heavier |
| [PolyFlow](https://www.alphaxiv.org/abs/2606.30673) | 2026 | flow matching, ~6 s for 4k verts vs ~550 s for BPT (authors' numbers) |
| [MeshFlow](https://arxiv.org/pdf/2606.04621), [LATO.2](https://arxiv.org/pdf/2607.10623) | 2026 | flow-based, read papers |
| [QuadGPT](https://arxiv.org/pdf/2509.21420) | 2026 | native quad output |
| [MeshFIM](https://arxiv.org/abs/2605.08744) (Tencent) | 2026 | local low-poly mesh editing; Tencent license likely EU-excluded |

Open questions: which of the 2026 ones have released weights; license; VRAM; fp16 on Turing.

## 3. Part-based generation (chair = seat + back + legs + wheels)

- [PartCrafter](https://neurips.cc/virtual/2025/poster/120299) (NeurIPS 2025, code + weights, up to 16 parts from one image).
- OmniPart (SIGGRAPH Asia 2025, MIT).
- Hunyuan3D-Part: strongest but Tencent license (EU-excluded).
- Each part voxelises cleaner and decimates better, and parts can be textured / animated separately.
  Still implicit-field geometry underneath, so combine with 1 or 2.

## 4. Script-built geometry for hard-surface props

- Desks, shelves, binders, monitors, vending machines are bevelled boxes and cylinders. A `bpy` script
  (Claude via the Blender MCP) gives exact dimensions, clean topology, clean UVs, our material setup,
  no voxel artefacts.
- Use the AI pipeline only for organic props (plants, cushions, food, clutter).
- Cheapest to try: pick three existing props, rebuild them by script, compare.

## 5. Commercial reference points (not local)

Tripo, Rodin Gen-2, Meshy ship quad retopology and a "low poly" style and are license-clean in the EU.
Useful as a quality ceiling to compare against, not as the pipeline.

## Also worth checking when revisiting

- Same-seed A/B of `--model pixal3d` vs `--model trellis2`: done on one prop (2026-10-29, see ai-3d-improvements.md): TRELLIS.2 is upright by construction but follows the picture less well.
- Whether a newer ComfyUI has native retopo / quad nodes (the mesh node set grew fast in 2026).
- Texture side: outputs lack the hand-drawn ink outlines of the house style; a Z-Image LoRA on our
  own diffuse textures, or an edge-detect pass baked into the diffuse, could get closer.
