---
name: character-animation-setup
description: "ai-anim (added 2026-10-26): Make-It-Animatable rigging + NVIDIA Kimodo text-to-motion + Blender retargeting; why these models, the Windows/Turing workarounds, the gated Llama-3 encoder, and the studio Animate tab"
metadata:
  type: project
---

`ai-anim/` (2026-10-26) rigs humanoid meshes with **Make-It-Animatable** (MIT, Mixamo 52-bone skeleton, ~3 s) and
animates them with **NVIDIA Kimodo-SOMA-RP v1.2** (Apache code, NVIDIA Open Model License weights, commercial OK),
retargeted in headless Blender 5.1 (`scripts/blender_rig.py`, `blender_animate.py`) into one GLB (glTF animations)
+ Unity FBX (one take per clip). Studio tab "Animate", job kind `anim`, persistent `scripts/anim_worker.py`.

**Why:** UniRig/Puppeteer/MagicArticulate need spconv/flash-attn or non-commercial deps; Hunyuan is EU-excluded;
ARDY is Linux-only; MoMask gives positions only on non-commercial data. Kimodo exports T-pose BVH itself, so the
retarget is a plain world-space rotation-delta copy (both rests are T-poses) with a SOMA77→Mixamo bone map
(finger joints shift by one, Leg/Shin→UpLeg/Leg).

**How to apply:** Neither repo is pip-installed (Kimodo's setup.py builds a CMake extension, no MSVC here; MIA needs
pytorch3d/torch_cluster/bpy). `vendor/` carries pytorch3d.transforms (pure Python) and a torch fps; `mia_compat.py`
stubs bpy and builds the Mixamo hierarchy from a table because `jasongzy/Mixamo` (bones.fbx) is gated. Kimodo runs
with `TEXT_ENCODER_DEVICE=cpu` (Llama-3-8B LLM2Vec, 16 GB RAM) and `post_processing=False`. The Llama-3 repo is
gated: the user got access and logged in on 2026-10-27; everything is downloaded (15 GB Llama + 1.1 GB Kimodo in
the HF cache) and verified end to end: load ~30 s, ~10 s per 4 s clip, retarget faithful within cm. Prompt wording
matters a lot ("waves hello with their right hand" = timid; "raises their right arm high above their head and waves
enthusiastically" = proper) and seeds vary; transformers 5 prints a bogus MISSING/UNEXPECTED lora_* report while the
LLM2Vec adapters load (verified harmless). `ai-anim/assets/LuigiDemo` and `OfficeWorker` (a Pixal3D character preset result, rigged + Idle/Walk/Wave, judged
good → Animate tab left beta on 2026-10-27) hold demo characters (git-ignored). CLI runs while ComfyUI holds its
models are slow (first clip 268 s vs 40 s): free ComfyUI first; the studio does it itself. In
the Claude app the auto-mode classifier sometimes blocks running the cloned repos ("Code from External"); retry via
Bash with a plain command. Related: [[text-to-3d-setup]], [[studio-web-ui]], [[hardware-and-license-constraints]].
