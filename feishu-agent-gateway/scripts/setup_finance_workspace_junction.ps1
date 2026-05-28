# scripts/setup_finance_workspace_junction.ps1
#
# 维护脚本：在英文路径 C:\Users\30112\Desktop\finance_workspace 上创建一个指向
# 中文真实目录 C:\Users\30112\Desktop\财务 的 NTFS 目录 junction。
#
# 用于解决「飞书 -> 网关 -> Codex CLI」链路里中文路径被 Codex shell 误编码成乱码
# 的问题：网关 DEFAULT_PROJECT_PATH 用英文别名，Codex 在英文路径下工作，落地仍是
# 同一个真实目录。
#
# fail-closed 原则：
#   1. 目标 C:\Users\30112\Desktop\财务 必须真实存在。
#   2. 如果 finance_workspace 不存在，创建一个 junction 指向 财务。
#   3. 如果 finance_workspace 已经存在并且就是指向 财务 的 junction，直接成功。
#   4. 如果 finance_workspace 存在但**不是**一个 junction，或者是 junction 但
#      指向别处，**绝对不覆盖、不删除**，以非零退出码失败，让运维人工介入。
#   5. 该脚本只创建/读取目录元数据，不复制任何文件，不移动真实项目。
#
# 退出码：
#   0 - 成功（已新建 junction，或 junction 已就位且指向正确）
#   2 - 目标 财务 目录不存在
#   3 - finance_workspace 已存在但不是 junction
#   4 - finance_workspace 已存在并指向另一个目标，需要人工介入
#   5 - 创建 junction 时 cmd /c mklink 返回非 0 退出码
#   6 - 参数路径不合法

[CmdletBinding()]
param(
    [string]$LinkPath = "C:\Users\30112\Desktop\finance_workspace",
    [string]$TargetPath = "C:\Users\30112\Desktop\财务"
)

$ErrorActionPreference = "Stop"

function Write-Info($msg) {
    Write-Host "[setup_finance_workspace_junction] $msg"
}

function Write-Fail($msg) {
    Write-Host "[setup_finance_workspace_junction][FAIL] $msg"
}

if ([string]::IsNullOrWhiteSpace($LinkPath) -or [string]::IsNullOrWhiteSpace($TargetPath)) {
    Write-Fail "LinkPath / TargetPath 不能为空"
    exit 6
}

try {
    $LinkPath   = [System.IO.Path]::GetFullPath($LinkPath)
    $TargetPath = [System.IO.Path]::GetFullPath($TargetPath)
} catch {
    Write-Fail "无法解析为绝对路径: $($_.Exception.Message)"
    exit 6
}

Write-Info "LinkPath   = $LinkPath"
Write-Info "TargetPath = $TargetPath"

if (-not (Test-Path -LiteralPath $TargetPath -PathType Container)) {
    Write-Fail "目标真实目录不存在或不是目录: $TargetPath"
    Write-Fail "请确认 财务 项目目录是否在桌面上，再重新运行本脚本。"
    exit 2
}

function Get-JunctionTarget([string]$path) {
    try {
        $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
    } catch {
        return $null
    }
    $isReparse = ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
    if (-not $isReparse) {
        return [pscustomobject]@{ IsLink = $false; LinkType = $null; Target = $null; Item = $item }
    }
    $linkType = $null
    $target = $null
    try { $linkType = $item.LinkType } catch {}
    try {
        $rawTarget = $item.Target
        if ($rawTarget) {
            if ($rawTarget -is [System.Array]) {
                $target = [string]$rawTarget[0]
            } else {
                $target = [string]$rawTarget
            }
        }
    } catch {}
    return [pscustomobject]@{ IsLink = $true; LinkType = $linkType; Target = $target; Item = $item }
}

function Resolve-Compare([string]$a, [string]$b) {
    if ([string]::IsNullOrWhiteSpace($a) -or [string]::IsNullOrWhiteSpace($b)) { return $false }
    try {
        $na = [System.IO.Path]::GetFullPath($a).TrimEnd('\','/').ToLowerInvariant()
        $nb = [System.IO.Path]::GetFullPath($b).TrimEnd('\','/').ToLowerInvariant()
        return $na -eq $nb
    } catch {
        return $false
    }
}

if (Test-Path -LiteralPath $LinkPath) {
    $info = Get-JunctionTarget $LinkPath
    if ($null -eq $info) {
        Write-Fail "无法读取 LinkPath 元数据，请用管理员账号检查权限：$LinkPath"
        exit 3
    }
    if (-not $info.IsLink) {
        Write-Fail "LinkPath 已存在但不是 junction/symlink：$LinkPath"
        Write-Fail "为防止覆盖真实文件，脚本不会自动删除。如确实是空目录，请人工核对后手动 rmdir，然后重跑本脚本。"
        exit 3
    }
    Write-Info "已检测到 reparse point，LinkType=$($info.LinkType) Target=$($info.Target)"
    if (Resolve-Compare $info.Target $TargetPath) {
        Write-Info "junction 已就位并指向正确目标，无需变更。"
        exit 0
    }
    Write-Fail "junction 已存在但指向其它目标：$($info.Target)"
    Write-Fail "脚本拒绝自动改写。请人工确认后用 rmdir 删除该 junction（不会删除被指向的真实目录），再重跑本脚本。"
    exit 4
}

# 不存在 -> 用 cmd /c mklink /J 创建。不使用 New-Item -ItemType Junction，因为它在某些 PS 版本上有差异。
Write-Info "LinkPath 不存在，准备创建 junction ..."
$mklinkArgs = @("/c", "mklink", "/J", "`"$LinkPath`"", "`"$TargetPath`"")
$proc = Start-Process -FilePath "cmd.exe" -ArgumentList $mklinkArgs -NoNewWindow -Wait -PassThru
if ($proc.ExitCode -ne 0) {
    Write-Fail "cmd /c mklink /J 退出码 $($proc.ExitCode)，junction 未创建"
    exit 5
}

$info = Get-JunctionTarget $LinkPath
if ($null -eq $info -or -not $info.IsLink -or -not (Resolve-Compare $info.Target $TargetPath)) {
    Write-Fail "mklink 返回成功但 junction 校验未通过：IsLink=$($info.IsLink) Target=$($info.Target)"
    exit 5
}

Write-Info "junction 创建成功：$LinkPath  ->  $TargetPath"
Write-Info "你现在可以把网关 DEFAULT_PROJECT_PATH 改成英文别名："
Write-Info "  DEFAULT_PROJECT_PATH=$LinkPath"
exit 0
