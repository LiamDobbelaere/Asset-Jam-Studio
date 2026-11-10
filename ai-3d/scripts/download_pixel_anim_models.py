"""Download the weights for pixel.bat --animate --engine wan14 (about 21 GB) into ComfyUI/models. Run via
setup_pixel_anim.ps1 or:
    ComfyUI/.venv/Scripts/python.exe -I scripts/download_pixel_anim_models.py

Wan 2.2 I2V A14B as Q4_K_S GGUF (QuantStack), the lightx2v 4-step distillation LoRAs, styly-agents' pixel-animate LoRA
(rank 256, Apache-2.0), the Wan 2.1 VAE and the umT5 text encoder (shared with ai-vid).
"""
import os, shutil
os.environ["HF_HUB_ENABLE_HF_TRANSFER"]="1"
from huggingface_hub import hf_hub_download
M=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ComfyUI", "models")
jobs=[
 ("QuantStack/Wan2.2-I2V-A14B-GGUF","HighNoise/Wan2.2-I2V-A14B-HighNoise-Q4_K_S.gguf","unet/Wan2.2-I2V-A14B-HighNoise-Q4_K_S.gguf"),
 ("QuantStack/Wan2.2-I2V-A14B-GGUF","LowNoise/Wan2.2-I2V-A14B-LowNoise-Q4_K_S.gguf","unet/Wan2.2-I2V-A14B-LowNoise-Q4_K_S.gguf"),
 ("lightx2v/Wan2.2-Distill-Loras","wan2.2_i2v_A14b_high_noise_lora_rank64_lightx2v_4step_1022.safetensors","loras/wan2.2_i2v_high_noise_lightx2v_4step.safetensors"),
 ("lightx2v/Wan2.2-Distill-Loras","wan2.2_i2v_A14b_low_noise_lora_rank64_lightx2v_4step_1022.safetensors","loras/wan2.2_i2v_low_noise_lightx2v_4step.safetensors"),
 ("styly-agents/Wan2-2-pixel-animate","wan2.2_animate_adapter_model.safetensors","loras/wan2.2_pixel_animate.safetensors"),
 ("Comfy-Org/Wan_2.1_ComfyUI_repackaged","split_files/vae/wan_2.1_vae.safetensors","vae/wan_2.1_vae.safetensors"),
 ("Comfy-Org/Wan_2.2_ComfyUI_Repackaged","split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors","text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"),
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
