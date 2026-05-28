---
name: 中国银行单步支付
description: 使用 Python/Playwright 打开中国银行企业网银，加载中行证书扩展，处理 U盾/证书登录，可停在登录后首页，也可进入「付款服务」→「转账汇款」。默认只填表不提交；资金字段和制单提交都必须用显式风险开关开启，并为每一步保存日志和截图。
---

# 中国银行单步支付

> 财务项目总览优先看 `C:\Users\30112\Desktop\财务\银行自动化项目总览.md`；迁移期旧总览在 `C:\Users\30112\Desktop\银行自动化项目总览.md`。如果本项目的用途、入口、风险开关、安全边界、USBHub 规则、日志/截图路径发生变化，必须同步更新总览。

这是 `C:\Users\30112\Desktop\财务\中行\中国银行` 项目的接手说明。看到本文件和项目代码时，优先按这里的规则行动。本文件必须跟项目最新代码同步；改主流程、清理策略、日志路径、USBHub、证书/PIN 或转账表单逻辑后，都要顺手更新这里。

本项目是一个 Windows 自动化：`open_boc.py` 只是稳定入口，实际代码在 `boc_automation/` 包里。它用 Playwright 打开中国银行企业网银 `https://netc1.igtb.boc.cn/`，加载本机 Chrome/Edge 中的 **BOC Certificate Application Extension**，通过 U盾数字证书登录。默认完整流程会登录后导航到「付款服务」→「转账汇款」；调试时可以用 `python open_boc.py --open-login` 只打开登录页并保持浏览器打开，或用 `python open_boc.py --login-only` 完成登录后停在首页。脚本可以按截图/环境变量预填收款信息和金额；默认不提交。只有显式设置 `BOC_ENABLE_ORDER_SUBMIT=1` 时，才会滚到底部点击第一步「提交」做制单测试，并在提交后停止，不继续确认、支付或授权。

## 最新状态

- 当前是「薄入口 + 模块包」结构，不再是单文件脚本。`open_boc.py` 只负责启动，所有业务逻辑在 `boc_automation/`。
- 根目录保持干净：运行日志在 `logs/`，批量日志在 `logs/batches/`，单轮截图和单轮日志在 `debug_runs/<run_id>/`。
- 每次启动都会执行 `prepare_artifact_layout()`：迁移旧根目录日志、裁剪旧 debug 目录、裁剪旧批量日志、删除 `__pycache__`。
- `open_boc.py` 退出时会调用 `C:\Users\30112\Desktop\财务\公共\maintenance\cleanup_after_automation.ps1` 做跨项目运行产物收尾清理；该清理不改变中行业务流程退出结果，6 小时内的新产物会跳过。
- 启动前只开启 USBHub 10 口；结束后默认 USBHub all-off，并关闭 U盾拔出/温馨提示窗口。
- 系统 Edge/中行扩展可能在浏览器启动后自动打开 `www.boc.cn` 公网页。脚本会在启动前、登录页 `goto()` 后、页面加载后、收尾时多次清理，并把 Playwright 企业网银页拉回前台。
- Edge/Windows 原生 `Microsoft Edge` 白色提示气泡不是网页错误。脚本用 `_dismiss_browser_chrome_tooltips()` 按 Esc 并移动鼠标，避免气泡挡住调试截图。
- 制单提交是独立风险开关：`BOC_ENABLE_ORDER_SUBMIT=1`。默认关闭；开启后只点击表单底部第一个红色「提交」，提交后截图并停止。
- 登录页停止是独立流程模式：`python open_boc.py --open-login` 只打开企业网银登录页，不点击「数字证书登录」、不输入 U盾 PIN、不会输入网页登录密码；命令行模式默认保持浏览器打开，并且不触碰 USBHub。
- 登录后停止是独立流程模式：`python open_boc.py --login-only` 会在网页登录成功、登录后页面等待完成后停止，不进入付款服务/转账汇款/填表/制单，并默认保持浏览器打开。自动化场景也可用 `.env` 设置 `BOC_STOP_AFTER_LOGIN=1` 或 `BOC_RUN_UNTIL=login`；这种方式是否关闭浏览器仍由 `BOC_CLOSE_ON_FINISH` 决定。
- 中国银行脚本永远不走代理。`boc_automation/proxy.py` 会清理进程代理，浏览器启动参数也强制 no-proxy。启动前会做直连预检：如果 `netc1.igtb.boc.cn` / `netc2.igtb.boc.cn` 解析到 `198.18.0.0/15` fake-ip，或 TCP 443 直连不通，脚本必须停止，不能通过代理继续。若机器存在 CodexProxy / Meta Tunnel / fake-ip DNS，但本地国内网关 DNS 能返回真实中行 IP，可显式设置 `BOC_DIRECT_DNS_SERVER=<国内网关DNS>`（本机示例为 `192.168.8.1`）；脚本会用该 DNS 重新解析中行域名，并通过 Chromium `--host-resolver-rules` 固定到真实 IP，仍保持 `direct://` / no-proxy，不走 HTTP 代理。
- 客户端证书自动选择策略 **默认关闭**（安全默认）。仅当操作人显式设置 `BOC_ENABLE_EDGE_CERT_AUTO_SELECT=1` 时，`edge_policy.py` 才会向**当前用户**注册表 `HKCU\Software\Policies\Microsoft\Edge\AutoSelectCertificateForUrls` 写入策略，**边界仅限**中行 `netc1/netc2.igtb.boc.cn` 两个 URL，并把 `PromptOnMultipleMatchingCertificates` 置 0（不再多证书提示）；可选 `BOC_CERT_ISSUER_CN` / `BOC_CERT_SUBJECT_CN` / `BOC_EDGE_CERT_POLICY_FILTER` 收窄过滤。不开启时不写任何注册表，回退到 Windows 原生证书选择框由人工选证书。如何关闭：不设或设 `BOC_ENABLE_EDGE_CERT_AUTO_SELECT=0`（默认即此）；如何清除已写策略：删除上述 HKCU 注册表项下 `9010/9011` 值。若证书选择框仍出现，脚本必须确认弹窗已关闭才继续，确认失败要停止后续 U盾 PIN、网页登录和填表。
- 由 M3 `bank_route.py` 调用中行时，默认 `BOC_ROUTE_RETRY_ON_FAILURE=1`：单条完整流程失败后先关闭中行页面/脚本和原生弹窗、USBHub all-off，等待 `BOC_ROUTE_RETRY_WAIT_SECONDS=15` 秒后完整重跑一次；每条之间默认等待 `BOC_ROUTE_BETWEEN_ITEMS_SECONDS=15` 秒释放 UKey/浏览器控件；第二次仍失败才进入最终失败/网关上报路径。
- 2026-05-11 批量 10 条曾跑通；后续若改登录、PIN、开户行匹配、清理或 USBHub，要重新跑相关轮次验证。

## 最快接手

1. 先读这几个位置：
   - `open_boc.py`：只负责作为入口，不放业务逻辑。
   - `boc_automation/app.py`：主流程编排。
   - `boc_automation/transfer.py`：转账表单预填和复核。
   - `boc_automation/windows.py`：证书、U盾 PIN、官网残留页、拔出提示等原生窗口处理。
   - `boc_automation/artifacts.py`：日志、截图、缓存的长期整理策略。
   - `.env`：本机运行配置、密码/PIN、USBHub 配置、默认收款测试值。
   - `batch_run_screenshots.py`：从截图整理出的 10 条批量测试数据。
2. 单次调试用：
   - `python open_boc.py`
3. 只登录到登录后首页、给人工确认页面用：
   - `python open_boc.py --login-only`
   - 这个命令行模式会自动保持浏览器打开；关闭窗口或在控制台按 `Ctrl+C` 后，脚本才会进入 finally 清理、关闭 USBHub。
4. 只打开企业网银登录页、不碰 U盾/证书/密码：
   - `python open_boc.py --open-login`
   - 这个模式不会开启 USBHub 口，适合只看登录页面或确认扩展/浏览器启动状态。
5. 正常无控制台运行用：
   - 双击 `启动.bat`
   - 或 `start "" pythonw open_boc.py`
6. 跑截图批量测试用：
   - 全部 10 条：`python batch_run_screenshots.py`
   - 只跑指定序号：`python batch_run_screenshots.py 3 7 8`
7. 看结果不要只看控制台。以 `logs/boc.log`、`debug_runs/<run_id>/run.log` 和 `page_screenshots/` 为准。
8. 如果桌面残留浏览器、官网页、U盾拔出提示，不要靠手工清理凑结果；把清理逻辑写回 `boc_automation/windows.py` 或主流程的 finally。

## 当前机器约定

- 系统：Windows。
- 中行 U盾：USBHub 第 `10` 口。
- USBHub 串口：`COM3`（30 口新 Hub）。
- USBHub 控制脚本默认位置：
  - `C:\Users\30112\Desktop\财务\公共\usbhub\usbhub\多口USB控制器软件以及驱动\hub_ctrl.py`
- 启动前默认只开启 10 口。
- 收尾默认关闭所有 USBHub 口，并关闭可能出现的 U盾拔出/温馨提示窗口。
- U盾插入或系统 Edge 加载中行扩展后，可能自动打开一个中国银行官网 Edge 页面；脚本必须在启动前、登录页打开后、收尾时都自动清理这个官网页，只保留本次 Playwright 驱动的企业网银页面。
- 如果证书登录失败打开 `loginCAFailSolution.html` / `常见解决方案` 页面，也必须在下一轮开始前和失败收尾时关闭；否则下一轮 UIA 会误看见失败页而不是证书选择框。
- 银行网络必须是真实直连 DNS + 真实直连 TCP。当前若看到 `Meta Tunnel` 网卡、DNS/网关为 `198.18.x.x`，或 `netc1.igtb.boc.cn` 解析成 `198.18.x.x`，脚本不应直接使用系统 DNS 继续跑。优先处理方式是关闭 TUN/fake-ip，或在代理客户端里把 `*.igtb.boc.cn`、`*.boc.cn` 配成 DIRECT 且使用真实 DNS，并确认当前 Wi‑Fi/网络允许直连。若这台机器必须保留 CodexProxy / Meta Tunnel，可先确认国内网关 DNS 能返回真实 IP：`Resolve-DnsName netc1.igtb.boc.cn -Server 192.168.8.1`；确认不是 `198.18.x.x` 后，在当前进程显式设置 `BOC_DIRECT_DNS_SERVER=192.168.8.1` 再运行。该方式只影响中行脚本当前进程，不改系统路由、不写 hosts、不改 `.env`。

## 依赖

最少需要：

```powershell
pip install playwright pywinauto pywin32 pyautogui pillow interception
python -m playwright install chromium
```

说明：

- `playwright`：驱动企业网银页面。
- `pywinauto` / `pywin32` / `pyautogui`：处理证书选择、U盾 PIN 原生窗口、截图、关闭残留窗口。
- `pillow`：桌面截图/图片处理。
- `interception`：可选但推荐，用于更可靠地向原生证书/PIN 弹窗发送键盘事件；若驱动不可用，脚本会回退到 SendInput 或中行 PIN 软键盘。

## `.env` 配置

`.env` 放在 `open_boc.py` 同目录，脚本启动时会读取。常用配置如下：

```dotenv
BOC_LOGIN_PASSWORD=网页登录密码
BOC_USHIELD_PIN=U盾PIN
BOC_AUTO_USHIELD_PIN=1

BOC_ENABLE_TRANSFER_FILL=1
BOC_ENABLE_ORDER_SUBMIT=0
BOC_STOP_AT_LOGIN_PAGE=0
BOC_STOP_AFTER_LOGIN=0
BOC_CLOSE_ON_FINISH=1

BOC_USBHUB_CTRL_PATH=C:\Users\30112\Desktop\财务\公共\usbhub\usbhub\多口USB控制器软件以及驱动\hub_ctrl.py
BOC_USBHUB_COM=COM3
BOC_USBHUB_PORT=10
BOC_USBHUB_POWER_ON_START=1
BOC_USBHUB_ALL_OFF_ON_FINISH=1
BOC_USBHUB_SETTLE_SECONDS=8
BOC_ROUTE_RETRY_ON_FAILURE=1
BOC_ROUTE_RETRY_WAIT_SECONDS=15
BOC_ROUTE_BETWEEN_ITEMS_SECONDS=15

BOC_PAYEE_NAME=收款人户名
BOC_PAYEE_ACCOUNT=收款账号
BOC_PAYEE_BANK=收款人开户行名称
BOC_PAYEE_BANK_CODE=
BOC_PAYMENT_AMOUNT=0.01

BOC_KEEP_DEBUG_RUNS=30
BOC_KEEP_BATCH_LOGS=30
```

配置语义：

- `BOC_LOGIN_PASSWORD`：网页里的「用户密码」。
- `BOC_USHIELD_PIN`：U盾 PIN。
- `BOC_USHIELD_PASSWORD`：旧变量，只能作为 `BOC_LOGIN_PASSWORD` 的兼容回退，不要再用它表示 U盾 PIN。
- `BOC_AUTO_USHIELD_PIN=1`：允许自动输入 U盾 PIN。不开这个就等待人工输入。
- `BOC_ENABLE_TRANSFER_FILL=1`：允许预填转账表单。不开这个只登录并进入转账页。
- `BOC_ENABLE_ORDER_SUBMIT=1`：允许滚到底部点击第一步「提交」做制单测试。默认关闭；提交后只截图/记录状态，不继续点确认、支付、授权。
- `BOC_STOP_AT_LOGIN_PAGE=1`：只打开登录页，不点击证书登录、不输入 U盾 PIN/网页登录密码。该模式默认不触碰 USBHub。
- `BOC_STOP_AFTER_LOGIN=1`：网页登录成功后停止，不进入付款服务/转账汇款/填表/制单；是否关闭浏览器由 `BOC_CLOSE_ON_FINISH` 决定。
- `BOC_RUN_UNTIL=login-page`：同 `BOC_STOP_AT_LOGIN_PAGE=1`。
- `BOC_RUN_UNTIL=login`：同 `BOC_STOP_AFTER_LOGIN=1`。可读性更强，但不要和其它停止开关混着用。
- `BOC_CLOSE_ON_FINISH=1`：主流程结束后自动关闭本次浏览器、官网残留页、原生弹窗和 USBHub。
- `BOC_ROUTE_RETRY_ON_FAILURE=1`：M3 路由层使用；单条完整流程失败后清理浏览器/弹窗/USBHub 并完整重跑一次，设为 `0` 才关闭该兜底。
- `BOC_ROUTE_RETRY_WAIT_SECONDS=15` / `BOC_ROUTE_BETWEEN_ITEMS_SECONDS=15`：M3 路由层使用；避免连续登录太快导致中行控件报 USBKey 证书检测失败。
- `BOC_DEBUG_SCREENSHOTS=0`：关闭桌面截图，默认开启。
- `BOC_DEBUG_PAGE_SCREENSHOTS=0`：关闭 Playwright 页面截图，默认开启。
- `BOC_CHROME_PROFILE=Default`：指定 Chrome/Edge profile，默认 `Default`。
- `BOC_PAYEE_*` / `BOC_PAYMENT_AMOUNT`：单次运行的收款测试数据。批量脚本会覆盖这些值。
- `BOC_PAYEE_TYPE=单位|个人`：收款人类型，默认 `单位`；M3 自然人收款样本要传 `个人`。
- `BOC_KEEP_DEBUG_RUNS=30`：长期保留最近 30 个 `debug_runs/<run_id>`，旧目录启动时自动删除。
- `BOC_KEEP_BATCH_LOGS=30`：长期保留最近 30 个批量日志，旧日志启动时自动删除。

`BOC_PAYEE_BANK_CODE` 留空时，脚本不会硬填行号，只要求页面在选择开户行后自动带出非空行号。这是目前推荐方式，因为 `收款人开户行行号` 是禁用输入框。

## 代码地图

`open_boc.py` 是薄入口，保持很小：

- 检查系统必须是 Windows。
- 设置 `sys.dont_write_bytecode = True`，减少 `__pycache__` 垃圾。
- 导入并调用 `boc_automation.app.main()`。

不要再把主流程或定位代码塞回 `open_boc.py`。

模块职责：

- `boc_automation/config.py`：环境变量、路径、常量、日志和 debug 目录、USBHub 默认值。
- `boc_automation/artifacts.py`：启动时创建 `logs/`、迁移旧根目录日志、裁剪旧 debug 目录、删除 `__pycache__`。
- `boc_automation/app.py`：主流程编排和 finally 清理。
- `boc_automation/browser.py`：中行证书扩展查找、临时 profile、Chrome Preferences、Edge 可执行文件查找。
- `boc_automation/debug.py`：总日志/单次日志 tee、桌面截图、页面截图、加载等待。
- `boc_automation/keyboard.py`：Interception 和 SendInput 键盘后端。
- `boc_automation/windows.py`：证书选择框、U盾 PIN 原生窗口、中行官网残留页、U盾拔出提示。
- `boc_automation/login.py`：网页登录、证书登录按钮、登录密码、菜单导航。
- `boc_automation/transfer.py`：转账字段定位、输入、开户行候选匹配、最终复核。
- `boc_automation/usbhub.py`：USBHub 10 口上电、全部断电。

主流程在 `boc_automation/app.py` 的 `main()`：

1. `prepare_artifact_layout()`
   创建 `logs/`、`logs/batches/`、`debug_runs/`，迁移旧日志、裁剪旧产物、删除 `__pycache__`。
2. `_setup_file_logging()`
   创建 `debug_runs/<run_id>/run.log`，并把输出追加到 `logs/boc.log`。
3. `_power_on_boc_usbhub_port()`
   启动前执行 `hub_ctrl.py only 10 --settle 8 --com COM3`，只开启中行 U盾口。
4. `_close_auto_opened_boc_home_tab()`
   先清一次 U盾插入时已经自动打开的中行公网首页。
5. `_disable_proxy_for_playwright()`
   临时清除代理环境变量。中国银行脚本必须直连，不能走外网代理，不要删这一步。
6. `_direct_network_preflight()`
   解析 `netc1.igtb.boc.cn` / `netc2.igtb.boc.cn` 并测试 TCP 443 直连。如果解析结果包含 `198.18.0.0/15` fake-ip，或 443 直连不通，直接终止并提示关闭 Meta Tunnel/Clash/Mihomo TUN、改真实 DNS 或恢复直连网络。不要改成“发现 fake-ip/直连不通就走本地代理”，银行线永远不走代理。
7. `_prepare_chrome_extensions()`
   在 Chrome/Edge profile 中查找中行证书扩展，复制到临时 profile。支持：
   - Chrome ID：`nhhdpdhiemjpkaikglglhabjafffdjfo`
   - Edge ID：`cpiogedigcbdifgefmkjpfnampochfca`
8. `_write_chrome_preferences()`
   写入临时 profile 的 Chrome Preferences，压制翻译提示。
9. `launch_persistent_context(...)`
   用 Playwright 启动有头浏览器并加载扩展。若扩展来自 Edge，脚本会用系统 `msedge.exe`，因为 Chromium 可能不接受 Edge 扩展 manifest。
10. `_dismiss_browser_chrome_tooltips()`
   清掉 Edge/Windows 原生提示气泡，避免它遮挡桌面调试截图。
11. `page.goto(BOC_URL)` 后 `_close_boc_public_home_windows()`
    处理系统 Edge/中行扩展在浏览器启动后才带出的 `www.boc.cn` 官网页；关闭后 `page.bring_to_front()`。
12. 登录页停止判断
    如果命令行用了 `--open-login`，或 `.env` 设置了 `BOC_STOP_AT_LOGIN_PAGE=1` / `BOC_RUN_UNTIL=login-page`，流程在「中行登录页加载完成」后停止，并记录 `登录页停止_未点击数字证书登录`。命令行 `--open-login` 默认保持浏览器打开，且不触碰 USBHub。
13. `_click_certificate_login()`
   点击「数字证书登录」。
14. `_confirm_certificate_selection()`
   先依赖 Edge 客户端证书自动选择策略；若证书选择框仍出现，用 UIA 找到证书选择框并确认。必须检测弹窗已关闭才算成功，失败时主流程停止。
15. `_maybe_submit_ushield_password()`
   默认等待人工输入 PIN；只有 `BOC_AUTO_USHIELD_PIN=1` 才自动输入。自动输入优先级是 Interception → SendInput → 中行 PIN 弹窗软键盘。
16. `_fill_page_password_and_login()`
    填网页用户密码并点击「登录」。
17. 登录后停止判断
    如果命令行用了 `--login-only`，或 `.env` 设置了 `BOC_STOP_AFTER_LOGIN=1` / `BOC_RUN_UNTIL=login`，流程在「网页登录后页面等待完成」后停止，并记录 `登录后停止_未进入付款服务`。命令行 `--login-only` 默认保持浏览器打开，方便人工判断页面。
18. `_click_payment_transfer()`
    进入「付款服务」→「转账汇款」。
19. `_fill_transfer_form()`
    只有 `BOC_ENABLE_TRANSFER_FILL=1` 才执行，默认只预填、不提交。
20. `_submit_transfer_order()`
    只有 `BOC_ENABLE_ORDER_SUBMIT=1` 才执行。它滚到底部点击红色「提交」，等待页面反馈并截图，然后停止；绝不继续点确认、支付、授权。
21. `finally` 清理
    关闭本次浏览器、删除临时 profile、恢复代理环境、关闭中行官网页/原生弹窗、USBHub all-off、清理 U盾拔出提示。

## 硬安全规则

- 默认严禁自动点击任何 `提交`、`确认`、`转账`、`支付`、`下一步确认付款` 一类按钮。
- 唯一例外是用户明确要求制单测试时，设置 `BOC_ENABLE_ORDER_SUBMIT=1` 后允许点击转账表单底部第一个「提交」。点击后必须停止，不得继续确认/支付/授权。
- 预填表单不等于付款。默认脚本只能填字段并停住；制单提交必须由显式开关控制。
- 资金字段默认不填；必须显式设置 `BOC_ENABLE_TRANSFER_FILL=1`。
- U盾 PIN 自动输入默认关闭；必须显式设置 `BOC_AUTO_USHIELD_PIN=1`。
- 自动 PIN 输入不得盲打到未知焦点。应先识别 U盾/PIN 原生窗口，失败要写日志。
- 不要把真实收款数据硬编码成默认生产路径；批量测试数据在 `batch_run_screenshots.py`，单次测试用 env 覆盖。
- 不要为了跑通而关掉截图、日志、失败校验。银行页面排错靠这些东西救命。

## 证书和 U盾窗口规则

- 证书选择框不是 DOM，Playwright 的 `page.keyboard` 通常打不到它。
- `_confirm_certificate_selection()` 必须通过 UIA/窗口识别中行证书框，再「点击证书项 + Enter」。
- 当前证书框里会出现类似 `CFCA OCA1`、`95566...`、`netc2.igtb.boc.cn:443` 的文本。
- U盾 PIN 弹窗标题可能包含 `校验用户密码`、`U盾`、`USBKey`、`UKey`、`PIN`。
- 中行 PIN 控件可能拒绝普通键盘事件；保留 `_submit_ushield_pin_with_soft_keyboard()`，它会打开随机软键盘并按按钮标签点击。
- 目前自动软键盘路径只验证过小写字母和数字 PIN。

## 转账表单填写规则

`_fill_transfer_form()` 的顺序很重要，不能随便改：

1. 等到页面出现 `收款人账号`。
2. 取消 `保存为常用收款人`。这个勾选会触发表单联动和校验干扰。
3. 填 `收款人账号`，等待稳定并回读。账号必须完整一致；如果页面只留下尾号/截断值，必须判失败并触发重跑/最终上报。
4. 账号填完后，页面通常先弹出 `账号|户名` 的常用收款人候选行。先点击包含目标户名的候选，再回读 `收款人户名`；如果回读值包含目标户名就视为成功，不能再强行覆盖。只有没有候选/没有回读成功时，才输入户名并按模糊查询候选选择。
5. 按开户行品牌选择 `收款行类型`：`中国银行...` 选 `中行`，其他银行选 `他行`。
6. 若为 `中行`，确认页面自动带出的 `中国银行`，不要再填写具体支行或行号。
7. 若为 `他行`，填 `收款人开户行名称`，并选择开户行下拉候选。
8. `他行` 读取 `收款人开户行行号`，只校验，不输入。
9. 复核 `收款人账号`。开户行联动后账号可能被清空，发现清空必须补填。
10. 只读取复核当前 `收款行类型` 状态，不要再次点击，否则可能清空开户行名称/行号。
11. 选择 `收款人类型 = 单位`。
12. 填 `金额`。
13. 最后逐项复核账号、户名、开户行；`他行` 还要复核行号，并扫描可见校验提示。
14. 如果 `BOC_ENABLE_ORDER_SUBMIT=1`，滚到页面底部，点击红色 `提交`，等待并截图下一页/提示；不要继续点任何后续按钮。

开户行匹配规则：

- 页面候选经常和截图不完全一致。
- 要忽略 `股份有限公司`、`有限责任公司`、`有限公司` 等中间词。
- 要按“银行品牌 + 支行尾部”做模糊匹配。
- 例子：
  - `招商银行杭州解放支行` 可以匹配 `招商银行股份有限公司杭州解放支行`。
  - `中信银行股份有限公司奥运村支行` 可以匹配 `中信银行北京奥运村支行`。
  - `中国建设银行股份有限公司沙坪坝华宇广场支行` 可以匹配 `中国建设银行股份有限公司重庆沙坪坝华宇广场支行`。

金额说明：

- 脚本只预填，不提交。
- 页面可能显示可用限额或大写金额红字，这不代表脚本可以继续提交。
- 如果出现 `请输入`、`请选择`、`不能为空`、`必填`、`错误`、`不匹配` 这类可见校验，脚本应判定失败。
- 低额制单测试可临时设置 `BOC_PAYMENT_AMOUNT=0.01`，但不要把低额测试等同于付款安全；后续确认/支付仍然禁止自动化。

## 批量截图测试

`batch_run_screenshots.py` 保存了 10 条从 `C:\Users\30112\Pictures\Screenshots` 截图整理出的测试数据。

运行方式：

```powershell
python batch_run_screenshots.py
python batch_run_screenshots.py 3 7 8
```

批量脚本会为每条记录设置：

- `BOC_ENABLE_TRANSFER_FILL=1`
- `BOC_ENABLE_ORDER_SUBMIT=0`
- `BOC_CLOSE_ON_FINISH=1`
- `BOC_USBHUB_POWER_ON_START=1`
- `BOC_USBHUB_ALL_OFF_ON_FINISH=1`
- `BOC_PAYEE_NAME`
- `BOC_PAYEE_ACCOUNT`
- `BOC_PAYEE_BANK`
- `BOC_PAYEE_BANK_CODE=`
- `BOC_PAYMENT_AMOUNT`

批量调度默认会做银行会话保护：

- `BOC_BATCH_MAX_ATTEMPTS=3`：每条最多尝试 3 次。
- `BOC_BATCH_RETRY_WAIT_SECONDS=30`：检测到会话失效/跳回登录页后等待 30 秒再重试。
- `BOC_BATCH_BETWEEN_JOBS_SECONDS=15`：每条之间默认等待 15 秒，给银行端释放上一轮会话。
- 触发重试的典型表现：页面从转账页跳回 `https://netc2.igtb.boc.cn/#/login-page`，或截图里出现“已经在其它浏览器登录，会话已失效，请重新登录”。

成功判定：

- 子进程退出码为 0。
- 最新 `debug_runs/<run_id>/run.log` 里出现：
  - `[完成] 转账表单必填项已填写（未提交）`
  - 如果启用了制单提交，还要看 `[已点击] 制单提交按钮` 和提交后截图。

注意：

- PowerShell 重定向出的 `logs/batches/batch_*.out.log` 可能中文乱码；以每轮 `run.log` 为准。
- 如果某条失败，先看该轮 `page_screenshots/053_*` 或最后几张截图，再看 `run.log` 中的 `[失败]`、`[重试]`、`[已选] 下拉候选`。
- 2026-05-11 已验证：10 条整体跑通。此前失败集中在开户行候选匹配和开户行联动清空账号，已在 `boc_automation/transfer.py` 中修复。

## 排错产物

- 总日志：`logs/boc.log`
  - 追加写入。
  - 每次运行以 `========== BOC open ==========` 开头。
- 单次运行目录：`debug_runs/<YYYYmmdd_HHMMSS_pid>/`
- 单次日志：`debug_runs/<run_id>/run.log`
- 桌面截图：`debug_runs/<run_id>/screenshots/NNN_*.png`
  - 用于看证书框、U盾 PIN 框、官网残留页、温馨提示等原生窗口。
- 页面截图：`debug_runs/<run_id>/page_screenshots/NNN_*.png`
  - 用于看企业网银 DOM 页面状态。
- 批量日志：`logs/batches/batch_*.out.log` / `logs/batches/batch_*.err.log`
  - 只作为批量调度外层日志；业务失败优先看每轮 `run.log`。

长期整理策略由 `boc_automation/artifacts.py` 在每次启动时执行：

- 根目录旧 `boc.log` 自动移动到 `logs/boc.log`，如目标已存在则改名为 `boc_legacy_<mtime>.log`。
- 根目录旧 `batch_*.log` 自动移动到 `logs/batches/`。
- `debug_runs/` 默认只保留最近 30 个，可用 `BOC_KEEP_DEBUG_RUNS` 调整。
- `logs/batches/` 默认只保留最近 30 个，可用 `BOC_KEEP_BATCH_LOGS` 调整。
- 工作区内 `__pycache__/` 自动删除，`open_boc.py` 和批量脚本也设置了不写 bytecode。
- `.gitignore` 已排除 `.env`、日志、debug_runs、Python bytecode、临时文件。

常用看日志命令：

```powershell
Select-String -LiteralPath "debug_runs\<run_id>\run.log" -Pattern "失败|警告|重试|完成|下拉候选|USBHub|清理"
Get-ChildItem -LiteralPath "debug_runs\<run_id>\page_screenshots" -File | Select-Object -Last 8 FullName
```

## 常见问题和处理

- 找不到证书扩展：
  - 检查 Chrome/Edge profile 是否装了 `BOC Certificate Application Extension`。
  - 检查 `BOC_CHROME_PROFILE` 是否是实际 profile，例如 `Default`、`Profile 1`。
- 浏览器报“无法安装扩展，manifest 不支持”：
  - 多半是 Edge 扩展被 Chromium 加载。保留“Edge 扩展用系统 Edge 可执行文件”的逻辑。
- U盾插入后弹出中行官网：
  - 这是 U盾/安全控件或系统 Edge 加载中行扩展时自动打开的公网首页。脚本应通过 `_close_auto_opened_boc_home_tab()` / `_close_boc_public_home_windows()` 自动关闭。
  - 如果它出现在浏览器刚启动后，不是登录流程失败；通常是启动前清理太早，页面是 Edge 扩展随后带出的。登录页 `page.goto()` 后再清一次即可。
- 截图里出现 `Microsoft Edge` 白色小气泡：
  - 这是 Edge/Windows 原生工具提示，不是网页 DOM，也不是银行错误。
  - 启动后用 `_dismiss_browser_chrome_tooltips()` 按 Esc 并把鼠标移到页面中央，避免气泡挡住桌面截图。
- PIN 自动输入失败：
  - 看日志中的 `[输入后端]`。
  - 如果 Interception 驱动不可用，SendInput 可能被安全控件拦截，此时要走中行软键盘。
- 开户行选不中：
  - 看 `page_screenshots/048_*`，确认下拉候选实际文本。
  - 不要硬等精确全称，要按银行品牌和支行尾部匹配。
- 账号填了又没了：
  - 多半是开户行或常用收款人联动清空。选完开户行后必须复核并补填账号。
- 清理后桌面仍有窗口：
  - 不要手工关闭算完成。把窗口标题/文本加入清理逻辑，重新跑验证。

## 修改代码时的原则

- Web 页面操作用 Playwright；原生证书/PIN/提示窗口用 UIA、win32、pyautogui。
- 新增定位逻辑要遍历 `page.frames`，中行页面经常嵌套 frame。
- 关键动作后加 `_debug_checkpoint("中文说明", page)`，让截图和日志对得上。
- 关键失败不要 `except Exception: pass` 静默吞掉，至少在最终失败路径打印异常或状态。
- 不要删除代理清理、翻译禁用、中文 locale、自动化隐藏参数；这些都会影响中行站点。
- 不要让银行站点走代理。`netc*.igtb.boc.cn` 解析到 `198.18.x.x` 时是代理/TUN fake-ip，不是银行真实地址；TCP 443 直连不通时也不能继续。必须先修直连 DNS/网络，再运行脚本。
- 不要删除浏览器启动后清理 `www.boc.cn` 公网页的二次逻辑；它解决的是 Edge 扩展启动后才弹官网的问题。
- 不要删除 `_dismiss_browser_chrome_tooltips()`；它解决的是桌面截图里 Edge 原生提示气泡遮挡页面的问题。
- 不要让 `BOC_ENABLE_ORDER_SUBMIT` 默认开启。测试时用临时环境变量打开，并用低金额如 `0.01`。
- 制单提交后遇到下一页、确认页、验证码、U盾确认、支付确认，必须停止并汇报，不要继续点。
- 保持 `open_boc.py` 作为薄入口；新增代码按职责放进 `boc_automation/` 对应模块。
- 不要把新日志写回根目录；常规日志进 `logs/`，批量日志进 `logs/batches/`，临时排错图片进 `debug_runs/<run_id>/`。
- 如果某个模块继续变得很大，优先按真实边界拆：页面流程、原生窗口、表单字段、开户行匹配、清理策略，不要为了少行数切出一堆无语义文件。
- 每次实质改动后同步更新 `SKILL.md`；它是下一次接手项目时的第一入口。
