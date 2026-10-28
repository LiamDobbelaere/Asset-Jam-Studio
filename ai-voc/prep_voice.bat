@echo off
REM Usage: prep_voice.bat C:\recordings\liam --name liam [--ref-seconds 12] [--ref "substring of a clip name" ...]
"%~dp0.venv\Scripts\python.exe" -I "%~dp0scripts\prep_voice.py" %*
