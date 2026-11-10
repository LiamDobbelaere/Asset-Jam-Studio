"""Builds workflows/pixel_wan14_api.json (used by pixel.py --animate --engine wan14): Wan 2.2 I2V A14B (both experts as
Q4_K_S GGUF through ComfyUI-GGUF), the lightx2v 4-step distillation LoRAs and styly-agents' pixel-animate LoRA, the
way that LoRA's own ComfyUI workflow wires it: shift 5, 2 + 2 steps (high-noise expert for steps 0-2, low-noise for
2-4), CFG 1, ddim / simple. The frames are cut out by BiRefNet and saved as RGBA like the 5B workflow.

Run (no server needed):
    ComfyUI\\.venv\\Scripts\\python.exe scripts\\build_pixel_wan_workflow.py
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HIGH = "Wan2.2-I2V-A14B-HighNoise-Q4_K_S.gguf"
LOW = "Wan2.2-I2V-A14B-LowNoise-Q4_K_S.gguf"
LIGHT_HIGH = "wan2.2_i2v_high_noise_lightx2v_4step.safetensors"
LIGHT_LOW = "wan2.2_i2v_low_noise_lightx2v_4step.safetensors"
PIXEL_LORA = "wan2.2_pixel_animate.safetensors"
TEXT_ENCODER = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
VAE = "wan_2.1_vae.safetensors"
PROMPT = "pixel art sprite animation, the knight walks in place, side view, static camera, plain white background"
NEGATIVE = "blurry, smooth gradients, photorealistic, 3d render, text, watermark, camera motion, zoom, morphing, extra limbs, deformed"

nodes = {}


def node(nid, class_type, title=None, **inputs):
    nodes[nid] = {"inputs": inputs, "class_type": class_type, "_meta": {"title": title or class_type}}
    return nid


def link(nid, idx=0):
    return [nid, idx]


node("1", "UnetLoaderGGUF", "Wan 2.2 I2V 14B high noise (GGUF)", unet_name=HIGH)
node("2", "UnetLoaderGGUF", "Wan 2.2 I2V 14B low noise (GGUF)", unet_name=LOW)
node("3", "CLIPLoader", "umT5-XXL text encoder", clip_name=TEXT_ENCODER, type="wan", device="default")
node("4", "VAELoader", "Wan 2.1 VAE", vae_name=VAE)
node("10", "LoraLoaderModelOnly", "lightx2v 4-step (high)", model=link("1"), lora_name=LIGHT_HIGH, strength_model=1.0)
node("11", "LoraLoaderModelOnly", "pixel animate (high)", model=link("10"), lora_name=PIXEL_LORA, strength_model=1.0)
node("12", "ModelSamplingSD3", "Shift 5 (high)", model=link("11"), shift=5.0)
node("20", "LoraLoaderModelOnly", "lightx2v 4-step (low)", model=link("2"), lora_name=LIGHT_LOW, strength_model=1.0)
node("21", "LoraLoaderModelOnly", "pixel animate (low)", model=link("20"), lora_name=PIXEL_LORA, strength_model=1.0)
node("22", "ModelSamplingSD3", "Shift 5 (low)", model=link("21"), shift=5.0)
node("5", "CLIPTextEncode", "PROMPT (what moves)", text=PROMPT, clip=link("3"))
node("6", "CLIPTextEncode", "NEGATIVE", text=NEGATIVE, clip=link("3"))
node("30", "LoadImage", "START frame (the still)", image="pixel_start.png", upload="image")
node("31", "WanImageToVideo", "Image to video conditioning", positive=link("5"), negative=link("6"), vae=link("4"), width=512, height=512,
     length=33, batch_size=1, start_image=link("30"))
node("40", "KSamplerAdvanced", "Sample: high-noise expert (steps 0-2)", model=link("12"), add_noise="enable", noise_seed=0, steps=4, cfg=1.0,
     sampler_name="ddim", scheduler="simple", positive=link("31", 0), negative=link("31", 1), latent_image=link("31", 2),
     start_at_step=0, end_at_step=2, return_with_leftover_noise="enable")
node("41", "KSamplerAdvanced", "Sample: low-noise expert (steps 2-4)", model=link("22"), add_noise="disable", noise_seed=0, steps=4, cfg=1.0,
     sampler_name="ddim", scheduler="simple", positive=link("31", 0), negative=link("31", 1), latent_image=link("40"),
     start_at_step=2, end_at_step=4, return_with_leftover_noise="disable")
node("42", "VAEDecode", "Decode frames", samples=link("41"), vae=link("4"))
node("43", "SaveImage", "Save frames (PNG)", filename_prefix="aivid/frames/f", images=link("42"))
node("50", "LoadBackgroundRemovalModel", "BiRefNet", bg_removal_name="birefnet.safetensors")
node("51", "RemoveBackground", "Cut out every frame", bg_removal_model=link("50"), image=link("42"))
node("53", "InvertMask", "Invert", mask=link("51"))
node("54", "JoinImageWithAlpha", "Add alpha", image=link("42"), alpha=link("53"))
node("55", "SaveImage", "Save RGBA frames", filename_prefix="aivid/rgba/f", images=link("54"))

if __name__ == "__main__":
    out = os.path.join(ROOT, "workflows", "pixel_wan14_api.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(nodes, f, indent=1)
    print("wrote", out)
