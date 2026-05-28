@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONDONTWRITEBYTECODE=1"
set "FINANCE_HOME=C:\Users\30112\Desktop\财务"
set "CODEX_GATEWAY_HOME=C:\Users\30112\Desktop\网关"
set "M3_AUTO_REPORT_ERRORS=0"
set "M3_MONITOR_REPORT_ERRORS=0"
set "BANK_ROUTE_REPORT_ERRORS=0"
set "M3_PRODUCTION_MODE=1"
set "M3_BANK_AMOUNT_OVERRIDE="
set "M3_CMB_USB_PORT="
set "M3_CONTRACT_REPORT_NAME=合同付款（凭证）"
set "M3_REPORT_NAMES=合同付款（云链）;合同付款（凭证）;日常报销;对公请款（云链）;对公请款（社保公积金）"
set "USB_HUB_FORCE_PORT="
set "ZHIDAN_ALLOW_FORCE_USB_PORT="
set "CIB_ALLOW_SUBMIT="
set "BOC_ENABLE_ORDER_SUBMIT="
set "M3_ENABLE_SCREENSHOTS=1"
set "M3_REQUIRE_SCREENSHOTS=1"
set "M3_SCAN_ROW_LIMIT=30"
set "M3_SCAN_FORCE_DETAILS=0"
set "M3_DIRECT_REPORT_FIRST=1"
set "OA_NAVIGATION_RETRIES=4"
set "M3_BROWSER_RESTARTS=2"
set "OA_RETRY_DELAY_SECONDS=8"
set "OA_NAVIGATION_BACKOFF_SECONDS=8,20,45"

"%PYTHON_EXE%" "C:\Users\30112\Desktop\网关\scripts\run_and_report.py" --project-path "%CD%" --title "M3生产自动调度进程异常退出" --source "M3 auto_extract_dispatch production" --cwd "%CD%" -- "%PYTHON_EXE%" "auto_extract_dispatch.py" --poll-seconds 60 --quiet-idle --notify-final
set "AUTOMATION_EXIT=%ERRORLEVEL%"
set "FINANCE_CLEANUP=C:\Users\30112\Desktop\财务\公共\maintenance\cleanup_after_automation.bat"
if exist "%FINANCE_CLEANUP%" call "%FINANCE_CLEANUP%"
exit /b %AUTOMATION_EXIT%
