# ai-3d: text → game-ready 3D props, world textures, 2D sprites and pixel art (local, RTX 2080 / 8 GB)

Local pipeline that turns a text prompt into a textured, UV-unwrapped GLB with PBR maps
(base color, metallic, roughness, normal, ambient occlusion). Everything runs on the GPU in
ComfyUI with no compiled CUDA extensions.

| Stage | Model | License | Notes |
|---|---|---|---|
| Text → reference image | Z-Image-Turbo (int8) + Qwen3-4B text encoder | Apache-2.0 | 8 steps, ~20 s at 1024² |
| Background removal | BiRefNet | MIT | |
| Camera estimation | MoGe-2 | MIT | gives Pixal3D the field of view |
| Image → shape + PBR texture | **Pixal3D** (default) or TRELLIS.2 (int8) | MIT / MIT | switch with `--model` |
| Post-processing | ComfyUI mesh nodes | GPL-3 (ComfyUI) | remesh, decimate, UV unwrap, bake |
| 2D sprite edit / pose / generation | **FLUX.2 [klein] 4B** (fp8) + the same Qwen3-4B encoder | Apache-2.0 | `sprite.bat`, 4 steps, ~30 s per sprite |
| Pixel art stills | FLUX.2 klein 4B + two **pixel-art LoRAs** (Limbicnation sprite LoRA, adirik scene LoRA), then grid + palette snapping | Apache-2.0 (LoRAs trained on CC0 / free-to-use pixel art) | `pixel.bat`, ~15 s per sprite, ~50 s per scene |
| Walk-cycle sprite sheets | FLUX.2 klein base 4B + svntax-dev's **4-walk sprite-sheet LoRA**: one 32 px character, 4x4 sheet (3 walk frames x 4 directions + 4 poses), snapped, keyed and split | Apache-2.0 (LoRA data CC-BY 4.0) | `pixel.bat --sheet`, ~1 min |
| Animated pixel art from a still | **Wan 2.2 I2V 14B** (4-bit GGUF, both experts) + lightx2v 4-step LoRAs + styly-agents' **pixel-animate LoRA**, BiRefNet per frame, frames re-pixelized with the still's palette (`setup_pixel_anim.ps1`, ~21 GB); `--engine wan5b` = the ai-vid 5B model without a pixel LoRA (weak) | Apache-2.0 | `pixel.bat --animate`, ~2 min warm, ~3.5 min cold |

Why these: the strongest-texturing open alternative, Hunyuan3D 2.x, is excluded for the EU in
its license and its paint stage needs ~21 GB VRAM. TRELLIS.2 upstream needs 24 GB and Linux,
but the ComfyUI native port uses int8 weights and RAM offloading, so it fits an 8 GB card.

## Setup on a fresh machine

```bash
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Clones ComfyUI at the pinned commit into `ai-3d/ComfyUI/` (git-ignored), creates the venv with uv,
installs CUDA 13 PyTorch, applies `patches/comfyui-turing-fp16.patch`, copies the workflows into
ComfyUI's sidebar and `custom_nodes/` into ComfyUI, and downloads ~35 GB of weights. `-SkipModels` skips
the download. With `CIVITAI_TOKEN` set in the environment it also fetches the optional texture LoRA.

## Layout

```
setup.ps1                     one-time install (ComfyUI, venv, patch, workflows, models)
setup_paint.ps1               optional: IP-Adapter nodes + the SDXL models paint.bat uses (~12 GB)
setup_pixel_anim.ps1          optional: ComfyUI-GGUF + Wan 2.2 14B GGUF, lightx2v and pixel-animate LoRAs for pixel.bat --animate (~21 GB)
patches/                      local ComfyUI patches applied by setup.ps1
custom_nodes/ai3d_seamless/   our ComfyUI nodes: seamless tiling for Z-Image (installed by setup.ps1)
custom_nodes/ai3d_project/    our ComfyUI node: projects the reference picture onto Pixal3D meshes (installed by setup.ps1)
start_comfyui.bat             start the server (http://127.0.0.1:8188)
generate.bat "prompt" [...]   command-line text → GLB
texture.bat "prompt" [...]    command-line text → seamless world texture PNG (walls, floors, ground)
sprite.bat "prompt" [...]     command-line 2D sprite edit / new pose from references / text → transparent PNG
pixel.bat "prompt" [...]      command-line pixel art (or --style illustration): text / sprite → PNG; --sheet → 4x4 walk-cycle sheet; --animate still.png → Wan 2.2 14B animation
paint.bat model.glb pic.png "prompt"   paint a modelled prop from its reference picture (SDXL from part-colour renders)
scripts/generate.py           the 3D CLI (queues the API workflow, waits, prints paths)
scripts/texture.py            the texture CLI
scripts/sprite.py             the 2D sprite CLI (FLUX.2 klein)
scripts/pixel.py              the pixel-art CLI: klein + LoRA stills, grid/palette snapping, 4x4 walk sheets, Wan 2.2 animation of a still
scripts/build_pixel_wan_workflow.py   regenerates workflows/pixel_wan14_api.json (Wan 2.2 14B GGUF + pixel-animate LoRA)
scripts/download_pixel_anim_models.py  model downloader used by setup_pixel_anim.ps1
scripts/prop_kit.py           Blender modelling helpers for props written as scripts (box, lathe, tube, export)
scripts/paint.py              the paint CLI: mask, camera fit, guides, one SDXL img2img per view, projection, FBX
scripts/paint_fit.py          finds the picture's camera by matching the model's silhouette to its mask
scripts/paint_guides.py       Blender: renders the model in its part colours + depth from every paint camera
scripts/paint_project.py      Blender: projects the painted views onto the model, bakes, locks plain parts, exports
scripts/download_paint_models.py   model downloader used by setup_paint.ps1
scripts/prop_model.py         runs Claude Code headless to model a prop from its picture (studio kind prop)
scripts/prop_brief.md         Claude's brief for that: conventions, the build + check loop, the paint answer
scripts/prop_check.py         builds a prop_kit script and writes check.png (picture + four clay views)
scripts/prop_views.py         Blender: the four clay views for prop_check.py
scripts/prop_style.py         loads the style guides in styles/ (picture words, modelling rules, paint settings)
styles/<id>.json + <id>.md    a style guide; inked = chunky hand-painted props with ink lines
scripts/build_texture_workflow.py   regenerates both text_to_texture workflow files from one graph
scripts/build_sprite_workflow.py    same for the sprite_edit workflow files (uses scripts/ui_export.py)
workflows/text_to_3d_gameasset.json        UI workflow, low-poly game-asset preset (in ComfyUI's sidebar)
workflows/text_to_3d_zimage_pixal3d.json   UI workflow, full-detail preset
workflows/text_to_texture.json             UI workflow, seamless hand-painted world texture
workflows/sprite_edit.json                 UI workflow, FLUX.2 klein sprite edit with up to 3 reference images (+ a bypassed LoRA node pixel.py uses)
workflows/pixel_wan14_api.json             API workflow, Wan 2.2 14B GGUF image-to-video with the pixel-animate LoRA (pixel.py --animate)
workflows/*_api.json                       API-format exports of the same workflows, used by the CLIs
scripts/glb_to_fbx.py         Blender script: GLB -> Unity-ready FBX + diffuse PNG
scripts/compare_render.py     Blender script: render a generated FBX next to a prop from your own .blend
scripts/download_models.py    model downloader used by setup.ps1
assets/                       generated FBX + PNG game assets
assets/textures/              generated world textures
assets/sprites/               generated RGBA sprites
assets/pixel/                 pixel art: <Name>.png stills, <Name>/ sprite-sheet and animation folders (sheet, gifs, json, frames)
ComfyUI/                      ComfyUI 0.42 + .venv (Python 3.12, torch 2.14 cu130), git-ignored
ComfyUI/models/               ~35 GB of weights (see below)
ComfyUI/output/3d/            generated GLB files (ComfyUI_00001.glb, ...)
ComfyUI/output/text_to_3d/    generated reference images
ComfyUI/output/texture/       generated textures (tile, preview2x2; raw and tile_repaint with --tile crossfade)
ComfyUI/output/sprite/        generated sprites and pixel-art pictures before snapping (raw = painted on white, rgba = cut out)
ComfyUI/output/paint/         paint.bat work folders: mask, camera fit, guides, painted views
assets/modelled/<Name>/       Claude's modelling folder per prop: <Name>.py, <Name>.glb (clay), reference.png, check_N.png, claude.jsonl
```

## Presets

| Preset | Target | Geometry | Textures | Output |
|---|---|---|---|---|
| `gameasset` (default) | stylised low-poly game props (~1–3k triangles) | shape res 1024, remesh 512 with QEF vertices (256 for perforated parts), decimated to `--tris` (default 3000, round 1500) with feature edges pinned (`--edges sharp`), hard edges > 60°, then Blender planar-dissolves coplanar triangles within `--dissolve` 10° so flat panels give their triangles back (a box lands near 1200, a chair near 2100) | one 1024² diffuse = the reference picture projected onto the faces the camera saw, the voxel-baked base color elsewhere (`--texture-from`), × AO 0.3 (round: × 0.45 with a 2 px blur), plus a 1024² normal map baked from the remeshed high-poly | GLB (diffuse + normal), plus `assets/<Name>.fbx` + `<Name>Diffuse.png` + `<Name>Normal.png` via Blender |
| `character` | a person or creature for `ai-anim/` | as `gameasset`, 4000-tri budget + dissolve; the prompt asks for a front-view T-pose | one 1024² diffuse + normal map | GLB + FBX + PNGs |
| `detailed` | hero / showcase models | shape res 1536, 700k tris | 4096² base color, metallic-roughness, normal, AO | GLB with full PBR |

The game-asset FBX has one material named after the asset using only the diffuse texture, is
centred with its base on Y=0 (Y-up, -Z forward) and scaled so its longest side is `--size`
metres (default 1.0). Drop the FBX and PNG into `Assets/3D/Art/Models` and `Textures`, then
assign the PNG as Base Map on a URP/Lit material like your other props.

## Usage

1. Run `start_comfyui.bat` and leave it open.
2. Either open http://127.0.0.1:8188, pick **text_to_3d_zimage_pixal3d** from the Workflows
   sidebar, edit the **PROMPT** node and press Run, or from a terminal:

```bash
generate.bat "an office desk chair with wheels" --name DeskChair --size 1.0 --tris 1200
```

```bash
generate.bat "a wooden treasure chest" --preset detailed
```

Options: `--preset gameasset|character|detailed`, `--shape hard|round` (round: fully smooth shading, finer
remesh, a 1500-tri default and no dissolve, for bottles, pots, balls), `--edges sharp|soft` (see below),
`--tris N` (decimation budget, default 3000 / round 1500 / character 4000), `--dissolve DEG` (planar dissolve
after decimation, default 10 / round 0, see below), `--no-normalmap`, `--normal-from remesh|raw` (which high-poly
the normal map is baked from), `--texture-from picture|voxels` (diffuse from the projected reference picture, default, or the voxel bake everywhere,
see below), `--blur PX` / `--ao F` (diffuse blur and AO strength, default 0 / 0.3, round 2 / 0.45),
`--remesh N` (remesh voxel resolution, default 512 / round 384; 256 for perforated parts), `--save-highpoly` (also writes the remeshed
high-poly as `output/3d/highpoly_*.glb`), `--seed N`, `--model pixal3d|trellis2`, `--name`, `--size` (metres),
`--out` (folder for FBX/PNG, default `assets/`), `--no-fbx`, `--resolution` / `--texture` (override the preset),
`--image-only` (just the reference picture), `--raw` (no style hints appended).

### Why game assets used to look melted, and the fix (`--edges`)

Pixal3D's surface is crisp (a filing cabinet comes out of the shape VAE with sharp corners and the drawer
grooves intact, ~280k triangles). The old post-processing threw that away twice: the narrow-band remesh ran
**20 Taubin smoothing passes** with QEF vertex placement off, and the decimator collapsed edges to their
**midpoint**, which pulls every corner inward until a box is a pillow at 1500 triangles. Same-seed A/B on the
cabinet (`scripts/render_row.py` renders variants side by side): less smoothing alone barely helps; QEM-optimal
vertex placement alone restores the box; QEF remesh + QEM with line quadrics on feature edges (dihedral > 30°)
keeps the panel grooves too; and a normal map baked from the remeshed high-poly into the low-poly UVs makes the
1.5k-triangle model read like the high-poly in the viewport. Remesh 512 instead of 256 added ~30 s and no
visible gain. So the defaults are now `--edges sharp` + normal map; `--edges soft` is the old behaviour, kept for
comparison. Round / organic props (`--shape round`) keep QEF off on the remesh (it makes jagged creases on
organic surfaces) but still use the QEM decimator and the normal map.

The normal map lands in the GLB (three.js / glTF viewers pick it up) and next to the FBX as `<Name>Normal.png`,
wired as the Normal Map in the FBX material; in Unity assign it as the Normal Map (mark the texture type
"Normal map") on the same URP/Lit material as the diffuse.

### Why small parts went missing, and the fix (`--tris` + `--dissolve`)

With the sharp pipeline a filing cabinet was fine, but an office chair at 1500 triangles had no castor wheels and
no lumbar pad, and the printer bot's feet were stubs. Same-seed A/Bs (`todo/ai-3d-improvements.md`) showed where they
go: the shape VAE's own mesh has them (5.5M triangles for the chair at the 1024 cascade; 1536 adds nothing visible
and costs 25 minutes on a big flat prop), the 256 remesh keeps them (256k triangles), and the QEM decimation
drops them, because small parts have a tiny quadric error and are collapsed first, whatever the feature-edge
weights (10 / 50 / 200 and 15° / 30° / 45° all look the same). Remesh 512, decimating the raw mesh, Blender's own
remeshers on the raw mesh and a normal map baked from the raw mesh do not help (details in the TODO).

What helps is the budget: at 3000 triangles the chair has its castors and lumbar pad. To keep flat props from
paying for that, `glb_to_fbx.py` then runs Blender's planar decimate: coplanar triangles within `--dissolve`
degrees (default 10, inside UV islands only, so the baked textures stay valid) merge into one face and are
re-triangulated, with the normals recomputed at the 60° crease. A cabinet decimated to 3000 lands at ~1250
triangles with crisp handles, the chair at ~2100 with wheels, the printer bot at ~2050. The GLB is rewritten
with the reduced mesh too. `--dissolve 0` turns it off, `--dissolve 5` gives slightly cleaner shading on very
large panels at ~40 % more triangles; round props skip it (nothing coplanar to merge). On hard-surface props
the diffuse also lost its 2 px blur and the AO multiply went from 0.45 to 0.3: the blur smeared the handles and
panel lines the normal map restores, and the AO darkened every recess into a smudge (`--blur`, `--ao`).

The remesh is now 512 by default (+15 s over 256): on a water cooler it turns the melted taps and bottle ribs of
the 256 remesh into three distinct taps and crisp ribs at the same budget, the single biggest step toward the
`detailed` look. Use `--remesh 256` on perforated parts such as a mesh chair back, where the 512 grid resolves the
holes and the decimator stalls on them. Not worth it, measured: `--resolution 1536` (same
low-poly, and a cabinet ran 120 s per upsample step), `--normal-from raw` (adds voxel grain on flat areas). Artist-topology models (MeshAnything, MeshFlow, BPT, QuadGPT) are out on licence
or have no weights; LATO.2 (MIT code, ~8 GB VRAM) is the one left to try.

### Why textures came out soft, and the fix (`--texture-from picture`)

The diffuse used to come only from the texture stage's voxels: TRELLIS.2's texture VAE predicts colours on a
1024 voxel grid and `BakeTextureFromVoxel` samples them, so labels, logos and outlines arrive blurred and dark
(a charcoal hole punch baked near-black). But Pixal3D is pixel-aligned: it builds the object in the frame of a
fixed eye-level camera (that is also why its voxel preview looks tilted: the three-quarter picture is shot from
above), so every surface point lands exactly on its pixel in the cropped reference picture.
`custom_nodes/ai3d_project` (`ProjectReferenceTexture`) uses that: it rasterizes the UV atlas, snaps each texel onto
the dense surface, projects it through the Pixal3D camera and takes the picture's colour where the surface is
visible (depth test against the low-poly) and faces the camera (full above cos 0.5, fading out to the bake at 0.2).
The hidden side keeps the voxel bake, shifted per colour towards what the picture shows for the same baked colour
(a smooth Gaussian-weighted average over visible texels with a similar baked colour), so a near-black bake becomes the picture's charcoal while
a white lid stays white. On a coffee cup with a frog logo and a cooler with a big "G" the logo, its outline and the
handles come out as crisp as the picture; the seam to the baked side does not show. TRELLIS.2 is not
pixel-aligned, so `--model trellis2` always uses the voxels. `--texture-from voxels` is the old bake.

### Modelled + painted props (`paint.bat`)

The closest match to hand-made low-poly props is to model them the same way: a Blender script that box-models
the prop from its reference picture (`scripts/prop_kit.py`: bevelled boxes, cylinders, lathed profiles, tubes along
a path, mirror; Claude writes these through the studio skill), then paint it. A desk comes out at ~900 triangles with
real drawers and a pedestal, a chair at ~1500, a coffee cup at ~500. `paint.bat` paints such a model from the picture:
it renders the model **in its own part colours** from every camera (`scripts/paint_guides.py`) and SDXL (RealVisXL 5 +
the 8-step Lightning LoRA) repaints each render (img2img, denoise 0.7) held to the model's depth (ControlNet 0.8) and
styled by the picture (IP-Adapter Plus, 0.6). Because every view starts from the same colours on the same shape, the
views agree; `scripts/paint_project.py` projects them onto the model and bakes one diffuse on a fresh UV unwrap.
~2-3 minutes per prop; `setup_paint.ps1` installs the IP-Adapter nodes and ~12 GB of models.

```bash
paint.bat Desk.glb reference.png "stylized 3D game prop, soft hand-painted texture, clean, pastel, wooden office desk | drawers with metal handles on the front | plain flat wooden back panel, no drawers" --low
```

- Model conventions: metres, Z up, standing on z = 0, front facing -Y (the side the picture shows), one material per
  part named after it (`Wood`, `Snow`, `Lid`) with its colour: the colours are where every view starts. A model with
  a single material gets a grey guide and denoise 0.85, so the picture decides more.
- Why not generate the views from noise: the first version did, with [StableGen](https://github.com/sakalond/StableGen)
  (SDXL, depth ControlNet 0.6, IP-Adapter). Every view drew its own version of the object (a second seat on a
  snowmobile, a wheeled track where the model has a solid tunnel), and projected together the views smeared: the
  snowmobile lost its track. Starting from the part-colour render keeps the layout, so the views can only add detail
  (track lugs, stripes, panel lines). It also needs no Blender window and no add-on.
- Cameras: the picture's own first, found by fitting the model's silhouette to the picture's BiRefNet mask
  (`scripts/paint_fit.py`), then five around at eye level and one from above, 30° FOV each. `--low` adds two views
  from below for props with undersides (chair stretchers, table frames); on other props they invented a second
  logo copied from the picture, so they are off by default.
- The prompt has three parts: `shared | front-only | other views`. Everything the picture shows on its side only (a
  logo, drawers) goes in the middle part, which only the picture's camera gets; with one prompt for every view the cup
  got a frog on every side and the lid, and the desk drawers on its back. The last part describes the plain look
  of the whole prop for the other views: told only "plain cardboard sleeve", the top view painted the lid as cardboard.
- `--lock Snow,Floor` (material names): plain parts keep the painted shading but take one colour, the part's median
  painted colour (or the modelled colour when that median strays). Fixes colour bleeding from a neighbour: the
  tent's snow came out mostly orange, the lantern's post glowed.
- Projection (`scripts/paint_project.py`): every texel's position and normal are baked, and a view colours a texel
  only where the texel's depth matches the view's depth map (hidden surfaces are skipped) and away from silhouette and
  depth edges (the picture bleeds background there); views are weighted by facing^6, the picture's own view 1.5x.
  Texels no view saw keep the part colour with baked ambient occlusion.
- `--denoise` (0.7): lower keeps the modelled colours, higher adds painted detail; `--depth` (0.8), `--ipadapter` (0.6).
- Outputs: `assets/<Name>.glb`, `<Name>Diffuse.png`, `<Name>.fbx`; the guides, views, mask and camera fit stay in
  `ComfyUI/output/paint/<Name>_<time>/`. In the studio: "Paint a modelled prop" on the 3D Models page.

From a prompt alone, the studio's **Text → modelled prop (Claude)** (job kind `prop`) runs the whole chain: Z-Image
draws the reference picture, Claude Code runs headless (`scripts/prop_model.py`, `claude -p` with your Claude Code
login) in `assets/modelled/<Name>/` with `scripts/prop_brief.md` as its brief, writes `<Name>.py` with prop_kit,
builds it and compares clay renders with the picture (`scripts/prop_check.py` writes `check.png`: the picture and
four clay views) for a few rounds, then answers with the paint prompts, the parts to lock and whether low views are
needed; the paint step above uses them. Claude may only write inside that folder, read files and run that one check
command. The snowmobile prompt took Claude 4 rounds in about 2 minutes (~$0.50 of usage on Opus). The model
defaults to `opus`; the CLI's own default can be one your plan has no credits for.

### Style guides (`styles/`)

A style guide makes the modelled-prop flow aim at one look across a whole set of props. It is a JSON file in
`styles/` (`<id>.json`, read by `scripts/prop_style.py`) plus the modelling rules Claude gets on top of
`prop_brief.md` (`<id>.md`), and it sets all three stages:

- `picture`: the style words after the prompt for the Z-Image reference picture (instead of the game-asset preset's).
- `brief`: the rules file: triangle budgets, which details to model and which to paint, proportions, colours.
- `paint`: the painter (`sdxl` or `klein`), its denoise (`denoise_other` for every view but the picture's),
  `guide_light`, prompt words for every view, and the `finish` that `paint_project.py` applies to the bake.

The studio's prop and paint forms have a Style menu; the API takes `style` on `prop` and `paint` jobs; the CLIs take
`--style <id>` (`paint.bat`, `scripts/prop_model.py`). No style is the pipeline described above.

**`inked`** (Inked hand-paint) was tuned against a set of hand-made low-poly game props: chunky, simple shapes at
12-250 triangles for small props and up to ~1800 for big soft furniture, detail in the texture instead of the mesh,
flat muted colours, soft airbrushed shading and dark ink lines along the hard edges. What it changes, and why:

- Modelling: keys, buttons, screens, labels, vents and drawer lines are painted, not modelled; hard objects keep
  sharp edges (they get the ink), soft parts are puffy and rounded. Claude places the painted details as **decals**
  (`prop_kit.decal`: flat coloured marks on a surface; materials named `Decal*`): they are in the guides, so every
  view paints the keys or the label in the same place, and `paint_project.py` drops them before the bake, so they
  cost no triangles. Repeating details are one decal per key or slot: with one big rectangle for "the keys", the
  keys painted as a flat block. A keyboard came out at 52 triangles with every key painted, a vending machine at 220.
- Guides with flat light (`guide_light: flat`): with the default studio light, every view started from shaded
  colours, the shading was baked into the diffuse and lit again in the engine; the test props' diffuses came out
  1.3-4x darker than the hand-made ones (an office chair's median 38 against 181).
- Painting with FLUX.2 klein 4B (the sprite flow's model) as img2img from the guide, the picture as image 2 for
  its own view only. RealVisXL kept painting glossy metal, realistic cans and leather grain whatever the prompt;
  klein paints flat, outlined colours. klein has no depth ControlNet, so the denoise holds the layout: 0.75 for the
  picture's view (silhouette overlap with the guide 0.95-0.99), 0.6 for the others; at 0.75 they painted a vending
  machine's window and buttons on its plain back, at 0.85 a back view turned into a second front. As a plain
  edit (no img2img) klein changed the camera of every view (overlap 0.41-0.93), which projection cannot use.
- Finish (`paint_project.py`): `tone` scales each part's painted brightness back to its modelled colour, `flatten`
  pulls small colour differences (view-to-view shading, photo texture) to the part's median and keeps big ones
  (labels, keys), `shade` adds soft painted shading from the baked AO and the height, `ink` draws dark lines
  (`ink` texels wide on each side, `ink_dark` x the local colour) along edges sharper than `ink_angle` and around
  the decals.

To make another style, copy `inked.json` and `inked.md`, change the words and numbers, and pick it by its file name.

#### A style from your own props (private)

With a .blend of your own hand-made props, a private style can learn from them directly. Everything it builds
stays out of git: the style in `styles/private/<id>/` and the reference library in `assets/style_refs/<id>/`.

1. `styles/private/<id>/refs.json`: `blend`, `out` (the library folder), `front` (the axis your props face, e.g.
   `+X`), `exclude` (name patterns of level pieces that are not props), `held_out` (props kept back for evaluation),
   `hidden` (near-copies of held-out ones) and `descriptions` (a JSON file of one-line descriptions per prop).
2. `blender -b your.blend --python scripts/style_refs.py -- export refs.json`, then
   `blender -b --python scripts/style_refs.py -- render refs.json`: every prop as a GLB turned to face -Y, its
   textures, stats (triangles, parts, UV islands) and renders (textured, flat-lit, clay, wireframe).
   `python scripts/style_index.py refs.json` writes `index.json` and the catalog sheets.
3. `styles/private/<id>/style.json` like `inked.json`, plus `"refs": "refs.json"`. The chain then:
   - picks your 3 props most like the request (`scripts/prop_refs.py`, a short text-only Claude call over the index)
     and stages their sheets in Claude's modelling folder, with the brief telling it to build the same way;
   - `"picture_from": "restyle"`: Z-Image draws the object, FLUX.2 klein redraws it in the style of your most
     similar prop. (`"refs"`, drawing from your props alone, copied them: a wheelie bin came out as the reference
     pedal bin, a stool as the reference kitchen chair);
   - `"paint": {"ref_views": "all"}`: your prop is image 2 of every klein view instead of the picture;
   - `"paint": {"scale": 1.8}`: the export is scaled to your scenes' units (props are modelled at real size).
4. `python scripts/style_eval.py run refs.json <style> <run>` generates the held-out props from their descriptions;
   `score refs.json <run> ...` renders them like the references and reports a "theirs or generated?" probe on CLIP
   ViT-H embeddings (0.5 = indistinguishable), triangle and size ratios and texture stats, with a side-by-side sheet
   and a shuffled blind lineup; `fit refs.json <run> '<finish json>' ...` re-bakes a run with other finish settings
   and compares the texture stats with your props' (`"saturation"` below 1 mutes the palette), and `rebake` saves
   one such finish as a new run to score.
   On a test library of 70 props with 12 held out, the public `inked` style scored probe 0.88, triangles 1.68x and
   size 0.56x of the originals; a private style with the reference props, restyled pictures, reference views and
   a 1.8 scale scored probe 0.68, triangles 1.23x, size 0.99x. What still gives the generated props away is
   content: hand-drawn labels, logos and text in the originals' textures.
5. `scripts/style_lora_data.py` renders LoRA training pairs from the library (a guide in flat colour regions taken
   from your texture -> your painted prop, same camera), for an edit LoRA that teaches klein your painting. Train it
   with musubi-tuner (SETUP.md section 7) on FLUX.2 klein base 4B, copy the result to `ComfyUI/models/loras/` and
   name it in the style: `"paint": {"lora": {"file": "<name>.safetensors", "strength": 1.0}}` (`"picture_lora"`
   does the same for the picture). `style_eval.py run ... --from <run>` repaints an earlier run's models with it,
   to measure the LoRA on its own. On the test library, a rank-16 LoRA (560 pairs at 512 px, 1600 steps, 5.4 h on
   an RTX 2080) brought the texture brightness and flatness to within 6 levels and 0.01 of the originals' (from 12
   and 0.05) but left the probe where it was (0.69 vs 0.68): what still tells the props apart is their design and
   the hand-drawn graphics, which come from the picture and the modelling, not the paint step.
6. A picture LoRA: `style_lora_data.py --picture` renders your props from three-quarter views under studio light,
   captioned with the picture prompt (`prop_chain.PICTURE_CAPTION`); train it the same way without control images
   and set `"picture_from": "lora", "picture_lora": {...}`: klein then draws the reference picture from text alone.
   On the test library (336 pictures, 1200 steps, 2.5 h) the probe went from 0.68 to 0.62-0.64 and some designs came
   out like the originals (a 2x2 cube shelf, a full keyboard slab, a plain whiteboard); the pictures are as dark as
   the shaded renders it learned from, so the finish's `"value"` (the originals' median brightness, 192 there)
   brings the textures back up. Some designs still follow the prompt rather than the artist (a cone-shaped wet
   floor sign came out as an A-frame).
7. How far the numbers carry: `score` prints the probe's 90 % range and "chance" (what random labels reach at this
   many props), and `floor` scores the originals against the artist's own most similar other props. With 12 held
   out, the floor was 0.64 and chance up to 0.62-0.67, so every private-style run above (0.62-0.69) was already
   at the noise level; only the public style (0.88) stood clearly above it. Past that point, judge by the pair
   score (similarity to the original made for the same prompt) and by eye, or hold out more props.
   `"picture_candidates": N` draws N pictures and lets Claude keep the one that shows the object with all its
   parts and looks most like the reference props (`prop_refs.pick_picture`; `"picture_pick": "clip"` ranks them by
   CLIP similarity to the library instead, which did not track the style: the public style scored best on it). On
   the test library best-of-4 left the pair score where it was (0.738 vs 0.739): it fixed a bin without wheels and
   picked worse colours or designs for others, so it is off by default.

Prompt tips: describe one object, not a scene. Each preset appends its own style hints (the
game-asset preset asks for a smooth, rounded low-poly prop with clean hand-painted colours on a plain background)
unless `--raw`. The reference image drives the look of the 3D result, so check
`output/text_to_3d/reference_*.png` first when a result is off.

UI workflows: **text_to_3d_gameasset** and **text_to_3d_zimage_pixal3d** in the Workflows sidebar.

## World textures (walls, floors, ground)

Textures for the level geometry are not baked from a mesh; they come from a separate Z-Image-Turbo
workflow that mimics a classic hand-painted game-texture look (flat matte colour fills,
grainy speckle mottling, a few brush smudges and spatter dots, no lighting). ~20 s per 1024² texture,
~50 s for 1024×2048.

```bash
texture.bat "dark oak parquet floor, long horizontal wooden planks in staggered rows with thin dark seams, warm orange-brown wood" --name ParquetFloorDark
```

```bash
texture.bat "plain cream yellow painted plaster wall, smooth" --name PlasterWall --height 2048 --tile crossfade
```

The result is written to `assets/textures/<Name>.png` (sRGB, Unity "Default" texture type, wrap mode
Repeat). Options: `--width` / `--height` (multiples of 64, default 1024²), `--seed N`, `--steps`
(default 14 native, 8 cross-fade), `--tile native|crossfade|none`, `--lora STRENGTH` (0 = off, see below), `--band` / `--denoise` (cross-fade only),
`--reference picture.png --strength 0.6` (img2img from an existing texture: 0.3 keeps the layout, 0.8 only the palette),
`--out`, `--raw`, `--dump`.

How it tiles (`--tile native`, the default): `custom_nodes/ai3d_seamless` wraps the model so the latent is
rolled by a different pseudo-random offset on every sampling step (a different wrap point each time, so no
fixed seam can form, with offsets in whole 2×2 DiT patches and an amplitude that shrinks with the noise
level), and the VAE decodes with circular convolution padding so the border pixels continue into each
other. The picture is periodic by construction: plank rows, tile grids and brick courses line up across
the borders, which no post-process can do. The method is adapted from Oliver Crosby's
ComfyUI-Universal-Seamless-Tiles (MIT); the usual Conv2d circular-padding trick does nothing for DiT
models like Z-Image. Cost: a few more steps than Z-Image's usual 8 (14 by default) for clean seams.

`--tile crossfade` is the older post-process: the picture is rolled by half its size in x and y, which puts
the old borders in a cross through the middle, and the original image's centre (continuous there) is
cross-faded over that cross. Use it for plain walls and other near-flat materials: the native roll can leave
very faint straight lines on those, while the cross-fade is invisible on them. It cannot align plank or tile
grids; `--denoise 0.4` runs a masked repaint over the band to hide the ghosting, at the cost of occasional
smudges.

Optional LoRA (`--lora 0.8`): `ComfyUI/models/loras/StylizedTexture_ZIT.safetensors` is Civitai model 503877,
"Stylized Textures (Game Asset) [ZIT]", trained on hand-painted game textures for Z-Image-Turbo (commercial use
of the images allowed). Off by default. Tested on the same seeds: it turns the sparse grass dashes into a dense
stroke field close to the hand-painted original (the biggest gain), gives glazed tiles a cleaner painted look,
but adds scratch marks and stronger roll lines to plain plaster and makes parquet ignore "long horizontal
planks in staggered rows" at every strength (0.5, 0.8, 1.0, also with the LoRA's own trigger phrasing). Use it
for grass, fabric and tiles; 1.0 turns grass into vertical hair. Civitai downloads need an API key: put it in
`CIVITAI_TOKEN` and run `scripts/download_models.py`.

Prompt tips: name the material, colour and layout ("long horizontal planks in staggered rows", "short loop
pile fabric", "seen from above"); the style hints are appended automatically. Do not ask for outlines or
seams in general terms, the model then draws a tile grid; say "thin dark seams between planks" instead.
"Grass" makes Z-Image paint tufts and bushes; describe the look instead, e.g. "flat olive green background
completely covered with thousands of tiny thin dry-brush dashes in lime, mid and sage green in random
directions", and add `--lora 0.8` for a dense stroke field.
Check `ComfyUI/output/texture/preview2x2_*.png` to judge the repeat before copying the texture into
`Assets/3D/Art/Textures`.

UI workflow: **text_to_texture** in the Workflows sidebar is the native pipeline with the LoRA node bypassed
(enable it once the file is installed). It is generated from `scripts/build_texture_workflow.py` (with the
server running), so edit that script rather than the JSON when changing the graph; the API export also
carries the cross-fade branch for the CLI.

## 2D sprites (characters, poses, shading passes)

Sprites are edited and generated by **FLUX.2 [klein] 4B** (Black Forest Labs, Apache-2.0), a 4-billion-parameter
rectified-flow model that does text-to-image and multi-reference image editing in one network. ComfyUI runs it
natively (`Flux2` model family, fp16 on Turing, fp8 weights dequantised on the fly) with the Qwen3-4B text encoder
already installed for Z-Image. Both klein checkpoints are used: the step-distilled one (4 steps, CFG 1, ~6 s of
sampling, ~30 s per sprite including loads) and the base one (20 steps, CFG 5, ~2 min, follows long prompts more
closely; ~3 min with two references). Each run is ~1 MP; outputs are cut out by BiRefNet and saved as RGBA PNG.

Why this model: Qwen-Image-Edit-2511 (Apache-2.0) edits a little better but is 20B parameters, so on 8 GB it only
runs as a 3-bit GGUF with RAM offloading at several minutes per image. Qwen-Image-2.1 is research-licensed, FLUX.2
[dev] and klein 9B are non-commercial, FLUX 3 Image Klein (Oct 2026) is gated behind the "Kommunity" license
(free under $5M revenue) and bf16-only in ComfyUI (fp32 fallback on the 2080), and Z-Image-Edit is unreleased.

```bash
sprite.bat "Improve the shading of this character: keep the exact same pose, proportions, outfit, line art and colours, add clean cel shading with a second darker tone and soft highlights on the hair, clothes and face" --image refs\hero_front_idle.png --alpha keep --name HeroFrontIdleShaded
```

```bash
sprite.bat "The character from image 1 sitting down on the office chair from image 2, seen from the front in a southeast isometric three-quarter view, hands resting on her knees, same character, same outfit and colours" --image refs\hero_front_idle.png --ref refs\office_chair.png --name HeroSitFront --width 896 --height 1488
```

```bash
sprite.bat "a short round office robot on one wheel with a single big eye" --name OfficeBot
```

The result is written to `assets/sprites/<Name>.png` (RGBA, Unity "Sprite (2D and UI)" import). Options: `--image`
(source sprite = "image 1" in the prompt; omit it to draw from text alone), `--ref` (up to two more references =
"image 2", "image 3"), `--model distilled|base`, `--steps`, `--cfg` (base only), `--negative` (base only),
`--seed N`, `--width`/`--height` (output canvas, multiples of 16; default = source size at 1 MP, or 768×1024 for
text-to-image; give a bigger canvas when a new pose needs room, e.g. sitting), `--megapixels` (working resolution
of the references), `--bg` (colour the transparent inputs are flattened on, default white), `--alpha auto|keep|none`
(BiRefNet cut-out; reuse the source alpha for shading/colour passes that keep the silhouette; or opaque),
`--match-source` (resize back to the source's pixel size), `--out`, `--raw`, `--dump`.

Prompt tips: write what the finished sprite shows, not just the change, and say what must stay ("same character,
same outfit and colours", "keep the exact same pose and line art"). Refer to references as "image 1", "image 2".
The distilled model drifts on colours (near-black hair came back dark brown, dark coloured trousers
blue-grey) and makes the face a touch more anime; `--model base` (173 s for a two-reference edit on the 2080) kept
the exact colours, the rounder face and the whole figure in frame, so iterate on prompts with the distilled model
and render final frames with `--model base`. The sitting pose, chair and line style came through on the first try
with both. Style hints (cel-shaded, dark ink outlines, flat colours, white background, no ground
shadow) are appended unless `--raw`. If a limb is clipped, the canvas is the source's aspect: pass a wider
`--width`/`--height`. BiRefNet occasionally keeps a sliver of white around thin lines; `--alpha keep` is exact
when the silhouette did not change.

UI workflow: **sprite_edit** in the Workflows sidebar (SOURCE = image 1, REF 2/3 bypassed until used). It is
generated by `scripts/build_sprite_workflow.py`, so edit that script rather than the JSON.

## Pixel art and illustrated sprites (stills, sprite sheets, animation)

`pixel.bat` makes real pixel art: a PNG at its actual pixel size (64×64 for a character, 128×96 for a room), with
every pixel on the grid and at most N colours, plus animated sprite sheets from a still. `--style illustration`
runs the same still and animation flows for clean digitally illustrated sprites instead (see the end of this section).

**Stills.** The same FLUX.2 [klein] 4B as the sprite flow draws the picture, with one of two pixel-art LoRAs
(both Apache-2.0, ~350 MB each, fetched by `scripts/download_models.py`): `sprite` = Limbicnation's character LoRA
on the distilled model (trained on 1,010 CC0 sprites at 512², 4 steps), `scene` = adirik's LoRA on the base
model (463 free-to-use 64×64 scenes and objects upsampled to 512², trigger `PXART4`, 20 steps with CFG). The
canvas is a whole number of blocks (64 px art at ×8 = 512², 128×96 at ×4 = 512×384), characters are cut out by
BiRefNet, and then the picture is **snapped**: one sample per block at the grid offset that best explains the
diffusion output (its own pixel grid is never quite aligned with ours; `--sample box` averages instead), alpha
thresholded, and the opaque colours quantized to a palette (`--colors`, default 32: Pillow median cut refined with
k-means, no dithering). The palette and size are written into the PNG (`pixel` text chunk) so the animation step
can reuse them. `--kind none` draws without a LoRA for comparison.

```bash
pixel.bat "a knight in blue armour with a red plume on his helmet, holding a sword, idle pose, side view" --name Knight
```

```bash
pixel.bat "the character from image 1" --image assets\sprites\Hero.png --width 48 --name HeroPixel
```

```bash
pixel.bat "a cosy medieval tavern interior with a stone fireplace, wooden tables and barrels" --kind scene --width 128 --height 96 --name Tavern
```

Results go to `assets/pixel/<Name>.png`. Options: `--kind sprite|scene|none`, `--width`/`--height` (art pixels;
default 64², scene 128², or the source's aspect), `--colors` (0 keeps all), `--sample center|box`, `--scale`
(block size; default auto ≈ 512 px canvas), `--model auto|distilled|base`, `--lora-strength`, `--alpha auto|keep|none`
(default: cut out for sprites, opaque for scenes), `--steps`, `--cfg`, `--negative`, `--seed`, `--raw`, `--dump`.
View the PNG with nearest-neighbour scaling (the studio does; in Unity set the sprite's filter mode to Point).
Measured on the 2080: a 64² sprite in ~15 s warm (65 s with the model load), a 128×96 scene in ~50 s. The
distilled model drifts colours when redrawing an existing sprite (a dusty green bush came back bright green):
name the colours in the prompt or use `--model base`.

**Walk-cycle sprite sheets** (`--sheet`). The reliable way to animated pixel characters on this card is to draw
the whole animation as one picture: svntax-dev's 4-walk LoRA (Apache-2.0, trained on sheets built from George
Bailey's CC-BY 16x16 template) makes FLUX.2 klein *base* draw a 512² sheet of one 32 px character, a 4×4 grid
of 3 walk frames facing down / left / right / up plus arms up, jump left, jump right and lying down. Because every
frame comes out of the same image, the character is consistent across all 16 cells, which no video model managed
here. The sheet is snapped to 128×128 (×4 blocks, the same grid-offset search as stills), the background is
flood-filled to transparent from the edges (enclosed background-coloured pixels such as eyes stay), and it is split
into named cells with a GIF per walk cycle (`0-1-2-1`).

```bash
pixel.bat --sheet "a medieval knight wearing blue metal armor and a helmet with a red plume, holding a sword" --name Knight
```

```bash
pixel.bat --sheet --image assets\pixel\Knight.png "a knight" --name KnightSheet
```

Outputs in `assets/pixel/<Name>/`: `<Name>_sheet.png` (128², RGBA), `frames/<Name>_<cell>.png` (`down_0..2`, `arms_up`,
`left_0..2`, `jump_left`, `right_0..2`, `jump_right`, `up_0..2`, `lie_down`), `<Name>_walk_<dir>.gif`, `<Name>.json`
(cell map, animations, palette, seed) and `<Name>_raw.png`. Options: `--image` (a pixel still, a sprite, any
picture = image 1; the description alone keeps details such as a plume better, a picture keeps the overall look;
the LoRA leans humanoid: a slime came back as a green person), `--colors`, `--fps` (GIFs, default 6), `--seed`,
`--steps` (20), `--cfg` (5), `--lora-strength`, `--raw`. About 60 s on the 2080. Limits: 32 px characters only
(the LoRA's training size), three walk frames per direction, occasional cut-off hats or inconsistent back views
(the author's own caveats); regenerate with another seed when a row is off.

**Animating a still with a video model** (`--animate`). `--animate still.png "what moves"` hands the still to an
image-to-video model and re-pixelizes the result. The engine that works is **`wan14`** (default when installed):
Wan 2.2 I2V A14B, both experts as Q4_K_S GGUF files through city96's ComfyUI-GGUF node, the lightx2v 4-step
distillation LoRAs and styly-agents' pixel-animate LoRA (rank 256, trained on 226 pixel sprite animations: idles,
walks, attacks, spell and explosion effects; Apache-2.0), wired as that LoRA's own workflow: shift 5, 2 + 2 steps,
CFG 1, ddim. `setup_pixel_anim.ps1` installs the node and fetches the ~21 GB. The still is enlarged nearest-neighbour
(the LoRA saw real pixel animations) and used as the first frame; there is no end frame. Measured on the 2080:
33 frames at 512² in 206 s and 45 frames in 198 s, peak 7.7 GB VRAM; the time goes into streaming the two 8.7 GB
experts from RAM, so warm runs are no faster. The frames come back as crisp pixel art in the still's colours with
no flicker, and the character stays the same; the motion is modest (with a walk-cycle prompt and 45 frames the
knight stepped his legs and swung the sword, short of a full stride), which is why walk cycles belong to `--sheet`
and `--animate` suits idles, effects and small gestures. `--engine wan5b` keeps the earlier route (Wan 2.2 TI2V-5B from ai-vid, no pixel LoRA,
Lanczos-upscaled still, optional `--loop pin|pingpong`); it flickered and drifted in every test and is only kept
for comparison. Every decoded frame is cut out by BiRefNet, then `--frames`
(8) frames are picked evenly from the clip and re-pixelized at the still's size with the still's own palette, so
all frames share one grid and one set of colours (no flicker). Outputs in `assets/pixel/<Name>/`: `<Name>_sheet.png`
(frames side by side), `<Name>.gif` (preview, `--preview-scale` 4), `<Name>.json` (frame size, count, `--fps`,
palette, seed), `frames/<Name>_NN.png` and `<Name>_wan.mp4` (the raw clip, for judging the motion).

```bash
pixel.bat --animate assets\pixel\Knight.png "the knight walks in place facing right, legs stepping, arms swinging" --frames 8 --fps 8 --seconds 1.5 --name KnightWalk
```

`--seconds` (wan14 default 1.33 = 33 frames; 4k+1 frames at 24 fps) is the clip the `--frames` are picked from.
Outputs as before: `<Name>_sheet.png` (frames side by side), `<Name>.gif`, `<Name>.json`, `frames/` and
`<Name>_wan.mp4` (the raw clip: look at it before judging the frames). Any PNG can be animated: a big picture
is pixelized first (`--width`/`--height`/`--colors`). Other options: `--lora-strength` (pixel-animate LoRA),
`--steps` (4), `--cfg` (1), `--upscale nearest|smooth`, `--negative`, `--seed`.

**Illustration style** (`--style illustration`, the **Style** switch at the top of the studio page). The same two
flows without the pixel look: the still is klein without a LoRA at the size you ask for (`--width`/`--height`,
default 512² for a sprite, 1024×768 for a scene, multiples of 16 up to ~1 MP), prompted as a clean digital
illustration with soft cel shading, cut out to RGBA, and saved as it is (no grid, no palette). `--animate`
runs Wan 2.2 14B without the pixel-animate LoRA (just the 4-step distillation), with the picture fitted into
512 px, and keeps the cut-out frames at that size: strip, frames, a 255-colour GIF preview, JSON. Same times
as the pixel style. The walk-cycle sheet is pixel-only (its LoRA knows 32 px characters). Measured: an
illustrated knight at 512² in ~30 s, its 33-frame walk in ~4 min; the motion stays modest here too.

The studio's **Pixel art** page wraps all three (`kind pixel`, `pixel_sheet` and, folded away below them,
`pixel_anim` with an engine switch; a still's **Walk-cycle sheet** button fills the sheet form with it) and a
**Style** switch (pixel art / illustration) that both the still and the animate form read.

## Local ComfyUI patches (needed on Turing / RTX 20-series)

Two small edits in `ComfyUI/`, kept as `patches/comfyui-turing-fp16.patch` and applied by setup.ps1,
both because the 2080 has no bf16:

- `comfy/supported_models.py`: TRELLIS.2 allows fp16 inference. Without it ComfyUI falls back
  to fp32, which ran at ~7 minutes per sampling step instead of under a second.
- `comfy/ldm/trellis2/model.py`: the sparse transformer keeps its residual stream and layer
  norms in fp32 when running fp16, because the residual exceeds the fp16 maximum (65504) in
  late blocks and produced all-NaN latents. Attention and MLP still run in fp16.

After updating ComfyUI, re-apply the patch with `git -C ComfyUI apply ../patches/comfyui-turing-fp16.patch`. On an Ampere or newer
card (RTX 30/40/50) neither patch is needed; bf16 is used natively.

First full run (Pixal3D, 1536 res, 4096 texture): 24.6 minutes end to end, peak ~6.7 GB VRAM.
Output was 699k triangles, 50 MB GLB with base color, metallic-roughness, normal and occlusion maps.

## Checking a result against your existing props

```bash
"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" -b path\to\your_props.blend --python scripts\compare_render.py -- assets\OfficeChairGen.fbx DeskChair compare.png
```

Renders the generated prop (scaled to the reference height) beside the named object from
your .blend with the same lighting. Game-asset runs take about 3 minutes each on the 2080.

```bash
"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" -b --python scripts\render_row.py -- out\row a.glb b.glb c.glb
```

Renders several GLBs side by side (`row_tex.png` textured, `row_clay.png` clay with cavity shading) and prints
their triangle counts: the tool for same-seed A/Bs of post-processing settings. ComfyUI keeps the shape stages of a
seed in RAM, so variants of one seed re-run in 60-90 s; mixing seeds in one batch evicts the cache.

## Running out of VRAM

Lower `--resolution 1024` first, then `--texture 2048`. In the UI the same knobs are the
`Trellis2UpsampleStage` target_resolution and the "Texture Resolution" primitive.

## Model files

Downloaded from the Comfy-Org repackaged repos on Hugging Face into `ComfyUI/models/`:

- `diffusion_models/pixal3d_int8_convrot.safetensors` (5.6 GB), `trellis_2_int8_convrot.safetensors` (5.3 GB), `z_image_turbo_int8_convrot.safetensors` (6.2 GB)
- `diffusion_models/flux-2-klein-4b-fp8.safetensors` (3.9 GB, distilled) and `flux-2-klein-base-4b-fp8.safetensors` (3.9 GB), from the black-forest-labs fp8 repos; `vae/flux2-vae.safetensors` (336 MB)
- `text_encoders/qwen_3_4b_fp8_mixed.safetensors` (5.6 GB)
- `vae/trellis_2_shape_vae_bf16.safetensors`, `vae/trellis_2_texture_vae_bf16.safetensors`, `vae/z_image_ae.safetensors`
- `clip_vision/dino_v3_L_naf_fp32.safetensors`
- `background_removal/birefnet.safetensors`
- `geometry_estimation/moge_2_vitl_normal_fp16.safetensors`
- `loras/pixel_sprite_klein4b.safetensors` (310 MB, Limbicnation/pixel-art-lora), `loras/pixel_scene_klein4b.safetensors` (353 MB, adirik/pixel-art-lora-flux.2-klein-4B) and `loras/pixel_4walk_klein4b_base.safetensors` (88 MB, svntax-dev/pixel_spritesheet_4walk_small_lora_v1), all Apache-2.0, for `pixel.bat`
- optional, from Civitai with `CIVITAI_TOKEN`: `loras/StylizedTexture_ZIT.safetensors` (172 MB)

## Updating

```bash
cd ComfyUI && git pull && uv pip install --python .venv/Scripts/python.exe -r requirements.txt
```
