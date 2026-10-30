@echo off
REM Usage: paint.bat Model.glb picture.png "shared prompt | front-only details | plain wording" [--name Desk] [--lock Snow,Floor] [--low]
"%~dp0ComfyUI\.venv\Scripts\python.exe" "%~dp0scripts\paint.py" %*
