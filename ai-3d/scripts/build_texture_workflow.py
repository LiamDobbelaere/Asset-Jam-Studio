"""Builds workflows/text_to_texture_api.json (used by texture.py) and workflows/text_to_texture.json
(UI workflow for the ComfyUI sidebar) from one node graph, so the two can't drift apart.

Graph: Z-Image-Turbo text -> image with native seamless tiling (custom_nodes/ai3d_seamless): the model is
wrapped so the latent is rolled by a different offset on every sampling step, and the VAE decodes with
circular padding, so the picture tiles by construction and plank/tile grids line up across the borders.
An optional LoRA loader (StylizedTexture_ZIT, bypassed in the UI workflow) adds a trained hand-painted
game-texture look. A 2x2 preview is saved too.

The API graph also keeps the older post-process tiling (roll the picture by half, cross-fade the
original centre over the seam cross, optional masked repaint) on a second sampler, selected by the CLI
with --tile crossfade. It is seam-free on flat materials where the native roll can leave faint lines.

Run with the ComfyUI server up (it reads /object_info for the UI export):
    ComfyUI\\.venv\\Scripts\\python.exe scripts\\build_texture_workflow.py
"""
import json
import os
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.environ.get("COMFY_SERVER", "http://127.0.0.1:8188")

W = H = 1024          # defaults; texture.py patches every size-dependent input
STEPS = 14            # native tiling gets cleaner with a few more steps than Z-Image's usual 8
BAND = 192            # cross-fade variant: width of the seam strip
FEATHER = 96          # soft edge on each side of the strip (half the band: a full cross-fade)
LORA = "StylizedTexture_ZIT.safetensors"
PROMPT = ("dark oak parquet floor, long horizontal wooden planks in staggered rows, warm orange-brown wood, "
          "seamless tileable game texture, flat top-down orthographic view, hand-painted in a simple stylized cartoon style, "
          "flat matte solid color fills with a faint grainy speckled mottling, a few soft brush smudges and small paint spatter dots, "
          "thin dark seam lines between planks with a thin pale highlight edge, very low detail, no fine grain, no photo realism, "
          "low contrast, even flat lighting, no shadows, no perspective, no vignette, the material fills the entire frame edge to edge")

nodes = {}
order = []


def node(nid, class_type, title=None, **inputs):
    nodes[nid] = {"inputs": inputs, "class_type": class_type, "_meta": {"title": title or class_type}}
    order.append(nid)
    return nid


def link(nid, idx=0):
    return [nid, idx]


# --- text to image, native seamless -------------------------------------------------------------
node("1", "UNETLoader", "Z-Image-Turbo", unet_name="z_image_turbo_int8_convrot.safetensors", weight_dtype="default")
node("2", "CLIPLoader", "Qwen3 text encoder", clip_name="qwen_3_4b_fp8_mixed.safetensors", type="lumina2", device="default")
node("3", "VAELoader", "Z-Image VAE", vae_name="z_image_ae.safetensors")
node("4", "ModelSamplingAuraFlow", shift=3.0, sampling="flow", model=link("1"))
node("40", "LoraLoaderModelOnly", "Stylized texture LoRA (optional)", lora_name=LORA, strength_model=0.8, model=link("4"))
node("41", "SeamlessTileModel", "Seamless tiling (roll latent per step)", model=link("40"), tiling="enable", seed=0, patch=2)
node("5", "CLIPTextEncode", "PROMPT (describe the material)", text=PROMPT, clip=link("2"))
node("6", "ConditioningZeroOut", conditioning=link("5"))
node("7", "EmptySD3LatentImage", "Texture size", width=W, height=H, batch_size=1)
node("42", "KSampler", "Text to image (seamless)", seed=0, steps=STEPS, cfg=1.0, sampler_name="res_multistep", scheduler="simple",
     denoise=1.0, model=link("41"), positive=link("5"), negative=link("6"), latent_image=link("7"))
node("43", "CircularVAEDecode", "Circular VAE decode", samples=link("42"), vae=link("3"))
node("44", "SaveImage", "Save tileable texture", filename_prefix="texture/tile", images=link("43"))
node("45", "ImageStitch", "2x1", image1=link("43"), direction="right", match_image_size=False,
     spacing_width=0, spacing_color="white", image2=link("43"))
node("46", "ImageStitch", "2x2", image1=link("45"), direction="down", match_image_size=False,
     spacing_width=0, spacing_color="white", image2=link("45"))
node("47", "SaveImage", "Save 2x2 tiling preview", filename_prefix="texture/preview2x2", images=link("46"))
UI_NODES = list(order)   # the UI workflow shows only the native pipeline

# --- cross-fade variant (API graph only): plain sampler, roll by half, cross-fade, optional repaint ----
node("8", "KSampler", "Text to image", seed=0, steps=8, cfg=1.0, sampler_name="res_multistep", scheduler="simple",
     denoise=1.0, model=link("40"), positive=link("5"), negative=link("6"), latent_image=link("7"))
node("9", "VAEDecode", samples=link("8"), vae=link("3"))
node("10", "SaveImage", "Save untiled", filename_prefix="texture/raw", images=link("9"))
hw, hh = W // 2, H // 2
node("11", "ImageCrop", "Crop top-left", image=link("9"), width=hw, height=hh, x=0, y=0)
node("12", "ImageCrop", "Crop top-right", image=link("9"), width=hw, height=hh, x=hw, y=0)
node("13", "ImageCrop", "Crop bottom-left", image=link("9"), width=hw, height=hh, x=0, y=hh)
node("14", "ImageCrop", "Crop bottom-right", image=link("9"), width=hw, height=hh, x=hw, y=hh)
node("15", "ImageStitch", "Rolled top row", image1=link("14"), direction="right", match_image_size=False,
     spacing_width=0, spacing_color="white", image2=link("13"))
node("16", "ImageStitch", "Rolled bottom row", image1=link("12"), direction="right", match_image_size=False,
     spacing_width=0, spacing_color="white", image2=link("11"))
node("17", "ImageStitch", "Rolled image", image1=link("15"), direction="down", match_image_size=False,
     spacing_width=0, spacing_color="white", image2=link("16"))
node("18", "SolidMask", "Empty mask", value=0.0, width=W, height=H)
node("19", "SolidMask", "Horizontal strip", value=1.0, width=W, height=BAND)
node("20", "SolidMask", "Vertical strip", value=1.0, width=BAND, height=H)
node("21", "FeatherMask", mask=link("19"), left=0, top=FEATHER, right=0, bottom=FEATHER)
node("22", "FeatherMask", mask=link("20"), left=FEATHER, top=0, right=FEATHER, bottom=0)
node("23", "MaskComposite", "Add horizontal strip (hard)", destination=link("18"), source=link("19"), x=0, y=(H - BAND) // 2, operation="add")
node("24", "MaskComposite", "Seam cross mask (hard)", destination=link("23"), source=link("20"), x=(W - BAND) // 2, y=0, operation="add")
node("33", "MaskComposite", "Add horizontal strip (soft)", destination=link("18"), source=link("21"), x=0, y=(H - BAND) // 2, operation="add")
node("34", "MaskComposite", "Seam cross mask (soft)", destination=link("33"), source=link("22"), x=(W - BAND) // 2, y=0, operation="add")
node("36", "ImageCompositeMasked", "Cross-fade original centre over the seams", destination=link("17"), source=link("9"),
     x=0, y=0, resize_source=False, mask=link("34"))
node("25", "VAEEncode", pixels=link("36"), vae=link("3"))
node("26", "SetLatentNoiseMask", samples=link("25"), mask=link("24"))
node("27", "KSampler", "Repaint seams", seed=1, steps=8, cfg=1.0, sampler_name="res_multistep", scheduler="simple",
     denoise=0.4, model=link("40"), positive=link("5"), negative=link("6"), latent_image=link("26"))
node("28", "VAEDecode", samples=link("27"), vae=link("3"))
node("35", "ImageCompositeMasked", "Blend repaint onto rolled image", destination=link("36"), source=link("28"), x=0, y=0,
     resize_source=False, mask=link("34"))
node("29", "SaveImage", "Save cross-faded texture", filename_prefix="texture/tile", images=link("36"))
node("37", "SaveImage", "Save cross-faded texture (repainted seams)", filename_prefix="texture/tile_repaint", images=link("35"))
node("30", "ImageStitch", "2x1 (cross-fade)", image1=link("36"), direction="right", match_image_size=False,
     spacing_width=0, spacing_color="white", image2=link("36"))
node("31", "ImageStitch", "2x2 (cross-fade)", image1=link("30"), direction="down", match_image_size=False,
     spacing_width=0, spacing_color="white", image2=link("30"))
node("32", "SaveImage", "Save 2x2 tiling preview (cross-fade)", filename_prefix="texture/preview2x2", images=link("31"))


def build_ui(api, keep):
    """Converts the API graph to the frontend's workflow format (positions, typed slots, links)."""
    info = json.load(urllib.request.urlopen(SERVER + "/object_info"))
    columns = {"1": 0, "2": 0, "3": 0, "4": 1, "40": 2, "41": 3, "5": 1, "6": 2, "7": 1, "42": 4, "43": 5, "44": 6, "45": 6, "46": 7, "47": 8}
    rows = {}
    ui_nodes, links = [], []
    link_id = 0
    for nid in keep:
        n = api[nid]
        ct = n["class_type"]
        spec = info[ct]["input"]
        in_names = list(spec.get("required", {})) + list(spec.get("optional", {}))
        inputs, widgets = [], []
        for name in in_names:
            val = n["inputs"].get(name)
            if isinstance(val, list) and len(val) == 2 and isinstance(val[0], str):
                src, slot = val
                link_id += 1
                typ = info[api[src]["class_type"]]["output"][slot]
                links.append([link_id, int(src), slot, int(nid), len(inputs), typ])
                inputs.append({"name": name, "type": typ, "link": link_id})
            else:
                inputs.append({"name": name, "type": (spec.get("required", {}).get(name) or spec["optional"][name])[0]
                               if isinstance((spec.get("required", {}).get(name) or spec["optional"][name])[0], str) else "COMBO",
                               "widget": {"name": name}, "link": None})
                widgets.append(val)
                if name == "seed":
                    widgets.append("fixed")
        outs = [{"name": on, "type": ot, "links": []} for on, ot in zip(info[ct]["output_name"], info[ct]["output"])]
        col = columns[nid]
        row = rows.get(col, 0)
        rows[col] = row + 1
        ui_nodes.append({"id": int(nid), "type": ct, "pos": [col * 420, row * 260],
                         "size": [380, 220 if ct in ("CLIPTextEncode", "KSampler") else 120],
                         "flags": {}, "order": keep.index(nid), "mode": 4 if ct == "LoraLoaderModelOnly" else 0,
                         "inputs": inputs, "outputs": outs,
                         "properties": {"Node name for S&R": ct}, "widgets_values": widgets, "title": n["_meta"]["title"]})
    by_id = {n["id"]: n for n in ui_nodes}
    for lid, src, slot, dst, dslot, typ in links:
        by_id[src]["outputs"][slot]["links"].append(lid)
    return {"id": "7e3b1a2c-texture-0001", "revision": 0, "last_node_id": max(int(k) for k in api), "last_link_id": link_id,
            "nodes": ui_nodes, "links": links, "groups": [
                {"id": 1, "title": "Text to image, seamless by construction (LoRA node is bypassed; enable it if the LoRA is installed)",
                 "bounding": [-20, -60, 2500, 820], "color": "#3f789e", "flags": {}},
                {"id": 2, "title": "Save + 2x2 preview", "bounding": [2500, -60, 1300, 820], "color": "#8A8", "flags": {}}],
            "config": {}, "extra": {}, "version": 0.4}


if __name__ == "__main__":
    out = os.path.join(ROOT, "workflows")
    with open(os.path.join(out, "text_to_texture_api.json"), "w", encoding="utf-8") as f:
        json.dump(nodes, f, indent=1)
    with open(os.path.join(out, "text_to_texture.json"), "w", encoding="utf-8") as f:
        json.dump(build_ui(nodes, UI_NODES), f, indent=1)
    print("wrote text_to_texture_api.json and text_to_texture.json")
