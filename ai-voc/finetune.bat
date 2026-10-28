@echo off
REM Usage: finetune.bat --voice liam [--epochs 8] [--lr 1e-4] [--rank 16]
"%~dp0.venv\Scripts\python.exe" -I "%~dp0scripts\finetune.py" %*
