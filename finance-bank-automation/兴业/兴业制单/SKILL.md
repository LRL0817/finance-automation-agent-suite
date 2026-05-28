---
name: cib-single-transfer
description: Operate the local 兴业制单 / 兴业银行企业网银 single-transfer automation in C:\Users\30112\Desktop\财务\兴业\兴业制单. Use when the user says 兴业制单, 兴业银行制单, 跑测试, 批量测试, 只填单截图, 实际单笔付款, 预演实际单笔, 修改付款信息, or provides transfer fields such as label, amount, acct_no, acct_name, bank, branch_full, branch_queries, and purpose for this CIB workflow.
---

# 兴业制单

> 财务项目总览优先看 `C:\Users\30112\Desktop\财务\银行自动化项目总览.md`；迁移期旧总览在 `C:\Users\30112\Desktop\银行自动化项目总览.md`。如果本项目的用途、入口、提交开关、安全边界、USBHub 规则、日志/截图路径发生变化，必须同步更新总览。

Use this skill for the local CIB enterprise online banking automation in:

```text
C:\Users\30112\Desktop\财务\兴业\兴业制单
```

The live entry points are:

- `batch_fill_only.py`: batch test, `fill_only=True`, fills ten transfers and screenshots, never clicks 下一步/提交.
- `open_bank.py`: single-transfer entry. It fills `DEFAULT_TRANSFER` from `cib_open_bank/main_flow.py`. By default it **does not** submit. For fast maker-entry submission, set `CIB_ALLOW_SUBMIT=1`; the script logs the current fingerprint for audit only, then clicks 下一步/提交 after confirmation-page hard-check passes. It no longer requires `CIB_SUBMIT_FINGERPRINT`.
- `login_only.py`: login verification only; success keeps the bank window and UKey connected.

> **Important:** `DEFAULT_TRANSFER` in `cib_open_bank/main_flow.py` ships as **placeholder data** (e.g. `acct_no="000000000000000"`, `bank="示例银行"`). It is **not** a real payment payload and must never be used for actual submit. Always write the user-supplied fields into `DEFAULT_TRANSFER` before any real-submit attempt.

Treat every full run as a real bank maker-entry operation. If the user explicitly says actual run, 实际运行, 需要提交, 真实付款, 提交付款, 直接走全流程, or 快速制单 together with transfer fields, the intended path is the Fast Single Maker Entry workflow below. Use safe preview only when the user asks for preview/test/no-submit.

## Transfer Template

The user should be able to complete an actual-submit request by sending only this short Chinese template:

```text
用兴业制单，实际运行，需要提交。

付款信息：
制单标签：……
金额：……
收款账号：……
收款户名：……
收款银行大类：……
完整开户行：……
收款行搜索关键词：["……", "……", "……"]
用途：……
```

Treat this as a complete maker-entry instruction if every field after `付款信息` is present and non-empty. Do not require the user to repeat workflow text; execute the fast maker-entry workflow yourself.

Chinese field mapping:

- `制单标签` -> `label`
- `金额` -> `amount`
- `收款账号` -> `acct_no`
- `收款户名` -> `acct_name`
- `收款银行大类` -> `bank`
- `完整开户行` -> `branch_full`
- `收款行搜索关键词` -> `branch_queries`
- `用途` -> `purpose`

English/internal field template:

```text
label: 例如 SAMPLE_某某付款
amount: 0.01
acct_no: 收款账号
acct_name: 收款户名
bank: 收款银行大类，例如 招商银行 / 中国民生银行 / 桂林国民村镇银行
branch_full: 来源截图里的完整开户行，可写支行全名
branch_queries: ["搜索关键词1", "搜索关键词2", "银行大类"]
purpose: 用途，例如 合同付款
```

Map it to a Python dict like:

```python
{
    "label": "SAMPLE_example",
    "amount": "0.01",
    "acct_no": "000000000000000000",
    "acct_name": "某某有限公司",
    "bank": "招商银行",
    "branch_full": "招商银行股份有限公司某某支行",
    "branch_queries": ["某某支行", "招商银行某某", "招商银行"],
    "purpose": "合同付款",
}
```

For this CIB workflow, `bank` is the final receiving-bank category. It does not need the branch name. `branch_full` and `branch_queries` are source/reference/search hints only. Candidate ordering for the receiving-bank picker uses the shared `公共\bank_branch_matcher.py` scorer, but success is still fail-closed on one unique bank-category match.

Field roles for fast maker-entry submission:

- **Confirmation-page hard check** (`_verify_confirmation_page`): `amount` (normalized: commas/¥/￥/元/whitespace stripped), `acct_no` (digits-only), `acct_name`, `bank` (bank category), `purpose`, plus a negative-keyword scan (错误/失败/未通过/重新/异常). `branch_full` is **not** a confirmation-page hard-check field — the business rule is that the receiving-bank field only needs the bank category, not the full branch name.
- **Audit fingerprint** (`compute_transfer_fingerprint`, sha256[:12] of pipe-joined fields in this fixed order): `amount | acct_no | acct_name | bank | branch_full | purpose`. This is printed for audit only and is **not** a submit gate.

## Update Single Payment

When the user says to change the actual single-payment information:

1. Edit only `cib_open_bank/main_flow.py`.
2. Replace `DEFAULT_TRANSFER` with the provided fields.
3. Preserve `LOGIN_NAME` / `CIB_LOGIN_NAME`, login logic, bank matching, USB Hub logic, the `CIB_ALLOW_SUBMIT` submit gate, placeholder-data guard, and the confirmation-page verification code.
4. Run:

```powershell
python -m compileall -q open_bank.py batch_fill_only.py cib_open_bank
```

5. Report the normalized payment fields back for confirmation.
6. If the user has already explicitly requested actual run/submit/快速制单 in the same request, proceed to Fast Single Maker Entry after the field readback. If the payment fields are incomplete or ambiguous, stop and ask for the missing fields.

## Fast Single Maker Entry

Use this as the default when the user says:

- 兴业制单，实际运行
- 兴业制单，需要提交
- 跑实际付款
- 真实付款
- 提交付款
- 直接走全流程
- 快速制单

Required input — **all** of these must be supplied or extracted from the user's instruction before touching `DEFAULT_TRANSFER`:

- `label`
- `amount`
- `acct_no`
- `acct_name`
- `bank` (bank category, e.g. `招商银行`; this is what the confirmation page hard-checks)
- `branch_full` (full branch name from the source; used for search/reference, not in confirmation hard-check)
- `branch_queries` (search-hint list)
- `purpose`

If any of these are missing or ambiguous, **stop and ask** — do not invent values, do not partially fill `DEFAULT_TRANSFER`, do not attempt to submit using the placeholder defaults.

If the user supplied the short Chinese template above, it is enough input. Parse it, update `DEFAULT_TRANSFER`, and continue directly with the fast maker-entry workflow. Do not require a dry run or fingerprint confirmation unless the user explicitly asks for preview/no-submit.

Fast maker-entry workflow:

1. Write the provided fields into `DEFAULT_TRANSFER` in `cib_open_bank/main_flow.py` (replace every placeholder value).
2. Run:

   ```powershell
   python -m compileall -q open_bank.py batch_fill_only.py cib_open_bank
   ```

3. Read back the final `DEFAULT_TRANSFER` to the user (label, amount, acct_no, acct_name, bank, branch_full, purpose).
4. Do the maker-entry submit in a single PowerShell session:

   ```powershell
   $env:CIB_ALLOW_SUBMIT='1'
   python open_bank.py
   ```

   The code will then:

   - Verify `CIB_ALLOW_SUBMIT=1`. If missing, it screenshots top/bottom and returns without submitting.
   - Stop before 下一步 if `DEFAULT_TRANSFER` still has empty or placeholder template data.
   - Print the current fingerprint for audit only.
   - Click 下一步 only after the allow flag and placeholder guard pass.
   - Re-read the confirmation page.
   - Hard-check that `amount` (normalized), `acct_no` (digits-only), `acct_name`, `bank`, and `purpose` are all on the confirmation page, and that no negative keyword (错误/失败/未通过/重新/异常) appears. `branch_full` is **not** part of this hard-check by design.
   - Click 提交 only after confirmation-page verification passes.
   - Stop fail-closed if any verification or button lookup fails.
   - Close bank windows and run USB Hub all-off afterward.

After running, verify:

- The command exit status.
- Final screenshots in `screenshots`.
- Bank windows are closed.
- USB Hub is all-off.
- No `Firmbank` or automation `python` processes remain.

Hard rules for this workflow:

- **Never** run with `CIB_ALLOW_SUBMIT=1` while `DEFAULT_TRANSFER` still contains placeholder values (`000000000000000`, `示例银行`, `示例收款方公司`, etc.).
- **Never** widen the confirmation-page hard-check to include `branch_full` — the business rule is that the receiving-bank field only needs the bank category.

## Safe Preview

For a single-transfer preview, do not set `CIB_ALLOW_SUBMIT`.

Run:

```powershell
python open_bank.py
```

Expected behavior:

- Logs in.
- Fills `DEFAULT_TRANSFER` (which is the placeholder template unless the user has explicitly populated it).
- Takes screenshots.
- Prints the current transfer fingerprint for audit, then stops at the safety gate before 下一步/提交.
- Closes bank windows and runs USB Hub all-off in `main()`.

Use this when the user says:

- 预演实际单笔
- 先填单不提交
- 跑一遍看看
- 不设置 `CIB_ALLOW_SUBMIT`

## Batch Test

For ten-transfer testing, run:

```powershell
python batch_fill_only.py --fresh
```

This is always safe test mode. It uses `cib_open_bank/batch_data.py` and calls `run_transfer_flow(fill_only=True, ...)`.

For a fresh ten-transfer test, always use `--fresh`. It archives old `screenshots\batch_*.png` into `screenshots\archive\batch_yyyyMMdd_HHmmss` before running, so old screenshots cannot cause skipped batches.

After every batch run, the script automatically tidies intermediate batch screenshots into `screenshots\archive\batch_debug_yyyyMMdd_HHmmss`, leaving only the 20 final verification screenshots in `screenshots` root unless `--keep-debug` is explicitly used.

After batch test, verify:

- `batch_01` through `batch_10` each have `06_填单完成_上半页_关闭前.png` and `06_填单完成_下半页_关闭前.png`.
- Bank windows are closed.
- USB Hub is all-off.
- No `Firmbank` or automation `python` processes remain.

## M3 Latest Payment Validation

For M3 latest-payment validation with this CIB project, use:

```powershell
python m3_latest30_cib_fill_only_runner.py
```

Rules:

- It is always a non-submit verification path unless a separate entry explicitly changes that contract; keep `CIB_ALLOW_SUBMIT` unset for validation.
- Each transfer amount is overwritten to `0.01` for CIB filling; M3 original application amount is preserved in the saved raw JSON and final Feishu report.
- At the end of every run, including success, failure, and `--extract-only`, the runner must send a Feishu final report through the Codex gateway.
- The final report must include M3 information screenshots as contact-sheet images, per-round success/failure status, problem field, problem reason, and whether manual confirmation is needed.
- The final report result is recorded in `summary.json` as `final_feishu_notification`; if gateway reporting fails, the runner returns non-zero.

## Login Test

For login-only validation:

```powershell
python login_only.py
```

Successful `login_only.py` intentionally keeps the bank window and UKey connected. If the user does not continue manually, close bank windows and all-off afterward.

## Workspace Housekeeping

Keep the project tidy with:

```powershell
python maintain_workspace.py          # dry-run, no changes
python maintain_workspace.py --apply  # apply cleanup/archive moves
```

The housekeeping tool only touches paths under `C:\Users\30112\Desktop\财务\兴业\兴业制单`. It moves generated screenshots into `screenshots\archive`, moves review contact sheets into `screenshots\reviews`, and removes Python cache files.

The project `.bat` launchers also call the cross-project cleanup helper `C:\Users\30112\Desktop\财务\公共\maintenance\cleanup_after_automation.bat` after each automation run, while preserving the original bank-flow exit code.

## Safety Rules

- Never run actual submit from `batch_fill_only.py`.
- Never create a batch actual-payment entry unless the user explicitly asks for it.
- Never set `CIB_ALLOW_SUBMIT=1` for preview/test.
- Never set `CIB_ALLOW_SUBMIT=1` while `DEFAULT_TRANSFER` still holds placeholder values; populate `DEFAULT_TRANSFER` from user-provided fields first.
- Fast maker-entry submit requires `CIB_ALLOW_SUBMIT=1`; `CIB_SUBMIT_FINGERPRINT` is no longer used. The fingerprint is printed only for audit.
- Do not use `CIB_LOGIN_COORD_FALLBACK=1` or `CIB_LOGIN_CARD_COORD_FALLBACK=1` unless the user explicitly approves it.
- If UIA cannot find an exact login name candidate, or the login card is not detected, the default is fail-closed.
- If the confirmation page does not contain all required fields, or contains a negative keyword (错误/失败/未通过/重新/异常), stop and do not submit. The hard-check fields are amount, acct_no, acct_name, bank, purpose — `branch_full` is not part of the confirmation hard-check by design.
- If an error occurs during GUI automation, close bank windows, USB Hub all-off, and dismiss UKey notices when possible.

## Environment

- Screen: `1920x1080`, Windows display scaling `100%`.
- `.env` must contain `LOGIN_PWD` and `CERT_PWD`.
- Optional login override: `.env` `LOGIN_NAME`, or environment variable `CIB_LOGIN_NAME`; default is `AJBGNP`.
- Bank shortcut defaults to `%USERPROFILE%\Desktop\兴业银行企业网银.lnk`.
- USB Hub defaults: company `兴业银行-风飞格公司`, port `17`, COM `COM3`.
