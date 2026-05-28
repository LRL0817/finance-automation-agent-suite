param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$Prompt,

    [string]$PromptFile,
    [string]$Cwd = (Get-Location).Path,
    [string]$OutputDir = "C:\Users\30112\Desktop\财务\.claude\codex_bridge_runs",

    [ValidateSet("text", "json", "stream-json")]
    [string]$OutputFormat = "text",

    [ValidateSet("default", "acceptEdits", "auto", "bypassPermissions", "dontAsk", "plan")]
    [string]$PermissionMode = "default",

    [string]$Model,
    [ValidateSet("low", "medium", "high", "xhigh", "max")]
    [string]$Effort = "medium",

    [string[]]$AddDir = @(),
    [string[]]$AllowedTools = @(),
    [string[]]$DisallowedTools = @(),

    [switch]$Continue,
    [switch]$NoSessionPersistence,
    [switch]$EchoPrompt,
    [switch]$DryRun,
    [int]$TimeoutSeconds = 1800
)

$ErrorActionPreference = "Stop"

function Write-Info {
    param([string]$Message)
    Write-Host "[claude-bridge] $Message"
}

function Read-PromptText {
    $parts = New-Object System.Collections.Generic.List[string]

    if ($PromptFile) {
        $resolvedPromptFile = Resolve-Path -LiteralPath $PromptFile
        $parts.Add((Get-Content -LiteralPath $resolvedPromptFile -Raw -Encoding utf8))
    }

    if ($Prompt -and $Prompt.Count -gt 0) {
        $parts.Add(($Prompt -join " "))
    }

    try {
        if ([Console]::IsInputRedirected) {
            $stdinText = [Console]::In.ReadToEnd()
            if ($stdinText -and $stdinText.Trim().Length -gt 0) {
                $parts.Add($stdinText)
            }
        }
    } catch {
        # Some hosts do not expose IsInputRedirected cleanly. Ignore and rely on args/files.
    }

    $text = (($parts | Where-Object { $_ -and $_.Trim().Length -gt 0 }) -join "`n`n")
    if (-not $text -or $text.Trim().Length -eq 0) {
        throw "没有收到提示词。请传入文本、-PromptFile，或通过管道输入。"
    }
    return $text
}

if (-not (Test-Path -LiteralPath $Cwd -PathType Container)) {
    throw "工作目录不存在: $Cwd"
}

$claudeCmd = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claudeCmd) {
    throw "未找到 claude 命令。请先安装/登录 Claude Code CLI，并确认 claude 在 PATH 中。"
}

$promptText = Read-PromptText

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$runDir = Join-Path $OutputDir $timestamp
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

$promptPath = Join-Path $runDir "prompt.md"
$outputPath = Join-Path $runDir "claude_output.txt"
$metaPath = Join-Path $runDir "meta.json"

Set-Content -LiteralPath $promptPath -Value $promptText -Encoding utf8

$claudeArgs = @(
    "-p",
    "--input-format", "text",
    "--output-format", $OutputFormat,
    "--permission-mode", $PermissionMode,
    "--effort", $Effort
)

if ($Model) {
    $claudeArgs += @("--model", $Model)
}

if ($Continue) {
    $claudeArgs += "-c"
}

if ($NoSessionPersistence) {
    $claudeArgs += "--no-session-persistence"
}

foreach ($dir in $AddDir) {
    if ($dir -and $dir.Trim().Length -gt 0) {
        $claudeArgs += @("--add-dir", $dir)
    }
}

if ($AllowedTools -and $AllowedTools.Count -gt 0) {
    $claudeArgs += @("--allowedTools", ($AllowedTools -join ","))
}

if ($DisallowedTools -and $DisallowedTools.Count -gt 0) {
    $claudeArgs += @("--disallowedTools", ($DisallowedTools -join ","))
}

$meta = [ordered]@{
    timestamp = $timestamp
    cwd = (Resolve-Path -LiteralPath $Cwd).Path
    claude = $claudeCmd.Source
    args = $claudeArgs
    permissionMode = $PermissionMode
    outputFormat = $OutputFormat
    promptPath = $promptPath
    outputPath = $outputPath
    timeoutSeconds = $TimeoutSeconds
    dryRun = [bool]$DryRun
}

($meta | ConvertTo-Json -Depth 5) | Set-Content -LiteralPath $metaPath -Encoding utf8

Write-Info "工作目录: $Cwd"
Write-Info "提示词: $promptPath"
Write-Info "输出将保存到: $outputPath"
Write-Info "权限模式: $PermissionMode"

if ($EchoPrompt) {
    Write-Host ""
    Write-Host "========== PROMPT =========="
    Write-Host $promptText
    Write-Host "============================"
    Write-Host ""
}

if ($DryRun) {
    Write-Info "DryRun 已开启，只保存提示词和元数据，不调用 Claude。"
    exit 0
}

$scriptBlock = {
    param(
        [string]$ClaudeExe,
        [string[]]$ClaudeArgs,
        [string]$WorkingDirectory,
        [string]$PromptText
    )

    try {
        [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
        $OutputEncoding = [System.Text.Encoding]::UTF8
    } catch {
    }

    Set-Location -LiteralPath $WorkingDirectory
    $lines = $PromptText | & $ClaudeExe @ClaudeArgs 2>&1
    $exitCode = $LASTEXITCODE
    if ($null -eq $exitCode) {
        $exitCode = 0
    }

    [pscustomobject]@{
        ExitCode = [int]$exitCode
        Text = (($lines | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine)
    }
}

$jobArgs = @(
    $claudeCmd.Source,
    (, $claudeArgs),
    (Resolve-Path -LiteralPath $Cwd).Path,
    $promptText
)
$job = Start-Job -ScriptBlock $scriptBlock -ArgumentList $jobArgs

try {
    $completed = Wait-Job -Job $job -Timeout $TimeoutSeconds
    if (-not $completed) {
        Stop-Job -Job $job -Force | Out-Null
        throw "Claude 调用超过 $TimeoutSeconds 秒，已停止后台任务。"
    }

    $jobErrors = $job.ChildJobs | ForEach-Object { $_.Error } | Where-Object { $_ }
    $result = Receive-Job -Job $job
    if ($jobErrors) {
        $errorText = (($jobErrors | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine)
        $errorText | Set-Content -LiteralPath $outputPath -Encoding utf8
        throw "Claude 后台任务报错: $errorText"
    }

    $text = if ($result -and $result.Text) { $result.Text } else { "" }
    Set-Content -LiteralPath $outputPath -Value $text -Encoding utf8

    Write-Host ""
    Write-Host "========== CLAUDE OUTPUT =========="
    if ($text.Trim().Length -gt 0) {
        Write-Host $text
    } else {
        Write-Host "(Claude 没有返回文本输出)"
    }
    Write-Host "==================================="
    Write-Host ""
    Write-Info "结果已保存: $outputPath"

    if ($result.ExitCode -ne 0) {
        Write-Info "Claude 退出码: $($result.ExitCode)"
    }
    exit $result.ExitCode
} finally {
    Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
}
