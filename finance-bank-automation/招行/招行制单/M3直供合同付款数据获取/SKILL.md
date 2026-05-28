---
name: 获取数据
description: 从国瑞 OA (newoa.guorui.net) 获取合同付款数据，并对接招行 U-BANK 单笔转账制单脚本。当用户要求获取国瑞 OA 数据或与招行制单联动时使用。
---

# 获取数据

从国瑞 OA 系统抽取合同付款字段，输出对接招行 U-BANK 单笔转账制单脚本所需的 `bank_form.json`。
全程使用 Playwright 自带的 Chromium，不调用本机已安装的 Edge / Chrome。

## 前置条件

```bash
pip install playwright
playwright install chromium
```

登录态、cookie 持久化在脚本同目录的 `.browser_profile/`，首次登录后再次运行会跳过填密码。

## 文件清单

| 文件 | 说明 |
|---|---|
| `step1_open.py` | 打开 OA 主页，自动登录（凭证从 `.env` 或环境变量 `OA_USER` / `OA_PASS` 读取），暴露 `try_login` / `USER_DATA_DIR` / `OA_URL` 给后续脚本复用 |
| `step2_navigate.py` | 集团空间 → 菜单右翻一页 → 报表中心 → 报表分析 |
| `step3_finance_contract.py` | 直接 goto 报表分析 URL → 财务报表 → 合同付款 |
| `step4_extract.py` | 在合同付款列表点击首条 → 在新弹出『查询穿透显示』页抽取字段 → 写出 `latest.json` 与 `bank_form.json` |
| `latest.json` | OA 原始字段（申请人/部门/日期/金额、合同名称/编号、收款单位、开户银行、银行账户、申请说明、单据编号 等） |
| `bank_form.json` | 已映射到招行制单脚本所需字段，可直接被同级 `..\招行\skills\制单单账号单笔转账skill\制单.py` 读取 |
| `.browser_profile/` | Chromium 持久化用户目录（cookie / 会话） |

## 步骤

### 1. 打开国瑞 OA 主页并登录

- 访问 https://newoa.guorui.net/seeyon/main.do
- 持久化用户目录 `.browser_profile/` 保存登录态，cookie 跨步骤共享
- 凭证从同目录 `.env`（参考 `.env.example`）或环境变量 `OA_USER` / `OA_PASS` 读取，首次自动填表点击『登 录』

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

### 3. 报表分析 → 财务报表 → 合同付款

- 直接 `goto` 已知的报表分析 URL（已带登录态）
- 点击左侧『财务报表』分组
- 点击『合同付款』卡片打开列表

```bash
python step3_finance_contract.py
```

### 4. 点击首行 → 抽取字段 → 写出 JSON

- 在合同付款列表点击首条记录（按合同编号文本 `YLHTSP-\d{4}-\d+` 匹配），列表行在 iframe 里，脚本会遍历所有 frame
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
- 写出两份 JSON：
  - `latest.json`：OA 原始字段
  - `bank_form.json`：映射到招行字段（`付款单位名称 / 收方账号 / 收方户名 / 开户银行 / 支行名称 / 金额 / 用途 / 业务参考号`）；其中
    - 开户银行：从 OA 的完整支行名拆出总行名（按 `BANK_HEADS` 列表前缀匹配，如『中国工商银行某市某支行』→『中国工商银行』）
    - 支行名称：保留 OA 的完整支行名
    - 金额：去掉千分位逗号
    - 用途：固定写 `货款`
    - 业务参考号：OA 的 单据编号

```bash
python step4_extract.py
```

### 5. 调用招行制单脚本完成单笔转账填单

- 招行制单脚本位置：`..\招行\skills\制单单账号单笔转账skill\制单.py`
- 该脚本会自动把 `..\招行` 根目录加入 `sys.path`
- 制单脚本读取当前财务目录下 `M3直供合同付款数据获取\bank_form.json`。找不到时默认 fail-closed，不会打开 U-BANK；只有显式设置 `ZHIDAN_ALLOW_DEMO_MODE=1` 才进入本地 UI 调试占位值路径。
- 直接运行时默认 `ZHIDAN_TEST_MODE=1`：只点第一次「经办」并截图，不点确认页第二次「经办」。
- 真实生产制单必须显式设置 `ZHIDAN_TEST_MODE=0`，并让脚本按 `付款单位名称` 自动匹配 USBHub/UKey；不要设置 `USB_HUB_FORCE_PORT`。

```bash
python "..\招行\skills\制单单账号单笔转账skill\制单.py"
```

测试流程：打开 U-BANK → 登录 → 转账支付 → 单笔转账经办 → 按 `bank_form.json` 自动填表 → 提交前字段校验 → 第一次「经办」→ 截图 → 关闭 U-BANK 和 USBHub。

生产流程：在上述流程后继续点击确认页第二次「经办」→ 截图 → 关闭 U-BANK 和 USBHub。

批量回归测试使用根目录的 `run_batch_from_screenshots.ps1`，它默认强制 USBHub 3 口并保持测试模式；不要把这个脚本用于真实生产制单。

## 已知问题

- **U-BANK 控件读值可能回落成标签/placeholder**：制单脚本在 `06_全部填完` 后会做提交前字段校验，失败会截图 `07_提交前校验失败` 并终止，不点击「经办」。
- **开户地省 / 市/县 / 联行号**：OA 没有这些字段。当前不需要管（用户已确认），招行界面的『查询支行』按钮可在填完支行名后自动匹配联行号。
- **持久化目录互斥**：每个步骤会独占 `.browser_profile/`。运行下一步前请先关掉上一步的浏览器窗口，否则会报 `TargetClosedError`。
- **生成物整洁**：重复测试后用 `..\cleanup_generated_artifacts.ps1` 先 dry-run，再用 `-Apply` 清理/归档生成截图、日志和 `__pycache__`。

## 字段映射速查

| OA 字段 | 招行制单字段 | 备注 |
|---|---|---|
| 银行账户 | 收方账号 | 直接复制 |
| 收款单位名称 | 收方户名 | 直接复制 |
| 开户银行（完整含支行） | 开户银行（总行名） | 用 `BANK_HEADS` 前缀拆 |
| 开户银行（完整含支行） | 支行名称 | 完整保留 |
| 申请金额 | 金额 | 去千分位 |
| 付款单位名称 | 付款单位名称 | 用于匹配 USBHub/UKey |
| 申请说明 | 用途 | 当前脚本固定写 `货款`，不取 OA 原文 |
| 单据编号 | 业务参考号 | 招行『其他信息』里 |
