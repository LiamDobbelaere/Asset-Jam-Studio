@echo off
REM Usage: texture.bat "dark oak parquet floor planks" --name ParquetFloorDark [--width 1024 --height 2048] [--seed N]
"%~dp0ComfyUI\.venv\Scripts\python.exe" "%~dp0scripts\texture.py" %*
