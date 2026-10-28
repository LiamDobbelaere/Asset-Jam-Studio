# One-time setup for ai-sfx: clones MOSS-TTS (for its moss_soundeffect_v2 package) at a pinned commit, creates the
# venv (Python 3.12, torch 2.9.0+cu128), installs the inference subset of its requirements and downloads the
# MOSS-SoundEffect v2.0 weights (~11 GB).
# Requires: uv (https://docs.astral.sh/uv/), git, an NVIDIA driver with CUDA 12.8 support.
#   powershell -ExecutionPolicy Bypass -File setup.ps1 [-SkipModels]
param([switch]$SkipModels)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$clone = Join-Path $root "MOSS-TTS"
$commit = "934d6826b084c46a0d033402174d5f8ac4ed2519"   # 2026-09-06; bump deliberately, re-run setup afterwards

if (-not (Test-Path (Join-Path $clone "moss_soundeffect_v2\pyproject.toml"))) {
    git clone --filter=blob:none https://github.com/OpenMOSS/MOSS-TTS.git $clone
}
git -C $clone fetch --depth 1 origin $commit
git -C $clone checkout --quiet $commit

if (-not (Test-Path $py)) {
    uv venv --python 3.12 (Join-Path $root ".venv")
}
# the torch build moss_soundeffect_v2 pins; cu128 wheels still run on the RTX 2080 (sm_75)
uv pip install --python $py torch==2.9.0 torchaudio==2.9.0 --index-url https://download.pytorch.org/whl/cu128
uv pip install --python $py -r (Join-Path $root "requirements.txt")
# the package itself without its dependency list (gradio, torchcodec and the fine-tuning stack are not needed here)
uv pip install --python $py --no-deps -e (Join-Path $clone "moss_soundeffect_v2")

if (-not $SkipModels) {
    $env:HF_HUB_ENABLE_HF_TRANSFER = "1"
    & $py -I (Join-Path $root "scripts\download_models.py")
}
"Setup done. Try:  sfx.bat ""a huge explosion with debris falling"" --duration 4"
