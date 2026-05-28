# 银行运行产物清理

这里放跨银行项目共用的截图、日志和运行产物清理工具。

## 安全回归检查

改代码后先跑快速、非破坏性检查：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\30112\Desktop\财务\公共\maintenance\run_safe_regression_checks.ps1"
```

这个脚本只做 Python 编译、网关 TypeScript typecheck、危险环境变量检查、Git 敏感产物检查和可用时的 M3 只读核验；不会打开银行客户端、不会操作 USB Hub、不会触发 U盾或制单。

需要额外检查 M3 银行队列路由时，可加 `-RunM3DryRun`。它只 dry-run 打印命令和校验载荷，不启动银行程序：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\30112\Desktop\财务\公共\maintenance\run_safe_regression_checks.ps1" -RunM3DryRun
```

如果当前 `runtime\bank_batches` 是旧队列或缺少 M3 列表/详情截图，`-RunM3DryRun` 会按 fail-closed 规则失败；这是队列契约问题，不会打开银行或提交。

## 先预览

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\30112\Desktop\财务\公共\maintenance\cleanup_bank_artifacts.ps1"
```

不带 `-Apply` 时只列出计划，不删除文件。

## 立即清理

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\30112\Desktop\财务\公共\maintenance\cleanup_bank_artifacts.ps1" -Apply
```

## 自动化收尾清理

各银行 `.bat` / `.ps1` 启动器跑完后调用：

```text
C:\Users\30112\Desktop\财务\公共\maintenance\cleanup_after_automation.bat
```

这个 helper 会执行 `cleanup_bank_artifacts.ps1 -Apply`，但自己始终返回 0。启动器会先保存银行流程原始退出码，清理后再返回原始退出码，避免清理结果影响网关上报或计划任务判断。

默认保留策略：

- 农行/中行等高频 `debug_runs`：最近 10 个，或 30 天内；超过任一条件才进入清理候选。
- 余额查询 `runs\balance_*`：最近 30 个，或 90 天内。
- 招行制单批次：调用项目自带清理脚本，默认保留最近 30 批、30 天内批次，归档超过 90 天后清除。
- 兴业制单：调用项目自带 `maintain_workspace.py`，再按统一规则清理归档。
- 新生成 6 小时内的目录/文件会跳过，避免误删正在运行的产物。

脚本只触碰已知的运行产物目录；执行递归删除前会校验路径必须位于 `C:\Users\30112\Desktop\财务` 内，且拒绝处理 reparse point。

## 安装每日计划任务

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\30112\Desktop\财务\公共\maintenance\install_cleanup_scheduled_task.ps1"
```

默认每天 `03:30` 执行一次 `cleanup_bank_artifacts.ps1 -Apply`。

调整时间示例：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\30112\Desktop\财务\公共\maintenance\install_cleanup_scheduled_task.ps1" -At 04:15
```

删除计划任务：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\30112\Desktop\财务\公共\maintenance\install_cleanup_scheduled_task.ps1" -Unregister
```
