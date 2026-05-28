# 单笔转账维护说明

## 代码结构

- `open_browser.py`：兼容旧启动命令的薄入口，不放业务逻辑。
- `abc_single_transfer/runtime.py`：路径、日志、截图、环境变量、安全校验、运行产物清理。
- `abc_single_transfer/data.py`：转账数据加载、字段规范化、K 宝密码读取。
- `abc_single_transfer/auth.py`：登录页、证书弹窗、K 宝密码窗口处理。
- `abc_single_transfer/navigation.py`：登录后进入“付款业务 > 单笔转账”。
- `abc_single_transfer/form.py`：收款账号、户名、开户行、支行、金额、用途填写与批量覆盖验证。
- `abc_single_transfer/usb.py`：USB Hub 29 口农行 U 盾准备、30 口 OK 自动点击器准备和人工等待。
- `abc_single_transfer/runner.py`：主流程编排。

## 运行产物位置

以后新的日志和截图不再写进 `skills/单笔转账` 目录，而是写到：

```text
C:\Users\30112\Desktop\财务\农业\农业银行\artifacts\单笔转账
```

USB Hub 29/30 口辅助脚本的新日志写到：

```text
C:\Users\30112\Desktop\财务\农业\农业银行\artifacts\usb12
```

其中：

- `debug_runs`：每次运行的 `run.log`、checkpoint 截图、live screenshots。
- `logs`：滚动前的总日志 `abc_transfer.log`。
- `legacy_skill_artifacts`：从旧技能目录搬出来的历史日志和截图归档。

## 自动清理策略

主流程每次启动都会执行一次安全清理，只清理新的 `artifacts/单笔转账/debug_runs` 下的旧运行目录，永远跳过当前运行目录。

默认策略：

- 保留最近 20 个运行目录。
- 删除超过 30 天的运行目录。

可在 `.env` 中调整：

```text
ABC_AUTO_CLEAN_ARTIFACTS=true
ABC_ARTIFACT_RETAIN_RUNS=20
ABC_ARTIFACT_MAX_AGE_DAYS=30

USB12_AUTO_CLEAN_ARTIFACTS=true
USB12_ARTIFACT_RETAIN_RUNS=20
USB12_ARTIFACT_MAX_AGE_DAYS=30
```

如果要临时保留所有排障截图，把 `ABC_AUTO_CLEAN_ARTIFACTS=false` 写进 `.env`。

## 安全边界

- 默认只填表、不提交（测试/验证模式）；正常制单需操作人显式开启 ABC_* 生产门禁（`ABC_ALLOW_SUBMIT_ONCE=true` 等，物理 OK 仅在交易 K宝密码提交后触发），单条/逐条完成提交进银行待审核/待复核链后停止，系统不自动复核/授权/最终付款。
- 开户行必须经过页面实际值核对，不能只看输入框是否有文字。
- 真实回归测试必须同时检查 `run.log` 和对应 checkpoint 截图。
- 第 9 条 `桂林国民村镇银行` 当前仍应视为待核对数据，不算自动通过。
