"""Download the MOSS-SoundEffect v2.0 weights into models/ (run by setup.ps1).

One diffusers-style repo (~11 GB, Apache-2.0): transformer/ (1.3B DiT), text_encoder/ (Qwen3-1.7B),
tokenizer/, vae/ (DAC, 48 kHz) and scheduler/.
"""
import os
from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
REPO = "OpenMOSS-Team/MOSS-SoundEffect-v2.0"

os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")
dest = MODELS / REPO.split("/")[1]
print(f"{REPO} -> {dest}", flush=True)
snapshot_download(REPO, local_dir=str(dest))
print("models ready", flush=True)
