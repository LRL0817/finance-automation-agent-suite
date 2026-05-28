# run_laicanyuan_transfer_scheduled.ps1
#
# 计划任务专用的来参缘调拨入口。每天 10:00 / 16:00 由 Windows 任务计划程序
# 触发；不要直接手工双击运行（手工运行请用 run_laicanyuan_transfer_测试.bat
# 或 run_laicanyuan_transfer_生产_带Codex上报.bat）。
#
# 边界:
#   - 进入生产模式但只到银行待审核 / 待复核队列，不复核、不授权、不付款。
#   - 仅在本进程内设置 LAICANYUAN_TRANSFER_PRODUCTION=1，不写 .env / 用户级 /
#     机器级环境变量，wrapper 退出后该变量自动消失。
#   - 由现有 runner 内部强制把子进程 ZHIDAN_TEST_MODE 切到 0；本脚本不直接
#     设置 ZHIDAN_TEST_MODE。
#   - 依赖现有单实例锁 + 全局银行自动化锁（与 M3 互斥）；锁忙时 runner 自己
#     fail-closed 退出，本脚本只透传退出码，不自动重试、不强抢。
#   - 调度日志写到本目录 runtime\scheduler\ 下（已被 .gitignore 忽略）。

[CmdletBinding()]
param(
    [switch]$NoGatewayReport
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
} catch {
    # Windows Task Scheduler may run without a normal console; logging still works.
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location -LiteralPath $ScriptDir

$RuntimeDir = Join-Path $ScriptDir 'runtime\scheduler'
if (-not (Test-Path -LiteralPath $RuntimeDir)) {
    New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
}
$LogPath = Join-Path $RuntimeDir ("scheduled_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))

function Write-SchedLog {
    param([string]$Message)
    $stamp = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss')
    $line = "[$stamp] $Message"
    Write-Output $line
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

function Get-SchedulerReportStatus {
    param([int]$ExitCode)
    if ($ExitCode -eq 0) {
        return 'completed'
    }
    if ($ExitCode -eq 27 -or $ExitCode -eq 28) {
        return 'skipped'
    }
    return 'failed'
}

function Get-SchedulerReportReason {
    param([int]$ExitCode)
    switch ($ExitCode) {
        0 { return '定时调拨流程已完成：已按来参缘002余额规则执行，按余额向下取整到万元转出，万元以下零头留在002账户；流程只进入招行待审核/待复核队列后停止。' }
        20 { return '收方本地配置缺失或格式异常，本轮未启动银行。' }
        21 { return '来参缘招行U盾端口未配置/配置错误或USBHub切换失败，本轮未继续操作银行。' }
        22 { return '招行余额读取失败，本轮未制单。' }
        23 { return '余额页面信息无法唯一确认或存款/总资产校验未通过，本轮未制单。' }
        24 { return '余额不足1万元，本轮未制单。' }
        25 { return '按万元取整后可调拨金额异常，本轮未制单。' }
        26 { return '备份旧bank_form失败，本轮未继续制单。' }
        27 { return '来参缘调拨已有实例正在运行，本轮跳过，未启动银行。' }
        28 { return '银行自动化全局锁被占用（通常是M3或其它银行流程正在运行），本轮跳过，未启动银行。' }
        default { return "定时调拨流程异常退出，退出码 $ExitCode。若没有明确看到已进入待审核/待复核队列，请按未完成处理。" }
    }
}

function Send-GatewayReport {
    param(
        [int]$ExitCode,
        [string]$LogPath
    )
    if ($NoGatewayReport) {
        Write-SchedLog '[网关上报] 已传 -NoGatewayReport，跳过飞书/Codex 上报。'
        return
    }

    $GatewayReportScript = 'C:\Users\30112\Desktop\网关\scripts\report_to_codex.py'
    if (-not (Test-Path -LiteralPath $GatewayReportScript)) {
        Write-SchedLog "[网关上报] 未找到 report_to_codex.py，跳过上报: $GatewayReportScript"
        return
    }

    $status = Get-SchedulerReportStatus -ExitCode $ExitCode
    $reason = Get-SchedulerReportReason -ExitCode $ExitCode
    $context = @(
        '来参缘定时调拨结果通知。',
        '这是 Windows 计划任务 10:00/16:00 自动触发的结果。',
        '业务边界：只做招行经办/制单进入待审核/待复核队列后停止，不复核、不授权、不最终付款。',
        "本轮状态: $status",
        "本轮说明: $reason"
    ) -join "`n"

    $ReportArgs = @(
        '-X', 'utf8',
        $GatewayReportScript,
        '--project-path', 'C:\Users\30112\Desktop\财务\来参缘调拨',
        '--title', '来参缘定时调拨结果',
        '--status', $status,
        '--error', $reason,
        '--context', $context,
        '--log-file', $LogPath,
        '--source', 'laicanyuan-scheduled-task',
        '--ignore-disabled-flag'
    )

    $retryCountRaw = $env:LAICANYUAN_REPORT_RETRY_COUNT
    $retryDelayRaw = $env:LAICANYUAN_REPORT_RETRY_DELAY_SECONDS
    $retryCount = 3
    $retryDelay = 20
    if ($retryCountRaw -match '^\d+$') { $retryCount = [int]$retryCountRaw }
    if ($retryDelayRaw -match '^\d+$') { $retryDelay = [int]$retryDelayRaw }
    if ($retryCount -lt 1) { $retryCount = 1 }
    if ($retryDelay -lt 0) { $retryDelay = 0 }

    for ($attempt = 1; $attempt -le $retryCount; $attempt++) {
        try {
            Write-SchedLog ("[网关上报] 开始上报定时任务结果: status={0} rc={1} attempt={2}/{3}" -f $status, $ExitCode, $attempt, $retryCount)
            & $PythonExe @ReportArgs *>&1 | ForEach-Object {
                $line = "[网关上报] $_"
                Write-Output $line
                Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
            }
            $reportRc = $LASTEXITCODE
            Write-SchedLog ("[网关上报] report_to_codex.py 退出码: {0}" -f $reportRc)
            if ($reportRc -eq 0) {
                return
            }
        } catch {
            Write-SchedLog ("[网关上报] 上报过程异常（不改变 runner 退出码）: {0}" -f $_.Exception.Message)
        }

        if ($attempt -lt $retryCount -and $retryDelay -gt 0) {
            Write-SchedLog ("[网关上报] {0} 秒后重试..." -f $retryDelay)
            Start-Sleep -Seconds $retryDelay
        }
    }
    Write-SchedLog '[网关上报] 多次尝试后仍未成功；仅保留本地调度日志，不改变 runner 退出码。'
}

Write-SchedLog '============================================================'
Write-SchedLog '来参缘调拨 - 定时任务 wrapper 启动'
Write-SchedLog ("脚本目录: {0}" -f $ScriptDir)
Write-SchedLog ("当前用户: {0}" -f $env:USERNAME)
Write-SchedLog ("会话登录: {0}" -f $env:SESSIONNAME)
Write-SchedLog ("PowerShell: {0}" -f $PSVersionTable.PSVersion)

# 仅当前进程环境变量，不持久化、不改 .env。
$env:LAICANYUAN_TRANSFER_PRODUCTION = '1'
$env:USB_HUB_COM = 'COM3'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

Write-SchedLog '已在本进程设置 LAICANYUAN_TRANSFER_PRODUCTION=1 / USB_HUB_COM=COM3 / PYTHONUTF8=1 / PYTHONIOENCODING=utf-8'
Write-SchedLog '(这些变量仅本 PowerShell 进程可见，wrapper 退出后自动消失，不写 .env、不影响其它会话)'

$Runner = Join-Path $ScriptDir 'run_laicanyuan_transfer.py'
if (-not (Test-Path -LiteralPath $Runner)) {
    Write-SchedLog "[FATAL] 未找到 runner: $Runner"
    exit 2
}

# 优先使用 py launcher（生产机已有）；否则回落到 python.exe。
$PythonExe = $null
$pyCmd = Get-Command 'py.exe' -ErrorAction SilentlyContinue
if ($null -ne $pyCmd) {
    $PythonExe = $pyCmd.Path
    $PythonArgs = @('-3', '-X', 'utf8', $Runner)
} else {
    $pythonCmd = Get-Command 'python.exe' -ErrorAction SilentlyContinue
    if ($null -ne $pythonCmd) {
        $PythonExe = $pythonCmd.Path
        $PythonArgs = @('-X', 'utf8', $Runner)
    } else {
        Write-SchedLog '[FATAL] 既找不到 py.exe 也找不到 python.exe'
        exit 2
    }
}

Write-SchedLog ("调用: {0} {1}" -f $PythonExe, ($PythonArgs -join ' '))
Write-SchedLog '说明：runner 自身已封装单实例锁 + 全局银行自动化锁；如果忙会直接 fail-closed (exit 27 / 28)，wrapper 不做重试。'

try {
    # 直接前台调用，让 runner stdout 同时写进调度日志。
    # PowerShell 5.1 在 $ErrorActionPreference=Stop 时会把 native stderr 变成
    # terminating error，导致 Python traceback 只剩第一行并把 runner rc 改成 99。
    # 这里临时改成 Continue，把 stdout/stderr 都逐行落日志，保留真实 $LASTEXITCODE。
    $oldErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $PythonExe @PythonArgs 2>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) {
            $line = $_.ToString()
        } else {
            $line = [string]$_
        }
        Write-Output $line
        Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
    }
    $rc = $LASTEXITCODE
} catch {
    Write-SchedLog ("[ERROR] runner 调用过程抛异常: {0}" -f $_.Exception.Message)
    $rc = 99
} finally {
    if ($null -ne $oldErrorActionPreference) {
        $ErrorActionPreference = $oldErrorActionPreference
    }
}

Write-SchedLog ("runner 退出码: {0}" -f $rc)
Send-GatewayReport -ExitCode $rc -LogPath $LogPath
Write-SchedLog '============================================================'

# 透传 runner 的退出码（27=本地单实例锁忙 28=全局银行自动化锁忙 0=OK 其它=具体业务码）。
exit $rc
