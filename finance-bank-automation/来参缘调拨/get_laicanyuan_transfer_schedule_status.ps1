# get_laicanyuan_transfer_schedule_status.ps1
#
# 查询"财务-来参缘调拨-每日10点16点"Windows 计划任务的状态。
#   - 任务是否存在
#   - 当前状态 (Ready / Running / Disabled / ...)
#   - 上次运行时间 / 上次结果 / 下次运行时间
#   - 触发器列表
# 不启动、不修改、不删除任务。

[CmdletBinding()]
param(
    [string]$TaskName = '财务-来参缘调拨-每日10点16点'
)

$ErrorActionPreference = 'Stop'

Write-Host '== 来参缘调拨 计划任务状态 =='
Write-Host ("任务名称: {0}" -f $TaskName)

$task = $null
try {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
} catch {
    Write-Host ("[结果] 任务不存在或查询失败: {0}" -f $_.Exception.Message)
    exit 1
}

Write-Host ('任务路径: {0}' -f $task.TaskPath)
Write-Host ('当前状态: {0}' -f $task.State)
Write-Host ('描述    : {0}' -f $task.Description)
Write-Host ''

Write-Host '== 触发器 =='
$i = 0
foreach ($trigger in $task.Triggers) {
    $i++
    $startBoundary = if ($trigger.StartBoundary) { $trigger.StartBoundary } else { '(未设置)' }
    Write-Host ("  #{0} 类型: {1}" -f $i, $trigger.CimClass.CimClassName)
    Write-Host ("      StartBoundary: {0}" -f $startBoundary)
    Write-Host ("      Enabled      : {0}" -f $trigger.Enabled)
}
Write-Host ''

Write-Host '== 动作 =='
$j = 0
foreach ($action in $task.Actions) {
    $j++
    Write-Host ("  #{0} Execute  : {1}" -f $j, $action.Execute)
    Write-Host ("      Argument : {0}" -f $action.Arguments)
    if ($action.WorkingDirectory) {
        Write-Host ("      WorkingDir: {0}" -f $action.WorkingDirectory)
    }
}
Write-Host ''

Write-Host '== Principal =='
Write-Host ("  UserId   : {0}" -f $task.Principal.UserId)
Write-Host ("  LogonType: {0}" -f $task.Principal.LogonType)
Write-Host ("  RunLevel : {0}" -f $task.Principal.RunLevel)
Write-Host ''

Write-Host '== Settings 关键项 =='
Write-Host ("  StartWhenAvailable : {0}" -f $task.Settings.StartWhenAvailable)
Write-Host ("  MultipleInstances  : {0}" -f $task.Settings.MultipleInstances)
Write-Host ("  ExecutionTimeLimit : {0}" -f $task.Settings.ExecutionTimeLimit)
Write-Host ''

try {
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction Stop
    Write-Host '== 运行信息 =='
    Write-Host ("  LastRunTime  : {0}" -f $info.LastRunTime)
    $lastResult = $info.LastTaskResult
    $hexResult = '0x{0:X8}' -f ($lastResult -band 0xFFFFFFFF)
    Write-Host ("  LastResult   : {0} ({1})" -f $lastResult, $hexResult)
    Write-Host ("  NextRunTime  : {0}" -f $info.NextRunTime)
    Write-Host ("  NumberOfMissedRuns: {0}" -f $info.NumberOfMissedRuns)
} catch {
    Write-Warning ("Get-ScheduledTaskInfo 失败: {0}" -f $_.Exception.Message)
}

Write-Host ''
Write-Host '说明:'
Write-Host '  - 本脚本只读，不会调用 Start-ScheduledTask / Stop-ScheduledTask / Unregister-ScheduledTask。'
Write-Host '  - LastResult 含义示例: 0=成功; 27=本地单实例锁占用; 28=全局银行自动化锁忙(被 M3 占用);'
Write-Host '    20-26=runner fail-closed 业务错误（见 SKILL.md 中"Fail-closed 触发条件"）;'
Write-Host '    其它见 Windows 任务计划"上次运行结果"含义和 runner 日志。'

exit 0
