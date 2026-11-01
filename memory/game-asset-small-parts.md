---
name: game-asset-small-parts
description: "Why small parts (castors, feet) vanish on ai-3d game assets and the 2026-10-27 fix (3000 tris + Blender planar dissolve); what was measured not to help; ComfyUI cache + 1536 pitfalls"
metadata:
  type: project
---

Measured 2026-10-27 (same-seed A/Bs, chair 1279728908 / cabinet 399127219 / bot 829913924, table in
todo/ai-3d-improvements.md): the 1024 raw VAE mesh and the 256 remesh both keep castors, armrest pads, feet;
the QEM decimation to 1500 drops them (tiny quadric error), regardless of feature-edge weight (10/50/200) or
dihedral (15/30/45). Fix now default: `--tris 3000` then `--dissolve 10` (Blender planar decimate in glb_to_fbx.py,
UV-delimited) -> cabinet 1336, chair 2113, bot 2062 tris. Hard props: diffuse blur 0, AO 0.3. Remesh 512 (+15 s) is the big step toward the `detailed` look on CLEAN hard-surface props (cooler: taps/ribs crisp vs melted at 256) but stalls the decimator on perforated parts (mesh chair back), so 512 is the hard-shape DEFAULT since 2026-10-27 (REMESH in generate.py) and 256 is the opt-in for perforated props. `detailed` preset = no remesh/decimate at all (different workflow), --tris does nothing there. Presets append different style text, so the same seed gives different reference pictures across presets: A/B across presets needs the reference image fed in (reference_image / --reference_upload).

**Why:** "generate at 1536 then retopo" was the plan and it is dead: 1536 adds nothing visible, costs 120 s/step
on big flat props (cabinet, VRAM-bound), and the raw mesh is open/non-manifold so Blender remesh/decimate and
Instant-Meshes-style tools stall or shatter it. The ComfyUI `solid` remesh is the only bridge and it already keeps
the parts. Artist-topology models: MeshAnything/MeshFlow non-commercial, BPT/QuadGPT Tencent, Meshtron/PolyFlow
no weights. LATO.2 was tried the same day (ai-3d/LATO.2/run_lato2.py: torch 2.6 cu124 venv, xformers instead of flash-attn, fp16 instead of bf16, o_voxel + Open3D renderer shimmed): runs in 20-30 s on the 2080 but the chair comes back with holes, stray scene-spanning faces, 2.6 tris/vertex, no UVs. Not in the pipeline; retry only on a bf16 card or a newer checkpoint.

**How to apply:** for sweeps, run all variants of ONE seed back to back (mixing subjects evicts ComfyUI's RAM cache,
cached variants 60-90 s vs 4-6 min cold); never run `--resolution 1536` on big flat props and never render in
Blender while a huge remesh runs (froze the PC once, needed a hard reset). Experiment driver pattern: import
build_api, patch nodes, set Save3DAdvanced filename_prefix, queue on 8188 directly while the studio is idle.
Related: [[text-to-3d-setup]], [[game-art-style]].
