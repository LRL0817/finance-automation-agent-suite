@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
set "PYTHONDONTWRITEBYTECODE=1"
REM 安全默认：不内置 CIB_ALLOW_SUBMIT=1，默认运行只填单不提交。
REM 如需真实提交：操作人必须先确认 DEFAULT_TRANSFER 已替换为真实数据，
REM 再在命令行临时执行  set CIB_ALLOW_SUBMIT=1  后手动运行本启动器。
"%PYTHON_EXE%" "C:\Users\30112\Desktop\网关\scripts\run_and_report.py" --project-path "%CD%" --title "兴业快速制单失败" --source "兴业 run_快速制单" --cwd "%CD%" -- "%PYTHON_EXE%" "open_bank.py"
set "AUTOMATION_EXIT=%ERRORLEVEL%"
set "FINANCE_CLEANUP=%~dp0..\..\公共\maintenance\cleanup_after_automation.bat"
if exist "%FINANCE_CLEANUP%" call "%FINANCE_CLEANUP%"
pause
exit /b %AUTOMATION_EXIT%
