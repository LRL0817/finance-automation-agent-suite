#requires -Version 5.1
param(
    [string]$ProjectRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    [string]$TaskName = "财务银行运行产物清理",
    [string]$At = "03:30",
    [int]$KeepRunDirs = 10,
    [int]$KeepBalanceRuns = 30,
    [int]$KeepCmbRecentBatches = 30,
    [int]$MinAgeHours = 6,
    [switch]$RunNow,
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = [Console]::OutputEncoding
} catch {
}

$projectRootItem = Get-Item -LiteralPath $ProjectRoot -ErrorAction Stop
$projectRootFull = $projectRootItem.FullName.TrimEnd("\")
$cleanupScript = Join-Path $PSScriptRoot "cleanup_bank_artifacts.ps1"
if (-not (Test-Path -LiteralPath $cleanupScript -PathType Leaf)) {
    throw "Cleanup script not found: $cleanupScript"
}

if ($Unregister) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "已删除计划任务: $TaskName"
    } else {
        Write-Host "计划任务不存在: $TaskName"
    }
    exit 0
}

try {
    $atTime = [datetime]::ParseExact($At, "HH:mm", [System.Globalization.CultureInfo]::InvariantCulture)
} catch {
    throw "At must use HH:mm, for example 03:30"
}

$powershellArgs = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", ('"{0}"' -f $cleanupScript),
    "-ProjectRoot", ('"{0}"' -f $projectRootFull),
    "-Apply",
    "-KeepRunDirs", $KeepRunDirs,
    "-KeepBalanceRuns", $KeepBalanceRuns,
    "-KeepCmbRecentBatches", $KeepCmbRecentBatches,
    "-MinAgeHours", $MinAgeHours
)

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument ($powershellArgs -join " ") `
    -WorkingDirectory $projectRootFull

$trigger = New-ScheduledTaskTrigger -Daily -At $atTime
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "定期清理财务银行自动化生成的截图、debug_runs、balance runs 和缓存。只触碰已知运行产物目录。" `
    -Force | Out-Null

Write-Host "已创建/更新计划任务: $TaskName"
Write-Host "每日时间: $At"
Write-Host "执行脚本: $cleanupScript"
Write-Host ("保留策略: high-volume run dirs={0}, balance runs={1}, 招行批次={2}, 新文件保护={3}小时" -f $KeepRunDirs, $KeepBalanceRuns, $KeepCmbRecentBatches, $MinAgeHours)

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "已立即启动一次计划任务。"
}
