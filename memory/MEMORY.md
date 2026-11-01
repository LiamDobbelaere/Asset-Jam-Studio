# Project memory index

One line per memory in this folder. Private notes (gitignored) are indexed in `private/MEMORY.md`.

- [Hardware and license constraints](hardware-and-license-constraints.md) — RTX 2080 8 GB Turing, 64 GB RAM, Windows; EU rules out Hunyuan licenses
- [Public repo rules](public-repo-rules.md) — public as Asset Jam Studio, GPL-3.0; no private game names, voices, logos or local paths in tracked files
- [Game art style](game-art-style.md) — low-poly but smooth/rounded, never 'blocky'; 600–1800 tris, one 1024 painted diffuse
- [Studio web UI](studio-web-ui.md) — studio/ front-end + HTTP API/CLI + Claude skill over all ai-* flows; run via background Bash when testing; comfy.log pipe gotcha
- [Text-to-3D setup](text-to-3d-setup.md) — ComfyUI native Pixal3D/TRELLIS.2 + Z-Image-Turbo pipeline and install gotchas
- [Game-asset small parts](game-asset-small-parts.md) — QEM decimation drops castors/feet, not the cascade/remesh; default 3000 tris + 10° dissolve; 1536 and retopo on the raw mesh are dead ends; cache-thrash and freeze pitfalls
- [Box rebuild geometry](box-rebuild-geometry.md) — --geometry boxes: union of axis-aligned boxes for box-modelled props; rejected, stashed; CoACD/raw-mesh dead ends
- [Agent modelling exploration](agent-modelling-exploration.md) — Claude writes bpy props from the picture; texturing via StableGen, then part-colour renders
- [World texture generation](world-texture-generation.md) — texture.bat: native latent-roll tiling (ai3d_seamless nodes) default, cross-fade for flat walls, LoRA opt-in (--lora 0.8 for grass/tiles only)
- [Sprite generation setup](sprite-generation-setup.md) — sprite.bat: FLUX.2 klein 4B fp8 edit/pose/t2i + BiRefNet cut-out; why not Qwen-Image-Edit / FLUX 3 Klein; colour drift on distilled
- [Voice generation setup](voice-generation-setup.md) — ai-voc: Qwen3-TTS 1.7B-Base fp16 zero-shot cloning + LoRA fine-tune; why not Chatterbox/F5/Fish; 8 GB gotchas
- [Music generation setup](music-generation-setup.md) — ai-mus: ACE-Step 1.5 2B turbo + 0.6B planner (bf16 LM, CPU-offloaded, on Turing), MIT; why not MusicGen/Stable Audio/HeartMuLa
- [SFX generation setup](sfx-generation-setup.md) — ai-sfx: MOSS-SoundEffect v2.0 (Apache-2.0), fp16 DiT + swapped text encoder/VAE; VRAM-spill 14 s/step trap; crop margin + trim + 30 Hz HPF
- [Video intro (ai-vid)](video-intro-plan.md) — on main, experimental (first draft judged bad); Wan 2.2 5B fp16 pipeline + studio Video tab; why not LTX-2.5/Hunyuan/14B
- [Character animation (ai-anim)](character-animation-setup.md) — Make-It-Animatable rig + Kimodo text-to-motion + Blender retarget; gated Llama-3 encoder; studio Animate tab
