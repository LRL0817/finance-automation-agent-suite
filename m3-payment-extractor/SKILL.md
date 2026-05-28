---
name: 获取数据
description: 从国瑞 OA (newoa.guorui.net) 查询并获取合同付款数据，按付款银行分类输出 JSON，并可把分类 JSON 路由到对应银行制单程序。当用户要求获取国瑞 OA/M3 合同付款数据或按银行运行制单时使用。
---

# 获取数据

从国瑞 OA 系统抽取合同付款字段，输出查询结果 JSON，并按付款银行分类。需要制单时，`run_bank_zhidan.py` / `run_bank_batch.py` 会把分类后的 JSON 交给各银行目录下已有的制单程序；U 盾、USB Hub 和提交保护规则仍由各银行脚本负责。
全程使用 Playwright 自带的 Chromium，不调用本机已安装的 Edge / Chrome。

## 前置条件

```bash
pip install playwright
playwright install chromium
```

登录态、cookie 持久化在脚本同目录的 `.browser_profile/`，首次登录后再次运行会跳过填密码。

## 如何配置凭据

所有凭据通过项目根目录的 `.env` 提供。`.env` 已在 `.gitignore` 中忽略，**切勿把真实账号/口令写进任何被 git 跟踪的文件或 `.bat` 启动器**。

1. 在项目根目录新建本地 `.env`（旧的 `.env.example` 模板已删除；如需模板可自行整理一份只含 key、不含真实值的文件留在本地，不要提交）。
2. 按需填入下列 key（值为本地真实凭据，仅保存在本机）：
   - `OA_USER` / `OA_PASS`：国瑞 OA 登录账号与口令。
   - `LOGIN_PWD`：银行制单登录口令（按各银行制单模块需要）。
   - `CERT_PWD`：U 盾 / 证书口令（按各银行制单模块需要）。
   - 其余银行模块所需 key 以对应银行制单项目说明为准。
3. 上述清单只说明“需要哪些 key、如何配置”，不含真实值；不要在 `SKILL.md`、`.bat` 或任何提交到版本库的文件里写真实账号、口令、密钥、卡号。

## 文件清单

| 文件 | 说明 |
|---|---|
| `step1_open.py` | 打开 OA 主页，自动登录（凭证从 `.env` 或环境变量 `OA_USER` / `OA_PASS` 读取），暴露 `try_login` / `USER_DATA_DIR` / `OA_URL` 给后续脚本复用 |
| `step2_navigate.py` | 集团空间 → 菜单右翻一页 → 报表中心 → 报表分析 |
| `step3_finance_contract.py` | 直接 goto 报表分析 URL → 财务报表 → 合同付款（凭证） |
| `step4_extract.py` | 按 `M3_REPORT_NAMES` 顺序轮询合同付款（云链/凭证）、日常报销、对公请款（云链/社保公积金）等报表列表前 N 条 → 对新增/列表行变化的记录逐条打开详情抽取字段和截图 → 写入 `runtime/current/`，并按付款银行分类写出 `runtime/bank_forms/<银行>/bank_form.json` |
| `bank_route.py` | 银行路由共享逻辑：读取分类 JSON / 队列 JSON，校验字段，调用对应银行项目入口 |
| `run_bank_zhidan.py` | 单笔制单入口：默认读取 `runtime/bank_forms/latest_route.json` 指向的最新 JSON，也可传入指定 JSON |
| `run_bank_batch.py` | 批量制单入口：读取 `runtime/bank_batches/*.json`，按付款银行依次调用对应银行项目 |
| `monitor_bank_batches.py` | 自动监控入口：监听 `runtime/bank_batches/*.json`，发现新增/变更业务参考号后自动调用对应银行项目 |
| `auto_extract_dispatch.py` | 无人值守总入口：循环执行 M3 抽取，更新银行队列，再按付款银行自动分流到对应银行制单项目 |
| `m3_monitor_control.py` | 人工兜底：`pause` / `resume` / `status` 三命令，读写 `runtime/control/monitor_paused.json`。暂停期间 dispatch 不抽取、不分发、不启动银行；不杀进程、不操作 USBHub、不读 `.env` |
| `verify_m3_extraction_accuracy.py` | 只读校验入口：检查已保存的 M3 抽取 `records.json` 字段、截图、列表/详情一致性和付款银行路由识别，不访问 OA、不写银行队列 |
| `run_auto_extract_dispatch_带Codex上报.bat` | 测试/验证入口：启动 `auto_extract_dispatch.py`，设置财务目录、网关目录、最终摘要上报、`M3_PRODUCTION_MODE=0`、测试金额 `0.01`、招行 M3 USB 7 口和 OA 报表直达/重试/浏览器重开兜底，无 `pause` |
| `run_auto_extract_dispatch_生产_带Codex上报.bat` | 生产入口：启动 `auto_extract_dispatch.py` 并设置 `M3_PRODUCTION_MODE=1`；不设置测试金额覆盖，不强制招行 USB 口，由 `bank_route.py` 按银行门禁开启生产制单 |
| `run_m3_preflight_dry_run.ps1` | **上线前 dry-run-bank 预检（非生产）**：以子进程方式跑 `auto_extract_dispatch.py --once --dry-run-bank --quiet-idle --no-report`，强制注入 `M3_PRODUCTION_MODE=0` / `M3_BANK_AMOUNT_OVERRIDE=0.01` / 各银行提交闸门清空 / `M3_DISABLE_BANK_INVOCATION=1`（`bank_route.run_process` 在非 dry-run 真实调用前直接返回 125，不 spawn 任何银行子进程）/ `M3_EXTRACT_TIMEOUT_SECONDS=300` 等安全 env；不启动任何银行客户端、不切 USB Hub、不发送飞书。脚本兼容 Windows PowerShell 5.1（不使用 5.1 下会冲突的 `Split-Path -LiteralPath -Parent` 与 `Measure-Object -Sum {scriptblock}` 形式）；调用 Python 时使用 `-u`，让白屏 / about:blank 等卡住现场能实时写入 `round_NN.stdout.log`。报告里会显式列出本轮注入的全部安全 env。报告写到 `runtime/preflight/preflight_<yyyyMMdd_HHmmss>/`（`environment.json` + `round_NN.stdout/stderr.log` + `round_NN.summary.json` + `report.json` + `report.md`），脱敏后只统计：抽取退出码、银行路由日志行数、空闲/skipped/未知付款银行/截图缺失/金额异常/账号异常/疑似真实银行制单动作的行数等。参数：`-Rounds N` 跑几轮、`-Seconds N` 轮间等待、`-ForceDetails 1` 让本轮 `M3_SCAN_FORCE_DETAILS=1` 重抓详情、`-ExtractTimeoutSeconds N` 覆盖单轮 OA/M3 抽取超时。**这不是真实生产** —— 真实银行调用永远走 `--dry-run-bank` 关闭路径，runner 会在检测到子进程日志疑似真实银行制单（"开始制单 / 启动 U-BANK / 点击经办 / 提交按钮" 等关键字）时直接 fail-closed 退出 1 |
| `cleanup_workspace.py` | 长期整洁脚本：迁移旧版根目录产物、清理 `__pycache__` / `*.pyc`，默认 dry-run（只预览），加 `--apply` 才真正执行 |
| `runtime/current/latest.json` | OA 原始字段（申请人/部门/日期/金额、合同名称/编号、收款单位、开户银行、银行账户、申请说明、单据编号 等） |
| `runtime/current/bank_form.json` | 当前最新查询结果的兼容格式；新流程优先使用 `runtime/bank_forms/<银行>/bank_form.json` |
| `runtime/bank_forms/` | 按付款银行自动分类的银行 JSON，例如 `runtime/bank_forms/招行/bank_form.json`、`runtime/bank_forms/兴业银行/bank_form.json`、`runtime/bank_forms/农业银行/bank_form.json`、`runtime/bank_forms/中国银行/bank_form.json` |
| `runtime/bank_batches/` | 四家银行的批量队列 JSON，每个文件是一个数组；`step4_extract.py` 会按业务参考号去重追加/更新 |
| `runtime/bank_runs/` | 路由脚本运行时生成的临时载荷目录，已加入 `.gitignore` |
| `runtime/screenshots/` | M3 查询截图，每次运行一个子目录，默认至少保留最近 7 天或最近 500 次 |
| `runtime/archive/` | 清理脚本迁移旧产物时的归档目录，默认保留最近 20 个归档 |
| `.browser_profile/` | Chromium 持久化用户目录（cookie / 会话） |

> 本次变更已删除根目录的 `.env.example` 模板与旧的 `招行制单/` 模块目录：凭据改为按上文「如何配置凭据」用本地 `.env` 提供；招行制单统一走 `bank_route.py` 路由到 `C:\Users\30112\Desktop\财务\招行\招行制单`，不再使用仓库内旧模块。

## 步骤

### 0. 无人值守自动入口

正常自动化从 M3 项目启动，而不是从某个银行项目启动。测试/验证用：

```bash
run_auto_extract_dispatch_带Codex上报.bat
```

从 M3 监控开始跑实际生产用：

```bash
run_auto_extract_dispatch_生产_带Codex上报.bat
```

两个入口都会长期循环：

1. 运行 `step4_extract.py` 按配置顺序扫描多个 M3/OA 付款报表，逐条获取新增/变更付款数据。
2. 给每条新增/变更记录写入 `M3调度批次`、`M3调度序号` 和 `M3列表序号`。
3. 根据 M3/OA 里的付款银行字段写入对应队列。
4. 调用监控逻辑识别新增/变更业务参考号，并按 M3 调度序号跨银行合并排序。
5. 通过 `bank_route.py` 选择招行、兴业、农行或中行制单。

默认每 60 秒抽取一次，每轮扫描列表前 30 条；如需只跑一轮，可直接调用：

```bash
python auto_extract_dispatch.py --once
```

默认上报为最终结果模式：每个有效轮次结束后，通过 Codex CLI 飞书新发一条业务摘要。成功时只附 **1 张 M3 详情图**（即“详情抽取后”截图，供财务同事核对单据、收款方、金额、付款单位等关键信息），不刷屏发送全流程图；失败时才附流程/现场图（最多按网关限制发送 5 张），并把失败原因翻译成财务同事能看懂的话，例如“未识别付款银行 / 缺少截图 / 金额或账号异常 / 银行页面未找到按钮 / 超时需人工核对待审核队列”。通知正文必须包含 M3/OA 原始关键字段、制单字段、成功/失败状态、退出码、遇到的问题和是否为验证模式。子流程详细上报默认关闭，避免重复消息。摘要面向财务同事，不显示本机路径、文件名、脚本名、任务 ID 等技术细节。农行路由不批量覆盖同一页面：多条农行记录会逐条生成单笔载荷，每条单独登录、填表、退出并执行 USBHub all-off；单条子流程失败时，普通业务失败码仍按既有策略：`bank_route.py` 会先关闭农行页面/脚本、执行 USBHub all-off 并自动重跑一次，第二次仍失败时才返回失败，由最终通知或网关上报；但超时码 124 一律不自动重跑，需人工核对农行待审核/待复核队列后再决定是否重跑（详见「24 小时守护运行」节）。

可选的失败上报分三层：

- M3 抽取失败：`auto_extract_dispatch.py` 通过 `C:\Users\30112\Desktop\网关` 上报。
- 银行路由失败：`monitor_bank_batches.py` 直接上报。
- 银行程序失败：`bank_route.py` 包装银行子进程并上报。

上报只用于通知和分析；网关侧 Codex CLI 不应自动修改代码、重跑流程或执行修复动作。测试启动器会设置 `M3_PRODUCTION_MODE=0`、`M3_BANK_AMOUNT_OVERRIDE=0.01`、`M3_CMB_USB_PORT=7`，通过招行测试模式的强制端口机制固定使用 USB 7 口。生产启动器设置 `M3_PRODUCTION_MODE=1`，不设置金额覆盖、不强制招行 USB 口；`bank_route.py` 会按银行统一门禁切换：招行 `ZHIDAN_TEST_MODE=0` 并由付款单位匹配 UKey，兴业设置 `CIB_ALLOW_SUBMIT=1` 且不再 `fill_only`，中行设置 `BOC_ENABLE_ORDER_SUBMIT=1` 但仍在第一步提交后停止，农行也是制单项目、与其它三家一样进入正常制单链：单条/逐条设置 `ABC_ALLOW_SUBMIT_ONCE=true` / `ABC_CONFIRM_SUBMIT_MODAL_ONCE` / `ABC_CONFIRM_TRADE_INFO_ONCE` / `ABC_TRANSFER_KB_PASSWORD_AFTER_TRADE` / `ABC_OK_SERVO_ENABLE` / `ABC_OK_SERVO_AFTER_TRANSFER_KB_PASSWORD`，交易 K宝密码提交后触发物理 OK，完成提交进银行待审核/待复核队列后停止，不自动复核、授权或最终付款；M3 测试/验证入口（`M3_PRODUCTION_MODE=0`）下农行仍 fill-only、测试金额、不产生真实制单。

> **真实制单门禁（运行生产前必读）**
>
> - 默认 `production_mode()` 为 `False`（验证模式）。未显式开启下列开关时，链路只做抽取与验证，**不会真实制单**。
> - `M3_PRODUCTION_MODE=1`：真实制单总开关。仅由 `_生产_` 版 `.bat` 设置；不要在测试入口或临时命令行里手动置 1。
> - 各银行真实提交子开关：招行 `ZHIDAN_TEST_MODE=0`、兴业 `CIB_ALLOW_SUBMIT=1`、中行 `BOC_ENABLE_ORDER_SUBMIT=1`、农行 `ABC_ALLOW_SUBMIT_ONCE=true`（并随附 `ABC_CONFIRM_SUBMIT_MODAL_ONCE`/`ABC_CONFIRM_TRADE_INFO_ONCE`/`ABC_TRANSFER_KB_PASSWORD_AFTER_TRADE`/`ABC_OK_SERVO_ENABLE`/`ABC_OK_SERVO_AFTER_TRANSFER_KB_PASSWORD`，物理 OK 仅在交易 K宝密码提交后触发）。生产链路由 `bank_route.py` 在 `M3_PRODUCTION_MODE=1` 下统一切换：农行也是制单项目，与其它三家一样单条/逐条进入正常制单链，完成提交进银行待审核/待复核队列后停止（不自动复核/授权/最终付款）；测试/验证模式（`M3_PRODUCTION_MODE=0`）下农行仍只填表、测试金额、不产生真实制单。平时不要手工设置这些子开关。
> - `M3_BANK_AMOUNT_OVERRIDE`：**会无条件覆盖真实付款金额**。仅测试用（值为 `0.01`）。运行生产 `.bat` 前必须确认该变量未在 `.env`、系统环境变量或当前会话中残留，否则会以被覆盖的金额真实制单。
> - 测试入口与生产入口分别对应两个 `.bat`：测试版不设 `M3_PRODUCTION_MODE`/设为 0 并带 `0.01` 金额覆盖；`_生产_` 版才设 `M3_PRODUCTION_MODE=1`。请按用途选对启动器，不要改 `.bat` 去混用门禁。

### 1. 打开国瑞 OA 主页并登录

- 访问 https://newoa.guorui.net/seeyon/main.do
- 持久化用户目录 `.browser_profile/` 保存登录态，cookie 跨步骤共享
- 凭证从同目录 `.env`（配置方法见上文「如何配置凭据」）或环境变量 `OA_USER` / `OA_PASS` 读取，首次自动填表点击『登 录』

```bash
python step1_open.py
```

### 2. 进入集团空间 → 翻页 → 报表中心 → 报表分析

- 点击右上角『集团空间』
- 点击菜单条右侧的下一页箭头一次
- 点击『报表中心』
- 在弹出菜单中点击『报表分析』（会弹出新页签）

```bash
python step2_navigate.py
```

### 3. 报表分析 → 财务报表 → 合同付款（凭证）

- 直接 `goto` 已知的报表分析 URL（已带登录态）
- 点击左侧『财务报表』分组
- 默认点击『合同付款（凭证）』卡片打开列表；如需单页临时切换，可设置 `M3_CONTRACT_REPORT_NAME=合同付款（云链）`。如需多页轮询，设置 `M3_REPORT_NAMES=合同付款（云链）;合同付款（凭证）;日常报销;对公请款（云链）;对公请款（社保公积金）`

```bash
python step3_finance_contract.py
```

### 4. 扫描最新 N 条 → 抽取字段 → 写出 JSON

- 在 `M3_REPORT_NAMES` 配置的每个报表列表扫描前 `M3_SCAN_ROW_LIMIT` 条记录（默认 30）。列表行在 iframe 里，脚本会遍历所有 frame；有流水号/单据号时按编号识别，无流水号的对公请款页按列表行文本指纹识别。外层列表只用于发现、定位和排序，制单字段必须逐条点进详情页后从详情表单抽取
- 已经在银行队列中且列表行文本未变化的业务参考号默认不会每轮都重写队列；为避免“详情页已变但列表行未变”漏检，脚本会按 `M3_DETAIL_REFRESH_SECONDS`（默认 900 秒）定期重新打开列表内已处理记录的详情并计算稳定业务指纹。无流水号的对公请款列表行会先用 `ROW-*` 临时键定位，但详情页拿到真实 `单据编号` 后，去重状态改用真实业务参考号，避免每轮重复入队。若详情/制单字段未变化且对应银行队列项仍存在，只更新检查时间、不重写银行队列；若详情业务字段变化，或历史状态显示未变但对应银行队列项已被清理/丢失，则按新增/变更记录重新预检并写出。需要强制重抓列表内所有记录时，设置 `M3_SCAN_FORCE_DETAILS=1`，但仍会用稳定指纹避免无变化记录重复入队
- 弹出新页『查询穿透显示』后，等到『申请人』文字出现再抽取（避免表格未渲染就读空）
- 用 JS 在主页面 + 子 frame 中按标签匹配 td/th 邻接单元格，提取以下字段：
  - 申请人 / 申请部门 / 申请日期 / 申请金额
  - 付款方式
  - 合同名称 / 合同编号
  - 收款单位名称 / 付款单位名称
  - 开户银行 / 银行账户
  - 合同金额 / 累计申请金额
  - 申请说明 / 类别
  - 单据编号（`YLHTSP-XXXX-XXXX-付-XXXX-XXXXX`，从标题旁正则匹配）
- 本轮所有新增/变更记录都会先完成字段抽取、截图绑定和 OA → JSON 预校验。**对公请款（云链/社保公积金/工资）中明确属于社保/社会保险费（含「社会保险费缴费申报表」「医疗/养老/失业/工伤/生育保险」）/公积金/工资（含「代发工资/工资发放/工资导入模板」）/个税，且 M3 明细的「开户银行/银行账户」本身就是占位值 0/无，或写成「详情请见招行代发工资导入模板/详见附件/详见模板」等指向其它来源的说明文本（不是可制单账号）的记录**优先按 fail-closed 单条隔离（不写银行队列、不启动银行，隔离到 `runtime/skipped/` 并标记 state，列表行未变化时后续轮次不再重复阻塞），即使付款银行字段为空也提示为“社保/工资/公积金类不可制单”，不要求财务补付款银行规则。**未识别付款银行**的其它记录才按 fail-closed 单条隔离：不写任何银行队列、不默认猜银行，写入 `runtime/skipped/` 并标记 state，但不再中断整轮，确保同轮其它已正确识别的记录仍能写出/分发。其它金额/账号/截图/白名单等数据质量项预校验失败时（包括普通合同付款/日常报销的账号异常、普通银行转账记录的账号被抽错、付款银行不在白名单等），仍按既有策略整轮 fail-closed、不写银行队列、不启动银行
- 新增/变更记录写入队列时会保留全局顺序：先按 `M3_REPORT_NAMES` 中的 `M3报表序号`，再按每个报表列表从上到下的 `M3列表序号`，生成跨页面统一的 `M3调度序号`。后续监控会跨四家银行队列按这个顺序一条一条执行，而不是按银行固定顺序先跑完某一家
- 写出 JSON 到统一运行目录 `runtime/`：
  - `runtime/current/latest.json`：OA 原始字段
  - `runtime/current/bank_form.json`：当前最新查询结果的兼容字段
  - `runtime/bank_forms/<付款银行>/bank_form.json`：按付款银行分类后的制单字段，其中付款银行优先来自 OA/M3 的 `付款银行`、`付款开户银行`、`制单银行` 等字段；目前内置别名会把 `招行` 归一到 `招商银行`、`农行` 归一到 `中国农业银行`、`中行` 归一到 `中国银行`。如果详情页没有付款银行字段，M3 监控链路先应用用户确认的付款单位主路由：`来参缘` → `兴业银行`，`得鲜` / `蒙特` → `中国农业银行`，`河南迅动/河南讯动` → `中国银行`；这条优先级高于本机账户表，避免同一公司在本机表里存在招行一般户时误走招行。之后才读取本机 gitignored 的 `runtime/config/m3_company_bank_accounts.local.json` 作为「付款单位全称 + 付款账号/账户性质 → 付款银行」规则表：该表只在 M3-origin 数据上生效，真实账号不进源码；同一付款单位有多个银行账户时，优先按 M3 抽到的付款账号精确匹配，其次按**唯一**账户性质匹配，再按唯一默认基本户匹配，仍不唯一时单条隔离、不默认猜。本地规则可以为唯一命中的招行账户附带双账号元数据，但这些只服务于明确走招行的场景，不覆盖上述 M3 主路由。未命中本地账户表时，才对**已人工核实、可解释**的付款单位关键词做保守兜底（映射的是「付款单位 → 实际付款银行」业务事实），例如 `巡鲜` / `得鲜` / `蒙特` → `中国农业银行`，`云炫农` → `招商银行`，`来参缘` → `兴业银行`，`河南迅动/河南讯动` → `中国银行`；不会用收款开户行/收款银行/支行名兜底；未命中仍 fail-closed。
  - USBHub 物理口与付款银行路由是两张表：M3 农行路由已经确定后，`bank_route.py` 才按付款单位注入农行 UKey 口（蒙特 11、蒙选 13、巡鲜 14、得鲜 15）和外置 OK 口 30；本机临时验收或换 Hub 时可显式设置 `M3_ABC_UKEY_USB_PORT=29` / `M3_ABC_OK_SERVO_USB_PORT=30` 覆盖物理口，只影响农行 UKey/OK 通电口，不改变付款银行路由。来参缘主路由走兴业并使用兴业 17 口；中行河南讯动/河南迅动使用 10 口；明确走招行时才使用招行物理口（云链 1-9、蒙特 12、得鲜 16、来参缘 18、云炫农 19）。得鲜招行 001/002 是同一个 UKey 的付款方账号下拉；来参缘招行 001/002 是同一个 UKey 的登录名下拉；这些双账号能力不覆盖 M3 默认主路由。
  - 分类 JSON 同时包含中文兼容字段（`收方账号 / 收方户名 / 开户银行 / 支行名称 / 金额 / 用途 / 业务参考号`）和英文兼容字段（`amount / acct_no / acct_name / bank / branch_full / branch_queries / purpose`）；其中
    - 开户银行：从 OA 的完整支行名拆出总行名（按 `BANK_HEADS` 列表前缀匹配，如『中国工商银行成都高新综合保税区支行』→『中国工商银行』）
    - 支行名称：保留 OA 的完整支行名
    - 金额：去掉千分位逗号
    - 用途：固定为『货款』
    - 业务参考号：OA 的 单据编号

```bash
python step4_extract.py
```

字段准确性可以用只读脚本复核。`verify_m3_extraction_accuracy.py` **纯只读：不开浏览器、不制单、不联网、不写银行队列**，可安全反复运行。

```bash
python verify_m3_extraction_accuracy.py --help
python verify_m3_extraction_accuracy.py
python verify_m3_extraction_accuracy.py --records runtime\verification\<run>\records.json
python verify_m3_extraction_accuracy.py --max-issues 50 --write-report
```

- `--help`：查看全部参数。
- 不带参数：默认取 `runtime/verification/` 下最新批次的 `records.json` 做只读对账。退出码 `0`=无问题，`1`=发现 issue。
- `--records <path>`：指定某一批次的 `records.json`。
- `--max-issues N`：最多列出 N 条问题。
- `--write-report`：把对账报告写到 `runtime/verification/` 下（仅写报告文件，不触碰队列与制单）。

这个脚本会检查必填字段、金额格式、账号格式、M3 列表截图/详情截图文件、列表合同号/日期与详情合同号/日期是否一致，并统计当前付款银行识别规则能识别多少条。

默认会把查询侧截图保存到 `runtime/screenshots/<运行ID>/`：

- 合同付款（凭证）列表概览
- 每条新增/变更记录点击前的列表定位截图
- 每条新增/变更记录的详情抽取后截图

截图现在是付款信息的一部分，默认强制要求存在：`step4_extract.py` 在写出
`runtime/current/latest.json`、`runtime/current/bank_form.json`、
`runtime/bank_forms/<银行>/bank_form.json` 和 `runtime/bank_batches/*.json`
之前，会校验 `M3合同付款列表截图` 与 `M3抓取详情截图` 两个字段都存在且文件可读。
任一截图缺失时，本轮抽取 fail-closed，不写银行队列、不分发制单。`bank_route.py`
在启动银行程序前也会再次校验这两个截图字段，防止旧 JSON 或人工补 JSON 绕过截图依据。

同一写出点还会先做 OA → JSON 预校验：`step4_extract.py` 在写
`runtime/current/latest.json`、`runtime/current/bank_form.json`、银行分类 JSON
或银行队列之前，要求单据编号、收款单位、银行账户、开户银行、申请金额、付款银行和
M3 截图齐全；金额必须是大于 0 的有效数字且最多两位小数；收款账号去空白后必须为
8-32 位纯数字；付款银行必须能归一到当前四家自动制单白名单。招行还要求付款单位名
用于 U 盾匹配，兴业/中行要求完整开户行。未识别付款银行的记录按 fail-closed 单条隔离
（不写银行队列、不默认猜银行，隔离到 `runtime/skipped/`），不中断整轮；**对公请款
中明确属于社保/公积金/工资/个税且 M3 明细的「开户银行/银行账户」本身就是占位值
0/无 的记录**也单条隔离（由 `should_isolate_unvoucherable_corporate_request` 严格识别：
报表必须是「对公请款」类、文本必须命中「社保/社保费/公积金/住房公积金/工资/个税/
社会保险/社会保险费/保险费缴费申报表/医疗保险/养老保险/失业保险/工伤保险/生育保险」
之一（严禁单独使用「保险」「缴费」做关键词，避免误隔离普通商业保险/普通付款）、
银行账户或开户银行必须是占位值（0/-/无/暂无/N/A/空 等）**或**指向其它来源的说明文本
（「详情请见招行代发工资导入模板」「详见招行代发工资导入模板」「招行代发工资导入模板」
「代发工资导入模板」「工资导入模板」「代发工资模板」「详情请见附件」「详见附件」
「见附件」「详情请见模板」「详见模板」「见模板」，严禁单独使用「附件」「模板」做关键词
以免误隔离普通付款）、且 M3PrecheckError 落在收款账号/开户行/账号位数等
不可制单字段上），不写银行队列也不启动银行，列表行未变化时后续轮次不再重复阻塞；
其它预校验项（普通合同付款/日常报销的账号异常、普通银行转账记录账号被抽错、金额/
截图/白名单等）失败时本轮仍直接 fail-closed，不写队列、不启动银行，
由自动调度的失败通知通道反馈原因。注意 `M3_SKIP_UNROUTABLE_RECORDS=1` 只是测试开关、
会把所有预检失败都跳过，**不能作为生产逻辑**。

截图默认至少保留最近 7 天或最近 500 次运行，可用环境变量调整：

```bash
M3_ENABLE_SCREENSHOTS=1
M3_REQUIRE_SCREENSHOTS=1
M3_SCREENSHOT_KEEP_RUNS=500
M3_SCREENSHOT_KEEP_DAYS=7
M3_SCAN_ROW_LIMIT=30
M3_SCAN_FORCE_DETAILS=0
M3_DETAIL_REFRESH_SECONDS=900
M3_DETAIL_RECHECK_MAX_PER_ROUND=5
M3_DIRECT_REPORT_FIRST=1
OA_NAVIGATION_RETRIES=4
M3_BROWSER_RESTARTS=2
M3_TOP_BLANK_GRACE_MS=2500
M3_NAV_BLANK_RESTART_AFTER_ATTEMPTS=2
OA_RETRY_DELAY_SECONDS=8
OA_NAVIGATION_BACKOFF_SECONDS=8,20,45
```

抽取脚本会把详情页截图路径写入 `runtime/current/latest.json`、`runtime/current/bank_form.json`、`runtime/bank_forms/<银行>/bank_form.json` 和 `runtime/bank_forms/latest_route.json` 的 `M3抓取详情截图` 字段。启用 `--notify-final` 时，`auto_extract_dispatch.py` 会由 `bank_route.py` 汇总本轮执行明细：成功只选择 1 张 M3 详情图随通知发送；失败才生成/附加流程联页图或失败现场图，并随成功/失败/验证状态、JSON 关键字段和财务可读的问题原因一起上报飞书；如果本轮只有“未识别付款银行”隔离记录（例如暂未维护规则的鑫锐），也会发送一条财务提示，说明该记录已隔离、不写银行队列、需要补付款单位到银行/账户规则。社保/工资/公积金类正常不可制单隔离不主动刷群，避免干扰财务同事；可用 `M3_NOTIFY_SCREENSHOT=0` 关闭最终通知图片。

OA 首页或报表页偶发超时时，`step4_extract.py` 默认先直达报表分析 URL；如果已有登录态，就不再把 OA 首页作为必经入口。只有直达后检测到登录页，或直达失败需要登录态兜底时，才打开 OA 首页并尝试自动登录。导航失败时只在当前页面 `window.stop()` 停止加载（不再主动 `goto about:blank`，避免可见浏览器在退避期间长时间停在空白页），再按 `OA_NAVIGATION_BACKOFF_SECONDS` 递增等待重试。**新增**：如果是 transient 导航错误（如 `net::ERR_CONNECTION_CLOSED`/超时）且重试到第 `M3_NAV_BLANK_RESTART_AFTER_ATTEMPTS`（默认 2）次后顶层页仍停在 `about:blank`，不再叠加 20/45 秒长退避，而是直接抛 `M3WhiteScreenError` 交给 `run()` 关闭浏览器上下文并按 `M3_BROWSER_RESTARTS` 快速重开重试——这样用户不会长时间盯着空白页，退避也不会叠加外层 `M3_EXTRACT_TIMEOUT_SECONDS` 拖成 124；非空白页的 transient 失败仍保留完整 `OA_NAVIGATION_BACKOFF_SECONDS` 退避。仍失败时关闭浏览器上下文并重开再跑。抽取失败会保存一张 `00_OA_M3抽取失败现场.png`（如果页面仍可截图），最终通知会优先附这张失败现场图；本轮不写银行队列、不启动银行，也禁止 `latest_route` 旧数据回退；全部兜底失败后只进入失败通知。

### 5. 四家银行分类 JSON

`step4_extract.py` 每抽到一笔，会按付款银行写入：

```text
runtime\bank_batches\招行.json
runtime\bank_batches\兴业银行.json
runtime\bank_batches\农业银行.json
runtime\bank_batches\中国银行.json
```

### 6. 运行对应银行制单

实际运行会启动对应银行客户端/网页，并按银行项目现有规则控制 U 盾或 USB Hub。手动运行建议先 dry-run 看清楚将使用哪份 JSON 和哪个银行入口：

```bash
python run_bank_zhidan.py --dry-run
python run_bank_zhidan.py runtime\bank_forms\招行\bank_form.json --dry-run
python run_bank_batch.py --all --dry-run
```

确认无误后运行：

```bash
python run_bank_zhidan.py
python run_bank_batch.py --bank 招行
python run_bank_batch.py --all
```

银行入口对应关系：

| 付款银行 | 队列 JSON | 被调用的银行项目 |
|---|---|---|
| 招行 / 招商银行 | `runtime\bank_batches\招行.json` | `C:\Users\30112\Desktop\财务\招行\招行制单` |
| 兴业 / 兴业银行 | `runtime\bank_batches\兴业银行.json` | `C:\Users\30112\Desktop\财务\兴业\兴业制单` |
| 农行 / 农业银行 | `runtime\bank_batches\农业银行.json` | `C:\Users\30112\Desktop\财务\农业\农业银行` |
| 中行 / 中国银行 | `runtime\bank_batches\中国银行.json` | `C:\Users\30112\Desktop\财务\中行\中国银行` |

`bank_route.py` 默认优先使用 `C:\Users\30112\Desktop\财务` 下的新目录；如果要临时指定别的财务根目录，可以设置环境变量 `FINANCE_HOME`。旧的桌面直放路径仍作为兜底候选。

### 7. 自动监控新增队列

`monitor_bank_batches.py` 会持续监听四个银行队列。默认启动时把已有记录标记为 seen，只对之后新增或内容变更的记录自动运行，避免历史数据一启动就重跑。执行时会先合并四家银行队列，再按 `M3调度批次` + `M3调度序号` 排序，所以同一轮里会严格按 M3 列表顺序跨银行逐条制单；缺少调度字段的历史记录会排在有调度字段的新记录之后，并用原银行顺序兜底。

注意：这个脚本只监听已有队列，不主动访问 OA/M3 抽取新数据。无人值守全流程请用 `auto_extract_dispatch.py`，或按场景选择测试入口 `run_auto_extract_dispatch_带Codex上报.bat` / 生产入口 `run_auto_extract_dispatch_生产_带Codex上报.bat`。

```bash
python monitor_bank_batches.py --dry-run --once
python monitor_bank_batches.py --dry-run
python monitor_bank_batches.py --quiet-idle
```

状态记录在 `runtime/monitor/auto_bank_monitor_state.json`。同一业务参考号同一份 JSON 成功或失败后不会重复跑；如果 JSON 内容变了，会重新运行。失败后如需同内容重试，可加 `--retry-failed`。

### 7.1 人工兜底：暂停 / 查询 / 恢复 M3 监控

当 M3 自动制单出现问题（未识别付款银行、缺截图、金额或账号异常、银行页面卡住、超时需要人工核对待审核队列等），财务同事可以通过 Codex CLI 让本机暂停 M3 监控；人工查余额、补资料或手工制单完成后，再让 Codex CLI 恢复监控。

```bash
python m3_monitor_control.py status
python m3_monitor_control.py pause --reason "人工查余额/补资料"
python m3_monitor_control.py resume
```

控制文件位置：`runtime/control/monitor_paused.json`（暂停标志 + 原因 + 创建时间 + 创建进程 pid + 主机名等非敏感信息；恢复后归档到 `runtime/control/archive/monitor_paused_<时间>.json`）。

语义：

- 这是 **"暂停下一轮/下一笔"** 的安全控制，**不会强杀正在执行的银行流程**——已经登录银行客户端、已经填表、已经点过经办的子流程会按各银行子项目自身的 finally / 退出逻辑收尾；本控制文件只让"下一轮抽取"和"下一笔分发"停下来。
- 暂停期间 `auto_extract_dispatch.py` **不抽取、不分发、不启动银行**；`--once` 模式下检测到 paused 直接退出 0（不视为失败）。
- 暂停期间 `monitor_bank_batches.process_once_details` 在每个银行 item 处理前都会再确认一次：发现 paused 立即停止处理本轮队列，**已处理项保留既有状态；未处理项不会被标记 failed / running**。
- 恢复后下一轮按正常监控状态处理新增/变更记录；不影响 `succeeded` / `failed` / `seen` / `interrupted` 等既有语义，也不影响 124 超时不自动重试的规则、各银行提交闸门、生产/验证模式判断、银行子流程清理等任何既有行为。
- `m3_monitor_control.py` 自身不杀任何进程、不操作银行客户端、不操作 USBHub、不读 `.env`、不修改任何提交闸门；只读写 `runtime/control/monitor_paused.json` 一个文件。

`status` 输出还会展示 `runtime/monitor/auto_bank_monitor.lock` 是否存在与 lock 里记录的 pid 是否仍存活（基于 `OpenProcess` / `os.kill(pid, 0)`），方便人工判断监控是否真的在跑、是否需要清掉旧锁。本脚本本身**不会**清锁，避免误删活进程的锁。

失败通知里会自动追加一句给财务同事看的提示："如需人工处理，请先让 Codex CLI 暂停 M3 监控；处理完后再让 Codex CLI 继续 M3 监控。" 通知不包含本机路径、脚本名、任务 ID 等技术细节。

### 8. 保持目录整洁

运行产物长期只进 `runtime/`，源码留在根目录。旧版根目录产物或 Python 缓存可以用清理脚本处理：

```bash
python cleanup_workspace.py
python cleanup_workspace.py --apply
```

第一条只预览，第二条才执行。脚本只处理白名单内的运行产物：`latest.json`、`bank_form.json`、`bank_forms/`、`bank_batches/`、`bank_runs/`、旧 `data/`、`__pycache__/`、`*.pyc`，并会按“至少 7 天或最近 500 次”的默认保留规则清理 `runtime/screenshots/`。

**自动周期清理（无人值守入口）**：`auto_extract_dispatch.py` 每轮 dispatch 结束后会调用 `C:\Users\30112\Desktop\财务\公共\maintenance\cleanup_all.ps1 -Apply`——这是一个总入口包装，会顺序触发本项目的 `cleanup_workspace.py --apply`、财务套件的 `公共\maintenance\cleanup_bank_artifacts.ps1 -Apply`，并内联清理网关 `gateway.err/out.YYYYMMDD_*.log` 旋转日志、`data\reports\*.log`、`scripts\__pycache__`（默认保最近 10 个且 7 天、新文件 6 小时内不动）。触发间隔由 `M3_AUTO_CLEANUP_INTERVAL_HOURS` 控制（默认 24h），上次触发时间持久化到 `runtime\m3_cleanup_state.json`，所以重启 dispatch 不会立刻再触发一次；超时由 `M3_AUTO_CLEANUP_TIMEOUT_SECONDS` 控制（默认 900s）；想完全关掉设 `M3_AUTO_CLEANUP_ENABLED=0`，单次跳过加 `--skip-cleanup`。清理失败/超时**不影响**主抽取-分发循环。

## 已知问题

- **付款银行字段可能为空**：如果 OA/M3 详情页没有展示 `付款银行 / 付方开户行 / 制单银行` 等字段，脚本只会按已人工核实、可解释的付款单位关键词做保守兜底（`PAYER_BANK_FALLBACKS`）；仍未命中时按 fail-closed **单条隔离**——不写任何银行队列、不默认猜银行，记录隔离到 `runtime\skipped\` 并标记 state，不中断整轮（同轮其它已识别记录正常写出/分发）。需人工核实该付款单位的真实付款银行后补充 `PAYER_BANK_FALLBACKS` 再重跑该条；不得用收款开户行/收款银行/支行名推断付款银行。
- **开户地省 / 市/县 / 联行号**：OA 没有这些字段，当前只按查询结果原样输出。
- **持久化目录互斥**：每个步骤会独占 `.browser_profile/`。运行下一步前请先关掉上一步的浏览器窗口，否则会报 `TargetClosedError`。

## 注意事项 / 已知风险

- **不要把敏感目录提交到版本库**：提交前确认 `.claude/`、`.env`、`runtime/`、截图文件不进 git。`.gitignore` 已忽略 `.env`、`runtime/`、`.browser_profile/`、`__pycache__/`、`*.pyc` 等；`.claude/` 建议也加入 `.gitignore` 后再提交（如尚未包含需补充）。
- **凭据与收款信息不得硬编码**：被跟踪的源码、`.bat`、`SKILL.md` 中都不得写入真实账号、口令、密钥、卡号，也不得硬编码真实收款账号/户名/金额；关键字段缺失时应 fail-closed（拒绝继续并报错），而不是用默认值兜底。
- **生产单笔金额上限可配置（`M3_MAX_PAYMENT_AMOUNT`）**：默认**为空＝只做金额格式校验、不限额**（不硬编码任何真实上限）。若显式设置为正数，则 `bank_route.normalize_form` 在写银行队列/启动银行前校验：单笔金额超过该上限即 **fail-closed**（抛错终止、不启动银行），并打印超限原因。该机制只防"单笔异常大额"，不是单日累计上限；运行 `_生产_` 版 `.bat` 前仍需人工核对待制单队列内容与每笔金额，并确认 `M3_BANK_AMOUNT_OVERRIDE` 未在 `.env`、系统环境变量或当前会话残留。
- **外发内容含敏感信息**：截图与飞书摘要可能包含收款账号、金额等付款信息，注意接收范围，避免发到不该看到的群/人。
- **招行登录名字段是双账号特殊场景能力，默认不写入 bank_form**：登录名下拉选择只用于「同一台 U-BANK 挂多个登录名」的双账号公司，不是招行默认必配项。来参缘 001/002 是同一个 UKey，明确走招行时应在登录窗口切换登录名，并用 `CMB_DISABLE_PAYER_ACCOUNT_SELECTION=true` 禁止再按付款账号尾号操作制单页「付款方账号」下拉；得鲜 001/002 是同一个登录名下的两个付款方账号，明确走招行时只写 `CMB_PAYER_ACCOUNT_SUFFIX=001/002`，不设置登录名选择。注意：M3 监控默认主路由不是这两条招行一般户，`来参缘` 走兴业，`得鲜` 走农行。`bank_route.normalize_form` 默认会**丢弃**源 form 里的 `招行登录名 / CMB_LOGIN_ACCOUNT_NAME / CMB登录名` 字段，普通单账号公司的 bank_form 不会带这些字段，下游 `制单.py` 也就不会去操作登录名下拉，沿用 U-BANK 当前默认登录名；这样可以避免单账号公司被启动早期下拉候选短暂为空（Win32 `CB_GETCOUNT=0` + UIA 也未挂好）的时序问题卡住。仅当源 form 明确同时含登录名值**和**强制开关 `CMB_REQUIRE_LOGIN_ACCOUNT_SELECTION=true` 或 `招行强制选择登录名=true` 时，归一化才把值和开关一起镜像写入 bank_form。源 form 含登录名但无强制开关时，会打印 `[M3 路由] bank_form 含登录名字段，但未设置 CMB_REQUIRE_LOGIN_ACCOUNT_SELECTION=true/招行强制选择登录名=true，归一化时已丢弃；沿用 U-BANK 默认登录名`。一旦启用，下游 `制单.py` + `ubank_common.login_ubank` 仍然按严格 fail-closed 选择目标登录名（精确相等、唯一、回读校验），**避免 001/002 选错**；密码 / 证书密码必须在登录名选择成功后才读取。**普通单账号公司不要在 bank_form 里写登录名字段，也不要为流程整体设置 `CMB_LOGIN_ACCOUNT_NAME` / `CMB_LOGIN_ACCOUNT_INDEX`**。

## 24 小时守护运行（watchdog / 子流程超时 / 崩溃恢复）

当前链路本就能长期循环（`auto_extract_dispatch.py` 轮询）。下列补强让它更适合 24 小时无人值守，但**不改变业务边界**：系统只负责制单/经办录入到银行待审核/待复核队列，**不自动复核、授权、最终付款**。

- **银行子流程统一超时**：`bank_route.run_process()` 对每个银行子进程加 `BANK_ROUTE_PROCESS_TIMEOUT_SECONDS`（默认 `900` 秒，可被环境变量覆盖；设 `<=0` 表示禁用，仅在明确知道风险时用）。超时后强制结束子进程、返回固定退出码 **124**（区别于业务退出码 1/2/9/13），并打印银行名、工作目录、命令摘要、超时秒数。超时会尽力触发对应银行清理：农行 `cleanup_abc_runtime_after_failure()`+`abc_usb_hub_all_off()`、中行 `cleanup_boc_runtime_after_failure()`+`boc_usb_hub_all_off()`；招行/兴业**无专用清理函数**，超时仅记录日志，依赖银行子项目自身 `finally` 收尾与 `cleanup_after_automation` 外层清理，本调度层不新增强制关闭/all-off 以免误伤。**超时码 124 一律不自动重试（含 ABC/BOC）**：超时可能落在"已提交进银行待审核/待复核队列但脚本未正常退出"的灰区，自动重跑会重复制单，因此 ABC/BOC 的 retry loop 检测到 124 即停止重试、保留为失败（由 monitor 记为 failed），需**人工核对对应银行待审核/待复核队列后再决定是否重跑**；CMB/CIB 本就无内部重试（超时即失败不重试）。其它业务失败码（1/2/9/13 等）的既有重试行为**保持不变、未改动**。
- **OA/M3 抽取子流程超时**：`auto_extract_dispatch.py` 调用 `step4_extract.py` 时使用 `M3_EXTRACT_TIMEOUT_SECONDS`（默认 `900` 秒；`run_m3_preflight_dry_run.ps1` 默认注入 `300` 秒）。如果浏览器停在 `about:blank`、OA 白屏或 Playwright 卡死，外层会停止该轮抽取进程树并返回 **124**，本轮不写银行队列、不调用银行路由；下一轮由 monitor/watchdog 重新开始。Python 子进程使用 `-u`，便于卡住时实时查看日志。
- **顶层 `about:blank` 白屏内部快速重启（不必干等外层超时）**：`step4_extract.py` 在列表行 / 详情页等待阶段会检测**顶层窗口**本身停在 `about:blank`（含 `about:blank#...`）且正文基本为空的情况——这类「连 report frame 都没有」的白屏过去只靠 report iframe 检测会漏判、要拖到外层 `M3_EXTRACT_TIMEOUT_SECONDS`。命中后直接抛 `M3WhiteScreenError`，由 `run()` 关闭浏览器 context 并按 `M3_BROWSER_RESTARTS`（默认 2）立即重开重试；`find_frame_with_payment_rows`（列表）和 `wait_detail_ready`（新开详情页）都覆盖。为避免误伤 `reset_page_before_retry` 主动 `goto about:blank` 的瞬间，只有等待超过 `M3_TOP_BLANK_GRACE_MS`（默认 `2500` 毫秒）后**连续**命中才判失败，日志会写明 `url=about:blank...` 便于排查。
- **抽取失败/超时后只清 M3profile 浏览器**：抽取超时(124)或 `step4_extract.py` 非 0 退出后，`auto_extract_dispatch.py` 会做 scoped cleanup——只结束命令行里包含本项目 `.browser_profile` 路径的 `chrome/chromium/msedge` 进程，**绝不误杀用户普通 Chrome**。仅在抽取失败/超时后触发（成功不清）；cleanup 失败/超时只记录日志，**不掩盖原退出码**。
- **详情定期复查分批（避免单轮过长 / OA 白屏超时）**：`step4_extract.py` 把 `should_extract_row` 选中的记录拆成两类：
  - **新增 / 列表行变化 / `M3_SCAN_FORCE_DETAILS=1` 强制重扫**：**始终当轮处理，不受任何分批上限限制**。
  - **纯"详情定期复查（`M3_DETAIL_REFRESH_SECONDS` 秒，默认 900）"**：按 `M3_DETAIL_RECHECK_MAX_PER_ROUND`（默认 `5`）分批，按 `detail_checked_at_epoch` 最久未检查优先；超过预算的旧记录**本轮暂缓**，**不**标记失败、**不**写 `skipped`、**不**更新 `detail_checked_at`，下一轮自然轮到。日志会打印"本轮定期复查候选 X 条，按 M3_DETAIL_RECHECK_MAX_PER_ROUND=5 处理 Y 条，暂缓 Z 条。"。
  - `M3_DETAIL_RECHECK_MAX_PER_ROUND<=0` 表示**不限制**，仅用于人工排查；生产保持默认 `5`，目的是避免单轮把 30 条详情/采购合同链接连续打开导致接近或超过 `M3_EXTRACT_TIMEOUT_SECONDS`。
  - `M3_SCAN_FORCE_DETAILS=1` 是**人工强制全量重扫**，会绕过该分批（理由是"强制详情扫描"不属于"纯定期复查"），可能耗时很长，请仅在排查时使用、不要长期开启。
- **崩溃恢复三态区分**：
  - `--run-existing` / 不带：是否在启动时把队列里**全新**项目立即处理。不带（默认）会 `seed` 成 `seen`，避免首次接入历史队列误跑大量旧数据；`--run-existing` 跳过 seed、立即处理现有全新项。
  - `--resume-existing`（或 `M3_MONITOR_RESUME_EXISTING=1`）：**崩溃恢复**。只把上次进程死亡时仍停在 `running` 的项恢复为 `interrupted` 重新处理（监控单实例锁保证 `running` 必属已死进程）。**不重跑 `succeeded`/`seen`，不影响 `failed`**，因此不会整批误跑历史。生产 watchdog 默认开启此项。
  - `--retry-failed`：记录为 `failed` 且 fingerprint 不变时才允许重试；默认不无限重试。
  - 新增/变更 fingerprint：照常跑。
- **24 小时守护脚本**：`run_auto_extract_dispatch_生产_watchdog.ps1`（PowerShell，Windows 友好）。外层循环反复拉起**现有**生产入口 `run_auto_extract_dispatch_生产_带Codex上报.bat`（环境变量与网关上报仍以该 bat 为唯一来源），进程异常退出后等待 `M3_WATCHDOG_RESTART_DELAY_SECONDS`（默认 45s，钳制 30–120）再重启；watchdog 日志写 `runtime/watchdog/`。自身单实例（watchdog 锁）；**尊重 monitor 锁、绝不盲目 `--force-lock`**：仅当锁所属 PID 已不存在（确凿陈旧锁）才安全删除那一个锁文件并记录原因，锁被活进程持有时跳过本轮并复查。设置 `M3_MONITOR_RESUME_EXISTING=1` 走崩溃恢复。不吞退出码（每轮记录 ExitCode，失败上报仍由生产 bat 内网关负责）。
  - 启动方式（运行者手动执行；本仓库交付不代为运行）：
    `powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\30112\Desktop\M3直供合同付款数据获取\run_auto_extract_dispatch_生产_watchdog.ps1"`
- **守护边界**：watchdog 只能在**进程异常退出后重启**，**不能**保证桌面会话、UKey/U盾、USBHub、银行客户端、网络、OA 登录态长期可用——这些硬件/会话故障需人工处理；watchdog 不会、也不应替你重新插 U盾或重登 OA。
- **跨项目全局银行自动化锁（与"来参缘定时调拨"互斥）**：`bank_route.run_process()` 在真正 spawn 银行子进程前会持有一把**全局**锁，模块在 `C:\Users\30112\Desktop\财务\公共\locks\bank_automation_lock.py`，文件位置 `C:\Users\30112\Desktop\财务\runtime\locks\bank_automation.lock`，owner 标记为 `m3_bank_route`。`dry-run` / `M3_DISABLE_BANK_INVOCATION=1` / 纯路由验证 / 纯 preflight 等"不真正开银行"的路径**不**拿这把锁。锁忙或锁模块缺失时 `run_process` 直接 fail-closed 返回退出码 **126**（"未启动银行子进程"），区别于 124（超时）和 125（`M3_DISABLE_BANK_INVOCATION` 阻断）—— 该退出码**不自动重试，不强抢锁，不执行 USBHub all-off**，避免影响正在运行的来参缘或其它银行流程；由人工稍后核对调度日志后再决定是否重跑。生产银行路由会持有这把全局锁，与来参缘定时调拨互斥。锁文件 / `runtime/` 目录均已被 `.gitignore` 覆盖。

## 字段映射速查

| OA 字段 | 输出字段 | 备注 |
|---|---|---|
| 银行账户 | 收方账号 | 直接复制 |
| 收款单位名称 | 收方户名 | 直接复制 |
| 开户银行（完整含支行） | 开户银行（总行名） | 用 `BANK_HEADS` 前缀拆 |
| 开户银行（完整含支行） | 支行名称 | 完整保留 |
| 申请金额 | 金额 | 去千分位 |
| 固定值『货款』 | 用途 | 不再读取 OA 申请说明 |
| 单据编号 | 业务参考号 | 下游系统可按需使用 |
