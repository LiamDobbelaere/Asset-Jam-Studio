@echo off
REM Usage: sprite.bat "prompt" [--image source.png] [--ref extra.png] [--name Name] [--model base] [--seed N]
"%~dp0ComfyUI\.venv\Scripts\python.exe" "%~dp0scripts\sprite.py" %*
