@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONDONTWRITEBYTECODE=1
REM 安全默认：不内置 CIB_ALLOW_SUBMIT=1，默认运行只填单不提交。
REM 如需真实提交：操作人必须先确认 DEFAULT_TRANSFER 已替换为真实数据，
REM 再在命令行临时执行  set CIB_ALLOW_SUBMIT=1  后手动运行 open_bank.py。
python open_bank.py
set "AUTOMATION_EXIT=%ERRORLEVEL%"
set "FINANCE_CLEANUP=%~dp0..\..\公共\maintenance\cleanup_after_automation.bat"
if exist "%FINANCE_CLEANUP%" call "%FINANCE_CLEANUP%"
pause
exit /b %AUTOMATION_EXIT%
