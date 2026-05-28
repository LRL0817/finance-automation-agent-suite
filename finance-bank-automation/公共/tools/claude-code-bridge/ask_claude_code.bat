@echo off
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ask_claude_code.ps1" %*
exit /b %ERRORLEVEL%
