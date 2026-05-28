@echo off
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
python maintain_workspace.py --apply
pause
