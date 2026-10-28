"""Builds workflows/wan_i2v_api.json (used by shot.py) and workflows/wan_i2v.json (UI workflow for the ComfyUI
sidebar) from one node graph, so the two can't drift apart.

Graph: Wan 2.2 TI2V-5B text/image-to-video. Settings follow ComfyUI's own "Wan 2.2 5B video generation"
template (ModelSamplingSD3 shift 8, KSampler 20 steps, CFG 5, uni_pc / simple), with the latent node swapped for
our AIVidWan22FirstLastFrameToVideoLatent so a last frame can be pinned as well as the first. The API graph saves
every frame as PNG (shot.py encodes the mp4 with ffmpeg and keeps the exact last frame for chaining); the UI
workflow adds CreateVideo + SaveVideo for people.

Run with the ComfyUI server up for the UI export (it reads /object_info); without it only the API file is written:
    ..\\ai-3d\\ComfyUI\\.venv\\Scripts\\python.exe scripts\\build_video_workflow.py
"""
import json
import os
import sys
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AI3D_SCRIPTS = os.path.join(os.path.dirname(ROOT), "ai-3d", "scripts")
sys.path.insert(0, AI3D_SCRIPTS)

MODEL = "wan2.2_ti2v_5B_fp16.safetensors"
TEXT_ENCODER = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
VAE = "wan2.2_vae.safetensors"
PROMPT = ("A single black water droplet falls into still dark water and lands with a splash, concentric ripples "
          "spread outward, high contrast, minimal, cinematic lighting, smooth slow motion")
NEGATIVE = ("bright colors, overexposed, static, blurred details, subtitles, style, artwork, painting, picture, still, "
            "overall gray, worst quality, low quality, JPEG compression residue, ugly, incomplete, extra fingers, "
            "poorly drawn hands, poorly drawn faces, deformed, disfigured, malformed limbs, fused fingers, still picture, "
            "cluttered background, three legs, many people in the background, walking backwards")

nodes = {}
order = []


def node(nid, class_type, title=None, **inputs):
    nodes[nid] = {"inputs": inputs, "class_type": class_type, "_meta": {"title": title or class_type}}
    order.append(nid)
    return nid


def link(nid, idx=0):
    return [nid, idx]


node("1", "UNETLoader", "Wan 2.2 TI2V-5B (fp16)", unet_name=MODEL, weight_dtype="default")
node("2", "CLIPLoader", "umT5-XXL text encoder", clip_name=TEXT_ENCODER, type="wan", device="default")
node("3", "VAELoader", "Wan 2.2 VAE", vae_name=VAE)
node("4", "ModelSamplingSD3", "Shift 8", model=link("1"), shift=8.0)
node("5", "CLIPTextEncode", "PROMPT (what happens in the shot)", text=PROMPT, clip=link("2"))
node("6", "CLIPTextEncode", "NEGATIVE", text=NEGATIVE, clip=link("2"))
node("10", "LoadImage", "START frame (optional)", image="aivid_start.png", upload="image")
node("11", "LoadImage", "END frame (optional)", image="aivid_end.png", upload="image")
node("20", "AIVidWan22FirstLastFrameToVideoLatent", "Video latent (first + last frame)", vae=link("3"), width=832, height=480,
     length=73, batch_size=1, start_image=link("10"), end_image=link("11"))
node("30", "KSampler", "Sample (20 steps, CFG 5, uni_pc)", model=link("4"), positive=link("5"), negative=link("6"),
     latent_image=link("20"), seed=0, steps=20, cfg=5.0, sampler_name="uni_pc", scheduler="simple", denoise=1.0)
node("31", "VAEDecode", "Decode frames", samples=link("30"), vae=link("3"))
node("40", "SaveImage", "Save frames (PNG)", filename_prefix="aivid/frames/f", images=link("31"))
node("41", "ImageFromBatch", "Last frame", image=link("31"), batch_index=72, length=1)
node("42", "SaveImage", "Save last frame", filename_prefix="aivid/last/f", images=link("41"))
API_NODES = list(order)
# UI only: a playable mp4 next to the frames
node("50", "CreateVideo", "Frames -> video (24 fps)", images=link("31"), fps=24.0)
node("51", "SaveVideo", "Save mp4", video=link("50"), filename_prefix="video/aivid", format="auto", codec="auto")

COLUMNS = {"1": 0, "2": 0, "3": 0, "4": 1, "5": 1, "6": 1, "10": 0, "11": 0, "20": 2, "30": 3, "31": 4, "40": 5, "41": 5, "42": 6,
           "50": 5, "51": 6}
GROUPS = [
    {"id": 1, "title": "Wan 2.2 TI2V-5B: text / image / first+last frame to video. Bypass the LoadImage nodes you do not use.",
     "bounding": [-20, -60, 1300, 1500], "color": "#3f789e", "flags": {}},
    {"id": 2, "title": "Sample, decode, save frames + last frame (used by shot.py), and an mp4", "bounding": [1280, -60, 1700, 1500],
     "color": "#8A8", "flags": {}},
]

if __name__ == "__main__":
    out = os.path.join(ROOT, "workflows")
    os.makedirs(out, exist_ok=True)
    api = {k: nodes[k] for k in API_NODES}
    with open(os.path.join(out, "wan_i2v_api.json"), "w", encoding="utf-8") as f:
        json.dump(api, f, indent=1)
    print("wrote wan_i2v_api.json")
    try:
        from ui_export import build_ui
        ui = build_ui(nodes, order, COLUMNS, GROUPS, "5a1c9e7d-aivid-0001", bypass_ids={"11"})
        with open(os.path.join(out, "wan_i2v.json"), "w", encoding="utf-8") as f:
            json.dump(ui, f, indent=1)
        print("wrote wan_i2v.json")
    except (urllib.error.URLError, KeyError, ImportError) as e:
        print("UI workflow not written (ComfyUI server with the aivid node needed for /object_info):", e)
