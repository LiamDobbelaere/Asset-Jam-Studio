---
name: prop-style-guides
description: "Style guides for the modelled-prop flow (ai-3d/styles, 2026-11-01): the inked style tuned against the user's hand-made props; FLUX.2 klein img2img painter, flat guides, tone/flatten/shade/ink finish, decals; what failed"
metadata:
  type: project
---

On 2026-11-01 the user asked for the Claude-modelled prop flow to match their own hand-made props as a separate,
selectable style (never named after their game: see [[public-repo-rules]]). Built as `ai-3d/styles/<id>.json` +
`<id>.md` (prop_style.py), `style` param on studio kinds prop/paint, `--style` on paint.py / prop_model.py. The
style is `inked`. The reference props and their measurements are in [[game-art-style]] and the private note.

What the reference props are (rendered from the user's .blend, 20 props): bevelled-primitive shapes, box 22 tris,
keyboard 12 (one sloped slab), vending machine a box with an inset glass panel, couch/chair ~1150-1500; detail
painted (keys, buttons, labels, logos); textures = flat muted base colours, soft blotchy airbrushed shading, dark
outlines of a darker shade along island borders and around painted details; "Smooth by Angle" modifiers.

Findings, measured on 12 test props (vending machine, water cooler, chair, archive box, keyboard, couch, wheelie bin,
pedal bin, filing cabinet, microwave, backpack, bookshelf):
- Default paint baked the guides' studio light into the albedo: diffuses 1.3-4x darker than the user's (chair
  median 38 vs 181). Fixed for the style with flat-lit guides + the finish's `tone` (part brightness back to the
  modelled colour). The default pipeline still has this; not changed (user didn't ask).
- RealVisXL (SDXL) paints photo materials (glossy metal, realistic cans, leather grain, a lock on a plain side)
  whatever the prompt or IP-Adapter. FLUX.2 klein 4B as img2img from the guide paints the flat outlined look.
  klein as a plain edit (no img2img) moves the camera (silhouette IoU 0.41-0.93) -> unusable for projection.
  img2img denoise 0.75 keeps IoU 0.95-0.99; 0.85 broke a back view. At 0.75 the plain back/side views got a whole
  vending front (window, buttons) invented; fix: picture as image 2 only for the front view, other views 0.6 and
  "keep plain surfaces plain", and Claude's shared `prompt` must not name front-only details.
- Decals (prop_kit.decal, Decal* materials) let Claude place painted details: in the guides, dropped before the bake,
  also outlined by the ink. Claude used 1-10 per prop. A few big decals for "the keys" paint as flat blocks (klein
  keeps them); one decal per key in a loop gave a keyboard with every key painted (52 tris, studio job, 2026-11-01). Modelling took 48-127 s and $0.26-0.50 per prop on Opus.
- Z-Image pictures stay "3D icon" bright whatever the words; the picture mostly sets shape and layout, and decides
  the design: "chunky" in the picture words drew 40 % mini keyboards with tall keycaps, which Claude then modelled
  (200 tris vs the user's 12): wording changed to "slightly chunky proportions", and keys are always decals now.
  The pedal bin (drawn square with a door) and bookshelf (2x3 wooden) missed the user's designs for the same reason.
- Final comparison 2026-11-01: wheelie bin, backpack, archive box, couch, desk chair, water cooler judged close in
  look; texture brightness now in the user's range (chair 178 vs 236; was 40). Finish settled at flatten 0.85,
  ink 2 px, ink_dark 0.55 (3 px / 0.4 drew every edge too heavily: the user's vending machine, box and couch have
  no edge lines). UV islands are many small smart-project islands, unlike the user's hand-laid ones.

- 2026-11-01, private styles from the user's own .blend (style_refs.py, style_index.py, prop_refs.py, prop_chain.py,
  style_eval.py, style_lora_data.py): showing Claude its most similar reference props cut triangles (box 88 -> 24)
  and the held-out probe went 0.88 -> 0.68. Drawing the picture from refs alone made klein copy the reference
  object; "restyle" (Z-Image, then klein in the ref's style) keeps the object but still leaks distinctive parts.
  Finish stats (saturation, flatness) barely move the CLIP probe. Game-specific numbers: [[house-style-work]] (private).
- 2026-11-02, local paint LoRA (musubi-tuner, klein base 4B, RTX 2080): fp16 gives NaN loss, bf16 (emulated) works
  at 13 s/step, 6.8 GB. A guide -> painted-prop edit LoRA brought texture brightness/flatness to the originals'
  but did not move the CLIP probe: the paint step is no longer the bottleneck; design (the picture) is.
- 2026-11-02, a text-to-image picture LoRA (klein, the props' three-quarter renders) moved the probe 0.68 -> 0.62-0.64:
  the biggest single gain after the reference props. Its pictures are as dark as the shaded renders it learned from,
  so Claude picks dark part colours; the finish "value" (target median brightness) fixes the textures.

**Why:** the user wants generated props that sit next to their own; the texture look was the biggest gap, then
over-detailed geometry.

**How to apply:** tune a style in its JSON/MD, not in code. Judge with the same renderer for both sets (Workbench
TEXTURE, studio light) and compare texel brightness, not only looks. Scratch tools used: render the user's .blend
props from 8 angles with the render UV map made active (the active UV is often a second, unpainted layout).
