# Optional: painting modelled props (paint.bat, the studio's modelled-prop and paint flows).
# Installs the IP-Adapter Plus ComfyUI nodes (cubiq/ComfyUI_IPAdapter_plus, pinned) and downloads the SDXL models the
# paint uses (~12 GB: RealVisXL 5, SDXL-Lightning 8-step LoRA, depth ControlNet, IP-Adapter Plus + ViT-H encoder).
# Requires: setup.ps1 done, git, Blender 5.1 (or the BLENDER env var).
#   powershell -ExecutionPolicy Bypass -File setup_paint.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$comfy = Join-Path $root "ComfyUI"
$py = Join-Path $comfy ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "run setup.ps1 first ($py not found)" }

# the camera fit (scripts\paint_fit.py) loads the GLB with trimesh
uv pip install --python $py trimesh

$nodes = Join-Path $comfy "custom_nodes\ComfyUI_IPAdapter_plus"
if (-not (Test-Path $nodes)) {
    git clone https://github.com/cubiq/ComfyUI_IPAdapter_plus.git $nodes
}
git -C $nodes checkout a0f451a5113cf9becb0847b92884cb10cbdec0ef

& $py -I (Join-Path $root "scripts\download_paint_models.py")
"Paint setup done. Restart ComfyUI so it loads the IP-Adapter nodes, then: paint.bat Model.glb picture.png ""a wooden desk | drawers on the front | plain back"""
