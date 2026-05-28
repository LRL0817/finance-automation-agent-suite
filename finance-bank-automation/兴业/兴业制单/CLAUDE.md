# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

Desktop GUI automation for the 兴业银行企业网银 (CIB enterprise online banking) Windows client. Drives a real bank workflow: switches a USB Hub UKey port on, opens the bank client via desktop shortcut, types two passwords (UKey dialog + login page), navigates to 单笔转账, fills a transfer form, and only when `CIB_ALLOW_SUBMIT=1` clicks 下一步 then 提交 after confirmation-page hard-check passes. `CIB_SUBMIT_FINGERPRINT` is no longer a submit gate; the current fingerprint is printed for audit only.

Desktop-level overview: `C:\Users\30112\Desktop\银行自动化项目总览.md`. If this project changes purpose, entry points, submit gates, USBHub rules, logs/screenshots, or safety boundaries, update that overview in the same change.

A run executes a real bank operation. Do not invoke entry points to "test" — use the non-destructive checks below.

## Target Environment Constraints

The script's coordinate logic only works when the host matches:
- Resolution: 1920x1080
- Windows display scaling: 100%
- Desktop shortcut: `%USERPROFILE%\Desktop\兴业银行企业网银.lnk` (overridable via `CIB_SHORTCUT_PATH`)
- USB Hub control script at `USB_HUB_CTRL_DIR` (defaults to `C:\Users\30112\Desktop\财务\公共\usbhub\usbhub\多口USB控制器软件以及驱动`); must contain `hub_ctrl.py` exposing `USBHub`
- `.env` in repo root with `LOGIN_PWD` (UKey password) and `CERT_PWD` (login-page password)

Fixed-coordinate fallbacks were measured at 125% scaling. `cib_open_bank/runtime.py` rescales them via `COORD_SCALE = TARGET_DPI_SCALE / SOURCE_DPI_SCALE` (1.0/1.25 by default). When changing target hardware, override `CIB_SOURCE_DPI_SCALE` / `CIB_TARGET_DPI_SCALE` rather than rewriting coordinates.

## Entry Points

Three top-level wrappers, each backed by a function in `cib_open_bank/main_flow.py`:

| Wrapper | .bat | Behavior |
| --- | --- | --- |
| `open_bank.py` | `run_单笔转账.bat` | `main()` — full flow. Submits only when `CIB_ALLOW_SUBMIT=1`; the current fingerprint is printed for audit only and is not a submit gate. Otherwise screenshots top/bottom and returns without clicking 下一步/提交. Always finishes with close + USB all-off. |
| `login_only.py` | `run_只登录.bat` | `login_only_main()` — calls `run_transfer_flow(stop_after_login=True)`; leaves bank window + UKey connected |
| `batch_fill_only.py` | `run_批量只填单截图关闭.bat` | Iterates `cib_open_bank/batch_data.TRANSFERS`, calls `run_transfer_flow(fill_only=True, ...)` per transfer; never submits, screenshots, then closes window + all-off between transfers |

The `fill_only=True` path returns before clicking 下一步; the `stop_after_login=True` path returns before navigating to 转账付款. These two flags are how callers de-risk the flow — preserve them when changing `run_transfer_flow`.

When `CIB_ALLOW_SUBMIT=1` (and neither `fill_only` nor `stop_after_login` is set), `run_transfer_flow` does a clean startup before opening the client: `close_bank_windows()` then `all_off_usb_hub_ports()`, then the usual `select_usb_hub_port()` + `os.startfile(SHORTCUT_PATH)`. This guard is gated on submit-mode only so it never disturbs `login_only.py` / `batch_fill_only.py`.

## High-Level Architecture

`cib_open_bank/` is layered roughly: runtime/IO at the bottom, UIA helpers in the middle, the orchestration on top.

- `runtime.py` — config (`load_config`), screenshots, fixed-coordinate scaling (`fixed_point`/`click_fixed`), and USB Hub control (`select_usb_hub_port`, `all_off_usb_hub_ports`). Holds `_ACTIVE_WINDOW_ORIGIN`, mutated by `set_active_window_origin` after the bank window is found so coordinates rescale relative to it.
- `windows.py` — non-UIA window management via `pygetwindow` + `win32gui`. Finds the main bank window, the `验证网盾密码` dialog (recursing into child windows), focuses the password edit by detecting the title bar's blue band in a screenshot, dismisses 网盾管理工具 toasts, and closes only bank-related windows (skips this script's terminal/PID).
- `ui_helpers.py` — generic UIA helpers via `pywinauto`: `_find_label` (excludes Edit/Document controls), `_find_nearby_edit`/`_find_nearby_combo` (siblings sorted by y-distance), `fill_field_by_label` (with `use_clipboard=True` going through PowerShell `Set-Clipboard` to avoid pyautogui's CJK issues), `click_control_by_name` (with optional `click_bounds` to avoid hitting same-named search results), and `click_next_step`/`click_submit` (UIA-only, **no coordinate fallback** — fail-closed to avoid clicking the wrong place when UIA misses).
- `bank_fields.py` — the 收款行 picker. Two strategies: `click_match_fill_button` (intelligent match's 填入 button) and a panel-search fallback (`click_field_dropdown` + `pick_branch_option`). `dump_bank_debug_controls` is the diagnostic to call when the picker fails.
- `form_controls.py` — pure re-export shim aggregating `bank_fields` + `ui_helpers`. Import from here in `main_flow.py`.
- `main_flow.py` — the numbered orchestration ([0a] real-submit clean startup, [0] USB Hub, [1] open window, [2] UKey dialog, [3] login page, [4] post-login UKey dialog, [5] navigate, [6] fill form + confirmation hard-check, [7] cleanup). `_locate_login_card` finds the login card by detecting wide white runs on the right half of the screen; `_login_coords` derives 4 click points (dropdown, password, checkbox, login_btn) from the card rectangle. **Fail-closed by default** when the card is not detected — only allows the legacy window-relative fallback if `CIB_LOGIN_CARD_COORD_FALLBACK=1`. `compute_transfer_fingerprint` produces sha256[:12] over `amount|acct_no|acct_name|bank|branch_full|purpose` and prints it for audit only. `_verify_confirmation_page` hard-checks only `amount` (normalized), `acct_no` (digits-only), `acct_name`, `bank`, `purpose`, plus a negative-keyword scan (错误/失败/未通过/重新/异常); `branch_full` is intentionally **not** part of the confirmation hard-check — it participates in the fingerprint only, because the receiving-bank field on the form ultimately needs the bank category, not the full branch name.
- `batch_data.py` — only consumed by `batch_fill_only.py`; not used by the single-transfer flow.

The main flow tries UIA control names first (e.g. `转账付款`, `单笔转账`, `下一步`, `提交`). For navigation controls (`转账付款`/`单笔转账`) it falls back to scaled fixed coordinates when UIA misses; **`下一步` and `提交` are UIA-only / fail-closed** and will not auto-fall-back to coordinates. When fixing a regression, prefer adjusting the UIA selector over re-introducing coordinate fallbacks for these submit-path controls.

## Common Commands

```powershell
pip install -r requirements.txt
python open_bank.py            # full single-transfer flow; only submits when CIB_ALLOW_SUBMIT=1
python login_only.py           # login only, leaves session open
python batch_fill_only.py      # batch fill from batch_data.TRANSFERS, never submits
```

To submit for real, first ensure `DEFAULT_TRANSFER` has been replaced with real user-provided fields and no placeholder values remain:

```powershell
$env:CIB_ALLOW_SUBMIT = "1"
python open_bank.py            # now clicks 下一步 / 提交 (real submit) after confirmation-page hard-check passes
```

The script prints the current fingerprint for audit. If the transfer payload changes, the audit fingerprint changes too, but no `CIB_SUBMIT_FINGERPRINT` confirmation is required by current code.

Non-destructive checks (preferred over running entry points to "test"):

```powershell
python -c "import pyautogui, pygetwindow, win32gui, win32api, pywinauto, serial; print('imports ok')"
python -m compileall open_bank.py cib_open_bank
python -c "import pyautogui, win32api; print(pyautogui.size()); print(win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1))"
```

There is no test suite. Verification artifacts live in `screenshots/` — checking these is how coordinate drift, UIA mismatches, and login-card detection are diagnosed.

## Useful Environment Overrides

- `CIB_SHORTCUT_PATH` — bank-client shortcut path
- `CIB_SOURCE_DPI_SCALE` / `CIB_TARGET_DPI_SCALE` — fixed-coordinate scaling
- `USB_HUB_CTRL_DIR` — directory containing `hub_ctrl.py`
- `CIB_USB_HUB_COM` / `USB_HUB_COM` — COM port (default `COM3`)
- `CIB_USB_HUB_PORT` — Hub port number (default 12 for 兴业银行-风飞格公司)
- `CIB_USB_HUB_ENABLED=0` — skip both `only` and `all_off` Hub calls
- `CIB_USB_HUB_SETTLE_SECONDS` — wait after enabling the UKey port (default 10s)
- `CIB_ALLOW_SUBMIT=1` — required to allow clicking 下一步/提交; also triggers the [0a] clean-startup sequence (close bank windows + USB all-off before opening the client)
- `CIB_SUBMIT_FINGERPRINT` — obsolete; current code prints fingerprint for audit only and does not require this variable.
- `CIB_LOGIN_CARD_COORD_FALLBACK=1` — opt-in to the legacy window-relative login coordinates when login-card detection fails (default: fail-closed)
- `CIB_LOGIN_COORD_FALLBACK=1` — opt-in to the legacy coordinate-based selection of the login name when UIA can't precisely match a candidate

## Editing Notes

- The hard-coded single-transfer payload lives in `DEFAULT_TRANSFER` at the top of `main_flow.py`. **It ships with placeholder values** (`acct_no="000000000000000"`, `bank="示例银行"`, etc.) — these are deliberately not real and must be replaced with the user-supplied fields before any real submit attempt. **Never** run with `CIB_ALLOW_SUBMIT=1` while the placeholder values are still in place. Required fields when populating: `label`, `amount`, `acct_no`, `acct_name`, `bank` (bank category), `branch_full` (full branch name from the source — used only by the fingerprint and as a search hint, not by confirmation hard-check), `branch_queries`, `purpose`. Search for `AMOUNT =`, `ACCT_NO =`, `ACCT_NAME =`, `BRANCH_FULL =`, `PURPOSE_TEXT =` only inside the `run_transfer_flow` body — those are read from the `transfer` dict (which defaults to `DEFAULT_TRANSFER`). When the payload changes, the printed audit fingerprint changes; it does not need to be copied into an environment variable.
- Chinese strings into form fields: pass `use_clipboard=True` to `fill_field_by_label`. ASCII/digits use `pyautogui.write` directly.
- The login-page password input uses `pyautogui.write` with a manual CapsLock toggle (`type_with_capslock` inside `run_transfer_flow`); the UKey-dialog input does not. Don't unify these without testing both.
- `close_bank_windows` filters by PID + title to avoid killing the script's own console — when adding new bank-window keywords, extend `_BANK_WINDOW_KEYWORDS`/`_TOOL_WINDOW_MARKERS` in `windows.py` rather than loosening the filter.
