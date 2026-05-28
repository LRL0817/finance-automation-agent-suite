#requires -Version 5.1
param(
    [string]$ProjectRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    [int]$MinAgeHours = 6
)

$ErrorActionPreference = "Continue"
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = [Console]::OutputEncoding
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
} catch {
}

$cleanup = Join-Path $PSScriptRoot "cleanup_bank_artifacts.ps1"
if (-not (Test-Path -LiteralPath $cleanup -PathType Leaf)) {
    Write-Warning "cleanup_bank_artifacts.ps1 not found: $cleanup"
    exit 0
}

try {
    & $cleanup -ProjectRoot $ProjectRoot -Apply -MinAgeHours $MinAgeHours
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "artifact cleanup exited with code $LASTEXITCODE"
    }
} catch {
    Write-Warning "artifact cleanup failed: $($_.Exception.Message)"
}

exit 0
