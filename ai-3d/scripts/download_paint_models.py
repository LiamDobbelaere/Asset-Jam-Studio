"""Download the models paint.py uses (~12 GB) into ComfyUI/models. Run via setup_paint.ps1 or:
    ComfyUI/.venv/Scripts/python.exe -I scripts/download_paint_models.py
"""
import os, shutil
os.environ["HF_HUB_ENABLE_HF_TRANSFER"]="1"
from huggingface_hub import hf_hub_download
M=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ComfyUI", "models")
jobs=[
 # SDXL checkpoint (OpenRAIL++) + 8-step Lightning LoRA (OpenRAIL++)
 ("SG161222/RealVisXL_V5.0","RealVisXL_V5.0_fp16.safetensors","checkpoints/RealVisXL_V5.0_fp16.safetensors"),
 ("ByteDance/SDXL-Lightning","sdxl_lightning_8step_lora.safetensors","loras/sdxl_lightning_8step_lora.safetensors"),
 # depth ControlNet for SDXL (Apache-2.0)
 ("xinsir/controlnet-depth-sdxl-1.0","diffusion_pytorch_model.safetensors","controlnet/controlnet_depth_sdxl.safetensors"),
 # IP-Adapter Plus for SDXL + its ViT-H image encoder (Apache-2.0), the names IPAdapterUnifiedLoader looks for
 ("h94/IP-Adapter","sdxl_models/ip-adapter-plus_sdxl_vit-h.safetensors","ipadapter/ip-adapter-plus_sdxl_vit-h.safetensors"),
 ("h94/IP-Adapter","models/image_encoder/model.safetensors","clip_vision/CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors"),
]
for repo,fn,dest in jobs:
    out=os.path.join(M,dest)
    if os.path.exists(out) and os.path.getsize(out)>1e6: print("skip",dest); continue
    print("downloading",repo,fn,flush=True)
    p=hf_hub_download(repo,fn,cache_dir=os.path.join(M,"_hfcache"))
    os.makedirs(os.path.dirname(out),exist_ok=True)
    shutil.copyfile(p,out); print("done",dest,os.path.getsize(out)//2**20,"MB",flush=True)
shutil.rmtree(os.path.join(M,"_hfcache"),ignore_errors=True)
print("ALL_DONE")
