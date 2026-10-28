# ai-anim: rig a 3D character and animate it from text (local, RTX 2080 / 8 GB)

Turns a humanoid mesh (for example a `--preset character` run from `ai-3d`) into a rigged, skinned character with
the standard Mixamo skeleton, then generates animation clips from text and bakes them into the same GLB / FBX.

| Stage | Model | License | Notes |
|---|---|---|---|
| Mesh → skeleton, skin weights, rest pose | **Make-It-Animatable** (CVPR 2025) | MIT | 52-bone Mixamo skeleton with fingers, ~3 s on the 2080; humanoids only |
| Text → motion | **NVIDIA Kimodo-SOMA-RP v1.2** | code Apache-2.0, weights NVIDIA Open Model License (commercial use allowed) | 282M-parameter diffusion model, 30 fps, joint rotations on the SOMA skeleton; ~10–30 s per clip |
| Text encoder for Kimodo | LLM2Vec on Llama-3-8B-Instruct | Meta Llama 3 Community License | runs on the CPU (16 GB RAM) so the GPU only holds the denoiser; the Llama repo is **gated** on Hugging Face |
| Rig build, retargeting, export | Blender 5.x (headless) | GPL | GLB with glTF animations, FBX with one take per clip, `.blend` kept for re-animation |

Why these: the strongest open rigging models either need compiled CUDA extensions that do not build on
Windows/Turing (UniRig: spconv + flash-attn; Puppeteer, MagicArticulate) or carry licenses that exclude the EU
(Tencent Hunyuan3D-Omni / HY-Motion). Make-It-Animatable is pure PyTorch once its three small dependencies are
replaced (see below) and outputs the Mixamo skeleton, which Unity's Humanoid avatar maps automatically and which
every Mixamo animation also fits. For motion, NVIDIA's ARDY is Linux-only, MoMask only outputs joint positions
and is trained on non-commercial data; Kimodo outputs rotations, exports BVH itself and its weights are cleared
for commercial use.

## Setup on a fresh machine

```bash
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Clones Make-It-Animatable and Kimodo at the commits in `pins.txt` (git-ignored `mia/`, `kimodo/`), creates the venv
with uv (Python 3.12, torch cu130), installs `requirements.txt` and downloads the Make-It-Animatable weights
(~2.2 GB). Kimodo's weights (~1.2 GB) and the text encoder (~16 GB) are pulled into the Hugging Face cache on the
first animation. Before that first animation:

1. Request access to [meta-llama/Meta-Llama-3-8B-Instruct](https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct) (usually granted within minutes).
2. `hf auth login` with a read token (or paste the token into `~/.cache/huggingface/token`).
3. Optionally `python scripts\download_models.py --kimodo` to download everything now instead of during the first clip.

Rigging works without the token. Blender 5.x must be installed (`BLENDER` env var, default
`C:\Program Files\Blender Foundation\Blender 5.1\blender.exe`).

Neither upstream repo is pip-installed: Kimodo's `setup.py` builds a C++ extension (foot-skate post-processing,
needs CMake + MSVC; the pipeline runs with post-processing off) and Make-It-Animatable needs `pytorch3d`,
`torch_cluster` and `bpy`, which `vendor/` replaces: a copy of pytorch3d's pure-Python `transforms` package
(BSD), a plain-PyTorch farthest-point sampling in place of `torch_cluster.fps`, and `scripts/mia_compat.py`
that loads the dataset module without Blender and builds the Mixamo hierarchy from a table instead of the
gated template FBX.

## Layout

```
setup.ps1                     one-time install (clones, venv, deps, weights); pins.txt holds the commits
animate.bat                   command line: rig | anim | full | motion
scripts/animate.py            the pipeline: rig_character(), animate_character() (also used by the studio)
scripts/rig_infer.py          Make-It-Animatable inference: mesh -> rig.npz (joints, tails, weights, to-rest pose)
scripts/mia_compat.py         loads MIA's code without bpy / pytorch3d / torch_cluster / the Mixamo template
scripts/motion.py             Kimodo: prompt -> .npz + SOMA-77 BVH (T-pose rest, 30 fps); also npz -> bvh
scripts/anim_worker.py        persistent JSON-lines worker (rig + motion) used by the studio
scripts/blender_rig.py        Blender: build the armature, skin the original textured mesh, T-pose, scale, export
scripts/blender_animate.py    Blender: retarget BVH clips onto the rig, export GLB (animations) + FBX (takes)
scripts/blender_preview.py    Blender: render a GLB / one frame of a clip to PNG (front, side, three-quarter)
scripts/download_models.py    weights downloader
vendor/pytorch3d, vendor/torch_cluster   pure-Python replacements (see above)
assets/<Name>/                rigged characters: <Name>.glb, <Name>.fbx, <Name>Diffuse.png, <Name>.blend,
                              rig.npz, source.glb, clips/<Clip>.bvh + .npz, character.json
mia/, kimodo/, .venv/         clones and the environment (git-ignored)
```

## Usage

```bash
animate.bat full ..\ai-3d\assets\Hero.glb --name Hero --clip "Idle=A person stands still, breathing calmly and shifting their weight" --clip "Walk=A person walks forward at a relaxed pace" --seconds 4
```

```bash
animate.bat rig ..\ai-3d\assets\Hero.glb --name Hero --height 1.75        # rig only, check the T-pose
animate.bat anim Hero --clip "Wave=A person waves hello with the right hand" --seconds 3 --seed 7
animate.bat motion "A person jumps over an obstacle" --seconds 4 --out output\clips\Jump   # BVH only
```

Options: `--height` (metres, default 1.8), `--no-fingers` (fold finger weights into the hands for mitten-like
low-poly hands), `--keep-pose` (do not move the mesh into the T-pose), `--seconds` (per clip; several sentences
separated by ` | ` play in sequence inside one clip), `--seed`, `--steps` (Kimodo denoising steps, default 100),
`--inplace` (drop the horizontal root motion). `anim` keeps the clips from earlier runs and re-exports everything.

The result in `assets/<Name>/`: `<Name>.glb` (skin + every clip as a glTF animation), `<Name>.fbx` (Y-up,
-Z forward, one animation take per clip, `<Name>Diffuse.png` next to it) and `<Name>.blend`. In Unity, import the
FBX, set Rig → Animation Type to Humanoid (the `mixamorig:` names map automatically) and the takes show up as
clips. The character stands on Y=0, faces +Z, and the clips keep the hips translation unless `--inplace`.

Prompt tips: Kimodo was trained on captions like "A person walks forward and waves." Name one action per
sentence; 2–8 s per clip works best; name the clip after the Unity state it will drive. Kimodo does not loop
clips: trim or pick a clip whose last frame resembles its first. Be explicit about the body: "A person waves
hello with their right hand." (seed 7) gave a timid chest-height wave, "A person raises their right arm high
above their head and waves hello enthusiastically." (seed 3) a proper one with the hand 20 cm above the head.
Different seeds give noticeably different takes, so generate a few and keep the best.

First real run (2026-10-27, RTX 2080): Kimodo + the CPU text encoder load in ~30 s (16 GB RAM), then a
4-second clip takes ~10 s (5 s of denoising at 20 steps/s, the rest is the encoder and the BVH export) and the
Blender retarget + GLB/FBX export ~15 s for three clips. From the command line, free the GPU first if ComfyUI
still holds its 3D models (Free VRAM in the studio header, or `curl -X POST http://127.0.0.1:8188/free -d
"{\"unload_models\":true}"`): with Pixal3D resident the first clip took 268 s instead of 40. The studio does
this hand-over itself.

Second run, on a generated character (`generate.bat "a person in casual clothes ..." --preset character`, 3500
tris): rig 66 s including Blender, clean T-pose, Idle / Walk / Wave all read correctly on the first try; the
clothes and hair deform without artefacts, the hands are mitten-like at this triangle count. The "MISSING / UNEXPECTED lora_*" report that
transformers 5 prints while the LLM2Vec adapters load is noise: both adapters end up with their trained
weights (checked: every `lora_B` is non-zero, which it would not be if the load had silently failed).

## How it works

1. `rig_infer.py` samples 32k surface points, runs MIA's coarse joint model, rotates the mesh into its canonical
   frame (hips at the origin, facing +Z), resamples with extra points around the hands and runs the fine models:
   blend weights (4 bones per vertex after conflict clean-up), joint heads + tails, and a per-bone rigid
   transform from the input pose to the Mixamo T-pose.
2. `blender_rig.py` imports the ORIGINAL mesh (textures intact), moves it into the same frame, builds the
   armature from the hierarchy table, assigns the weights by nearest vertex, applies the to-rest transforms,
   bakes the deformation and makes the T-pose the rest pose, scales to `--height` with the feet on the ground
   and exports.
3. `motion.py` runs Kimodo with the text encoder on the CPU and writes the clip as a SOMA-77 BVH whose rest pose
   is the standard T-pose.
4. `blender_animate.py` imports each BVH and copies, per frame, every source bone's world-space rotation change
   (pose × rest⁻¹) onto the mapped Mixamo bone (`MAP` in the script: SOMA Spine1/Spine2/Chest → Spine/Spine1/
   Spine2, Leg/Shin → UpLeg/Leg, finger joints shifted by one because SOMA has a metacarpal joint). Because
   both rests are T-poses this needs no calibration; the hips translation is scaled by the leg-length ratio.
   Clips are stashed as NLA tracks so the glTF exporter writes one animation per clip and the FBX exporter one
   take per clip.

Tested with MIA's `luigi.glb` example: rig in 3.4 s (17.5k vertices), Kimodo's bundled "runs and leaps over an
obstacle" motion plus generated Wave / Walk / WaveHigh clips retargeted and exported; the rig's hand follows the
source motion within a few centimetres (the rest is Luigi's shorter arms). `blender_preview.py` renders frames;
pass `--fps 30` (the default) so frame numbers match the BVH, the glTF importer maps time to the scene fps.

## Model files

- `mia/output/best/new/{bw,bw_normal,joints,joints_coarse,pose}.pth` (2.2 GB) from `jasongzy/Make-It-Animatable`
- Hugging Face cache: `nvidia/Kimodo-SOMA-RP-v1.2`, `McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp` (+ `-supervised`
  PEFT adapter), `meta-llama/Meta-Llama-3-8B-Instruct` (gated)
