@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"
set "M3_CONTRACT_REPORT_NAME=合同付款（凭证）"
"%PYTHON_EXE%" "C:\Users\30112\Desktop\网关\scripts\run_and_report.py" --project-path "%CD%" --title "招行M3最新30条测试自动化失败" --source "M3 latest30 -> CMB test automation" --cwd "%CD%" -- "%PYTHON_EXE%" "m3_latest30_cmb_test_runner.py" --limit 30 --usb-port 7 --fail-on-extract-failures
set "AUTOMATION_EXIT=%ERRORLEVEL%"
set "FINANCE_CLEANUP=%~dp0..\..\公共\maintenance\cleanup_after_automation.bat"
if exist "%FINANCE_CLEANUP%" call "%FINANCE_CLEANUP%"
exit /b %AUTOMATION_EXIT%
