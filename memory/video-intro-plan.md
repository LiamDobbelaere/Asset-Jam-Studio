---
name: video-intro-plan
description: "ai-vid/ (built 2026-10-26): Wan 2.2 TI2V-5B fp16 shot-list pipeline in the ai-3d ComfyUI + studio Video tab; measured speed, gotchas, rejected alternatives (LTX-2.5, Hunyuan, 14B)"
metadata:
  type: project
---

Built 2026-10-26 for the user's own studio-logo intro (a private side project). **Merged into main (commit 2e0dd0b) at the user's request, still experimental**: the user found the first full draft "really bad" (one shot drifted to a different subject mid-clip, another was physically implausible) and did not want to vouch for it. If revisited: longer pinned shots, fewer pinned end frames, better prompts; the models (18 GB) and the aivid_wan node stay installed in ai-3d/ComfyUI. `ai-vid/` = `intro.bat shots\<name>.json` → scripts/intro.py (shot list runner), shot.py (one Wan clip, build_api()), logo_reveal.py (Pillow animation of the real PNG), assemble.py (ffmpeg xfade + audio); `custom_nodes/aivid_wan` adds an end-frame input to the 5B latent node (noise-mask in-painting). Studio got a `video` job kind, Video tab (shot editor, keyframes-only / assemble-only / regenerate one shot), `/api/video`, `/api/library/videos`.

**Model:** Wan 2.2 TI2V-5B fp16 (`Comfy-Org/Wan_2.2_ComfyUI_Repackaged`, 10 GB) + umt5_xxl fp8 (6.7 GB) + wan2.2_vae, in ai-3d/ComfyUI/models. ComfyUI 0.42's WAN22 class lists fp16, no Turing patch needed. Settings = ComfyUI's 5B template: shift 8, 20 steps, CFG 5, uni_pc/simple. Measured on the 2080: 832×480 2 s clip in 85 s incl. model load (~40 s sampling per second of video), ~1.3 GB VRAM at rest (weights stream from RAM via DynamicVRAM).

**Why:** LTX-2.3/2.5 are 22B + Gemma 12B, 16 GB floor even at Q4, bf16-only in ComfyUI (would need fp16 patch), $10M-revenue community license; HunyuanVideo 1.5 EU-excluded; Wan 14B ~16 GB; Wan 2.5–2.7 API-only. A video model can't reproduce exact logo art/text, hence the hybrid (klein keyframes from the logo + Wan clips + Pillow reveal).

**How to apply:** frames = 4k+1 at 24 fps; width/height multiples of 32; SaveVideo's DynamicCombo is awkward in API JSON, so shot.py saves PNG frames and encodes with ffmpeg (C:\ffmpeg\bin), keeping the exact last frame for chaining. Keyframes use sprite.build_api(raw=True, alpha="none", model="base"). See [[hardware-and-license-constraints]], [[sprite-generation-setup]], [[studio-web-ui]].
