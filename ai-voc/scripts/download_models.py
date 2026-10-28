"""Download the ai-voc model weights into models/ (run by setup.ps1)."""
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts run with python -I
from common import MODELS

DOWNLOADS = [
    # repo, local folder, allow_patterns
    ("Qwen/Qwen3-TTS-12Hz-1.7B-Base", "Qwen3-TTS-12Hz-1.7B-Base", None),      # 4.3 GB, Apache-2.0: the TTS + voice cloning model
    ("Qwen/Qwen3-TTS-Tokenizer-12Hz", "Qwen3-TTS-Tokenizer-12Hz", None),      # 0.65 GB: speech codec (needed by the model and by finetune.py)
    ("openai/whisper-large-v3-turbo", "whisper-large-v3-turbo",               # 1.6 GB, MIT: transcribes reference clips in prep_voice.py
     ["*.json", "*.txt", "model.safetensors"]),
]

for repo, folder, patterns in DOWNLOADS:
    dest = MODELS / folder
    print(f"{repo} -> {dest}", flush=True)
    snapshot_download(repo, local_dir=str(dest), allow_patterns=patterns)
print("models ready")
