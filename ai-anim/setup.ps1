# One-time setup for ai-anim: clones Make-It-Animatable (rigging) and NVIDIA Kimodo (text-to-motion) at pinned
# commits, creates the venv (Python 3.12, torch cu130), installs the runtime dependencies and downloads the
# Make-It-Animatable weights (~2.2 GB). Kimodo's weights (~1.2 GB) and its Llama-3-8B text encoder (~16 GB)
# are fetched on first use from the Hugging Face cache; the Llama repo is gated, so run `hf auth login`
# with an account that has access to meta-llama/Meta-Llama-3-8B-Instruct before the first animation.
# Requires: uv, git, Blender 5.x (BLENDER env var or C:\Program Files\Blender Foundation\Blender 5.1).
#   powershell -ExecutionPolicy Bypass -File setup.ps1 [-SkipModels]
param([switch]$SkipModels)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"

$mia = Join-Path $root "mia"
$kimodo = Join-Path $root "kimodo"
$pins = Get-Content (Join-Path $root "pins.txt") | Where-Object { $_ -match "=" } | ForEach-Object { $k, $v = $_ -split "=", 2; @{ $k.Trim() = $v.Trim() } }
$pin = @{}
foreach ($p in $pins) { $pin += $p }

if (-not (Test-Path (Join-Path $mia "model.py"))) {
    git clone https://github.com/jasongzy/Make-It-Animatable.git $mia
}
git -C $mia fetch --depth 1 origin $pin["mia"]
git -C $mia checkout --quiet $pin["mia"]

if (-not (Test-Path (Join-Path $kimodo "pyproject.toml"))) {
    git clone https://github.com/nv-tlabs/kimodo.git $kimodo
}
git -C $kimodo fetch --depth 1 origin $pin["kimodo"]
git -C $kimodo checkout --quiet $pin["kimodo"]

if (-not (Test-Path $py)) {
    uv venv --python 3.12 (Join-Path $root ".venv")
}
# CUDA 13 wheels (same as the ai-3d ComfyUI venv); cu128 also works on the RTX 2080
uv pip install --python $py torch torchvision --index-url https://download.pytorch.org/whl/cu130
uv pip install --python $py -r (Join-Path $root "requirements.txt")
# Neither repo is pip-installed: Kimodo's setup.py builds a C++ extension (foot-skate post-processing, not used
# here) and Make-It-Animatable needs pytorch3d / torch_cluster / bpy, which vendor/ and scripts/mia_compat.py
# replace. The scripts add mia/ and kimodo/ to sys.path themselves.

if (-not $SkipModels) {
    & $py -I (Join-Path $root "scripts\download_models.py")
}
"Setup done. Try:  animate.bat full ..\ai-3d\assets\Hero.glb --name Hero --clip ""Idle=a person stands and looks around"""
