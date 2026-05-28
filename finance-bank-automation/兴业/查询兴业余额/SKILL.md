---
name: query-cib-balance
description: Query 兴业银行企业网银 corporate account balance from the Windows GUI. Use when Codex needs to log in to 兴业银行企业网银, reuse the 兴业制单 login/USB flow, read the current company name, common account, currency, and visible/expanded available balance, or build/run the 查询兴业余额 workflow.
---

# 查询兴业余额

> 财务项目总览优先看 `C:\Users\30112\Desktop\财务\银行自动化项目总览.md`；迁移期旧总览在 `C:\Users\30112\Desktop\银行自动化项目总览.md`。如果本项目的用途、入口、输出、安全边界或 USBHub/登录规则发生变化，必须同步更新总览。

Use this skill to read the visible company and account balance from 兴业银行企业网银. This workflow is read-only: it may open/login the CIB client, click safe navigation such as `首页`, and click the balance eye icon to reveal the masked balance, but it must not enter transfer/order pages or fill forms.

## Entry Point

Run:

```powershell
C:\Users\30112\Desktop\财务\兴业\查询兴业余额\run_query_balance.bat
```

Or:

```powershell
python C:\Users\30112\Desktop\财务\兴业\查询兴业余额\scripts\query_balance.py
```

The script first tries to reuse an already logged-in 兴业企业网银 window. If no logged-in main window is present, it imports and reuses the existing login/USB/window logic from:

```text
C:\Users\30112\Desktop\财务\兴业\兴业制单\cib_open_bank\main_flow.py
```

Specifically, it calls `run_transfer_flow(stop_after_login=True)`, which logs in and stops before any transfer navigation. The login name/password/UKey settings stay in `C:\Users\30112\Desktop\财务\兴业\兴业制单\.env` and related runtime config.

## What To Read

Collect UIA visible text from the main 兴业网银 window, preferably the home page. Extract:

- `company`: first visible company name ending in `有限公司`, such as `商融（天津）科技有限公司`.
- `account_no`: long account number near `常用账号`.
- `currency`: usually `人民币(CNY)`.
- `available_balance`: amount nearest to the `可用余额` label after clicking the eye icon if the value is masked.
- `status`: `OK`, `MASKED`, or `NOT_FOUND`.

If the balance is still masked as `******` after attempting to reveal it, return `status=MASKED` with company/account when available. Do not guess a balance from unrelated numbers.

## Output

Each run creates:

```text
C:\Users\30112\Desktop\财务\兴业\查询兴业余额\runs\balance_yyyyMMdd_HHmmss\
```

Files:

- `balance.json`: normalized result, matched text controls, and timestamp.
- `screen.png`: screenshot of the 兴业网银 window when available.

The command also prints a concise summary to stdout. On exit, the script always calls `close_bank_windows()`, then `all_off_usb_hub_ports()`, and dismisses UKey notices when possible.

## Safety Rules

- Read-only only. Never click transfer/payment/order buttons.
- Reuse `兴业制单` login helpers; do not duplicate credentials or print passwords.
- If no active window is found, login with `run_transfer_flow(stop_after_login=True)` only.
- Do not call `run_transfer_flow()` without `stop_after_login=True` from this skill.
- Do not set `CIB_ALLOW_SUBMIT=1`.
- If UIA cannot find `可用余额` or a nearby amount, save diagnostics and report `NOT_FOUND` or `MASKED` rather than guessing.
- After every query attempt, successful or failed, the 兴业企业网银 window opened from `C:\Users\30112\Desktop\兴业银行企业网银.lnk` must be closed with `close_bank_windows()`.
- After every query attempt, successful or failed, all USB Hub ports must be turned off with `all_off_usb_hub_ports()`.
- The bank window and UKey must not remain active after the query script exits.

## Useful Prompts

```text
查询兴业余额，读当前登录公司的可用余额。
```

```text
用查询兴业余额，登录后告诉我当前公司和可用余额。
```
