---
name: zhaohang-zhidan
description: Use this skill when the user says 招行制单, 真实制单, 生产经办, CMB U-BANK, or asks Codex to run China Merchants Bank single-transfer order creation from provided transfer fields. It writes bank_form.json, runs the real ZHIDAN production flow, matches the UKey by payer name, and reviews logs/screenshots after execution.
---

# 招行制单

> 财务项目总览优先看 `C:\Users\30112\Desktop\财务\银行自动化项目总览.md`；迁移期旧总览在 `C:\Users\30112\Desktop\银行自动化项目总览.md`。如果本项目的用途、入口、生产/测试模式、安全边界、USBHub/UKey 匹配规则、日志/截图路径发生变化，必须同步更新总览。

## Use Case

Use this skill for real China Merchants Bank U-BANK single-transfer order creation. The user should be able to start a new conversation, say "招行制单", provide the transfer fields, and have Codex prepare `bank_form.json`, run the production flow, then inspect logs and screenshots.

This skill opts into the real production path by explicitly setting `ZHIDAN_TEST_MODE=0`. The underlying entrypoint defaults to test mode, so production intent must be visible in the command environment. Do not use the batch screenshot runner for production because it forces a USB Hub port. Do not force USB Hub port 3. The UKey must be selected by matching `付款单位名称`.

## Required Input

Require these fields before running. If any field is missing, ask only for the missing field and stop.

```json
{
  "付款单位名称": "<付款单位名称>",
  "收方账号": "<收方银行账号>",
  "收方户名": "<收款单位名称>",
  "开户银行": "<开户银行>",
  "支行名称": "<支行名称/联行号>",
  "金额": "<金额，例如：83658.11>",
  "用途": "货款",
  "业务参考号": "<业务参考号/单据编号>"
}
```

If `用途` is omitted, use `货款`. Do not invent or normalize account, amount, bank, branch, or payer information beyond safe JSON formatting.

## Important Paths

- Project root: `C:\Users\30112\Desktop\财务\招行\招行制单\招行`
- Transfer JSON: `C:\Users\30112\Desktop\财务\招行\招行制单\M3直供合同付款数据获取\bank_form.json`
- Real single-transfer entrypoint: `C:\Users\30112\Desktop\财务\招行\招行制单\招行\skills\制单单账号单笔转账skill\制单.py`
- Screenshots and run artifacts: `C:\Users\30112\Desktop\财务\招行\招行制单\招行\screenshots`
- Do not use for production: `C:\Users\30112\Desktop\财务\招行\招行制单\run_batch_from_screenshots.ps1`
- M3 latest-payment test automation runner: `C:\Users\30112\Desktop\财务\招行\招行制单\m3_latest30_cmb_test_runner.py`。这是独立测试自动化程序，不是生产招行制单入口；程序/计划任务可调用 `run_m3_latest30_带Codex上报.bat`，通过 `C:\Users\30112\Desktop\网关` 包装运行并按退出码上报。该 runner 会读取 `C:\Users\30112\Desktop\M3直供合同付款数据获取`，把已知地方/城商行归一到 U-BANK 的“城市商业银行”等银行大类，并记录原始银行名与自动修正字段。
- Artifact cleanup: `C:\Users\30112\Desktop\财务\招行\招行制单\cleanup_generated_artifacts.ps1`

## Workflow

1. Validate that all required fields are present.
2. Write exactly one transfer record to `bank_form.json` as UTF-8 JSON with the required keys.
3. Run syntax checks for the real-flow modules before launching U-BANK:

```powershell
python -m py_compile "C:\Users\30112\Desktop\财务\招行\招行制单\招行\skills\制单单账号单笔转账skill\zhidan_bank.py"
python -m py_compile "C:\Users\30112\Desktop\财务\招行\招行制单\招行\skills\制单单账号单笔转账skill\zhidan_form.py"
python -m py_compile "C:\Users\30112\Desktop\财务\招行\招行制单\招行\skills\制单单账号单笔转账skill\zhidan_input.py"
python -m py_compile "C:\Users\30112\Desktop\财务\招行\招行制单\招行\skills\制单单账号单笔转账skill\制单.py"
```

4. Run the production flow only after the data is written and syntax checks pass. Request shell escalation when needed because this opens U-BANK, switches USB, and submits the real order.

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
$env:ZHIDAN_TEST_MODE='0'
Remove-Item Env:USB_HUB_FORCE_PORT -ErrorAction SilentlyContinue
Remove-Item Env:ZHIDAN_ALLOW_FORCE_USB_PORT -ErrorAction SilentlyContinue
python "C:\Users\30112\Desktop\财务\招行\招行制单\招行\skills\制单单账号单笔转账skill\制单.py"
```

5. Let `zhidan_usb_hub.py` match the USB Hub/UKey from `付款单位名称`. If the payer cannot be mapped or USB switching fails, stop and report the failure. Never choose a random UKey.
6. After the run, inspect the newest screenshots, stdout/stderr, and log files. Report the final result with the latest screenshot path/folder and any warnings.
7. If the run produced loose diagnostic artifacts, use the cleanup script in dry-run mode first, then apply only if the plan is clearly limited to generated artifacts.

## Success Checks

After every real run, verify and summarize:

- `ZHIDAN_TEST_MODE=0` was used.
- No `USB_HUB_FORCE_PORT` or `ZHIDAN_ALLOW_FORCE_USB_PORT` override was active.
- The log shows the payer matched a concrete USB Hub port.
- The first and second 经办 actions completed if the run succeeded.
- U-BANK was closed and USB Hub was powered off, or clearly state if this could not be confirmed.
- Any `[fail-closed] 支行下拉无精确或安全等价匹配`, `[支行探测]`, or `[开户银行兜底]` messages, with the relevant screenshot paths for manual review.
- No unexpected retry happened after a submit click. A real production submission must never be blindly repeated after the first or second `经办` might have started.

## Safety Rules

- Treat this as real funds/order submission. Fail closed on ambiguous input, unmapped payer, invalid amount, or unexpected U-BANK state.
- Do not run in test mode unless the user explicitly asks for a test. This skill is for the real workflow.
- Do not run the batch screenshot script for production. It is a regression-test tool that sets `USB_HUB_FORCE_PORT` and `ZHIDAN_ALLOW_FORCE_USB_PORT=1` internally.
- Do not run the M3 test batch runner for production. It is limited to `ZHIDAN_TEST_MODE=1`, amount `0.01`, artifact collection, and explicitly allowed forced USB-port test runs.
- Do not set or preserve forced USB-port environment variables.
- Do not log passwords, UKey PINs, or other credentials.
- Branch dropdown first-row fallback is forbidden. For M3 data, normalize `开户银行` to the bank head (for example `中国银行`) and keep the full branch name in `支行名称/联行号`. First type the full branch name and inspect the dropdown. Candidate ordering uses the shared `公共\bank_branch_matcher.py` scorer, but the allow/deny boundary stays local: prefer exact or verified safe-equivalent options. If those are missing, the only allowed fallback is still inside the same branch input/dropdown: type data-derived route terms by行政区 strength (区县, then 市, then 省), and select only one unique higher-level same-bank candidate from that dropdown. When this region-level route is used, the script must notify through the Codex gateway before any `经办` click; notification failure aborts the run. In real submit mode (`ZHIDAN_TEST_MODE=0`), the notification must also send the M3 contract-payment list screenshot and payment-detail screenshot through Feishu; missing either screenshot aborts before `经办`. Do not directly fill the outer `开户地址` province/city as a silent fallback, do not click `查询支行`, and do not choose the first visible candidate. Use `ZHIDAN_BRANCH_PROBE=1` only to inspect candidate dropdown items and stop. Do not add one-off city-specific `if/else` rules or chained suffix/keyword guesses.

## Test-Only 10 Round Runner

When the user explicitly asks for a test such as "重新测试10轮，端口3", use:

```powershell
C:\Users\30112\Desktop\财务\招行\招行制单\run_batch_from_screenshots.ps1
```

Rules for that path:

- It is test mode by default and stops after the first `经办` screenshot.
- It forces USBHub port 3 by default for the current fixed test setup.
- It writes one `batch_yyyyMMdd_HHmmss` folder under `招行\screenshots`.
- Per-item screenshots live in `NN_screenshots`; logs and result JSON live directly in the batch folder.
- It may retry one item once for main-window startup failure or U-BANK crash before submit. In production mode it must not retry after submit has started.
- After the run, inspect `summary.json`, `*.err.log`, per-item logs, and final screenshots before reporting.

## Artifact Hygiene

Before or after repeated tests, use:

```powershell
C:\Users\30112\Desktop\财务\招行\招行制单\cleanup_generated_artifacts.ps1
```

This is a dry-run. Apply only after checking the plan:

```powershell
C:\Users\30112\Desktop\财务\招行\招行制单\cleanup_generated_artifacts.ps1 -Apply
```

The cleanup script consolidates legacy loose screenshots into batch subfolders, archives unindexed screenshots/logs into `_archive`, removes `__pycache__`, and keeps recent batches according to the timestamp in the `batch_yyyyMMdd_HHmmss` name. Defaults keep at least 30 days or the newest 100 batches, and purge archived generated files after 90 days. `制单.py` runs this cleanup automatically after standalone runs; the M3 latest-30 runner runs it once after the whole batch.

The project launchers also call `C:\Users\30112\Desktop\财务\公共\maintenance\cleanup_after_automation.bat` after each automation run. That helper preserves the original bank-flow exit code and then applies cross-project artifact pruning with a 6-hour new-file protection window.

## New Conversation Prompt

The user can start a new conversation with:

```text
用招行制单跑真实流程，信息如下：
{
  "付款单位名称": "...",
  "收方账号": "...",
  "收方户名": "...",
  "开户银行": "...",
  "支行名称": "...",
  "金额": "...",
  "用途": "货款",
  "业务参考号": "..."
}
```

For the regression-test path, the user can start with:

```text
重新测试10轮，端口3。跑完看 summary、err log、每条截图；如果 U-BANK 启动失败或崩溃，按脚本规则自动重跑该条一次。
```
