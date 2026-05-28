@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "ABC_USB12_PREPARE=true"
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%~dp0scripts\query_balance.py"
set "AUTOMATION_EXIT=%ERRORLEVEL%"
set "FINANCE_CLEANUP=%~dp0..\..\公共\maintenance\cleanup_after_automation.bat"
if exist "%FINANCE_CLEANUP%" call "%FINANCE_CLEANUP%"
pause
exit /b %AUTOMATION_EXIT%
