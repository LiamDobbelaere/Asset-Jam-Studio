r"""Download the Wan 2.2 TI2V-5B weights (~18 GB) into ai-3d/ComfyUI/models. Run via setup.ps1 or:
    ..\ai-3d\ComfyUI\.venv\Scripts\python.exe -I scripts\download_models.py
"""
import os, shutil
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
from huggingface_hub import hf_hub_download
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
M = os.path.join(os.path.dirname(ROOT), "ai-3d", "ComfyUI", "models")
jobs = [
 # Wan 2.2 TI2V-5B, Apache-2.0: one dense model for text-to-video and image-to-video, 720p 24 fps, fp16 (Turing has no bf16)
 ("Comfy-Org/Wan_2.2_ComfyUI_Repackaged", "split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors", "diffusion_models/wan2.2_ti2v_5B_fp16.safetensors"),
 ("Comfy-Org/Wan_2.2_ComfyUI_Repackaged", "split_files/vae/wan2.2_vae.safetensors", "vae/wan2.2_vae.safetensors"),
 # umT5-XXL text encoder, fp8 weights (upcast on load; it runs offloaded so Turing's lack of fp8 does not matter)
 ("Comfy-Org/Wan_2.2_ComfyUI_Repackaged", "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", "text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
]
for repo, fn, dest in jobs:
    out = os.path.join(M, dest)
    if os.path.exists(out) and os.path.getsize(out) > 1e6:
        print("skip", dest); continue
    print("downloading", repo, fn, flush=True)
    p = hf_hub_download(repo, fn, cache_dir=os.path.join(M, "_hfcache"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    shutil.copyfile(p, out)
    print("done", dest, os.path.getsize(out) // 2**20, "MB", flush=True)
shutil.rmtree(os.path.join(M, "_hfcache"), ignore_errors=True)
print("ALL_DONE")
