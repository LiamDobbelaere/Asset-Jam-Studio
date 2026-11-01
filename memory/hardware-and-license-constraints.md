---
name: hardware-and-license-constraints
description: "User's local ML hardware (RTX 2080 8 GB, Turing, 64 GB RAM, Windows 11) and the EU-license constraint that rules out Tencent Hunyuan models"
metadata:
  type: user
---

The user runs local AI on Windows 11 with an NVIDIA RTX 2080 (8 GB VRAM, Turing sm_75: no flash-attn, no native bf16/fp8), an i9-9900KF, 64 GB RAM and lots of disk. Python 3.14 is the system default, so projects use uv with Python 3.12. The user is based in Belgium (EU).

**Why:** Model picks must fit 8 GB (int8 weights + RAM offloading work; 16–24 GB upstream requirements do not), and Tencent Hunyuan community licenses (Hunyuan3D 2.0/2.1, HY-Motion) exclude the EU, UK and South Korea, so they were deliberately not used.

**How to apply:** Prefer MIT/Apache models with ComfyUI-native (no compiled extension) ports; check VRAM against 8 GB and license territory before recommending. See [[text-to-3d-setup]].
