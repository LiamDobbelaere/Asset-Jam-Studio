"""Builds workflows/sprite_edit_api.json (used by sprite.py) and workflows/sprite_edit.json (UI workflow for the
ComfyUI sidebar) from one node graph, so the two can't drift apart.

Graph: FLUX.2 [klein] 4B image editing. The source sprite (LoadImage "SOURCE") plus up to two extra reference
images ("REF 2", "REF 3") are VAE-encoded and attached to the conditioning with ReferenceLatent, so the prompt can
say "the character in image 1 sitting on the chair from image 2". The distilled model samples in 4 steps at CFG 1
(negative = zeroed conditioning); the base model uses 20 steps at CFG 5 with a real negative prompt. The output is
saved as painted (sprite/raw) and after BiRefNet background removal as RGBA (sprite/rgba).

A text-to-image branch (no source image) draws a new sprite from the prompt alone: EmptyFlux2LatentImage at the
requested size instead of the source's size. sprite.py routes between the branches and prunes unused nodes.

Run with the ComfyUI server up (it reads /object_info for the UI export):
    ComfyUI\\.venv\\Scripts\\python.exe scripts\\build_sprite_workflow.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ui_export import build_ui  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MODEL = "flux-2-klein-4b-fp8.safetensors"            # distilled, 4 steps
MODEL_BASE = "flux-2-klein-base-4b-fp8.safetensors"  # undistilled, 20 steps, CFG 5
CLIP = "qwen_3_4b_fp8_mixed.safetensors"             # same Qwen3-4B encoder as Z-Image-Turbo
VAE = "flux2-vae.safetensors"
PROMPT = ("Redraw the character from image 1 sitting on the office chair from image 2, facing the camera, "
          "southeast isometric view, same character design, same art style, same line weight and colours, "
          "plain white background")
NEGATIVE = "blurry, photo, realistic, 3d render, extra limbs, text, watermark, drop shadow on the ground"

nodes = {}
order = []


def node(nid, class_type, title=None, **inputs):
    nodes[nid] = {"inputs": inputs, "class_type": class_type, "_meta": {"title": title or class_type}}
    order.append(nid)
    return nid


def link(nid, idx=0):
    return [nid, idx]


# --- loaders ----------------------------------------------------------------------------------------
node("1", "UNETLoader", "FLUX.2 klein 4B (distilled fp8)", unet_name=MODEL, weight_dtype="default")
node("2", "CLIPLoader", "Qwen3-4B text encoder", clip_name=CLIP, type="flux2", device="default")
node("3", "VAELoader", "FLUX.2 VAE", vae_name=VAE)

# --- prompt ------------------------------------------------------------------------------------------
node("5", "CLIPTextEncode", "PROMPT (what the finished sprite shows)", text=PROMPT, clip=link("2"))
node("6", "ConditioningZeroOut", "Negative (distilled: zeroed)", conditioning=link("5"))
node("7", "CLIPTextEncode", "NEGATIVE (base model only)", text=NEGATIVE, clip=link("2"))

# --- source sprite + references ----------------------------------------------------------------------
node("10", "LoadImage", "SOURCE (image 1)", image="sprite_source.png", upload="image")
node("11", "ImageScaleToTotalPixels", "Scale source to 1 MP", image=link("10"), upscale_method="lanczos", megapixels=1.0, resolution_steps=16)
node("12", "GetImageSize", "Output size = source size", image=link("11"))
node("13", "VAEEncode", "Encode source", pixels=link("11"), vae=link("3"))
node("14", "ReferenceLatent", "Attach source (positive)", conditioning=link("5"), latent=link("13"))
node("15", "ReferenceLatent", "Attach source (negative, zeroed)", conditioning=link("6"), latent=link("13"))
node("16", "ReferenceLatent", "Attach source (negative, base)", conditioning=link("7"), latent=link("13"))

node("20", "LoadImage", "REF 2 (image 2, optional)", image="sprite_ref2.png", upload="image")
node("21", "ImageScaleToTotalPixels", "Scale ref 2", image=link("20"), upscale_method="lanczos", megapixels=1.0, resolution_steps=16)
node("22", "VAEEncode", "Encode ref 2", pixels=link("21"), vae=link("3"))
node("23", "ReferenceLatent", "Attach ref 2 (positive)", conditioning=link("14"), latent=link("22"))
node("24", "ReferenceLatent", "Attach ref 2 (negative, zeroed)", conditioning=link("15"), latent=link("22"))
node("25", "ReferenceLatent", "Attach ref 2 (negative, base)", conditioning=link("16"), latent=link("22"))

node("30", "LoadImage", "REF 3 (image 3, optional)", image="sprite_ref3.png", upload="image")
node("31", "ImageScaleToTotalPixels", "Scale ref 3", image=link("30"), upscale_method="lanczos", megapixels=1.0, resolution_steps=16)
node("32", "VAEEncode", "Encode ref 3", pixels=link("31"), vae=link("3"))
node("33", "ReferenceLatent", "Attach ref 3 (positive)", conditioning=link("23"), latent=link("32"))
node("34", "ReferenceLatent", "Attach ref 3 (negative, zeroed)", conditioning=link("24"), latent=link("32"))
node("35", "ReferenceLatent", "Attach ref 3 (negative, base)", conditioning=link("25"), latent=link("32"))

# --- sampling (edit branch: canvas = source size) ------------------------------------------------------
node("40", "EmptyFlux2LatentImage", "Canvas (source size)", width=link("12", 0), height=link("12", 1), batch_size=1)
node("41", "Flux2Scheduler", "Sigmas", steps=4, width=link("12", 0), height=link("12", 1))
node("42", "KSamplerSelect", sampler_name="euler")
node("43", "RandomNoise", noise_seed=0)
node("44", "CFGGuider", "Guider (distilled: CFG 1)", cfg=1.0, model=link("1"), positive=link("33"), negative=link("34"))
node("45", "SamplerCustomAdvanced", "Sample (edit)", noise=link("43"), guider=link("44"), sampler=link("42"), sigmas=link("41"), latent_image=link("40"))
node("46", "VAEDecode", "Decode", samples=link("45"), vae=link("3"))
node("47", "SaveImage", "Save painted result", filename_prefix="sprite/raw", images=link("46"))

# --- transparency ---------------------------------------------------------------------------------------
node("50", "LoadBackgroundRemovalModel", "BiRefNet", bg_removal_name="birefnet.safetensors")
node("51", "RemoveBackground", "Cut out the sprite", bg_removal_model=link("50"), image=link("46"))
node("53", "InvertMask", "Invert (JoinImageWithAlpha expects 1 = transparent)", mask=link("51"))
node("54", "JoinImageWithAlpha", "Add alpha", image=link("46"), alpha=link("53"))
node("52", "SaveImage", "Save RGBA sprite", filename_prefix="sprite/rgba", images=link("54"))
UI_NODES = list(order)

# --- text-to-image branch (API graph only; sprite.py uses it when no --image is given) -------------------
node("60", "EmptyFlux2LatentImage", "Canvas (text to image)", width=768, height=1024, batch_size=1)
node("61", "Flux2Scheduler", "Sigmas (text to image)", steps=4, width=768, height=1024)
node("62", "CFGGuider", "Guider (text to image)", cfg=1.0, model=link("1"), positive=link("5"), negative=link("6"))
node("63", "SamplerCustomAdvanced", "Sample (text to image)", noise=link("43"), guider=link("62"), sampler=link("42"), sigmas=link("61"), latent_image=link("60"))
node("64", "VAEDecode", "Decode (text to image)", samples=link("63"), vae=link("3"))
node("65", "SaveImage", "Save painted result (text to image)", filename_prefix="sprite/raw", images=link("64"))
node("66", "RemoveBackground", "Cut out the sprite (text to image)", bg_removal_model=link("50"), image=link("64"))
node("68", "InvertMask", "Invert (text to image)", mask=link("66"))
node("69", "JoinImageWithAlpha", "Add alpha (text to image)", image=link("64"), alpha=link("68"))
node("67", "SaveImage", "Save RGBA sprite (text to image)", filename_prefix="sprite/rgba", images=link("69"))

COLUMNS = {"1": 0, "2": 0, "3": 0, "5": 1, "6": 2, "7": 1,
           "10": 0, "11": 1, "12": 2, "13": 2, "14": 3, "15": 3, "16": 3,
           "20": 0, "21": 1, "22": 2, "23": 4, "24": 4, "25": 4,
           "30": 0, "31": 1, "32": 2, "33": 5, "34": 5, "35": 5,
           "40": 6, "41": 6, "42": 6, "43": 6, "44": 6, "45": 7, "46": 8, "47": 9, "50": 8, "51": 9, "53": 10, "54": 10, "52": 11}
GROUPS = [
    {"id": 1, "title": "FLUX.2 klein 4B sprite edit: SOURCE = image 1, REF 2/3 optional (bypassed; enable when used). "
                       "Distilled: 4 steps, CFG 1. Base model: swap the loader, 20 steps, CFG 5, wire the NEGATIVE chain into the guider.",
     "bounding": [-20, -60, 2500, 1700], "color": "#3f789e", "flags": {}},
    {"id": 2, "title": "Sample, decode, cut out (BiRefNet) and save RGBA", "bounding": [2500, -60, 2100, 1700], "color": "#8A8", "flags": {}},
]
# In the UI workflow the guider reads the source-only chain; refs 2/3 and the base-model negative chain are bypassed.
UI_OVERRIDES = {"44": {"positive": link("14"), "negative": link("15")}}
BYPASS = {"7", "16", "20", "21", "22", "23", "24", "25", "30", "31", "32", "33", "34", "35"}

if __name__ == "__main__":
    out = os.path.join(ROOT, "workflows")
    with open(os.path.join(out, "sprite_edit_api.json"), "w", encoding="utf-8") as f:
        json.dump(nodes, f, indent=1)
    ui_api = json.loads(json.dumps(nodes))
    for nid, inp in UI_OVERRIDES.items():
        ui_api[nid]["inputs"].update(inp)
    ui = build_ui(ui_api, UI_NODES, COLUMNS, GROUPS, "7e3b1a2c-sprite-0001", bypass_ids=BYPASS)
    with open(os.path.join(out, "sprite_edit.json"), "w", encoding="utf-8") as f:
        json.dump(ui, f, indent=1)
    print("wrote sprite_edit_api.json and sprite_edit.json")
