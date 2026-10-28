# ai-3d: text → game-ready 3D props, world textures and 2D sprites (local, RTX 2080 / 8 GB)

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
patches/                      local ComfyUI patches applied by setup.ps1
custom_nodes/ai3d_seamless/   our ComfyUI nodes: seamless tiling for Z-Image (installed by setup.ps1)
start_comfyui.bat             start the server (http://127.0.0.1:8188)
generate.bat "prompt" [...]   command-line text → GLB
texture.bat "prompt" [...]    command-line text → seamless world texture PNG (walls, floors, ground)
sprite.bat "prompt" [...]     command-line 2D sprite edit / new pose from references / text → transparent PNG
scripts/generate.py           the 3D CLI (queues the API workflow, waits, prints paths)
scripts/texture.py            the texture CLI
scripts/sprite.py             the 2D sprite CLI (FLUX.2 klein)
scripts/build_texture_workflow.py   regenerates both text_to_texture workflow files from one graph
scripts/build_sprite_workflow.py    same for the sprite_edit workflow files (uses scripts/ui_export.py)
workflows/text_to_3d_gameasset.json        UI workflow, low-poly game-asset preset (in ComfyUI's sidebar)
workflows/text_to_3d_zimage_pixal3d.json   UI workflow, full-detail preset
workflows/text_to_texture.json             UI workflow, seamless hand-painted world texture
workflows/sprite_edit.json                 UI workflow, FLUX.2 klein sprite edit with up to 3 reference images
workflows/*_api.json                       API-format exports of the same workflows, used by the CLIs
scripts/glb_to_fbx.py         Blender script: GLB -> Unity-ready FBX + diffuse PNG
scripts/compare_render.py     Blender script: render a generated FBX next to a prop from your own .blend
scripts/download_models.py    model downloader used by setup.ps1
assets/                       generated FBX + PNG game assets
assets/textures/              generated world textures
assets/sprites/               generated RGBA sprites
ComfyUI/                      ComfyUI 0.42 + .venv (Python 3.12, torch 2.14 cu130), git-ignored
ComfyUI/models/               ~35 GB of weights (see below)
ComfyUI/output/3d/            generated GLB files (ComfyUI_00001.glb, ...)
ComfyUI/output/text_to_3d/    generated reference images
ComfyUI/output/texture/       generated textures (tile, preview2x2; raw and tile_repaint with --tile crossfade)
ComfyUI/output/sprite/        generated sprites (raw = painted on white, rgba = cut out)
```

## Presets

| Preset | Target | Geometry | Textures | Output |
|---|---|---|---|---|
| `gameasset` (default) | stylised low-poly game props (~1–3k triangles) | shape res 1024, remesh 512 with QEF vertices (256 for perforated parts), decimated to `--tris` (default 3000, round 1500) with feature edges pinned (`--edges sharp`), hard edges > 60°, then Blender planar-dissolves coplanar triangles within `--dissolve` 10° so flat panels give their triangles back (a box lands near 1200, a chair near 2100) | one 1024² diffuse = baked base color × AO 0.3 (round: × 0.45 with a 2 px blur), plus a 1024² normal map baked from the remeshed high-poly | GLB (diffuse + normal), plus `assets/<Name>.fbx` + `<Name>Diffuse.png` + `<Name>Normal.png` via Blender |
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
the normal map is baked from), `--blur PX` / `--ao F` (diffuse blur and AO strength, default 0 / 0.3, round 2 / 0.45),
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
- optional, from Civitai with `CIVITAI_TOKEN`: `loras/StylizedTexture_ZIT.safetensors` (172 MB)

## Updating

```bash
cd ComfyUI && git pull && uv pip install --python .venv/Scripts/python.exe -r requirements.txt
```
