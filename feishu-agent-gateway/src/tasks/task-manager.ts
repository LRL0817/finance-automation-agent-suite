import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import type { AppConfig } from "../config.js";
import { GatewayDatabase } from "../db/database.js";
import {
  extractFinanceError,
  formatFinanceErrorMessage,
  type FinanceError
} from "../domain/finance-error.js";
import type { AgentRunner, RunnerName, SessionRecord, TaskRecord } from "../domain/types.js";
import { logger } from "../logger.js";
import { isPathInside } from "../utils/paths.js";
import { tail, truncateMiddle } from "../utils/text.js";

export interface OutboundMessenger {
  sendText(chatId: string, text: string): Promise<string | null>;
  updateText?(messageId: string, text: string): Promise<void>;
  sendImage?(chatId: string, imagePath: string): Promise<string | null>;
  sendFile?(chatId: string, filePath: string): Promise<string | null>;
  downloadMessageImage?(messageId: string, imageKey: string): Promise<string>;
  downloadMessageVideo?(messageId: string, videoKey: string): Promise<string>;
  canExtractVideoFrames?(): Promise<boolean>;
  extractVideoFrames?(videoPath: string): Promise<string[]>;
}

interface RunningTask {
  taskId: string;
  sessionId: string;
  chatId: string;
  controller: AbortController;
  projectPath: string;
  output: string;
  imagePaths: string[];
  notifyImagePaths: string[];
  videoFrameIntervalSeconds?: number;
  timeoutTimer: NodeJS.Timeout;
  progressMessageId: string | null;
  progressMessagePromise: Promise<string | null> | null;
  lastProgressUpdateAt: number;
  progressUpdateTimer: NodeJS.Timeout | null;
  progressUpdateInFlight: Promise<void> | null;
  finalDelivery: "update" | "send";
}

interface StartTaskOptions {
  finalDelivery?: "update" | "send";
  notifyImagePaths?: string[];
}

export class TaskManager {
  private readonly running = new Map<string, RunningTask>();

  constructor(
    private readonly config: AppConfig,
    private readonly db: GatewayDatabase,
    private readonly runners: Record<RunnerName, AgentRunner>,
    private readonly messenger: OutboundMessenger
  ) {}

  startTask(
    session: SessionRecord,
    prompt: string,
    imagePaths: string[] = [],
    videoFrameIntervalSeconds?: number,
    options: StartTaskOptions = {}
  ): string {
    if (!session.project_path) {
      throw new Error("请先使用 /start <project_path> 绑定项目目录。");
    }

    if (this.running.has(session.session_id)) {
      throw new Error("当前会话已有任务正在执行。");
    }

    const taskId = crypto.randomUUID();
    const runner = this.runners[session.runner] ?? this.runners[this.config.runner.defaultRunner];
    const task = this.db.createTask({
      taskId,
      sessionId: session.session_id,
      userId: session.user_id,
      projectPath: session.project_path,
      runner: runner.name,
      prompt,
      runnerThreadId: session.runner_thread_id
    });

    this.db.updateSessionStatus({
      sessionId: session.session_id,
      status: "running",
      currentTaskId: taskId,
      lastOutput: null,
      lastError: null
    });

    const controller = new AbortController();
    const runningTask: RunningTask = {
      taskId,
      sessionId: session.session_id,
      chatId: session.chat_id,
      controller,
      projectPath: task.project_path,
      output: "",
      imagePaths,
      notifyImagePaths: options.notifyImagePaths ?? [],
      videoFrameIntervalSeconds,
      progressMessageId: null,
      progressMessagePromise: null,
      lastProgressUpdateAt: 0,
      progressUpdateTimer: null,
      progressUpdateInFlight: null,
      finalDelivery: options.finalDelivery ?? "update",
      timeoutTimer: setTimeout(() => {
        controller.abort(new Error(`任务超过 ${this.config.runner.taskTimeoutMs}ms 超时。`));
      }, this.config.runner.taskTimeoutMs)
    };

    this.running.set(session.session_id, runningTask);
    void this.executeTask(task, runningTask, runner);
    return taskId;
  }

  async cancel(session: SessionRecord): Promise<string> {
    const runningTask = this.running.get(session.session_id);
    if (!runningTask) {
      const reconciled = this.reconcileStaleRunningSession(session);
      if (reconciled.task_status !== session.task_status) {
        return "当前没有正在执行的任务；已清理卡住的运行状态。现在可以发送 /new 或继续下一条任务。";
      }

      if (session.task_status === "pending_approval") {
        this.db.clearPendingApproval(session.session_id);
        this.db.updateSessionStatus({
          sessionId: session.session_id,
          status: "canceled",
          currentTaskId: null,
          lastError: "待审批任务已取消。"
        });
        return "待审批任务已取消。";
      }

      return "当前没有正在执行的任务。";
    }

    runningTask.controller.abort(new Error("用户通过 /cancel 取消任务。"));
    return "已收到取消请求，正在停止当前任务。";
  }

  reconcileStaleRunningSession(session: SessionRecord): SessionRecord {
    if (session.task_status !== "running" || this.running.has(session.session_id)) {
      return session;
    }

    const currentTask = session.current_task_id ? this.db.getTask(session.current_task_id) : null;
    const staleReason = "网关内存中没有对应运行任务，已清理卡住的 running 状态。";

    if (currentTask?.status === "running") {
      this.db.updateTask({
        taskId: currentTask.task_id,
        status: "failed",
        error: staleReason,
        finished: true
      });
    }

    const latestTask = currentTask ?? this.db.getLatestTask(session.session_id);
    const nextStatus =
      latestTask && latestTask.status !== "running" ? latestTask.status : "failed";
    this.db.updateSessionStatus({
      sessionId: session.session_id,
      status: nextStatus,
      currentTaskId: null,
      lastError: latestTask?.error ?? staleReason
    });

    return this.db.getSession(session.session_id) ?? {
      ...session,
      task_status: nextStatus,
      current_task_id: null,
      last_error: latestTask?.error ?? staleReason
    };
  }

  formatStatus(session: SessionRecord): string {
    session = this.reconcileStaleRunningSession(session);
    const latestTask = this.db.getLatestTask(session.session_id);
    if (isFinanceAudienceProject(session.project_path)) {
      const lines = [
        `当前状态：${financeStatusLabel(session.task_status)}`,
        `工作目录：${session.project_path ?? "未绑定"}`
      ];
      if (latestTask) {
        lines.push(`最近任务：${financeStatusLabel(latestTask.status)}`);
        if (latestTask.last_output) {
          lines.push("", "最近结果：", normalizeFinanceMessage(latestTask.last_output));
        }
        if (latestTask.error) {
          lines.push("", `最近错误：${humanizeFinanceFailure(latestTask.error)}`);
        }
      } else if (session.last_output) {
        lines.push("", "最近结果：", normalizeFinanceMessage(session.last_output));
      }
      return lines.join("\n");
    }

    const lines = [
      `会话：${session.session_id}`,
      `状态：${session.task_status}`,
      `项目：${session.project_path ?? "未绑定"}`,
      `Runner：${session.runner}`
    ];

    if (session.runner_thread_id) {
      lines.push(`Codex thread：${session.runner_thread_id}`);
    }

    if (session.pending_reason) {
      lines.push(`待审批原因：${session.pending_reason}`);
    }

    if (latestTask) {
      lines.push(`最近任务：${latestTask.task_id}`);
      lines.push(`任务状态：${latestTask.status}`);
      if (latestTask.last_output) {
        lines.push(`最后输出：\n${tail(latestTask.last_output, 1200)}`);
      }
      if (latestTask.error) {
        lines.push(`错误：${latestTask.error}`);
      }
    } else if (session.last_output) {
      lines.push(`最后输出：\n${tail(session.last_output, 1200)}`);
    }

    return lines.join("\n");
  }

  private async executeTask(
    task: TaskRecord,
    runningTask: RunningTask,
    runner: AgentRunner
  ): Promise<void> {
    try {
      logger.info(`Starting task ${task.task_id} with runner ${runner.name}`);
      runningTask.progressMessagePromise = this.messenger
        .sendText(runningTask.chatId, "Codex 正在输入...")
        .then((messageId) => {
          runningTask.progressMessageId = messageId;
          return messageId;
        })
        .catch((error) => {
          logger.warn("Failed to send Feishu progress message", error);
          return null;
        });

      const result = await runner.run({
        sessionId: task.session_id,
        taskId: task.task_id,
        userId: task.user_id,
        projectPath: task.project_path,
        prompt: this.buildRunnerPrompt(task.prompt, task.project_path),
        imagePaths: runningTask.imagePaths,
        videoFrameIntervalSeconds: runningTask.videoFrameIntervalSeconds,
        runnerThreadId: task.runner_thread_id,
        signal: runningTask.controller.signal,
        onOutput: (chunk) => {
          if (runningTask.controller.signal.aborted || !this.running.has(task.session_id)) {
            return;
          }
          runningTask.output += chunk;
          this.db.updateTask({ taskId: task.task_id, lastOutput: runningTask.output });
          this.db.updateSessionStatus({
            sessionId: task.session_id,
            status: "running",
            lastOutput: runningTask.output
          });
          this.scheduleProgressUpdate(runningTask);
        }
      });
      const outboundImages = extractOutboundImageDirectives(result.output);
      const outboundFiles = extractOutboundFileDirectives(outboundImages.text);
      const finalOutput = finalOutputForFeishu(result.output, outboundImages, outboundFiles);
      const finalResult = {
        ...result,
        output: finalOutput
      };

      await this.stopProgressUpdates(runningTask);
      this.clearRunning(task.session_id);
      this.db.updateTask({
        taskId: task.task_id,
        status: "completed",
        lastOutput: finalResult.output,
        changedFiles: result.changedFiles,
        testResult: result.testResult,
        runnerThreadId: result.runnerThreadId ?? task.runner_thread_id,
        finished: true
      });
      this.db.updateSessionStatus({
        sessionId: task.session_id,
        status: "completed",
        currentTaskId: null,
        runnerThreadId: result.runnerThreadId ?? task.runner_thread_id,
        lastOutput: finalResult.output,
        lastError: null
      });

      await this.sendOrUpdateFinalMessage(
        runningTask.chatId,
        await this.resolveProgressMessageId(runningTask),
        formatCompletedMessage(task, finalResult),
        runningTask.finalDelivery
      );
      await this.deliverOutboundImages(
        runningTask.chatId,
        task.project_path,
        uniqueStrings([...outboundImages.imagePaths, ...runningTask.notifyImagePaths])
      );
      await this.deliverOutboundFiles(runningTask.chatId, task.project_path, outboundFiles.filePaths);
    } catch (error) {
      const reason =
        error instanceof Error ? error.message : typeof error === "string" ? error : "未知错误";
      const wasCanceled = runningTask.controller.signal.aborted;
      const status = wasCanceled ? "canceled" : "failed";

      await this.stopProgressUpdates(runningTask);
      this.clearRunning(task.session_id);
      this.db.updateTask({
        taskId: task.task_id,
        status,
        lastOutput: runningTask.output,
        error: reason,
        finished: true
      });
      this.db.updateSessionStatus({
        sessionId: task.session_id,
        status,
        currentTaskId: null,
        lastOutput: runningTask.output,
        lastError: reason
      });

      const message = wasCanceled
        ? `任务已取消。\n原因：${reason}`
        : formatFailedMessage(task, reason, runningTask.output);
      await this.sendOrUpdateFinalMessage(
        runningTask.chatId,
        await this.resolveProgressMessageId(runningTask),
        message,
        runningTask.finalDelivery
      );
    }
  }

  private buildRunnerPrompt(prompt: string, projectPath: string): string {
    const promptParts = [prompt];

    if (isFinanceAudienceProject(projectPath) && !isTechnicalDebugRequest(prompt)) {
      promptParts.push("", FINANCE_AUDIENCE_PROMPT);
    }

    if (!this.messenger.sendImage) {
      return promptParts.join("\n");
    }

    promptParts.push(
      "",
      "网关提示：如果你需要把本地图片发回飞书，请先保存为 jpg/jpeg/png/gif/webp 文件，然后在最终回复中单独一行输出：FEISHU_IMAGE: <图片绝对路径>。如果要把本地文件发回飞书，请单独一行输出：FEISHU_FILE: <文件绝对路径>。不需要发附件时不要输出这些指令。"
    );
    return promptParts.join("\n");
  }

  private async resolveProgressMessageId(runningTask: RunningTask): Promise<string | null> {
    if (runningTask.progressMessageId) {
      return runningTask.progressMessageId;
    }

    if (!runningTask.progressMessagePromise) {
      return null;
    }

    return runningTask.progressMessagePromise;
  }

  private async sendOrUpdateFinalMessage(
    chatId: string,
    messageId: string | null,
    text: string,
    finalDelivery: "update" | "send" = "update"
  ): Promise<void> {
    if (finalDelivery === "send") {
      if (messageId && this.messenger.updateText) {
        try {
          await this.messenger.updateText(messageId, "自动化结果已生成，见下一条通知。");
        } catch (error) {
          logger.warn("Failed to update Feishu progress message before final send", error);
        }
      }
      const sentMessageId = await this.messenger.sendText(chatId, text);
      logger.info("Sent Feishu final message", { chatId, messageId: sentMessageId });
      return;
    }

    if (messageId && this.messenger.updateText) {
      try {
        await this.messenger.updateText(messageId, text);
        logger.info("Updated Feishu final message", { chatId, messageId });
        return;
      } catch (error) {
        logger.warn("Failed to update Feishu progress message, falling back to sendText", error);
      }
    }

    const sentMessageId = await this.messenger.sendText(chatId, text);
    logger.info("Sent Feishu final message", { chatId, messageId: sentMessageId });
  }

  private async deliverOutboundImages(
    chatId: string,
    projectPath: string,
    imagePaths: string[]
  ): Promise<void> {
    if (imagePaths.length === 0) {
      return;
    }

    if (!this.messenger.sendImage) {
      await this.messenger.sendText(chatId, "Codex 生成了图片路径，但当前网关没有启用飞书发图能力。");
      return;
    }

    const failures: string[] = [];
    const limitedPaths = imagePaths.slice(0, MAX_OUTBOUND_IMAGES_PER_TASK);
    if (imagePaths.length > limitedPaths.length) {
      failures.push(`本次最多发送 ${MAX_OUTBOUND_IMAGES_PER_TASK} 张图片，已跳过 ${imagePaths.length - limitedPaths.length} 张。`);
    }

    for (const imagePath of limitedPaths) {
      const validation = this.validateOutboundImagePath(imagePath, projectPath);
      if (!validation.ok) {
        failures.push(`${imagePath}：${validation.reason}`);
        continue;
      }

      try {
        await this.messenger.sendImage(chatId, validation.path);
      } catch (error) {
        const reason = error instanceof Error ? error.message : "未知错误";
        failures.push(`${validation.path}：${reason}`);
      }
    }

    if (failures.length > 0) {
      await this.messenger.sendText(
        chatId,
        ["有图片没有发送成功：", ...failures.map((item) => `- ${truncateMiddle(item, 500)}`)].join("\n")
      );
    }
  }

  private async deliverOutboundFiles(
    chatId: string,
    projectPath: string,
    filePaths: string[]
  ): Promise<void> {
    if (filePaths.length === 0) {
      return;
    }

    if (!this.messenger.sendFile) {
      await this.messenger.sendText(chatId, "Codex 生成了文件路径，但当前网关没有启用飞书发文件能力。");
      return;
    }

    const failures: string[] = [];
    const limitedPaths = filePaths.slice(0, MAX_OUTBOUND_FILES_PER_TASK);
    if (filePaths.length > limitedPaths.length) {
      failures.push(`本次最多发送 ${MAX_OUTBOUND_FILES_PER_TASK} 个文件，已跳过 ${filePaths.length - limitedPaths.length} 个。`);
    }

    for (const filePath of limitedPaths) {
      const validation = this.validateOutboundFilePath(filePath, projectPath);
      if (!validation.ok) {
        failures.push(`${filePath}：${validation.reason}`);
        continue;
      }

      try {
        await this.messenger.sendFile(chatId, validation.path);
      } catch (error) {
        const reason = error instanceof Error ? error.message : "未知错误";
        failures.push(`${validation.path}：${reason}`);
      }
    }

    if (failures.length > 0) {
      await this.messenger.sendText(
        chatId,
        ["有文件没有发送成功：", ...failures.map((item) => `- ${truncateMiddle(item, 500)}`)].join("\n")
      );
    }
  }

  private validateOutboundImagePath(
    rawPath: string,
    projectPath: string
  ): { ok: true; path: string } | { ok: false; reason: string } {
    const resolvedPath = path.resolve(path.isAbsolute(rawPath) ? rawPath : path.join(projectPath, rawPath));
    const allowedRoots = [...this.config.security.projectWhitelist, this.config.attachments.dir];
    const allowed = allowedRoots.some((root) => isPathInside(resolvedPath, root));
    if (!allowed) {
      return { ok: false, reason: "图片路径不在 PROJECT_WHITELIST 或附件目录内。" };
    }

    const extension = path.extname(resolvedPath).toLowerCase();
    if (!ALLOWED_OUTBOUND_IMAGE_EXTENSIONS.has(extension)) {
      return { ok: false, reason: "只允许发送 jpg/jpeg/png/gif/webp 图片。" };
    }

    let stat: fs.Stats;
    try {
      stat = fs.statSync(resolvedPath);
    } catch {
      return { ok: false, reason: "文件不存在或不可访问。" };
    }

    if (!stat.isFile()) {
      return { ok: false, reason: "路径不是文件。" };
    }

    if (stat.size <= 0) {
      return { ok: false, reason: "图片文件为空。" };
    }

    if (stat.size > MAX_OUTBOUND_IMAGE_BYTES) {
      return { ok: false, reason: `图片超过 ${Math.round(MAX_OUTBOUND_IMAGE_BYTES / 1024 / 1024)}MB 上限。` };
    }

    return { ok: true, path: resolvedPath };
  }

  private validateOutboundFilePath(
    rawPath: string,
    projectPath: string
  ): { ok: true; path: string } | { ok: false; reason: string } {
    const resolvedPath = path.resolve(path.isAbsolute(rawPath) ? rawPath : path.join(projectPath, rawPath));
    const allowedRoots = [...this.config.security.projectWhitelist, this.config.attachments.dir];
    const allowed = allowedRoots.some((root) => isPathInside(resolvedPath, root));
    if (!allowed) {
      return { ok: false, reason: "文件路径不在 PROJECT_WHITELIST 或附件目录内。" };
    }

    const basename = path.basename(resolvedPath).toLowerCase();
    if (SENSITIVE_FILE_NAMES.has(basename) || SENSITIVE_FILE_EXTENSIONS.has(path.extname(resolvedPath).toLowerCase())) {
      return { ok: false, reason: "安全限制：不发送密钥、证书或环境变量文件。" };
    }

    let stat: fs.Stats;
    try {
      stat = fs.statSync(resolvedPath);
    } catch {
      return { ok: false, reason: "文件不存在或不可访问。" };
    }

    if (!stat.isFile()) {
      return { ok: false, reason: "路径不是文件。" };
    }

    if (stat.size <= 0) {
      return { ok: false, reason: "飞书不支持发送 0 字节空文件。" };
    }

    if (stat.size > MAX_OUTBOUND_FILE_BYTES) {
      return { ok: false, reason: `文件超过 ${Math.round(MAX_OUTBOUND_FILE_BYTES / 1024 / 1024)}MB 上限。` };
    }

    return { ok: true, path: resolvedPath };
  }

  private scheduleProgressUpdate(runningTask: RunningTask): void {
    if (!this.messenger.updateText || runningTask.output.trim().length === 0) {
      return;
    }

    const interval = this.config.runner.streamUpdateIntervalMs;
    const now = Date.now();
    const waitMs = Math.max(0, runningTask.lastProgressUpdateAt + interval - now);

    if (waitMs === 0) {
      void this.flushProgressUpdate(runningTask);
      return;
    }

    if (!runningTask.progressUpdateTimer) {
      runningTask.progressUpdateTimer = setTimeout(() => {
        runningTask.progressUpdateTimer = null;
        void this.flushProgressUpdate(runningTask);
      }, waitMs);
    }
  }

  private async flushProgressUpdate(runningTask: RunningTask): Promise<void> {
    if (!this.running.has(runningTask.sessionId) || !this.messenger.updateText) {
      return;
    }

    if (runningTask.output.trim().length === 0) {
      return;
    }

    if (runningTask.progressUpdateInFlight) {
      if (!runningTask.progressUpdateTimer) {
        runningTask.progressUpdateTimer = setTimeout(() => {
          runningTask.progressUpdateTimer = null;
          void this.flushProgressUpdate(runningTask);
        }, this.config.runner.streamUpdateIntervalMs);
      }
      return;
    }

    runningTask.lastProgressUpdateAt = Date.now();
    runningTask.progressUpdateInFlight = (async () => {
      const messageId = await this.resolveProgressMessageId(runningTask);
      if (!messageId || !this.running.has(runningTask.sessionId) || !this.messenger.updateText) {
        return;
      }

      await this.messenger.updateText(messageId, formatStreamingMessage(runningTask.output, runningTask.projectPath));
    })()
      .catch((error) => {
        logger.warn("Failed to update Feishu streaming progress message", error);
      })
      .finally(() => {
        runningTask.progressUpdateInFlight = null;
      });

    await runningTask.progressUpdateInFlight;
  }

  private async stopProgressUpdates(runningTask: RunningTask): Promise<void> {
    if (runningTask.progressUpdateTimer) {
      clearTimeout(runningTask.progressUpdateTimer);
      runningTask.progressUpdateTimer = null;
    }

    if (runningTask.progressUpdateInFlight) {
      await runningTask.progressUpdateInFlight;
    }
  }

  private clearRunning(sessionId: string): void {
    const runningTask = this.running.get(sessionId);
    if (!runningTask) {
      return;
    }

    if (runningTask.progressUpdateTimer) {
      clearTimeout(runningTask.progressUpdateTimer);
      runningTask.progressUpdateTimer = null;
    }

    clearTimeout(runningTask.timeoutTimer);
    this.running.delete(sessionId);
  }
}

const MAX_OUTBOUND_IMAGES_PER_TASK = 5;
const MAX_OUTBOUND_IMAGE_BYTES = 20 * 1024 * 1024;
const ALLOWED_OUTBOUND_IMAGE_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".gif", ".webp"]);
const OUTBOUND_IMAGE_DIRECTIVE = /^\s*(?:FEISHU_IMAGE|飞书图片)\s*[:=：]\s*(.+?)\s*$/i;
const MAX_OUTBOUND_FILES_PER_TASK = 5;
const MAX_OUTBOUND_FILE_BYTES = 30 * 1024 * 1024;
const SENSITIVE_FILE_NAMES = new Set([".env", ".npmrc", ".pypirc", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"]);
const SENSITIVE_FILE_EXTENSIONS = new Set([".key", ".pem", ".pfx", ".p12"]);
const OUTBOUND_FILE_DIRECTIVE = /^\s*(?:FEISHU_FILE|飞书文件)\s*[:=：]\s*(.+?)\s*$/i;

interface OutboundImageDirectives {
  text: string;
  imagePaths: string[];
}

interface OutboundFileDirectives {
  text: string;
  filePaths: string[];
}

function extractOutboundImageDirectives(output: string): OutboundImageDirectives {
  const imagePaths: string[] = [];
  const keptLines: string[] = [];

  for (const line of output.split(/\r?\n/)) {
    const match = line.match(OUTBOUND_IMAGE_DIRECTIVE);
    if (!match) {
      keptLines.push(line);
      continue;
    }

    const imagePath = cleanDirectivePath(match[1]);
    if (imagePath) {
      imagePaths.push(imagePath);
    }
  }

  return {
    text: keptLines.join("\n").trim(),
    imagePaths
  };
}

function extractOutboundFileDirectives(output: string): OutboundFileDirectives {
  const filePaths: string[] = [];
  const keptLines: string[] = [];

  for (const line of output.split(/\r?\n/)) {
    const match = line.match(OUTBOUND_FILE_DIRECTIVE);
    if (!match) {
      keptLines.push(line);
      continue;
    }

    const filePath = cleanDirectivePath(match[1]);
    if (filePath) {
      filePaths.push(filePath);
    }
  }

  return {
    text: keptLines.join("\n").trim(),
    filePaths
  };
}

function finalOutputForFeishu(
  originalOutput: string,
  outboundImages: OutboundImageDirectives,
  outboundFiles: OutboundFileDirectives
): string {
  if (outboundFiles.text) {
    return outboundFiles.text;
  }

  if (outboundImages.imagePaths.length > 0 || outboundFiles.filePaths.length > 0) {
    return "附件已生成，正在发送到飞书。";
  }

  return originalOutput;
}

function cleanDirectivePath(rawPath: string): string {
  let cleaned = rawPath.trim();
  cleaned = cleaned.replace(/^["'`<]+/, "").replace(/[>"'`]+$/, "").trim();
  return cleaned;
}

function uniqueStrings(values: string[]): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    const key = value.trim();
    if (!key || seen.has(key)) {
      continue;
    }
    seen.add(key);
    result.push(key);
  }
  return result;
}

function formatCompletedMessage(
  task: TaskRecord,
  result: { output: string; changedFiles: string[]; testResult: string }
): string {
  if (task.runner === "codex") {
    if (isTechnicalDebugRequest(task.prompt)) {
      return truncateMiddle(result.output || "Codex 未返回输出。", 7000);
    }
    return truncateMiddle(formatFinanceCompletedOutput(task.project_path, result.output || "Codex 未返回输出。"), 7000);
  }

  const changedFiles =
    result.changedFiles.length > 0 ? result.changedFiles.map((file) => `- ${file}`).join("\n") : "未检测到改动文件。";

  return [
    "执行完成。",
    `任务：${task.task_id}`,
    "",
    "结果：",
    truncateMiddle(result.output || "Runner 未返回输出。", 2200),
    "",
    "改动文件：",
    changedFiles,
    "",
    "测试结果：",
    truncateMiddle(result.testResult, 1000)
  ].join("\n");
}

function formatStreamingMessage(output: string, projectPath: string): string {
  if (isFinanceAudienceProject(projectPath)) {
    return [
      "正在处理，请稍等。",
      "",
      "网关已收到任务，正在让 Codex 执行。完成后会只发业务结果；过程日志、脚本路径和退出码不在群里滚动展示，避免打扰财务同事。",
      "",
      "如需停止，请发送 /cancel。"
    ].join("\n");
  }

  return [
    "Codex 正在输出，完成后会更新为最终结果。",
    "",
    tail(output.trim(), 6500),
    "",
    "（仍在运行...）"
  ].join("\n");
}

function formatFailedMessage(task: TaskRecord, reason: string, output: string): string {
  if (isFinanceAudienceProject(task.project_path)) {
    if (isTechnicalDebugRequest(task.prompt)) {
      return [
        "执行失败。",
        `任务：${task.task_id}`,
        "",
        "错误原因：",
        truncateMiddle(reason, 1200),
        "",
        "最后输出：",
        output ? tail(output, 1200) : "无输出。"
      ].join("\n");
    }
    return formatFinanceFailedMessage(reason, output);
  }

  return [
    "执行失败。",
    `任务：${task.task_id}`,
    "",
    "错误原因：",
    truncateMiddle(reason, 1200),
    "",
    "最后输出：",
    output ? tail(output, 1200) : "无输出。",
    "",
    "建议：",
    "检查 runner 是否已安装、项目目录是否可访问、任务描述是否需要先 /approve。"
  ].join("\n");
}

const FINANCE_AUDIENCE_PROMPT = [
  "网关回复口径（重要）：这条回复会直接发到飞书，可能由财务同事阅读。",
  "请用业务可读的中文回答，第一行先给结论。",
  "成功时只说明：做了什么、公司/银行、余额或金额、是否只读、是否已进入待审核/待复核、下一步需要谁处理。",
  "失败时只说明：哪里卡住、是否已经操作银行/是否产生待审核记录、财务同事下一步怎么做；把技术细节留给技术同事。",
  "除非用户明确要求排障，不要输出本机路径、脚本名、命令行、任务 ID、Codex thread、runner、stdout/stderr、exit code、probe_dir、summary.json、auto_id、sandbox/policy 等技术词。",
  "不要暴露密码、证书、token、.env 内容；账号尽量只写尾号或用户已经给出的必要字段。"
].join("\n");

function isFinanceAudienceProject(projectPath: string | null | undefined): boolean {
  const normalized = (projectPath ?? "").toLowerCase().replace(/\//g, "\\");
  return (
    normalized.includes("\\财务") ||
    normalized.includes("\\finance_workspace") ||
    normalized.includes("\\m3直供合同付款数据获取")
  );
}

function isTechnicalDebugRequest(prompt: string | null | undefined): boolean {
  return /技术排障|排障模式|显示路径|保留路径|显示退出码|显示脚本名|原始日志|stdout|stderr|exit[_ ]?code|probe_dir/i.test(
    prompt ?? ""
  );
}

function formatFinanceCompletedOutput(projectPath: string, output: string): string {
  if (!isFinanceAudienceProject(projectPath)) {
    return output;
  }
  return normalizeFinanceMessage(output);
}

function formatFinanceFailedMessage(reason: string, output: string): string {
  // 优先使用制单程序直接给出的结构化错误（FINANCE_ERROR_JSON），
  // 它能明确卡在哪一步、是否操作了银行/产生待审核、下一步怎么做。
  const structured = extractFinanceError(reason, output);
  if (structured) {
    return formatFinanceErrorMessage(structured);
  }

  const readableReason = humanizeFinanceFailure(`${reason}\n${output}`);
  return [
    "任务没有完成。",
    "",
    `原因：${readableReason}`,
    "",
    "影响：如果没有看到明确的“已进入待审核/待复核队列”，就按“没有完成制单”处理；如提示可能已提交，请先人工核对银行待审核/待复核队列。",
    "",
    "下一步：请让技术同事查看网关日志；财务同事不用根据脚本路径或退出码判断。"
  ].join("\n");
}

function normalizeFinanceMessage(output: string): string {
  const text = output.trim();
  if (!text) {
    return "任务已结束，但没有返回业务结果。请让技术同事查看网关日志。";
  }

  const balanceSummary = summarizeBalanceResult(text);
  if (balanceSummary) {
    return balanceSummary;
  }

  const blocked = humanizePolicyBlocked(text);
  if (blocked) {
    return blocked;
  }

  const cleanedLines = text
    .split(/\r?\n/)
    .map((line) => line.trimEnd())
    .filter((line) => !isTechnicalNoiseLine(line))
    .map(maskLocalPaths)
    .filter((line, index, arr) => !(line.trim() === "" && arr[index - 1]?.trim() === ""));

  const cleaned = cleanedLines.join("\n").trim();
  if (!cleaned) {
    return "任务已结束。详细技术日志已隐藏；如需排障，请让技术同事查看网关记录。";
  }

  return truncateMiddle(cleaned, 2500);
}

function summarizeBalanceResult(text: string): string | null {
  const company = matchFirst(text, /公司[:：]\s*`?([^`\r\n]+?)`?\s*(?:\r?\n|$)/);
  const balance = matchFirst(text, /余额[:：]\s*`?([0-9,]+(?:\.[0-9]{1,2})?)`?\s*元?/);
  const status = matchFirst(text, /状态[:：]\s*`?([A-Za-z\u4e00-\u9fa5]+)`?/);
  if (!company && !balance) {
    return null;
  }

  return [
    "余额查询完成。",
    "",
    company ? `公司：${company.trim()}` : null,
    balance ? `余额：${balance.trim()} 元` : null,
    status ? `状态：${status.trim()}` : null,
    "",
    "说明：这是余额只读查询，没有制单、没有提交、没有付款。"
  ]
    .filter((line): line is string => typeof line === "string")
    .join("\n");
}

function humanizePolicyBlocked(text: string): string | null {
  if (!/(blocked by policy|policy block|沙箱策略|执行策略拦截|权限.*拦截|sandbox)/i.test(text)) {
    return null;
  }
  return [
    "任务没有实际操作银行。",
    "",
    "原因：网关权限不足，Codex CLI 没能启动查询或制单程序。",
    "",
    "影响：没有新查余额，也没有产生制单记录。",
    "",
    "下一步：请技术同事处理网关权限后重试。"
  ].join("\n");
}

function humanizeFinanceFailure(text: string): string {
  // 制单程序若直接给出结构化错误（FINANCE_ERROR_JSON），优先用 stage/reason/safe_state 概括。
  const structured = extractFinanceError(text);
  if (structured) {
    return summarizeFinanceError(structured);
  }

  const blocked = humanizePolicyBlocked(text);
  if (blocked) {
    return "网关权限不足，Codex CLI 没能启动查询或制单程序。";
  }
  if (/timeout|超时|124/i.test(text)) {
    return "银行或网页长时间没有响应，流程超时停止。需要人工确认银行待审核/待复核队列里是否已经有这笔记录。";
  }
  if (/登录|login/i.test(text)) {
    return "登录银行客户端阶段失败，通常是窗口、按钮或 UKey 状态异常。";
  }
  if (/未识别付款银行|付款银行/i.test(text)) {
    return "M3 单据没有识别出付款银行，系统已停止，未进入制单。";
  }
  if (/账号|账户|开户行|支行/i.test(text)) {
    return "单据里的账号或开户行信息不完整/不一致，系统已停止，未继续制单。";
  }
  if (/金额|余额不足/i.test(text)) {
    return "金额或余额校验没有通过，系统已停止。";
  }
  return truncateMiddle(
    maskLocalPaths(
      text
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter((line) => line && !isTechnicalNoiseLine(line))
        .slice(0, 3)
        .join("；")
    ) || "执行过程中出现异常，系统已停止。",
    500
  );
}

function summarizeFinanceError(error: FinanceError): string {
  const parts: string[] = [];
  if (error.stage) {
    parts.push(`卡在「${error.stage}」`);
  }
  if (error.reason) {
    parts.push(error.reason);
  }
  if (error.safe_state) {
    parts.push(`安全状态：${error.safe_state}`);
  } else {
    parts.push("安全状态：未确认是否已操作银行/产生待审核，请按可能未完成处理。");
  }
  if (error.next_action) {
    parts.push(`下一步：${error.next_action}`);
  }
  const summary = parts.join("；");
  return truncateMiddle(summary || "执行过程中出现异常，系统已停止。", 500);
}

function isTechnicalNoiseLine(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed) {
    return false;
  }
  return (
    /^[A-Za-z_]+=\S+/.test(trimmed) ||
    /^\[Codex error\]/i.test(trimmed) ||
    /\b(?:Reconnecting|stream disconnected|tls handshake|ECONNRESET|ETIMEDOUT)\b/i.test(trimmed) ||
    /(?:[A-Z]:\\|\/Users\/|\\runtime\\|\\runs\\|\.json|\.log|\.csv|\.png|\.py|\.ps1|\.bat)/i.test(trimmed) ||
    /\b(?:probe_dir|exit_code|timeout_ms|stdout|stderr|summary\.json|screen\.png|task_id|runner|thread|auto_id|sandbox|policy|spawn|preflight)\b/i.test(trimmed) ||
    /^[-*]\s*(?:汇总目录|截图文件|来源文件|最新目录|最新图片文件名|运行产物|证据目录)[:：]/.test(trimmed) ||
    /^任务[:：][0-9a-f-]{12,}/i.test(trimmed)
  );
}

function maskLocalPaths(line: string): string {
  return line.replace(/[A-Za-z]:\\[^\s，。；,;)）]+/g, "[本机运行记录]");
}

function matchFirst(text: string, pattern: RegExp): string | null {
  const match = text.match(pattern);
  return match?.[1] ?? null;
}

function financeStatusLabel(status: string): string {
  switch (status) {
    case "idle":
      return "空闲";
    case "running":
      return "正在处理";
    case "completed":
      return "已完成";
    case "failed":
      return "失败";
    case "canceled":
      return "已取消";
    case "pending_approval":
      return "等待确认";
    default:
      return status;
  }
}
