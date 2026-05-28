# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## What this is

Playwright automation that opens Bank of China corporate net banking (`netc1.igtb.boc.cn`), drives the digital-certificate login flow with a U-shield, and navigates to 付款服务 → 转账汇款. `open_boc.py` is intentionally only a thin entry point now; implementation lives under `boc_automation/`.

Production-style use. Transfer-field prefill is disabled by default and only runs when `BOC_ENABLE_TRANSFER_FILL=1`. First-step order submission is separately gated by `BOC_ENABLE_ORDER_SUBMIT=1`; after clicking the bottom `提交` button the script must stop and never continue to confirmation/payment/authorization.

Companion `SKILL.md` registers this as the `中国银行单步支付` skill.

## Run

- Headless-of-console (recommended, browser only): double-click `启动.bat`, or `start "" pythonw open_boc.py`. The script writes stdout/stderr to `logs/boc.log` and a per-run `debug_runs/<run_id>/run.log`.
- Visible console (debugging): `python open_boc.py`. Console output remains visible and is also tee'd to the same log files.
- First-time deps: `pip install playwright interception` then `python -m playwright install chromium`. The `interception` package is optional but strongly preferred (see "Keyboard backend" below).

`.env` (next to `open_boc.py`) is loaded at startup. The main fields are:

- `BOC_LOGIN_PASSWORD` for the page-side 用户密码. Legacy `BOC_USHIELD_PASSWORD` is still accepted as a compatibility fallback for this value only.
- `BOC_USHIELD_PIN` plus `BOC_AUTO_USHIELD_PIN=1` to explicitly allow automatic U-shield PIN entry. By default, the user enters the U-shield PIN manually in the native dialog.
- `BOC_ENABLE_TRANSFER_FILL=1` to prefill the transfer form. Default is disabled. The payee fields come **only** from `BOC_PAYEE_ACCOUNT` / `BOC_PAYEE_NAME` / `BOC_PAYEE_BANK` / `BOC_PAYMENT_AMOUNT`; `boc_automation/transfer.py` no longer ships any real fallback default. If any of these is missing while fill is enabled, `_fill_transfer_form` is **fail-closed**: it prints the missing vars, skips filling, and does not submit (no real default is ever used).
- `BOC_ENABLE_ORDER_SUBMIT=1` to click the bottom first-step `提交` button after a successful fill. Default is disabled; use only for explicit low-value order-making tests.
- `BOC_CHROME_PROFILE` to select a non-Default Chrome profile for locating the certificate extension.
- `BOC_CLOSE_ON_FINISH=1` to close the Playwright browser, native BOC dialogs, and public BOC portal windows at the end of the flow; set `0` to keep the browser open for manual inspection.
- `BOC_USBHUB_CTRL_PATH`, `BOC_USBHUB_COM`, and `BOC_USBHUB_PORT=10` for the USB hub controller. On this machine BOC U-shield is on port 10.
- `BOC_USBHUB_POWER_ON_START=1` to power only the BOC U-shield port before launching the browser.
- `BOC_USBHUB_ALL_OFF_ON_FINISH=1` to power off every USBHub port after the browser/profile cleanup and then close possible U-shield removal notices.
- `BOC_ROUTE_RETRY_ON_FAILURE=1` is honored by the M3 `bank_route.py` caller, not `open_boc.py` itself: after a per-item failure, the router closes BOC browser/script/dialog residue, powers USBHub all-off, waits `BOC_ROUTE_RETRY_WAIT_SECONDS=15` seconds, and reruns the full item once before final reporting. The router also waits `BOC_ROUTE_BETWEEN_ITEMS_SECONDS=15` seconds between BOC items so the U-shield/browser control can release cleanly.
- `BOC_DEBUG_SCREENSHOTS=0` to disable the default desktop screenshots.
- `BOC_DEBUG_PAGE_SCREENSHOTS=0` to disable the default Playwright page screenshots.
- `BOC_ENABLE_EDGE_CERT_AUTO_SELECT=1` to opt in to the Edge client-cert auto-select policy. **Default is OFF.** When enabled, `edge_policy.py` writes a per-user `HKCU\Software\Policies\Microsoft\Edge\AutoSelectCertificateForUrls` policy scoped **only** to `netc1/netc2.igtb.boc.cn` and sets `PromptOnMultipleMatchingCertificates=0`; optionally narrowed by `BOC_CERT_ISSUER_CN` / `BOC_CERT_SUBJECT_CN` / `BOC_EDGE_CERT_POLICY_FILTER`. When off (default) no registry is written and the native certificate picker is used. To remove an already-written policy, delete the `9010/9011` values under that HKCU key.

## Hard rules

- **Never add automatic payment/confirmation/authorization.** The script's default contract is: navigate, log in, optionally fill required fields only behind the explicit `BOC_ENABLE_TRANSFER_FILL=1` gate, then either auto-clean (`BOC_CLOSE_ON_FINISH=1`) or idle until the user closes the window (`BOC_CLOSE_ON_FINISH=0`). First-step order submission is allowed only behind `BOC_ENABLE_ORDER_SUBMIT=1`; it may click the bottom `提交` once, then must stop.
- `TEST_PAYEE_ACCOUNT` / `TEST_PAYEE_NAME` / `TEST_PAYEE_BANK_NAME` / `TEST_AMOUNT` are now **empty unless** the matching `BOC_PAYEE_*` env vars are set — no real account/name/branch/行号 default is hard-coded. Never reintroduce a real fallback value. `BOC_ENABLE_ORDER_SUBMIT=1` is only for first-step order-making tests and must not click any later confirm/pay/auth action.
- Do not reintroduce blind U-shield PIN typing. Automatic U-shield PIN entry must stay behind `BOC_AUTO_USHIELD_PIN=1`, and failures must be visible in the log.
- The BOC site is sensitive to automation flags and translation overlays. Don't strip `--disable-blink-features=AutomationControlled`, `--disable-features=Translate,TranslateUI`, the `translate_*` blacklists in `_write_chrome_preferences`, or the `--lang=zh-CN` / `locale="zh-CN"` settings.
- All proxy env vars are deliberately cleared (`_disable_proxy_for_playwright`) and `--no-proxy-server` / `--proxy-server=direct://` is forced — BOC's certificate handshake breaks behind most proxies.

## Architecture

Entry point:

- `open_boc.py` only checks Windows, disables bytecode writing, imports `boc_automation.app.main`, and runs it.
- Do not move business logic back into `open_boc.py`; keep it as a stable launcher for `.bat`, shortcuts, and human memory.

Module map:

- `boc_automation/config.py` — paths, env loading, constants, debug/log locations, USBHub defaults.
- `boc_automation/artifacts.py` — long-term workspace hygiene: create `logs/`, move old root logs, prune old debug/batch artifacts, remove `__pycache__`.
- `boc_automation/app.py` — top-level orchestration and `main()`.
- `boc_automation/browser.py` — Chrome/Edge extension discovery, temp profile prep, Chrome Preferences.
- `boc_automation/debug.py` — tee logging, desktop screenshots, page screenshots, load waiting.
- `boc_automation/keyboard.py` — Interception and SendInput keyboard backends.
- `boc_automation/windows.py` — native certificate chooser, U-shield PIN dialog, public BOC portal cleanup, unplug notice cleanup.
- `boc_automation/login.py` — web login page and menu navigation.
- `boc_automation/transfer.py` — transfer form locating, filling, dropdown selection, verification.
- `boc_automation/usbhub.py` — USBHub port power on/off.

Top-to-bottom flow in `boc_automation.app.main()`:

1. `_prepare_chrome_extensions()` — locates the **BOC Certificate Application Extension** under Chrome or Edge profiles (`nhhdpdhiemjpkaikglglhabjafffdjfo` for Chrome, `cpiogedigcbdifgefmkjpfnampochfca` for Edge), picks the newest version subdir, and copies it into a fresh temp profile. The user must have this extension installed in Chrome/Edge already; the script aborts with `SystemExit` otherwise. The temp profile is `rmtree`'d on exit.
2. `_write_chrome_preferences()` writes a Chrome `Default/Preferences` JSON into the temp profile to suppress translate prompts on `netc1/netc2.igtb.boc.cn`.
3. `launch_persistent_context` with `--load-extension=...` (comma-joined version dirs) and `ignore_default_args=["--disable-extensions"]` is what actually makes the cert extension load — Playwright disables extensions by default. If the source extension is Edge, the script uses system `msedge.exe` through Playwright because bundled Chromium rejects this Edge manifest.
4. Login chain: install/check the narrow Edge `AutoSelectCertificateForUrls` client-certificate policy for `netc1/netc2.igtb.boc.cn` → `_click_certificate_login` → `_confirm_certificate_selection` (fallback UIA for the Windows/Edge certificate picker; it must verify the picker closed, otherwise the flow stops before U-shield PIN) → `_maybe_submit_ushield_password` (manual by default, automatic only with `BOC_AUTO_USHIELD_PIN=1`; falls back to the bank PIN dialog's own soft keyboard when Interception/SendInput is blocked) → `_fill_page_password_and_login` → `_click_payment_transfer` → optional `_fill_transfer_form` only with `BOC_ENABLE_TRANSFER_FILL=1` → optional `_submit_transfer_order` only with `BOC_ENABLE_ORDER_SUBMIT=1`. After first-step order submission, the script does not click any later confirm/pay/auth controls. After that the script either auto-cleans (`BOC_CLOSE_ON_FINISH=1`) or idles in `while context.browser.is_connected(): sleep(1)` until the user closes the window.

### Keyboard backend (important)

The native Chrome certificate chooser is an OS dialog outside the page DOM, so `page.keyboard.*` events do not reach it. `_send_enter` / `_send_ascii` route through, in priority order:

1. **Interception driver** (`interception` Python package + kernel driver). At import time the script scans `_g_context.devices[0..9]` for the first slot that is a real HID keyboard (HWID containing `HID\VID_`) and pins it with `_itc.set_devices(keyboard=...)`. If found, `_ITC_OK = True`.
2. **`SendInput` scancode fallback** via `user32.dll` (the `_KEYBDINPUT` / `_INPUT` ctypes structs). Some sites/dialogs ignore virtual-key events but accept scancodes, so this path always uses `KEYEVENTF_SCANCODE`.

Logged at startup as `[输入后端] Interception 驱动 ...` or `[输入后端] SendInput 回退（证书弹窗可能被拦截）`. If certificate-chooser confirmation regresses, this is the first place to look.

### Login-page detection

`_is_page_password_login_ready` distinguishes the two terminal states after the cert dialog: U-shield PIN is still pending, vs. the netc2 web 用户密码 page is up. `_maybe_submit_ushield_password` waits for manual PIN entry by default and only types the PIN when `BOC_AUTO_USHIELD_PIN=1`. Don't collapse these paths.

On this machine the Python `interception` package may be present while the kernel driver is not installed (`interception_create_context()` returns NULL), and BOC's PIN edit rejects SendInput/pyautogui text. Keep `_submit_ushield_pin_with_soft_keyboard`: it opens the native PIN dialog's randomized soft keyboard and clicks buttons by label, then clicks `确定`. It currently supports lowercase letters and digits, matching the test PIN shape.

The `收款人开户行行号` field is disabled and auto-filled after selecting the bank-branch dropdown option. Do not type into it. It is page-derived from whichever branch is selected from `收款人开户行名称`; a manually supplied or stale 行号 that does not match the page-selected branch causes false verification failures, so always read-confirm the auto-filled value rather than asserting a fixed one. Real payee/branch values come only from the `BOC_PAYEE_*` env vars (no real example is kept in this doc).

Transfer-form ordering is fragile. First clear `保存为常用收款人`, then fill account and let the page settle. BOC often shows a `账号|户名` common-payee candidate under `收款人户名`; click that candidate first, then accept any readback containing the target name and do not overwrite it. Only use the fuzzy-query candidate path when the name is still empty. Then choose `中行` for `中国银行...` payee branches and `他行` for other banks, fill/select the bank branch, and read-confirm the line number. After selecting the branch, only read-confirm the current bank type; do not click it again because the page can clear the bank name/line number. Selecting a branch can also clear the payee account, so always re-check/re-fill the account after branch/line-number linkage. The payee account must read back as the full expected account; a suffix/truncated display is a fail-closed mismatch, not success.

Bank-branch matching must tolerate page-expanded names. For example `招商银行杭州解放支行` can be returned as `招商银行股份有限公司杭州解放支行`, and `中信银行奥运村支行` can be returned as `中信银行北京奥运村支行`; normalize company suffixes and match by bank brand plus branch tail for dropdown matching and verification.

### DOM probing patterns

BOC pages render inside multiple frames and use Vue-style label-then-input layouts with `*` markers, so locators that work elsewhere often fail here:

- All "find X" helpers iterate `page.frames` and try multiple strategies (Playwright role/text → injected JS that walks visible candidates → coordinate fallback under a label). Keep this pattern when adding new fields.
- `_find_input_by_label` walks up to 6 ancestors from a label element looking for the first `input:not([type=hidden])` or `textarea`. `_click_radio_by_text` similarly hunts a sibling/ancestor `input[type=radio]`. `_fill_input_handle` types with a delay and dispatches `input`/`change`/`blur` to satisfy Vue change detection.
- After typing in `_fill_page_password_and_login`, the script clicks a role-exact `登录` button rather than pressing Enter, because the page also has a `密码登录` tab that text matching would hit.

## Logs

`logs/boc.log` is the canonical append-only log and starts each run with a `========== BOC open ==========` banner. Every run also creates `debug_runs/<run_id>/run.log`, `debug_runs/<run_id>/screenshots/`, and `debug_runs/<run_id>/page_screenshots/`. Each `_debug_checkpoint` prints `[排错] 第 NNN 步...` and captures a desktop screenshot; when a Playwright page is available it also captures a page screenshot. Desktop screenshots are important because certificate and U-shield PIN dialogs are native windows outside the page DOM.

Workspace hygiene is part of startup, not a one-off manual chore:

- Root-level legacy `boc.log` is moved into `logs/`.
- Root-level `batch_*.log` files are moved into `logs/batches/`.
- `debug_runs/` keeps the newest `BOC_KEEP_DEBUG_RUNS` directories, default `30`.
- `logs/batches/` keeps the newest `BOC_KEEP_BATCH_LOGS` files, default `30`.
- `__pycache__/` directories under the workspace are removed.
- `.gitignore` excludes `.env`, debug artifacts, logs, bytecode, and temporary files.

When system Edge loads the BOC extension, it can auto-open the public `www.boc.cn` portal after the initial pre-launch cleanup has already run. The app therefore cleans public BOC portal windows again after navigating the Playwright page to the iGTB login URL, then refocuses the Playwright page. `_dismiss_browser_chrome_tooltips()` also clears native Edge/Windows tooltip bubbles so desktop screenshots do not look like page errors.
