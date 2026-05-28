@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
set "PYTHONDONTWRITEBYTECODE=1"
"%PYTHON_EXE%" "C:\Users\30112\Desktop\网关\scripts\run_and_report.py" --project-path "%CD%" --title "招行批量测试失败" --source "招行 run_batch_from_screenshots" --cwd "%CD%" -- powershell.exe -NoProfile -ExecutionPolicy Bypass -File "run_batch_from_screenshots.ps1"
set "AUTOMATION_EXIT=%ERRORLEVEL%"
set "FINANCE_CLEANUP=%~dp0..\..\公共\maintenance\cleanup_after_automation.bat"
if exist "%FINANCE_CLEANUP%" call "%FINANCE_CLEANUP%"
pause
exit /b %AUTOMATION_EXIT%
