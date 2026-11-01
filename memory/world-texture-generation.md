---
name: world-texture-generation
description: "texture.bat tiling: native latent-roll + circular VAE (ai3d_seamless nodes) is default; cross-fade for flat walls; LoRA opt-in (grass yes, walls no); grass prompt trick"
metadata:
  type: project
---

World textures (walls/floors/ground) come from Z-Image-Turbo via `texture.bat`. As of 2026-10-26 the default
tiling is native: `custom_nodes/ai3d_seamless` (SeamlessTileModel rolls the latent per step in 2-patch
multiples, CircularVAEDecode patches Conv2d padding_mode for one decode). 14 steps default. Edge diff equals
interior diff on every material tested; planks/tiles line up across borders.

**Why:** the old roll + cross-fade (still `--tile crossfade`) can't align plank/tile grids. Tested and
rejected: mikemojen latent circular-padding node (blobs/artifacts with Z-Image), upstream
Universal-Seamless-Tiles "Make a copy" VAE (deepcopy crashes on comfy_aimdo pinned buffers and poisons the
server until restart), a settle_sigma cutoff that stops rolling on late steps (makes lines worse).
Flat materials (plaster) still show very faint straight lines with native tiling, so cross-fade is the
recommendation for plain walls. Z-Image draws tufts for "grass"; describe dense dry-brush dashes instead.
No better base model for the 2080: Flux texture LoRAs are non-commercial and Flux dev doesn't fit 8 GB.

**How to apply:** edit `scripts/build_texture_workflow.py` (not the JSON) and re-run it with the server up;
copy `custom_nodes/ai3d_seamless` into ComfyUI/custom_nodes and restart the server after node edits.
The StylizedTexture_ZIT LoRA (Civitai 503877) is installed (user downloaded it 2026-10-26) but opt-in:
`--lora 0.8` is a big win for grass and fine for tiles; it hurts plain walls and parquet layout, so default 0. Related: [[game-art-style]], [[text-to-3d-setup]].
