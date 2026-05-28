#requires -Version 5.1
<#
.SYNOPSIS
  M3 dry-run-bank 上线前预检：跑 N 轮 `auto_extract_dispatch.py --once --dry-run-bank`，
  收集每轮的银行路由结果，输出一份脱敏报告，**绝不**真实启动银行制单。

.DESCRIPTION
  封装现有 `auto_extract_dispatch.py` 的 dry-run-bank 入口。强制注入以下安全约束：

    M3_PRODUCTION_MODE=0           （禁用所有银行的生产门禁注入）
    M3_BANK_AMOUNT_OVERRIDE=0.01   （强制测试金额，禁止真实金额覆盖；与 M3 test/verification 入口一致）
    CIB_ALLOW_SUBMIT=               （清空：兴业制单提交闸门）
    BOC_ENABLE_ORDER_SUBMIT=        （清空：中行提交闸门）
    BOC_ENABLE_TRANSFER_FILL=       （清空：中行预填闸门）
    ZHIDAN_TEST_MODE=1              （招行制单：默认测试模式）
    ABC_ALLOW_SUBMIT_ONCE=          （清空：农行单笔提交闸门）
    LAICANYUAN_TRANSFER_PRODUCTION= （清空：来参缘调拨生产门禁）
    M3_AUTO_REPORT_ERRORS=0         （本预检不通过网关上报）
    M3_MONITOR_REPORT_ERRORS=0
    BANK_ROUTE_REPORT_ERRORS=0
    M3_DISABLE_BANK_INVOCATION=1    （硬阻断：bank_route.run_process 在非 dry-run 真实调用前直接返回 125，不 spawn 任何银行子进程）
    M3_EXTRACT_TIMEOUT_SECONDS=300  （单轮 OA/M3 抽取超时；白屏 / about:blank 卡住时退出 124）

  并且总是 `--once --dry-run-bank --quiet-idle --no-report`，每轮独立子进程。
  不允许启动任何银行客户端 / USB Hub / 制单子流程；本脚本不会调用 `--notify-final`、
  不会向飞书发送，也不会调用真实的 `auto_extract_dispatch` 长循环或 watchdog。

  报告写到 `runtime/preflight/preflight_<yyyyMMdd_HHmmss>/`，包含：
    - environment.json   : 本次注入的关键 env、参数、Python/Git 版本
    - round_<N>.stdout.log / round_<N>.stderr.log : 子进程输出
    - round_<N>.summary.json : 解析子进程输出得到的脱敏结构化摘要
    - report.json        : 全局摘要（路由计数 / skipped / isolated / unknown / 异常）
    - report.md          : 人类可读摘要

.PARAMETER Rounds
  跑几轮（每轮 = 一次 --once 调用）。默认 1。范围 1..20。

.PARAMETER Seconds
  每轮之间的等待秒数。默认 0。Rounds==1 时忽略。范围 0..600。

.PARAMETER ForceDetails
  设为 1 时给子进程注入 `M3_SCAN_FORCE_DETAILS=1`，让本轮重抓列表内全部记录的详情。
  默认 0（增量扫描）。

.PARAMETER PythonExe
  显式指定 Python 解释器路径；默认按现有 `.bat` 一样优先 Python312 安装，再退回 PATH 的 `python`。

.PARAMETER ProjectRoot
  M3 项目根目录。默认脚本所在目录。

.PARAMETER FinanceHome
  传给子进程的 `FINANCE_HOME`。默认 `C:\Users\30112\Desktop\财务`。

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File `
    C:\Users\30112\Desktop\M3直供合同付款数据获取\run_m3_preflight_dry_run.ps1 `
    -Rounds 3 -Seconds 30
#>
[CmdletBinding()]
param(
    [ValidateRange(1,20)]
    [int]$Rounds = 1,
    [ValidateRange(0,600)]
    [int]$Seconds = 0,
    [ValidateRange(0,1)]
    [int]$ForceDetails = 0,
    [string]$PythonExe = "",
    [string]$ProjectRoot = "",
    [string]$FinanceHome = "C:\Users\30112\Desktop\财务",
    [ValidateRange(0,3600)]
    [int]$ExtractTimeoutSeconds = 300
)

$ErrorActionPreference = "Stop"
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = [Console]::OutputEncoding
} catch {
}

if (-not $ProjectRoot) { $ProjectRoot = [System.IO.Path]::GetDirectoryName($PSCommandPath) }
if (-not (Test-Path -LiteralPath $ProjectRoot)) {
    Write-Host "[FAIL] ProjectRoot 不存在: $ProjectRoot" -ForegroundColor Red
    exit 2
}

$dispatchScript = Join-Path $ProjectRoot "auto_extract_dispatch.py"
if (-not (Test-Path -LiteralPath $dispatchScript)) {
    Write-Host "[FAIL] 未找到 auto_extract_dispatch.py: $dispatchScript" -ForegroundColor Red
    exit 2
}

if (-not $PythonExe) {
    $py312 = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (Test-Path -LiteralPath $py312) { $PythonExe = $py312 }
    else { $PythonExe = "python" }
}

$stamp = (Get-Date).ToString("yyyyMMdd_HHmmss")
$preflightRoot = Join-Path $ProjectRoot "runtime\preflight"
$runDir = Join-Path $preflightRoot "preflight_$stamp"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

# 子进程 env（局部应用；不污染父会话）
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:FINANCE_HOME = $FinanceHome
# 强制安全开关 —— 任何已存在的生产/提交开关都清空。
$env:M3_PRODUCTION_MODE = "0"
$env:M3_BANK_AMOUNT_OVERRIDE = "0.01"
$env:M3_AUTO_REPORT_ERRORS = "0"
$env:M3_MONITOR_REPORT_ERRORS = "0"
$env:BANK_ROUTE_REPORT_ERRORS = "0"
$env:M3_EXTRACT_TIMEOUT_SECONDS = [string]$ExtractTimeoutSeconds
$env:CIB_ALLOW_SUBMIT = ""
$env:BOC_ENABLE_ORDER_SUBMIT = ""
$env:BOC_ENABLE_TRANSFER_FILL = ""
$env:ZHIDAN_TEST_MODE = "1"
$env:ABC_ALLOW_SUBMIT_ONCE = ""
$env:LAICANYUAN_TRANSFER_PRODUCTION = ""
# 防御性：dry-run 路径里不会 spawn 银行子进程，但 ENV 兜底，即使有未来代码改动也阻断真实调用。
$env:M3_DISABLE_BANK_INVOCATION = "1"
if ($ForceDetails -eq 1) {
    $env:M3_SCAN_FORCE_DETAILS = "1"
} else {
    $env:M3_SCAN_FORCE_DETAILS = "0"
}

# 记录环境（脱敏：只记入注入项 + python/git 版本；不记入完整 env）
$pyVer = & $PythonExe --version 2>&1
$gitVer = (& git --version 2>&1)
$envSnapshot = [pscustomobject]@{
    timestamp = $stamp
    project_root = $ProjectRoot
    dispatch_script = $dispatchScript
    python_exe = $PythonExe
    python_version = ($pyVer -join " ").Trim()
    git_version = ($gitVer -join " ").Trim()
    rounds = $Rounds
    seconds_between = $Seconds
    force_details = ($ForceDetails -eq 1)
    injected_env = @{
        M3_PRODUCTION_MODE = $env:M3_PRODUCTION_MODE
        M3_BANK_AMOUNT_OVERRIDE = $env:M3_BANK_AMOUNT_OVERRIDE
        M3_AUTO_REPORT_ERRORS = $env:M3_AUTO_REPORT_ERRORS
        M3_MONITOR_REPORT_ERRORS = $env:M3_MONITOR_REPORT_ERRORS
        BANK_ROUTE_REPORT_ERRORS = $env:BANK_ROUTE_REPORT_ERRORS
        M3_EXTRACT_TIMEOUT_SECONDS = $env:M3_EXTRACT_TIMEOUT_SECONDS
        CIB_ALLOW_SUBMIT = $env:CIB_ALLOW_SUBMIT
        BOC_ENABLE_ORDER_SUBMIT = $env:BOC_ENABLE_ORDER_SUBMIT
        BOC_ENABLE_TRANSFER_FILL = $env:BOC_ENABLE_TRANSFER_FILL
        ZHIDAN_TEST_MODE = $env:ZHIDAN_TEST_MODE
        ABC_ALLOW_SUBMIT_ONCE = $env:ABC_ALLOW_SUBMIT_ONCE
        LAICANYUAN_TRANSFER_PRODUCTION = $env:LAICANYUAN_TRANSFER_PRODUCTION
        M3_DISABLE_BANK_INVOCATION = $env:M3_DISABLE_BANK_INVOCATION
        M3_SCAN_FORCE_DETAILS = $env:M3_SCAN_FORCE_DETAILS
        FINANCE_HOME = $env:FINANCE_HOME
    }
}
$envSnapshot | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $runDir "environment.json") -Encoding UTF8

function Parse-RoundStdout {
    param([string[]]$Lines)
    # 不依赖结构化输出，从子进程 stdout 抓常见关键字。所有 capture 都是脱敏后的数字/状态计数。
    $banksMatch = ($Lines | Select-String -Pattern '\[(?:监控|路由)\]' -AllMatches | Measure-Object).Count
    $extractFailed = ($Lines | Select-String -Pattern 'M3 抽取失败|抽取退出码非 0' | Measure-Object).Count -gt 0
    $idle = ($Lines | Select-String -Pattern '没有新增/变更的银行队列项' | Measure-Object).Count -gt 0
    $dryRunMarked = ($Lines | Select-String -Pattern 'dry_run_bank=True|--dry-run-bank|dry_run=True' | Measure-Object).Count -gt 0
    $screenshotMissing = ($Lines | Select-String -Pattern '截图缺失|missing.*screenshot' | Measure-Object).Count
    $amountAnomalies = ($Lines | Select-String -Pattern '金额.*异常|amount.*invalid|金额.*<= 0' | Measure-Object).Count
    $accountAnomalies = ($Lines | Select-String -Pattern '收方账号.*异常|account.*invalid|账号位数异常' | Measure-Object).Count
    $unknownBanks = ($Lines | Select-String -Pattern '未知付款银行|未识别付款银行|未配置.*银行|unknown.*bank' | Measure-Object).Count
    $skipped = ($Lines | Select-String -Pattern '跳过|skipped|isolated|已 seen' | Measure-Object).Count
    # 安全自检：子进程不应包含 "Firmbank.exe started"、"开始制单"、"经办点击" 这类真实银行操作日志
    $bankProcessStarted = ($Lines | Select-String -Pattern '开始制单|启动 U-BANK|点击经办|提交按钮|开始打开兴业|开始打开农行|开始打开中行' | Measure-Object).Count

    return [pscustomobject]@{
        extract_failed = $extractFailed
        idle = $idle
        dry_run_marked = $dryRunMarked
        screenshot_missing_lines = $screenshotMissing
        amount_anomaly_lines = $amountAnomalies
        account_anomaly_lines = $accountAnomalies
        unknown_bank_lines = $unknownBanks
        skipped_lines = $skipped
        bank_process_started_lines = $bankProcessStarted
        routing_log_lines = $banksMatch
    }
}

$roundResults = @()
$bankProcessAccident = $false
for ($i = 1; $i -le $Rounds; $i++) {
    Write-Host ""
    Write-Host "== Round $i / $Rounds =="
    $stdoutPath = Join-Path $runDir ("round_{0:00}.stdout.log" -f $i)
    $stderrPath = Join-Path $runDir ("round_{0:00}.stderr.log" -f $i)
    $summaryPath = Join-Path $runDir ("round_{0:00}.summary.json" -f $i)

    $argList = @(
        "-X", "utf8",
        "-u",
        $dispatchScript,
        "--once",
        "--dry-run-bank",
        "--quiet-idle",
        "--no-report",
        "--poll-seconds", "60"
    )
    Write-Host "调用: $PythonExe $argList"
    $startedAt = Get-Date
    $proc = Start-Process -FilePath $PythonExe -ArgumentList $argList -WorkingDirectory $ProjectRoot `
        -NoNewWindow -PassThru -Wait `
        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    $endedAt = Get-Date
    $exitCode = $proc.ExitCode
    Write-Host "退出码: $exitCode  耗时: $([int]($endedAt - $startedAt).TotalSeconds) s"

    $stdoutLines = @()
    if (Test-Path -LiteralPath $stdoutPath) {
        $stdoutLines = Get-Content -LiteralPath $stdoutPath -Encoding UTF8
    }
    $summary = Parse-RoundStdout -Lines $stdoutLines
    $roundObj = [pscustomobject]@{
        round = $i
        started_at = $startedAt.ToString("o")
        ended_at = $endedAt.ToString("o")
        exit_code = $exitCode
        stdout_path = $stdoutPath
        stderr_path = $stderrPath
        summary = $summary
    }
    $roundObj | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
    $roundResults += $roundObj

    if ($summary.bank_process_started_lines -gt 0) {
        Write-Host "[FAIL] Round $i 子进程日志中疑似出现真实银行制单动作（routing 应该 dry-run）" -ForegroundColor Red
        $bankProcessAccident = $true
    }
    if (-not $summary.dry_run_marked) {
        Write-Host "[WARN] Round $i 子进程未明确打印 dry_run_bank=True；请人工核对 stdout" -ForegroundColor Yellow
    }
    if ($i -lt $Rounds -and $Seconds -gt 0) {
        Write-Host "等待 $Seconds s..."
        Start-Sleep -Seconds $Seconds
    }
}

$overall = [pscustomobject]@{
    timestamp = $stamp
    project_root = $ProjectRoot
    rounds_planned = $Rounds
    rounds_completed = $roundResults.Count
    all_rounds_dry_run_marked = ($roundResults | Where-Object { -not $_.summary.dry_run_marked }).Count -eq 0
    any_bank_process_started = $bankProcessAccident
    aggregate = [pscustomobject]@{
        extract_failed_rounds = @($roundResults | Where-Object { $_.summary.extract_failed }).Count
        idle_rounds = @($roundResults | Where-Object { $_.summary.idle }).Count
        nonzero_exit_rounds = @($roundResults | Where-Object { $_.exit_code -ne 0 }).Count
        screenshot_missing_total = (($roundResults | ForEach-Object { $_.summary.screenshot_missing_lines } | Measure-Object -Sum).Sum)
        amount_anomaly_total = (($roundResults | ForEach-Object { $_.summary.amount_anomaly_lines } | Measure-Object -Sum).Sum)
        account_anomaly_total = (($roundResults | ForEach-Object { $_.summary.account_anomaly_lines } | Measure-Object -Sum).Sum)
        unknown_bank_total = (($roundResults | ForEach-Object { $_.summary.unknown_bank_lines } | Measure-Object -Sum).Sum)
        skipped_total = (($roundResults | ForEach-Object { $_.summary.skipped_lines } | Measure-Object -Sum).Sum)
        bank_process_started_total = (($roundResults | ForEach-Object { $_.summary.bank_process_started_lines } | Measure-Object -Sum).Sum)
    }
    rounds = $roundResults
}
$overall | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $runDir "report.json") -Encoding UTF8

# 人类可读摘要
$mdLines = New-Object System.Collections.Generic.List[string]
$mdLines.Add("# M3 dry-run-bank 预检报告") | Out-Null
$mdLines.Add("") | Out-Null
$mdLines.Add("- 时间戳: $stamp") | Out-Null
$mdLines.Add("- 项目根: $ProjectRoot") | Out-Null
$mdLines.Add("- Python: $($envSnapshot.python_version)") | Out-Null
$mdLines.Add("- 轮数: $($roundResults.Count) / $Rounds") | Out-Null
$mdLines.Add("- 各轮 dry_run_bank=True 全部打印: $($overall.all_rounds_dry_run_marked)") | Out-Null
$mdLines.Add("- 检测到任何真实银行制单动作: $($overall.any_bank_process_started)") | Out-Null
$mdLines.Add("") | Out-Null
$mdLines.Add("## 注入的安全 env") | Out-Null
$envKeys = @($envSnapshot.injected_env.Keys | Sort-Object)
foreach ($k in $envKeys) {
    $v = $envSnapshot.injected_env[$k]
    if ($null -eq $v -or $v -eq "") { $v = "(empty)" }
    $mdLines.Add("- $k = $v") | Out-Null
}
$mdLines.Add("") | Out-Null
$mdLines.Add("## 每轮摘要") | Out-Null
foreach ($r in $roundResults) {
    $mdLines.Add("### Round $($r.round) (exit=$($r.exit_code))") | Out-Null
    $mdLines.Add("- 标记为 dry-run: $($r.summary.dry_run_marked)") | Out-Null
    $mdLines.Add("- M3 抽取失败: $($r.summary.extract_failed)") | Out-Null
    $mdLines.Add("- 空闲(无新增/变更): $($r.summary.idle)") | Out-Null
    $mdLines.Add("- 跳过/seen/isolated 行数: $($r.summary.skipped_lines)") | Out-Null
    $mdLines.Add("- 未知付款银行行数: $($r.summary.unknown_bank_lines)") | Out-Null
    $mdLines.Add("- 截图缺失行数: $($r.summary.screenshot_missing_lines)") | Out-Null
    $mdLines.Add("- 金额异常行数: $($r.summary.amount_anomaly_lines)") | Out-Null
    $mdLines.Add("- 账号异常行数: $($r.summary.account_anomaly_lines)") | Out-Null
    $mdLines.Add("- 检测到真实银行制单动作行数: $($r.summary.bank_process_started_lines)") | Out-Null
}
$mdLines.Add("") | Out-Null
$mdLines.Add("---") | Out-Null
$mdLines.Add("本报告由 run_m3_preflight_dry_run.ps1 生成；只跑 dry-run-bank，不启动任何银行客户端 / U盾 / USB Hub / 真实制单流程；不发送飞书通知；不触发 watchdog。") | Out-Null
Set-Content -LiteralPath (Join-Path $runDir "report.md") -Value $mdLines -Encoding UTF8

Write-Host ""
Write-Host "== 预检完成 =="
Write-Host "报告目录: $runDir"
Write-Host "总结:"
Write-Host "  rounds_planned          = $($overall.rounds_planned)"
Write-Host "  rounds_completed        = $($overall.rounds_completed)"
Write-Host "  all_dry_run_marked      = $($overall.all_rounds_dry_run_marked)"
Write-Host "  any_bank_process_started= $($overall.any_bank_process_started)"
Write-Host "  nonzero_exit_rounds     = $($overall.aggregate.nonzero_exit_rounds)"
Write-Host "  extract_failed_rounds   = $($overall.aggregate.extract_failed_rounds)"
Write-Host "  unknown_bank_total      = $($overall.aggregate.unknown_bank_total)"
Write-Host "  screenshot_missing_total= $($overall.aggregate.screenshot_missing_total)"
Write-Host "  amount_anomaly_total    = $($overall.aggregate.amount_anomaly_total)"
Write-Host "  account_anomaly_total   = $($overall.aggregate.account_anomaly_total)"

if ($bankProcessAccident) {
    Write-Host "[FAIL] 检测到子进程日志疑似出现真实银行制单动作，预检不通过" -ForegroundColor Red
    exit 1
}
if ($overall.aggregate.nonzero_exit_rounds -gt 0) {
    Write-Host "[WARN] 有轮次 exit_code != 0，请人工核对 round_*.stdout.log" -ForegroundColor Yellow
    exit 0
}
Write-Host "OK" -ForegroundColor Green
exit 0
