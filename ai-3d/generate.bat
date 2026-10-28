@echo off
REM Usage: generate.bat "a wooden treasure chest" [--seed N] [--model pixal3d|trellis2] [--resolution 1024] [--texture 2048]
"%~dp0ComfyUI\.venv\Scripts\python.exe" "%~dp0scripts\generate.py" %*
