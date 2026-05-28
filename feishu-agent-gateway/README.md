# Feishu Agent Gateway MVP

Feishu Agent Gateway 是一个命令式聊天网关：你可以在飞书机器人聊天里发送 `/start`、`/run`、`/status`、`/approve`、`/cancel`，网关会把任务转给 Codex / Claude Code runner，并把状态、摘要、结果回发到飞书。

当前版本是 MVP，重点是跑通“飞书消息 -> 安全校验 -> runner 执行 -> 飞书回执”的闭环，不包含复杂 UI。

## 功能

- 接收飞书 `im.message.receive_v1` 消息。
- 支持飞书长连接 WebSocket，也支持事件订阅 webhook。
- 支持白名单 Feishu `user_id`。
- 支持项目目录白名单。
- 支持 SQLite 保存 session 和 task 状态。
- 支持 Codex CLI runner：`codex exec`。
- 支持 Claude Code CLI runner：`claude -p` 或 `claude --bare`。
- 支持飞书图片消息：图片会下载到本地缓存，并通过 Codex CLI 的 `--image` 参数交给 Codex。
- 支持 Codex 回传图片：Codex 输出 `FEISHU_IMAGE: <图片路径>` 后，网关会上传图片并发回飞书。
- 支持 Codex 回传文件：Codex 输出 `FEISHU_FILE: <文件路径>` 后，网关会上传文件并发回飞书。
- 支持飞书视频消息：安装 `ffmpeg` 后可按秒抽帧，分批交给 Codex 分析。
- 保留 Codex app-server HTTP 封装作为可选扩展。
- 对高危意图进入 `pending_approval`，需要 `/approve` 才继续。

## 项目结构

```text
.
├── src
│   ├── commands          # 命令解析与控制器
│   ├── db                # SQLite 状态存储
│   ├── domain            # 类型定义
│   ├── feishu            # 飞书 SDK 入口与消息发送
│   ├── runners           # Claude / Codex runner
│   ├── security          # 用户、项目路径、高危命令限制
│   ├── tasks             # 任务生命周期、摘要、取消
│   ├── utils
│   └── index.ts
├── .env.example
├── package.json
└── tsconfig.json
```

## 环境要求

- Node.js `>= 22.5.0`
- npm
- 飞书自建应用机器人
- Codex runner 需要本机已安装并登录 Codex CLI
- Claude runner 需要本机已安装并登录 Claude Code CLI

本项目使用 Node 内置 `node:sqlite`，避免 MVP 阶段安装原生 sqlite 依赖。

## 安装

```bash
npm install
cp .env.example .env
```

Windows PowerShell 可以使用：

```powershell
Copy-Item .env.example .env
```

然后编辑 `.env`。

## 配置

关键配置：

```env
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_EVENT_MODE=ws
ALLOWED_FEISHU_USER_IDS=user_xxx
PROJECT_WHITELIST=C:\path\to\your\project
DEFAULT_PROJECT_PATH=C:\path\to\your\project
DEFAULT_RUNNER=codex
CODEX_BIN=codex
CODEX_SANDBOX=workspace-write
CODEX_APPROVAL_POLICY=on-request
CODEX_EXTRA_ARGS=--disable plugins
STREAM_UPDATE_INTERVAL_MS=3000
FFMPEG_BIN=ffmpeg
VIDEO_FRAME_INTERVAL_SECONDS=1
VIDEO_MAX_FRAMES=300
VIDEO_FRAME_WIDTH=1280
VIDEO_CODEX_BATCH_SIZE=20
ATTACHMENT_DIR=./data/attachments
ATTACHMENT_RETENTION_HOURS=24
ATTACHMENT_CLEANUP_INTERVAL_MINUTES=60
REPORT_TOKEN=change_me_to_a_random_token
REPORT_MAX_BODY_BYTES=262144
SQLITE_PATH=./data/gateway.sqlite
```

`FEISHU_EVENT_MODE` 可选：

- `ws`：飞书长连接，适合本地开发，不需要公网回调地址。
- `webhook`：事件订阅 webhook，需要公网地址映射到 `/webhook/event`。
- `both`：同时启用。

`PROJECT_WHITELIST` 使用分号分隔多个目录。用户 `/start` 绑定的目录必须等于白名单目录或位于白名单目录之下。

`DEFAULT_PROJECT_PATH` 可选。配置后，新飞书会话不需要先 `/start`，会直接从这个目录开始；网关重启会保留已有 Codex thread 记忆，只给尚未绑定目录的会话补上默认目录。

如果你希望飞书里的 Codex CLI 像本机直接运行一样能截图、访问用户目录、操作更多本机文件，可以在 `.env` 中使用高权限模式：

```env
PROJECT_WHITELIST=C:\Users\30112
DEFAULT_PROJECT_PATH=C:\Users\30112\Desktop\finance_workspace
CODEX_SANDBOX=danger-full-access
```

这个模式更接近本机 Codex CLI，但权限更大，只建议配合 `ALLOWED_FEISHU_USER_IDS` 白名单使用。

> 银行 GUI / 截图 / USBHub / 招行客户端等需要桌面会话权限，`CODEX_SANDBOX=workspace-write` 默认会拦截大量 Windows COM、自动化、按键控件，强烈建议为这类链路使用 `danger-full-access`，并且只在严格 `ALLOWED_FEISHU_USER_IDS` 白名单下开启。

### 飞书 -> Codex CLI -> 中文路径项目（招行制单 / 财务）

如果项目真实目录在中文路径下（典型例子：`C:\Users\30112\Desktop\财务`），Codex CLI 的 shell 在某些 Windows 控制台编码下会把中文目录显示成乱码，让链路误判「目录不存在」或「找不到子目录」。同时 Codex CLI 的 shell 工具默认约 10 秒超时，而招行 U-BANK 启动 + 登录 + 进入单笔转账经办需要约 90 秒，单步 GUI 操作很容易在 Codex 这一层被判定为「超时失败」，但本地直接 `python 制单.py` 实际是能跑完的。

推荐做法：

1. 给中文真实目录创建一个**英文路径别名**（NTFS 目录 junction），让 Codex 和网关都走英文路径，真实文件仍落在中文目录里，不需要移动任何项目：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File `
     C:\Users\30112\Desktop\网关\scripts\setup_finance_workspace_junction.ps1
   ```

   脚本会在 `C:\Users\30112\Desktop\finance_workspace` 上创建 junction 指向 `C:\Users\30112\Desktop\财务`。如果别名目录已经存在但不是 junction、或者 junction 指向了别的目标，脚本会**fail-closed**，不会自动删除或覆盖。

   验证：

   ```powershell
   Get-Item C:\Users\30112\Desktop\finance_workspace | Select-Object FullName, LinkType, Target
   # 期望：LinkType=Junction, Target=C:\Users\30112\Desktop\财务
   cmd /c dir C:\Users\30112\Desktop\finance_workspace
   ```

2. 网关 `.env` 推荐变量（只写变量名和示例占位，**不要把真实 .env 内容**贴回 README/Git/Codex 对话）：

   ```env
   DEFAULT_PROJECT_PATH=C:\Users\30112\Desktop\finance_workspace
   PROJECT_WHITELIST=C:\Users\30112\Desktop;C:\Users\30112
   CODEX_SANDBOX=danger-full-access
   ALLOWED_FEISHU_USER_IDS=<your_feishu_user_id>
   ```

   要点：

   - `DEFAULT_PROJECT_PATH` 用英文别名，避免中文路径被 Codex shell 误编码。
   - `PROJECT_WHITELIST` 至少包含 `C:\Users\30112\Desktop` 或 `C:\Users\30112`，让别名目录和中文真实目录都落在白名单范围内。
   - `CODEX_SANDBOX=danger-full-access` 是银行 GUI / 截图 / USBHub 链路的硬性要求；`workspace-write` 会把这些都拦掉。

3. 飞书里的标准操作顺序：先 `/start` 英文别名，再 `/run` 任务：

   ```text
   /start C:\Users\30112\Desktop\finance_workspace
   /run 跑一次招行测试模式 probe
   ```

4. 想从网关侧硬验证「飞书路径 == 本机直接跑」是否还能跑通招行测试模式，使用 probe 脚本（只跑测试模式、永不点击第二次「经办」、不点击提交/确认/复核/授权/付款）：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File `
     C:\Users\30112\Desktop\网关\scripts\cmb_gateway_probe.ps1 `
     -FinanceRoot C:\Users\30112\Desktop\finance_workspace `
     -TimeoutSeconds 300
   ```

   probe 把证据写到 `<FinanceRoot>\runtime\gateway_cmb_probe\probe_<yyyyMMdd_HHmmss>\`，至少包含：

   - `before_process.txt` / `after_process.txt`：运行前后 `Get-Process Firmbank,python,codex,node` 的快照。
   - `before_screenshots.txt` / `after_screenshots.txt`：招行 `screenshots\` 目录里最新的 PNG 列表。
   - `script.stdout.log` / `script.stderr.log`：被测脚本的 stdout / stderr。
   - `process_poll.log`：运行期间每 2 秒采样一次 Firmbank 进程。
   - `exit_code.txt`：被测脚本退出码。
   - `summary.json`：包含 `probe_dir / exit_code / firmbank_seen / new_screenshots / first_jingban_seen / test_mode_skip_second_seen / all_off_seen / dangerous_submit_words_seen / timeout` 等字段。

   probe 脚本本身强制 `ZHIDAN_TEST_MODE=1`、清空 5 个跨行提交开关 (`CMB_LOGIN_ACCOUNT_NAME` / `CMB_LOGIN_ACCOUNT_INDEX` / `CIB_ALLOW_SUBMIT` / `BOC_ENABLE_ORDER_SUBMIT` / `ABC_ALLOW_SUBMIT_ONCE`)、`M3_PRODUCTION_MODE=0`。它**不会**修改任何招行制单业务代码。超时会直接结束子进程、记录 `timeout=true`、不自动重试。

首次只配置 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 也可以启动。此时 `ALLOWED_FEISHU_USER_IDS` 可以先留空，给机器人发消息后，网关会回显你的 `user_id`，再把它写入 `.env` 并重启。

## 飞书开放平台配置

1. 创建企业自建应用并启用机器人。
2. 开通机器人接收消息相关权限，并发布/安装应用到企业。
3. 订阅事件 `im.message.receive_v1`。
4. 如果使用长连接：
   - `.env` 设置 `FEISHU_EVENT_MODE=ws`。
   - 本地直接 `npm run dev`。
5. 如果使用 webhook：
   - `.env` 设置 `FEISHU_EVENT_MODE=webhook` 或 `both`。
   - 飞书事件订阅 URL 配置为：`https://你的域名/webhook/event`。
   - 如启用 Encrypt Key / Verification Token，同步填入 `.env`。

## 启动

开发模式：

```bash
npm run dev
```

生产构建：

```bash
npm run build
npm start
```

健康检查：

```bash
curl http://localhost:3000/healthz
```

## 飞书命令

```text
/start <project_path>
/run <task>
/status
/approve
/cancel
/截图 [问题]
```

示例：

```text
/start C:\Users\me\work\demo
/run 修复登录页表单校验，并运行相关测试
/status
```

普通文本也可以直接发送给机器人，不必每次写 `/run`。同一个飞书会话会复用同一个 Codex CLI thread；发送 `/new` 可以开始新的 Codex CLI 对话。

截图命令支持两种用法：

```text
/截图
/截图 看一下当前桌面为什么报错
```

第一种会直接截取当前 Windows 桌面并发回飞书。第二种会先把截图发回飞书，再把截图作为图片输入交给 Codex 分析。截图依赖网关进程能访问当前可见桌面；如果屏幕锁定、远程桌面断开、UAC/银行控件进入安全桌面，Windows 可能只返回黑屏或拒绝截图。

图片消息支持两种方式：

```text
直接发送图片
发送图片 + 一段文字说明
```

网关会把飞书图片下载到 `data/attachments`，然后调用 `codex exec --image=<image_path>`。如果图片下载失败，通常是飞书应用还缺少读取消息资源/图片的权限。

Codex 如果生成了图片并需要发回飞书，只要在最终回复里单独输出一行：

```text
FEISHU_IMAGE: C:\Users\30112\Desktop\网关\data\attachments\result.png
```

网关会检查图片是否位于 `PROJECT_WHITELIST` 或附件目录内，只允许发送 `jpg/jpeg/png/gif/webp`，单次最多 5 张、单张最大 20MB，然后上传为飞书图片消息。普通聊天不需要写这个指令。

Codex 如果需要把本地文件发回飞书，可以在最终回复里单独输出一行：

```text
FEISHU_FILE: C:\Users\30112\Desktop\codex测试.txt
```

网关会检查文件是否位于 `PROJECT_WHITELIST` 或附件目录内，单次最多 5 个文件、单个最大 30MB；飞书不支持发送 0 字节空文件。出于安全限制，`.env`、私钥和证书文件不会被发送。

视频消息会按 Codex CLI 能理解的方式处理：网关下载视频，用 `ffmpeg` 默认每 1 秒抽 1 帧，最多 300 帧，覆盖 3-5 分钟视频；然后每 20 张图分一批交给 Codex 逐秒记录，最后再让 Codex 汇总回答。Codex CLI 本身目前没有直接传整个视频文件的参数，所以这是“逐秒抽帧视觉分析”。

图片、视频和抽帧缓存默认写入 `data/attachments`。网关启动时会清理一次缓存，之后每 60 分钟清理一次，默认删除 24 小时以前的附件。可以通过 `ATTACHMENT_RETENTION_HOURS` 和 `ATTACHMENT_CLEANUP_INTERVAL_MINUTES` 调整。

如果 `/run` 中包含类似 `rm -rf`、`git reset --hard`、`drop database`、大范围删除等高危意图，任务会进入 `pending_approval`：

```text
/approve
```

或取消：

```text
/cancel
```

## 自动化失败上报

制单程序遇到兜底异常时，可以把问题上报给网关：

```http
POST http://127.0.0.1:3000/agent/report
Authorization: Bearer <REPORT_TOKEN>
Content-Type: application/json
```

请求体：

```json
{
  "project_path": "C:\\Users\\30112\\Desktop\\财务\\农业\\农业银行",
  "title": "农业银行制单失败",
  "error": "异常信息",
  "context": "当前执行到哪一步、输入文件、账号别名等",
  "log": "日志片段",
  "log_file": "C:\\path\\to\\run.log",
  "image_paths": ["C:\\path\\to\\screenshot.png"],
  "source": "agricultural-bank-batch"
}
```

网关会找到最近与你对话的飞书会话，把报告交给 Codex CLI，Codex 的分析或修复结果会回到飞书。`image_paths` 可选，网关会把图片传给 Codex 看，并在最终摘要后把图片发到飞书。

### 结构化错误原因（FINANCE_ERROR_JSON）

为了让飞书兜底通知能给出财务同事看得懂的失败原因，而不是让 Codex 自己读自由日志猜，制单程序可以在 `error` / `context` / `log` / 输出里写一行结构化错误（单行 JSON，字段可缺省）：

```text
FINANCE_ERROR_JSON={"stage":"招行登录","reason":"UKey 未插入","impact":"未提交制单","next_action":"插好UKey后重跑","bank":"招商银行","doc":"M3单据","safe_state":"未操作银行、无待审核"}
```

字段含义：`stage` 卡在哪一步、`reason` 原因、`impact` 影响、`next_action` 下一步、`bank` 涉及银行、`doc` 相关单据、`safe_state` 是否已操作银行/产生待审核。网关解析后会：

- 在交给 Codex 的 prompt 里加入“结构化错误原因”区块，提示 Codex 优先采用它来组织回复；
- 在 Codex runner 本身失败的兜底里，直接据此生成财务同事可读的通知（卡在哪一步、为什么、是否操作银行/产生待审核、下一步）。

解析失败、字段缺省或没有这一行时，会回退到原有的 timeout / 登录 / 付款银行 / 账号 / 金额等正则兜底规则。所有进入飞书/prompt 的内容都会做路径与敏感信息脱敏，不暴露 `.env`、密码、token 或完整本机路径。示例里不要写真实账号、密码或 token。

本机已经内置通用运行器，适合包住任何制单命令。原命令成功时它不做额外动作；原命令失败时，它会把输出日志尾部交给 Codex：

```powershell
python C:\Users\30112\Desktop\网关\scripts\run_and_report.py `
  --project-path "C:\Users\30112\Desktop\财务\农业\农业银行" `
  --title "农业银行制单失败" `
  --source "农业银行批量制单" `
  --cwd "C:\Users\30112\Desktop\财务\农业\农业银行" `
  -- powershell.exe -NoProfile -ExecutionPolicy Bypass -File "run_abc_batch_local.ps1"
```

也可以直接调用内置脚本：

```powershell
python C:\Users\30112\Desktop\网关\scripts\report_to_codex.py `
  --project-path "C:\Users\30112\Desktop\财务\农业\农业银行" `
  --title "农业银行制单失败" `
  --error "这里填异常信息" `
  --context "这里填业务步骤和输入文件" `
  --image-path "C:\path\to\screenshot.png" `
  --log-file "C:\path\to\run.log"
```

如果某个明确的无人值守任务必须每轮都上报结果（例如来参缘调拨 10:00 / 16:00 计划任务），可以加
`--ignore-disabled-flag`。它会绕过本机 `data\reporting_disabled.flag` / `REPORT_TO_CODEX_DISABLED`
对普通自动化上报的全局暂停，仅用于这类"不希望静默无反馈"的结果通知。成功、跳过、失败都可以通过
`--status completed|skipped|failed` 传入；上报脚本本身不会重跑业务流程。

Python 程序兜底示例：

```python
import subprocess
import traceback

try:
    run_order_job()
except Exception:
    subprocess.run([
        "python",
        r"C:\Users\30112\Desktop\网关\scripts\report_to_codex.py",
        "--project-path", r"C:\Users\30112\Desktop\财务\农业\农业银行",
        "--title", "农业银行制单失败",
        "--error", traceback.format_exc(),
        "--source", "农业银行制单",
    ], check=False)
    raise
```

## Runner

### Codex runner

默认 runner 是 Codex CLI：

```env
DEFAULT_RUNNER=codex
CODEX_BIN=codex
CODEX_SANDBOX=workspace-write
CODEX_APPROVAL_POLICY=on-request
CODEX_EXTRA_ARGS=--disable plugins
STREAM_UPDATE_INTERVAL_MS=3000
```

网关会调用：

```bash
codex --ask-for-approval on-request exec --json --cd "<project_path>" --add-dir "<其他白名单目录>" --sandbox workspace-write "<task>"
```

如果飞书消息带图片，会追加：

```bash
-i "<data/attachments/xxx.jpg>"
```

同一飞书会话后续消息会调用：

```bash
codex exec resume --json "<thread_id>" "<task>"
```

高危任务会先由网关进入 `/approve`。

`PROJECT_WHITELIST` 里的目录会作为 Codex 可操作目录。`/start` 选择当前工作目录，其它白名单目录会通过 `--add-dir` 加入同一个 Codex thread。

### Claude runner

如需切到 Claude：

```env
DEFAULT_RUNNER=claude
CLAUDE_BIN=claude
CLAUDE_MODE=print
```

`CLAUDE_MODE=print` 会调用：

```bash
claude -p "<task>"
```

`CLAUDE_MODE=bare` 会调用：

```bash
claude --bare "<task>"
```

### Codex app-server 可选扩展

如果你有兼容的 Codex app-server HTTP endpoint：

```env
DEFAULT_RUNNER=codex
CODEX_APP_SERVER_URL=http://localhost:7331
CODEX_APP_SERVER_TOKEN=
```

配置 `CODEX_APP_SERVER_URL` 后，runner 会优先向 `POST /api/runs` 发送：

```json
{
  "session_id": "...",
  "task_id": "...",
  "user_id": "...",
  "project_path": "...",
  "prompt": "...",
  "image_paths": ["C:\\path\\to\\downloaded-image.jpg"]
}
```

期望返回可包含：

```json
{
  "output": "执行结果",
  "changed_files": ["src/a.ts"],
  "test_result": "测试摘要"
}
```

如果你的 Codex app-server 协议不同，只需要调整 `src/runners/codex-runner.ts`。

## 状态存储

SQLite 默认写入：

```text
./data/gateway.sqlite
```

保存内容包括：

- `session_id`
- `user_id`
- `chat_id`
- `project_path`
- `task_status`
- `last_output`
- task 运行记录、错误、改动文件、测试摘要

## 注意事项

- 飞书里会先看到一条 `Codex 正在输入...`，运行中按 `STREAM_UPDATE_INTERVAL_MS` 定时编辑这条消息展示增量输出，完成后再编辑成最终回复，避免聊天里堆太多过程消息。
- 对 `财务` / `finance_workspace` / `M3直供合同付款数据获取` 这类财务自动化目录，网关会额外加一层“财务同事可读”约束：Codex 的 prompt 会要求最终回复先说业务结论，避免本机路径、脚本名、任务 ID、stdout/stderr、exit code、probe_dir、sandbox/policy 等排障细节；运行中消息也不再滚动展示过程日志，只提示“正在处理”。如果你明确写“技术排障模式 / 显示路径 / 显示退出码 / 原始日志”等关键词，网关会保留原始技术输出，方便自己排障。完整记录仍可在 `data/gateway.sqlite`、`gateway.out.*.log` 或对应运行目录查看。
- 任务超时由 `TASK_TIMEOUT_MS` 控制。
- MVP 的危险命令检测是保守的文本扫描，不等价于完整沙箱。生产环境建议把 runner 放进独立容器或受控工作区。
- Codex CLI、Claude Code CLI 与 Codex app-server 的实际权限仍取决于运行该服务的系统用户。

## spawn 失败排查（Windows）

如果飞书消息里只看到 `spawn EPERM` / `spawn ENOENT` / `spawn EACCES` / `spawn EINVAL` 而没有任何输出，按以下顺序自检：

1. **跑本地 spawn smoke**（read-only，不发飞书、不跑真实任务）：

   ```powershell
   cd C:\Users\30112\Desktop\网关
   npm run build
   node scripts\spawn_smoke.mjs
   ```

   会顺序跑 5 个场景：`cmd /c echo` → `python -c "print(...)"` → `codex --version` → 不存在的二进制 → 不存在的 cwd。每个场景都打印 `prepareSpawnCommand` 解析出的 `resolvedBin` / `windowsCmdWrapped` / `diagnosticHints`、`preflightSpawnEnvironment` 的结果，以及 `formatSpawnError` 在真出错时的完整诊断块。三组核心 smoke 都应该 exit 0；EPERM 的真实"灰区"通常只在 4) 或 5) 上出现。

2. **codex/claude runner 启动前置校验**：`src/runners/spawn-command.ts::preflightSpawnEnvironment` 会在 `child_process.spawn` **之前**校验：
   - `cwd` 存在且是目录；不存在/不是目录 → 直接抛"启动前置校验失败：cwd 不可用 (ENOENT): …"。
   - Windows 上 runner bin 是绝对路径时必须真实存在、扩展名在 `.cmd/.exe/.bat/.com` 白名单内。

   这把绝大多数 `EPERM` / `ENOENT` / `EINVAL` 类失败提前到能给出明确人类可读原因。

3. **EPERM 常见根因（按出现频率排）**：
   - **AppLocker / SRP / 防病毒** 拦截了 runner bin 或它的安装目录。检查事件查看器和杀软日志。
   - **试图把目录当 exe 跑**：`CODEX_BIN` 指向的不是 `.cmd/.exe/.bat`，而是一个目录或没有可执行扩展名的文件。
   - **cwd 在受控目录里**：例如 OneDrive 同步占位文件、Windows 受控文件夹访问。把 project 路径移到不受这些策略管的目录。
   - **runner bin 被另一个进程独占写打开**：例如 npm 正在覆盖 `codex.cmd`。等 install / update 完成再触发任务。

4. **ENOENT 常见根因**：bin 没装 / 不在 PATH、cwd 拼写错。`spawn-command.ts` 会输出 `Windows PATH 解析未在 PATH 中找到 \"<bin>\"…` 作为 prepare-hints。

5. **EINVAL 常见根因**：Node ≥ 20.12 因 CVE-2024-27980 拒绝直接 spawn `.bat / .cmd`。本仓库统一用 `cmd.exe /d /s /c <bin>` 包裹（见 `prepareSpawnCommand`）；不要在新增的 runner 里绕过这一层。

6. **EACCES**：文件存在但当前用户没执行权限；用 `icacls <bin-path>` 查 NTFS ACL 或杀软策略。

`formatSpawnError` 输出的诊断块固定脱敏：只暴露 `command / resolvedBin / display / cwd / cmd-wrapped / node version / platform / pid / prepare-hints / 建议检查 / 原始 message`，**不**包含 `process.env`、`FEISHU_APP_SECRET`、`token`、`cookie`、`.env` 内容。
