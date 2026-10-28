# One-time setup for ai-vid: installs the aivid_wan node and the workflow into ai-3d's ComfyUI and downloads the
# Wan 2.2 TI2V-5B weights (~18 GB). Run ai-3d/setup.ps1 first (ComfyUI, venv, ffmpeg is expected on PATH or in C:\ffmpeg\bin).
#   powershell -ExecutionPolicy Bypass -File setup.ps1 [-SkipModels]
param([switch]$SkipModels)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$comfy = Join-Path (Split-Path $root) "ai-3d\ComfyUI"
$py = Join-Path $comfy ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "ai-3d/ComfyUI venv not found; run ai-3d\setup.ps1 first" }

Copy-Item (Join-Path $root "custom_nodes\*") (Join-Path $comfy "custom_nodes") -Recurse -Force
$wf = Join-Path $comfy "user\default\workflows"
New-Item -ItemType Directory -Force $wf | Out-Null
Copy-Item (Join-Path $root "workflows\*.json") $wf -Force
New-Item -ItemType Directory -Force (Join-Path $root "output") | Out-Null

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -and -not (Test-Path "C:\ffmpeg\bin\ffmpeg.exe")) {
    Write-Warning "ffmpeg not found on PATH or in C:\ffmpeg\bin: install it (https://www.gyan.dev/ffmpeg/builds/) or set FFMPEG"
}
if (-not $SkipModels) {
    & $py -I (Join-Path $root "scripts\download_models.py")
}
"Setup done. Restart ComfyUI (so it loads the aivid_wan node), then: intro.bat shots\example_intro.json"
