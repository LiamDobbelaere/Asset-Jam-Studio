@echo off
REM Usage: compose.bat "calm piano and strings, village at dusk" [--duration 60] [--loop] [--takes 3] [--seed N] [--lyrics lines.txt] [--out file.wav]
"%~dp0.venv\Scripts\python.exe" -I "%~dp0scripts\compose.py" %*
