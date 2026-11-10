@echo off
REM Usage: pixel.bat "prompt" [--kind sprite|scene|none] [--image src.png] [--width 64 --height 64] [--colors 32] [--name Name]
REM        pixel.bat --animate assets\pixel\Name.png "what moves" [--frames 8] [--fps 8] [--name NameWalk]
"%~dp0ComfyUI\.venv\Scripts\python.exe" "%~dp0scripts\pixel.py" %*
