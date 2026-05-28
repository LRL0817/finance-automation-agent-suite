#requires -Version 5.1
<#
.SYNOPSIS
  Sensitive pre-commit scan for the 财务 / M3 / 网关 trio.

.DESCRIPTION
  Repeatable, read-only check that:
    1. Validates suspect file/dir patterns (env, runs, runtime, screenshots,
       transfer payloads, USB hub log) are honored by .gitignore in each
       git repo, via `git check-ignore`.
    2. Lists untracked candidates via `git ls-files --others --exclude-standard`
       and flags any that match a suspect pattern (would slip into a commit).
    3. Greps source files for generic sensitive markers — patterns only, no
       real account / payee / branch / amount / password values hardcoded.
       Operator can supply additional needle patterns via -ExtraNeedlesFile
       (file path, gitignored by convention).

  Strictly read-only: never opens banks, never spawns U-BANK / U-key /
  USB Hub control, never sets production env vars, never git add / commit / push.

.PARAMETER FinanceRoot
  Root of the 财务 directory. Default: C:\Users\30112\Desktop\财务

.PARAMETER M3Root
  Root of the M3 directory. Default: C:\Users\30112\Desktop\M3直供合同付款数据获取

.PARAMETER GatewayRoot
  Root of the 网关 directory. Default: C:\Users\30112\Desktop\网关

.PARAMETER ExtraNeedlesFile
  Optional path to a local file containing additional rg needles, one per
  line; lines starting with # are ignored. The file itself MUST be outside
  any tracked location or itself be gitignored — the script does not check
  this for you; do not commit needles that are themselves real values.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File `
    C:\Users\30112\Desktop\财务\公共\maintenance\run_sensitive_precommit_scan.ps1
#>
[CmdletBinding()]
param(
    [string]$FinanceRoot = "C:\Users\30112\Desktop\财务",
    [string]$M3Root = "C:\Users\30112\Desktop\M3直供合同付款数据获取",
    [string]$GatewayRoot = "C:\Users\30112\Desktop\网关",
    [string]$ExtraNeedlesFile = "",
    [string]$RgPath = "",
    [switch]$SelfTest
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

function Add-Pass {
    param([string]$Text)
    $script:Passes.Add($Text) | Out-Null
    Write-Host "[PASS] $Text" -ForegroundColor Green
}

function Add-Warning {
    param([string]$Text)
    $script:Warnings.Add($Text) | Out-Null
    Write-Host "[WARN] $Text" -ForegroundColor Yellow
}

function Add-Failure {
    param([string]$Text)
    $script:Failures.Add($Text) | Out-Null
    Write-Host "[FAIL] $Text" -ForegroundColor Red
}

function Write-Section {
    param([string]$Text)
    Write-Host ""
    Write-Host "== $Text =="
}

# Patterns whose **runtime artifacts must always be gitignored**.
# Each pattern is checked by git check-ignore using a *probe relative path*
# inside the repo root; we don't actually need the file to exist.
# `Roots` is a whitelist of root labels this probe applies to. Empty = apply to all.
$SuspectPatterns = @(
    @{ Relative = ".env";                                    Description = ".env (顶层凭据)";              Roots = @() },
    @{ Relative = "招行/招行制单/招行/.env";                  Description = "招行制单 .env";                 Roots = @("财务") },
    @{ Relative = "兴业/兴业制单/.env";                       Description = "兴业制单 .env";                 Roots = @("财务") },
    @{ Relative = "农业/农业银行/.env";                       Description = "农业银行 .env";                 Roots = @("财务") },
    @{ Relative = "中行/中国银行/.env";                       Description = "中行 .env";                     Roots = @("财务") },
    @{ Relative = "来参缘调拨/laicanyuan_payee.local.json";    Description = "来参缘真实收方 *.local.json";   Roots = @("财务") },
    @{ Relative = "runs/balance_x/balance.json";              Description = "runs/balance_*/balance.json";   Roots = @("财务") },
    @{ Relative = "runs/probe.log";                           Description = "runs/*.log";                    Roots = @("财务") },
    @{ Relative = "runtime/probe.lock";                       Description = "runtime/*.lock";                Roots = @() },
    @{ Relative = "runtime/current/latest.json";              Description = "M3 runtime latest.json";        Roots = @("M3") },
    @{ Relative = "latest.json";                              Description = "顶层 latest.json";              Roots = @("M3") },
    @{ Relative = "latest30_anything.json";                   Description = "latest30*.json";                Roots = @("财务") },
    @{ Relative = "screenshot_anything.png";                  Description = "screenshot*.png";               Roots = @("财务") },
    @{ Relative = "screenshot_anything.jpg";                  Description = "screenshot*.jpg";               Roots = @("财务") },
    @{ Relative = "screenshot_transfer_batch_anything.json";  Description = "screenshot_transfer_batch*.json"; Roots = @("财务") },
    @{ Relative = "transfer_batch_anything.json";             Description = "transfer_batch*.json";          Roots = @("财务") },
    @{ Relative = "log.txt";                                  Description = "log.txt (USB Hub vendor log)"; Roots = @("财务") }
)

# Suspect filename globs (PowerShell -like). Used to flag untracked files
# that .gitignore did NOT mask. The script does not delete or move anything.
$SuspectFilenameLike = @(
    "*.env",
    ".env",
    ".env.*",
    "*.local.json",
    "latest.json",
    "latest30*.json",
    "screenshot*.png",
    "screenshot*.jpg",
    "screenshot_transfer_batch*.json",
    "transfer_batch*.json",
    "log.txt"
)

# Filenames that LOOK suspect but are intentionally tracked as non-secret templates.
# `!.env.example` / `!**/.env.example` is preserved by the repo .gitignore on purpose.
$SuspectFilenameAllowlist = @(
    ".env.example"
)

# Suspect directory names — if untracked, they leak runtime artifacts.
$SuspectDirLike = @(
    "runs",
    "runtime",
    "debug_runs",
    "live_screenshots",
    "page_screenshots",
    "screenshots",
    "artifacts",
    "logs",
    "bank_forms",
    "bank_batches",
    "bank_runs",
    ".browser_profile"
)

function Test-IsGitRepo {
    param([string]$Root)
    if (-not (Test-Path -LiteralPath $Root)) { return $false }
    try {
        $prev = Get-Location
        Set-Location -LiteralPath $Root
        $inside = & git rev-parse --is-inside-work-tree 2>$null
        Set-Location -LiteralPath $prev
        return ($LASTEXITCODE -eq 0) -and ($inside -eq "true")
    } catch {
        return $false
    }
}

function Invoke-Git {
    param(
        [string]$Root,
        [Parameter(Mandatory)]
        [string[]]$Arguments
    )
    $prev = Get-Location
    Set-Location -LiteralPath $Root
    try {
        $out = & git @Arguments 2>&1
        $code = $LASTEXITCODE
        return [pscustomobject]@{ ExitCode = $code; Output = $out }
    } finally {
        Set-Location -LiteralPath $prev
    }
}

function Test-GitCheckIgnoreOne {
    param(
        [string]$Root,
        [string]$RelativePath
    )
    $r = Invoke-Git -Root $Root -Arguments @("check-ignore", "-v", "--no-index", $RelativePath)
    return [pscustomobject]@{
        IsIgnored = ($r.ExitCode -eq 0)
        Source    = ($r.Output -join " ")
    }
}

function Test-GitignoreCoverage {
    param([string]$Root, [string]$Label)

    Write-Section "$Label :: .gitignore coverage"
    if (-not (Test-IsGitRepo -Root $Root)) {
        Add-Warning "$Label 不是 git 仓库（$Root），跳过 git check-ignore"
        return
    }
    foreach ($pat in $SuspectPatterns) {
        # 跳过明确不适用于当前 root 的 probe
        if ($pat.Roots -and ($pat.Roots.Count -gt 0) -and ($pat.Roots -notcontains $Label)) {
            continue
        }
        $r = Test-GitCheckIgnoreOne -Root $Root -RelativePath $pat.Relative
        if ($r.IsIgnored) {
            Add-Pass "$Label :: $($pat.Description) -> ignored ($($r.Source.Trim()))"
        } else {
            Add-Failure "$Label :: $($pat.Description) 未被 .gitignore 覆盖（probe=$($pat.Relative)）"
        }
    }
}

function Test-UntrackedSuspects {
    param([string]$Root, [string]$Label)

    Write-Section "$Label :: 未跟踪文件命中敏感模式"
    if (-not (Test-IsGitRepo -Root $Root)) {
        Add-Warning "$Label 不是 git 仓库，跳过未跟踪扫描"
        return
    }
    $r = Invoke-Git -Root $Root -Arguments @("ls-files", "--others", "--exclude-standard")
    if ($r.ExitCode -ne 0) {
        Add-Failure "$Label :: git ls-files 失败 (rc=$($r.ExitCode))"
        return
    }
    $lines = @($r.Output) | Where-Object { $_ -ne $null -and $_.ToString().Trim().Length -gt 0 }
    # 本地 IDE / agent 配置目录的局部 settings 文件不视作敏感产物
    $allowlistDirs = @(".claude", ".codex", ".vscode", ".idea")
    $hits = New-Object System.Collections.Generic.List[string]
    foreach ($line in $lines) {
        $rel = $line.ToString()
        $name = Split-Path -Leaf $rel
        $parts = $rel -split "[\\/]"
        $matched = $false
        # 若路径包含任一 allowlist 目录前缀，跳过 filename/dir glob 检查
        $insideAllowlistDir = $false
        foreach ($d in $allowlistDirs) {
            if ($parts -contains $d) { $insideAllowlistDir = $true; break }
        }
        if ($insideAllowlistDir) {
            continue
        }
        if ($SuspectFilenameAllowlist -contains $name) {
            # 明确允许的非秘密模板（例如 .env.example），跳过 filename glob 检查
        } else {
            foreach ($glob in $SuspectFilenameLike) {
                if ($name -like $glob) {
                    $hits.Add("$rel  (glob=$glob)") | Out-Null
                    $matched = $true
                    break
                }
            }
        }
        if (-not $matched) {
            foreach ($d in $SuspectDirLike) {
                if ($parts -contains $d) {
                    $hits.Add("$rel  (dir=$d/)") | Out-Null
                    $matched = $true
                    break
                }
            }
        }
    }
    if ($hits.Count -eq 0) {
        Add-Pass "$Label :: 未发现可提交且命中敏感模式的文件"
    } else {
        foreach ($h in $hits) {
            Add-Failure "$Label :: 未跟踪 + 未忽略的敏感候选 -> $h"
        }
    }
}

function Resolve-Ripgrep {
    param([string]$Explicit)
    if ($Explicit -and (Test-Path -LiteralPath $Explicit)) { return $Explicit }
    foreach ($n in @("rg", "rg.exe")) {
        $cmd = Get-Command $n -ErrorAction SilentlyContinue
        if ($null -ne $cmd) { return $cmd.Source }
    }
    foreach ($p in @(
        "$env:USERPROFILE\AppData\Local\Programs\CodeBuddy CN\resources\app\node_modules\@vscode\ripgrep\bin\rg.exe",
        "$env:USERPROFILE\AppData\Local\Programs\Microsoft VS Code\resources\app\node_modules.asar.unpacked\@vscode\ripgrep\bin\rg.exe",
        "$env:USERPROFILE\scoop\apps\ripgrep\current\rg.exe",
        "C:\ProgramData\chocolatey\bin\rg.exe"
    )) {
        if ($p -and (Test-Path -LiteralPath $p)) { return $p }
    }
    return $null
}


# ----- value-level allowlist helpers ------------------------------------------------

# 「显然是占位 / 不是真实密钥」的值。两个目的：
#   (a) 文档里 `LOGIN_PWD=...` / `BOC_USHIELD_PIN=U盾PIN` 这类示例文本不要报；
#   (b) 代码里 `os.environ.get("BOC_USHIELD_PIN", "")` / `$env:X = "true"` 不要报。
function Test-IsPlaceholderValue {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $true }
    $v = $Value.Trim()
    # 剥离首尾引号 / 反引号 / 中文引号 / 角括号（用 [char] 避免 PowerShell 字串拼接歧义）
    $stripChars = @([char]34, [char]39, [char]96, [char]0x201C, [char]0x201D, [char]60, [char]62, [char]91, [char]93, [char]0xFF08, [char]0xFF09)
    $v = $v.Trim($stripChars)
    if ($v.Length -eq 0) { return $true }
    # 省略号 / 占位字符
    if ($v -match '^[…\.\?\*\-_…]+$') { return $true }
    if ($v -match '^xxx+$|^XXX+$|^xxxx.*xxxx$') { return $true }
    # 布尔 / 数字开关
    $boolVals = @('true','false','yes','no','on','off','0','1')
    if ($boolVals -contains $v.ToLowerInvariant()) { return $true }
    # 纯中文（仅 CJK + 标点）：当作描述文字，例如 `网盾密码`、`登录页密码`、`U盾PIN` 里的中文部分
    # 注意：`U盾PIN` 含 ASCII 但也属于描述性占位；下面单独列。
    if ($v -match '^[一-龥　-〿＀-￯]+$') { return $true }
    # 已知中文占位关键字
    $cnPlaceholders = @(
        '示例','占位','请勿提交','请勿用于','真实凭据','本地真实凭据','网盾密码','登录页密码',
        '证书密码','登录密码','U盾PIN','U盾密码','请填','请改','请用','某某','假数据','测试值'
    )
    foreach ($k in $cnPlaceholders) { if ($v -like "*$k*") { return $true } }
    # 已知英文占位关键字
    $enPlaceholders = @(
        'placeholder','example','sample','fake','demo','your-','your_','change_me','changeme',
        'TODO','FIXME','REDACTED','SECRET_HERE','TEST_TOKEN','TEST_SECRET','NEVER_SUBMIT','xxxxxx'
    )
    foreach ($k in $enPlaceholders) { if ($v.ToLowerInvariant() -like "*$($k.ToLowerInvariant())*") { return $true } }
    # 「U盾PIN」「U盾密码」这种 ASCII+CJK 混合的纯描述
    if ($v -match '^[A-Z]{1,3}盾(?:PIN|密码|口令|凭据)?$') { return $true }
    # 看起来像变量名占位（大写字母 + 下划线，长度 ≤ 32 且无数字混合）
    if ($v -match '^[A-Z][A-Z_]{2,31}$' -and ($v.Length -le 32)) { return $true }
    return $false
}

# 一行只是在「读」环境变量、而不是把 secret 写进源码。
function Test-LineLooksLikeCodeReader {
    param([string]$Line)
    if ($Line -match 'os\.environ(?:\.get)?\s*[\(\[]') { return $true }
    if ($Line -match 'os\.getenv\s*\(' ) { return $true }
    if ($Line -match 'process\.env\b' ) { return $true }
    if ($Line -match 'env::var\(' ) { return $true }
    if ($Line -match 'std::env::var') { return $true }
    if ($Line -match 'getenv\s*\(' ) { return $true }
    if ($Line -match '\$env:\w+\b' ) {
        # $env:NAME 出现在 PowerShell 行里，可能是赋值（$env:X = "true"）也可能是读取
        # 后续 value 校验会进一步过滤 placeholder（"true" / "false"）
        return $false
    }
    return $false
}

# 一行是 markdown 表格 / 行内代码 / 注释 / 文档说明
function Test-LineLooksLikeDocOrComment {
    param([string]$Line)
    $trim = $Line.TrimStart()
    if ($trim -match '^(#|//|--|;|\*\s|<!--|REM\b|rem\b)') { return $true }
    # markdown 内联 code 块（左右反引号包住）
    if ($Line -match '`[^`]*=[^`]*`') { return $true }
    return $false
}

function Test-LongDigitFalsePositive {
    param([string]$Line, [string]$Digits)
    if ($Digits -match '^0+$') { return $true }
    # 80% 以上是 0
    $zeroCount = ($Digits.ToCharArray() | Where-Object { $_ -eq '0' }).Count
    if (($zeroCount / [double]$Digits.Length) -ge 0.8) { return $true }
    # 文档/注释里写"长度 16-24 位"这种范围描述
    if ($Line -match '\d{1,3}[-_]?\d{1,3}\s*位') { return $true }
    if ($Line -match '\\d\{[0-9, ]+\}') { return $true } # 正则字面量本身
    if ($Line -match '\.length\b|len\s*\(') { return $true }
    # URL / 浏览器导航参数：OA 报表入口、portalId / rptDesignId / formmain_*_id / extendParams 之类
    if ($Line -match 'https?://') { return $true }
    if ($Line -match '\b(?:portalId|rptDesignId|reportPenetrate|extendParams|resourceCode|formmain_\d+_\d+_id|seeyon|report4Result|vReportView)\b') { return $true }
    # 通用 URL query 参数：`?xxx=long-digit&` 或 `&xxx=long-digit`
    if ($Line -match '[?&][A-Za-z_]+=\d{8,}') { return $true }
    # 时间戳类：2026-05-20T16:00:00 之类的 ISO-8601 通常不会被 \b\d{16,24}\b 命中，但保险起见放过
    # （\b\d{16,24}\b 要求 16-24 位连续，时间戳中带 `-` `:` 不会拼成长串）
    return $false
}

# rg 调用：分离 stdout / stderr / rc。
# 注意：`$Args` 是 PowerShell 自动变量，不能作为参数名直接 splat —— 这里用 $RgArgs。
function Invoke-Rg {
    param([string]$RgPath, [string[]]$RgArgs)
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $errFile = [System.IO.Path]::GetTempFileName()
    try {
        $stdout = & $RgPath @RgArgs 2>$errFile
        $rc = $LASTEXITCODE
        $stderrText = ""
        if (Test-Path -LiteralPath $errFile) {
            $stderrText = (Get-Content -LiteralPath $errFile -Raw -ErrorAction SilentlyContinue) -as [string]
        }
        return [pscustomobject]@{
            ExitCode = $rc
            Stdout = @($stdout)
            Stderr = $stderrText
        }
    } catch {
        return [pscustomobject]@{
            ExitCode = -1
            Stdout = @()
            Stderr = "(rg invocation threw) " + $_.Exception.Message
        }
    } finally {
        $ErrorActionPreference = $prevEAP
        Remove-Item -LiteralPath $errFile -ErrorAction SilentlyContinue
    }
}

# 从一行 rg -n 输出（`<path>:<lineno>:<text>`）提取 text。允许路径含 `:`（如 `C:\...`）。
function Get-RgLineBody {
    param([string]$RgLine)
    if (-not $RgLine) { return $null }
    # rg 默认 `<path>:<lineno>:<text>`；如果 path 含 `:`，仍按右数第二个 `:` 之后视为 body
    # 安全做法：找前两个非路径 `:`。
    $m = [regex]::Match($RgLine, '^(?<path>.+?):(?<lineno>\d+):(?<body>.*)$')
    if ($m.Success) { return $m.Groups['body'].Value }
    return $RgLine
}

# 单条候选行后处理。
# kind: "kv" 表示 `KEY=value` 风格；"longdigit" 表示纯长数字。
function Test-CandidateLooksReal {
    param(
        [string]$Body,
        [string]$Kind = "kv"
    )
    if (Test-LineLooksLikeDocOrComment -Line $Body) { return $false }
    if (Test-LineLooksLikeCodeReader -Line $Body) { return $false }
    if ($Kind -eq "longdigit") {
        $dm = [regex]::Match($Body, '(?<!\d)(\d{16,24})(?!\d)')
        if (-not $dm.Success) { return $false }
        if (Test-LongDigitFalsePositive -Line $Body -Digits $dm.Groups[1].Value) { return $false }
        return $true
    }
    # 提取 KEY = / : 后的 value （引号类只用 ASCII `"` `'` 和反引号；PowerShell 单引号字串里 `''` = 一个 `'`）
    $m = [regex]::Match(
        $Body,
        '(?i)(?<key>LOGIN_PWD|CERT_PWD|OA_PASS|BOC_USHIELD_PIN|CIB_LOGIN_PWD|password|secret|token|api[_-]?key)\s*[:=]\s*(?<q>["''`]?)(?<val>[^\r\n#`"'']+)'
    )
    if (-not $m.Success) { return $false }
    $val = $m.Groups['val'].Value.Trim()
    if ([string]::IsNullOrWhiteSpace($val)) { return $false }
    # 去掉行尾注释
    $val = ($val -split '#')[0].Trim()
    $val = ($val -split '//')[0].Trim()
    if (Test-IsPlaceholderValue -Value $val) { return $false }
    # `os.environ.get("KEY", "")` 类整行表达式：value 会以 `os.environ.get(...` 开头
    if ($val -match '^(?:os\.|process\.|env::|std::)') { return $false }
    return $true
}

function Test-SourceForSensitiveMarkers {
    param([string]$Root, [string]$Label, [AllowEmptyString()][string]$RgPath, [string[]]$ExtraNeedles)

    Write-Section "$Label :: 源码/文档敏感词扫描"
    if ([string]::IsNullOrWhiteSpace($RgPath)) {
        Add-Warning "$Label :: 未找到 rg，跳过敏感词扫描"
        return
    }
    if (-not (Test-Path -LiteralPath $Root)) {
        Add-Warning "$Label :: 根目录不存在 ($Root)，跳过"
        return
    }

    # 候选模式：用 rg 抓 candidate lines；然后用 PowerShell 后处理 value 是否真像 secret。
    # 第二个字段 Kind: kv = key=value 风格；longdigit = 纯长数字。
    # 注意 1：Windows native arg quoting 对 `"` 极不友好，正则里要 `"` / `'` 一律用 \x22 / \x27。
    # 注意 2：bundled rg 不一定支持 lookbehind/lookahead，长数字用 \b 边界足够（` "12..." ` 之类前后都是非 word 字符）。
    $needleSpecs = @(
        @{ Pattern = 'LOGIN_PWD\s*[:=]';                              Kind = "kv";        Label = "LOGIN_PWD" },
        @{ Pattern = 'CERT_PWD\s*[:=]';                               Kind = "kv";        Label = "CERT_PWD" },
        @{ Pattern = 'OA_PASS\s*[:=]';                                Kind = "kv";        Label = "OA_PASS" },
        @{ Pattern = 'BOC_USHIELD_PIN\s*[:=]';                        Kind = "kv";        Label = "BOC_USHIELD_PIN" },
        @{ Pattern = 'CIB_LOGIN_PWD\s*[:=]';                          Kind = "kv";        Label = "CIB_LOGIN_PWD" },
        @{ Pattern = '(?i)\bpassword\s*[:=]\s*[\x22\x27]';            Kind = "kv";        Label = "password=***REDACTED***" },
        @{ Pattern = '(?i)\bsecret\s*[:=]\s*[\x22\x27]';              Kind = "kv";        Label = "secret=***REDACTED***" },
        @{ Pattern = '(?i)\btoken\s*[:=]\s*[\x22\x27]';               Kind = "kv";        Label = "token=***REDACTED***" },
        @{ Pattern = '(?i)\bapi[_-]?key\s*[:=]\s*[\x22\x27]';         Kind = "kv";        Label = "api_key=***REDACTED***" },
        @{ Pattern = '\b\d{16,24}\b';                                  Kind = "longdigit"; Label = "16-24 digit run" }
    )

    # 额外 needles 来自本地 ignored 文件；不做 placeholder 推断（操作员自负）。
    $extraSpecs = @()
    foreach ($n in @($ExtraNeedles)) {
        if (-not $n) { continue }
        $t = $n.Trim()
        if (-not $t) { continue }
        if ($t.StartsWith("#")) { continue }
        $extraSpecs += @{ Pattern = $t; Kind = "kv"; Label = "extra:$t" }
    }

    # 排除：自身脚本、ignored 文件、运行产物目录、本地真实配置 *.local.json、
    # 银行项目 .env / 截图 / 长队列 JSON / 运行日志 / 网关 data 目录 等。
    $rgGlobExcludes = @(
        "!**/.git/**",
        "!**/__pycache__/**",
        "!**/node_modules/**",
        "!**/dist/**",
        "!**/build/**",
        "!**/runs/**",
        "!**/runtime/**",
        "!**/debug_runs/**",
        "!**/screenshots/**",
        "!**/page_screenshots/**",
        "!**/live_screenshots/**",
        "!**/artifacts/**",
        "!**/logs/**",
        "!**/data/**",
        "!**/reports/**",
        "!**/bank_forms/**",
        "!**/bank_batches/**",
        "!**/bank_runs/**",
        "!**/.browser_profile/**",
        "!**/.claude/**",
        "!**/.codex/**",
        "!**/.vscode/**",
        "!**/.idea/**",
        "!**/memory/**",
        "!**/*.env",
        "!**/.env",
        "!**/.env.*",
        "!**/*.local.json",
        "!**/screenshot*.png",
        "!**/screenshot*.jpg",
        "!**/latest*.json",
        "!**/transfer_batch*.json",
        "!**/screenshot_transfer_batch*.json",
        "!**/log.txt",
        "!**/*.log",
        "!**/gateway.*.log",
        "!**/gateway.out*.log",
        "!**/gateway.err*.log",
        "!**/run_sensitive_precommit_scan.ps1"
    )

    foreach ($spec in ($needleSpecs + $extraSpecs)) {
        $needle = $spec.Pattern
        $kind = $spec.Kind
        $needleLabel = $spec.Label
        # `--hidden` 是为了能扫到 `.env.example` 这类故意"反 ignore"的模板；
        # **不**再用 `-uu` —— 我们就是在做 pre-commit 扫描，要扫"会进入提交的文件"，
        # rg 默认会自然 respect `.gitignore`，让本地 runtime 产物（含 bank_form.json）跳过。
        # 网关目录没有 .gitignore，靠下面 $rgGlobExcludes 显式排掉 data/ logs/ node_modules/ 等。
        $rgArgs = @("--no-config", "-n", "--hidden", "-S", "--no-messages")
        foreach ($g in $rgGlobExcludes) { $rgArgs += @("-g", $g) }
        $rgArgs += @("-e", $needle, $Root)
        $r = Invoke-Rg -RgPath $RgPath -RgArgs $rgArgs
        if ($r.ExitCode -gt 1 -or $r.ExitCode -lt 0) {
            Add-Warning "$Label :: rg 异常退出 rc=$($r.ExitCode) for [$needleLabel]；stderr=$($r.Stderr.Trim())"
            continue
        }
        if ($r.ExitCode -eq 1) {
            Add-Pass "$Label :: 未匹配 [$needleLabel]"
            continue
        }
        # rc == 0：有候选；后处理判定是否真像泄露
        $real = New-Object System.Collections.Generic.List[string]
        $placeholderCount = 0
        foreach ($rawLine in $r.Stdout) {
            if (-not $rawLine) { continue }
            $line = [string]$rawLine
            $body = Get-RgLineBody -RgLine $line
            if (-not $body) { continue }
            $looksReal = Test-CandidateLooksReal -Body $body -Kind $kind
            if (-not $looksReal) {
                $placeholderCount++
                continue
            }
            $real.Add($line) | Out-Null
        }
        if ($real.Count -eq 0) {
            $candidateTotal = @($r.Stdout).Count
            Add-Pass "$Label :: [$needleLabel] 候选 $candidateTotal 条，全部判定为 placeholder / 代码读取 env / 文档示例，已放行"
        } else {
            $sample = ($real | Select-Object -First 3) -join " | "
            Add-Failure "$Label :: [$needleLabel] 疑似真实泄露 $($real.Count) 条；示例 -> $sample"
        }
    }
}

function Invoke-SelfTest {
    param([string]$RgPath)
    Write-Section "SelfTest :: 占位 / placeholder / fake-real 用例"
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("sensitive_scan_selftest_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null

    $cases = @(
        @{ Name = "doc_ellipsis";        Text = 'LOGIN_PWD=…';                                                              Expect = $false }
        @{ Name = "doc_cn_desc";         Text = 'LOGIN_PWD=网盾密码';                                                       Expect = $false }
        @{ Name = "doc_cn_desc2";        Text = 'CERT_PWD=登录页密码';                                                       Expect = $false }
        @{ Name = "doc_cn_pin_label";    Text = 'BOC_USHIELD_PIN=U盾PIN';                                                   Expect = $false }
        @{ Name = "code_env_reader";     Text = 'BOC_USHIELD_PIN = os.environ.get("BOC_USHIELD_PIN", "").strip()';            Expect = $false }
        @{ Name = "flag_password_true";  Text = '$env:ABC_OK_SERVO_AFTER_TRANSFER_KB_PASSWORD = "true"';                     Expect = $false }
        @{ Name = "flag_password_false"; Text = '$env:ABC_OK_SERVO_AFTER_KB_PASSWORD = "false"';                             Expect = $false }
        @{ Name = "md_inline";           Text = '| `LOGIN_PWD=…` | password 占位 |';                                          Expect = $false }
        @{ Name = "placeholder_xxx";     Text = 'token = "***REDACTED***"';                                                Expect = $false }
        @{ Name = "placeholder_var";     Text = 'token = "***REDACTED***"';                                                        Expect = $false }
        @{ Name = "leak_login_pwd";      Text = 'LOGIN_PWD=abc123456';                                                       Expect = $true }
        @{ Name = "leak_cert_pwd";       Text = 'CERT_PWD=real-secret';                                                      Expect = $true }
        @{ Name = "leak_oa_pass";        Text = 'OA_PASS=real-secret';                                                       Expect = $true }
        @{ Name = "leak_boc_pin";        Text = 'BOC_USHIELD_PIN=123456';                                                    Expect = $true }
        @{ Name = "leak_password";       Text = 'password = "***REDACTED***"';                                              Expect = $true }
        @{ Name = "leak_token";          Text = 'token = "***REDACTED***"';                                   Expect = $true }
        @{ Name = "leak_long_digit";     Text = '$account = "9876543210123456"';                                              Expect = $true }
        @{ Name = "doc_long_digit_zero"; Text = '$account = "00000000000000"';                                                Expect = $false }
    )
    $fails = New-Object System.Collections.Generic.List[string]
    foreach ($c in $cases) {
        $body = $c.Text
        $isKv = ($c.Name -notlike "*long_digit*")
        $kind = if ($isKv) { "kv" } else { "longdigit" }
        $verdict = Test-CandidateLooksReal -Body $body -Kind $kind
        $ok = ($verdict -eq $c.Expect)
        if ($ok) {
            Add-Pass "SelfTest :: $($c.Name) -> $verdict (期望 $($c.Expect))"
        } else {
            Add-Failure "SelfTest :: $($c.Name) -> $verdict (期望 $($c.Expect))；text=[$body]"
            $fails.Add($c.Name) | Out-Null
        }
    }
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    return ($fails.Count -eq 0)
}

# ---------- main ----------

Write-Section "环境"
$git = (Get-Command git -ErrorAction SilentlyContinue)
if ($null -eq $git) {
    Add-Failure "未找到 git，无法继续"
    Write-Host "FAILED (no git)"
    exit 2
}
Write-Host "git           : $($git.Source)"
$rgPath = Resolve-Ripgrep -Explicit $RgPath
if ($null -ne $rgPath) {
    Write-Host "rg            : $rgPath"
} else {
    Write-Host "rg            : not found (源码扫描会跳过)"
}
Write-Host "FinanceRoot   : $FinanceRoot"
Write-Host "M3Root        : $M3Root"
Write-Host "GatewayRoot   : $GatewayRoot"

$extraNeedles = @()
if ($ExtraNeedlesFile -and (Test-Path -LiteralPath $ExtraNeedlesFile)) {
    $extraNeedles = Get-Content -LiteralPath $ExtraNeedlesFile -Encoding UTF8
    Write-Host "Extra needles : $ExtraNeedlesFile ($($extraNeedles.Count) lines)"
} else {
    if ($ExtraNeedlesFile) {
        Add-Warning "ExtraNeedlesFile 不存在: $ExtraNeedlesFile"
    }
    Write-Host "Extra needles : (none)"
}

if ($SelfTest) {
    [void](Invoke-SelfTest -RgPath $rgPath)
}

foreach ($pair in @(
    @{ Root = $FinanceRoot;  Label = "财务"   },
    @{ Root = $M3Root;       Label = "M3"     },
    @{ Root = $GatewayRoot;  Label = "网关"   }
)) {
    if (-not (Test-Path -LiteralPath $pair.Root)) {
        Add-Warning "$($pair.Label) 根目录不存在: $($pair.Root)，跳过"
        continue
    }
    Test-GitignoreCoverage  -Root $pair.Root -Label $pair.Label
    Test-UntrackedSuspects  -Root $pair.Root -Label $pair.Label
    Test-SourceForSensitiveMarkers -Root $pair.Root -Label $pair.Label -RgPath $rgPath -ExtraNeedles $extraNeedles
}

Write-Section "汇总"
Write-Host "PASS=$($script:Passes.Count)  WARN=$($script:Warnings.Count)  FAIL=$($script:Failures.Count)"
if ($script:Warnings.Count -gt 0) {
    Write-Host ""
    Write-Host "WARN list:" -ForegroundColor Yellow
    foreach ($w in $script:Warnings) { Write-Host "  - $w" -ForegroundColor Yellow }
}
if ($script:Failures.Count -gt 0) {
    Write-Host ""
    Write-Host "FAIL list:" -ForegroundColor Red
    foreach ($f in $script:Failures) { Write-Host "  - $f" -ForegroundColor Red }
    exit 1
}
Write-Host "OK" -ForegroundColor Green
exit 0

