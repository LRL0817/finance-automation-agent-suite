<#
  M3 生产自动调度 —— 24 小时守护层 (watchdog)

  作用：
    外层循环反复拉起【现有】生产入口
    run_auto_extract_dispatch_生产_带Codex上报.bat。
    生产 bat 仍是环境变量与网关上报的唯一来源，本脚本不复制那段 env，
    只负责「异常退出后等待若干秒再重启」，让链路更适合 24 小时运行。

  明确不做的事（业务边界不变）：
    - 不打开任何银行客户端 / OA / USBHub / U盾 / K宝 / 物理 OK；这些由
      子流程在生产模式下按既有门禁处理。本脚本只管「进程级重启」。
    - 不自动复核、授权、最终付款。
    - 不吞退出码：每轮把生产 bat 的 ExitCode 写日志；失败上报仍由生产
      bat 内部的网关 run_and_report.py 负责。
    - 不并发：本脚本自身单实例（watchdog 锁）；并尊重 monitor 锁，
      绝不对 monitor 传 --force-lock。仅当 monitor 锁所属 PID 已不存在
      （确凿陈旧锁）时才安全清理那一个锁文件，并打印清楚原因。

  崩溃恢复：
    设置 M3_MONITOR_RESUME_EXISTING=1（等价 --resume-existing）。该开关
    只把上次未完成(running)的项恢复为 interrupted 重新处理，不会重跑
    succeeded/seen，也不影响 failed —— 不会整批误跑历史队列。

  用法（不要在本任务里实际运行；仅供运行者手动执行）：
    powershell -NoProfile -ExecutionPolicy Bypass -File `
      "C:\Users\30112\Desktop\M3直供合同付款数据获取\run_auto_extract_dispatch_生产_watchdog.ps1"

  可调环境变量：
    M3_WATCHDOG_RESTART_DELAY_SECONDS  异常退出后重启等待秒数（默认 45，钳制 30..120）
#>

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ScriptDir

$ProductionBat = Join-Path $ScriptDir 'run_auto_extract_dispatch_生产_带Codex上报.bat'
$WatchdogDir   = Join-Path $ScriptDir 'runtime\watchdog'
$MonitorLock   = Join-Path $ScriptDir 'runtime\monitor\auto_bank_monitor.lock'
$WatchdogLock  = Join-Path $WatchdogDir 'watchdog.lock'

New-Item -ItemType Directory -Force -Path $WatchdogDir | Out-Null
$LogFile = Join-Path $WatchdogDir ("watchdog_{0}.log" -f (Get-Date -Format 'yyyyMMdd'))

function Write-WatchdogLog([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Write-Host $line
    Add-Content -LiteralPath $LogFile -Value $line -Encoding utf8
}

function Test-PidAlive([int]$procId) {
    if ($procId -le 0) { return $false }
    try { $null = Get-Process -Id $procId -ErrorAction Stop; return $true }
    catch { return $false }
}

# ---- 重启等待秒数：默认 45，钳制 30..120 ----
$RestartDelay = 45
$rawDelay = $env:M3_WATCHDOG_RESTART_DELAY_SECONDS
if ($rawDelay) {
    $parsed = 0
    if ([int]::TryParse($rawDelay, [ref]$parsed)) {
        $RestartDelay = [Math]::Max(30, [Math]::Min(120, $parsed))
    }
}

if (-not (Test-Path -LiteralPath $ProductionBat)) {
    Write-WatchdogLog "[致命] 找不到生产入口 bat：$ProductionBat —— watchdog 退出。"
    exit 2
}

# ---- watchdog 自身单实例锁（防止并发起多个 watchdog） ----
if (Test-Path -LiteralPath $WatchdogLock) {
    $oldPid = 0
    try { [int]::TryParse(((Get-Content -LiteralPath $WatchdogLock -ErrorAction SilentlyContinue | Select-Object -First 1)), [ref]$oldPid) | Out-Null } catch {}
    if (Test-PidAlive $oldPid) {
        Write-WatchdogLog "[跳过] 已有 watchdog 实例在运行 (PID=$oldPid)，本次不重复启动。"
        exit 0
    }
    Write-WatchdogLog "[清理] 发现陈旧 watchdog 锁 (PID=$oldPid 不存在)，安全清理后继续。"
    Remove-Item -LiteralPath $WatchdogLock -Force -ErrorAction SilentlyContinue
}
Set-Content -LiteralPath $WatchdogLock -Value $PID -Encoding ascii

# ---- 崩溃恢复开关：只 running→interrupted，安全（见脚本头注释） ----
$env:M3_MONITOR_RESUME_EXISTING = '1'

Write-WatchdogLog "watchdog 启动：生产入口=$ProductionBat 重启等待=${RestartDelay}s 恢复模式=M3_MONITOR_RESUME_EXISTING=1 日志=$LogFile"

try {
    $cycle = 0
    while ($true) {
        $cycle++

        # 尊重 monitor 锁：绝不盲目 --force-lock。
        # 仅当锁所属 PID 已不存在（确凿陈旧）才安全清理那一个锁文件。
        if (Test-Path -LiteralPath $MonitorLock) {
            $lockPid = 0
            try {
                $lockLine = Get-Content -LiteralPath $MonitorLock -ErrorAction SilentlyContinue | Where-Object { $_ -match 'pid=' } | Select-Object -First 1
                if ($lockLine -match 'pid=(\d+)') { $lockPid = [int]$Matches[1] }
            } catch {}
            if (Test-PidAlive $lockPid) {
                Write-WatchdogLog "[等待] monitor 锁被活进程持有 (PID=$lockPid)，说明已有调度在跑；本轮不启动，${RestartDelay}s 后复查。"
                Start-Sleep -Seconds $RestartDelay
                continue
            }
            Write-WatchdogLog "[清理] monitor 锁陈旧 (PID=$lockPid 不存在，来自上次崩溃)，安全删除该锁文件后继续；不使用 --force-lock。"
            Remove-Item -LiteralPath $MonitorLock -Force -ErrorAction SilentlyContinue
        }

        Write-WatchdogLog "第 $cycle 轮：启动生产入口 ..."
        $startedAt = Get-Date
        # 通过 cmd.exe 调用现有生产 bat：env + 网关上报仍由该 bat 内部负责。
        & cmd.exe /c "`"$ProductionBat`""
        $code = $LASTEXITCODE
        $dur = [int]((Get-Date) - $startedAt).TotalSeconds
        Write-WatchdogLog "第 $cycle 轮结束：ExitCode=$code 运行时长=${dur}s（退出码不被吞掉，失败上报由生产 bat 内网关负责）。"

        Write-WatchdogLog "等待 ${RestartDelay}s 后重启（24 小时守护：进程异常退出即重拉，但不能保证银行/OA/USB 硬件可用）。"
        Start-Sleep -Seconds $RestartDelay
    }
}
finally {
    Remove-Item -LiteralPath $WatchdogLock -Force -ErrorAction SilentlyContinue
    Write-WatchdogLog "watchdog 退出，已释放 watchdog 锁。"
}
