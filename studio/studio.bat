@echo off
REM Asset Jam Studio: local web UI + HTTP API for ai-3d, ai-voc, ai-mus, ai-vid and ai-anim (see README.md, API.md).
REM Usage: studio.bat [--port 8765] [--start-comfy] [--no-browser]
set PY=%~dp0..\ai-3d\ComfyUI\.venv\Scripts\python.exe
if not exist "%PY%" (
    echo ai-3d\ComfyUI\.venv not found; run ai-3d\setup.ps1 first ^(the studio runs with that Python^).
    exit /b 1
)
"%PY%" "%~dp0server.py" %*
