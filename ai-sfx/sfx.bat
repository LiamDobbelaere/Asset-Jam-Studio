@echo off
REM Usage: sfx.bat "a huge explosion with debris falling" [--duration 4] [--takes 3] [--seed N] [--loop] [--out file.wav]
"%~dp0.venv\Scripts\python.exe" -I "%~dp0scripts\sfx.py" %*
