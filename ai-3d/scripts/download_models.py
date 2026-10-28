"""Download the model weights (~35 GB) into ComfyUI/models. Run via setup.ps1 or:
    ComfyUI/.venv/Scripts/python.exe -I scripts/download_models.py

With CIVITAI_TOKEN set (civitai.com -> account settings -> API keys) it also fetches the optional
StylizedTexture_ZIT LoRA (172 MB) that texture.py uses when present.
"""
import os, shutil, urllib.request
os.environ["HF_HUB_ENABLE_HF_TRANSFER"]="1"
from huggingface_hub import hf_hub_download
M=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ComfyUI", "models")
jobs=[
 ("Comfy-Org/TRELLIS.2","diffusion_models/trellis_2_int8_convrot.safetensors","diffusion_models/trellis_2_int8_convrot.safetensors"),
 ("Comfy-Org/Pixal3D","diffusion_models/pixal3d_int8_convrot.safetensors","diffusion_models/pixal3d_int8_convrot.safetensors"),
 ("Comfy-Org/Pixal3D","clip_vision/dino_v3_L_naf_fp32.safetensors","clip_vision/dino_v3_L_naf_fp32.safetensors"),
 ("Comfy-Org/Pixal3D","vae/trellis_2_shape_vae_bf16.safetensors","vae/trellis_2_shape_vae_bf16.safetensors"),
 ("Comfy-Org/Pixal3D","vae/trellis_2_texture_vae_bf16.safetensors","vae/trellis_2_texture_vae_bf16.safetensors"),
 ("Comfy-Org/BiRefNet","background_removal/birefnet.safetensors","background_removal/birefnet.safetensors"),
 ("Comfy-Org/MoGe","geometry_estimation/moge_2_vitl_normal_fp16.safetensors","geometry_estimation/moge_2_vitl_normal_fp16.safetensors"),
 ("Comfy-Org/z_image_turbo","split_files/diffusion_models/z_image_turbo_int8_convrot.safetensors","diffusion_models/z_image_turbo_int8_convrot.safetensors"),
 ("Comfy-Org/z_image_turbo","split_files/text_encoders/qwen_3_4b_fp8_mixed.safetensors","text_encoders/qwen_3_4b_fp8_mixed.safetensors"),
 ("Comfy-Org/z_image_turbo","split_files/vae/ae.safetensors","vae/z_image_ae.safetensors"),
 # 2D sprites (sprite.bat): FLUX.2 [klein] 4B, Apache-2.0, fp8 weights; shares the Qwen3-4B text encoder above
 ("black-forest-labs/FLUX.2-klein-4b-fp8","flux-2-klein-4b-fp8.safetensors","diffusion_models/flux-2-klein-4b-fp8.safetensors"),
 ("black-forest-labs/FLUX.2-klein-base-4b-fp8","flux-2-klein-base-4b-fp8.safetensors","diffusion_models/flux-2-klein-base-4b-fp8.safetensors"),
 ("Comfy-Org/Flux-2-Klein-4B","split_files/vae/flux2-vae.safetensors","vae/flux2-vae.safetensors"),
]
for repo,fn,dest in jobs:
    out=os.path.join(M,dest)
    if os.path.exists(out) and os.path.getsize(out)>1e6: print("skip",dest); continue
    print("downloading",repo,fn,flush=True)
    p=hf_hub_download(repo,fn,cache_dir=os.path.join(M,"_hfcache"))
    os.makedirs(os.path.dirname(out),exist_ok=True)
    shutil.copyfile(p,out); print("done",dest,os.path.getsize(out)//2**20,"MB",flush=True)
shutil.rmtree(os.path.join(M,"_hfcache"),ignore_errors=True)

lora=os.path.join(M,"loras","StylizedTexture_ZIT.safetensors")
token=os.environ.get("CIVITAI_TOKEN")
if os.path.exists(lora): print("skip loras/StylizedTexture_ZIT.safetensors")
elif not token: print("CIVITAI_TOKEN not set, skipping the optional StylizedTexture_ZIT LoRA (civitai.com/models/503877)")
else:
    print("downloading StylizedTexture_ZIT.safetensors from civitai",flush=True)
    req=urllib.request.Request("https://civitai.com/api/download/models/2550731?fileId=2439046",headers={"Authorization":"Bearer "+token})
    with urllib.request.urlopen(req) as r, open(lora+".part","wb") as f: shutil.copyfileobj(r,f)
    os.replace(lora+".part",lora); print("done loras/StylizedTexture_ZIT.safetensors",os.path.getsize(lora)//2**20,"MB")
print("ALL_DONE")
