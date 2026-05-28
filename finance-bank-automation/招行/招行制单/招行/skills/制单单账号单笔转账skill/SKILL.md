---
name: zhidan-skill
description: Automate China Merchants Bank CMB U-BANK corporate transfer order creation for the existing Windows GUI package. Use when Codex needs to run, maintain, or troubleshoot 招行制单, 单账号单笔转账, 单笔转账经办, U-BANK 登录填单, bank_form.json 表单填写, U 盾/USB Hub 切换, or the two-step 经办 submission flow.
---

# 招行制单 Skill

## Current Default: Test Mode (Single 经办 Click)

**This package currently runs in test mode by default**: it clicks 经办 once on the form page, takes a screenshot, then closes U-BANK without performing the second confirmation-page click. No real transfer is submitted.

- Test mode is gated by `ZHIDAN_TEST_MODE` (default `1`).
- Setting `ZHIDAN_TEST_MODE=0` declares production intent and enables the second confirmation-page `经办` click. Treat this as a real submission/order-creation path.
- Production submission requires two `经办` clicks: the form-page click plus the confirmation-page click. Never bypass this env-var contract or silently enable the second click in test mode.

## Core Workflow

Use the existing scripts in this folder to complete CMB U-BANK single-account single-transfer order creation. The main entrypoint is `制单.py`; helper modules are split by responsibility and should be reused instead of duplicating logic in the entrypoint.

Run from the package root with:

```bat
run_zhidan.bat
```

Or run the entrypoint directly:

```powershell
python "招行\skills\制单单账号单笔转账skill\制单.py"
```

The script performs this sequence:

1. Load transfer data from `M3直供合同付款数据获取\bank_form.json`.
2. Select the correct UKey by switching the USB Hub port for the payer.
3. Open CMB U-BANK, log in with `招行\.env`, and wait for the main window.
4. Navigate to `转账支付` -> `单笔转账经办`.
5. Fill recipient account, recipient name, opening bank, branch, amount, and purpose.
6. Click `经办` once on the form page. **Test mode (default): stop here, screenshot, and close.** Production mode (`ZHIDAN_TEST_MODE=0`): click `经办` again on the confirmation page, screenshot the result, then close.
7. Close `Firmbank.exe` with `taskkill /F /T` and turn off all USB Hub ports.

## Preconditions

- Run on Windows with the CMB U-BANK client installed and usable manually.
- Prefer 1920x1080 resolution with 100% display scaling. The scripts enable DPI awareness and use ratio-based fallbacks, but this package is tuned for that display profile.
- Install Python dependencies from the package-root `requirements.txt`: `pywinauto`, `pywin32`, `python-dotenv`, and `pillow`.
- Keep login credentials in `招行\.env`; never print or copy credential values into chat or logs. Expected keys include `LOGIN_PWD`, `CERT_PWD`, and optionally `CMB_UBANK_SHORTCUT_PATH`.
- Ensure the U-BANK shortcut exists at `CMB_UBANK_SHORTCUT_PATH`, or at the default desktop shortcut name `招行U-BANK.lnk`.
- Ensure `M3直供合同付款数据获取\bank_form.json` exists before a real run.

## Form Data

`zhidan_usb_hub._load_bank_form()` reads:

```text
M3直供合同付款数据获取\bank_form.json
```

Expected transfer fields:

- `付款单位名称` or `付款单位`
- `收方账号`
- `收方户名`
- `开户银行`
- `支行名称`
- `金额`
- `用途`
- `业务参考号`

### Manual JSON Entry

For one-off manual orders, use:

```bat
C:\Users\30112\Desktop\财务\招行\招行制单\招行\手动录入制单入口.bat
```

The entry opens a console, asks for a pasted JSON-like transfer template, then writes the
canonical `M3直供合同付款数据获取\bank_form.json` used by `制单.py`. Paste the JSON and
finish with a single line containing `END`.

Supported template fields:

```json
{
  "付款单位名称": "示例付款方公司",
  "收方账号": "000000000000000",
  "收方户名": "示例收款方公司",
  "开户银行": "示例银行",
  "支行名称": "示例银行示例支行",
  "金额": "0.01",
  "用途": "请勿用于真实提交",
  "业务参考号": "无"
}
```

The parser is intentionally forgiving: it accepts pasted `<br/>` line breaks, missing outer
braces, common key aliases, and trailing commas. It normalizes `招商银行股份有限公司` to
`招商银行` before writing `bank_form.json`, because the U-BANK bank field must receive the
short bank head to avoid duplicate `招商银行招商银行` input.

After writing the JSON, the entry can launch `制单.py`:

- Direct Enter starts test mode (`ZHIDAN_TEST_MODE=1`), which only clicks the first `经办`.
- `N` only saves `bank_form.json`.
- `PROD` starts production mode (`ZHIDAN_TEST_MODE=0`) and can click the confirmation-page
  second `经办`; use only when the user clearly intends a real order.

If the payer is not configured in the payer-to-USBHub map, the entry asks for a one-time
USBHub port. Entering a port sets `USB_HUB_FORCE_PORT` and `ZHIDAN_ALLOW_FORCE_USB_PORT=1`
only for that child process; pressing Enter saves only and does not launch U-BANK.

**Default behavior when `bank_form.json` is missing**: `zhidan_usb_hub._switch_usb_hub_for_form` fails closed and returns `False`, the entrypoint exits with code `2`, and U-BANK is never opened. This is intentional — running with no form data risks targeting the wrong UKey or filling the wrong fields.

**Demo mode for local UI debugging only**: setting `ZHIDAN_ALLOW_DEMO_MODE=1` opts into running without a `bank_form.json`. In this mode no USB Hub port is switched, and `zhidan_form.fill_transfer_form` falls back to obvious placeholder values (`DEMO_ACCOUNT_NEVER_SUBMIT`, `示例占位_严禁真实提交`, `amount=0`, etc.) and prints a `[示例模式警告]` block. These placeholders are designed to be rejected by U-BANK's own field validation, so they cannot accidentally produce a real transfer. Do not treat demo mode as a valid production run, and do not click「经办」against the live client while it is on.

## Login Name Selection (双账号)

> **业务口径**：登录名下拉选择是「同一台 U-BANK 挂多个登录名」的**双账号特殊场景**能力，**不是招行默认必配项**。绝大多数公司是单账号，**不需要**配置任何登录名字段；也不应该因为登录名候选读取失败而中断单账号公司的制单。**默认 = 完全不操作登录名下拉，沿用 U-BANK 当前默认登录名**。

Some payers run **两个登录名挂在同一台 U-BANK / 同一个制单流程下**（例如「来参缘潘思婷001」「来参缘潘思婷002」）。来参缘 001/002 是**同一个 UKey**，差异只在登录窗口选择不同登录名；明确走招行的一次性/人工场景才设置 `CMB_LOGIN_ACCOUNT_NAME` + `CMB_REQUIRE_LOGIN_ACCOUNT_SELECTION=true`，并禁止再按付款账号尾号去操作制单页「付款方账号」下拉。M3 监控默认路由中，来参缘仍走兴业。`ubank_common.login_ubank()` 在输入登录密码 / 证书密码之前，**仅当启用条件命中时**才在「联机登录」窗口的登录名下拉里选择目标账号。

**启用条件**（任一即可，未命中则完全不操作下拉）：

1. 当前进程显式设置了 `CMB_LOGIN_ACCOUNT_NAME` —— 与下拉项**精确相等**（不是 substring、不是 startswith）的登录名文本，例如 `"来参缘潘思婷002"`；优先级最高，bank_form 完全不参与注入。
2. 当前进程显式设置了 `CMB_LOGIN_ACCOUNT_INDEX` —— 1-based 下拉序号（仅在 `CMB_LOGIN_ACCOUNT_NAME` 未设置时生效）。
3. `bank_form.json` 同时包含登录名值**和**强制开关 `CMB_REQUIRE_LOGIN_ACCOUNT_SELECTION=true`（或别名 `招行强制选择登录名=true`）—— 由 `制单.py` 在密码读取之前把登录名注入到 `CMB_LOGIN_ACCOUNT_NAME`。
4. 上述都未命中时**不做任何 UI 操作**，沿用 U-BANK 当前默认登录名，保持原行为不变（不展开下拉、不读控件、不修改窗口状态）。即使 bank_form 残留 `招行登录名 / CMB_LOGIN_ACCOUNT_NAME / CMB登录名` 字段，没有强制开关一律忽略，只打印一行 `[登录名选择] bank_form 含登录名字段，但未设置强制选择开关，已忽略；沿用 U-BANK 默认登录名`。

实现要点（招行登录名下拉本身是标准 Win32 ComboBox，UIA 在该窗口上易把无关 ListItem 混进来，所以走 Win32 消息为主路径）：

- 用 `pywinauto` 在「联机登录」窗口里通过 `class_name == "ComboBox"` 找到登录名控件，拿到底层 `HWND`。
- 用 Win32 消息读候选 + 选择 + 自检：
  - `CB_GETCOUNT (0x0146)`、`CB_GETLBTEXTLEN (0x0149)`、`CB_GETLBTEXT (0x0148)` 读全部候选文本。
  - `CB_SETCURSEL (0x014E)` 选中目标 index，`CB_GETCURSEL (0x0147)` 自检返回值等于目标 index。
  - 选中后向父窗口转发 `WM_COMMAND` + `CBN_SELCHANGE`，让应用层的选中回调正常触发。
- 选完之后用 `_read_combo_value(combo)` 回读当前显示值，依次尝试：
  1. UIA `iface_value.CurrentValue`（ValuePattern）；
  2. UIA `legacy_properties()["Value"]`（LegacyIAccessible）；
  3. Win32 `WM_GETTEXT`（`GetWindowTextW`）；
  4. 子 `Edit` 的 legacy `Value`；
  5. 最后才是 `window_text` / `element_info.name` 兜底。
- 回读必须**精确等于**目标文本：空读回视为失败，substring / startswith / contains **不放行**。
- UIA 仅在拿不到 HWND 时作为窄路径 fallback，且**只扫 ComboBox 自身子树**，去重用 `element_info.runtime_id` 而不是 `id(element_info)`；不再扫描整桌面的 List/Popup/Pane。

fail-closed 触发条件（任一发生都让 `login_ubank()` 返回 `False`，制单主流程按登录失败退出，**不会读取 `LOGIN_PWD` / `CERT_PWD`，不输入任何密码，不点击登录**）：

- 找不到登录名 ComboBox；
- `CB_GETCOUNT` / `CB_GETLBTEXT` 返回错误，或候选列表为空；
- `CMB_LOGIN_ACCOUNT_NAME` 未在 Win32 候选中精确命中（含多条同名也拒绝）；
- `CMB_LOGIN_ACCOUNT_INDEX` 非正整数 / 越界；
- `CB_SETCURSEL` / `CB_GETCURSEL` 自检不一致；
- 回读为空，或回读文本与目标文本不严格相等。

凭据读取顺序：`login_ubank()` 找到「联机登录」窗口后，**先**调用 `_select_login_account_if_requested(win)`；只有它返回 `True` 之后，才 `os.getenv("LOGIN_PWD")` / `os.getenv("CERT_PWD")` 并喂给密码框。任何选择失败路径都不接触凭据。日志只打印 `已选择指定招行登录名: [<目标>]` 与候选列表 / 命中失败原因，**绝不打印密码 / 证书密码**。

bank_form 透传（**仅强制开关启用时**）：`制单.py` 在加载 `bank_form.json` 之后做下列判断（已显式 env 优先，bank_form 不覆盖）：

- 当前进程已设 `CMB_LOGIN_ACCOUNT_NAME` 或 `CMB_LOGIN_ACCOUNT_INDEX` → 打印 `[登录名选择] 当前进程已显式设置 ... 跳过 bank_form 注入`，bank_form 字段一律不参与。
- 否则当且仅当 bank_form 同时满足：
  - 含登录名值（`招行登录名` / `CMB_LOGIN_ACCOUNT_NAME` / `CMB登录名` 任一非空字符串），且
  - 含强制开关 `CMB_REQUIRE_LOGIN_ACCOUNT_SELECTION=true` 或 `招行强制选择登录名=true`（接受 bool / `"1"` / `"true"` / `"yes"` / `"on"` / `"是"` / `"真"`，其它字符串视为 false）
  时，才把值注入到 `CMB_LOGIN_ACCOUNT_NAME`，并打印 `[登录名选择] 已从 bank_form[...] 注入 CMB_LOGIN_ACCOUNT_NAME（已显式开启强制选择登录名开关）`。
- 只有登录名值、没有强制开关 → 打印 `[登录名选择] bank_form 含登录名字段，但未设置强制选择开关，已忽略；沿用 U-BANK 默认登录名`，不注入。
- 只有强制开关、没有登录名值 → 打印 `[登录名选择] bank_form 设置了强制选择登录名开关但无登录名值，已忽略；沿用 U-BANK 默认登录名`，不注入。

`M3直供合同付款数据获取\bank_route.py` 的 `normalize_form` 也遵守同一边界：**默认丢弃** `招行登录名 / CMB_LOGIN_ACCOUNT_NAME / CMB登录名`，普通单账号公司的 bank_form 不会带这些字段；**仅当源 form 含 `CMB_REQUIRE_LOGIN_ACCOUNT_SELECTION=true` 或 `招行强制选择登录名=true` 时**才把值与强制开关一起镜像写入归一化 bank_form。源 form 残留登录名值但无强制开关时，会打印 `[M3 路由] bank_form 含登录名字段，但未设置 CMB_REQUIRE_LOGIN_ACCOUNT_SELECTION=true/招行强制选择登录名=true，归一化时已丢弃；沿用 U-BANK 默认登录名`。整段不影响银行路由、金额覆盖、提交门禁、U 盾端口逻辑。

这一切只影响**登录名选择**，不改变制单 / 经办 / 提交的安全边界：`ZHIDAN_TEST_MODE` 默认 `1` 仍然只点第一次「经办」；生产二次「经办」仍然只有 `ZHIDAN_TEST_MODE=0` 时才会发生。一旦启用登录名选择，仍按上面的 fail-closed 条件严格筛选目标（精确相等、唯一、回读校验），**避免 001/002 选错**。**普通单账号公司不要在 bank_form 里写登录名字段，也不要设置 `CMB_LOGIN_ACCOUNT_NAME` / `CMB_LOGIN_ACCOUNT_INDEX`**。

## Payer Account Selection (双账号付款方账号)

注意：这是「单笔转账经办」**页面**里的「付款方账号」下拉，**不是**登录窗口的「登录名」下拉。同一台 U-BANK 同一个登录名下挂了多个付款方账号（例如尾号 001 / 002）时，用这里描述的能力在填写收款方信息之前选择正确的付款方账号。得鲜 001/002 是**同一个 UKey + 同一个登录名**，差异在制单页付款方账号下拉；明确走招行的一次性/人工场景才写 `CMB_PAYER_ACCOUNT_SUFFIX=001/002`，不要设置登录名选择开关。M3 监控默认路由中，得鲜仍走农行。

**生产主路径**：`M3 / OA 字段 → bank_route.normalize_form() → 招行 bank_form.json → 制单.py 注入 CMB_PAYER_ACCOUNT_SUFFIX → 页面选择付款方账号`。生产环境推荐由 M3 字段推导**尾号**完成自动切换，避免把完整账号长期写在 env 或配置里。

输入优先级（写入「当前进程环境变量」的口径）：

1. `CMB_PAYER_ACCOUNT_TEXT` — 与下拉项**精确相等**的完整可见文本。**调试 / 兜底**能力，**生产主路径不推荐**；M3 路由也不会主动构造此值，仅在 form 显式提供完整文本时透传。
2. `CMB_PAYER_ACCOUNT_SUFFIX` — **生产推荐**。纯数字，按下拉候选「账号段末位 N 位数字」精确相等匹配。账号段定义见下面「尾号提取」。例如 `SUFFIX=002` 只命中账号段尾号正好是 `002` 的候选，**不会**被 `0012` 等其它前缀/包含关系误命中。
3. `CMB_PAYER_ACCOUNT_INDEX` — 1-based 下拉序号，**仅作为调试兜底**，**生产不推荐**。真实 U-BANK 是 CEF 伪 ComboBox，下拉候选的相对顺序可能受「当前选中项」影响（被选中的账号可能被排到首位），同一 UKey 下同一份配置在 INDEX=1 和 INDEX=2 之间得到的账号可能并不稳定 —— 生产请始终用 `CMB_PAYER_ACCOUNT_SUFFIX` 按数字尾号锚定目标。

三者都未设置时**不做任何 UI 操作**，沿用页面默认付款方账号，保持原行为不变（不展开下拉、不读控件、不修改窗口状态）。

**重要业务口径**：绝大多数情况下登录名和付款方账号都是**单账号**。这一节描述的能力**只在显式配置时启用**（三个 env 之一非空 / bank_form 有对应字段）。**默认不启用**、默认不展开下拉、默认不影响任何单账号流程；任何「U-BANK 主窗口里有这个下拉」的情况下，没显式配置就一行 UI 操作都不会发生。**单账号场景永远不需要设置任何 `CMB_PAYER_ACCOUNT_*`**。

实现要点（与登录名选择共用 `ubank_common.py` 的 ComboBox 基础设施）：

- 调用点：`zhidan_form.fill_transfer_form` 在 `_wait_transfer_form_ready` 之后、第一次 `_input_or_abort(main_win, "收方账号", ...)` 之前；helper 返回 `False` 直接 `raise RuntimeError`，主流程退出码 `9`（经办前终止），**不填收款方、不点经办、不点提交**。
- 定位：先扫主窗口收集所有可见 ComboBox 与 Name 含 `付方账号 / 付款方账号 / 默认付方账户 / 默认付款方账户` 的元素；按矩形几何距离把标签关联到最近的 ComboBox。标签未命中时退而求其次按「候选含长数字串」启发，只有 1 个 ComboBox 满足才返回，多于 1 个或没有 → fail-closed。
- 候选读取（两条路径，按需切换）：
  1. **快速路径** `_payer_combo_items_text(combo)`：优先 Win32 `CB_GETCOUNT` / `CB_GETLBTEXT`（与登录名选择共用 `_user32` 已声明 `LPARAM` 签名的安全路径，64 位无溢出）；拿不到 HWND 时退到只扫 ComboBox 自身子树的 UIA 路径，绝不扫桌面或其它窗口。
  2. **CEF / UIA 展开收集路径** `_payer_collect_expanded_candidates(main_win, combo)`：真实 U-BANK 单笔转账经办页的付款方账号是 CEF 渲染的伪 ComboBox，没有 Win32 HWND，且未展开时 combo 子树里也读不到候选。快速路径返回空时，模拟点击 combo 右侧箭头展开下拉（停 ~0.6s 等渲染），然后**仅**在「主窗口子树」+「combo 几何区」(`combo.left - 80` 到 `combo.right + 450`，纵向 `combo.top` 到 `combo.bottom + 180`) 内、且不在 combo 自身子树里、可见的 `ListItem / DataItem / Text` 节点里收集候选。绝不扫桌面、任务栏、浏览器或别的应用窗口；与业务模式 / 默认收方账号等同页其它下拉的候选互不污染（依赖几何区 + tail-3 数字特征双重过滤）。
- 候选去重 key 用「**账号段完整数字串**」（`_account_segment_digits(text)`，即 `,` / `，` 之前那部分的全部数字）。同一个真实账号同时以 ListItem 和 Text 出现 → 去重折叠为一条；两个**不同**真实账号即使尾 3 位相同 → 账号段数字串不同 → 保留为两条 → SUFFIX 模式按「多条同尾号」fail-closed。日志只打印 `***NNNN` 末 4 位脱敏字符串。
- 选择动作：Win32 路径（有 HWND）走 `CB_SETCURSEL` + 父窗口 `WM_COMMAND/CBN_SELCHANGE` + `CB_GETCURSEL` 自检；CEF/UIA 路径用展开收集时拿到的候选 UIA 节点本身做 `invoke` / `select` / `click_input` / 坐标兜底点击。
- 回读校验分两层 fallback，都走 `_payer_readback_matches(current, chosen_text)` 严格相等（整段相等 OR 账号段数字串相等，无 substring / startswith / contains）：
  1. **第一层** `_read_combo_value(combo)`：依次 `iface_value.CurrentValue` → `legacy_properties()["Value"]` → `WM_GETTEXT` → 子 `Edit` 的 legacy `Value` → `window_text` / `name`。适合 Win32 ComboBox 与暴露 ValuePattern 的标准 UIA 控件。
  2. **第二层** `_read_payer_combo_display_text(main_win, combo)`（仅在第一层为空或不匹配时启用）：在 main_win 子树内、紧贴 `combo.rectangle()` ±6 px padding 的几何区里，收集可见 `Text / DataItem / Edit / ComboBox` 节点；跳过纯标签节点（含「付款方账号」等关键字的 Name）；按账号段数字串分组，**只有 1 组**时返回相交面积最大的那条文本，**≥ 2 组不同账号段**时 fail-closed。CEF 伪 ComboBox 选中变更后 `_read_combo_value` 常常读不到当前值，但页面视觉已切换，本层负责把那条「已经显示出来的账号文本」严格读回来。
- 两层都没匹配成功，或第二层读到空/读到与目标不同的账号段 → fail-closed。
- 唯一性：`TEXT` 必须唯一精确命中；`SUFFIX` 必须唯一命中（同尾号出现 ≥ 2 条 → fail-closed）；`INDEX` 必须正整数且在候选范围内。
- 成功路径会在日志末尾打印 `回读源: value-pattern` 或 `回读源: display-geometry`，便于审计判定走的哪条 fallback。

fail-closed 触发汇总：未定位到付款方账号下拉 / 候选为空（含 CEF 展开后仍空） / `TEXT` 未精确命中或多条命中 / `SUFFIX` 非纯数字、未命中或多条命中 / `INDEX` 非正整数或越界 / `CB_SETCURSEL` 失败 / `CB_GETCURSEL` 自检不一致 / CEF 路径候选 invoke + click 全部失败 / 两层回读都为空 / 显示区几何回读发现 ≥ 2 条不同账号段 / 任一回读层读到的账号段数字串 ≠ 目标账号段数字串。

日志脱敏：候选与命中结果只打印**末位 4 位数字**（`***NNNN`）或显式尾号，**绝不打印完整账号、户名、付款单位、密码、证书密码**。成功时打印 `已选择付款方账号尾号: 002` 这类信息；失败时只说「未找到尾号 NNN 的唯一候选 / 候选中存在多条同尾号」等脱敏原因。

### 尾号提取（账号段优先，防公司名数字污染）

U-BANK 付款方账号下拉的候选文本常见格式：

```
1109 6079 5610 001, 北京示例公司
1109 6079 5610 002，北京示例公司2026
```

`ubank_common._payer_account_tail_digits(text, n)` 规则：

1. 先按 `,` / `，` 把候选切成「账号段」+ 「说明段」，**只在账号段里**提取数字；
2. 账号段数字位数 `>= n` 才取末 `n` 位返回；
3. **有分隔符但账号段数字不足 n 位时直接返回空串**（明确 fail-closed），绝不退到公司名一侧的数字凑数；
4. 没有分隔符时整串就是账号段，规则同上。

举例（n=3）：

| 输入 | 输出 |
| --- | --- |
| `"1109 6079 5610 001, 北京示例公司"` | `"001"` |
| `"1109 6079 5610 001, 示例公司2026"` | `"001"`（不会因为公司名里的 `2026` 被读成 `026`） |
| `"1109 6079 5610 0012, 示例公司"` | `"012"`（`SUFFIX=001` 不会误命中此条） |
| `"*** 002"` | `"002"` |
| `"ab12"` | `""`（位数不足 → fail-closed） |

日志脱敏 `_payer_account_mask(text)` 走同一个账号段切分，输出 `***NNNN`（账号段数字末 4 位），公司名里的数字不会进入脱敏字符串。

### bank_form 透传与 env 显式优先

`制单.py` 加载 `bank_form.json` 后处理付款方账号注入的顺序：

1. **任何一个 `CMB_PAYER_ACCOUNT_*` 环境变量已被进程显式设置** → 完全跳过 bank_form 注入，直接使用进程环境变量。日志会打印 `当前进程已显式设置 CMB_PAYER_ACCOUNT_*，跳过 bank_form 注入`。
2. 三个 env 都没设置时：
   - 先按 `招行付款方账号尾号 / CMB_PAYER_ACCOUNT_SUFFIX / CMB付款方账号尾号` 注入 `CMB_PAYER_ACCOUNT_SUFFIX`（生产主路径，命中即结束）；
   - 上一步没拿到值才退而求其次按 `招行付款方账号 / CMB_PAYER_ACCOUNT_TEXT / CMB付款方账号` 注入 `CMB_PAYER_ACCOUNT_TEXT`（调试 / 兜底）。
3. **绝不会同时**注入 SUFFIX 与 TEXT。

### M3 路由透传与尾号推导

`M3直供合同付款数据获取\bank_route.py` `normalize_form` 内部，付款方账号字段的写出策略：

- **`CMB_PAYER_ACCOUNT_TEXT` / 招行付款方账号 / CMB付款方账号**：只在 form 显式提供完整付款方账号字段时透传；**绝不主动**从 M3 付款账号字段构造完整文本。
- **`CMB_PAYER_ACCOUNT_SUFFIX` / 招行付款方账号尾号 / CMB付款方账号尾号** 由 `derive_cmb_payer_account_suffix(form, payer)` 推导：
  1. form 显式尾号字段（任一别名）→ 直接用；
  2. 否则尝试 M3/OA 付款账号字段 `付款账号 / 付款方账号 / 付方账号 / 付款账户 / 付款账户账号 / 付方账户 / 付方账户账号 / 付款人账号 / 付款人账户`，**只在「账号段」末 3 位**提取；
  3. 否则按已**人工确认**的「付款单位 → 尾号」规则表 `_CMB_PAYER_UNIT_TO_ACCOUNT_SUFFIX` 匹配 —— **该表默认留空**，不能基于收款字段推导，未命中保持页面默认（不写 suffix）；
  4. 都未命中返回空串，bank_route 不会写 suffix，下游保持页面默认账号 —— 不猜。
- **`CMB_DISABLE_PAYER_ACCOUNT_SELECTION=true` / `招行禁用付款方账号选择=true`**：用于来参缘 001/002 这类“同一个 UKey、只在登录窗口切换账号”的记录。该开关为真时，即使 M3 本地账户表带出了 `付款账号`，`bank_route.normalize_form` 也会删除/不写 `CMB_PAYER_ACCOUNT_SUFFIX` 与 `CMB_PAYER_ACCOUNT_TEXT`，避免登录名选择和制单页付款方账号选择同时触发。

任何一条命中都不会改变银行路由、金额覆盖、提交门禁、U 盾端口匹配逻辑。

这一切只影响**付款方账号选择**，不改变制单 / 经办 / 提交的安全边界。后续仍由人工审核 / 复核 / 授权 / 付款；脚本永远不自动复核 / 授权 / 最终付款。

## USB Hub

`zhidan_usb_hub.py` switches the UKey before launching U-BANK. Default COM port is `COM3`, overridable with `USB_HUB_COM`.

Configured payer-to-port mapping:

- `云链跳动` -> port `1`
- `云链跃动` -> port `2`
- `云链悠动` -> port `3`
- `云链智动` -> port `4`
- `云链逸动` -> port `5`
- `云链灵动` -> port `6`
- `云链讯动` -> port `7`
- `云链慧动` -> port `8`
- `云链炫动` -> port `9`
- `蒙特` -> port `12`（仅明确走招行时；M3 默认主路由仍把蒙特送农行）
- `得鲜` -> port `16`（001/002 同一个招行 UKey，在制单页付款方账号下拉切换）
- `来参缘` -> port `18`（001/002 同一个招行 UKey，在登录窗口登录名下拉切换）
- `浙江云炫农` / `云炫农` -> port `19`
- port `10` 当前是河南讯动 / 中行 UKey，**不属于招行映射**
- port `11/13/14/15/17/30` 分别属于农行/兴业/外置 OK 场景，**不属于招行映射**
- 这张表是 USBHub 物理口表，和 M3 制单路由是两码事：M3 默认主路由仍是来参缘走兴业、得鲜/蒙特走农行；只有记录已经明确路由到招行时，才使用这里的招行 UKey 口。

If a real `bank_form.json` is loaded but the payer is missing or unmapped, the script aborts to avoid using the wrong UKey. If `bank_form.json` is missing entirely, the script also aborts by default (exit code `2`); to bypass this for local UI debugging only, set `ZHIDAN_ALLOW_DEMO_MODE=1` — this skips USB Hub switching and runs against placeholder form values, and is **not** a valid production path. After completion or failure cleanup, the script attempts to turn all USB Hub ports off.

`USB_HUB_FORCE_PORT` is a dangerous override because it bypasses payer-to-UKey matching. It fail-closes unless `ZHIDAN_ALLOW_FORCE_USB_PORT=1` is also set, and only accepts 1-30 for the current 30-port hub. The intended caller is the batch test runner only; production runs must not preserve either env var.

## Batch Test Runner

Use `C:\Users\30112\Desktop\财务\招行\招行制单\run_batch_from_screenshots.ps1` for the fixed 10-record regression test set. This is a **test runner**, not a production runner.

Default behavior:

- `UsbHubPort` defaults to `3`; this is the approved test setup when only 悠动 is plugged into USBHub port 3.
- `ZHIDAN_TEST_MODE` is left at the entrypoint default (`1`), so each item stops after the first `经办` screenshot and does not click the confirmation-page `经办`.
- `USB_HUB_FORCE_PORT=<port>` and `ZHIDAN_ALLOW_FORCE_USB_PORT=1` are set inside the runner so all 10 test records use the same test UKey.
- `PYTHONDONTWRITEBYTECODE=1` is set to avoid creating `__pycache__` during repeated tests.
- Each batch creates `招行\screenshots\batch_yyyyMMdd_HHmmss`.
- Each item writes stdout/stderr logs and `NN_result.json` in that batch folder.
- New PNG screenshots are moved into `NN_screenshots` under the batch folder, and `summary.json` points to those paths. The screenshots root should stay free of loose step screenshots after batch runs.

Useful commands:

```powershell
# Full 10-record regression test on USBHub port 3.
C:\Users\30112\Desktop\财务\招行\招行制单\run_batch_from_screenshots.ps1

# Run only item 6 on USBHub port 3.
C:\Users\30112\Desktop\财务\招行\招行制单\run_batch_from_screenshots.ps1 -OnlyIndex 6

# Override timeout if U-BANK is slow.
C:\Users\30112\Desktop\财务\招行\招行制单\run_batch_from_screenshots.ps1 -TimeoutSeconds 480
```

Retry behavior:

- The runner retries an item once after cleaning U-BANK and turning USBHub off when there are no screenshots and the log shows main-window startup failure.
- It also retries once for detected U-BANK crash logs such as `招行客户端崩溃`, `守护线程已捕获并强杀招行崩溃`, or `Firmbank.exe - 应用程序错误`.
- In production mode (`ZHIDAN_TEST_MODE=0`), the runner must not retry after a submit click has started. A retry after submit could duplicate a real order.
- `summary.json` records `Attempt`, `RetryCause`, `FirstAttemptLog`, and `FirstAttemptErrorLog`.

## Artifact Cleanup

Use `C:\Users\30112\Desktop\财务\招行\招行制单\cleanup_generated_artifacts.ps1` to keep the workspace clean over time.
`制单.py` runs this cleanup automatically after a normal standalone run, and
the M3 latest-30 runner runs it once after the whole batch. Disable with
`ZHIDAN_AUTO_CLEANUP_ARTIFACTS=0` only for debugging.

Default mode is dry-run only:

```powershell
C:\Users\30112\Desktop\财务\招行\招行制单\cleanup_generated_artifacts.ps1
```

Apply the cleanup plan only after reading the plan:

```powershell
C:\Users\30112\Desktop\财务\招行\招行制单\cleanup_generated_artifacts.ps1 -Apply
```

Cleanup policy:

- Moves legacy loose root screenshots into the correct `batch_*\NN_screenshots` folder when `summary.json` / `NN_result.json` references them, then rewrites the JSON paths.
- Archives unindexed root screenshots, diagnostic crops, and `watch_*.log` files under `招行\screenshots\_archive`.
- Removes `__pycache__` unless `-KeepPyCache` is passed.
- Keeps recent batch folders by real timestamp parsed from `batch_yyyyMMdd_HHmmss`, not by directory `LastWriteTime`. Defaults keep at least 30 days or the newest 100 batches.
- Purges archived generated files older than 90 days by default.
- Supports retention knobs such as `-KeepBatchDays`, `-KeepRecentBatches`, `-KeepRootScreenshotDays`, `-KeepWatchLogDays`, and `-PurgeArchiveDays`.

## File Map

- `制单.py`: main automation entrypoint and end-to-end orchestration. Reads `ZHIDAN_TEST_MODE` to decide whether to skip the second `经办` click (default: skip).
- `zhidan_paths.py`: adds the `招行` project root to `sys.path` and defines screenshot paths.
- `zhidan_usb_hub.py`: loads `bank_form.json` and switches or shuts down USB Hub ports.
- `zhidan_form.py`: top-level transfer-form filling sequence.
- `zhidan_input.py`: low-level UIA lookup, keyboard input, clipboard-safe helpers, and coordinate fallbacks.
- `zhidan_bank.py`: opening-bank and branch dropdown handling.
- `zhidan_payee.py`: beneficiary namebook/search popup handling.
- `zhidan_submit.py`: finds and clicks the real `经办` buttons, including confirmation-page handling.
- `zhidan_confirm.py`: legacy 退出确认弹窗 helpers (UIA + 几何兜底)。当前主流程 `_close_with_confirm` 已改用 `taskkill /F /T`，本模块仅在需要回退到优雅关闭路径时使用。
- `zhidan_crash.py`: U-BANK crash detection, crash-dialog cleanup, and Firmbank process cleanup.
- `zhidan_utils.py`: display diagnostics, trace logging, and screenshots.
- `..\..\ubank_common.py`: shared U-BANK login, click, keyboard, and window helpers.
- `C:\Users\30112\Desktop\财务\招行\招行制单\run_batch_from_screenshots.ps1`: 10-record test runner, forced USBHub test port, per-item logs/results, batch-local screenshots, and one safe retry for startup/crash failures.
- `C:\Users\30112\Desktop\财务\招行\招行制单\cleanup_generated_artifacts.ps1`: repeatable artifact cleanup and retention script.

## Maintenance Rules

- Keep `制单.py` thin. Add field-specific UI behavior to the corresponding `zhidan_*` module.
- Preserve the no-clear input strategy unless deliberately changing the crash-risk tradeoff. Existing code avoids `Ctrl+A`/`Backspace` because U-BANK has crashed on some form controls.
- Prefer UIA control discovery first, then use the existing ratio-based click/input fallbacks.
- Keep screenshots in `招行\screenshots`; batch-test screenshots should live inside the relevant `batch_*` folder, not loose in the screenshots root.
- Keep artifact cleanup repeatable. Prefer `cleanup_generated_artifacts.ps1` dry-run first, then `-Apply`; do not hand-delete generated files unless the script rules are wrong.
- Treat two successful `经办` clicks as a real submission workflow. Before running the script against live U-BANK, make sure the user intends an actual transfer-order submission.
- Test mode (`ZHIDAN_TEST_MODE=1`, default) only ever issues the first `经办` click. Do **not** silently re-enable the second click without also gating it behind `ZHIDAN_TEST_MODE=0`; the env var is the contract that protects against accidental real submissions.
- If changing USB Hub behavior, keep the fail-closed behavior for unknown payers and for missing `bank_form.json`. The `ZHIDAN_ALLOW_DEMO_MODE=1` opt-in is the only path that may continue without a real form, and even then it must skip USB Hub switching.
- If changing batch-run behavior, preserve the distinction between test-only forced port and production payer-matched UKey selection.

## Pre-Submit Field Verification

After `06_全部填完` and before the first `经办` click, `verify_filled_form_or_abort`
in `zhidan_form.py` reads each filled UIA control back and compares against
`bank_form.json`. Mismatches produce a `07_提交前校验失败` screenshot and raise
`RuntimeError`, so `_click_submit_button` is never reached. Rules:

Important UI detail: the U-BANK "常用/名册" candidate panel is triggered around
the `收方账号` input, not the `用途` input. When a matching row appears, the
script double-clicks it immediately after account input and lets U-BANK auto-fill
收方户名、开户银行、支行/联行号. If those fields are complete, the script skips
manual recipient/opening-bank/branch input and goes straight to amount/purpose.
If the candidate leaves those details incomplete, the script falls back to manual
填写 from `bank_form.json`. At submit-time, account and recipient name are still
checked against M3; only after those identity checks pass does the branch field
use the current U-BANK namebook value as authoritative. If the M3 branch differs
from the U-BANK namebook branch, the run logs `[名册支行修正]` and captures
`07_名册支行与M3不一致_按名册` for audit.

Before that strict verification, `fill_transfer_form` runs one automatic
incomplete-field refill pass. If UIA reads a key field as empty, placeholder,
or label fallback (for example `开户银行`, `支行名称/联行号`, `金额`, or `用途`),
the script re-enters that field manually from `bank_form.json`, saves
`06_信息不全_自动重填后`, and only then performs the normal submit-time checks.
It does not blindly overwrite non-placeholder mismatches, because retyping into
a non-empty U-BANK field can append text and make the order less safe.

- 收方账号: digit-only equality.
- 收方户名: bidirectional containment after whitespace strip.
- 开户银行: substring containment for ordinary banks. If U-BANK reads a non-empty
  short display name that is a substring of the target (for example village-bank
  short-name display), the code logs `[开户银行兜底]` and allows manual screenshot
  review. **招商银行** must read back exactly `招商银行`; any UIA fallback to
  `开户银行` / `* 开户银行` fails closed unless
  `ZHIDAN_ALLOW_MANUAL_SCREENSHOT_REVIEW=1` is explicitly set for that batch —
  and even then a manual look at `06_全部填完` is required.
- 支行名称/联行号: strict equality after whitespace strip, one unique verified
  safe-equivalent display name in the branch dropdown, or one unique same-bank
  higher-level region route selected from that same branch dropdown. M3 data is
  normalized before fill: major banks are reduced to the bank head used by
  U-BANK (for example `中国银行`), while the full branch name stays in
  `支行名称/联行号`. The branch flow is intentionally simple: first type the
  full branch name and inspect the dropdown. If no exact or safe equivalent
  option exists, the only allowed fallback is still inside the same branch
  input/dropdown: type data-derived route terms by 区县 → 市 → 省 strength, then
  select only a unique same-bank higher-level candidate in that dropdown.
  When that region-level route is used, the script must notify through the
  Codex gateway before any `经办` click; notification failure aborts the run.
  In real submit mode (`ZHIDAN_TEST_MODE=0`), the notification must also send
  the M3 contract-payment list screenshot and payment-detail screenshot through
  Feishu. If either M3 screenshot is missing, abort before `经办`.
  Do not directly fill the outer `开户地址` province/city as a silent fallback,
  do not click `查询支行`, and do not choose the first visible candidate.
  It must not be extended with one-off `if city == ...` fixes or chained
  suffix/keyword guesses.
  Skipped when `_check_skip_branch` reported the field is disabled (招商银行 path).
  If no exact, verified safe-equivalent, or unique same-region higher-level
  option is found, the flow fails closed before `经办`; it must not choose the
  first visible candidate because similar place names can select a different
  city.
  Exception: when a U-BANK 常用/名册 candidate was double-clicked and submit-time
  account + recipient-name checks both pass, the U-BANK namebook branch readback
  is authoritative for that account/name pair; M3 branch mismatches are audited
  instead of blocking.
  For diagnostics, set `ZHIDAN_BRANCH_PROBE=1` to print dropdown candidates and
  stop before any branch option or `经办` click. Each branch/dropdown/query step
  saves screenshots so the attempted full name, region-level dropdown choice,
  and returned candidates can be reviewed.
- 金额: `Decimal` equality after stripping commas/spaces; rejects ≤ 0.
- 用途: strict equality after whitespace strip.
- When the beneficiary namebook auto-fills 开户银行/支行 (`payee_lookup_selected`),
  account and recipient-name checks remain mandatory. After those pass, branch
  mismatches such as an omitted road character in M3 are resolved in favor of
  U-BANK's saved namebook value and logged for review.

`ZHIDAN_ALLOW_MANUAL_SCREENSHOT_REVIEW` is **not** set by `run_batch_from_screenshots.ps1`
on purpose. Only opt in when you have already inspected the saved screenshot.

Overlay cleanup is keyboard-first. `ZHIDAN_OVERLAY_CLICK_FALLBACK=1` is the
only path that enables legacy coordinate clicks for stubborn guide popups.

## Exit Codes And Troubleshooting

- `2`: USB Hub switching failed or U-BANK could not open.
- `3`: login failed.
- `4`: main U-BANK window not found.
- `5`: single-transfer page not confirmed.
- `8`: U-BANK crash detected.
- `9`: one of the `经办` clicks failed or a submission validation error was detected.
- `10`: `bank_form.json` failed `validate_bank_form` field-level checks.
- `11`: demo mode (`form_loaded=False`) — filled + screenshot only, never clicks `经办`.
- `12`: top-nav (`转账支付`) or left-nav (`单笔转账经办`) click did not find the control.

For U-BANK crash investigation, set `UBANK_CRASH_DIAG=1` before running. In that mode the crash watcher keeps the crash scene for diagnostics instead of immediately killing `Firmbank.exe`.
