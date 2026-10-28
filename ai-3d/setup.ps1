# One-time setup: clones ComfyUI at the pinned commit, creates the venv, installs CUDA 13 PyTorch,
# applies the Turing fp16 patches, installs the workflows and downloads the models (~35 GB).
# Requires: git, uv (https://docs.astral.sh/uv/), an NVIDIA driver with CUDA 13 support.
#   powershell -ExecutionPolicy Bypass -File setup.ps1 [-SkipModels]
param([switch]$SkipModels)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$comfy = Join-Path $root "ComfyUI"
$pinned = "0b05cd8a3683cbf0c291b1472bb8b64da41de9ae"   # ComfyUI 0.42.0, 2026-10-26; the patch in patches/ applies to this commit

if (-not (Test-Path $comfy)) {
    git clone https://github.com/comfyanonymous/ComfyUI.git $comfy
    git -C $comfy checkout $pinned
}
$py = Join-Path $comfy ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    uv venv --python 3.12 (Join-Path $comfy ".venv")
}
uv pip install --python $py torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu130
uv pip install --python $py -r (Join-Path $comfy "requirements.txt")
uv pip install --python $py "huggingface_hub[hf_transfer]"

# fp16 inference for TRELLIS.2 / Pixal3D on GPUs without bf16 (RTX 20-series). Harmless elsewhere.
$patch = Join-Path $root "patches\comfyui-turing-fp16.patch"
git -C $comfy apply --check $patch 2>$null
if ($LASTEXITCODE -eq 0) { git -C $comfy apply $patch; "applied $patch" } else { "patch already applied or does not apply cleanly; check manually" }

$wf = Join-Path $comfy "user\default\workflows"
New-Item -ItemType Directory -Force $wf | Out-Null
Copy-Item (Join-Path $root "workflows\*.json") $wf -Force

# our own ComfyUI nodes (seamless tiling for the texture workflow)
Copy-Item (Join-Path $root "custom_nodes\*") (Join-Path $comfy "custom_nodes") -Recurse -Force

if (-not $SkipModels) {
    & $py -I (Join-Path $root "scripts\download_models.py")
}
"Setup done. Run start_comfyui.bat, then generate.bat ""a potted plant"" --name PottedPlant"
"Optional: set CIVITAI_TOKEN and re-run scripts\download_models.py to fetch the StylizedTexture_ZIT LoRA for texture.bat"
