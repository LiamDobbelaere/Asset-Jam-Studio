@echo off
REM Starts the ComfyUI server (text -> textured 3D). Open http://127.0.0.1:8188 in a browser.
cd /d "%~dp0ComfyUI"
set PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
".venv\Scripts\python.exe" main.py --listen 127.0.0.1 --port 8188 --preview-method auto %*
