# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## What this directory is

`C:\Users\30112\Desktop\财务` is a Windows desktop GUI automation suite for four Chinese corporate banking clients: 招行 (CMB U-BANK), 兴业 (CIB enterprise online banking), 农业 (ABC), and 中行 (BOC). Each bank has two separate sub-projects under its folder:

- a **制单 / 单笔转账** project that drives a real maker-entry transfer flow
- a **查询X余额** project that only reads the account balance

`公共/usbhub` holds the shared `hub_ctrl.py` (绿精灵 USB Hub controller via pyserial); the制单/balance projects import it from `USB_HUB_CTRL_DIR` (default `C:\Users\30112\Desktop\财务\公共\usbhub\usbhub\多口USB控制器软件以及驱动`).

**USBHub vendor binaries are not committed.** Only `hub_ctrl.py` is in-tree. The CH341 serial driver (`公共/usbhub/usbhub/CH341SER/`), the vendor "第一步驱动包" (`CH341SER.exe`), and the vendor "第二步软件" (`usbHubCtrl.exe` + `Qt*.dll` + `vc_redist.x64.exe`) are kept locally on this machine but `.gitignore`-excluded — they are not needed by the Python flow. On a new machine, install the corresponding vendor driver / control software manually at the same paths; do not commit vendor `log.txt`, installers, or any `*.dll` / `*.exe` / `*.sys` / `*.cat` / `*.inf` binaries.

There is **no top-level build, no test suite, no linter, no package manager**. Each sub-project is an independent Python tree with its own `.env`, `requirements.txt` (when needed), entry scripts, and `SKILL.md` / `AGENTS.md` / `AGENTS.md` / `README.md`. Always read the specific project's docs before touching it; this file only covers what is true across the whole suite.

## System boundary

This suite only automates **maker-entry (制单 / 经办录入)** into each bank's client. It does **not** perform final review, authorization, confirmation, or payment / disbursement.

- **Default = test / verification mode.** With no production entry or production env var chosen, runs use placeholder data, test amounts, fill-only / dry-run, and screenshots — no real voucher is produced.
- **Production voucher mode.** Entered only when the operator explicitly selects a production entry or sets a production env var (`ZHIDAN_TEST_MODE=0` / `CIB_ALLOW_SUBMIT=1` / `BOC_ENABLE_ORDER_SUBMIT=1`; ABC is also a 制单 project — it requires `ABC_ALLOW_SUBMIT_ONCE=true` for single/per-record voucher submission, with its confirm-modal / trade-info / transfer-K宝-password / physical-OK-servo steps each behind their own explicit gate; the OK servo fires only **after** the transaction K宝 password, never early, never default-on). In that mode the flow completes 经办 / 制单 / 提交 **into the bank's own pending-review (待审核 / 待复核) maker queue and then stops.**
- "Submit" here is **not** "pay". The script never auto-reviews, auto-authorizes, auto-confirms, or makes final payment / disbursement — review, authorization, and payment remain with the human operator and the bank's own approval workflow.
- Do not add any automatic review / authorize / confirm / final-pay path, do not bypass the production gates, and do not preset those gates to on in source defaults, `.bat`, or tracked files.

## Read order before doing anything

1. `银行自动化项目总览.md` — the master index. It maps each project to its purpose, main entry, safety boundary, USBHub rules, and submit gates. **If you change a project's purpose, entry script, run command, USBHub port, log/screenshot paths, cleanup policy, safety switch, or submit boundary, you must also update this overview in the same change.**
2. The target project's own `SKILL.md` / `AGENTS.md` / `AGENTS.md` / `README.md` — these are the source of truth for that project's specifics.
3. `自动报错上报说明.md` — describes the optional Codex error-reporting gateway used by the `*_带Codex上报.bat` launchers (gateway lives at `C:\Users\30112\Desktop\网关`).

Migration note: code still tolerates both the new `C:\Users\30112\Desktop\财务\...` paths and the legacy `C:\Users\30112\Desktop\...` paths. **Do not add new hard-coded legacy desktop paths**; prefer project-relative paths, environment variables, or the locations listed in the overview.

## Project map

| Project | Entry | Submit gate (env var, default OFF) | Notes |
| --- | --- | --- | --- |
| 招行制单 (`招行/招行制单`) | `run_zhidan.bat` or `招行/skills/制单单账号单笔转账skill/制单.py` | `ZHIDAN_TEST_MODE=0` to enable the second 经办 click. Default `1` = test mode (first 经办 only). | UKey selected by payer-name → USBHub-port map. `USB_HUB_FORCE_PORT` requires `ZHIDAN_ALLOW_FORCE_USB_PORT=1` and is **test-runner only**. |
| 兴业制单 (`兴业/兴业制单`) | `open_bank.py` / `batch_fill_only.py` / `login_only.py` | `CIB_ALLOW_SUBMIT=1` to allow 下一步/提交. `CIB_SUBMIT_FINGERPRINT` is no longer a gate — the fingerprint is printed for audit only. | `DEFAULT_TRANSFER` in `cib_open_bank/main_flow.py` ships with **placeholder data** — replace before any real submit. `fill_only=True` / `stop_after_login=True` are the de-risk paths. |
| 农业银行 (`农业/农业银行`) | `skills/单笔转账/open_browser.py` or `run_abc_batch_local.ps1`; `press_ok_servo.py --press` only tests the external OK servo | Default = fill-only (no submit). Production voucher requires `ABC_ALLOW_SUBMIT_ONCE=true` (single/per-record); the page-submit confirm modals, trade-info confirm, transfer K宝 password and physical OK servo are each behind their own explicit gate (`ABC_CONFIRM_SUBMIT_MODAL_ONCE` / `ABC_CONFIRM_TRADE_INFO_ONCE` / `ABC_TRANSFER_KB_PASSWORD_AFTER_TRADE` / `ABC_OK_SERVO_ENABLE`+`ABC_OK_SERVO_AFTER_TRANSFER_KB_PASSWORD`). OK servo only fires **after** the transaction K宝 password — never early, never default-on. Submits into the bank's pending-review queue, then stops; no auto review/authorize/pay. | Playwright headful + pyautogui for native cert dialog and K宝 PIN. K宝 PIN reads from `.env` only when `ALLOW_INSECURE_ENV_PASSWORD=true`; otherwise prompted via `getpass`. M3 农行路由按付款单位注入 UKey 物理口：蒙特 11、蒙选 13、巡鲜 14、得鲜 15；external OK clicker port 30. Optional external servo uses a lock-key hotkey; prefer ScrollLock over CapsLock. |
| 中国银行 (`中行/中国银行`) | `启动.bat` / `open_boc.py` / `batch_run_screenshots.py` | `BOC_ENABLE_TRANSFER_FILL=1` to prefill required fields; `BOC_ENABLE_ORDER_SUBMIT=1` to click the bottom `提交` **once and stop**. `BOC_AUTO_USHIELD_PIN=1` to auto-enter the U-shield PIN. | USBHub port 10. BOC **never goes through a proxy** — `_disable_proxy_for_playwright` is load-bearing. |
| 查询招行余额 (`招行/查询招行余额`) | `run_query_balance.bat` or `scripts/query_balance.py` | Read-only. Must not set `ZHIDAN_TEST_MODE=0`. | Reuses `招行/招行制单/招行/ubank_common.py` for login. |
| 查询兴业余额 (`兴业/查询兴业余额`) | `run_query_balance.bat` or `scripts/query_balance.py` | Read-only. Must not set `CIB_ALLOW_SUBMIT=1`. Must call `run_transfer_flow(stop_after_login=True)` only. | Always finishes with `close_bank_windows()` + `all_off_usb_hub_ports()`. |
| 查询农业余额 (`农业/查询农业余额`) | `run_query_balance.bat` or `scripts/query_balance.py` | Read-only. May click the `余额` row `显示` button, must not enter transfer pages. | ABC UKey physical port must match the queried company; M3 hard-codes 蒙特 11、蒙选 13、巡鲜 14、得鲜 15. `ABC_USB12_ALL_OFF_AFTER_RUN=true` by default. |
| 查询中行余额 (`中行/查询中行余额`) | `run_query_balance.bat` or `scripts/query_balance.py` | Read-only. May log in, auto-enter U盾 PIN (read-only flow needs it), must not enter 付款服务/转账汇款. | USBHub port 10. Closes browser, deletes temp profile, USBHub `all-off` on exit. |

Each balance-query project intentionally reuses the matching 制单 project's `.env`, login helpers, and USB Hub config — do not duplicate credentials.

## Cross-cutting safety rules

These rules apply across every sub-project. Project-specific docs may tighten them but never loosen.

- **Fail closed on resource/transfer ambiguity.** No payer match, no `bank_form.json` (招行), placeholder `DEFAULT_TRANSFER` still in place (兴业), invalid amount, unmapped UKey port, or unexpected page state → abort before opening the bank, or before clicking 经办/下一步/提交. Each制单 project has explicit exit codes for these states (see e.g. 招行 `SKILL.md` exit-code table).
- **Submission is always env-var-gated.** Default behavior of every制单 entry point is non-submitting. Real submission requires an explicit env var (`ZHIDAN_TEST_MODE=0` / `CIB_ALLOW_SUBMIT=1` / `BOC_ENABLE_ORDER_SUBMIT=1`; ABC is also a 制单 project — it needs `ABC_ALLOW_SUBMIT_ONCE=true` plus its chained `ABC_CONFIRM_SUBMIT_MODAL_ONCE` / `ABC_CONFIRM_TRADE_INFO_ONCE` / `ABC_TRANSFER_KB_PASSWORD_AFTER_TRADE` / `ABC_OK_SERVO_ENABLE` / `ABC_OK_SERVO_AFTER_TRANSFER_KB_PASSWORD`, with the physical OK servo only after the transaction K宝 password). The M3 production route (`M3_PRODUCTION_MODE=1`) applies these per-bank gates automatically, ABC included; the M3 test/verification route keeps ABC fill-only. Real submission means **maker-entry into the bank's own pending-review (待审核/待复核) queue, then stop — it is not final review, authorization, confirmation, or payment**, which stay with the human operator and the bank's approval workflow. Never silently bypass these gates, add a new submit path that isn't gated, or add any auto review/authorize/confirm/final-pay step.
- **BOC must stop after the first-step bottom `提交`.** No automatic confirm/pay/authorize. This is a hard rule encoded in `中行/中国银行/AGENTS.md` and `AGENTS.md`.
- **Balance-query projects are read-only.** No transfer/payment/order clicks, no `经办`, no setting any production submit env var.
- **24h unattended hardening (M3 route).** `M3直供合同付款数据获取` adds a per-bank subprocess timeout (`BANK_ROUTE_PROCESS_TIMEOUT_SECONDS`, default 900s → exit 124 + per-bank cleanup; CMB/CIB have no dedicated cleanup, only logged) — **exit 124 is never auto-retried (ABC/BOC included)**: timeout may land in the grey zone where the voucher was already submitted into the bank's pending-review queue but the script didn't exit cleanly, so it is kept failed and requires manual review of that bank's pending-review queue before any re-run; other business exit codes keep their existing retry behavior unchanged. Crash recovery (`--resume-existing` / `M3_MONITOR_RESUME_EXISTING=1`, re-runs only interrupted `running` items, never bulk-replays history), a watchdog `run_auto_extract_dispatch_生产_watchdog.ps1` (respects the monitor lock, never blind `--force-lock`), and an optional production cap `M3_MAX_PAYMENT_AMOUNT` (empty = format-check only, set → over-cap fail-closed). The boundary is unchanged: still no auto review/authorize/confirm/final payment. Full detail in that project's `SKILL.md` and `银行自动化项目总览.md`.
- **UKey selection.** Production must match the UKey to the payer (招行: payer-name → USBHub port map; 兴业: configured port, currently 17; 农业 M3 route: payer-name → ABC USBHub port map; 中行: port 10 unless reconfigured). Forced-port overrides exist only as test-runner affordances and require a paired safety env var (e.g. `ZHIDAN_ALLOW_FORCE_USB_PORT=1`). After any run — success or failure — turn the USB Hub off (`all-off`) unless the project's docs explicitly say to leave it on (e.g. `login_only.py`). Current hard-coded 30-port table: CMB 云链 1-9 (`跳动/跃动/悠动/智动/逸动/灵动/讯动/慧动/炫动`), BOC 河南讯动/迅动 10, ABC 蒙特 11 / 蒙选 13 / 巡鲜 14 / 得鲜 15, CMB 蒙特 12 / 得鲜 16 / 来参缘 18 / 云炫农 19, CIB 来参缘 17, external ABC OK clicker 30. 来参缘 CMB 001/002 shares one UKey and switches login name on the login window; 得鲜 CMB 001/002 shares one UKey and switches payer account on the transfer form. This physical USBHub table is separate from M3 routing: M3 still routes 来参缘→兴业 and 得鲜/蒙特→农行 unless an explicit bank field says otherwise.
- **Credentials.** Login passwords / UKey PINs / cert passwords stay in each project's `.env` (e.g. `招行/招行制单/招行/.env`, `兴业/兴业制单/.env`, `农业/农业银行/.env`, `中行/中国银行/.env`). Never print, copy into chat, or write into shared docs. The ABC K宝 password reads from `.env` only when `ALLOW_INSECURE_ENV_PASSWORD=true`; default is `getpass.getpass()` at runtime.
- **`*_带Codex上报.bat` launchers** wrap the normal entry through `C:\Users\30112\Desktop\网关\scripts\run_and_report.py` so failed runs are reported to the Codex gateway (requires the gateway to be running and a prior Feishu/飞书 message to the bot so it knows the destination chat). The plain `.bat` siblings do not auto-report — use them if you don't want gateway reporting.

## Operating environment

- Windows 11, 1920×1080, **display scaling 100%**. Fixed-coordinate fallbacks elsewhere were measured at 125% and scaled at runtime via `COORD_SCALE = TARGET_DPI_SCALE / SOURCE_DPI_SCALE`; override `CIB_SOURCE_DPI_SCALE` / `CIB_TARGET_DPI_SCALE` (or the matching variables in other projects) rather than rewriting coordinates.
- Bank client shortcuts are expected on the user's desktop (paths overridable per project, e.g. `CIB_SHORTCUT_PATH`).
- Current 30-port USB Hub controller is `COM3` by default in `hub_ctrl.py`; override with `USB_HUB_COM` / `CIB_USB_HUB_COM` / `BOC_USBHUB_COM` / `ABC_USB_HUB_COM`. `hub_ctrl.py::PORT_BIT` maps physical ports 1–30; business UKey ownership is a separate table and must not be confused with M3 local account rule ids (for example ids 11+ in `m3_company_bank_accounts.local.json` are not USBHub ports).
- BOC always runs direct — proxy env vars are wiped at process startup; do not add proxy fallbacks for BOC even if a different bank's machine config goes through a proxy.

## How to "verify" without running the live flow

There is no test suite. Use the non-destructive checks each project documents — typically:

```powershell
python -m py_compile <module>.py
python -m compileall <project_dir>
```

For 兴业, also:

```powershell
python -c "import pyautogui, pygetwindow, win32gui, win32api, pywinauto, serial; print('imports ok')"
```

**Pre-commit sensitive scan** (read-only; not opening any bank / USB Hub):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  "C:\Users\30112\Desktop\财务\公共\maintenance\run_sensitive_precommit_scan.ps1"
```

Walks 财务 / M3直供合同付款数据获取 / 网关 三个根，逐条 `git check-ignore` 验证常见敏感产物（凭据、本地真实配置、运行截图、批量队列 JSON、USB Hub 厂商日志等）被覆盖；再用 `git ls-files --others --exclude-standard` 找未跟踪 + 命中敏感模式的文件；可选 rg + PowerShell 两阶段敏感值扫描：rg 抓凭据键名 + `=`/`:` 赋值候选与 16-24 位长数字串，PowerShell 后处理只对"真像 secret 的值"判 FAIL —— ellipsis / 中文描述（如 `网盾密码 / 登录页密码 / U盾PIN`） / 布尔 / `os.environ.get(...)` / `$env:` / `placeholder` / `示例` / `占位` / `REDACTED` 等全部走 allowlist；URL `portalId / rptDesignId / formmain_*_id / extendParams / resourceCode` 等长 ID 也放行。rg 任何异常退出码转 WARN 不打断主流程；`-SelfTest` 跑占位 + fake-real 用例自检。脚本绝不在源码硬编码真实账号 / 户名 / 支行 / 金额 / 密码；任一 FAIL → 退出码 1。详见 `银行自动化项目总览.md` 的 "敏感产物入库预检" 段。

Real verification is **inspecting the artifacts** each run produces:

- 招行制单: `招行/招行制单/招行/screenshots/batch_yyyyMMdd_HHmmss/` (with `summary.json`, per-item `NN_screenshots`, logs). Cleanup via `cleanup_generated_artifacts.ps1` (dry-run, then `-Apply`).
- 兴业制单: `screenshots/`.
- 农业银行: `artifacts/单笔转账/debug_runs/<RUN_ID>/run.log`, optional `screenshots/`, optional `live_screenshots/` (live screenshots leak account numbers/amounts — off by default).
- 中国银行: `logs/boc.log`, `debug_runs/<run_id>/run.log`, `debug_runs/<run_id>/screenshots/`, `debug_runs/<run_id>/page_screenshots/`. Workspace hygiene (move legacy logs, prune old `debug_runs/` keeping `BOC_KEEP_DEBUG_RUNS=30`) runs automatically on startup.
- Balance-query projects: `runs/balance_yyyyMMdd_HHmmss/balance.json` + `screen.png` + (中行) `run.log`.
- Cross-project cleanup: `公共/maintenance/cleanup_after_automation.bat` / `.ps1` is called by the automation launchers after each run and preserves the original bank-flow exit code. The underlying `cleanup_bank_artifacts.ps1` is dry-run by default and uses `-Apply` from automation收尾 and the daily scheduled task `财务银行运行产物清理` (03:30) to prune old screenshots/debug runs with a 6-hour new-file protection window.

## When making changes

- Touch the smallest project necessary. If a fix belongs in a single bank's flow, don't lift it into shared code unless multiple banks already need it. There is no shared Python package — only `公共/usbhub` and the cross-project imports the balance-query projects do back into their制单 sibling.
- Preserve the de-risk paths in 兴业 (`fill_only=True`, `stop_after_login=True`) and the gating env vars everywhere. These are the contract that keeps an accidental run from submitting.
- After editing a project's contract, sync `银行自动化项目总览.md` in the same change. The overview is the single document that an outside operator — Codex, Codex, or a human — will look at first to decide which project to use and what it will actually do.
- Source files are UTF-8 with Chinese identifiers and string literals (skill and folder names are Chinese). When writing files via PowerShell, use `-Encoding utf8` explicitly — Windows PowerShell 5.1's default is UTF-16 LE.
