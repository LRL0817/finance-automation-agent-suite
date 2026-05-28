# scripts/cmb_gateway_probe.ps1
#
# 用途：
#   从「英文路径工作区」运行招行制单测试模式（ZHIDAN_TEST_MODE=1），用于排查
#   「飞书 -> 网关 -> Codex CLI -> 招行制单测试」链路上由于中文路径乱码 / Codex
#   shell 默认 10s 超时 / 截图证据不对齐导致的误判问题。
#
# 安全边界（脚本自身永远不变更）：
#   * 永远显式设置 ZHIDAN_TEST_MODE=1，**不允许**关测试模式。
#   * 只运行招行 制单.py 一次，不重试。
#   * 不点击第二次「经办」、不点击提交/确认/复核/授权/付款。
#   * 不启动 M3、农行、兴业、中行、余额查询、watchdog、monitor、生产入口。
#   * 不读取、打印、复制或修改任何 .env。
#   * 不修改招行业务代码，不 git add/commit/push。
#
# 参数：
#   -FinanceRoot     默认 C:\Users\30112\Desktop\finance_workspace
#                    （英文路径别名，背后应当指向中文真实目录 C:\Users\30112\Desktop\财务）
#   -TimeoutSeconds  默认 300
#
# 证据目录：
#   <FinanceRoot>\runtime\gateway_cmb_probe\probe_<yyyyMMdd_HHmmss>
#
# 退出码：
#   0 - probe 完成（即使被测脚本本身退出码非 0 也是 0；语义是「probe 跑完了」）
#   2 - 入参/路径不可用
#   3 - 制单.py 不存在
#   4 - 超时：被测进程被强杀
#   5 - 未能启动 Python

[CmdletBinding()]
param(
    [string]$FinanceRoot = "C:\Users\30112\Desktop\finance_workspace",
    [int]$TimeoutSeconds = 300
)

$ErrorActionPreference = "Stop"

function Write-Info($msg) {
    Write-Host "[cmb_gateway_probe] $msg"
}

function Write-Fail($msg) {
    Write-Host "[cmb_gateway_probe][FAIL] $msg"
}

if ([string]::IsNullOrWhiteSpace($FinanceRoot)) {
    Write-Fail "FinanceRoot 不能为空"
    exit 2
}
if ($TimeoutSeconds -lt 30) {
    Write-Fail "TimeoutSeconds 不能小于 30（招行 GUI 启动约 90s，缺省 300s）"
    exit 2
}

try {
    $FinanceRoot = [System.IO.Path]::GetFullPath($FinanceRoot)
} catch {
    Write-Fail "FinanceRoot 不是合法路径: $($_.Exception.Message)"
    exit 2
}

if (-not (Test-Path -LiteralPath $FinanceRoot -PathType Container)) {
    Write-Fail "FinanceRoot 不存在或不是目录: $FinanceRoot"
    Write-Fail "如果还没创建英文别名，请先运行 scripts\setup_finance_workspace_junction.ps1"
    exit 2
}

$cmbProjectDir   = Join-Path $FinanceRoot "招行\招行制单\招行"
$cmbSkillDir     = Join-Path $cmbProjectDir "skills\制单单账号单笔转账skill"
$cmbScriptPath   = Join-Path $cmbSkillDir "制单.py"
$cmbScreenshotDir = Join-Path $cmbProjectDir "screenshots"

if (-not (Test-Path -LiteralPath $cmbScriptPath -PathType Leaf)) {
    Write-Fail "招行 制单.py 不存在: $cmbScriptPath"
    exit 3
}

$timestamp = (Get-Date).ToString("yyyyMMdd_HHmmss")
$probeRoot = Join-Path $FinanceRoot ("runtime\gateway_cmb_probe\probe_" + $timestamp)
[void](New-Item -ItemType Directory -Path $probeRoot -Force)

Write-Info "FinanceRoot     = $FinanceRoot"
Write-Info "招行 制单.py    = $cmbScriptPath"
Write-Info "证据目录        = $probeRoot"
Write-Info "TimeoutSeconds  = $TimeoutSeconds"

$beforeProcessFile     = Join-Path $probeRoot "before_process.txt"
$beforeScreenshotsFile = Join-Path $probeRoot "before_screenshots.txt"
$afterProcessFile      = Join-Path $probeRoot "after_process.txt"
$afterScreenshotsFile  = Join-Path $probeRoot "after_screenshots.txt"
$exitCodeFile          = Join-Path $probeRoot "exit_code.txt"
$stdoutFile            = Join-Path $probeRoot "script.stdout.log"
$stderrFile            = Join-Path $probeRoot "script.stderr.log"
$processPollFile       = Join-Path $probeRoot "process_poll.log"
$summaryFile           = Join-Path $probeRoot "summary.json"

function Snapshot-Processes([string]$outFile, [string]$label) {
    $header = "# $label  ($(Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'))"
    $procs = Get-Process -Name Firmbank,python,codex,node -ErrorAction SilentlyContinue
    $lines = @($header)
    if ($null -ne $procs) {
        $rendered = $procs |
            Sort-Object -Property ProcessName,Id |
            Select-Object Id,ProcessName,StartTime,Path |
            Format-Table -AutoSize | Out-String
        $lines += $rendered.TrimEnd()
    } else {
        $lines += "(no matching processes)"
    }
    Set-Content -LiteralPath $outFile -Value ($lines -join "`r`n") -Encoding UTF8
}

function Snapshot-Screenshots([string]$outFile, [string]$label) {
    $header = "# $label  ($(Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'))"
    $lines = @($header, "# dir: $cmbScreenshotDir")
    if (Test-Path -LiteralPath $cmbScreenshotDir -PathType Container) {
        $files = Get-ChildItem -LiteralPath $cmbScreenshotDir -Filter "*.png" -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 20
        if ($files) {
            foreach ($f in $files) {
                $lines += ("{0}  {1,10}  {2}" -f $f.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss"), $f.Length, $f.Name)
            }
        } else {
            $lines += "(no png screenshots)"
        }
    } else {
        $lines += "(screenshot dir missing)"
    }
    Set-Content -LiteralPath $outFile -Value ($lines -join "`r`n") -Encoding UTF8
}

Snapshot-Processes $beforeProcessFile "before"
Snapshot-Screenshots $beforeScreenshotsFile "before"

$beforeScreenshotSet = @{}
if (Test-Path -LiteralPath $cmbScreenshotDir -PathType Container) {
    Get-ChildItem -LiteralPath $cmbScreenshotDir -Filter "*.png" -File -ErrorAction SilentlyContinue | ForEach-Object {
        $beforeScreenshotSet[$_.Name] = $true
    }
}

# 设置环境变量（**只对该子进程生效**，进程退出后不会污染当前会话）
# 显式置空的 5 个变量：从父进程移除继承的值，确保子进程读到「未设置」状态。
$envOverrides = [ordered]@{
    "ZHIDAN_TEST_MODE"            = "1"
    "USB_HUB_FORCE_PORT"          = "10"
    "ZHIDAN_ALLOW_FORCE_USB_PORT" = "1"
    "M3_PRODUCTION_MODE"          = "0"
    "PYTHONUTF8"                  = "1"
    "PYTHONIOENCODING"            = "utf-8"
}
$envClear = @(
    "CMB_LOGIN_ACCOUNT_NAME",
    "CMB_LOGIN_ACCOUNT_INDEX",
    "CIB_ALLOW_SUBMIT",
    "BOC_ENABLE_ORDER_SUBMIT",
    "ABC_ALLOW_SUBMIT_ONCE"
)

# 子进程超时硬保护：ZHIDAN_TEST_MODE 必须是 "1"，不允许任何方式被改为 "0"。
$savedTestMode = $env:ZHIDAN_TEST_MODE
foreach ($k in $envOverrides.Keys) { Set-Item -Path ("env:" + $k) -Value $envOverrides[$k] }
foreach ($k in $envClear)         { if (Test-Path ("env:" + $k)) { Remove-Item -Path ("env:" + $k) -Force } }

if ($env:ZHIDAN_TEST_MODE -ne "1") {
    Write-Fail "意外：ZHIDAN_TEST_MODE 未被设为 1，拒绝继续。"
    exit 2
}

Write-Info "已设置环境变量：ZHIDAN_TEST_MODE=1 USB_HUB_FORCE_PORT=10 ZHIDAN_ALLOW_FORCE_USB_PORT=1 M3_PRODUCTION_MODE=0，并强制 Python UTF-8 输出；已清空 5 个跨行提交开关。"

# 启动 Python 子进程
$startInfo = New-Object System.Diagnostics.ProcessStartInfo
$startInfo.FileName  = "python"
$startInfo.Arguments = "`"$cmbScriptPath`""
$startInfo.WorkingDirectory  = $cmbSkillDir
$startInfo.UseShellExecute   = $false
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError  = $true
$startInfo.CreateNoWindow    = $false
$startInfo.StandardOutputEncoding = [System.Text.Encoding]::UTF8
$startInfo.StandardErrorEncoding  = [System.Text.Encoding]::UTF8
# 把当前会话已设好的环境（含上面 4 个 override 与 5 个 clear）原样灌给子进程
foreach ($e in [System.Environment]::GetEnvironmentVariables().GetEnumerator()) {
    $startInfo.EnvironmentVariables[$e.Key] = [string]$e.Value
}
# 再次保险：override 必须存在；clear 必须不在
foreach ($k in $envOverrides.Keys) { $startInfo.EnvironmentVariables[$k] = [string]$envOverrides[$k] }
foreach ($k in $envClear) {
    if ($startInfo.EnvironmentVariables.ContainsKey($k)) {
        [void]$startInfo.EnvironmentVariables.Remove($k)
    }
}

$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $startInfo

$stdoutSb = New-Object System.Text.StringBuilder
$stderrSb = New-Object System.Text.StringBuilder
$stdoutEvent = Register-ObjectEvent -InputObject $proc -EventName OutputDataReceived -Action {
    if ($EventArgs.Data -ne $null) { [void]$Event.MessageData.AppendLine($EventArgs.Data) }
} -MessageData $stdoutSb
$stderrEvent = Register-ObjectEvent -InputObject $proc -EventName ErrorDataReceived -Action {
    if ($EventArgs.Data -ne $null) { [void]$Event.MessageData.AppendLine($EventArgs.Data) }
} -MessageData $stderrSb

$timedOut = $false
$startedAt = Get-Date
try {
    $started = $proc.Start()
    if (-not $started) {
        Write-Fail "无法启动 python 子进程"
        exit 5
    }
} catch {
    Write-Fail "Start() 抛异常：$($_.Exception.Message)"
    exit 5
}

$proc.BeginOutputReadLine()
$proc.BeginErrorReadLine()

Write-Info "已启动子进程 PID=$($proc.Id)，开始每 2s 轮询 Firmbank 进程..."
Set-Content -LiteralPath $processPollFile -Value ("# poll start " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) -Encoding UTF8

$pollLines = New-Object System.Collections.Generic.List[string]
$firmbankSeen = $false
while (-not $proc.HasExited) {
    $elapsed = (Get-Date) - $startedAt
    if ($elapsed.TotalSeconds -ge $TimeoutSeconds) {
        $timedOut = $true
        Write-Fail "TimeoutSeconds=$TimeoutSeconds 已到，准备终止子进程"
        break
    }
    $fb = Get-Process -Name Firmbank -ErrorAction SilentlyContinue
    $tsLine = (Get-Date -Format 'HH:mm:ss')
    if ($fb) {
        $firmbankSeen = $true
        $ids = ($fb | ForEach-Object { $_.Id }) -join ","
        $pollLines.Add("$tsLine  Firmbank pids=$ids")
    } else {
        $pollLines.Add("$tsLine  Firmbank (none)")
    }
    if (($pollLines.Count % 5) -eq 0) {
        Add-Content -LiteralPath $processPollFile -Value ($pollLines -join "`r`n") -Encoding UTF8
        $pollLines.Clear()
    }
    Start-Sleep -Seconds 2
}

if ($timedOut) {
    try {
        if (-not $proc.HasExited) {
            $proc.Kill($true)  # 包含子进程树（PS 7+）；老版本忽略多余参数
        }
    } catch {
        try { $proc.Kill() } catch {}
    }
    # 兜底：试着关 Firmbank 但不点任何银行按钮
    Get-Process -Name Firmbank -ErrorAction SilentlyContinue | ForEach-Object {
        try { $_.CloseMainWindow() | Out-Null } catch {}
    }
    Start-Sleep -Seconds 2
    Get-Process -Name Firmbank -ErrorAction SilentlyContinue | ForEach-Object {
        try { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue } catch {}
    }
}

# Wait for output flush
try { $proc.WaitForExit() } catch {}

Unregister-Event -SourceIdentifier $stdoutEvent.Name -ErrorAction SilentlyContinue
Unregister-Event -SourceIdentifier $stderrEvent.Name -ErrorAction SilentlyContinue
Remove-Job -Job $stdoutEvent -Force -ErrorAction SilentlyContinue
Remove-Job -Job $stderrEvent -Force -ErrorAction SilentlyContinue

if ($pollLines.Count -gt 0) {
    Add-Content -LiteralPath $processPollFile -Value ($pollLines -join "`r`n") -Encoding UTF8
    $pollLines.Clear()
}
Add-Content -LiteralPath $processPollFile -Value ("# poll end " + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) -Encoding UTF8

$exitCode = $proc.ExitCode
$stdoutText = $stdoutSb.ToString()
$stderrText = $stderrSb.ToString()
Set-Content -LiteralPath $stdoutFile -Value $stdoutText -Encoding UTF8
Set-Content -LiteralPath $stderrFile -Value $stderrText -Encoding UTF8
Set-Content -LiteralPath $exitCodeFile -Value ([string]$exitCode) -Encoding UTF8

Snapshot-Processes $afterProcessFile "after"
Snapshot-Screenshots $afterScreenshotsFile "after"

# 还原父会话 ZHIDAN_TEST_MODE
if ($null -eq $savedTestMode) {
    if (Test-Path "env:ZHIDAN_TEST_MODE") { Remove-Item -Path "env:ZHIDAN_TEST_MODE" -Force }
} else {
    Set-Item -Path "env:ZHIDAN_TEST_MODE" -Value $savedTestMode
}

# 标记分析
$firstJingbanMarkers = @(
    "第一次经办按钮点击后",
    "10_第一次经办等待后",
    "已点击第一次经办"
)
$testSkipMarkers = @(
    "[测试模式] 已跳过第二次",
    "如需开启生产二次经办，请设置环境变量 ZHIDAN_TEST_MODE=0"
)
$allOffMarkers = @(
    "USB Hub 已关闭",
    "USBHub all-off",
    "[USB Hub] 全部端口已关闭",
    "U-BANK 和 USB Hub 已关闭",
    "all ports off"
)
# 出现以下词意味着流程进入了**不期望**的二次经办/真实提交分支
$dangerousMarkers = @(
    "[生产模式] 开始点击第二次",
    "11_第二次经办等待后",
    "已点击第二次经办",
    "制单生产流程完成",
    "已成功提交",
    "提交成功",
    "确认继续经办",
    "授权成功",
    "复核通过"
)

function Test-Markers([string]$haystack, [string[]]$markers) {
    foreach ($m in $markers) { if ($haystack.IndexOf($m) -ge 0) { return $true } }
    return $false
}

$combinedOutput = $stdoutText + "`n" + $stderrText

$newScreenshots = @()
if (Test-Path -LiteralPath $cmbScreenshotDir -PathType Container) {
    $newScreenshots = @(
        Get-ChildItem -LiteralPath $cmbScreenshotDir -Filter "*.png" -File -ErrorAction SilentlyContinue |
            Where-Object { -not $beforeScreenshotSet.ContainsKey($_.Name) } |
            Sort-Object LastWriteTime |
            ForEach-Object { $_.Name }
    )
}

$firstJingbanByScreenshot = @(
    $newScreenshots | Where-Object {
        (($_ -like "*第一次经办等待后*.png") -or ($_ -like "*经办点击后*.png")) -and ($_ -notlike "*第二次*")
    }
).Count -gt 0
$dangerousByScreenshot = @(
    $newScreenshots | Where-Object {
        ($_ -like "*第二次*") -or
        ($_ -like "*提交*") -or
        ($_ -like "*确认*") -or
        ($_ -like "*复核*") -or
        ($_ -like "*授权*") -or
        ($_ -like "*付款*")
    }
).Count -gt 0
$firmbankAfter = Get-Process -Name Firmbank -ErrorAction SilentlyContinue

$firstJingbanSeen = (Test-Markers $combinedOutput $firstJingbanMarkers) -or $firstJingbanByScreenshot
$dangerousSeen    = (Test-Markers $combinedOutput $dangerousMarkers) -or $dangerousByScreenshot
$testSkipSeen     = (Test-Markers $combinedOutput $testSkipMarkers) -or (($exitCode -eq 0) -and $firstJingbanSeen -and (-not $dangerousSeen))
$allOffSeen       = (Test-Markers $combinedOutput $allOffMarkers) -or (($exitCode -eq 0) -and ($null -eq $firmbankAfter))

$summary = [ordered]@{
    probe_dir                      = $probeRoot
    finance_root                   = $FinanceRoot
    started_at                     = $startedAt.ToString("yyyy-MM-dd HH:mm:ss.fff")
    ended_at                       = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss.fff")
    timeout_seconds                = $TimeoutSeconds
    timeout                        = $timedOut
    exit_code                      = $exitCode
    firmbank_seen                  = $firmbankSeen
    new_screenshots                = $newScreenshots
    first_jingban_seen             = $firstJingbanSeen
    test_mode_skip_second_seen     = $testSkipSeen
    all_off_seen                   = $allOffSeen
    dangerous_submit_words_seen    = $dangerousSeen
}
$summary | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $summaryFile -Encoding UTF8

Write-Info "summary.json 写入：$summaryFile"
Write-Info "exit_code=$exitCode  timeout=$timedOut  firmbank_seen=$firmbankSeen  first_jingban_seen=$firstJingbanSeen  test_mode_skip_second_seen=$testSkipSeen  all_off_seen=$allOffSeen  dangerous_submit_words_seen=$dangerousSeen"

if ($dangerousSeen) {
    Write-Fail "检测到危险词（疑似进入了二次经办 / 真实提交分支），请立即人工核对 stdout/stderr 与截图。"
}

if ($timedOut) {
    exit 4
}
exit 0
