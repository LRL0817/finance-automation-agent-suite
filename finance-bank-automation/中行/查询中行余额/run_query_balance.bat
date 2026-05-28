@echo off
chcp 65001 >nul
cd /d "%~dp0"
python scripts\query_balance.py
set "AUTOMATION_EXIT=%ERRORLEVEL%"
set "FINANCE_CLEANUP=%~dp0..\..\公共\maintenance\cleanup_after_automation.bat"
if exist "%FINANCE_CLEANUP%" call "%FINANCE_CLEANUP%"
exit /b %AUTOMATION_EXIT%
