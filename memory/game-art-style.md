---
name: game-art-style
description: "Target art style for the user's game props: low-poly but smooth and rounded, NOT blocky; one hand-painted 1024 diffuse per prop"
metadata:
  type: project
---

The user's game props are low-poly (about 600–1800 triangles for the bigger ones: desk chair 1156, couch 1506,
vending machine 1766, water cooler 658, potted plant 582, pool table 633) with fully smooth-shaded, rounded,
bevelled forms and a single hand-painted 1024² diffuse texture per prop (soft gradients, dark ink outlines on seams),
URP/Lit with only a base map.

Full curated set (2026-10-29): 76 objects, 21.6k tris total, median ~150 tris (cardboard box 22, basic desk 38,
kitchen chair 116, stool 356); only chairs/couch/vending machine exceed 1000. Most props are simple bevelled primitives
whose detail (labels, panel lines, outlines, decals) lives in the painted diffuse. The generated assets (~1800 tris,
blurry voxel-baked diffuse) are far above that budget and below that texture clarity.

**Why:** The user objected when I described the style as "blocky" and prompted for blocky shapes; low poly here means
economical topology with smooth shading, not Minecraft-like cubes.

**How to apply:** Never use "blocky" / "cubic" in prompts or descriptions. Ask for "smooth rounded low-poly forms, soft
bevelled edges, hand-painted flat colours with soft shading". Keep smooth shading (high crease angle) and a 600–1800
tri budget. Judge results against renders of the source .blend, not its FBX export (the FBX import loses textures).
File locations and the game's name: [[game-project-private]].
