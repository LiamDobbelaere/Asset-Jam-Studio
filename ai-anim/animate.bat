@echo off
REM Usage: animate.bat rig <mesh.glb> --name Hero | anim Hero --clip "Walk=a person walks forward" | full <mesh> --name Hero --clip ... | motion "..." --out stem
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\animate.py" %*
