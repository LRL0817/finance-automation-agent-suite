# uninstall_laicanyuan_transfer_schedule.ps1
#
# 删除"财务-来参缘调拨-每日10点16点"Windows 计划任务。
# 默认 dry-run，只打印任务存在性与将执行的删除命令；必须传 -Apply 才真正
# 调用 Unregister-ScheduledTask。
#
# 用法:
#   powershell -NoProfile -ExecutionPolicy Bypass -File `
#       "C:\Users\30112\Desktop\财务\来参缘调拨\uninstall_laicanyuan_transfer_schedule.ps1"
#   # 默认 dry-run。
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File `
#       "...\uninstall_laicanyuan_transfer_schedule.ps1" -Apply
#   # 真正删除任务。

[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$TaskName = '财务-来参缘调拨-每日10点16点'
)

$ErrorActionPreference = 'Stop'

Write-Host '== 来参缘调拨 计划任务卸载脚本 =='
Write-Host ("任务名称: {0}" -f $TaskName)

$existing = $null
try {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
} catch {
    $existing = $null
}

if ($null -eq $existing) {
    Write-Host "[noop] 任务不存在: $TaskName"
    exit 0
}

Write-Host ('任务当前状态: {0}' -f $existing.State)
Write-Host ('任务路径    : {0}' -f $existing.TaskPath)
Write-Host '说明: 本脚本只调用 Unregister-ScheduledTask；不会 Stop-ScheduledTask，'
Write-Host '      不会清理 runs / runtime / 截图 / 锁文件等运行产物。'

if (-not $Apply) {
    Write-Host ''
    Write-Host '[dry-run] 未传 -Apply，仅打印将执行的删除命令。'
    Write-Host ('[dry-run] 真正卸载请加 -Apply: Unregister-ScheduledTask -TaskName "{0}" -Confirm:$false' -f $TaskName)
    exit 0
}

try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Write-Host "[Apply] 已删除任务: $TaskName"
} catch {
    Write-Error ("[Apply] 删除任务失败: {0}" -f $_.Exception.Message)
    exit 3
}

exit 0
