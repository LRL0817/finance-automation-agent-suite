#requires -Version 5.1
param(
    [string]$ProjectRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    [int]$KeepRunDirs = 10,
    [int]$KeepBalanceRuns = 30,
    [int]$KeepArchiveDirs = 20,
    [int]$KeepLogFiles = 60,
    [int]$KeepCmbRecentBatches = 30,
    [int]$KeepCmbBatchDays = 30,
    [int]$MaxRunAgeDays = 30,
    [int]$MaxBalanceAgeDays = 90,
    [int]$MaxArchiveAgeDays = 90,
    [int]$MaxLogAgeDays = 180,
    [int]$MinAgeHours = 6,
    [switch]$Apply,
    [switch]$SkipProjectHousekeeping,
    [switch]$KeepPyCache,
    [switch]$ShowAll
)

$ErrorActionPreference = "Stop"
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = [Console]::OutputEncoding
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
} catch {
}

$ProjectRootItem = Get-Item -LiteralPath $ProjectRoot -ErrorAction Stop
$script:ProjectRootFull = $ProjectRootItem.FullName.TrimEnd("\")
$script:Actions = New-Object System.Collections.Generic.List[object]

function Test-InsideProjectRoot {
    param([string]$Path)

    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    $resolved = $item.FullName.TrimEnd("\")
    if ($resolved -eq $script:ProjectRootFull) {
        return $item
    }
    if (-not $resolved.StartsWith($script:ProjectRootFull + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to touch path outside project root: $resolved"
    }
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to touch reparse point: $resolved"
    }
    return $item
}

function Join-ProjectPath {
    param([string]$RelativePath)
    return Join-Path $script:ProjectRootFull $RelativePath
}

function Get-SizeBytes {
    param([string]$Path)

    try {
        $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            return [int64]0
        }
        if (-not $item.PSIsContainer) {
            return [int64]$item.Length
        }
        $sum = (Get-ChildItem -LiteralPath $item.FullName -File -Recurse -Force -ErrorAction SilentlyContinue |
            Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0 } |
            Measure-Object Length -Sum).Sum
        if ($null -eq $sum) {
            return [int64]0
        }
        return [int64]$sum
    } catch {
        return [int64]0
    }
}

function Add-Action {
    param(
        [string]$Kind,
        [string]$Path,
        [string]$Reason,
        [int64]$Bytes
    )

    $script:Actions.Add([pscustomobject]@{
        Kind = $Kind
        Path = $Path
        Reason = $Reason
        Bytes = $Bytes
    }) | Out-Null
}

function Add-PruneDirectoryActions {
    param(
        [string]$RelativeRoot,
        [string]$Label,
        [int]$Keep,
        [int]$MaxAgeDays
    )

    $root = Join-ProjectPath $RelativeRoot
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        return
    }
    $rootItem = Test-InsideProjectRoot $root
    $dirs = @(Get-ChildItem -LiteralPath $rootItem.FullName -Directory -Force -ErrorAction SilentlyContinue |
        Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0 } |
        Sort-Object LastWriteTime -Descending)
    if ($dirs.Count -eq 0) {
        return
    }

    $maxAgeCutoff = $null
    if ($MaxAgeDays -gt 0) {
        $maxAgeCutoff = (Get-Date).AddDays(-1 * $MaxAgeDays)
    }
    $minAgeCutoff = $null
    if ($MinAgeHours -gt 0) {
        $minAgeCutoff = (Get-Date).AddHours(-1 * $MinAgeHours)
    }

    for ($i = 0; $i -lt $dirs.Count; $i += 1) {
        $dir = $dirs[$i]
        $reasons = @()
        if ($Keep -ge 0 -and $i -ge $Keep) {
            $reasons += ("超过最近 {0} 个保留上限" -f $Keep)
        }
        if ($null -ne $maxAgeCutoff -and $dir.LastWriteTime -lt $maxAgeCutoff) {
            $reasons += ("超过 {0} 天保留期" -f $MaxAgeDays)
        }
        if ($reasons.Count -eq 0) {
            continue
        }
        if ($null -ne $minAgeCutoff -and $dir.LastWriteTime -gt $minAgeCutoff) {
            continue
        }
        Add-Action `
            -Kind "RemoveDir" `
            -Path $dir.FullName `
            -Reason ("{0}: {1}" -f $Label, ($reasons -join "；")) `
            -Bytes (Get-SizeBytes -Path $dir.FullName)
    }
}

function Add-PruneFileActions {
    param(
        [string]$RelativeRoot,
        [string]$Pattern,
        [string]$Label,
        [int]$Keep,
        [int]$MaxAgeDays,
        [switch]$Recurse
    )

    $root = Join-ProjectPath $RelativeRoot
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        return
    }
    $rootItem = Test-InsideProjectRoot $root
    $params = @{
        LiteralPath = $rootItem.FullName
        File = $true
        Force = $true
        ErrorAction = "SilentlyContinue"
    }
    if ($Recurse) {
        $params["Recurse"] = $true
    }
    $files = @(Get-ChildItem @params -Filter $Pattern |
        Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0 } |
        Sort-Object LastWriteTime -Descending)
    if ($files.Count -eq 0) {
        return
    }

    $maxAgeCutoff = $null
    if ($MaxAgeDays -gt 0) {
        $maxAgeCutoff = (Get-Date).AddDays(-1 * $MaxAgeDays)
    }
    $minAgeCutoff = $null
    if ($MinAgeHours -gt 0) {
        $minAgeCutoff = (Get-Date).AddHours(-1 * $MinAgeHours)
    }

    for ($i = 0; $i -lt $files.Count; $i += 1) {
        $file = $files[$i]
        $reasons = @()
        if ($Keep -ge 0 -and $i -ge $Keep) {
            $reasons += ("超过最近 {0} 个保留上限" -f $Keep)
        }
        if ($null -ne $maxAgeCutoff -and $file.LastWriteTime -lt $maxAgeCutoff) {
            $reasons += ("超过 {0} 天保留期" -f $MaxAgeDays)
        }
        if ($reasons.Count -eq 0) {
            continue
        }
        if ($null -ne $minAgeCutoff -and $file.LastWriteTime -gt $minAgeCutoff) {
            continue
        }
        Add-Action `
            -Kind "RemoveFile" `
            -Path $file.FullName `
            -Reason ("{0}: {1}" -f $Label, ($reasons -join "；")) `
            -Bytes (Get-SizeBytes -Path $file.FullName)
    }
}

function Add-PythonCacheActions {
    if ($KeepPyCache) {
        return
    }
    foreach ($cacheDir in @(Get-ChildItem -LiteralPath $script:ProjectRootFull -Directory -Recurse -Force -Filter "__pycache__" -ErrorAction SilentlyContinue)) {
        if (($cacheDir.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            continue
        }
        Add-Action `
            -Kind "RemoveDir" `
            -Path $cacheDir.FullName `
            -Reason "Python bytecode cache，可自动再生成" `
            -Bytes (Get-SizeBytes -Path $cacheDir.FullName)
    }
}

function Get-PythonExe {
    $localPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (Test-Path -LiteralPath $localPython -PathType Leaf) {
        return $localPython
    }
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    return $null
}

function Invoke-ProjectHousekeeping {
    if ($SkipProjectHousekeeping) {
        Write-Host "[project] skipped project housekeeping"
        return
    }

    $cmbCleanup = Join-ProjectPath "招行\招行制单\cleanup_generated_artifacts.ps1"
    if (Test-Path -LiteralPath $cmbCleanup -PathType Leaf) {
        Write-Host "[project] 招行 cleanup_generated_artifacts.ps1"
        $cmbParams = @{
            Root = (Join-ProjectPath "招行\招行制单")
            KeepBatchDays = $KeepCmbBatchDays
            KeepRecentBatches = $KeepCmbRecentBatches
            PurgeArchiveDays = $MaxArchiveAgeDays
        }
        if ($Apply) {
            $cmbParams["Apply"] = $true
        }
        & $cmbCleanup @cmbParams
    }

    $cibMaintain = Join-ProjectPath "兴业\兴业制单\maintain_workspace.py"
    if (Test-Path -LiteralPath $cibMaintain -PathType Leaf) {
        $python = Get-PythonExe
        if ($python) {
            Write-Host "[project] 兴业 maintain_workspace.py"
            $cibArgs = @($cibMaintain)
            if ($Apply) {
                $cibArgs += "--apply"
            }
            & $python @cibArgs
        } else {
            Write-Warning "Python not found; skipped 兴业 maintain_workspace.py"
        }
    }
}

function Build-ActionPlan {
    Add-PruneDirectoryActions "农业\农业银行\artifacts\单笔转账\debug_runs" "农行单笔转账 debug_runs" $KeepRunDirs $MaxRunAgeDays
    Add-PruneDirectoryActions "农业\农业银行\artifacts\usb12\debug_runs" "农行 USB12 debug_runs" $KeepRunDirs $MaxRunAgeDays
    Add-PruneDirectoryActions "中行\中国银行\debug_runs" "中行 debug_runs" $KeepRunDirs $MaxRunAgeDays

    Add-PruneDirectoryActions "招行\查询招行余额\runs" "查询招行余额 runs" $KeepBalanceRuns $MaxBalanceAgeDays
    Add-PruneDirectoryActions "兴业\查询兴业余额\runs" "查询兴业余额 runs" $KeepBalanceRuns $MaxBalanceAgeDays
    Add-PruneDirectoryActions "农业\查询农业余额\runs" "查询农业余额 runs" $KeepBalanceRuns $MaxBalanceAgeDays
    Add-PruneDirectoryActions "中行\查询中行余额\runs" "查询中行余额 runs" $KeepBalanceRuns $MaxBalanceAgeDays

    Add-PruneDirectoryActions "兴业\兴业制单\screenshots\archive" "兴业 screenshots archive" $KeepArchiveDirs $MaxArchiveAgeDays
    Add-PruneDirectoryActions "兴业\兴业制单\screenshots\reviews" "兴业 screenshot reviews" $KeepArchiveDirs $MaxArchiveAgeDays
    Add-PruneDirectoryActions "招行\招行制单\招行\screenshots\_archive\batches" "招行 archived batches" $KeepArchiveDirs $MaxArchiveAgeDays
    Add-PruneDirectoryActions "招行\招行制单\招行\screenshots\_archive\root_screenshots" "招行 archived loose screenshots" $KeepArchiveDirs $MaxArchiveAgeDays
    Add-PruneDirectoryActions "招行\招行制单\招行\screenshots\_archive\watch_logs" "招行 archived watch logs" $KeepArchiveDirs $MaxArchiveAgeDays
    Add-PruneDirectoryActions "农业\农业银行\artifacts\单笔转账\legacy_skill_artifacts" "农行 legacy skill artifacts" $KeepArchiveDirs $MaxArchiveAgeDays
    Add-PruneDirectoryActions "农业\农业银行\artifacts\root_legacy_artifacts" "农行 root legacy artifacts" $KeepArchiveDirs $MaxArchiveAgeDays
    Add-PruneDirectoryActions "农业\农业银行\artifacts\codex_repair_archive" "农行 repair archives" $KeepArchiveDirs $MaxArchiveAgeDays

    Add-PruneFileActions "中行\中国银行\logs\batches" "batch_*.log" "中行 batch logs" $KeepLogFiles $MaxLogAgeDays
    Add-PruneFileActions "农业\农业银行\artifacts\单笔转账\logs" "*.log" "农行 aggregate logs" $KeepLogFiles $MaxLogAgeDays
    Add-PruneFileActions "农业\农业银行\artifacts\usb12\logs" "*.log" "农行 USB12 logs" $KeepLogFiles $MaxLogAgeDays
    Add-PruneFileActions "兴业\兴业制单\screenshots\archive" "*.log" "兴业 archived logs" $KeepLogFiles $MaxLogAgeDays -Recurse
    Add-PruneFileActions "招行\招行制单\招行\screenshots\_archive" "*.log" "招行 archived logs" $KeepLogFiles $MaxLogAgeDays -Recurse

    Add-PythonCacheActions
}

function Invoke-ActionPlan {
    foreach ($action in $script:Actions) {
        $item = Test-InsideProjectRoot $action.Path
        if ($action.Kind -eq "RemoveDir") {
            if ($item.PSIsContainer) {
                Remove-Item -LiteralPath $item.FullName -Recurse -Force
            }
        } elseif ($action.Kind -eq "RemoveFile") {
            if (-not $item.PSIsContainer) {
                Remove-Item -LiteralPath $item.FullName -Force
            }
        } else {
            throw "Unknown action kind: $($action.Kind)"
        }
    }
}

$mode = if ($Apply) { "APPLY" } else { "DRY-RUN" }
Write-Host "MODE=$mode"
Write-Host "PROJECT_ROOT=$script:ProjectRootFull"
Write-Host ("POLICY=KeepRunDirs:{0}; KeepBalanceRuns:{1}; KeepCmbRecentBatches:{2}; MinAgeHours:{3}" -f $KeepRunDirs, $KeepBalanceRuns, $KeepCmbRecentBatches, $MinAgeHours)

Invoke-ProjectHousekeeping
Build-ActionPlan

$totalBytes = ($script:Actions | Measure-Object Bytes -Sum).Sum
if ($null -eq $totalBytes) {
    $totalBytes = 0
}
$totalMb = [math]::Round($totalBytes / 1MB, 2)

Write-Host "ACTIONS=$($script:Actions.Count); SIZE_MB=$totalMb"
if ($script:Actions.Count -gt 0) {
    $script:Actions |
        Group-Object Kind |
        Sort-Object Name |
        Select-Object Name, Count, @{Name="MB"; Expression = { [math]::Round((($_.Group | Measure-Object Bytes -Sum).Sum) / 1MB, 2) }} |
        Format-Table -AutoSize

    $preview = if ($ShowAll) { $script:Actions } else { @($script:Actions | Select-Object -First 60) }
    $preview |
        Select-Object Kind, Reason, @{Name="MB"; Expression = { [math]::Round($_.Bytes / 1MB, 3) }}, Path |
        Format-Table -Wrap
    if (-not $ShowAll -and $script:Actions.Count -gt 60) {
        Write-Host ("仅显示前 60 条；如需完整清单加 -ShowAll。剩余 {0} 条。" -f ($script:Actions.Count - 60))
    }
}

if (-not $Apply) {
    Write-Host "未加 -Apply，只展示计划，不删除任何文件。"
    exit 0
}

Invoke-ActionPlan
Write-Host "清理完成。"
