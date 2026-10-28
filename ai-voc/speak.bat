@echo off
REM Usage: speak.bat "the line to say" --voice liam [--out file.wav] [--seed N] [--lines lines.txt] [--model output\liam-ft --speaker liam]
"%~dp0.venv\Scripts\python.exe" -I "%~dp0scripts\speak.py" %*
