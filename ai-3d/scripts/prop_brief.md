# Model a game prop in Blender from its reference picture

You are modelling ONE low-poly game prop as a Blender Python script, the way a 3D artist box-models it, from the
reference picture in this folder (`reference.png`). The studio paints it afterwards from the same picture with an
image model, so **you build the shape only**; colours, wood grain, labels and small printed details come from the
paint. Work in this folder only.

## The loop

1. Read `reference.png` and `{SCRIPTS}\prop_kit.py` (the modelling helpers, read the whole file once).
2. Write `{NAME}.py` in this folder (template below).
3. Build and look: run exactly
   `{CHECK} {NAME}.py`
   It prints the triangle count and the parts, or the Python error, and writes `check.png`: the reference picture
   followed by your model as clay from the front-left, front-right, right side and back. Read `check.png`.
4. Compare with the picture and fix the biggest difference first: proportions (height vs width vs depth), the
   number and placement of the parts, which parts touch, thickness of slabs and legs, overall silhouette. Edit the
   script and run the check again. Stop when the clay reads as the object in the picture from every side: usually
   3-5 rounds, never more than {ROUNDS}.

## Rules

- Units are metres at the real size of the object (a desk ~1.2 m wide, a cup ~0.15 m tall, a snowmobile ~2.8 m long).
  Z is up, the object stands on z = 0, **its front faces -Y** (the side the picture shows most), centred on x = 0.
- Build from the parts a person would model: bevelled boxes (`k.box(..., bevel=...)`), cylinders (`k.cyl`), lathed
  profiles (`k.lathe`: cups, lids, bowls, posts, domes, wheels), tubes along a path (`k.tube` + `k.smooth_curve`:
  handles, frames, rails, poles), and `k.edit(obj, fn)` with bmesh for anything else (move vertices to taper or
  bend a box, inset, extrude). Rotate parts with `rot=` (radians). `k.mirror_x` for symmetric parts.
- Parts must touch or overlap where they are joined in the picture: nothing floats.
- Give visible mechanical parts real volume and their own material: a snowmobile track or tank tread is a long
  rounded box (or lathed wheels in a belt) that clearly sticks out under the body, wheels are cylinders, a grille or
  vent is an inset panel. A thin strip under the body does not show up in the paint.
- One material per kind of part, named after it in PascalCase (`Body`, `Seat`, `Windshield`, `Ski`, `Wood`,
  `Metal`, `Snow`), with its colour from the picture (linear RGB 0-1). Get the colours right: the paint starts
  every view from a render of your model in these colours, so they set where each colour goes. Give parts that
  differ in colour in the picture their own material (a white stripe, a black bumper, a dark seat).
- 300-3000 triangles. Spend them on the silhouette (rounded edges with `bevel`/`segs`, 12-24 segments on round parts),
  not on details smaller than ~2 % of the object: those are painted.
- Model what the picture shows, nothing extra (no ground plane, no background, no text).
- Soft organic shapes (foliage, creatures, cloth) are out of scope: approximate them with a few smooth lathed or
  bent forms rather than many small pieces.

## Template

```python
import math, os, sys
sys.path.insert(0, r"{SCRIPTS}")
import prop_kit as k
from mathutils import Vector

k.reset()
wood = k.mat("Wood", (0.55, 0.36, 0.20))
metal = k.mat("Metal", (0.18, 0.18, 0.19))
k.box("Post", (0.075, 0.075, 0.68), (0, 0, 0.34), wood, bevel=0.008)
L = Vector((0, -0.10, 0.28))   # something hanging in front of the post (-Y)
k.lathe("Base", [(0.0, 0.0), (0.055, 0.0), (0.06, 0.012), (0.055, 0.03), (0.035, 0.045)], metal, segs=14, loc=L)
k.tube("Hook", k.smooth_curve([L + Vector((0, 0, 0.30)), Vector((0, -0.09, 0.62)), Vector((0, -0.05, 0.645))], 10),
       0.005, metal, segs=4)
k.export("{NAME}", os.path.dirname(os.path.abspath(__file__)))
```

A cup, for the lathe profile style (radius, height) from the bottom up:
`k.lathe("Cup", [(0.0, 0.0), (0.030, 0.0), (0.033, 0.004), (0.045, 0.125)], white, segs=20, cap_top=False)`

## When you are done

Your final answer is the structured output the studio asks for. It describes the painting, view by view:
- `prompt`: what the whole object is and its materials and colours, for every view
  ("stylized 3D game prop, soft hand-painted texture, clean, pastel, " + e.g. "safety orange and black snowmobile
  with a dark tinted windshield, black skis and track").
- `front`: details only the front (the picture's side) has: a logo, a printed face, drawers, a control panel.
  Empty if the object looks the same all round.
- `plain`: how every OTHER side looks, describing ALL parts, so nothing from the front is repeated: "plain orange
  body panels without decals, black seat, black track". A top view told only "plain sleeve" painted a cup's lid as
  cardboard, so name every part.
- `lock`: material names of plain single-colour parts that a neighbour's colour could bleed onto (snow, a plain
  wooden post next to a glowing lamp, a white lid above a printed sleeve). Often empty.
- `low`: true only if the object has an underside worth painting that is seen from below (chair or table frames,
  stretchers). False for anything sitting flat on the ground.
- `notes`: one or two sentences on what you modelled and what you could not get right.
