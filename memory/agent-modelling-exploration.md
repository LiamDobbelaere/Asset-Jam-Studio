---
name: agent-modelling-exploration
description: "2026-10-29 exploration of Claude writing Blender code to model props from the reference picture, and how to texture them; what worked, what failed, what's next (StableGen)"
metadata:
  type: project
---

User rejected the box rebuild ([[box-rebuild-geometry]]) as "a non-solution": they want props built the way a person box-models them. On 2026-10-29 I modelled props as bpy scripts from the reference pictures (scratchpad agent/mlib.py: box+bevel, lathe, tube along path, inset). The desk, chair, cup, punch, tent and dish read as intentional at 300-1500 tris. The snowy pine (organic) still looked bad after 4 rounds.

Texturing attempts the user judged bad:
- Projecting the reference picture via a silhouette-fitted camera: smears wherever the model doesn't match the picture.
- Baking the pipeline's own generated texture onto the clean model (ICP align + selected-to-active): black gaps and misplaced colours, because the shapes differ.

No open "artist mesh" model is usable. MeshAnything, BPT and FastMesh are non-commercial or exclude the EU; the MIT/Apache ones output dense triangle soup.

**Why:** The user wants generated props on par with their hand-made ones; the texture quality decides whether agent modelling is viable.

2026-10-30: StableGen v0.4.0 works and is far better than both texturing attempts above. Setup:
- Models: installer package 2 plus RealVisXL into ai-3d/ComfyUI.
- Add-on: installed in a scratch Blender profile (BLENDER_USER_RESOURCES), not the user's.
- Generation: driven from a windowed Blender, because its operator is modal: `object.test_stable`, ARCHITECTURE preset, the picture's fitted camera plus 5 more, IP-Adapter = the reference picture. About 2 min per prop.
- Bake: StableGen's own `unwrap` + `bake_texture` functions in -b. A plain Cycles bake of its material comes out flat.

2026-10-30 flaw fixes (scratchpad agent/sg2/, run_sg2.sh):
- "Black unseen faces" were really a bake bug: StableGen's `unwrap(obj,"smart",...)` left about 30% of faces at UV (0,0). Use bpy.ops.uv.smart_project on all faces into a "BakeUV" layer instead; bake_texture picks that name up.
- The darker colours in my first sheet came from my turn.py studio light (COLTYPE), not the bake. Judge colour with flat light.
- Prompt bleed is fixed by view prompts (scene.camera_prompts, name=camera): "shared | front-only details | plain wording for the other views". Details then go on the picture camera only. The plain wording must cover the whole prop, or the top view paints the cup lid as cardboard.
- Cameras: an eye-level ring plus a top view. Two low views at -25° only with SG_LOW, for props with undersides: on the cup they invented green logo pills copied from the IP-Adapter picture. Discard angle 65 (the preset's 80 smears). Magenta fallback filled from the neighbouring texels in sg_bake.py.
- Part lock (sg_bake.py args: source.glb + part names, e.g. Snow,Floor / Wood / Lid,White): plain parts take the median generated colour, or the modelled colour if the median strays more than 0.25, with clamped shading. This fixed the orange tent snow and the lantern glow on the post. Material indices must be read before StableGen's bake_texture, which resets them.
- 2026-10-30: guided img2img built in, replacing StableGen.
  - Scripts: paint_guides.py, paint_project.py, paint.py view_api; setup_paint.ps1 + download_paint_models.py, no add-on. paint_drive/paint_bake removed.
  - Params: denoise (0.7, or 0.85 for single-material models), depth 0.8, ipadapter 0.6.
  - Verified on snowmobile, cup, tent, lantern, chair and desk (the desk through the studio). ~3 min per prop, views ~20 s each.
  - Committed and pushed as 4a878b6.
- 2026-10-30, texturing fix found (first prototyped in scratchpad diag/):
  - Why StableGen's views conflicted: they are generated from noise, and depth ControlNet 0.6 does not hold SDXL to the shape. Each view invented its own snowmobile (extra seat, wheeled track) and the projection smeared them together.
  - Fix, "guided img2img":
    1. render_guides.py renders each camera as Claude's part colours (Workbench MATERIAL + studio light + cavity) plus a 16-bit depth map.
    2. guided_gen.py runs the same SDXL stack (RealVisXL + Lightning + IP-Adapter style 0.6 + depth CN 0.8) as img2img from that guide; denoise 0.7 adds detail and stays consistent.
    3. project_bake.py (own numpy projection) bakes texel position and normal, keeps a view only where its depth matches and away from depth edges, weights by facing^6 (front view x1.5), and fills unseen texels with the part colours + AO.
  - It beat StableGen and flat colours clearly: track lugs, stripes, no conflicts. It needs no StableGen and no Blender window.
  - Claude painting the texture itself was judged inferior: the flat colours are clean but plain. Claude's role is choosing the part colours that drive the guide.
- 2026-10-30, later: studio kind "prop" does the whole chain from a prompt.
  - Steps: Z-Image picture, then headless `claude -p` (ai-3d/scripts/prop_model.py + prop_brief.md, cwd ai-3d/assets/modelled/<Name>/, dontAsk, may only Write/Edit ./** and run prop_check.py), then paint.
  - Snowmobile: 3-4 rounds, ~2 min, ~$0.45 on Opus; ~5 min end to end.
  - The CLI default model became claude-fable-5-1, which was out of credits, so the flow defaults to --model opus.
- 2026-10-30: built into the repo and committed as 39393f3 (paint only):
  - Scripts: ai-3d/scripts/prop_kit.py, paint.py, paint_fit.py, paint_drive.py, paint_bake.py, plus paint.bat and setup_paint.ps1. The StableGen profile lives in ai-3d/blender/ (gitignored).
  - Studio: kind "paint" (page model3d, "Paint a modelled prop" form).
  - Skill: workflow is model the prop in ai-3d/assets/modelled/<Name>.py, then submit paint.
  - Verified end to end on the chair (CLI) and the desk (studio).

Original flaws: unseen faces go black (fallback colour), prompt items bleed onto other parts (frog on the cup lid, orange tent colour on the snow), and the back view can invent detail (drawers on the desk's back). Scripts: scratchpad agent/sg_drive.py, sg_bake.py, run_sg.sh.

**How to apply:** Earlier plan, now done: try StableGen (GPL-3.0 Blender add-on + ComfyUI, SDXL + depth ControlNet + IP-Adapter, ~10 GB of models) to paint the clean mesh directly. Wait for the user's go-ahead first: they paused it to free the GPU for streaming. The rejected box rebuild is in git stash@{0} ("box rebuild (--geometry boxes), rejected; kept for reference").
