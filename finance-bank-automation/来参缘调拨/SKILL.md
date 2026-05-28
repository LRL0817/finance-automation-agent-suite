---
name: laicanyuan-tiaobo
description: One-shot CMB → CIB 调拨 runner driven by login name 来参缘潘思婷002. Reads CMB U-BANK aggregate balance, floors the balance down to a whole 10000-RMB unit, and leaves the sub-10000 remainder on the 002 account. Real payee identity (account/name/branch) lives in the gitignored local config only. Safety boundary mirrors 招行制单: test mode by default; production gate is LAICANYUAN_TRANSFER_PRODUCTION=1 (only then is ZHIDAN_TEST_MODE=0 allowed). Never auto-reviews, authorizes, confirms, or pays.
---

# 来参缘调拨

专用的招行 → 兴业内部调拨流程。**只做经办/制单进入「待审核」队列后停止**，不复核、不授权、不确认、不最终付款。

> 真实收方账号 / 户名 / 支行只保存在 `laicanyuan_payee.local.json`（已被本目录 `.gitignore` 忽略），不写进本说明、模板、示例或运行参数。本文档中举的所有「收方」字段都只描述形状，不描述具体值。

## 安全边界一句话总结

- **默认 = 测试模式**。runner 内部强制把子进程 `ZHIDAN_TEST_MODE` 写成 `"1"`，只点第一次「经办」并截图，不向招行待审核 / 待复核队列写入任何真实流水。
- **生产开关 = `LAICANYUAN_TRANSFER_PRODUCTION=1`**，必须由操作员在当前 PowerShell / CMD 会话里显式设置；只有这个开关为 `1` 时，runner 才会把子进程 `ZHIDAN_TEST_MODE` 改为 `"0"`，去点第二次「经办」并把单据写入招行待审核 / 待复核队列后**立即停止**。
- **金额规则 = 按万元向下取整转出**：`transferable = floor(balance / 10000) * 10000`；万元以下零头留在 002 账户。余额 `< 10000` → fail-closed，不制单。例如 `109021.98 -> 100000`。
- **真实收方信息 = 仅在 gitignored 的 `laicanyuan_payee.local.json`**，禁止写入本文档、模板、`.bat`、源码默认值。
- **绝不自动复核 / 授权 / 确认 / 最终付款**。复核、授权、付款仍由人工和招行系统完成。
- **来参缘招行 UKey 端口固定 18**：来参缘招行 001/002 是同一个 UKey，物理口固定为 `18`，差异只在 U-BANK 登录窗口选择 `来参缘潘思婷002`。`LAICANYUAN_CMB_USB_HUB_PORT` / `LAICANYUAN_USB_HUB_PORT` 仅用于排障覆盖，且也必须是 `18`；填其它口一律 fail-closed，不打开 U-BANK、不切 USBHub。
- **单实例锁防重入**：runner 启动时在 `runtime\laicanyuan_transfer.lock` 写入 `pid` / `started_at` / `run_dir`，同一时刻只允许一个真实运行实例存在。其它实例发现锁的 pid 仍存活会直接 fail-closed（退出码 27），既不会重复打开 U-BANK，也不会重复切 USB Hub 或重复点经办。锁文件不影响任何其它银行 / M3 / 网关流程，`runtime/` 目录已被 `.gitignore` 忽略。
- **全局银行自动化锁防与 M3 并发**：除本地单实例锁外，runner 在真正开始动银行 / USB Hub 之前还会拿一把**跨项目**的全局锁（`财务\runtime\locks\bank_automation.lock`，模块在 `财务\公共\locks\bank_automation_lock.py`）。如果同一时刻 M3 银行制单路由也在跑，全局锁会被它持有；本 runner 不等、不强抢，直接 fail-closed 退出码 28（"银行自动化忙，本轮跳过，未启动 U-BANK / USB Hub"）。如果全局锁模块缺失，也按同一退出码 fail-closed，拒绝在无互斥保护下打开银行。定时任务不会与 M3 银行制单并发；拿不到全局锁则跳过本轮。`--dry-run-amount-only` 不接触银行 / USB Hub / bank_form，跳过本锁。

## 入口

测试模式（默认，单次第一次「经办」截图后停止）：

```powershell
C:\Users\30112\Desktop\财务\来参缘调拨\run_laicanyuan_transfer_测试.bat
```

或直接调用：

```powershell
python C:\Users\30112\Desktop\财务\来参缘调拨\run_laicanyuan_transfer.py
```

生产模式（**显式确认才能用**，会向「待审核」队列提交一笔真实经办流水）：

```powershell
$env:LAICANYUAN_TRANSFER_PRODUCTION="1"
C:\Users\30112\Desktop\财务\来参缘调拨\run_laicanyuan_transfer_生产_带Codex上报.bat
```

## 定时运行（每天 10:00 / 16:00）

为方便无人值守每日定时调拨，本目录提供一组与现有手工入口完全独立的 PowerShell 脚本：

| 脚本 | 作用 |
| --- | --- |
| `run_laicanyuan_transfer_scheduled.ps1` | 计划任务专用 wrapper：**只在本进程内**设置 `LAICANYUAN_TRANSFER_PRODUCTION=1` / `USB_HUB_COM=COM3` / `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8`，然后 `python -X utf8 run_laicanyuan_transfer.py`，把 runner 输出同步写到 `runtime\scheduler\scheduled_<时间戳>.log`。退出码原样透传，**不重试业务流程**；跑完后无论成功/跳过/失败都会调用网关 `report_to_codex.py` 发一条财务口径结果通知（上报默认最多 3 次、间隔 20 秒，可用 `LAICANYUAN_REPORT_RETRY_COUNT` / `LAICANYUAN_REPORT_RETRY_DELAY_SECONDS` 调整；`-NoGatewayReport` 跳过上报，仅用于排障）。PowerShell 5.1 下会临时关闭 native stderr 的 terminating-error 行为，保留 Python 完整 traceback 和真实退出码。 |
| `install_laicanyuan_transfer_schedule.ps1` | 安装 Windows 计划任务（默认 dry-run，必须 `-Apply` 才真正注册）。同名任务存在时**默认 fail-closed**，需 `-Replace` 才覆盖。 |
| `uninstall_laicanyuan_transfer_schedule.ps1` | 卸载计划任务（默认 dry-run，必须 `-Apply` 才真正 unregister）。 |
| `get_laicanyuan_transfer_schedule_status.ps1` | 只读：打印任务状态 / 上次运行 / 上次结果 / 下次运行；**不**启动 / 停止 / 修改任务。 |

计划任务参数：

- 任务名：`财务-来参缘调拨-每日10点16点`
- 触发器：两条 Daily trigger，分别 10:00 / 16:00
- Action：`powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<本目录>\run_laicanyuan_transfer_scheduled.ps1"`，工作目录 = 本目录
- Principal：**当前 Windows 用户**，`LogonType=Interactive`。安装脚本优先注册 `RunLevel=Highest`；如果当前 PowerShell 未提升权限导致 Access denied，会自动降级为 `RunLevel=Limited` 并在安装日志 / 状态脚本里显示（**不是**「无论是否登录都运行」—— 银行 GUI 自动化必须可见桌面）
- Settings：`StartWhenAvailable=$false`（不补跑漏掉的触发，避免在不可控时间动手）、`MultipleInstances=IgnoreNew`（前一轮没跑完则跳过下一轮，避免并发）、`ExecutionTimeLimit=01:00:00`

运行条件（任一不满足，本轮要么 fail-closed 要么完全不被触发）：

- 电脑已开机，并以**计划任务 Principal 指定的 Windows 账号**登录。
- 桌面未锁定（UKey 驱动弹出的窗口、银行客户端、Playwright 浏览器都需要可见桌面）。
- 来参缘招行 U 盾插在 `18` 口、Hub 通电、`COM3` 可用。来参缘 001/002 不是两个 U 盾，runner 通过登录名选择 002。
- 网关保持 `C:\Users\30112\Desktop\网关` 服务运行。定时 wrapper 会在 runner 结束后主动上报结果；即使网关存在 `data\reporting_disabled.flag` 暂停普通自动上报，本定时结果也会用显式 `--ignore-disabled-flag` 发送，避免 10:00 / 16:00 静默无反馈。

安装 / 查询 / 卸载示例：

```powershell
# 1. dry-run 预览（推荐先跑一次确认所有字段都对）
powershell -NoProfile -ExecutionPolicy Bypass -File `
    "C:\Users\30112\Desktop\财务\来参缘调拨\install_laicanyuan_transfer_schedule.ps1"

# 2. 正式安装
powershell -NoProfile -ExecutionPolicy Bypass -File `
    "C:\Users\30112\Desktop\财务\来参缘调拨\install_laicanyuan_transfer_schedule.ps1" -Apply

# 3. 查看任务状态
powershell -NoProfile -ExecutionPolicy Bypass -File `
    "C:\Users\30112\Desktop\财务\来参缘调拨\get_laicanyuan_transfer_schedule_status.ps1"

# 4. 卸载（dry-run，再加 -Apply 才删）
powershell -NoProfile -ExecutionPolicy Bypass -File `
    "C:\Users\30112\Desktop\财务\来参缘调拨\uninstall_laicanyuan_transfer_schedule.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -File `
    "C:\Users\30112\Desktop\财务\来参缘调拨\uninstall_laicanyuan_transfer_schedule.ps1" -Apply
```

**仍是同一套安全边界**：

- 10:00 / 16:00 触发后，wrapper 在**自己这个 PowerShell 进程**里写入 `LAICANYUAN_TRANSFER_PRODUCTION=1` 后立即调用现有的 `run_laicanyuan_transfer.py`，wrapper 退出该变量自动消失；不写 `.env`、不写用户级 / 机器级环境变量、不污染其它会话。
- runner 内部仍按"余额向下取整到万元 → 写 bank_form → 制单到待审核 / 待复核队列后停止"的既有规则；定时任务**不引入任何新的复核 / 授权 / 确认 / 最终付款路径**。
- runner 自身的单实例锁（27）+ 全局银行自动化锁（28）依然生效；如果 M3 银行制单路由正在跑，本轮直接退出码 28、不动 U-BANK / USB Hub。
- 结果通知：计划任务每轮结束都会通过网关发一条飞书/Codex 业务摘要，状态分为 `completed`（完成）、`skipped`（本地锁/全局锁忙，本轮未启动银行）、`failed`（配置、余额、USBHub、制单等失败）。上报失败只写入调度日志，不改变 runner 退出码，也不触发业务重试；上报自身会短暂重试，防止刚好撞上网关忙。
- 失败时排查顺序：① 飞书结果摘要 → ② 本目录 `runtime\scheduler\scheduled_*.log`（wrapper 自己的调度日志）→ ③ `runs\laicanyuan_<时间戳>\run.log` 等四件套（runner 内部日志）→ ④ 网关 `data\reports\` 下的飞书上报记录。

## 流程

1. 加载 `laicanyuan_payee.local.json`（真实收方配置，**未提交到 git**）。缺失或字段不全 → 立即 fail-closed。
2. 子进程环境固定 `CMB_LOGIN_ACCOUNT_NAME=来参缘潘思婷002`、`USB_HUB_FORCE_PORT=18`、`ZHIDAN_ALLOW_FORCE_USB_PORT=1`、`ZHIDAN_TEST_MODE=1`。只有当父进程显式 `LAICANYUAN_TRANSFER_PRODUCTION=1` 时，子进程 `ZHIDAN_TEST_MODE=0`。
3. 通过 `公共/usbhub/.../hub_ctrl.py` 主动切到 18 口（只在 runner 子进程链路里强制端口；不修改全局付款单位→端口映射；其它口被明确拒绝）。
4. 调起 `招行/查询招行余额/scripts/query_balance.py --page auto --save-all-texts`（只读，登录名 002）读取人民币余额。`--page` 可通过 `LAICANYUAN_BALANCE_PAGE` 改成 `current` / `home` / `workbench`（仅用于排障），默认 `auto`。
   - 必须返回 `status=OK`。
   - `page_type` 必须是 `home` 或 `workbench`：来参缘 002 实测登录后首页空白（无「人民币账户实时余额合计(元)」），余额实际在 `工作台` 上的「人民币总资产(元)」，所以 `--page auto` 会自动尝试 `current → 首页 → 工作台` 并取第一个 `status=OK` 的页面。
   - `balance_label` 必须在白名单内：`人民币账户实时余额合计(元)`（home）或 `人民币总资产(元)`（workbench），且必须与 `page_type` 配对，否则 fail-closed。
   - `company` 必须严格等于本目录 `laicanyuan_payee.local.json` 里的 `付款单位名称`（真实值仅维护在该 gitignored 本地文件，不出现在本说明里）；空、不一致或读不到 → fail-closed。
   - `balance_decimal` 必须能 `Decimal` 精确解析；缺失 / 非法 / 负数 → fail-closed。
   - 若 `page_type=home`：保留 `account_count==1` 校验（多账户无法唯一锁定 002 的付款账户）。
   - 若 `page_type=workbench`：不再要求 `account_count`（工作台不提供），但**必须**通过「存款 vs 总资产」一致性兜底。算法：
     1. 在 `all_texts` 里收集所有 `存款` / `存款(元)` / `存款（元）` 标签项（实测一个工作台上可能有多张资产卡片各自带 存款 子标签）。
     2. 在多个 存款 label 中，按到「`人民币总资产(元)` label」(`result.label_control.rect`) 的二维距离选 **最近** 一个，即主资产卡片那张。
     3. 在该 存款 label 的「同卡片几何范围」里找唯一金额：水平中心 x 距离 ≤ 80 px，金额完全位于 label 上方或下方且垂直 gap ≤ 80 px。两种布局都支持：**金额在标签上方**（实测来参缘 002 工作台：存款金额 y=712，存款 label y=734）或 **金额在标签下方**（旧布局假设）。命中后 audit 日志写明 `定位来源=above-label` 或 `below-label`。
     4. 计算过程中显式排除「人民币总资产(元)」的主金额 rect（`result.balance_control.rect`），避免把总资产再当成存款。
     5. **fail-closed 触发**：
        - 同卡片几何内**找不到**金额（标签缺失 / 金额超出几何容差 / 数字格式无法解析）→ fail-closed（status=`not_found`）。
        - 同卡片几何内**多个不同金额**候选，无法唯一判断 → fail-closed（status=`ambiguous`）。
        - 唯一金额 ≠ `balance_decimal` → fail-closed（status=`mismatch`，疑似含 理财 / 票据 / 信用证 等非现金资产）。
     6. 通过深一层的「**理财 / 票据等非现金资产同卡片金额必须为 0**」防御：对 `理财` / `理财(元)` / `理财（元）` / `票据` / `票据(元)` / `票据（元）` 走同一套同卡片几何识别，若任何一项非现金资产金额非 0（或自身就 ambiguous），即使存款数学上 == 总资产也 fail-closed —— 这种情况通常意味着工作台版本变了 / 卡片布局非预期。
   - 始终不接受 理财 / 票据 等非现金字段作为可转金额来源；只接受白名单两个标签。
5. 关闭 U-BANK + USB Hub 全口下电，避免 U 盾长时间通电。
6. 按"余额向下取整到万元"的规则计算金额：
   - `balance < Decimal("10000")` → fail-closed（余额不足 1 万元，本轮不调拨）。
   - 否则 `transferable = floor(balance / 10000) * 10000`；例如 `109021.98 -> 100000`，万元以下零头留在 002 账户。
   - `金额` 永远是万元整数，不带小数；若余额刚好是整万元，会按整万元转出。
7. 备份现有 `招行/招行制单/M3直供合同付款数据获取/bank_form.json`（如有），再写入本次的 bank_form。
8. 调起 `招行/招行制单/招行/skills/制单单账号单笔转账skill/制单.py`，由它处理 U 盾切换、登录、填表、经办。

## 写入的 bank_form 字段（runtime-only，未提交到 git）

| 字段 | 来源 |
| --- | --- |
| `付款单位名称` | `laicanyuan_payee.local.json::付款单位名称` |
| `招行登录名` | 固定 `来参缘潘思婷002`（透传给 `CMB_LOGIN_ACCOUNT_NAME`） |
| `收方账号` / `收方户名` / `开户银行` / `支行名称` | `laicanyuan_payee.local.json` |
| `金额` | 计算得到的可调拨金额，整数字符串 |
| `用途` | 固定 `转款` |
| `业务参考号` | `LCY-<yyyyMMdd-HHmmss>` |

## Fail-closed 触发条件

- `laicanyuan_payee.local.json` 不存在 / 字段缺失 / 字段值含逗号/小数（收方账号必须纯数字）。
- `LAICANYUAN_CMB_USB_HUB_PORT` / `LAICANYUAN_USB_HUB_PORT` 显式配置为非 18、非数字、越界，或 18 口切换失败。
- `query_balance` 子进程退出码非 0 / `status != OK` / `page_type` 不在 `{home, workbench}` / `balance_label` 不在 `{人民币账户实时余额合计(元), 人民币总资产(元)}` 或与 `page_type` 不配对 / `company` 与 `laicanyuan_payee.local.json::付款单位名称` 不严格相等 / `balance_decimal` 缺失 / 非法 / 解析为负。
- `page_type=home` 时 `account_count` 缺失 / 非整数 / != 1。
- `page_type=workbench` 时：找不到「存款」标签或其同卡片金额（上下两种布局都试过）/ 同卡片几何范围内多个不同金额候选无法唯一判断 / 唯一存款金额 ≠ 「人民币总资产(元)」 / 理财 / 票据等非现金资产同卡片金额非零 —— 任一情况都视为"无法证明人民币总资产全部为可用存款"，fail-closed。
- 余额 < 10000 元；或按万元取整后金额异常。
- 现有 bank_form.json 备份失败。
- 已有来参缘调拨实例在跑（`runtime\laicanyuan_transfer.lock` 中 `pid` 仍存活），新实例直接 fail-closed，退出码 27；不打开 U-BANK、不切 USB Hub、不写 bank_form。锁文件无法解析或 stale 后删除失败也按"活跃锁"处理同样 fail-closed；pid 已不在则当作 stale 锁安全删除并续接，日志写明。
- 全局银行自动化锁被另一个 owner（通常是 M3 银行制单路由）持有，或锁模块缺失 → fail-closed，退出码 28；不打开 U-BANK、不切 USB Hub、不写 bank_form。stale 全局锁会被安全接管，日志写明 prev owner。
- `LAICANYUAN_TRANSFER_PRODUCTION` 未设为 `1` 但调用方又试图把 `ZHIDAN_TEST_MODE=0` 透传进 runner —— runner 仍然把子进程的 `ZHIDAN_TEST_MODE` 强制写回 `1`。

## 真实配置文件

- `laicanyuan_payee.local.json`：**真实**收方账号、户名、开户银行、支行、付款单位名称。已被本目录 `.gitignore` 忽略。
- `laicanyuan_payee.example.json`：**占位**模板，禁止填真实账号；可提交到 git。

## 安全边界

- 仅经办入待审核队列，绝不自动复核 / 授权 / 确认 / 最终付款。
- 测试模式只点第一次「经办」并截图；生产模式（`LAICANYUAN_TRANSFER_PRODUCTION=1` → `ZHIDAN_TEST_MODE=0`）会继续点第二次「经办」，把该笔流水写入招行「待审核」队列后立刻关闭 U-BANK 并 all-off USB Hub。
- 强制 USB 口只在本 runner 子进程链路里通过 env 注入，**不**写进任何全局 `.env` / `.bat`，也不修改 `zhidan_usb_hub._USB_HUB_PORT_BY_PAYER`；来参缘调拨固定 18 口，拒绝使用其它公司/银行 UKey 口。
- 余额读取走 UIA，**不**接受 OCR 自动制单；OCR 仅可在人工排障时使用。

## 第一次真实窗口验证（推荐顺序）

第一次在带 U 盾的机器上跑本流程时，**必须**先走测试 bat，不要直接跑生产 bat：

```powershell
C:\Users\30112\Desktop\财务\来参缘调拨\run_laicanyuan_transfer_测试.bat
```

测试 bat 会显式清空 `LAICANYUAN_TRANSFER_PRODUCTION`，runner 一定走测试模式（子进程 `ZHIDAN_TEST_MODE=1`）。跑完后到本目录 `runs\laicanyuan_<yyyyMMdd_HHmmss>\` 下，按顺序看以下产物，确认无误后再考虑是否要进生产：

1. `run.log` —— runner 自己的日志：登录名是不是 002、强制端口是不是 18、`LAICANYUAN_TRANSFER_PRODUCTION` 是不是被识别为「未设置 / 测试模式」、`balance` / `TRANSFER_UNIT_RMB` / `transferable` 三个值是否符合预期，以及 fail-closed 路径是否覆盖你预想的边界。
2. `query_balance.json` —— 从 `查询招行余额` 子进程拷过来的余额快照：`status=OK`、`page_type` 是 `home` 或 `workbench`、`balance_label` 在白名单内、`company` 与本目录 `laicanyuan_payee.local.json::付款单位名称` 严格相等、`balance_decimal` 解析为 Decimal 正常。`home` 路径还要 `account_count=1`；`workbench` 路径要在 `all_texts` 中能找到「存款(元)」且其金额严格等于 `balance_decimal`，否则 runner 已经 fail-closed。
3. `bank_form_used.json` —— 本次写入招行制单的 bank_form 快照：付款单位名称、收方户名、开户银行、支行名称、收方账号末 4 位、`金额`（应为整数字符串、不带小数）、`业务参考号`（`LCY-<时间戳>`）是否都对。
4. `zhidan.stdout.log` —— 招行 `制单.py` 子进程的 stdout：是否选中了登录名 002、USB Hub 是否切到 18 口、表单各字段是否成功填写、是否只点了第一次「经办」（测试模式应只点一次）、是否截图并清理 U-BANK / USB Hub。

只有以上四项**全部**通过人工核对，再决定是否启用生产模式（设置 `LAICANYUAN_TRANSFER_PRODUCTION=1` 后跑生产 bat）。生产模式不会改变其它任何安全边界 —— 仍然只把单据送入招行待审核 / 待复核队列后停止，**不**自动复核 / 授权 / 确认 / 最终付款。

## 静态/非破坏性自检

```powershell
python -m py_compile C:\Users\30112\Desktop\财务\来参缘调拨\run_laicanyuan_transfer.py
python -m py_compile C:\Users\30112\Desktop\财务\公共\locks\bank_automation_lock.py
python C:\Users\30112\Desktop\财务\来参缘调拨\tests\test_amount.py
python C:\Users\30112\Desktop\财务\来参缘调拨\run_laicanyuan_transfer.py --dry-run-amount-only --fake-balance 50000
```

以上四条命令都**不**打开 U-BANK、**不**操作 USB Hub、**不**调用 `query_balance.py` 或 `制单.py`，只跑 Python 编译和金额规则 + 单实例锁 + 跨项目全局锁的纯函数测试。`test_amount.py` 后追加了 7 条 `global_lock`（`财务\公共\locks\bank_automation_lock.py`）的单元用例（无锁创建 / 活跃 pid 拒绝 / stale pid 接管 / 损坏锁按活跃锁处理 / `finally` 只释放 owner+pid 都匹配的锁 / 完整生命周期 / `is_held_by_active_pid` 诊断）。

PowerShell 脚本静态语法校验（不会真的注册或运行计划任务）：

```powershell
$files = @(
    'C:\Users\30112\Desktop\财务\来参缘调拨\run_laicanyuan_transfer_scheduled.ps1',
    'C:\Users\30112\Desktop\财务\来参缘调拨\install_laicanyuan_transfer_schedule.ps1',
    'C:\Users\30112\Desktop\财务\来参缘调拨\uninstall_laicanyuan_transfer_schedule.ps1',
    'C:\Users\30112\Desktop\财务\来参缘调拨\get_laicanyuan_transfer_schedule_status.ps1'
)
foreach ($f in $files) {
    $tokens = $null; $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($f, [ref]$tokens, [ref]$errors) | Out-Null
    if ($errors -and $errors.Count -gt 0) { Write-Host "FAIL $f"; $errors | ForEach-Object { $_ } } else { Write-Host "OK   $f" }
}

# install 脚本默认就是 dry-run（不传 -Apply），只打印任务定义，不会注册任何任务：
powershell -NoProfile -ExecutionPolicy Bypass -File `
    "C:\Users\30112\Desktop\财务\来参缘调拨\install_laicanyuan_transfer_schedule.ps1"
```
