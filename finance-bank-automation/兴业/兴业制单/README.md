# 兴业银行单笔转账脚本（1920x1080 / 100% 缩放）

## 目标环境

- 屏幕分辨率：1920x1080
- Windows 显示缩放：100%
- 桌面存在快捷方式：`兴业银行企业网银.lnk`
- Python 已安装，并能在命令行使用 `python`

## 第一次使用

1. 安装依赖：

```bat
pip install -r requirements.txt
```

2. 复制 `.env.example`，改名为 `.env`，并填写：

```text
LOGIN_PWD=网盾密码
CERT_PWD=登录页密码
```

3. 预览填单可双击运行：

```text
run_单笔转账.bat
```

只填单不提交（安全默认）的快速入口，可双击：

```text
run_快速制单.bat
```

> 注意：`run_快速制单.bat` 已**不再内置** `CIB_ALLOW_SUBMIT=1`，双击只会填单不会提交。
> 真实制单提交必须由操作人确认 `DEFAULT_TRANSFER` 已替换为真实数据后，
> 在命令行临时 `set CIB_ALLOW_SUBMIT=1` 再手动运行（见下文“快速制单提交（必读）”）。

## 坐标调整说明

这个导出版从当前电脑的 125% 缩放坐标换算到目标电脑 100% 缩放，默认参数在 `cib_open_bank/runtime.py` 顶部：

```python
SOURCE_DPI_SCALE = 1.25
TARGET_DPI_SCALE = 1.0
```

脚本对导航类按钮（"转账付款"/"单笔转账"）会优先用 UIA 控件名点击，控件名找不到时才回退到换算后的固定兜底点；但 **"下一步" / "提交" 是 fail-closed 的**：UIA 找不到就直接停止流程，不再使用任何坐标兜底，避免误点其它按钮造成错误转账。

## 快速制单提交（必读）

`open_bank.py` **默认不会** 点击"下一步/提交"。要执行制单提交，只需要设置：

```powershell
$env:CIB_ALLOW_SUBMIT = "1"
python open_bank.py
```

脚本会在填完表单后打印当前 `fingerprint`，它由 `amount | acct_no | acct_name | bank | branch_full | purpose` 拼接后取 sha256 前 12 位生成，仅用于审计留痕，不再作为提交闸门。

- 未设置 `CIB_ALLOW_SUBMIT=1` → 截图上下半页 → 直接 return，不点下一步/提交。
- 设置 `CIB_ALLOW_SUBMIT=1` → 点下一步，进确认页做硬校验，校验通过后点提交。

典型用法（PowerShell，一步制单）：

```powershell
# 写入/确认 DEFAULT_TRANSFER 后，直接跑制单提交
$env:CIB_ALLOW_SUBMIT = "1"
python open_bank.py
```

为避免把占位模板误提交，`DEFAULT_TRANSFER` 如果包含空值、`000000000000000`、`示例`、`请勿用于真实提交` 等占位内容，脚本会在提交前停止。

### 字段校验规则

- **审计指纹（fingerprint）**：`amount | acct_no | acct_name | bank | branch_full | purpose`，仅打印留痕。
- **确认页硬校验（fail-closed 不放行）**：`amount`（去逗号/¥/￥/元/空白后比较）、`acct_no`（只保留数字后比较）、`acct_name`、`bank`、`purpose`，外加负面关键词扫描（错误/失败/未通过/重新/异常）。
- **`branch_full` 不参与确认页硬校验**：业务规则是"收款行最终只填到银行大类，不需要支行名"；`branch_full` 与 `branch_queries` 仅作为来源参考和搜索关键词使用。

> 说明：当 `CIB_ALLOW_SUBMIT=1` 时，脚本在打开网银前会先 `close_bank_windows()` + `all_off_usb_hub_ports()` 做一次干净启动，再 only 通电目标 U 盾口并打开快捷方式。`login_only.py` 与 `batch_fill_only.py` 不受此逻辑影响。

## 当前转账数据（占位模板）

`cib_open_bank/main_flow.py` 里的 `DEFAULT_TRANSFER` 当前是 **占位模板**，不是真实付款信息：

```python
DEFAULT_TRANSFER = {
    "label": "SAMPLE_请勿用于真实提交",
    "amount": "0.01",
    "acct_no": "000000000000000",
    "acct_name": "示例收款方公司",
    "bank": "示例银行",
    "branch_full": "示例银行示例支行",
    "branch_queries": ["示例支行", "示例银行"],
    "purpose": "请勿用于真实提交",
}
```

**真实付款前必须** 在这里写入用户提供的字段：`label / amount / acct_no / acct_name / bank / branch_full / branch_queries / purpose`。改完之后：

1. `python -m compileall -q open_bank.py batch_fill_only.py cib_open_bank` 检查语法；
2. 按上面的"快速制单提交"流程设置 `CIB_ALLOW_SUBMIT=1` 后运行一次。

不要在占位状态下设置 `CIB_ALLOW_SUBMIT=1`。

## 输出

运行截图会保存在本文件夹下：

```text
screenshots
```

`m3_latest30_cib_fill_only_runner.py` 的 M3 测试/验证运行结束后必须通过网关发飞书最终通知：

- 成功、失败、`--extract-only` 都会发送。
- 通知包含本次 M3 信息联页汇总图、每条成功/失败状态、问题字段、问题原因和是否需要人工确认。
- M3 原始申请金额会保留在通知明细里；兴业填单测试金额仍按安全规则覆盖为 `0.01`。
- 通知失败会写入 `summary.json` 的 `final_feishu_notification`，并让本次 runner 返回非 0，方便外层监控发现。

## 工作区整洁策略

长期规则：

- 批量十轮测试请从 `run_批量只填单截图关闭.bat` 启动，或手动运行 `python batch_fill_only.py --fresh`。
- `--fresh` 会先把旧的 `batch_*.png` 移入 `screenshots\archive\batch_yyyyMMdd_HHmmss`，避免旧图导致跳过。
- 批量脚本结束后会自动整理调试截图：`screenshots` 根目录只保留 `batch_01` 到 `batch_10` 的上下半页最终核验图；登录、填字段、收款行调试等中间截图会移入 `screenshots\archive\batch_debug_yyyyMMdd_HHmmss`。
- 单笔/调试历史截图、旧的 `archive_batch_*` 目录、复核拼图、`__pycache__` 可用 `python maintain_workspace.py --apply` 统一整理；双击 `run_整理工作区.bat` 等价。
- 各 bat 入口会设置 `PYTHONDONTWRITEBYTECODE=1`，避免日常运行继续生成 Python 缓存文件。

维护命令默认先 dry-run，不会改文件：

```powershell
python maintain_workspace.py
```

确认列表没问题后再应用：

```powershell
python maintain_workspace.py --apply
```
