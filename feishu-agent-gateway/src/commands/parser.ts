export type GatewayCommand =
  | { type: "start"; projectPath: string }
  | { type: "run"; task: string }
  | { type: "chat"; prompt: string }
  | { type: "status" }
  | { type: "approve" }
  | { type: "cancel" }
  | { type: "new" }
  | { type: "screen"; prompt: string }
  | { type: "help" }
  | { type: "unknown"; raw: string };

export function parseCommand(rawText: string): GatewayCommand {
  const text = stripBotMention(rawText).trim();

  if (!text || text === "/help") {
    return { type: "help" };
  }

  if (!text.startsWith("/")) {
    return { type: "chat", prompt: text };
  }

  const [command, ...rest] = text.split(/\s+/);
  const payload = rest.join(" ").trim();

  switch (command.toLowerCase()) {
    case "/start":
      return payload ? { type: "start", projectPath: payload } : { type: "unknown", raw: text };
    case "/run":
      return payload ? { type: "run", task: payload } : { type: "unknown", raw: text };
    case "/status":
      return { type: "status" };
    case "/approve":
      return { type: "approve" };
    case "/cancel":
      return { type: "cancel" };
    case "/new":
      return { type: "new" };
    case "/screen":
    case "/screenshot":
    case "/截图":
      return { type: "screen", prompt: payload };
    default:
      return { type: "unknown", raw: text };
  }
}

export function commandHelp(): string {
  return [
    "可用命令：",
    "/start <project_path> 绑定项目目录",
    "/run <task> 执行任务",
    "普通消息会直接作为 Codex prompt 发送",
    "/status 查看当前任务状态",
    "/approve 批准待审批任务",
    "/cancel 取消当前任务",
    "/new 清空当前飞书会话绑定的 Codex CLI thread",
    "/截图 [问题] 截图并发回飞书；带问题时会把截图交给 Codex 分析"
  ].join("\n");
}

function stripBotMention(text: string): string {
  return text
    .replace(/<at\b[^>]*>.*?<\/at>/gi, "")
    .replace(/^@\S+\s+/, "")
    .trim();
}
