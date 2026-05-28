#requires -Version 5.1
<#
.SYNOPSIS
  桌面三大项目（财务/ M3直供合同付款数据获取/ 网关/）的统一垃圾清理入口。

.DESCRIPTION
  - 财务/   委托给 财务\公共\maintenance\cleanup_bank_artifacts.ps1
  - M3/    委托给 M3直供合同付款数据获取\cleanup_workspace.py
  - 网关/   本脚本内联处理（旋转日志 gateway.err/out.YYYYMMDD_*.log、
            data\reports\*.log、scripts\__pycache__）。
            保留策略：最近 10 个 AND 最近 7 天 AND 至少 6 小时新文件保护。

  默认 dry-run，只列计划不动手；加 -Apply 才真删。

.PARAMETER Apply
  执行实际删除/迁移；不加只是预览。

.PARAMETER ShowAll
  传给子脚本，展开全部条目而非前 N 条。

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\30112\Desktop\财务\公共\maintenance\cleanup_all.ps1
  # 预览三个项目的清理计划，不动任何文件

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\30112\Desktop\财务\公共\maintenance\cleanup_all.ps1 -Apply
  # 确认后真删
#>
param(
    [switch]$Apply,
    [switch]$ShowAll,
    [int]$GatewayKeepLogFiles = 10,
    [int]$GatewayMaxLogAgeDays = 7,
    [int]$MinAgeHours = 6,
    [switch]$SkipFinance,
    [switch]$SkipM3,
    [switch]$SkipGateway
)

$ErrorActionPreference = "Stop"
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = [Console]::OutputEncoding
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
} catch {
}

$DesktopRoot = "C:\Users\30112\Desktop"
$FinanceRoot  = Join-Path $DesktopRoot "财务"
$M3Root       = Join-Path $DesktopRoot "M3直供合同付款数据获取"
$GatewayRoot  = Join-Path $DesktopRoot "网关"

$AllowedRoots = @($FinanceRoot, $M3Root, $GatewayRoot) |
    ForEach-Object { (Get-Item -LiteralPath $_ -ErrorAction Stop).FullName.TrimEnd("\") }

function Test-InsideAllowedRoot {
    param([string]$Path)
    try {
        $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    } catch {
        return $false
    }
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to touch reparse point: $($item.FullName)"
    }
    $resolved = $item.FullName.TrimEnd("\")
    foreach ($root in $AllowedRoots) {
        if ($resolved -eq $root) { return $true }
        if ($resolved.StartsWith($root + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
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
        if ($null -eq $sum) { return [int64]0 }
        return [int64]$sum
    } catch {
        return [int64]0
    }
}

$mode = if ($Apply) { "APPLY" } else { "DRY-RUN" }
Write-Host ""
Write-Host "================================================================"
Write-Host " 桌面三项目统一清理"
Write-Host " MODE = $mode    (加 -Apply 才真删)"
Write-Host "================================================================"

# ----------------------------------------------------------------------
# 1) 财务/  委托给已有的 cleanup_bank_artifacts.ps1
# ----------------------------------------------------------------------
if (-not $SkipFinance) {
    $financeCleanup = Join-Path $FinanceRoot "公共\maintenance\cleanup_bank_artifacts.ps1"
    Write-Host ""
    Write-Host "[1/3] 财务/  -> $financeCleanup"
    Write-Host "----------------------------------------------------------------"
    if (-not (Test-Path -LiteralPath $financeCleanup)) {
        Write-Warning "找不到 $financeCleanup，跳过财务清理。"
    } else {
        $financeArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $financeCleanup)
        if ($Apply)   { $financeArgs += "-Apply" }
        if ($ShowAll) { $financeArgs += "-ShowAll" }
        & powershell.exe @financeArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "财务清理脚本返回非零退出码: $LASTEXITCODE"
        }
    }
} else {
    Write-Host ""
    Write-Host "[1/3] 财务/  跳过 (-SkipFinance)"
}

# ----------------------------------------------------------------------
# 2) M3直供合同付款数据获取/  委托给已有的 cleanup_workspace.py
# ----------------------------------------------------------------------
if (-not $SkipM3) {
    $m3Cleanup = Join-Path $M3Root "cleanup_workspace.py"
    Write-Host ""
    Write-Host "[2/3] M3直供合同付款数据获取/  -> $m3Cleanup"
    Write-Host "----------------------------------------------------------------"
    if (-not (Test-Path -LiteralPath $m3Cleanup)) {
        Write-Warning "找不到 $m3Cleanup，跳过 M3 清理。"
    } else {
        $m3Args = @($m3Cleanup)
        if ($Apply) { $m3Args += "--apply" }
        Push-Location -LiteralPath $M3Root
        try {
            & python @m3Args
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "M3 清理脚本返回非零退出码: $LASTEXITCODE"
            }
        } finally {
            Pop-Location
        }
    }
} else {
    Write-Host ""
    Write-Host "[2/3] M3直供合同付款数据获取/  跳过 (-SkipM3)"
}

# ----------------------------------------------------------------------
# 3) 网关/  内联处理
# ----------------------------------------------------------------------
$GatewayActions = New-Object System.Collections.Generic.List[object]

function Add-GatewayAction {
    param([string]$Kind, [string]$Path, [string]$Reason)
    $bytes = Get-SizeBytes -Path $Path
    $GatewayActions.Add([pscustomobject]@{
        Kind   = $Kind
        Path   = $Path
        Reason = $Reason
        Bytes  = $bytes
    }) | Out-Null
}

function Plan-RotatedLogs {
    param(
        [string]$Directory,
        [string]$Pattern,
        [string]$Label
    )
    if (-not (Test-Path -LiteralPath $Directory)) { return }
    $candidates = @(
        Get-ChildItem -LiteralPath $Directory -File -Filter $Pattern -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending
    )
    if ($candidates.Count -le 0) { return }

    $freshCutoff = (Get-Date).AddHours(-1 * $MinAgeHours)
    $ageCutoff   = (Get-Date).AddDays(-1 * $GatewayMaxLogAgeDays)

    for ($i = 0; $i -lt $candidates.Count; $i++) {
        $file = $candidates[$i]

        if ($i -lt $GatewayKeepLogFiles) { continue }            # 保最近 N 个
        if ($file.LastWriteTime -ge $ageCutoff)   { continue }   # 保最近 7 天
        if ($file.LastWriteTime -ge $freshCutoff) { continue }   # 保最近 6 小时新文件

        if (-not (Test-InsideAllowedRoot -Path $file.FullName)) {
            Write-Warning "拒绝处理白名单外的路径: $($file.FullName)"
            continue
        }
        Add-GatewayAction -Kind "DeleteLog" -Path $file.FullName `
            -Reason "$Label：超过保留窗口（>$GatewayKeepLogFiles 且 >$GatewayMaxLogAgeDays 天）"
    }
}

function Plan-PyCache {
    param([string]$Directory)
    if (-not (Test-Path -LiteralPath $Directory)) { return }
    $caches = @(
        Get-ChildItem -LiteralPath $Directory -Directory -Filter "__pycache__" -Recurse -Force -ErrorAction SilentlyContinue
    )
    foreach ($cache in $caches) {
        if (-not (Test-InsideAllowedRoot -Path $cache.FullName)) { continue }
        Add-GatewayAction -Kind "RemovePyCache" -Path $cache.FullName `
            -Reason "Python 字节码缓存，可自动再生成"
    }
}

if (-not $SkipGateway) {
    Write-Host ""
    Write-Host "[3/3] 网关/  内联清理 (保最近 $GatewayKeepLogFiles 个且 $GatewayMaxLogAgeDays 天，"
    Write-Host "                       新文件 $MinAgeHours 小时内不动)"
    Write-Host "----------------------------------------------------------------"
    if (-not (Test-Path -LiteralPath $GatewayRoot)) {
        Write-Warning "找不到 $GatewayRoot，跳过网关清理。"
    } else {
        # 根目录旋转日志 gateway.err.YYYYMMDD_*.log / gateway.out.YYYYMMDD_*.log
        # 注意：不带时间戳的 gateway.err.log / gateway.out.log 是 live 文件，不在此 pattern 内。
        Plan-RotatedLogs -Directory $GatewayRoot -Pattern "gateway.err.*_*.log" -Label "网关 stderr 旋转日志"
        Plan-RotatedLogs -Directory $GatewayRoot -Pattern "gateway.out.*_*.log" -Label "网关 stdout 旋转日志"

        # data/reports/ 失败上报日志
        $reportsDir = Join-Path $GatewayRoot "data\reports"
        Plan-RotatedLogs -Directory $reportsDir -Pattern "*.log" -Label "data/reports 历史上报日志"

        # __pycache__
        Plan-PyCache -Directory $GatewayRoot

        $total = ($GatewayActions | Measure-Object Bytes -Sum).Sum
        if ($null -eq $total) { $total = 0 }
        $mb = [math]::Round($total / 1MB, 2)
        Write-Host "网关计划: $($GatewayActions.Count) 条, 合计约 $mb MB"

        if ($GatewayActions.Count -gt 0) {
            $preview = if ($ShowAll) { $GatewayActions } else { @($GatewayActions | Select-Object -First 40) }
            $preview |
                Select-Object Kind, @{Name="MB"; Expression={[math]::Round($_.Bytes/1MB, 3)}}, Reason, Path |
                Format-Table -Wrap
            if (-not $ShowAll -and $GatewayActions.Count -gt 40) {
                Write-Host ("仅显示前 40 条；剩余 {0} 条 (-ShowAll 看全部)。" -f ($GatewayActions.Count - 40))
            }
        }

        if ($Apply -and $GatewayActions.Count -gt 0) {
            foreach ($action in $GatewayActions) {
                if (-not (Test-InsideAllowedRoot -Path $action.Path)) {
                    Write-Warning "执行前路径校验失败，跳过: $($action.Path)"
                    continue
                }
                try {
                    if ($action.Kind -eq "DeleteLog") {
                        Remove-Item -LiteralPath $action.Path -Force
                    } elseif ($action.Kind -eq "RemovePyCache") {
                        Remove-Item -LiteralPath $action.Path -Recurse -Force
                    }
                } catch {
                    Write-Warning "删除失败 $($action.Path) :: $($_.Exception.Message)"
                }
            }
            Write-Host "网关清理完成。"
        }
    }
} else {
    Write-Host ""
    Write-Host "[3/3] 网关/  跳过 (-SkipGateway)"
}

Write-Host ""
Write-Host "================================================================"
if ($Apply) {
    Write-Host " 清理完成 (APPLY 模式)。"
} else {
    Write-Host " 仅预览 (DRY-RUN)。确认无误后重跑：加 -Apply。"
}
Write-Host "================================================================"
