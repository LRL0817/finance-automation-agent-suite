#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$FinanceRoot = "",
    [string]$M3Root = "",
    [string]$GatewayRoot = "",
    [switch]$SkipCompile,
    [switch]$SkipGateway,
    [switch]$SkipM3Verification,
    [switch]$RunM3DryRun,
    [switch]$FailOnWarnings
)

$ErrorActionPreference = "Stop"
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $OutputEncoding = [Console]::OutputEncoding
    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
} catch {
}

$script:Failures = New-Object System.Collections.Generic.List[string]
$script:Warnings = New-Object System.Collections.Generic.List[string]
$script:Passes = New-Object System.Collections.Generic.List[string]
$StartedAt = Get-Date

function Write-Section {
    param([string]$Text)
    Write-Host ""
    Write-Host "== $Text =="
}

function Add-Pass {
    param([string]$Text)
    $script:Passes.Add($Text) | Out-Null
    Write-Host "[PASS] $Text" -ForegroundColor Green
}

function Add-Warning {
    param([string]$Text)
    $script:Warnings.Add($Text) | Out-Null
    Write-Warning $Text
}

function Add-Failure {
    param([string]$Text)
    $script:Failures.Add($Text) | Out-Null
    Write-Host "[FAIL] $Text" -ForegroundColor Red
}

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$ScriptBlock
    )

    Write-Section $Name
    try {
        & $ScriptBlock
    } catch {
        Add-Failure "$Name :: $($_.Exception.Message)"
    }
}

function Resolve-Tool {
    param(
        [string[]]$Names,
        [switch]$Required
    )

    foreach ($name in $Names) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -ne $cmd) {
            return $cmd.Source
        }
    }
    if ($Required) {
        throw "Command not found: $($Names -join ', ')"
    }
    return $null
}

function Invoke-External {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory
    )

    Push-Location -LiteralPath $WorkingDirectory
    try {
        & $FilePath @Arguments
        $code = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
        if ($code -ne 0) {
            throw "Command exited with code $code : $FilePath $($Arguments -join ' ')"
        }
    } finally {
        Pop-Location
    }
}

function Assert-Directory {
    param([string]$Path, [string]$Label)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Label does not exist or is not a directory: $Path"
    }
    return (Get-Item -LiteralPath $Path).FullName
}

function Test-Truthy {
    param([string]$Value)
    if ($null -eq $Value) {
        return $false
    }
    return @("1", "true", "yes", "on") -contains ($Value.Trim().ToLowerInvariant())
}

function Test-GitRepo {
    param([string]$Root)
    $git = Resolve-Tool -Names @("git") -Required:$false
    if (-not $git) {
        return $false
    }
    Push-Location -LiteralPath $Root
    $oldErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $null = & $git rev-parse --is-inside-work-tree 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
        Pop-Location
    }
}

function Get-GitLines {
    param([string]$Root, [string[]]$Arguments)
    $git = Resolve-Tool -Names @("git") -Required
    Push-Location -LiteralPath $Root
    try {
        return @(& $git @Arguments)
    } finally {
        Pop-Location
    }
}

function Find-LatestM3VerificationRecords {
    param([string]$Root)
    $verificationRoot = Join-Path $Root "runtime\verification"
    if (-not (Test-Path -LiteralPath $verificationRoot -PathType Container)) {
        return $null
    }
    $records = @(Get-ChildItem -LiteralPath $verificationRoot -Recurse -File -Filter "records.json" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending)
    if ($records.Count -eq 0) {
        return $null
    }
    return $records[0].FullName
}

function Resolve-DefaultRoots {
    $desktop = [Environment]::GetFolderPath("Desktop")
    if (-not $script:FinanceRoot -or -not $script:FinanceRoot.Trim()) {
        $script:FinanceRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    }
    if (-not $script:M3Root -or -not $script:M3Root.Trim()) {
        $m3Candidate = @(Get-ChildItem -LiteralPath $desktop -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "M3*" } |
            Sort-Object Name |
            Select-Object -First 1)
        if ($m3Candidate.Count -gt 0) {
            $script:M3Root = $m3Candidate[0].FullName
        }
    }
    if (-not $script:GatewayRoot -or -not $script:GatewayRoot.Trim()) {
        $gatewayName = ([string][char]0x7f51) + ([string][char]0x5173)
        $script:GatewayRoot = Join-Path $desktop $gatewayName
    }
}

Resolve-DefaultRoots

Invoke-Step "Path checks" {
    $script:FinanceRootFull = Assert-Directory -Path $script:FinanceRoot -Label "Finance root"
    $script:M3RootFull = Assert-Directory -Path $script:M3Root -Label "M3 root"
    $script:GatewayRootFull = Assert-Directory -Path $script:GatewayRoot -Label "Gateway root"
    Add-Pass "Finance: $script:FinanceRootFull"
    Add-Pass "M3: $script:M3RootFull"
    Add-Pass "Gateway: $script:GatewayRootFull"
}

Invoke-Step "Dangerous environment checks" {
    $dangerous = @(
        @{ Name = "ZHIDAN_TEST_MODE"; Bad = "0"; Reason = "CMB real maker-entry switch" },
        @{ Name = "CIB_ALLOW_SUBMIT"; Bad = "1"; Reason = "CIB submit switch" },
        @{ Name = "BOC_ENABLE_ORDER_SUBMIT"; Bad = "1"; Reason = "BOC bottom submit switch" },
        @{ Name = "ABC_ALLOW_SUBMIT_ONCE"; Bad = "true"; Reason = "ABC page submit switch" },
        @{ Name = "M3_PRODUCTION_MODE"; Bad = "1"; Reason = "M3 production dispatch switch" }
    )

    foreach ($item in $dangerous) {
        $value = [Environment]::GetEnvironmentVariable($item.Name)
        if ($null -ne $value -and $value.Trim().ToLowerInvariant() -eq $item.Bad) {
            Add-Failure "$($item.Name)=$value is enabled in the current shell: $($item.Reason)"
        }
    }

    $amountOverride = [Environment]::GetEnvironmentVariable("M3_BANK_AMOUNT_OVERRIDE")
    if ($amountOverride) {
        Add-Warning "M3_BANK_AMOUNT_OVERRIDE=$amountOverride is set; clear it before production."
    }

    if ($script:Failures.Count -eq 0) {
        Add-Pass "No production submit switches found in the current shell."
    }
}

Invoke-Step "Documentation entry checks" {
    $requiredDocs = @(
        (Join-Path $script:FinanceRootFull "AGENTS.md"),
        (Join-Path $script:FinanceRootFull "CLAUDE.md"),
        (Join-Path $script:M3RootFull "SKILL.md"),
        (Join-Path $script:GatewayRootFull "README.md")
    )
    foreach ($doc in $requiredDocs) {
        if (-not (Test-Path -LiteralPath $doc -PathType Leaf)) {
            Add-Failure "Missing documentation file: $doc"
        }
    }
    if ($script:Failures.Count -eq 0) {
        Add-Pass "Key documentation files exist."
    }
}

Invoke-Step "Git sensitive artifact checks" {
    $roots = @($script:FinanceRootFull, $script:M3RootFull, $script:GatewayRootFull)
    foreach ($root in $roots) {
        if (-not (Test-GitRepo -Root $root)) {
            Add-Warning "Not a Git repository; skipped tracked-file check: $root"
            continue
        }

        $tracked = Get-GitLines -Root $root -Arguments @("ls-files")
        $bad = @($tracked | Where-Object {
            $_ -match '(^|/)\.env$' -or
            $_ -match '(^|/)\.env\.(local|production|prod|development|dev|test)$' -or
            $_ -match '(^|/)(runtime|data|screenshots|debug_runs|runs|logs|\.browser_profile)(/|$)' -or
            $_ -match '\.(sqlite|sqlite-shm|sqlite-wal|log|png|jpg|jpeg)$'
        })
        if ($bad.Count -gt 0) {
            Add-Failure "Sensitive/runtime artifacts are tracked by Git: $root :: $($bad -join ', ')"
        } else {
            Add-Pass "No tracked .env/runtime/screenshots/log artifacts found: $root"
        }

        $status = @(Get-GitLines -Root $root -Arguments @("status", "--short", "--untracked-files=normal"))
        if ($status.Count -gt 0) {
            Add-Warning "Working tree has uncommitted changes: $root :: $($status.Count) items. Create a baseline commit or snapshot before risky edits."
        }
    }
}

if (-not $SkipCompile) {
    Invoke-Step "Python compile checks" {
        $python = Resolve-Tool -Names @("python", "py") -Required
        Invoke-External -FilePath $python -Arguments @("-m", "compileall", "-q", $script:FinanceRootFull) -WorkingDirectory $script:FinanceRootFull
        Add-Pass "Finance Python compileall passed."
        Invoke-External -FilePath $python -Arguments @("-m", "compileall", "-q", $script:M3RootFull) -WorkingDirectory $script:M3RootFull
        Add-Pass "M3 Python compileall passed."
    }
}

if (-not $SkipGateway) {
    Invoke-Step "Gateway TypeScript check" {
        $packageJson = Join-Path $script:GatewayRootFull "package.json"
        if (-not (Test-Path -LiteralPath $packageJson -PathType Leaf)) {
            Add-Warning "Gateway package.json is missing; skipped typecheck."
            return
        }
        $npm = Resolve-Tool -Names @("npm.cmd", "npm") -Required
        Invoke-External -FilePath $npm -Arguments @("run", "typecheck", "--silent") -WorkingDirectory $script:GatewayRootFull
        Add-Pass "Gateway npm run typecheck passed."
    }
}

if (-not $SkipM3Verification) {
    Invoke-Step "M3 read-only verification" {
        $records = Find-LatestM3VerificationRecords -Root $script:M3RootFull
        if (-not $records) {
            Add-Warning "No runtime\\verification\\*\\records.json found; skipped M3 read-only verification."
            return
        }
        $python = Resolve-Tool -Names @("python", "py") -Required
        Push-Location -LiteralPath $script:M3RootFull
        try {
            $output = @(& $python "verify_m3_extraction_accuracy.py" "--records" $records "--max-issues" "20" 2>&1)
            $code = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
            if ($code -ne 0) {
                $output | Select-Object -Last 80 | ForEach-Object { Write-Host $_ }
                throw "M3 read-only verification exited with code $code"
            }
            try {
                $summary = ($output -join "`n") | ConvertFrom-Json
                Add-Pass "M3 read-only verification passed: records=$($summary.records), issues=$($summary.issues), source=$records"
            } catch {
                Add-Pass "M3 read-only verification passed: $records"
            }
        } finally {
            Pop-Location
        }
    }
}

if ($RunM3DryRun) {
    Invoke-Step "M3 bank queue dry-run" {
        $scriptPath = Join-Path $script:M3RootFull "run_bank_batch.py"
        if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
            Add-Warning "run_bank_batch.py is missing; skipped M3 dry-run."
            return
        }
        $python = Resolve-Tool -Names @("python", "py") -Required
        Invoke-External -FilePath $python -Arguments @("run_bank_batch.py", "--all", "--dry-run", "--limit", "1") -WorkingDirectory $script:M3RootFull
        Add-Pass "M3 bank queue dry-run passed."
    }
}

Write-Section "Summary"
$elapsed = New-TimeSpan -Start $StartedAt -End (Get-Date)
Write-Host ("Elapsed: {0:n1}s" -f $elapsed.TotalSeconds)
Write-Host "Passed: $($script:Passes.Count), warnings: $($script:Warnings.Count), failures: $($script:Failures.Count)"

if ($script:Warnings.Count -gt 0) {
    Write-Host ""
    Write-Host "Warnings:" -ForegroundColor Yellow
    foreach ($warning in $script:Warnings) {
        Write-Host " - $warning" -ForegroundColor Yellow
    }
}

if ($script:Failures.Count -gt 0) {
    Write-Host ""
    Write-Host "Failures:" -ForegroundColor Red
    foreach ($failure in $script:Failures) {
        Write-Host " - $failure" -ForegroundColor Red
    }
    exit 1
}

if ($FailOnWarnings -and $script:Warnings.Count -gt 0) {
    exit 2
}

exit 0
