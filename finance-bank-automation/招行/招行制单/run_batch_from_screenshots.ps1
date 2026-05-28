param(
    [int]$TimeoutSeconds = 360,
    [int]$UsbHubPort = 3,
    [int[]]$OnlyIndex = @()
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$FinanceRoot = Split-Path -Parent (Split-Path -Parent $Root)
$CleanupAfterAutomation = Join-Path $FinanceRoot "公共\maintenance\cleanup_after_automation.ps1"
$Python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}

$Entry = Join-Path $Root "招行\skills\制单单账号单笔转账skill\制单.py"
$BankFormPath = Join-Path $Root "M3直供合同付款数据获取\bank_form.json"
$ScreenshotsDir = Join-Path $Root "招行\screenshots"

function Invoke-FinanceArtifactCleanup {
    if (-not (Test-Path -LiteralPath $CleanupAfterAutomation -PathType Leaf)) {
        return
    }
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $CleanupAfterAutomation
    } catch {
        Write-Warning "artifact cleanup failed: $($_.Exception.Message)"
    }
}

function Resolve-FirstExistingPath {
    param([string[]]$Candidates)

    $fallback = $null
    foreach ($candidate in $Candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }
        if ($null -eq $fallback) {
            $fallback = $candidate
        }
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    return $fallback
}

$Desktop = [Environment]::GetFolderPath("Desktop")
$FinanceRootCandidate = Split-Path -Parent (Split-Path -Parent $Root)
$HubCtrl = Resolve-FirstExistingPath @(
    $env:ZHIDAN_USB_HUB_CTRL,
    $env:USB_HUB_CTRL,
    (Join-Path $FinanceRootCandidate "公共\usbhub\usbhub\多口USB控制器软件以及驱动\hub_ctrl.py"),
    (Join-Path $Desktop "财务\公共\usbhub\usbhub\多口USB控制器软件以及驱动\hub_ctrl.py"),
    (Join-Path $Desktop "usbhub\usbhub\多口USB控制器软件以及驱动\hub_ctrl.py")
)
$RunStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BatchDir = Join-Path $ScreenshotsDir "batch_$RunStamp"
New-Item -ItemType Directory -Force -Path $BatchDir | Out-Null

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:USB_HUB_FORCE_PORT = [string]$UsbHubPort
# Batch 脚本是 USB_HUB_FORCE_PORT 的唯一合法调用方：所有付款单位共享同一 U 盾测试口。
# 显式打开 ZHIDAN_ALLOW_FORCE_USB_PORT，让 zhidan_usb_hub 知道这是有意为之；
# 其他场景没有这个变量，强制端口会 fail-closed。
$env:ZHIDAN_ALLOW_FORCE_USB_PORT = "1"

function Stop-ResidualWorkflow {
    param([string]$Reason)
    Write-Host "[$Reason] 清理 U-BANK 与 USB Hub..."
    try {
        taskkill /F /T /IM Firmbank.exe 2>$null | Out-Null
    } catch {
    }
    if (Test-Path -LiteralPath $HubCtrl) {
        try {
            & $Python $HubCtrl all-off --com COM3 | Out-File -FilePath (Join-Path $BatchDir "usb_cleanup.log") -Encoding utf8 -Append
        } catch {
            "USB cleanup failed: $($_.Exception.Message)" | Out-File -FilePath (Join-Path $BatchDir "usb_cleanup.log") -Encoding utf8 -Append
        }
    }
}

function Get-NewRunScreenshots {
    param([datetime]$StartTime)

    @(Get-ChildItem -LiteralPath $ScreenshotsDir -File -Filter "*.png" |
        Where-Object {
            $_.LastWriteTime -ge $StartTime -and
            $_.DirectoryName -eq $ScreenshotsDir -and
            $_.Name -match '^\d{6}_.*\.png$'
        } |
        Sort-Object LastWriteTime)
}

function Get-UniquePath {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return $Path
    }

    $directory = Split-Path -Parent $Path
    $baseName = [System.IO.Path]::GetFileNameWithoutExtension($Path)
    $extension = [System.IO.Path]::GetExtension($Path)
    $counter = 1
    do {
        $candidate = Join-Path $directory ("{0}_{1}{2}" -f $baseName, $counter, $extension)
        $counter += 1
    } while (Test-Path -LiteralPath $candidate)
    return $candidate
}

function Move-RunScreenshotsToBatch {
    param(
        [int]$Index,
        [object[]]$Screenshots
    )

    $shots = @($Screenshots | Where-Object { $null -ne $_ -and (Test-Path -LiteralPath $_.FullName) })
    if ($shots.Count -eq 0) {
        return @()
    }

    $shotDir = Join-Path $BatchDir ("{0:00}_screenshots" -f $Index)
    New-Item -ItemType Directory -Force -Path $shotDir | Out-Null

    $moved = @()
    foreach ($shot in $shots) {
        if ($shot.DirectoryName -eq $shotDir) {
            $moved += $shot
            continue
        }

        $destination = Get-UniquePath -Path (Join-Path $shotDir $shot.Name)
        Move-Item -LiteralPath $shot.FullName -Destination $destination
        $moved += Get-Item -LiteralPath $destination
    }

    return @($moved | Sort-Object LastWriteTime)
}

# 占位/示例数据，不是真实收款方。真实批量制单前由操作人替换 $Items，
# 切勿把真实账号/户名/金额/单据号提交进版本库。
$Items = @(
    [ordered]@{
        Index = 1
        Payer = "示例付款方公司"
        Payee = "示例收款方公司一"
        Account = "000000000000000"
        BankHead = "示例银行"
        Branch = "示例银行示例支行"
        Amount = "0.01"
        Ref = "SAMPLE-0001-请勿用于真实提交"
    },
    [ordered]@{
        Index = 2
        Payer = "示例付款方公司"
        Payee = "示例收款方公司二"
        Account = "000000000000000"
        BankHead = "示例银行"
        Branch = "示例银行示例支行"
        Amount = "0.01"
        Ref = "SAMPLE-0002-请勿用于真实提交"
    }
)

if ($OnlyIndex.Count -gt 0) {
    $Items = @($Items | Where-Object { $OnlyIndex -contains [int]$_.Index })
    if ($Items.Count -eq 0) {
        throw "OnlyIndex did not match any configured batch item."
    }
}

$Results = @()
Stop-ResidualWorkflow -Reason "batch-start"

foreach ($item in $Items) {
    $idx = [int]$item.Index
    $start = Get-Date
    $log = Join-Path $BatchDir ("{0:00}_{1}.log" -f $idx, $item.Ref)
    $err = Join-Path $BatchDir ("{0:00}_{1}.err.log" -f $idx, $item.Ref)

    $form = [ordered]@{
        "付款单位名称" = $item.Payer
        "收方账号" = $item.Account
        "收方户名" = $item.Payee
        "开户银行" = $item.BankHead
        "支行名称" = $item.Branch
        "金额" = $item.Amount
        "用途" = "货款"
        "业务参考号" = $item.Ref
    }
    $formJson = $form | ConvertTo-Json -Depth 5
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($BankFormPath, $formJson, $utf8NoBom)
    "=== RUN $idx START $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File -FilePath $log -Encoding utf8
    ($form | ConvertTo-Json -Depth 5) | Out-File -FilePath $log -Encoding utf8 -Append

    Write-Host ("[{0}/10] 开始: {1} -> {2}, 金额 {3}, 强制U盾 {4}口" -f $idx, $item.Payer, $item.Payee, $item.Amount, $UsbHubPort)

    $proc = Start-Process -FilePath $Python `
        -ArgumentList "`"$Entry`"" `
        -WorkingDirectory $Root `
        -RedirectStandardOutput $log `
        -RedirectStandardError $err `
        -WindowStyle Hidden `
        -PassThru

    $timedOut = $false
    while (-not $proc.HasExited) {
        Start-Sleep -Seconds 2
        if (((Get-Date) - $start).TotalSeconds -gt $TimeoutSeconds) {
            $timedOut = $true
            try {
                Stop-Process -Id $proc.Id -Force
            } catch {
            }
            break
        }
    }

    if (-not $timedOut) {
        $proc.WaitForExit()
        $proc.Refresh()
    }
    $exitCode = if ($timedOut) { 124 } else { $proc.ExitCode }
    $logText = ""
    if (Test-Path -LiteralPath $log) {
        try {
            $logText = Get-Content -LiteralPath $log -Raw -Encoding UTF8
        } catch {
            $logText = Get-Content -LiteralPath $log -Raw
        }
    }
    $newShots = @(Get-NewRunScreenshots -StartTime $start)
    $newShots = @(Move-RunScreenshotsToBatch -Index $idx -Screenshots $newShots)
    $finalShot = $newShots | Where-Object { $_.Name -like "*10_第一次经办等待后*" } | Select-Object -Last 1
    $completionLogged = (
        $logText -like "*制单测试流程完成*" -or
        $logText -like "*鍒跺崟娴嬭瘯娴佺▼瀹屾垚*" -or
        $logText -like "*第一次经办等待后*" -or
        $logText -like "*绗竴娆＄粡鍔炵瓑寰呭悗*"
    )
    $ok = (-not $timedOut -and $null -ne $finalShot -and $completionLogged)

    $retriedMainWindowStartup = $false
    $retryCause = ""
    $firstAttemptLog = ""
    $firstAttemptErrorLog = ""
    $isProductionMode = ($env:ZHIDAN_TEST_MODE -eq "0")
    $submitStarted = (
        $logText -like "*开始定位并点击「经办」按钮*" -or
        $logText -like "*已通过 UIA invoke 点击「经办」按钮*" -or
        @($newShots | Where-Object {
            $_.Name -like "*07_经办点击_滚动前*" -or
            $_.Name -like "*09_经办点击后*" -or
            $_.Name -like "*10_第一次经办等待后*"
        }).Count -gt 0
    )
    $mainWindowStartupFailure = (
        -not $timedOut -and
        -not $ok -and
        $newShots.Count -eq 0 -and
        (
            $logText -like "*未找到主界面窗口*" -or
            $logText -like "*未找到包含 V12/U-BANK/招商银行/企业银行*"
        )
    )
    $ubankCrashFailure = (
        -not $timedOut -and
        -not $ok -and
        (
            $logText -like "*招行客户端崩溃*" -or
            $logText -like "*守护线程已捕获并强杀招行崩溃*" -or
            $logText -like "*Firmbank.exe - 应用程序错误*"
        ) -and
        -not ($isProductionMode -and $submitStarted)
    )
    $retryableFailure = ($mainWindowStartupFailure -or $ubankCrashFailure)

    if ($retryableFailure) {
        $retriedMainWindowStartup = $true
        if ($mainWindowStartupFailure) {
            $retryCause = "登录后未进入主界面"
        } elseif ($ubankCrashFailure) {
            $retryCause = "U-BANK客户端崩溃"
        } else {
            $retryCause = "可重试异常"
        }
        $firstAttemptLog = $log
        $firstAttemptErrorLog = $err
        Write-Host ("[{0}/10] {1}，清理后自动重试一次..." -f $idx, $retryCause)
        Stop-ResidualWorkflow -Reason ("run-{0}-safe-retry" -f $idx)
        Start-Sleep -Seconds 3

        $retryStart = Get-Date
        $retryLog = Join-Path $BatchDir ("{0:00}_{1}.retry1.log" -f $idx, $item.Ref)
        $retryErr = Join-Path $BatchDir ("{0:00}_{1}.retry1.err.log" -f $idx, $item.Ref)
        [System.IO.File]::WriteAllText($BankFormPath, $formJson, $utf8NoBom)
        "=== RUN $idx RETRY 1 START $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File -FilePath $retryLog -Encoding utf8
        ($form | ConvertTo-Json -Depth 5) | Out-File -FilePath $retryLog -Encoding utf8 -Append

        $retryProc = Start-Process -FilePath $Python `
            -ArgumentList "`"$Entry`"" `
            -WorkingDirectory $Root `
            -RedirectStandardOutput $retryLog `
            -RedirectStandardError $retryErr `
            -WindowStyle Hidden `
            -PassThru

        $retryTimedOut = $false
        while (-not $retryProc.HasExited) {
            Start-Sleep -Seconds 2
            if (((Get-Date) - $retryStart).TotalSeconds -gt $TimeoutSeconds) {
                $retryTimedOut = $true
                try {
                    Stop-Process -Id $retryProc.Id -Force
                } catch {
                }
                break
            }
        }

        if (-not $retryTimedOut) {
            $retryProc.WaitForExit()
            $retryProc.Refresh()
        }

        $log = $retryLog
        $err = $retryErr
        $start = $retryStart
        $timedOut = $retryTimedOut
        $exitCode = if ($retryTimedOut) { 124 } else { $retryProc.ExitCode }
        $logText = ""
        if (Test-Path -LiteralPath $log) {
            try {
                $logText = Get-Content -LiteralPath $log -Raw -Encoding UTF8
            } catch {
                $logText = Get-Content -LiteralPath $log -Raw
            }
        }
        $newShots = @(Get-NewRunScreenshots -StartTime $start)
        $newShots = @(Move-RunScreenshotsToBatch -Index $idx -Screenshots $newShots)
        $finalShot = $newShots | Where-Object { $_.Name -like "*10_第一次经办等待后*" } | Select-Object -Last 1
        $completionLogged = (
            $logText -like "*制单测试流程完成*" -or
            $logText -like "*鍒跺崟娴嬭瘯娴佺▼瀹屾垚*" -or
            $logText -like "*第一次经办等待后*" -or
            $logText -like "*绗竴娆＄粡鍔炵瓑寰呭悗*"
        )
        $ok = (-not $timedOut -and $null -ne $finalShot -and $completionLogged)
        if ($ok) {
            Write-Host ("[{0}/10] 自动重试 OK: completion marker and final screenshot found" -f $idx)
        } else {
            Write-Host ("[{0}/10] 自动重试仍未通过" -f $idx)
        }
    }

    if ($timedOut -or -not $ok) {
        Stop-ResidualWorkflow -Reason ("run-{0}-failed" -f $idx)
    }

    $status = if ($ok) { "OK" } elseif ($timedOut) { "TIMEOUT" } else { "FAIL" }
    $reason = if ($ok) {
        if ($retriedMainWindowStartup) {
            "completion marker and final screenshot found after safe retry: $retryCause"
        } else {
            "completion marker and final screenshot found"
        }
    } elseif ($timedOut) {
        "timeout after $TimeoutSeconds seconds"
    } elseif ($retriedMainWindowStartup) {
        "safe retry failed after $retryCause; exit=$exitCode; final screenshot found=$($null -ne $finalShot)"
    } else {
        "exit=$exitCode; final screenshot found=$($null -ne $finalShot)"
    }

    $result = [ordered]@{
        Index = $idx
        Status = $status
        Reason = $reason
        Attempt = if ($retriedMainWindowStartup) { 2 } else { 1 }
        RetryCause = $retryCause
        ExitCode = $exitCode
        Ref = $item.Ref
        Payer = $item.Payer
        Payee = $item.Payee
        Amount = $item.Amount
        Log = $log
        ErrorLog = $err
        FirstAttemptLog = $firstAttemptLog
        FirstAttemptErrorLog = $firstAttemptErrorLog
        FinalScreenshot = if ($finalShot) { $finalShot.FullName } else { "" }
        Screenshots = @($newShots | ForEach-Object { $_.FullName })
    }
    $Results += [pscustomobject]$result
    $result | ConvertTo-Json -Depth 5 | Out-File -FilePath (Join-Path $BatchDir ("{0:00}_result.json" -f $idx)) -Encoding utf8
    Write-Host ("[{0}/10] {1}: {2}" -f $idx, $status, $reason)
}

Stop-ResidualWorkflow -Reason "batch-end"
$summaryPath = Join-Path $BatchDir "summary.json"
$Results | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
Write-Host "SUMMARY_PATH=$summaryPath"
$Results | Select-Object Index,Status,Reason,Ref,Payee,Amount,FinalScreenshot | Format-Table -AutoSize
Invoke-FinanceArtifactCleanup
