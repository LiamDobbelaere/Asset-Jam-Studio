---
name: pixel-art-generation-setup
description: "ai-3d/pixel.bat + studio Pixel art page (2026-11-10): FLUX.2 klein 4B + Apache pixel-art LoRAs, grid/palette snapping in Python, 4x4 walk-cycle sprite sheets via svntax-dev's LoRA (the working animation route); Wan 2.2 5B animation judged unusable by the user; Wan 14B GGUF route pending; timings"
metadata:
  type: project
---

Built 2026-11-10: `ai-3d/pixel.bat` → `scripts/pixel.py` (stills via `sprite.build_api(..., lora=)`, a `LoraLoaderModelOnly`
node "4" added to `build_sprite_workflow.py`, bypassed in the UI workflow; animation via ai-vid's `shot.build_api` with
start = end = the upscaled still, plus BiRefNet nodes appended to the API graph). Studio kinds `pixel` / `pixel_anim`,
page `pixel`, library `/api/library/pixels` (stills = `assets/pixel/<Name>.png`, animations = `assets/pixel/<Name>/`).

**Models:** stills = the installed FLUX.2 klein 4B + `loras/pixel_sprite_klein4b.safetensors` (Limbicnation/pixel-art-lora,
distilled klein, CC0 sprites, Apache-2.0, ComfyUI-format file) or `loras/pixel_scene_klein4b.safetensors`
(adirik/pixel-art-lora-flux.2-klein-4B, base klein, trigger `PXART4`, Apache-2.0). ComfyUI loads both; it skips the
3 klein modulation keys of the sprite LoRA (harmless warning). The "pixel-perfect" part is Python: sample one point per
block at the grid offset with the lowest nearest-neighbour reconstruction error (`best_offset`, 16 candidates), threshold
alpha, median-cut + k-means palette, no dither. Centre sampling beat box averaging clearly (box blurs the model's own
pixel edges). Animation = Wan 2.2 TI2V-5B (ai-vid weights) image-to-video from the nearest-upscaled still, every frame
cut out by BiRefNet (it loops per image, so a 25-frame batch is fine), frames picked evenly and re-quantized with the
still's locked palette at offset (0.5, 0.5).

**Why:** Retro Diffusion is closed / paid; the Wan 2.2 pixel-animation LoRAs (styly-agents Wan2-2-pixel-animate,
theamusing attack LoRA) target the 14B I2V model, which does not fit 8 GB; Sprite-Sheet-Diffusion (MIT, 2024) is an
Animate-Anyone pose pipeline, heavier and older; Z-Image-Turbo pixel LoRAs exist (tarn59) but klein also does
sprite→pixel edits through ReferenceLatent, so one model serves both.

**Animation verdict (2026-11-10):** the user judged the Wan 2.2 5B animation flow "basically doesn't work" (static pinned
loops, drift, brightness flicker). The replacement is `pixel.bat --sheet` / kind `pixel_sheet`: svntax-dev's
`pixel_spritesheet_4walk_small_lora_v1` (Apache-2.0, 88 MB, base klein only; the author says the distilled model ruins it)
draws a 512² 4×4 sheet of one 32 px character (3 walk frames × 4 directions + arms up, jump L/R, lying) in ~60 s; pixel.py
snaps it to 128², flood-fills the border background to alpha (scipy.ndimage.label) and splits it into named cells + walk
GIFs (0-1-2-1). Consistent across frames because it is one picture; identity from a reference picture is approximate (knight
plume → red hair, slime → green humanoid), text descriptions keep details better. 32 px only. The Wan path stays as an
experimental fold-away. Wan 2.2 14B I2V + styly-agents' pixel-animate LoRA (rank 256, 2.3 GB, both experts, 4-step lightx2v
LoRAs, 45 frames 832×480, CFG 1.1, shift 5, DDIM) is now engine `wan14` (default when installed; `ai-3d/setup_pixel_anim.ps1`, ComfyUI-GGUF
pinned at 6ea2651 + gguf 0.19, Q4_K_S GGUFs 8.7 GB each in models/unet, workflow built by build_pixel_wan_workflow.py:
UnetLoaderGGUF x2 → lightx2v LoRA → pixel LoRA → ModelSamplingSD3 shift 5, WanImageToVideo (no end frame), KSamplerAdvanced
high steps 0-2 / low 2-4, ddim, CFG 1). Measured: 33 frames at 512² in 206 s, 45 frames in 198 s (no warm speed-up: the experts stream from RAM), peak 7.7 GB VRAM; the lightx2v LoRA logs
~330 "lora key not loaded" for k_img/v_img/img_emb/diff_m keys the 2.2 I2V model lacks (harmless), the pixel LoRA's 800 keys
load. Output: crisp pixel frames in the still's colours, consistent character, no flicker, but modest motion (33 frames: sword and leg shifts;
45 frames with a walk-cycle prompt: real leg steps, short of a full stride). The user approved installing the node on 2026-11-10. A 3D route
(ai-anim clips rendered orthographically in Blender, then pixelized) was considered and not built.

**Illustration style (2026-11-10, user request):** `--style illustration` / schema `style` on `pixel` and `pixel_anim` and a
Style switch on the studio page: klein without LoRA at the asked size (ILLUSTRATION prompt templates in pixel.py, ILLUS_NEGATIVE),
no snapping; wan14 with the pixel-animate LoRA nodes pruned (shift nodes read the lightx2v LoRAs), frames kept at ~512 px, GIF
via write_gif_rgba (255-colour adaptive palette). Tested: illustrated knight 512² in ~30 s, its 33-frame walk via the studio in
295 s; sheets stay pixel-only.

**How to apply:** measured on the 2080: 64² sprite 11-15 s warm (65 s with model load), 128×96 scene 52 s (base, 20
steps), 1 s animation at 512² 224 s cold (Wan weights stream from RAM), then 1.5 s in 59 s and 2 s in 79 s warm.
Pinning the still as the end frame (ai-vid's end-frame node) kills the motion: 1 s and 2 s pinned clips only
shifted the sword, while an unpinned 1.5 s clip gave real strides (with some drift: the knight turned towards the
camera by frame 6). So `loop` defaults to `none`, `pingpong` (forward + backward frames) is the loop for idles,
`pin` stays as an option. Read a few frames before trusting the GIF. Wan also flickered a flat green cartoon slime
(48×64, white bg) dark in half of the frames with both a nearest-upscaled and a Lanczos-upscaled input (the raw mp4
shows it, so it is not the palette step); the default is `upscale smooth` (natural-video prior), and detailed
sprites like the knight animated cleanly. The distilled model drifts colours when redrawing an existing sprite (dusty green bush →
bright green): name colours or use `--model base`. See [[sprite-generation-setup]], [[video-intro-plan]],
[[hardware-and-license-constraints]].
