$ErrorActionPreference = "Stop"

$workspace = Split-Path -Parent $MyInvocation.MyCommand.Path
$financeRoot = Split-Path -Parent (Split-Path -Parent $workspace)
$cleanupAfterAutomation = Join-Path $financeRoot "公共\maintenance\cleanup_after_automation.ps1"
$python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
$batchPath = Join-Path $workspace "screenshot_transfer_batch.json"
$scriptPath = Join-Path $workspace "skills\单笔转账\open_browser.py"

function Invoke-FinanceArtifactCleanup {
    if (-not (Test-Path -LiteralPath $cleanupAfterAutomation -PathType Leaf)) {
        return
    }
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $cleanupAfterAutomation
    } catch {
        Write-Warning "artifact cleanup failed: $($_.Exception.Message)"
    }
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python not found: $python"
}
if (-not (Test-Path -LiteralPath $batchPath)) {
    throw "Batch data not found: $batchPath"
}
if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw "Open browser script not found: $scriptPath"
}

$env:TRANSFER_BATCH_PATH = $batchPath
$env:ABC_USB12_PREPARE = "true"
$env:ABC_USB_HUB_PORTS = "29,30"
$env:ABC_OK_SERVO_ENABLE = "true"
$env:ABC_OK_SERVO_AFTER_KB_PASSWORD = "false"
$env:ABC_OK_SERVO_AFTER_TRANSFER_KB_PASSWORD = "true"
$env:ABC_OK_SERVO_HOTKEY = "scrolllock"

Write-Host "Workspace: $workspace"
Write-Host "Batch data: $batchPath"
Write-Host "Launching ABC batch fill..."
Write-Host "The script fills forms only. It does not submit transfers or handle verification codes."
Write-Host "USB Hub ports: 30(auto-clicker), 29(ABC U-key). Servo is enabled only after transfer K-key password, not during login."

$exitCode = 0
try {
    & $python $scriptPath
    $exitCode = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
} finally {
    Invoke-FinanceArtifactCleanup
}
exit $exitCode
