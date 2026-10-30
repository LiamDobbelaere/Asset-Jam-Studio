# Optional: painting modelled props (paint.bat, the studio's "Paint a modelled prop" form).
# Installs the StableGen Blender add-on (GPL-3.0) into its own Blender profile under blender\ (your own Blender
# settings are untouched), then its ComfyUI nodes and models with StableGen's installer (package 2: IP-Adapter Plus
# nodes, SDXL IP-Adapter, CLIP vision, depth ControlNet, Lightning LoRA) and the RealVisXL 5 SDXL checkpoint, ~16 GB.
# Requires: setup.ps1 done, Blender 5.1 (or the BLENDER env var), git.
#   powershell -ExecutionPolicy Bypass -File setup_paint.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$comfy = Join-Path $root "ComfyUI"
$py = Join-Path $comfy ".venv\Scripts\python.exe"
$blender = if ($env:BLENDER) { $env:BLENDER } else { "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" }
$ver = "0.4.0"
if (-not (Test-Path $py)) { throw "run setup.ps1 first ($py not found)" }
if (-not (Test-Path $blender)) { throw "Blender not found at $blender; set the BLENDER env var" }

$profileDir = Join-Path $root "blender"
$dl = Join-Path $profileDir "downloads"
New-Item -ItemType Directory -Force $dl | Out-Null

# the camera fit (scripts\paint_fit.py) loads the GLB with trimesh
uv pip install --python $py trimesh

# 1. the add-on, into blender\ (BLENDER_USER_RESOURCES), enabled
$zip = Join-Path $dl "StableGen-$ver-windows-x64.zip"
if (-not (Test-Path $zip)) {
    Invoke-WebRequest "https://github.com/sakalond/StableGen/releases/download/v$ver/StableGen-$ver-windows-x64.zip" -OutFile $zip
}
$env:BLENDER_USER_RESOURCES = $profileDir
$zipPy = $zip -replace '\\', '/'
& $blender -b --python-expr "import bpy; bpy.ops.extensions.package_install_files(filepath='$zipPy', repo='user_default', enable_on_install=True); bpy.ops.wm.save_userpref(); print('STABLEGEN_INSTALLED')"
Remove-Item Env:\BLENDER_USER_RESOURCES

# 2. its ComfyUI custom nodes + models (pinned commits; skips what is already there)
$inst = Join-Path $dl "installer-$ver.py"
if (-not (Test-Path $inst)) {
    Invoke-WebRequest "https://raw.githubusercontent.com/sakalond/StableGen/v$ver/installer.py" -OutFile $inst
}
& $py -I $inst $comfy --python $py --packages 2 --yes

# 3. the SDXL checkpoint StableGen recommends (OpenRAIL++)
$ckpt = Join-Path $comfy "models\checkpoints\RealVisXL_V5.0_fp16.safetensors"
if (-not (Test-Path $ckpt)) {
    & $py -I -c "from huggingface_hub import hf_hub_download as d; d('SG161222/RealVisXL_V5.0', 'RealVisXL_V5.0_fp16.safetensors', local_dir=r'$comfy\models\checkpoints')"
}
"Paint setup done. Restart ComfyUI so it loads the IP-Adapter nodes, then: paint.bat Model.glb picture.png ""a wooden desk | drawers on the front | plain back"""
