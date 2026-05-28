# 单笔转账

> 财务项目总览优先看 `C:\Users\30112\Desktop\财务\银行自动化项目总览.md`；迁移期旧总览在 `C:\Users\30112\Desktop\银行自动化项目总览.md`。如果本项目的用途、入口、运行命令、安全边界、USBHub 规则、日志/截图路径发生变化，必须同步更新总览。

## 当前状态

本技能用于农行企业网银单笔转账页面自动化：登录企业网银、进入“付款业务 > 单笔转账”、按结构化 JSON 批量覆盖填写表单。

安全边界固定如下：

- 默认只填表、不提交（测试/验证模式：占位/测试金额/截图，不产生真实制单结果）。农行也是制单项目，不是永久 fill-only；正常制单是另一独立模式，须由操作人显式开启下列 ABC_* 生产门禁，单条/逐条完成提交进银行待审核/待复核链后停止，系统不自动复核/授权/最终付款。
- 正常制单必须显式设置 `ABC_ALLOW_SUBMIT_ONCE=true`，且本次只能加载 1 条数据（单条/逐条）；脚本只点击页面「提交」一次。若页面随后弹出“请确认收款账户位数是否准确”，必须再显式设置 `ABC_CONFIRM_SUBMIT_MODAL_ONCE=true` 才会点该网页「确定」一次；若进入“交易信息确认”，必须再显式设置 `ABC_CONFIRM_TRADE_INFO_ONCE=true` 才会点该网页「确定」一次；若再出现交易 K 宝密码窗口，必须设置 `ABC_TRANSFER_KB_PASSWORD_AFTER_TRADE=true` 才会复用登录阶段同一套 K 宝密码窗口策略输入密码并确认。外置 OK 点击器只允许在交易 K 宝密码提交后触发，避免提前按物理 OK。
- 不处理验证码、二次确认或最终安全校验。
- 当前 30 口 USB Hub 下，付方 U 盾固定使用 29 口；农行制单测试默认同时打开 30 口供外置 OK 自动点击器使用。
- 浏览器默认强制国内直连：`--no-proxy-server`，并清理代理环境变量。
- 开户行不能只看输入框是否有字，必须结合 `run.log` 和 checkpoint 截图确认页面实际值。
- 第 9 条 `桂林国民村镇银行` 当前数据缺支行，仍按待核对处理，不算自动通过。

## 代码结构

`open_browser.py` 现在只是兼容旧启动命令的薄入口，核心实现已拆到 `abc_single_transfer` 包：

| 文件 | 职责 |
| --- | --- |
| `open_browser.py` | 旧入口兼容，调用 `abc_single_transfer.runner.main()` |
| `abc_single_transfer/runtime.py` | 路径、日志、环境变量、截图、域名白名单、运行产物清理 |
| `abc_single_transfer/data.py` | 转账数据加载、字段规范化、K 宝密码读取 |
| `abc_single_transfer/auth.py` | 登录页白屏重试、证书弹窗、K 宝密码窗口处理 |
| `abc_single_transfer/navigation.py` | 登录后进入“付款业务 > 单笔转账” |
| `abc_single_transfer/form.py` | 表单填写、开户行/支行选择、批量覆盖验证 |
| `abc_single_transfer/usb.py` | USB Hub 29 口 U 盾与 30 口 OK 自动点击器准备、人工等待 |
| `abc_single_transfer/runner.py` | 主流程编排 |
| `MAINTENANCE.md` | 维护说明、产物目录、自动清理策略 |

## 运行入口

项目路径：

```text
C:\Users\30112\Desktop\财务\农业\农业银行
```

常用 10 轮批量测试命令：

```powershell
cd "C:\Users\30112\Desktop\财务\农业\农业银行"
$env:TRANSFER_BATCH_PATH = "C:\Users\30112\Desktop\财务\农业\农业银行\screenshot_transfer_batch.json"
$env:ABC_USB12_PREPARE = "true"
$env:ABC_USB_HUB_PORTS = "29,30"
$env:ABC_OK_SERVO_ENABLE = "true"
$env:ABC_OK_SERVO_AFTER_KB_PASSWORD = "false"
$env:ABC_OK_SERVO_AFTER_TRANSFER_KB_PASSWORD = "true"
$env:ABC_OK_SERVO_HOTKEY = "scrolllock"
$env:ENABLE_LIVE_SCREENSHOTS = "true"
$env:LIVE_SCREENSHOT_ON_CHECKPOINT = "true"
$env:LIVE_SCREENSHOT_INTERVAL_S = "1"
$env:LIVE_SCREENSHOT_MAX_FILES = "500"
$env:ABC_KEEP_BROWSER_OPEN = "false"
& "C:\Users\30112\AppData\Local\Programs\Python\Python312\python.exe" "C:\Users\30112\Desktop\财务\农业\农业银行\skills\单笔转账\open_browser.py"
```

也可以使用项目根目录的 `run_abc_batch_local.ps1`，但排错时优先用上面的显式环境变量命令。

## 数据来源

批量场景优先使用：

```text
C:\Users\30112\Desktop\财务\农业\农业银行\screenshot_transfer_batch.json
```

支持的数据来源顺序：

1. `TRANSFER_BATCH_PATH`：批量 JSON。
2. `TRANSFER_DATA_PATH`：单条 JSON。
3. 项目根目录 `transfer_data.json`。
4. M3 数据目录：`M3_TRANSFER_DATA_DIR`、`M3_BANK_FORM_PATH`。
5. `.env` 内联字段：`TRANSFER_ACCOUNT`、`TRANSFER_NAME`、`TRANSFER_BANK`、`TRANSFER_BRANCH`、`TRANSFER_AMOUNT`、`TRANSFER_PURPOSE`。

字段映射：

| JSON 字段 | 说明 |
| --- | --- |
| `收款账号` | 必填，8-32 位数字 |
| `收款户名` | 必填 |
| `收款方开户行` | 银行大类或开户行文本 |
| `支行名称` | 支行/网点名称，用于“查询开户支行”弹窗 |
| `金额` | 必填，最多两位小数 |
| `用途` | 可选 |

## 登录与证书策略

- 登录页支持白屏恢复：直达登录页空白或超时时，会关闭空白页、新建标签页重试。
- Chrome 客户端证书自动选择默认只作用于 `https://cbank.abchina.com.cn` 和 `:443`。
- `ABC_CERT_AUTOSELECT_BROAD=true` 才会扩展到 `https://[*.]abchina.com.cn`，平时不要开。
- 证书弹窗优先走原生窗口/UIA 识别，不在整页范围乱点“确定”。
- `CERT_ENTER_AFTER_CLICK=false` 默认关闭无标题 Enter，避免把 Enter 误送进 K 宝密码框。
- K 宝密码必须先确认原生窗口标题，才会自动输入。
- 密码默认来自交互输入；只有 `.env` 同时设置 `ALLOW_INSECURE_ENV_PASSWORD=true` 和 `KB_PASSWORD` 时才读取明文密码。

## 开户行与支行策略

开户行是当前最关键的风险点，规则如下：

- 必须定位 `bankNameBtn` 附近的银行下拉面板。
- 标准 `.el-select-dropdown` 不能接受第一个可见下拉，必须靠近开户行输入框或包含银行 token。
- exact 命中并核对页面实际值才算自动通过。
- fuzzy 子串匹配不算自动通过，必须进入待核对字段。
- 选择银行后若出现“查询开户支行”弹窗，会按 `支行名称` 查询，使用共享 `公共\bank_branch_matcher.py` 给结果行打分，只有唯一 exact / safe-equivalent 行才会继续选择；随后必须确认 radio 的页面视觉选中态才点“确定”。隐藏 input 的 checked 不单独算通过，若查到候选但无法确认选中态，进入待核对，不静默通过。
- 校验会读取页面实际值，例如日志中的：

```text
[校验] 收款方开户行 页面实际值=招商银行股份有限公司杭州解放支行；目标=招商银行；候选匹配=exact
```

第 9 条当前仍是特殊数据：

```text
桂林国民村镇银行
```

页面未找到可靠候选项，且源数据缺支行；应保留为待核对，不要强行算通过。

## 运行产物

新的运行产物不再写进 `skills/单笔转账`。

单笔转账主流程：

```text
C:\Users\30112\Desktop\财务\农业\农业银行\artifacts\单笔转账
```

USB Hub 29/30 口辅助脚本：

```text
C:\Users\30112\Desktop\财务\农业\农业银行\artifacts\usb12
```

目录含义：

| 目录 | 内容 |
| --- | --- |
| `artifacts\单笔转账\debug_runs\<RUN_ID>\run.log` | 单次主流程日志 |
| `artifacts\单笔转账\debug_runs\<RUN_ID>\live_screenshots` | 整屏截图和 `manifest.csv` |
| `artifacts\单笔转账\logs\abc_transfer.log` | 主流程聚合日志 |
| `artifacts\usb12\debug_runs\usb_<RUN_ID>\run.log` | USB 29/30 口单次日志（目录名沿用 usb12 兼容旧路径） |
| `artifacts\usb12\logs\open_abc_usb12.log` | USB 29/30 口聚合日志（文件名沿用 open_abc_usb12 兼容旧路径） |
| `artifacts\单笔转账\legacy_skill_artifacts` | 从旧技能目录搬出的历史日志/截图 |
| `artifacts\root_legacy_artifacts` | 从项目根目录搬出的历史调试产物 |
| `artifacts\codex_repair_archive` | Codex/PowerShell 修复安装包和记录归档 |

## 自动清理策略

主流程每次启动会清理旧运行目录，默认：

- 保留最近 20 个 run。
- 删除超过 30 天的 run。
- 永远跳过当前 run。

项目启动器跑完后还会调用跨项目清理入口 `C:\Users\30112\Desktop\财务\公共\maintenance\cleanup_after_automation.ps1` / `.bat`，在保留原银行流程退出码的前提下统一裁剪旧截图和运行产物；6 小时内新产物会跳过。

`.env` 可调：

```text
ABC_AUTO_CLEAN_ARTIFACTS=true
ABC_ARTIFACT_RETAIN_RUNS=20
ABC_ARTIFACT_MAX_AGE_DAYS=30

USB12_AUTO_CLEAN_ARTIFACTS=true
USB12_ARTIFACT_RETAIN_RUNS=20
USB12_ARTIFACT_MAX_AGE_DAYS=30
```

若正在排查复杂问题，可临时设置：

```text
ABC_AUTO_CLEAN_ARTIFACTS=false
USB12_AUTO_CLEAN_ARTIFACTS=false
```

## 10 轮测试检查口径

跑完 10 轮后，必须同时看日志和截图。

日志必查：

```text
USB Hub 端口已打开: 29,30
已强制 Chromium 不使用代理
已完成 10 条覆盖填表验证；未自动提交任何一条
字段自动校验通过
存在待核对字段
未找到候选项
未 exact 命中
收款方开户行 页面实际值=
ABC_KEEP_BROWSER_OPEN=false
浏览器资源已释放
```

截图必查：

```text
artifacts\单笔转账\debug_runs\<RUN_ID>\live_screenshots\*表单字段已处理_收款方开户行.png
```

至少确认：

- 第 1-8 条开户行已经落到表单开户行字段。
- 第 9 条应为待核对，不能因为残留上一条开户行而算通过。
- 第 10 条应重新正确选中 `招商银行股份有限公司杭州解放支行`。
- 没有点击提交按钮。

最近一次已知真实 10 轮基线：

```text
RUN_ID: 20260511_093121_18744
字段自动校验通过: 9/10
待核对字段: 1/10，第 9 条收款方开户行
结果: 已完成 10 条覆盖填表验证；未自动提交任何一条
```

该历史 run 已从旧技能目录归档到：

```text
C:\Users\30112\Desktop\财务\农业\农业银行\artifacts\单笔转账\legacy_skill_artifacts\20260511_legacy_from_skill_dir
```

## 关键环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ABC_USB12_PREPARE` | `true` | 登录前准备农行 USB 必要端口 |
| `ABC_USB_HUB_PORTS` | `29,30` | 登录前打开的 USB Hub 端口；29 为农行 U 盾，30 为 OK 自动点击器；脚本不执行 `only/all-off` |
| `ABC_USB_HUB_COM` | `COM3` | USB Hub 控制器串口 |
| `ABC_USB_HUB_EXACT_PORTS` | `true` | 精确端口模式：关闭非 29/30 的口，只保留配置端口通电 |
| `ABC_USB_CLOSE_AUTO_HOME_TAB` | `open_browser.py` 未显式设置时按 `true` 传给 USB helper；独立运行 `open_abc_usb12.py` 默认 `false` | 是否关闭 U 盾驱动自动打开的农行官网首页标签；枚举所有标题含“中国农业银行”的顶层浏览器窗口，只匹配农行官网首页 URL，不关闭企业网银登录页/主页；显式设 `false` 可跳过 |
| `ABC_USB12_HELPER_PATH` | `open_abc_usb12.py` | USB 必要端口辅助脚本路径 |
| `ABC_KEEP_BROWSER_OPEN` | `true` | 测试时设 `false`，结束后关闭浏览器 |
| `ABC_STOP_AFTER_LOGIN` | `false` | 只登录到企业网银主页；跳过转账数据加载、付款业务导航和制单动作；登录阶段始终不会触发自动点击器 |
| `ENABLE_LIVE_SCREENSHOTS` | `false` | 整屏截图，含敏感信息，只排错时开 |
| `LIVE_SCREENSHOT_ON_CHECKPOINT` | `false` | 每个 checkpoint 额外截图 |
| `ENABLE_SCREENSHOT` | `false` | Playwright 页面截图，含敏感信息 |
| `SCREENSHOT_ON_EACH_STEP` | `true` | 配合 `ENABLE_SCREENSHOT` 控制 checkpoint 截图 |
| `KB_PASSWORD_AUTO_ENTER` | `true` | 输入 K 宝密码后是否自动 Enter |
| `ABC_CLICK_CONTINUE_AFTER_KB_OK` | `true` | K 宝物理 OK 后补查网页“继续登录”弹窗 |
| `ABC_CONTINUE_AFTER_KB_OK_WAIT_S` | `8.0` | K 宝物理 OK 后补查“继续登录”的等待秒数 |
| `ABC_OK_SERVO_ENABLE` | `false`；批量测试入口设为 `true` | 是否允许触发外置舵机按物理 OK |
| `ABC_OK_SERVO_AFTER_KB_PASSWORD` | `false` | 旧变量，仅保留兼容；登录 K 宝密码提交后不再触发舵机 OK |
| `ABC_ALLOW_SUBMIT_ONCE` | `false` | 正常制单门禁（单条/逐条，默认关闭）；必须只加载 1 条数据才允许点击页面「提交」一次 |
| `ABC_CONFIRM_SUBMIT_MODAL_ONCE` | `false` | 正常制单门禁（单条/逐条，默认关闭）；只确认提交后“收款账户位数”网页弹窗一次 |
| `ABC_CONFIRM_SUBMIT_MODAL_TIMEOUT_S` | `8.0` | 等待提交后账户位数网页弹窗的秒数 |
| `ABC_CONFIRM_TRADE_INFO_ONCE` | `false` | 正常制单门禁（单条/逐条，默认关闭）；只确认“交易信息确认”网页弹窗一次 |
| `ABC_CONFIRM_TRADE_INFO_TIMEOUT_S` | `10.0` | 等待交易信息确认网页弹窗的秒数 |
| `ABC_TRANSFER_KB_PASSWORD_AFTER_TRADE` | `false` | 正常制单门禁（单条/逐条，默认关闭）；交易信息确认后复用登录阶段 K 宝密码窗口策略输入交易 K 宝密码一次 |
| `ABC_TRANSFER_KB_PASSWORD_TIMEOUT_S` | `15.0` | 等待交易 K 宝密码窗口的秒数 |
| `ABC_OK_SERVO_AFTER_TRANSFER_KB_PASSWORD` | `false` | 代码默认 false（fail-closed）；需物理 OK 时必须显式设为 `true`（M3 生产路由会自动设置，普通测试入口不要默认开启）；仍需 `ABC_OK_SERVO_ENABLE=true`，且只有提交门禁完整通过、交易 K 宝密码已提交后才会走到这里 |
| `ABC_OK_SERVO_AFTER_TRANSFER_KB_PASSWORD_DELAY_S` | `1.0` | 交易 K 宝密码提交后到触发外置 OK 的等待秒数 |
| `ABC_OK_SERVO_AFTER_SUBMIT` | `false` | 旧兼容开关；不会在页面提交/交易信息确认后提前触发，只能在交易 K 宝密码已提交后生效 |
| `ABC_OK_SERVO_AFTER_SUBMIT_DELAY_S` | `1.0` | 旧兼容等待秒数；优先使用 `ABC_OK_SERVO_AFTER_TRANSFER_KB_PASSWORD_DELAY_S` |
| `ABC_OK_SERVO_AFTER_SUBMIT_SETTLE_S` | `3.0` | 触发外置 OK 后等待并截图的秒数 |
| `ABC_TRANSFER_BATCH_INDEX` / `TRANSFER_BATCH_INDEX` | 空 | 只运行批量 JSON 的第 N 条 |
| `ABC_TRANSFER_AMOUNT_OVERRIDE` | 空 | 覆盖金额，仅用于受控测试，例如 `0.01` |
| `ABC_OK_SERVO_HOTKEY` | `scrolllock` | 舵机控制板快捷热键，可选 `capslock`/`numlock`/`scrolllock` |
| `ABC_OK_SERVO_AFTER_KB_PASSWORD_DELAY_S` | `0.8` | 旧变量；登录阶段不再使用 |
| `ALLOW_INSECURE_ENV_PASSWORD` | `false` | 是否允许读 `.env` 明文 K 宝密码 |
| `CERT_ENTER_AFTER_CLICK` | `false` | 是否启用受控无标题 Enter |
| `ALLOW_CERT_CLICK_WITHOUT_TITLE` | `false` | 是否允许证书弹窗坐标点击兜底 |
| `ABC_CERT_AUTOSELECT_BROAD` | `false` | 是否扩展证书自动选择到全 abchina 子域 |
| `DISMISS_DROPDOWNS_HIDE` | `false` | 是否启用旧的 DOM 隐藏浮层兜底 |
| `ABC_LOGIN_GOTO_RETRIES` | `3` | 登录页白屏/超时重试次数 |
| `ABC_LOGIN_GOTO_TIMEOUT_MS` | `30000` | 登录页 goto 超时 |

## 依赖

- Python 3.12
- Playwright
- pyautogui
- python-dotenv
- pywin32

缺少 pywin32 时，原生证书窗口和 K 宝密码窗口自动处理会退化为人工。

## 外置舵机 OK

农行交易 K 宝若需要物理点击 OK，可使用外置 USB 舵机。当前已知控制板通过 `USB舵机驱动设置 V2.1.exe` 配置，建议快捷启动热键使用 `scr lock` / `scrolllock`，避免 CapsLock 改变后续输入状态。脚本不会在登录 K 宝密码阶段触发物理 OK；只有交易 K 宝密码已提交后才允许触发。必须同时打开：

```dotenv
ABC_OK_SERVO_ENABLE=true
ABC_OK_SERVO_AFTER_TRANSFER_KB_PASSWORD=true
ABC_OK_SERVO_HOTKEY=scrolllock
```

单独测试舵机位置：

```powershell
python press_ok_servo.py --press
```

注意：不要为了恢复键盘锁定状态再自动按第二次，否则可能让舵机再按一次 OK。

## 维护原则

- 不要再把大逻辑塞回 `open_browser.py`。
- 修改开户行逻辑后必须真实跑 10 轮，并检查日志和截图。
- 新增排错截图或日志时必须写到 `artifacts`。
- 不要把敏感截图、账号、金额、户名写进长期说明文档。
- 未经显式门禁的自动提交仍然禁止；正常制单须经上述 ABC_* 门禁逐级显式开启，完成后停在银行待审核/待复核链，系统不自动复核、授权或最终付款。
