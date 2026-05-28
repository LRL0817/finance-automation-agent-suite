# Claude Code Bridge

把提示词发送给本机 Claude Code CLI，并把结果保存到：

`C:\Users\30112\Desktop\财务\.claude\codex_bridge_runs`

## 用法

```powershell
C:\Users\30112\Desktop\财务\公共\tools\claude-code-bridge\ask_claude_code.ps1 `
  -Cwd "C:\Users\30112\Desktop\财务" `
  "请阅读项目文档，给出修改方案，不要改文件。"
```

从提示词文件运行：

```powershell
C:\Users\30112\Desktop\财务\公共\tools\claude-code-bridge\ask_claude_code.ps1 `
  -Cwd "C:\Users\30112\Desktop\财务" `
  -PromptFile "C:\Users\30112\Desktop\财务\公共\tools\claude-code-bridge\prompts\cmb_001_002_payer_account_switch.md"
```

允许 Claude 修改文件时，可以切到更主动的权限模式：

```powershell
C:\Users\30112\Desktop\财务\公共\tools\claude-code-bridge\ask_claude_code.ps1 `
  -Cwd "C:\Users\30112\Desktop\财务" `
  -PromptFile "C:\Users\30112\Desktop\财务\公共\tools\claude-code-bridge\prompts\cmb_001_002_payer_account_switch.md" `
  -PermissionMode acceptEdits
```

## 说明

- 默认用 `claude -p` 非交互模式，不控制已经打开的 Claude Code 黑窗口。
- 默认 `PermissionMode=default`，不会自动绕过权限。
- 每次运行会保存 `prompt.md`、`claude_output.txt`、`meta.json`。
- 如果要继续最近一次 Claude Code 会话，加 `-Continue`。
