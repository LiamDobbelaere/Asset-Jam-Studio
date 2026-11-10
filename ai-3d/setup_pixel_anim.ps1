# Optional: animated pixel art with Wan 2.2 14B (pixel.bat --animate --engine wan14, studio kind pixel_anim).
# Installs city96/ComfyUI-GGUF (pinned) into ai-3d's ComfyUI so the 14B experts load as 4-bit GGUF files on 8 GB,
# then downloads ~21 GB of weights. Needs setup.ps1 first.
#   powershell -ExecutionPolicy Bypass -File setup_pixel_anim.ps1 [-SkipModels]
param([switch]$SkipModels)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$comfy = Join-Path $root "ComfyUI"
$py = Join-Path $comfy ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "run setup.ps1 first (no ComfyUI venv at $py)" }

$nodes = Join-Path $comfy "custom_nodes\ComfyUI-GGUF"
if (-not (Test-Path $nodes)) {
    git clone https://github.com/city96/ComfyUI-GGUF.git $nodes
    git -C $nodes checkout 6ea2651   # 2026-01-12, the tested commit
}
uv pip install --python $py "gguf>=0.13.0"

if (-not $SkipModels) {
    & $py -I (Join-Path $root "scripts\download_pixel_anim_models.py")
}
"Setup done. Restart ComfyUI (studio header, or start_comfyui.bat), then try:"
"  pixel.bat --animate assets\pixel\Knight.png ""the knight walks in place facing right"" --name KnightWalk"
