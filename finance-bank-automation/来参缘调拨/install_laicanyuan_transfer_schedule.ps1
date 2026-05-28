# install_laicanyuan_transfer_schedule.ps1
#
# 安装"财务-来参缘调拨-每日10点16点"Windows 计划任务。
# 默认 dry-run，只打印将创建的任务定义；必须传 -Apply 才真的注册任务。
# 如果同名任务已存在，默认 fail-closed（不覆盖、不重启已有任务）；如要强制
# 覆盖请同时传 -Replace。
#
# 边界:
#   - 任务以"当前 Windows 用户"身份运行（LogonType=Interactive，必须用户登录、
#     桌面未锁定、UKey 可用）。默认优先 RunLevel=Highest；如果当前 PowerShell
#     没有权限注册 Highest 任务且错误为 Access denied，会自动降级为
#     RunLevel=Limited 并在安装日志 / 状态脚本里显示。
#   - 不使用 -StartWhenAvailable，避免错过 10:00 后在不可控时间补跑。
#   - MultipleInstances=IgnoreNew：单一实例，绝不并发。
#   - 不安装"无论是否登录都运行"模式：银行 GUI 自动化必须可见桌面。
#   - 不在源码里硬编码 Windows 密码 / U盾 PIN。
#
# 用法:
#   powershell -NoProfile -ExecutionPolicy Bypass -File `
#       "C:\Users\30112\Desktop\财务\来参缘调拨\install_laicanyuan_transfer_schedule.ps1"
#   # 上面默认 dry-run，只打印任务定义和将执行的注册命令；不会真的注册。
#
#   # 真正注册：
#   powershell -NoProfile -ExecutionPolicy Bypass -File `
#       "...\install_laicanyuan_transfer_schedule.ps1" -Apply
#
#   # 同名任务存在时覆盖（先 unregister 再 register）：
#   powershell -NoProfile -ExecutionPolicy Bypass -File `
#       "...\install_laicanyuan_transfer_schedule.ps1" -Apply -Replace

[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$Replace,
    [switch]$NoLimitedFallback,
    [string]$TaskName = '财务-来参缘调拨-每日10点16点'
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Wrapper = Join-Path $ScriptDir 'run_laicanyuan_transfer_scheduled.ps1'
if (-not (Test-Path -LiteralPath $Wrapper)) {
    Write-Error "未找到 wrapper: $Wrapper"
    exit 2
}

# Windows 用户名按 DOMAIN\User 形式取，Interactive 登录需要该字符串。
$UserId = "$env:USERDOMAIN\$env:USERNAME"

Write-Host '== 来参缘调拨 计划任务安装脚本 =='
Write-Host ("脚本目录: {0}" -f $ScriptDir)
Write-Host ("Wrapper : {0}" -f $Wrapper)
Write-Host ("任务名称: {0}" -f $TaskName)
Write-Host ("运行身份: {0} (LogonType=Interactive, RunLevel=Highest preferred)" -f $UserId)
Write-Host '触发器  : 每日 10:00 和 16:00'
Write-Host '说明    : 不使用 StartWhenAvailable / RunOnlyIfLoggedOn=false；超时上限 1 小时；MultipleInstances=IgnoreNew'
Write-Host ''

if (-not $Apply) {
    Write-Host '[dry-run] 未传 -Apply，仅打印任务定义。不会调用 Register-ScheduledTask。'
}

# 同名任务存在？
$existing = $null
try {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
} catch {
    $existing = $null
}

if ($null -ne $existing) {
    if ($Replace) {
        Write-Host "[警告] 同名任务已存在: $TaskName"
        if ($Apply) {
            Write-Host '[Apply -Replace] 将先注销旧任务再注册新任务...'
            try {
                Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
                Write-Host "[Apply -Replace] 已注销旧任务: $TaskName"
            } catch {
                Write-Error ("[Apply -Replace] 注销旧任务失败: {0}" -f $_.Exception.Message)
                exit 3
            }
        } else {
            Write-Host '[dry-run -Replace] 真正运行 (-Apply) 时会先 Unregister 旧任务再注册新任务。'
        }
    } else {
        Write-Host "[FAIL-CLOSED] 同名任务已存在: $TaskName"
        Write-Host '            默认拒绝覆盖。若确需覆盖，请加 -Replace 重新运行；不会自动覆盖。'
        if ($Apply) {
            exit 4
        }
    }
}

# Action: powershell.exe -NoProfile -ExecutionPolicy Bypass -File "...\run_laicanyuan_transfer_scheduled.ps1"
$ActionExe = 'powershell.exe'
$ActionArgs = '-NoProfile -ExecutionPolicy Bypass -File "' + $Wrapper + '"'
$action = New-ScheduledTaskAction -Execute $ActionExe -Argument $ActionArgs -WorkingDirectory $ScriptDir

$trigger10 = New-ScheduledTaskTrigger -Daily -At '10:00'
$trigger16 = New-ScheduledTaskTrigger -Daily -At '16:00'
$triggers = @($trigger10, $trigger16)

function New-LaicanyuanPrincipal {
    param(
        [ValidateSet('Highest', 'Limited')]
        [string]$RunLevel
    )
    return New-ScheduledTaskPrincipal `
        -UserId $UserId `
        -LogonType Interactive `
        -RunLevel $RunLevel
}

$principal = New-LaicanyuanPrincipal -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable:$false `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew `
    -RestartCount 0

Write-Host '== 任务定义 =='
Write-Host ("Action.Execute  : {0}" -f $ActionExe)
Write-Host ("Action.Argument : {0}" -f $ActionArgs)
Write-Host ("Action.WorkingDir: {0}" -f $ScriptDir)
Write-Host ('Trigger 1       : Daily 10:00')
Write-Host ('Trigger 2       : Daily 16:00')
Write-Host ("Principal.UserId: {0}" -f $UserId)
Write-Host 'Principal.LogonType : Interactive  (需用户已登录、桌面可见)'
Write-Host 'Principal.RunLevel  : Highest preferred; Access denied 时自动降级为 Limited（除非传 -NoLimitedFallback）'
Write-Host 'Settings.StartWhenAvailable : False  (不补跑漏掉的触发，避免不可控时间点干活)'
Write-Host 'Settings.MultipleInstances  : IgnoreNew  (前一次没跑完则跳过下一次，避免并发)'
Write-Host 'Settings.ExecutionTimeLimit : 01:00:00'
Write-Host ''

if (-not $Apply) {
    Write-Host '[dry-run] 不执行 Register-ScheduledTask；如需安装请加 -Apply。'
    exit 0
}

function Register-LaicanyuanTask {
    param(
        [Parameter(Mandatory=$true)]
        $Principal,
        [Parameter(Mandatory=$true)]
        [string]$RunLevelLabel
    )
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $triggers `
        -Principal $Principal `
        -Settings $settings `
        -Description '财务-来参缘调拨 每日 10:00/16:00 自动制单（仅经办进入待审核队列，不复核/授权/付款）' `
        -Force | Out-Null
    Write-Host ("[Apply] 已注册任务: {0} (RunLevel={1})" -f $TaskName, $RunLevelLabel)
}

Write-Host '[Apply] 正在调用 Register-ScheduledTask (RunLevel=Highest) ...'
try {
    Register-LaicanyuanTask -Principal $principal -RunLevelLabel 'Highest'
} catch {
    $firstError = $_.Exception.Message
    $isAccessDenied = ($firstError -match 'Access is denied|拒绝访问|0x80070005')
    if ($NoLimitedFallback -or -not $isAccessDenied) {
        Write-Error ("[Apply] Register-ScheduledTask 失败: {0}" -f $firstError)
        exit 5
    }

    Write-Warning ("[Apply] RunLevel=Highest 注册失败（{0}）。当前 PowerShell 可能未提升权限，改用 RunLevel=Limited 注册交互式当前用户任务。" -f $firstError)
    Write-Warning '        Limited 仍要求用户已登录、桌面可见；若银行客户端/USBHub 后续需要管理员权限，请用管理员 PowerShell 重新运行本脚本。'
    $limitedPrincipal = New-LaicanyuanPrincipal -RunLevel Limited
    try {
        Register-LaicanyuanTask -Principal $limitedPrincipal -RunLevelLabel 'Limited'
    } catch {
        Write-Error ("[Apply] RunLevel=Limited 注册仍失败: {0}" -f $_.Exception.Message)
        exit 5
    }
}

# 安装后立即查询确认，不启动任务。
try {
    $created = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    Write-Host ('已安装任务状态: {0}' -f $created.State)
    Write-Host '说明: 本脚本只注册任务，不会调用 Start-ScheduledTask；下次触发由 Windows 任务计划自然执行。'
} catch {
    Write-Warning ("[Apply] 注册后查询任务失败（但 Register 已返回成功）: {0}" -f $_.Exception.Message)
}

exit 0
