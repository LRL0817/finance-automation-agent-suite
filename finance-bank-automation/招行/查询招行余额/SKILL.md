---
name: query-cmb-balance
description: Query China Merchants Bank CMB U-BANK corporate account balance from the Windows GUI. Use when Codex needs to log in to 招行 U-BANK, read the current company name and RMB balance/total assets, handle 首页 and 工作台 layouts, or build/run the 查询招行余额 workflow.
---

# 查询招行余额

> 财务项目总览优先看 `C:\Users\30112\Desktop\财务\银行自动化项目总览.md`；迁移期旧总览在 `C:\Users\30112\Desktop\银行自动化项目总览.md`。如果本项目的用途、入口、输出、安全边界或 USBHub/登录规则发生变化，必须同步更新总览。

Use this skill to read the visible company and RMB balance from CMB U-BANK. This workflow is read-only: it may open/login U-BANK and click safe navigation tabs such as `首页` or `工作台`, but it must not enter transfer/order pages, fill forms, or click `经办`.

## Entry Point

Run:

```powershell
C:\Users\30112\Desktop\财务\招行\查询招行余额\run_query_balance.bat
```

Or:

```powershell
python C:\Users\30112\Desktop\财务\招行\查询招行余额\scripts\query_balance.py
```

The script first tries to reuse an already logged-in U-BANK window. If no main window is present, it opens U-BANK and calls the existing login helpers from:

```text
C:\Users\30112\Desktop\财务\招行\招行制单\招行\ubank_common.py
```

## What To Read

Collect UIA visible text from the main U-BANK window and detect one of two known layouts:

- 首页 layout: label `人民币账户实时余额合计(元)` with the amount directly below it.
- 工作台 layout: label `人民币总资产(元)` with the amount directly below it.

Extract:

- `company`: first visible text matching `有限公司`.
- `balance`: nearest amount below the detected balance label.
- `page_type`: `home` or `workbench`.
- `label`: the label used for extraction.
- optional `account_count`: 首页 has `账户总数(个)` with a count below it.

Do not use OCR unless UIA text is unavailable. Current U-BANK V12.0.0.6 exposes the needed text through UIA.

## Output

Each run creates:

```text
C:\Users\30112\Desktop\财务\招行\查询招行余额\runs\balance_yyyyMMdd_HHmmss\
```

Files:

- `balance.json`: normalized result, matched text controls, and timestamp.
- `screen.png`: screenshot of the U-BANK window when available.

The command also prints a concise summary to stdout.

## Safety Rules

- Read-only only. Never click transfer/payment/order buttons.
- Do not click `经办`.
- Do not set `ZHIDAN_TEST_MODE=0`.
- Do not force USBHub ports inside this script. If the user needs a specific UKey, ask them to open the correct USBHub port first or run the hub controller separately.
- If both 首页 and 工作台 layouts fail, report `status=NOT_FOUND` and save diagnostic text; do not guess a balance from arbitrary numbers.
- Credentials stay in `C:\Users\30112\Desktop\财务\招行\招行制单\招行\.env`; never print them.

## Useful Prompts

```text
查询招行余额，读当前登录公司的人民币余额。
```

```text
先打开 USBHub 12 口，再查询招行余额。
```
