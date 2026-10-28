# One-time setup for ai-mus: clones ACE-Step 1.5 at a pinned commit, creates the venv (Python 3.12, torch 2.7.1+cu128),
# installs the runtime subset of its requirements and downloads the weights (~11 GB).
# Requires: uv (https://docs.astral.sh/uv/), git, an NVIDIA driver with CUDA 12.8 support.
#   powershell -ExecutionPolicy Bypass -File setup.ps1 [-SkipModels]
param([switch]$SkipModels)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$clone = Join-Path $root "ACE-Step-1.5"
$commit = "ca1e85fe9430179831e6bc6be790c332190a3866"   # 2026-08-29; bump deliberately, re-run setup afterwards

if (-not (Test-Path (Join-Path $clone "pyproject.toml"))) {
    git clone https://github.com/ace-step/ACE-Step-1.5.git $clone
}
git -C $clone fetch --depth 1 origin $commit
git -C $clone checkout --quiet $commit

if (-not (Test-Path $py)) {
    uv venv --python 3.12 (Join-Path $root ".venv")
}
# the torch build ACE-Step pins for Windows; cu128 wheels run on the RTX 2080 (sm_75)
uv pip install --python $py torch==2.7.1 torchaudio==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu128
uv pip install --python $py -r (Join-Path $root "requirements.txt")
# the package itself without its dependency list (gradio, fastapi, nano-vllm, training stack are not needed here)
uv pip install --python $py --no-deps -e $clone

if (-not $SkipModels) {
    $env:HF_HUB_ENABLE_HF_TRANSFER = "1"
    & $py -I (Join-Path $root "scripts\download_models.py")
}
"Setup done. Try:  compose.bat ""calm piano and soft strings, a quiet village at dusk"" --duration 45 --loop"
