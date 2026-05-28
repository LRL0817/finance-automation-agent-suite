param(
    [string]$Root = (Split-Path -Parent $MyInvocation.MyCommand.Path),
    [int]$KeepBatchDays = 30,
    [int]$KeepRecentBatches = 100,
    [int]$KeepRootScreenshotDays = 2,
    [int]$KeepWatchLogDays = 1,
    [int]$PurgeArchiveDays = 90,
    [switch]$Apply,
    [switch]$NoLegacyConsolidation,
    [switch]$KeepPyCache,
    [switch]$ShowAll
)

$ErrorActionPreference = "Stop"

$ScreenshotsDir = Join-Path $Root "招行\screenshots"
$ArchiveRoot = Join-Path $ScreenshotsDir "_archive"

if (-not (Test-Path -LiteralPath $ScreenshotsDir)) {
    throw "Screenshots directory not found: $ScreenshotsDir"
}

$Actions = New-Object System.Collections.Generic.List[object]
$JsonUpdates = New-Object System.Collections.Generic.List[object]
$MoveMap = @{}
$ReservedDestinations = @{}

function Get-PathKey {
    param([string]$Path)

    return ([System.IO.Path]::GetFullPath($Path)).ToLowerInvariant()
}

function Get-SizeBytes {
    param([string]$Path)

    try {
        if (Test-Path -LiteralPath $Path -PathType Container) {
            $sum = (Get-ChildItem -LiteralPath $Path -File -Recurse -Force | Measure-Object Length -Sum).Sum
            if ($null -eq $sum) {
                return [int64]0
            }
            return [int64]$sum
        }
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            return [int64](Get-Item -LiteralPath $Path).Length
        }
    } catch {
    }
    return [int64]0
}

function Add-Action {
    param(
        [string]$Kind,
        [string]$Path,
        [string]$Destination,
        [string]$Reason,
        [int64]$Bytes
    )

    $Actions.Add([pscustomobject]@{
        Kind = $Kind
        Path = $Path
        Destination = $Destination
        Reason = $Reason
        Bytes = $Bytes
    }) | Out-Null
}

function Ensure-ParentDirectory {
    param([string]$Path)

    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
}

function Get-UniqueDestination {
    param([string]$Destination)

    $candidate = $Destination
    $directory = Split-Path -Parent $Destination
    $baseName = [System.IO.Path]::GetFileNameWithoutExtension($Destination)
    $extension = [System.IO.Path]::GetExtension($Destination)
    $counter = 1

    while ((Test-Path -LiteralPath $candidate) -or $ReservedDestinations.ContainsKey((Get-PathKey $candidate))) {
        $candidate = Join-Path $directory ("{0}_{1}{2}" -f $baseName, $counter, $extension)
        $counter += 1
    }

    $ReservedDestinations[(Get-PathKey $candidate)] = $true
    return $candidate
}

function Get-LegacyScreenshotDestination {
    param(
        [string]$OriginalPath,
        [string]$BatchDir,
        [int]$Index
    )

    if ([string]::IsNullOrWhiteSpace($OriginalPath) -or $Index -le 0) {
        return $OriginalPath
    }

    try {
        $source = Get-Item -LiteralPath $OriginalPath -ErrorAction Stop
    } catch {
        return $OriginalPath
    }

    if ($source.PSIsContainer) {
        return $OriginalPath
    }
    if ((Get-PathKey $source.DirectoryName) -ne (Get-PathKey $ScreenshotsDir)) {
        return $OriginalPath
    }
    if ($source.Name -notmatch '^\d{6}_.*\.png$') {
        return $OriginalPath
    }

    $sourceKey = Get-PathKey $source.FullName
    if ($MoveMap.ContainsKey($sourceKey)) {
        return $MoveMap[$sourceKey]
    }

    $targetDir = Join-Path $BatchDir ("{0:00}_screenshots" -f $Index)
    $destination = Get-UniqueDestination -Destination (Join-Path $targetDir $source.Name)
    $MoveMap[$sourceKey] = $destination

    Add-Action `
        -Kind "ConsolidateScreenshot" `
        -Path $source.FullName `
        -Destination $destination `
        -Reason ("归入批次内第 {0:00} 条截图目录" -f $Index) `
        -Bytes (Get-SizeBytes -Path $source.FullName)

    return $destination
}

function Update-ResultObjectScreenshots {
    param(
        [object]$Result,
        [string]$BatchDir
    )

    if ($null -eq $Result -or -not ($Result.PSObject.Properties.Name -contains "Index")) {
        return $false
    }

    try {
        $index = [int]$Result.Index
    } catch {
        return $false
    }

    $changed = $false
    if ($Result.PSObject.Properties.Name -contains "FinalScreenshot") {
        $oldFinal = [string]$Result.FinalScreenshot
        $newFinal = Get-LegacyScreenshotDestination -OriginalPath $oldFinal -BatchDir $BatchDir -Index $index
        if ($newFinal -ne $oldFinal) {
            $Result.FinalScreenshot = $newFinal
            $changed = $true
        }
    }

    if ($Result.PSObject.Properties.Name -contains "Screenshots" -and $null -ne $Result.Screenshots) {
        $updatedScreenshots = @()
        foreach ($screenshot in @($Result.Screenshots)) {
            $oldPath = [string]$screenshot
            $newPath = Get-LegacyScreenshotDestination -OriginalPath $oldPath -BatchDir $BatchDir -Index $index
            if ($newPath -ne $oldPath) {
                $changed = $true
            }
            $updatedScreenshots += $newPath
        }
        $Result.Screenshots = [object[]]$updatedScreenshots
    }

    return $changed
}

function Register-JsonUpdate {
    param(
        [string]$Path,
        [object]$Data
    )

    Add-Action `
        -Kind "RewriteJson" `
        -Path $Path `
        -Destination "" `
        -Reason "更新截图路径到批次子目录" `
        -Bytes (Get-SizeBytes -Path $Path)
    $JsonUpdates.Add([pscustomobject]@{
        Path = $Path
        Data = $Data
    }) | Out-Null
}

function Get-BatchTimestamp {
    param([object]$BatchDir)

    if ($BatchDir.Name -match '^batch_(\d{8})_(\d{6})$') {
        $stamp = "{0}_{1}" -f $Matches[1], $Matches[2]
        try {
            return [datetime]::ParseExact($stamp, "yyyyMMdd_HHmmss", [System.Globalization.CultureInfo]::InvariantCulture)
        } catch {
        }
    }

    return $BatchDir.LastWriteTime
}

$batchDirs = @(Get-ChildItem -LiteralPath $ScreenshotsDir -Directory -Filter "batch_*" |
    Sort-Object @{ Expression = { Get-BatchTimestamp -BatchDir $_ }; Descending = $true })

if (-not $NoLegacyConsolidation) {
    foreach ($batch in $batchDirs) {
        $summaryPath = Join-Path $batch.FullName "summary.json"
        if (Test-Path -LiteralPath $summaryPath) {
            try {
                $summary = Get-Content -LiteralPath $summaryPath -Raw -Encoding UTF8 | ConvertFrom-Json
                $changed = $false
                foreach ($entry in @($summary)) {
                    if (Update-ResultObjectScreenshots -Result $entry -BatchDir $batch.FullName) {
                        $changed = $true
                    }
                }
                if ($changed) {
                    Register-JsonUpdate -Path $summaryPath -Data $summary
                }
            } catch {
                Write-Warning "无法解析 summary.json，已跳过: $summaryPath :: $($_.Exception.Message)"
            }
        }

        foreach ($resultPath in @(Get-ChildItem -LiteralPath $batch.FullName -File -Filter "*_result.json" -ErrorAction SilentlyContinue)) {
            try {
                $result = Get-Content -LiteralPath $resultPath.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
                if (Update-ResultObjectScreenshots -Result $result -BatchDir $batch.FullName) {
                    Register-JsonUpdate -Path $resultPath.FullName -Data $result
                }
            } catch {
                Write-Warning "无法解析 result.json，已跳过: $($resultPath.FullName) :: $($_.Exception.Message)"
            }
        }
    }
}

$rootScreenshotCutoff = (Get-Date).AddDays(-1 * $KeepRootScreenshotDays)
$rootScreenshots = @(Get-ChildItem -LiteralPath $ScreenshotsDir -File -Filter "*.png" |
    Where-Object { $_.Name -match '^(\d{6}_.*|diagnostic_.*)\.png$' -and $_.LastWriteTime -lt $rootScreenshotCutoff })

foreach ($screenshot in $rootScreenshots) {
    if ($MoveMap.ContainsKey((Get-PathKey $screenshot.FullName))) {
        continue
    }
    $day = $screenshot.LastWriteTime.ToString("yyyyMMdd")
    $destination = Get-UniqueDestination -Destination (Join-Path $ArchiveRoot (Join-Path "root_screenshots\$day" $screenshot.Name))
    Add-Action `
        -Kind "ArchiveRootScreenshot" `
        -Path $screenshot.FullName `
        -Destination $destination `
        -Reason "根目录截图/诊断图超过保留天数且未被批次索引接管" `
        -Bytes (Get-SizeBytes -Path $screenshot.FullName)
}

$watchLogCutoff = (Get-Date).AddDays(-1 * $KeepWatchLogDays)
$watchLogs = @(Get-ChildItem -LiteralPath $ScreenshotsDir -File -Filter "*.log" |
    Where-Object { $_.Name -like "watch_*.log" -and $_.LastWriteTime -lt $watchLogCutoff })

foreach ($log in $watchLogs) {
    $day = $log.LastWriteTime.ToString("yyyyMMdd")
    $destination = Get-UniqueDestination -Destination (Join-Path $ArchiveRoot (Join-Path "watch_logs\$day" $log.Name))
    Add-Action `
        -Kind "ArchiveWatchLog" `
        -Path $log.FullName `
        -Destination $destination `
        -Reason "临时盯跑日志超过保留天数" `
        -Bytes (Get-SizeBytes -Path $log.FullName)
}

$batchCutoff = (Get-Date).AddDays(-1 * $KeepBatchDays)
$recentBatchKeys = @{}
foreach ($batch in @($batchDirs | Select-Object -First $KeepRecentBatches)) {
    $recentBatchKeys[(Get-PathKey $batch.FullName)] = $true
}

foreach ($batch in $batchDirs) {
    $batchKey = Get-PathKey $batch.FullName
    $batchTimestamp = Get-BatchTimestamp -BatchDir $batch
    if ($recentBatchKeys.ContainsKey($batchKey) -or $batchTimestamp -ge $batchCutoff) {
        continue
    }
    $destination = Get-UniqueDestination -Destination (Join-Path (Join-Path $ArchiveRoot "batches") $batch.Name)
    Add-Action `
        -Kind "ArchiveBatch" `
        -Path $batch.FullName `
        -Destination $destination `
        -Reason "批次目录超过保留天数且不在最近批次内" `
        -Bytes (Get-SizeBytes -Path $batch.FullName)
}

if (-not $KeepPyCache) {
    foreach ($cacheDir in @(Get-ChildItem -LiteralPath $Root -Directory -Filter "__pycache__" -Recurse -Force -ErrorAction SilentlyContinue)) {
        if ($cacheDir.FullName -like "$ArchiveRoot*") {
            continue
        }
        Add-Action `
            -Kind "RemovePyCache" `
            -Path $cacheDir.FullName `
            -Destination "" `
            -Reason "Python 字节码缓存，可自动再生成" `
            -Bytes (Get-SizeBytes -Path $cacheDir.FullName)
    }
}

if ($PurgeArchiveDays -gt 0 -and (Test-Path -LiteralPath $ArchiveRoot)) {
    $archiveCutoff = (Get-Date).AddDays(-1 * $PurgeArchiveDays)
    foreach ($archivedFile in @(Get-ChildItem -LiteralPath $ArchiveRoot -File -Recurse -Force | Where-Object { $_.LastWriteTime -lt $archiveCutoff })) {
        Add-Action `
            -Kind "DeleteArchivedFile" `
            -Path $archivedFile.FullName `
            -Destination "" `
            -Reason "归档文件超过清除天数" `
            -Bytes (Get-SizeBytes -Path $archivedFile.FullName)
    }
}

$totalBytes = ($Actions | Measure-Object Bytes -Sum).Sum
if ($null -eq $totalBytes) {
    $totalBytes = 0
}
$totalMb = [math]::Round($totalBytes / 1MB, 2)
$mode = if ($Apply) { "APPLY" } else { "DRY-RUN" }

Write-Host "MODE=$mode"
Write-Host "ROOT=$Root"
Write-Host "ACTIONS=$($Actions.Count); SIZE_MB=$totalMb"

if ($Actions.Count -gt 0) {
    $Actions |
        Group-Object Kind |
        Sort-Object Name |
        Select-Object Name,Count,@{Name="MB";Expression={[math]::Round((($_.Group | Measure-Object Bytes -Sum).Sum) / 1MB, 2)}} |
        Format-Table -AutoSize

    $preview = if ($ShowAll) { $Actions } else { @($Actions | Select-Object -First 40) }
    $preview |
        Select-Object Kind,Reason,@{Name="MB";Expression={[math]::Round($_.Bytes / 1MB, 3)}},Path,Destination |
        Format-Table -Wrap

    if (-not $ShowAll -and $Actions.Count -gt 40) {
        Write-Host ("仅显示前 40 条；如需完整清单加 -ShowAll。剩余 {0} 条。" -f ($Actions.Count - 40))
    }
}

if (-not $Apply) {
    Write-Host "未加 -Apply，只展示计划，不移动/删除/改写任何文件。"
    exit 0
}

foreach ($action in @($Actions | Where-Object { $_.Kind -eq "ConsolidateScreenshot" })) {
    Ensure-ParentDirectory -Path $action.Destination
    if (Test-Path -LiteralPath $action.Path) {
        Move-Item -LiteralPath $action.Path -Destination $action.Destination
    }
}

foreach ($update in $JsonUpdates) {
    $json = $update.Data | ConvertTo-Json -Depth 8
    Set-Content -LiteralPath $update.Path -Value $json -Encoding UTF8
}

foreach ($action in @($Actions | Where-Object { $_.Kind -in @("ArchiveRootScreenshot", "ArchiveWatchLog", "ArchiveBatch") })) {
    Ensure-ParentDirectory -Path $action.Destination
    if (Test-Path -LiteralPath $action.Path) {
        Move-Item -LiteralPath $action.Path -Destination $action.Destination
    }
}

foreach ($action in @($Actions | Where-Object { $_.Kind -eq "RemovePyCache" })) {
    if (Test-Path -LiteralPath $action.Path) {
        Remove-Item -LiteralPath $action.Path -Recurse -Force
    }
}

foreach ($action in @($Actions | Where-Object { $_.Kind -eq "DeleteArchivedFile" })) {
    if (Test-Path -LiteralPath $action.Path) {
        Remove-Item -LiteralPath $action.Path -Force
    }
}

Write-Host "清理完成。"
