---
name: text-to-3d-setup
description: How the D:\Git\3d-model-gen text-to-textured-3D pipeline is built (ComfyUI 0.42 native Pixal3D/TRELLIS.2 + Z-Image-Turbo) and the gotchas hit on 2026-10-26
metadata:
  type: project
---

Set up on 2026-10-26: ComfyUI 0.42 in D:\Git\3d-model-gen\ComfyUI (uv venv, Python 3.12, torch 2.14.1+cu130) running the native Pixal3D / TRELLIS.2 int8 nodes, fed by Z-Image-Turbo int8 for text-to-image. CLI is generate.bat → scripts/generate.py which posts workflows/text_to_3d_api.json.

**Why:** Hunyuan3D is EU-excluded; upstream TRELLIS.2/Pixal3D need 24 GB and compiled CUDA kernels, but the ComfyUI port needs neither.

**How to apply:** Gotchas: ComfyUI warns that 20-series cards need the cu130 torch build (cu128 disables the comfy_kitchen CUDA backend). `huggingface_hub.commands.huggingface_cli` no longer exists as a module; use hf_hub_download in Python. UI→API workflow conversion was done in the browser with app.graphToPrompt() (dynamic combos like RemeshMesh sign_mode.fill are hard to convert by hand); re-export text_to_3d_api.json that way after editing the UI workflow. Related: [[hardware-and-license-constraints]].

Turing fp16 fixes applied 2026-10-26 (both in ComfyUI working tree, not upstream): supported_models.py Trellis2 adds torch.float16; trellis2/model.py keeps the sparse-transformer residual + norms in fp32 under fp16 (residual hit 63k → inf at block 29). First full run: 24.6 min, ~6.7 GB peak. Hunyuan3D was declined for license; user asked about it and about a GPU upgrade (gaming + AI, buys at alternate.be; Oct 2026 prices there are inflated: 5070 Ti €1479, 5080 €2499, 5090 €7999).

Game-asset preset added 2026-10-26: workflows/text_to_3d_gameasset(_api).json, generate.py default preset, Blender glb_to_fbx.py (FBX + diffuse PNG, longest side = --size m) and compare_render.py (side by side with props.blend objects). A game-asset run takes ~3 min; ComfyUI caches unchanged stages so re-running with only post-processing changes takes seconds.

Tilt (2026-10-29): Pixal3D reconstructs in the reference camera's frame (eye-level camera, pixel-aligned), so three-quarter-from-above pictures give a tilted voxel preview — by design, not a bug; feeding the true elevation via an orbit camera breaks the shape, TRELLIS.2 is upright but less faithful. Post-levelling (level_mesh.py) stays. Details in todo/ai-3d-improvements.md.

Texture projection (2026-10-29): custom_nodes/ai3d_project ProjectReferenceTexture projects the cropped reference picture onto the faces the Pixal3D camera saw (pinhole: camera on +Z at 0.5/tan(fov/2), mesh Y-up in [-0.5,0.5]), voxel bake on the hidden side with Gaussian colour-keyed matching (global mean/std tinted whites pink; kNN mottled). Default `texture_from=picture` in build_api, so studio/CLI/skill get it. Node code changes need a ComfyUI restart (cache lost, ~5 min cold per prop).

--shape round (generate.py) = crease 180, remesh 384, 1200 tris: fixed the faceted water-cooler bottle. The user plans more workflows in ai-3d/ soon. Note: moving a folder while the ComfyUI server or the app's file viewer holds files in it fails with Permission denied; stop the server first and move children individually.
