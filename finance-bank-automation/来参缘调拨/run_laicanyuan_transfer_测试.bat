@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
set "PYTHONDONTWRITEBYTECODE=1"

rem 测试模式专用入口：runner 内部强制把子进程 ZHIDAN_TEST_MODE 写回 "1"，
rem 只点第一次「经办」并截图后停止；绝不向招行待审核/待复核队列写入真实经办流水。
rem 显式清空 LAICANYUAN_TRANSFER_PRODUCTION，避免父会话偷渡生产开关。
set "LAICANYUAN_TRANSFER_PRODUCTION="

"%PYTHON_EXE%" "%~dp0run_laicanyuan_transfer.py" %*
set "AUTOMATION_EXIT=%ERRORLEVEL%"

set "FINANCE_CLEANUP=%~dp0..\公共\maintenance\cleanup_after_automation.bat"
if exist "%FINANCE_CLEANUP%" call "%FINANCE_CLEANUP%"
pause
exit /b %AUTOMATION_EXIT%