---
name: sprite-generation-setup
description: "ai-3d/sprite.bat = FLUX.2 klein 4B (fp8) sprite edit / pose-from-references / text-to-sprite with BiRefNet cut-out; why not Qwen-Image-Edit, FLUX 3 Klein, Z-Image-Edit; results on the user's reference sprites (2026-10-26)"
metadata:
  type: project
---

Set up 2026-10-26 in D:\Git\3d-model-gen\ai-3d: `sprite.bat "prompt" --image src.png [--ref x.png]` → scripts/sprite.py → workflows/sprite_edit_api.json (built by scripts/build_sprite_workflow.py + scripts/ui_export.py). Model: FLUX.2 [klein] 4B, fp8 distilled (4 steps, CFG 1, ~30 s) and base (20 steps, CFG 5, ~2 min), from black-forest-labs/FLUX.2-klein-4b-fp8 (Apache-2.0, not gated); reuses the Z-Image Qwen3-4B fp8 encoder with CLIPLoader type flux2, VAE flux2-vae. ComfyUI's Flux2 class allows fp16, so no Turing patch needed. RemoveBackground outputs a MASK (1 = foreground); JoinImageWithAlpha inverts, so InvertMask sits between them.

**Why:** Qwen-Image-Edit-2511 is 20B (only Q3 GGUF + offload on 8 GB), Qwen-Image-2.1 is research-only, FLUX.2 dev / klein 9B non-commercial, FLUX 3 Image Klein (Oct 2026) is gated + Kommunity license (<$5M revenue) and bf16-only in ComfyUI, Z-Image-Edit / Omni-Base weights unreleased as of Oct 2026. Reference sprites live in a private folder outside the repo (see private/ memories): a 282×624 cel-shaded iso character with ink outlines and 1024² platformer frames.

**How to apply:** Sitting-on-chair SE-iso pose from front idle + chair ref worked first try with the distilled model; shading passes keep line art but the distilled model drifts colours (hair → brown, plum jeans → blue-grey): name colours explicitly or use --model base. Use --alpha keep for shading passes (exact silhouette), --width/--height bigger than the source when the pose needs room. Possible next step: FLUX 3 Image Klein if the user accepts its license (needs an fp16 patch like TRELLIS), or a klein LoRA on the ~50 frames of the main character for stricter consistency. See [[text-to-3d-setup]], [[game-art-style]], [[hardware-and-license-constraints]].
