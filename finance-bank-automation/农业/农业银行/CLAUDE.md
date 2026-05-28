# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Windows-only Python automation that drives 农业银行 (Agricultural Bank of China) corporate net banking (企业网银) to perform single transfers (单笔转账). The single entry point is `skills/单笔转账/open_browser.py`. There is no test suite, build step, or linter configured.

## Run

```powershell
python skills/单笔转账/open_browser.py
```

Configure `.env` at the repo root before running. All keys are optional — the script falls back to interactive prompts / partial automation when a key is absent:

```
# Optional: skip clicking the 企业网银登录 entry by jumping straight to a login URL.
# Validated against an abchina.com.cn host whitelist before use.
ABC_LOGIN_URL=

# Optional: absolute path to a JSON file with transfer fields. If omitted, the
# script auto-loads repo-root transfer_data.json, then the M3 direct-supply
# payment-data project under the Desktop. There is no OCR step. See
# "Transfer data JSON" below.
TRANSFER_DATA_PATH=

# Optional: M3 direct-supply payment-data source. Defaults to the sibling
# Desktop project. The script tries bank_form.json first, then latest.json.
M3_TRANSFER_DATA_DIR=C:/Users/30112/Desktop/M3直供合同付款数据获取
M3_BANK_FORM_PATH=

# Optional: inline transfer fields in .env. Used only when TRANSFER_DATA_PATH is
# empty, repo-root transfer_data.json is absent, and M3 data is absent.
TRANSFER_ACCOUNT=
TRANSFER_NAME=
TRANSFER_BANK=
TRANSFER_AMOUNT=
TRANSFER_PURPOSE=

# Optional: absolute path to a 云链融 invoice screenshot. Used purely as a
# human-reference image; the script never reads pixels from it.
TRANSFER_IMAGE_PATH=

# Optional: K宝 password. Ignored unless ALLOW_INSECURE_ENV_PASSWORD=true.
# By default the script prompts for the password at runtime via getpass and
# refuses to read .env (storing the password on disk is a leak risk).
KB_PASSWORD=
ALLOW_INSECURE_ENV_PASSWORD=false

# Optional: send Enter after typing the K宝 password. On by default for
# zero-human login/navigation; set false to stop at the password window.
KB_PASSWORD_AUTO_ENTER=true
ABC_CLICK_CONTINUE_AFTER_KB_OK=true
ABC_CONTINUE_AFTER_KB_OK_WAIT_S=8.0
ABC_STOP_AFTER_LOGIN=false

# Optional: USB Hub ports to open before ABC login. The ABC U-key must stay on
# port 12. Port 6 is used by the external OK servo / auto-clicker. The helper
# opens these ports with `on`; it does not call `only` or `all-off`, so it will
# not cut power to the auto-clicker while preparing the U-key.
ABC_USB12_PREPARE=true
ABC_USB_HUB_PORTS=29,30
ABC_USB_HUB_COM=COM3
ABC_USB_HUB_EXACT_PORTS=true
ABC_USB_CLOSE_AUTO_HOME_TAB=true  # open_browser.py default when unset; set false to keep the U-key driver's public ABC homepage tab

# Optional: trigger the external USB servo to press the physical K宝 OK button.
# The known board is configured by USB舵机驱动设置 V2.1.exe. Prefer ScrollLock
# as its quick-start hotkey; CapsLock can alter later account/password typing.
# Direct `open_browser.py` runs keep this disabled by default; batch test entry
# scripts set the two enable flags to true because port 6 is powered for this
# auto-clicker. Test the physical position with `python press_ok_servo.py --press`.
ABC_OK_SERVO_ENABLE=false
ABC_OK_SERVO_AFTER_KB_PASSWORD=false
ABC_OK_SERVO_AFTER_TRANSFER_KB_PASSWORD=true
ABC_OK_SERVO_HOTKEY=scrolllock
ABC_OK_SERVO_AFTER_KB_PASSWORD_DELAY_S=0.8
ABC_ALLOW_SUBMIT_ONCE=false
ABC_CONFIRM_SUBMIT_MODAL_ONCE=false
ABC_CONFIRM_SUBMIT_MODAL_TIMEOUT_S=8.0
ABC_CONFIRM_TRADE_INFO_ONCE=false
ABC_CONFIRM_TRADE_INFO_TIMEOUT_S=10.0
ABC_TRANSFER_KB_PASSWORD_AFTER_TRADE=false
ABC_TRANSFER_KB_PASSWORD_TIMEOUT_S=15.0
ABC_OK_SERVO_AFTER_SUBMIT=false
ABC_OK_SERVO_AFTER_TRANSFER_KB_PASSWORD_DELAY_S=1.0
ABC_OK_SERVO_AFTER_SUBMIT_DELAY_S=1.0
ABC_OK_SERVO_AFTER_SUBMIT_SETTLE_S=3.0
ABC_TRANSFER_BATCH_INDEX=
ABC_TRANSFER_AMOUNT_OVERRIDE=

# Optional: if true, write screenshot.png / failure_screenshot.png on exit.
# Off by default because transfer pages contain account numbers and amounts.
# When enabled, checkpoint screenshots are also written under
# artifacts/单笔转账/debug_runs/<RUN_ID>/screenshots/.
ENABLE_SCREENSHOT=false

# Optional: when ENABLE_SCREENSHOT=true, save screenshots at every debug checkpoint.
# Set false to keep only final/failure checkpoint screenshots plus compatibility files.
SCREENSHOT_ON_EACH_STEP=true

# Optional: full-screen screenshots during the whole run. Disabled by default
# because the desktop snapshot will leak account numbers / amounts / payee
# names / K宝 windows to disk. Turn on only for short, supervised triage.
ENABLE_LIVE_SCREENSHOTS=false
LIVE_SCREENSHOT_INTERVAL_S=2
LIVE_SCREENSHOT_MAX_FILES=300
# Disabled by default; capturing on every debug checkpoint compounds the
# disclosure surface above. Only relevant when ENABLE_LIVE_SCREENSHOTS=true.
LIVE_SCREENSHOT_ON_CHECKPOINT=false

# Optional: native cert and K宝 timing. First short-wait for a visible cert
# dialog, then optionally (CERT_ENTER_AFTER_CLICK=true) send one controlled
# no-title Enter, and keep watching for K宝 or a late cert dialog. Keep
# CERT_WINDOW_WAIT_S high enough for slow Chrome cert dialogs.
CERT_INITIAL_WINDOW_WAIT_S=2
CERT_WINDOW_WAIT_S=75
KB_WINDOW_WAIT_AFTER_CERT_S=8

# Optional: post-cert no-title Enter. Disabled by default — sending an Enter
# without a confirmed 选择证书 window can land on the K宝 password window and
# burn a retry of its limited budget. Turn on only when you have verified
# that the local Chrome version doesn't expose the cert dialog as a top-level
# window. Even when enabled, the script aborts the Enter if a K宝 window is
# already detected (0.5s pre-check before the call site and inside the
# function).
CERT_ENTER_AFTER_CLICK=false

# Optional: screen-coordinate fallback for the 继续登录 web tip. Disabled by
# default — it clicks at a fixed (x, y) and would otherwise fire even when
# the foreground is some unrelated app. When enabled, the script also
# requires the foreground window title to look like a Chrome / Edge / 农行
# / 企业网银 window before clicking.
CONTINUE_LOGIN_COORD_FALLBACK=false

# Optional: broaden the Chrome client-cert auto-select rule from
# https://cbank.abchina.com.cn (default) to https://[*.]abchina.com.cn. Only
# enable if you actually need other ABC sub-domains to silently consume the
# K宝 cert; broadening the pattern increases the blast radius of any
# compromised sub-domain.
ABC_CERT_AUTOSELECT_BROAD=false

# Optional: re-enable the legacy display='none' overlay-hiding fallback in
# dismiss_transient_dropdowns. Disabled by default — the default path is
# Escape + blur only, which leaves the page DOM untouched. When enabled, the
# script records each element's prior inline display in
# data-codex-prev-display so restore_transient_dropdowns can put them back
# at the end of fill_transfer_form.
DISMISS_DROPDOWNS_HIDE=false

# Optional: interval in seconds for repeated polling logs while waiting for native windows
# or post-login pages. Lower values are noisier but easier to diagnose timing issues.
DEBUG_POLL_LOG_INTERVAL_S=2
```

If no transfer data source is available, the script still runs — it stops after navigating to the transfer page and skips form fill. If `TRANSFER_DATA_PATH`, repo-root `transfer_data.json`, M3 `bank_form.json/latest.json`, or inline `.env` transfer fields are present but fail validation, the script aborts before login (fail-closed). The script intentionally keeps the browser open after running; exit with Ctrl+C.

Dependencies (no `requirements.txt` exists — install ad-hoc):
- `playwright` (and `playwright install chromium` once)
- `pyautogui`
- `python-dotenv`
- `pywin32` — required for native-window detection (`focus_native_window_by_title`); without it, the script cannot confirm the cert-selection or K宝 password windows and will fall back to fully manual handling for those two steps.

## Architecture

### Why two automation layers

ABC's certificate-login flow has two surfaces Playwright cannot reach:
1. **The Chrome-native "select a certificate" dialog** — outside the DOM, so DOM/JS clicks fail. The script falls back to `pyautogui.press("enter")` to accept the highlighted default certificate.
2. **The K宝 (USB key) password prompt** — driven by a hardware-vendor browser plugin, not an HTML input. The script first uses `focus_native_window_by_title` to wait up to 8s for the 验证K宝密码 window and confirm it's actually in the foreground, then types per-character via `pyautogui.write()` at an 0.08s interval. The focus-confirm step is what stops the password from being typed into a stale/wrong foreground window. The script also rejects non-ASCII passwords up front: `pyautogui.write` mis-keys non-ASCII characters, and a wrong password consumes the K宝's limited retry budget, which can lock the USB key.

Anything DOM-visible (login button, navigation, form fields) goes through Playwright locators. Anything OS-level goes through pyautogui. Don't try to converge these — the split is load-bearing.

### Flow in `open_browser.py::main()`

1. `load_environment()` + `load_configured_transfer_data()` — validate transfer data before launching the browser. Priority: explicit `TRANSFER_DATA_PATH`, repo-root `transfer_data.json`, M3 `bank_form.json/latest.json`, then inline `.env` fields. Bad data aborts with exit code 2 (fail-closed) so an invalid amount can't reach the form.
2. `get_kb_password()` — returns the password from `.env` only when `ALLOW_INSECURE_ENV_PASSWORD=true`; otherwise prompts via `getpass.getpass()`, and a blank input means "I'll type it in the K宝 window myself."
3. `playwright.chromium.launch(headless=False)` — must be headful; the K宝 plugin and cert dialog don't work headless.
4. Open `ABC_LOGIN_URL` (if configured) or `DEFAULT_HOME_URL` → click `企业网银登录` (often opens a new tab; captured via `context.expect_page`).
5. `click_cert_login` — clicks `#m-kbbtn-new` with `no_wait_after=True` because the next step is a native dialog, not navigation.
6. `handle_certificate_selection` — chain: short-wait `CERT_INITIAL_WINDOW_WAIT_S` for the native 选择证书 window via `focus_native_window_by_title` → press Enter once if focused → otherwise, before any no-title Enter, do a 0.5s K宝 window pre-check; if K宝 already appeared, return `CERT_PASSWORD_WINDOW_DETECTED` immediately and skip Enter entirely → only with `CERT_ENTER_AFTER_CLICK=true` (default **false**) send one controlled no-title Enter → keep watching native windows for up to `CERT_WINDOW_WAIT_S`. If K宝 appears, skip DOM polling; if a late 选择证书 window appears, focus it and press Enter then wait for K宝. Keep this order: never resume Playwright DOM polling immediately after a no-title Enter, because a late native cert dialog can otherwise block the browser. The K宝 pre-check exists because an Enter sent into the password window submits an empty password and burns a retry of the K宝's small retry budget.
7. `input_kb_password(password)` — waits up to 8s for the 验证K宝密码 window, rejects non-ASCII passwords (pyautogui.write would mis-key them), then types per-char with an 0.08s interval. Enter is sent by default; set `KB_PASSWORD_AUTO_ENTER=false` to stop for manual confirmation. Login-stage K宝 password entry never triggers the external servo, even if legacy `ABC_OK_SERVO_AFTER_KB_PASSWORD=true` is present. Prefer `ABC_OK_SERVO_HOTKEY=scrolllock` over CapsLock so the hotkey does not change later typing behavior. After that, `ABC_CLICK_CONTINUE_AFTER_KB_OK=true` performs a short post-OK check for the web `继续登录` prompt, which can appear together with the native K宝 confirmation.
8. `navigate_to_single_transfer` — clicks `付款业务` → `单笔转账`. Selectors prefer `get_by_text(..., exact=True)` and fall back to substring `text=` only if exact misses (avoids hitting "单笔转账查询" / "单笔转账记录" instead of the menu item).
9. `fill_transfer_form(page, data, clear_residual=False)` — expects normalized keys `收款账号`, `收款户名`, `收款方开户行` (optional), `金额`, `用途` (optional). `normalize_transfer_data` also accepts common aliases such as `收方账号`, `银行账户`, `收方户名`, `收款单位名称`, `开户银行`, `支行名称`, `申请金额`, and `申请说明`. Standard thousands separators in amounts are accepted and stripped. `收款方开户行` is the ABC page's bank-category dropdown, so full branch names are normalized to the broad bank name before selection. Field selectors try exact placeholder first, then `placeholder*=` substring. The bank dropdown is handled by clicking the select input, typing into the visible dropdown search field when present, and selecting the matching bank option. When `clear_residual=True` (always set by `fill_transfer_batch`), the function attempts to clear `收款方开户行` / `用途` if the current record omits them, so the previous batch row's value cannot silently ride along; if clearing can't be confirmed, the field's status flips to `False` and the run log emits a "请人工核对该字段，避免上一条残留串单" warning instead of a quiet pass. **Submission is never automated.**
10. `assert_current_page_allowed(page, label)` — called after each `page.goto`, after a new tab is captured, and after `find_active_home_page` returns. Aborts the run with `RuntimeError` if the current `page.url` isn't `https` or its host isn't `abchina.com.cn` / a sub-domain. This complements `_validate_login_url` (which only screened `ABC_LOGIN_URL`) and stops the script from clicking, typing, or sending a K宝 password into a hijacked / phishing landing page mid-flow.

### Cert dialog fallback chain

`handle_certificate_selection` walks a fail-soft chain — keep this order if you refactor:

1. Native 选择证书 window detection (`focus_native_window_by_title`) → `pyautogui.press("enter")` once.
2. **K宝 pre-check.** Before path B and any no-title Enter, do a 0.5s `focus_native_window_by_title(KB_WINDOW_TITLES, ...)`. If K宝 is already visible, return `CERT_PASSWORD_WINDOW_DETECTED` and stop sending Enter. An Enter into the K宝 window submits an empty password, which costs one of the K宝's small retry budget — never bypass this guard.
3. `press_enter_after_cert_login_without_title` — opt-in (`CERT_ENTER_AFTER_CLICK=true`, default **false**). Even when the env flag is on, the function repeats the 0.5s K宝 pre-check and refuses to send Enter if K宝 became visible while the upper-level guard was running.
4. `click_certificate_ok_after_cert_login_without_title` — also opt-in (`ALLOW_CERT_CLICK_WITHOUT_TITLE=true`, default false), screen-coordinate click on the cert dialog's `确定`. Off in normal use.
5. `close_tip_dialog_if_needed` — closes 温馨提示 modals that occasionally sit on top of the cert dialog. The button click is now scoped to visible dialog wrappers (`.el-dialog__wrapper`, `.el-message-box__wrapper`, `[role='dialog']`, `[role='alertdialog']`) and the wrapper's text must contain `温馨提示` / `继续登录` / `已知晓` / `请知悉`. The script no longer clicks `确定` anywhere on the page — that prevents accidentally clicking the form's own submit button.
6. `click_confirm_in_certificate_dialog` — DOM/JS strategies for the rare web-modal variant of the cert dialog.
7. `fallback_press_enter()` — last-chance Enter, but it itself re-checks for the 选择证书 window and refuses to press if the window isn't focused. Never bypass that check; it's what stops Enter from hitting an unrelated foreground window.

If all of these fail, the function logs a warning and returns `CERT_MANUAL_REQUIRED`; the user is expected to click the cert dialog manually before the script reaches `input_kb_password`.

### Chrome client-cert auto-select scope

Launch args set `--auto-select-certificate-for-urls` to a narrow allow-list:

- Default: `https://cbank.abchina.com.cn` and `https://cbank.abchina.com.cn:443` only.
- Opt-in via `ABC_CERT_AUTOSELECT_BROAD=true` adds `https://[*.]abchina.com.cn`. The broader pattern lets any `*.abchina.com.cn` host silently consume the K宝 client cert; only enable it if you need that.

### Field mapping (云链融 invoice → ABC form)

| Source field (云链融) | ABC form field |
|---|---|
| 银行账户 | 收款账号 |
| 收款单位名称 | 收款户名 |
| 开户银行 | 收款方开户行 |
| 申请金额 | 金额 |
| 申请说明 | 用途 |

## Things to know

- **Timing is fragile.** Many `time.sleep()` calls are tuned to ABC's actual page-load and plugin-focus latencies. Don't replace them with shorter waits or with Playwright auto-waits without testing on the real site — the cert dialog and K宝 plugin in particular don't surface DOM events you can wait on.
- **Submit is gated.** ABC is a real 制单 project, not permanently fill-only. Default = fill-only (test/verification, no real voucher). The production voucher path (single/per-record) requires explicit operator direction plus `ABC_ALLOW_SUBMIT_ONCE=true`, and the run must load exactly one transfer. The script clicks the page's `提交` button once. If ABC then shows the account-length message box, `ABC_CONFIRM_SUBMIT_MODAL_ONCE=true` is required to click that web `确定` once. If ABC then shows `交易信息确认`, `ABC_CONFIRM_TRADE_INFO_ONCE=true` is required to click that web `确定` once. If ABC then shows the transfer K宝 password window, `ABC_TRANSFER_KB_PASSWORD_AFTER_TRADE=true` is required to reuse the same native K宝 password-window strategy as login; only after that may `ABC_OK_SERVO_AFTER_TRANSFER_KB_PASSWORD=true` trigger the physical OK clicker once. `ABC_OK_SERVO_AFTER_SUBMIT=true` is kept only as a compatibility alias and no longer triggers before transfer K宝 password submission. Do not make page-submit servo clicks default behavior. After the gated chain the voucher rests in the bank's pending-review (待审核/待复核) queue and the script stops — it never auto-reviews, authorizes, confirms, or makes final payment.
- **Encoding.** Source files are UTF-8 with Chinese identifiers and string literals (the skill directory itself is `单笔转账`). When writing files via PowerShell, use `-Encoding utf8` explicitly — Windows PowerShell 5.1's default is UTF-16 LE.
- **Debug runs.** Every run writes a dedicated log at `artifacts/单笔转账/debug_runs/<RUN_ID>/run.log` in addition to the aggregate `artifacts/单笔转账/logs/abc_transfer.log`. The log contains numbered `[排错] 第 NNN 轮` checkpoints and periodic polling progress for native windows / post-login page discovery.
- **Live screenshots.** Disabled by default — full-desktop captures leak account numbers, amounts, payee names, and any K宝 / Windows window in the foreground to disk. Set `ENABLE_LIVE_SCREENSHOTS=true` (and optionally `LIVE_SCREENSHOT_ON_CHECKPOINT=true`) to enable; PNGs plus `manifest.csv` then land in `artifacts/单笔转账/debug_runs/<RUN_ID>/live_screenshots/`. Useful for diagnosing whether Enter was sent to the certificate dialog or to the wrong foreground window — but treat the directory as sensitive and clean it up.
- **Page URL whitelist.** `assert_current_page_allowed` enforces `https://*.abchina.com.cn` after every navigation that could land on a different host (initial `goto`, new tab capture, post-login home). A failure aborts the run before any further click / type / K宝 password input — fail-closed.
- **Form residual clearing.** `fill_transfer_form` accepts `clear_residual: bool = False`. `fill_transfer_batch` always passes `clear_residual=True` so optional fields (`收款方开户行`, `用途`) absent from the current record cannot inherit the previous record's value. If the script can't confirm the field is empty, it flips that field's status to `False` and emits a 人工核对 warning instead of moving on silently.
- **Transient dropdown handling.** `dismiss_transient_dropdowns` is Escape + blur only by default; it doesn't mutate the DOM. Set `DISMISS_DROPDOWNS_HIDE=true` to re-enable the legacy `display='none'` overlay-hiding fallback — when on, original `inline display` values are stashed on `data-codex-prev-display` and `restore_transient_dropdowns` (called at the end of `fill_transfer_form`) puts them back so the page remains usable for human review.
- **Screenshot on exit and checkpoints.** Disabled by default. Set `ENABLE_SCREENSHOT=true` in `.env` to write compatibility screenshots (`screenshot.png` / `failure_screenshot.png`) and checkpoint screenshots under `artifacts/单笔转账/debug_runs/<RUN_ID>/screenshots/`; the page contains account numbers and amounts, hence the opt-in. Set `SCREENSHOT_ON_EACH_STEP=false` to keep only forced final/failure checkpoints.
- **USB helper diagnostics.** `open_abc_usb12.py` writes `artifacts/usb12/logs/open_abc_usb12.log` plus a per-run log under `artifacts/usb12/debug_runs/usb_<RUN_ID>/run.log`; if `ENABLE_SCREENSHOT=true`, it also captures before/after window states.

### Transfer data JSON

Shape expected by `load_transfer_data` (all keys are validated; failure aborts before login):

```json
{
  "收款账号": "00000000000000000000",
  "收款户名": "示例公司",
  "金额": "0.01",
  "收款方开户行": "中国农业银行示例支行",
  "用途": "货款"
}
```

Place a real copy at repo root as `transfer_data.json` for the default auto-fill path. Keep `transfer_data.example.json` as the non-sensitive template.

- `收款账号`: 8–32 digits, no separators.
- `收款户名`: non-empty.
- `金额`: positive decimal, ≤ 2 fractional digits, no thousand separators / scientific notation. The script does **not** round.
- `收款方开户行`, `用途`: optional. Empty values are skipped (the script never invents defaults).
