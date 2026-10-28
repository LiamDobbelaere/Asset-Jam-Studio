# High-detail generation → retopology, and other ways past the remaining blobbiness

Written 2026-10-27 after the `--edges sharp` + normal-map fix (see ai-3d.md step 0). **Worked through the same day;
results and the new defaults are below, the original idea list follows for reference.**

## The observation

The game-asset preset ran Pixal3D at shape resolution 1024 and remeshed at 256. Hard-surface props came out crisp,
but anything with small or thin features (chair castors, armrest pads, the printer bot's feet, the lumbar pad) was
still soft or gone: the assumption was that the 256-voxel remesh and the 1024 cascade simply do not resolve
features under ~1 % of the object, so the fix would be generate at full detail first, then retopologize.

## Results (2026-10-27)

Same-seed A/Bs rendered with `scripts/render_row.py` (textured + clay). Subjects: FilingCabinet (seed 399127219,
box), OfficeChair (seed 1279728908, thin parts), PrinterBot (seed 829913924, mixed; its budget is 2000).
Post-processing variants of a cached seed re-run in 60-90 s; a cold run is 4-6 min; mixing subjects in one batch
evicts ComfyUI's RAM cache and every variant goes cold again, so run all variants of one subject back to back.

### Where the small parts die

| stage | chair | verdict |
|---|---|---|
| raw shape-VAE mesh, 1024 cascade | 5.5M tris; castors, armrest pads, lumbar pad, mesh weave all present | the high-poly is already complete |
| raw mesh, 1536 cascade | 15.8M tris; nothing extra at prop size | cascade is not the bottleneck |
| remesh 256 (`solid`, QEF) | 256k tris; castors and lumbar pad intact (also with `drop_small_components` 0) | remesh is not the bottleneck |
| QEM decimation to 1500 | castors and lumbar pad gone | **this is where they go**: small parts have a tiny quadric error and are collapsed first |

So "generate at high detail first" has nothing to add: the detail is there, the decimator throws it away.

### Experiments, cheapest first

| # | variant | result |
|---|---|---|
| 1 | `--resolution 1536` (chair) | 239 s warm (vs 341 s cold baseline at 1024, texture 1024²); low-poly identical to the 1024 one |
| 1 | `--resolution 1536` (cabinet) | upsample ran at 120 s/step (12 steps, VRAM-bound at 7.6 GB) because the flat cabinet has 3x the active voxels (15.6M raw tris at 1024 already); the run, together with parallel Blender renders, froze the machine. Not viable as a default: the cost scales with surface area, not a flat +30 % |
| 1 | `--remesh 512` (chair) | decimator stalls at 3934 tris on the thousands of holes the 512 remesh resolves in the mesh back; castors still gone; 388 s. Worse |
| 1 | 1536 + remesh 512 | same as 1024/256 at 1500 tris, armrests slightly spikier |
| – | decimate the raw VAE mesh directly (no remesh) | stalls at 96.7k tris: the raw mesh is open and non-manifold. The remesh is a required bridge |
| 2 | normal map baked from the raw mesh (`--normal-from raw`) | carries the weave and fine detail but also the voxel staircase as grain/stripes on flat areas (bot paper tray, chair seat); nothing the remesh bake lacked. Kept as an option, default stays `remesh` |
| 3 | `feature_edge_quadric_weight` 10 / 50 / 200, dihedral 15° / 30° / 45° (chair and bot at the default budget) | all identical within noise; not a lever |
| 3 | `--tris` 1500 → 3000 → 4000 (chair) | 3000: castors and lumbar pad back; 4000: cleaner castors and armrest pads. **The budget is the lever** |
| 4 | Blender planar dissolve after decimation (UV-delimited, re-triangulated) | cabinet 1494 → 1164 / 823 / 593 tris at 2° / 5° / 10°, no visible UV distortion at 2-5°, 10° flattens a groove bevel. Chair 2994 → 2507 / 2151 / 1911 at 5° / 10° / 15°, castors kept, textures intact, faint shading gradient on big merged faces at 10°+ |
| 4 | decimate to 3000, then dissolve 10° | cabinet 2988 → 1254 tris with crisp handles (the 1486-tri baseline had blobs); chair 2994 → 2151 with castors. **New default** |
| 5 | Blender Remesh / Decimate on the raw high-poly | Collapse stalls at 714k (non-manifold); Voxel remesh 0.006 shatters the open shell; Sharp remesh depth 8 + planar + collapse stalls at 15.9k. Out, unless fed the ComfyUI `solid` remesh, which already keeps the parts, so there is nothing upstream to gain |
| 5 | Instant Meshes / Quad Remesher | not tried: with the budget + dissolve fix the remaining topology is still triangle soup, but the small parts are there; quads would only matter for deformation (characters) |
| 6 | artist-topology models | see below; none installed |
| 7 | diffuse blur 2 px → 0, AO 0.45 → 0.3 (cabinet) | handles and panel lines crisp, no smudged darkening; the 2 px blur was hiding what the normal map restores. **New default for `--shape hard`**, round keeps 2 / 0.45 |

### New `gameasset` defaults (in `scripts/generate.py`, the studio schema and 3D form)

- `--tris` 3000 for hard props (round 1500, character 4000), then **`--dissolve 10`** in `glb_to_fbx.py`: Blender's planar
  decimate inside UV islands, re-triangulated, normals recomputed with the 60° crease (180° for round). The FBX and the
  rewritten GLB carry the reduced mesh; the baked textures stay valid because the dissolve never crosses a UV seam.
  Through the studio: FilingCabinet_3 1336, OfficeChair_3 2113, PrinterBot_3 2062 triangles (each from ~2990). `--dissolve 0` turns it off,
  `--dissolve 5` gives slightly cleaner shading on big panels at +40 % triangles.
- `--blur 0 --ao 0.3` for hard props (round: 2 / 0.45).
- `--normal-from remesh|raw` exposed; default `remesh`.
- Remesh 512 for hard props (round 384; 256 for perforated parts, see below). Unchanged: cascade 1024,
  `--edges sharp` with feature weight 50 @ 30°, normal map on.
  `--edges soft` and every old value remain available for comparison.

### Remesh 512 on a clean hard-surface prop (water cooler, same reference picture as a `detailed` run)

The chair result above ("remesh 512 is worse") is chair-specific. On the water cooler (seed 26158060, the
`detailed` run's reference picture fed to the game-asset preset so both share the shape), rendered next to the
700k-triangle `detailed` mesh:

| variant | result |
|---|---|
| 1024 cascade, remesh 256, 3000 tris (default) | taps melt into one lump, bottle ribs wavy, neck collar smeared |
| 1024, **remesh 512**, 3000 | three distinct taps, crisp ribs, collar back; +15 s |
| 1536, remesh 512, 3000 | same as the line above, the cascade adds nothing visible again |
| 1536, remesh 512, 6000 | closest to the `detailed` silhouette |

So on props without perforated parts the 256 remesh is the biggest remaining source of blobbiness, and 512 is now
the hard-shape default; a mesh chair back (thousands of holes the 512 grid resolves) makes the decimator stall, so
`--remesh 256` stays available for perforated parts. The `detailed` preset applies no remesh or decimation at all (different workflow), which is why
its mesh looks crisp and why `--tris` does nothing there; it is 700k triangles of voxel surface and ran in 3.5 min
for this prop at 1536 / 4096.

### Artist-topology models (survey 2026-10-27, nothing installed)

| model | weights | licence | notes |
|---|---|---|---|
| MeshAnything V2 | yes | S-Lab, non-commercial | out |
| MeshFlow (Meta, CVPR 2026) | gated | FAIR non-commercial | out |
| BPT, QuadGPT (Tencent Hunyuan) | BPT lite / none | Tencent (EU-excluded), BPT ~12 GB fp16, Linux | out |
| Meshtron, PolyFlow, MeshFIM | none public | – | nothing to run |
| DeepMesh | 0.5B on HF | Apache-2.0 code, weights licence unclear | Ubuntu/A100-tested, built on BPT |
| **LATO.2** | HF checkpoints | MIT code, weights licence not stated | ~900M params over four models, README says ~8 GB VRAM, 200-5000 vertex budget, ~5 s/mesh on an H800; fp16 on Turing untested |

ComfyUI core has no retopology node; community packs only wrap Instant Meshes.

LATO.2 was tried the same day and discarded. It can be made to run on the 2080 (own venv with torch 2.6 cu124,
xformers instead of flash-attn, fp16 instead of bf16, a surface-sampling stand-in for TRELLIS.2's compiled
voxeliser and a pyrender stand-in for Open3D's offscreen renderer; 20-30 s per mesh), but on the chair's `solid`
remesh every run came back not watertight, with stray faces spanning half the object, ~2.6 triangles per vertex
(5055 tris for a 2000-vertex budget) and no UVs, at 1000 and 2000 vertices and from both a 60k and the full 256k
input. The edge / face logit thresholds do not remove the stray faces. fp16 on Turing may be part of it (the
authors ran bf16), so a retry only makes sense on a bf16 card or with a newer checkpoint.

### Still open

- Topology is still triangle soup; edge loops would need Quad Remesher (paid) or LATO.2 (above). Only matters for
  props that deform.
- The dissolve merges faces up to 10° apart, so very large panels get a faint shading gradient; 5° if it shows.
- Script-built hard-surface props (ai-3d.md option 4) remain the way to exact dimensions and clean UVs.

---

## Original idea list (kept for reference)

### 1. High-detail first, then retopology (the standard pipeline, now that the high-poly is good)

- Run the shape stage at 1536 (the `detailed` cascade) but stop before the 4K texture bake; shape at 1536 was
  a few minutes, the 25-minute figure for `detailed` is mostly the 4096² PBR bake. Measure this first: if the
  1536 shape + normal/diffuse bake at 1024² lands under ~6 min, it is a drop-in upgrade for `gameasset`.
- Replace `DecimateMesh` with real retopology on that high-poly:
  - **Quad Remesher** (Blender add-on, paid, seconds per mesh, hard-edge aware): a Blender step in
    `glb_to_fbx.py`, like the leveller. Best quality per minute of effort; needs a licence.
  - **Instant Meshes** (free, CLI, `instant-meshes mesh.obj -o out.obj -f 1500 -c`): weaker on hard edges but
    fine for organics. Could run from `generate.py` directly.
  - Blender's own `Remesh` modifier in `SHARP`/`VOXEL` mode plus `Decimate (Planar)` with an angle limit: free,
    already installed, merges coplanar faces into big quads, so a cabinet becomes ~60 faces + the edge loops.
    Worth trying first since it needs no new software.
- Bake normal + AO + diffuse from the 1536 high-poly onto the retopologized low-poly (the workflow's
  `BakeNormalMapFromMesh` / `BakeAmbientOcclusion` / `BakeTextureFromVoxel` already do high→low, they only need
  the low-poly input swapped; the diffuse bake needs the voxel colours from the texture stage, which exist for
  the 1536 run too). A Blender-made low-poly can be fed back through `Load 3D (Advanced)` → `Get 3D Components`.
- Risk: retopology tools need clean, mostly watertight input; the `solid` remesh mode at 512 can be the
  bridge (one watertight shell, 1M tris) if the raw VAE mesh is too messy for them. (Confirmed: it is.)

### 2. Generative retopology / artist-topology models (from ai-3d.md option 2, but fed the 1536 mesh)

Point-cloud → low-poly-mesh models (MeshAnything V2, BPT, DeepMesh, PolyFlow, QuadGPT, Meshtron) are exactly
"generate high detail, then let a model draw the low-poly the way an artist would". The high-detail mesh is
the right input for them: they sample points from the surface, so feeding the 1536 result instead of the 256
remesh gives them the small features to reproduce. Open questions remain the same as in ai-3d.md (released
weights, licence, 8 GB VRAM, fp16 on Turing, minutes per mesh), plus one new one: their face budgets
(1–4k, sometimes 8k) and their tendency to drop thin parts; a per-prop A/B against QEM + normal map is the
test, with the cabinet, the office chair and the printer bot as subjects (box, thin parts, mixed).

### 3. Cheaper partial steps, in order

1. `--resolution 1536 --remesh 512` on the current pipeline (nothing to build): see how much of the softness
   is the cascade and how much is the remesh. Same-seed A/B with `render_row.py`, ~10 min.
2. Bake the normal map from the **raw VAE mesh** instead of the remesh (one input swap in `build_api`): the
   remesh at 256 already loses the small features before they reach the bake.
3. `DecimateMesh` feature-edge weight and dihedral threshold sweep (50 @ 30° was the first value that worked,
   not a tuned one) and the `--tris` budget per prop class: 1500 is plenty for a box and too few for a chair.
4. Blender planar dissolve after decimation (free, no new tools) to spend the budget on edges instead of flat
   faces; check UV distortion.
5. Only then the paid/heavy options (Quad Remesher, artist-topology models).

### 4. Texture side, since it decides half of "blobby"

- The 1024² diffuse is base colour × AO with a 2 px blur ("painted look"); at 1500 tris the blur hides
  the panel lines the normal map just restored. Try blur 0–1 and AO strength 0.3 on hard-surface props.
- Bake at 2048² for props the camera gets close to (weapons), 1024² for the rest (`--texture 2048`, not tested).
- The Z-Image reference picture decides the design; a `--stage image` review before building was worth it
  (23 of 23 usable), keep that in the game workflow.
