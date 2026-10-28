# One-time setup for ai-voc: creates the venv, installs CUDA 13 PyTorch + qwen-tts, downloads the models (~6.5 GB).
# Requires: uv (https://docs.astral.sh/uv/), an NVIDIA driver with CUDA 13 support, ffmpeg on PATH (mp3 refs).
#   powershell -ExecutionPolicy Bypass -File setup.ps1 [-SkipModels]
param([switch]$SkipModels)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $py)) {
    uv venv --python 3.12 (Join-Path $root ".venv")
}
uv pip install --python $py torch torchaudio --index-url https://download.pytorch.org/whl/cu130
uv pip install --python $py -r (Join-Path $root "requirements.txt")

if (-not $SkipModels) {
    $env:HF_HUB_ENABLE_HF_TRANSFER = "1"
    & $py -I (Join-Path $root "scripts\download_models.py")
}
"Setup done. Prepare a voice:  prep_voice.bat C:\recordings\myvoice --name myvoice"
"Then speak:                  speak.bat ""Good morning, everyone."" --voice liam"
