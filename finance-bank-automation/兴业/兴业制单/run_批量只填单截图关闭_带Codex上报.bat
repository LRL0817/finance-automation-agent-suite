@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
set "PYTHONDONTWRITEBYTECODE=1"
"%PYTHON_EXE%" "C:\Users\30112\Desktop\网关\scripts\run_and_report.py" --project-path "%CD%" --title "兴业批量只填单失败" --source "兴业 run_批量只填单截图关闭" --cwd "%CD%" -- "%PYTHON_EXE%" "batch_fill_only.py" --fresh
set "AUTOMATION_EXIT=%ERRORLEVEL%"
set "FINANCE_CLEANUP=%~dp0..\..\公共\maintenance\cleanup_after_automation.bat"
if exist "%FINANCE_CLEANUP%" call "%FINANCE_CLEANUP%"
pause
exit /b %AUTOMATION_EXIT%
