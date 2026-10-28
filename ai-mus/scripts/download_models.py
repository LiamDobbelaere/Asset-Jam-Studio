"""Download the ACE-Step 1.5 weights into models/ (run by setup.ps1).

Mirrors acestep/model_downloader.py: the main repo (DiT, VAE, text encoder, 1.7B LM) plus the 0.6B LM
that fits next to the DiT on an 8 GB card, then the model code files shipped in the clone are copied
over the ones in the checkpoint folder (upstream does the same after every download).
"""
import os
import shutil
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parent.parent
ACESTEP = ROOT / "ACE-Step-1.5"
MODELS = ROOT / "models"

os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")

DOWNLOADS = [
    # repo, target folder (relative to models/)
    ("ACE-Step/Ace-Step1.5", "."),                     # ~10 GB, MIT: vae, Qwen3-Embedding-0.6B, acestep-v15-turbo (2B DiT), acestep-5Hz-lm-1.7B
    ("ACE-Step/acestep-5Hz-lm-0.6B", "acestep-5Hz-lm-0.6B"),   # ~1.2 GB: the LM that fits next to the DiT in 8 GB
]
# checkpoint folder -> acestep/models/<variant>/ whose .py files upstream copies into it after download
CODE_SYNC = {"acestep-v15-turbo": "turbo", "acestep-v15-sft": "sft", "acestep-v15-base": "base"}

MODELS.mkdir(exist_ok=True)
for repo, folder in DOWNLOADS:
    dest = (MODELS / folder).resolve()
    print(f"{repo} -> {dest}", flush=True)
    snapshot_download(repo, local_dir=str(dest))

for ckpt, variant in CODE_SYNC.items():
    src, dst = ACESTEP / "acestep" / "models" / variant, MODELS / ckpt
    if dst.is_dir() and src.is_dir():
        for py in src.glob("*.py"):
            if py.name != "__init__.py":
                shutil.copyfile(py, dst / py.name)
                print(f"synced {py.name} -> {dst.name}", flush=True)
print("models ready", flush=True)
