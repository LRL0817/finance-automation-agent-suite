---
name: query-abc-balance
description: Query Agricultural Bank of China corporate online banking home-page account balance. Use when Codex needs to log in to ABC corporate banking with USB Hub port 12, read the current company name, masked account, currency, and visible balance from the home page, or build/run the 查询农业余额 workflow.
---

# 查询农业余额

> 财务项目总览优先看 `C:\Users\30112\Desktop\财务\银行自动化项目总览.md`；迁移期旧总览在 `C:\Users\30112\Desktop\银行自动化项目总览.md`。如果本项目的用途、入口、输出、安全边界或 USBHub/登录规则发生变化，必须同步更新总览。

Use this skill to read the visible company and account balance from 农行企业网银.

This workflow is read-only:

- It may log in to 农行企业网银.
- It may stop at the logged-in home page.
- It may click the safe `余额` row `显示` button to reveal the home-page balance.
- It must not enter transfer pages.
- It must not fill forms.
- It must not click submit/next/confirm transfer buttons.
- After every query attempt, successful or failed, it must close all USB Hub ports.

## Entry Point

Run:

```powershell
C:\Users\30112\Desktop\财务\农业\查询农业余额\run_query_balance.bat
```

Or:

```powershell
python C:\Users\30112\Desktop\财务\农业\查询农业余额\scripts\query_balance.py
```

The script reuses the existing ABC login, certificate, USB Hub 29, no-proxy, and home-page navigation helpers from:

```text
C:\Users\30112\Desktop\财务\农业\农业银行\skills\单笔转账\abc_single_transfer
```

Credentials and ABC runtime settings stay in:

```text
C:\Users\30112\Desktop\财务\农业\农业银行\.env
```

## What To Read

Read the logged-in ABC home-page account summary card and extract:

- `company`: company name near `账户名` or the greeting, usually ending in `有限公司`.
- `account_masked`: masked account text near `账号`.
- `currency`: normally `人民币`.
- `balance`: value nearest to the `余额` label after clicking the balance `显示` button.
- `status`: `OK`, `MASKED`, or `NOT_FOUND`.

Do not guess balances from unrelated numbers. If the balance remains `--`, `******`, or cannot be paired with the `余额` label, return `MASKED` or `NOT_FOUND`.

## Output

Each run creates:

```text
C:\Users\30112\Desktop\财务\农业\查询农业余额\runs\balance_yyyyMMdd_HHmmss\
```

Files:

- `balance.json`: normalized result and diagnostic text.
- `screen.png`: final page screenshot when possible.

The command also prints a concise summary to stdout.
Before exit, the script closes the browser and then runs USB Hub `all-off`.

## Safety Rules

- Read-only only. Never click transfer/payment/order buttons.
- Always use USB Hub 29 by default via `ABC_USB12_PREPARE=true` (variable name kept for compatibility).
- Always close all USB Hub ports after the run via `ABC_USB12_ALL_OFF_AFTER_RUN=true` by default.
- Browser must use domestic direct network: `--no-proxy-server`.
- Only click a `显示` button whose row is visually aligned with the `余额` label.
- Never click `查询明细`, `单笔转账`, `回单打印`, `提交`, `下一步`, or transfer-related controls.
- Keep full extraction evidence in `balance.json`; do not invent values when DOM text is insufficient.
- Do not leave the K 宝 USB port active after the script exits unless `ABC_USB12_ALL_OFF_AFTER_RUN=false` is explicitly set for debugging.

## Useful Prompts

```text
查询农业余额，读当前登录公司的余额。
```

```text
用查询农业余额跑一次，告诉我哪个公司和余额。
```
