@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
set "PYTHONDONTWRITEBYTECODE=1"

echo ============================================================
echo [来参缘调拨 - 生产入口]
echo   * 本入口不会自动进入生产模式。
echo   * 必须由操作员在当前 PowerShell / CMD 会话里显式设置:
echo         $env:LAICANYUAN_TRANSFER_PRODUCTION="1"   (PowerShell)
echo         set LAICANYUAN_TRANSFER_PRODUCTION=1      (cmd.exe)
echo     再启动本 .bat。
echo   * 未设置时本 .bat 仍会启动 runner，但 runner 内部会强制把子进程
echo     ZHIDAN_TEST_MODE 写回 "1"（测试模式），只点第一次「经办」并截图，
echo     不会向招行待审核/待复核队列提交真实经办流水。
echo   * 即使进入生产模式，本流程也只把单据录入招行待审核/待复核队列后
echo     立即停止；绝不自动复核 / 授权 / 确认 / 最终付款 / 出款。
echo     复核、授权、付款仍由人工和招行系统完成。
echo   * 如只想做一次测试 / 验证，请改用同目录下的
echo         run_laicanyuan_transfer_测试.bat
echo     该 .bat 会显式清空 LAICANYUAN_TRANSFER_PRODUCTION，确保走测试模式。
echo ============================================================

rem 本 .bat 绝不自动设置 LAICANYUAN_TRANSFER_PRODUCTION。
rem 本 .bat 也绝不写真实账号 / 户名 / 支行 / 金额；这些只在 gitignored 的
rem laicanyuan_payee.local.json 里维护，并由 runner 在运行时读取。
if "%LAICANYUAN_TRANSFER_PRODUCTION%"=="" (
  echo.
  echo [提示] 当前会话未设置 LAICANYUAN_TRANSFER_PRODUCTION。
  echo [提示] runner 仍将以测试模式继续执行：只点第一次「经办」并截图。
  echo [提示] 若你确认本次需要真实经办进入待审核队列，请按以上说明设置后重新运行。
  echo [提示] 若只想验证流程，建议改跑 run_laicanyuan_transfer_测试.bat。
  echo.
) else if /I "%LAICANYUAN_TRANSFER_PRODUCTION%"=="1" (
  echo.
  echo [生产模式] 检测到 LAICANYUAN_TRANSFER_PRODUCTION=1。
  echo [生产模式] runner 会向子进程注入 ZHIDAN_TEST_MODE=0，执行真实经办，把单据录入
  echo            招行待审核/待复核队列后停止；后续复核 / 授权 / 付款由人工完成。
  echo.
) else (
  echo.
  echo [提示] 当前会话 LAICANYUAN_TRANSFER_PRODUCTION=[%LAICANYUAN_TRANSFER_PRODUCTION%]
  echo [提示] 仅 "1" 被视为生产开关；其它取值 runner 会按测试模式处理。
  echo.
)

"%PYTHON_EXE%" "C:\Users\30112\Desktop\网关\scripts\run_and_report.py" --project-path "%CD%" --title "来参缘调拨失败" --source "来参缘调拨 runner" --cwd "%CD%" -- "%PYTHON_EXE%" "%~dp0run_laicanyuan_transfer.py" %*
set "AUTOMATION_EXIT=%ERRORLEVEL%"

set "FINANCE_CLEANUP=%~dp0..\公共\maintenance\cleanup_after_automation.bat"
if exist "%FINANCE_CLEANUP%" call "%FINANCE_CLEANUP%"
pause
exit /b %AUTOMATION_EXIT%