import type { AppConfig } from "../config.js";
import type { GatewayDatabase } from "../db/database.js";
import type { IncomingFeishuMessage } from "../domain/types.js";
import { captureDesktopScreenshot } from "../media/screenshot.js";
import { AccessControl } from "../security/access-control.js";
import { detectDangerousIntent } from "../security/dangerous-command.js";
import type { TaskManager, OutboundMessenger } from "../tasks/task-manager.js";
import { logger } from "../logger.js";
import { commandHelp, parseCommand } from "./parser.js";

interface ResolvedMedia {
  imagePaths: string[];
  videoFrameIntervalSeconds?: number;
}

export class CommandController {
  constructor(
    private readonly config: AppConfig,
    private readonly db: GatewayDatabase,
    private readonly accessControl: AccessControl,
    private readonly taskManager: TaskManager,
    private readonly messenger: OutboundMessenger
  ) {}

  async handleMessage(message: IncomingFeishuMessage): Promise<void> {
    if (!this.accessControl.hasAllowedUsers()) {
      await this.messenger.sendText(
        message.chatId,
        [
          "网关已收到消息，但尚未配置 ALLOWED_FEISHU_USER_IDS，因此不会执行任何命令。",
          `你的 Feishu user_id 是：${message.userId}`,
          "请把这个值写入 .env 的 ALLOWED_FEISHU_USER_IDS 后重启网关。"
        ].join("\n")
      );
      return;
    }

    if (!this.accessControl.isAllowedUser(message.userId)) {
      await this.messenger.sendText(
        message.chatId,
        [
          "无权限：当前 Feishu user_id 不在网关白名单中。",
          `当前 user_id：${message.userId}`,
          "请确认 .env 的 ALLOWED_FEISHU_USER_IDS。"
        ].join("\n")
      );
      return;
    }

    const sessionId = `${message.chatId}:${message.userId}`;
    let session = this.db.getOrCreateSession({
      sessionId,
      userId: message.userId,
      chatId: message.chatId,
      runner: this.config.runner.defaultRunner,
      projectPath: this.config.security.defaultProjectPath ?? null
    });

    const command = parseCommand(normalizeMessageText(message));

    switch (command.type) {
      case "start": {
        const result = this.accessControl.validateProjectPath(command.projectPath);
        if (!result.ok) {
          await this.messenger.sendText(message.chatId, `绑定失败：${result.reason}`);
          return;
        }

        this.db.bindProject(session.session_id, result.path);
        await this.messenger.sendText(message.chatId, `已绑定项目目录：${result.path}`);
        return;
      }

      case "run": {
        const media = await this.resolveInputMedia(message);
        if (!media) {
          return;
        }
        await this.startCodexTurn(message.chatId, session, augmentPromptForMedia(command.task, message), media);
        return;
      }

      case "chat": {
        const media = await this.resolveInputMedia(message);
        if (!media) {
          return;
        }
        await this.startCodexTurn(message.chatId, session, augmentPromptForMedia(command.prompt, message), media);
        return;
      }

      case "approve": {
        session = this.db.getSession(session.session_id) ?? session;
        if (session.task_status !== "pending_approval" || !session.pending_task) {
          await this.messenger.sendText(message.chatId, "当前没有等待审批的任务。");
          return;
        }

        const task = session.pending_task;
        this.db.clearPendingApproval(session.session_id);
        await this.messenger.sendText(message.chatId, "已批准，继续发送给 Codex。");
        this.taskManager.startTask({ ...session, pending_task: null, pending_reason: null }, task);
        return;
      }

      case "cancel": {
        session = this.db.getSession(session.session_id) ?? session;
        const result = await this.taskManager.cancel(session);
        await this.messenger.sendText(message.chatId, result);
        return;
      }

      case "new": {
        session = this.db.getSession(session.session_id) ?? session;
        session = this.taskManager.reconcileStaleRunningSession(session);
        if (session.task_status === "running") {
          await this.messenger.sendText(message.chatId, "当前任务仍在运行，请先 /cancel 或等待完成。");
          return;
        }

        this.db.updateSessionStatus({
          sessionId: session.session_id,
          status: "idle",
          currentTaskId: null,
          runnerThreadId: null,
          lastOutput: null,
          lastError: null
        });
        await this.messenger.sendText(message.chatId, "已开启新的 Codex CLI 会话。下一条普通消息会创建新的 thread。");
        return;
      }

      case "screen": {
        const screenshotPath = await this.captureAndSendScreenshot(message.chatId);
        if (!screenshotPath) {
          return;
        }

        if (command.prompt) {
          await this.startCodexTurn(
            message.chatId,
            session,
            [
              "请基于这张当前 Windows 桌面截图回答用户的问题；如果需要操作项目，再按用户要求继续。",
              "",
              "用户问题：",
              command.prompt
            ].join("\n"),
            { imagePaths: [screenshotPath] }
          );
        }
        return;
      }

      case "status": {
        session = this.db.getSession(session.session_id) ?? session;
        await this.messenger.sendText(message.chatId, this.taskManager.formatStatus(session));
        return;
      }

      case "help":
      case "unknown":
      default:
        await this.messenger.sendText(message.chatId, commandHelp());
    }
  }

  private async startCodexTurn(
    chatId: string,
    sessionSnapshot: ReturnType<GatewayDatabase["getSession"]>,
    prompt: string,
    media: ResolvedMedia = { imagePaths: [] }
  ): Promise<void> {
    let session = sessionSnapshot;
    if (!session) {
      await this.messenger.sendText(chatId, "会话不存在，请重新发送消息。");
      return;
    }

    session = this.db.getSession(session.session_id) ?? session;
    if (!session.project_path) {
      await this.messenger.sendText(chatId, "请先使用 /start <project_path> 选择 Codex CLI 的工作目录。");
      return;
    }

    if (session.task_status === "running") {
      await this.messenger.sendText(chatId, "Codex 正在处理上一条消息。可用 /status 查看，或 /cancel 取消。");
      return;
    }

    if (session.task_status === "pending_approval") {
      await this.messenger.sendText(chatId, "已有消息等待审批。请先 /approve 或 /cancel。");
      return;
    }

    const dangerous = detectDangerousIntent(prompt);
    if (this.config.security.dangerousApprovalEnabled && dangerous.dangerous) {
      this.db.setPendingApproval(session.session_id, prompt, dangerous.reason ?? "检测到危险操作");
      await this.messenger.sendText(
        chatId,
        [
          "这条消息可能触发危险操作，已暂停发送给 Codex。",
          `原因：${dangerous.reason ?? "检测到危险操作"}`,
          "确认继续请发送 /approve；放弃请发送 /cancel。"
        ].join("\n")
      );
      return;
    }

    this.taskManager.startTask(
      session,
      prompt,
      media.imagePaths,
      media.videoFrameIntervalSeconds
    );
  }

  private async captureAndSendScreenshot(chatId: string): Promise<string | null> {
    if (!this.messenger.sendImage) {
      await this.messenger.sendText(chatId, "当前网关没有启用飞书发图能力，无法发送截图。");
      return null;
    }

    try {
      const screenshot = await captureDesktopScreenshot(this.config.attachments.dir);
      await this.messenger.sendImage(chatId, screenshot.path);
      return screenshot.path;
    } catch (error) {
      const reason = error instanceof Error ? error.message : "未知错误";
      await this.messenger.sendText(
        chatId,
        [
          "截图失败，暂时没有发给 Codex。",
          `原因：${reason}`,
          "常见原因：网关不是在当前可见桌面会话运行、屏幕被锁定、远程桌面断开、UAC/银行控件切到安全桌面。"
        ].join("\n")
      );
      return null;
    }
  }

  private async resolveInputMedia(message: IncomingFeishuMessage): Promise<ResolvedMedia | null> {
    if (message.imageKeys.length === 0 && message.videoKeys.length === 0) {
      return { imagePaths: [] };
    }

    if (!message.messageId) {
      await this.messenger.sendText(message.chatId, "这条媒体消息缺少 message_id，暂时无法下载附件。");
      return null;
    }

    if (!this.messenger.downloadMessageImage) {
      await this.messenger.sendText(message.chatId, "当前网关没有启用飞书媒体下载能力。");
      return null;
    }

    const media: ResolvedMedia = { imagePaths: [] };

    try {
      media.imagePaths.push(
        ...(await Promise.all(
        message.imageKeys.map((imageKey) => this.messenger.downloadMessageImage!(message.messageId!, imageKey))
        ))
      );
    } catch (error) {
      const reason = error instanceof Error ? error.message : "未知错误";
      await this.messenger.sendText(
        message.chatId,
        [
          "图片下载失败，暂时没有发送给 Codex。",
          `原因：${reason}`,
          "请确认飞书应用已经开通读取消息资源/图片的权限，然后重启网关再试。"
        ].join("\n")
      );
      return null;
    }

    if (message.videoKeys.length === 0) {
      return media;
    }

    if (!this.messenger.canExtractVideoFrames || !(await this.messenger.canExtractVideoFrames())) {
      if (media.imagePaths.length > 0) {
        return media;
      }

      await this.messenger.sendText(
        message.chatId,
        "视频已收到，但本机没有可用的 ffmpeg，暂时无法抽帧给 Codex。可以安装 ffmpeg 后重启网关。"
      );
      return null;
    }

    if (!this.messenger.downloadMessageVideo || !this.messenger.extractVideoFrames) {
      if (media.imagePaths.length > 0) {
        return media;
      }

      await this.messenger.sendText(message.chatId, "当前网关没有启用飞书视频下载/抽帧能力。");
      return null;
    }

    for (const videoKey of message.videoKeys) {
      try {
        const videoPath = await this.messenger.downloadMessageVideo(message.messageId, videoKey);
        const frames = await this.messenger.extractVideoFrames(videoPath);
        media.imagePaths.push(...frames);
        media.videoFrameIntervalSeconds = this.config.video.frameIntervalSeconds;
      } catch (error) {
        logger.warn("Failed to process Feishu video", error);
        if (media.imagePaths.length === 0) {
          const reason = error instanceof Error ? error.message : "未知错误";
          await this.messenger.sendText(message.chatId, `视频处理失败，暂时没有发送给 Codex。\n原因：${reason}`);
          return null;
        }
      }
    }

    return media;
  }
}

function normalizeMessageText(message: IncomingFeishuMessage): string {
  const text = message.text.trim();
  if (text) {
    return text;
  }

  if (message.imageKeys.length === 1) {
    return "请看这张图片并回答。";
  }

  if (message.imageKeys.length > 1) {
    return "请看这些图片并回答。";
  }

  if (message.videoKeys.length === 1) {
    return "请分析这个视频的关键画面并回答。";
  }

  if (message.videoKeys.length > 1) {
    return "请分析这些视频的关键画面并回答。";
  }

  return "";
}

function augmentPromptForMedia(prompt: string, message: IncomingFeishuMessage): string {
  if (message.videoKeys.length === 0) {
    return prompt;
  }

  return [
    prompt,
    "",
    "网关提示：飞书视频会按每秒抽帧后分批传给 Codex。请基于这些逐秒关键画面回答。"
  ].join("\n");
}
