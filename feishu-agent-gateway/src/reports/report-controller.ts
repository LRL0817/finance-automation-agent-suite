import fs from "node:fs";
import path from "node:path";
import type { AppConfig } from "../config.js";
import type { GatewayDatabase } from "../db/database.js";
import { extractFinanceError, formatFinanceErrorPromptBlock, sanitizeFinanceText } from "../domain/finance-error.js";
import type { SessionRecord } from "../domain/types.js";
import { AccessControl } from "../security/access-control.js";
import type { TaskManager } from "../tasks/task-manager.js";
import { isPathInside } from "../utils/paths.js";
import { truncateMiddle } from "../utils/text.js";

interface FailureReportPayload {
  project_path?: string;
  projectPath?: string;
  title?: string;
  status?: string;
  error?: string;
  context?: string;
  log?: string;
  log_file?: string;
  logFile?: string;
  image_path?: string;
  imagePath?: string;
  image_paths?: unknown;
  imagePaths?: unknown;
  source?: string;
}

export interface ReportResult {
  ok: boolean;
  status: number;
  body: Record<string, unknown>;
}

export class ReportController {
  constructor(
    private readonly config: AppConfig,
    private readonly db: GatewayDatabase,
    private readonly accessControl: AccessControl,
    private readonly taskManager: TaskManager
  ) {}

  handleFailureReport(payload: unknown): ReportResult {
    if (!this.config.report.token) {
      return {
        ok: false,
        status: 503,
        body: { ok: false, error: "REPORT_TOKEN is not configured" }
      };
    }

    if (!isRecord(payload)) {
      return {
        ok: false,
        status: 400,
        body: { ok: false, error: "JSON body must be an object" }
      };
    }

    const report = payload as FailureReportPayload;
    const rawProjectPath = report.project_path ?? report.projectPath;
    if (typeof rawProjectPath !== "string" || !rawProjectPath.trim()) {
      return {
        ok: false,
        status: 400,
        body: { ok: false, error: "project_path is required" }
      };
    }

    const project = this.accessControl.validateProjectPath(rawProjectPath);
    if (!project.ok) {
      return {
        ok: false,
        status: 403,
        body: { ok: false, error: project.reason }
      };
    }

    const session = this.findTargetSession();
    if (!session) {
      return {
        ok: false,
        status: 409,
        body: {
          ok: false,
          error: "No Feishu session found. Send any message to the bot first."
        }
      };
    }

    const imagePaths = this.resolveReportImagePaths(report, project.path);

    let taskId: string;
    try {
      taskId = this.taskManager.startTask(
        {
          ...session,
          project_path: project.path
        },
        buildPrompt(project.path, report),
        imagePaths,
        undefined,
        { finalDelivery: "send", notifyImagePaths: imagePaths }
      );
    } catch (error) {
      return {
        ok: false,
        status: 409,
        body: {
          ok: false,
          error: error instanceof Error ? error.message : "Failed to start Codex task"
        }
      };
    }

    return {
      ok: true,
      status: 202,
      body: {
        ok: true,
        task_id: taskId,
        message: "Failure report accepted and sent to Codex"
      }
    };
  }

  private findTargetSession(): SessionRecord | null {
    return this.db.getLatestSessionForUsers([...this.config.security.allowedUserIds]);
  }

  private resolveReportImagePaths(report: FailureReportPayload, projectPath: string): string[] {
    const rawPaths = collectReportImagePaths(report);
    const validPaths: string[] = [];
    const errors: string[] = [];

    for (const rawPath of rawPaths.slice(0, MAX_REPORT_IMAGES)) {
      const validation = this.validateReportImagePath(rawPath, projectPath);
      if (validation.ok) {
        validPaths.push(validation.path);
      } else {
        errors.push(`${rawPath}: ${validation.reason}`);
      }
    }

    if (rawPaths.length > MAX_REPORT_IMAGES) {
      errors.push(`最多接收 ${MAX_REPORT_IMAGES} 张上报图片，已忽略 ${rawPaths.length - MAX_REPORT_IMAGES} 张。`);
    }
    if (errors.length > 0) {
      console.warn("[report] skipped image paths", errors);
    }

    return validPaths;
  }

  private validateReportImagePath(rawPath: string, projectPath: string): ValidatedReportImage | InvalidReportImage {
    const resolvedPath = path.resolve(path.isAbsolute(rawPath) ? rawPath : path.join(projectPath, rawPath));
    const allowedRoots = [...this.config.security.projectWhitelist, this.config.attachments.dir];
    const allowed = allowedRoots.some((root) => isPathInside(resolvedPath, root));
    if (!allowed) {
      return { ok: false, reason: "不在 PROJECT_WHITELIST 或附件目录内" };
    }

    const extension = path.extname(resolvedPath).toLowerCase();
    if (!REPORT_IMAGE_EXTENSIONS.has(extension)) {
      return { ok: false, reason: "不是允许的图片格式" };
    }

    let stat: fs.Stats;
    try {
      stat = fs.statSync(resolvedPath);
    } catch {
      return { ok: false, reason: "文件不存在或不可访问" };
    }

    if (!stat.isFile()) {
      return { ok: false, reason: "路径不是文件" };
    }
    if (stat.size <= 0) {
      return { ok: false, reason: "文件为空" };
    }
    if (stat.size > MAX_REPORT_IMAGE_BYTES) {
      return { ok: false, reason: `超过 ${Math.round(MAX_REPORT_IMAGE_BYTES / 1024 / 1024)}MB` };
    }

    return { ok: true, path: resolvedPath };
  }
}

const REPORT_IMAGE_EXTENSIONS = new Set([".jpg", ".jpeg", ".png", ".gif", ".webp"]);
const MAX_REPORT_IMAGES = 5;
const MAX_REPORT_IMAGE_BYTES = 20 * 1024 * 1024;

interface ValidatedReportImage {
  ok: true;
  path: string;
}

interface InvalidReportImage {
  ok: false;
  reason: string;
}

function buildPrompt(_projectPath: string, report: FailureReportPayload): string {
  const title = report.title?.trim() || "自动化制单失败";
  const status = report.status?.trim() || "failed";
  const error = report.error?.trim() || "未提供错误信息";
  const context = report.context?.trim() || "未提供上下文";
  const log = report.log?.trim() || "未提供日志";
  const logFile = report.log_file ?? report.logFile ?? "";
  const source = report.source?.trim() ? "本机自动化流程" : "未提供来源";
  const imageCount = collectReportImagePaths(report).length;
  const financeError = extractFinanceError(report.error, report.context, report.log);
  const financeErrorBlock = financeError
    ? ["", formatFinanceErrorPromptBlock(financeError), ""]
    : [];

  return [
    "你收到了一条来自本机自动化制单程序的结果上报。",
    "只做通知：根据上报内容用飞书回复用户。不要修改文件、不要重跑流程、不要启动银行客户端、不要执行修复动作。",
    "回复必须面向财务同事，精炼、业务化，只说关键信息；不要出现本机路径、文件名、脚本名、任务 ID、技术堆栈或调试细节。",
    "成功时只说已完成到哪一步、是否真实提交，并提示已附 1 张 M3 详情图供核对；不要展开日志或流程。",
    "失败时说明失败发生在哪一步、财务能理解的原因、是否已停止制单、下一步该由谁处理；如果附了多张图，说明这些是流程/现场图，可按顺序查看。",
    "失败原因要翻译成人话，例如：未识别付款银行、缺少截图、金额/账号异常、银行页面未找到按钮、超时需人工核对待审核队列。不要把 Python/Node/Playwright/stack trace 原样甩给财务同事。",
    financeError
      ? "重要：下方有“结构化错误原因”区块，这是制单程序直接给出的结论。请优先采用它来组织回复，自由日志只作辅助参考，不要凭日志猜测覆盖结构化结论。"
      : "",
    "不要执行真实付款、转账、提交银行制单等不可逆业务动作；除非用户随后明确授权，否则不要做任何会改变本机状态的操作。",
    "",
    "项目：自动化制单程序",
    `来源：${source}`,
    `标题：${title}`,
    `状态：${status}`,
    imageCount > 0 ? `截图：已附加 ${imageCount} 张抓取/执行现场截图，可结合截图理解界面状态；回复中不要展示路径或文件名。` : "截图：未提供",
    ...financeErrorBlock,
    "错误：",
    truncateMiddle(sanitizeForFinancePrompt(error), 4000),
    "",
    "上下文：",
    truncateMiddle(sanitizeForFinancePrompt(context), 4000),
    "",
    logFile ? "日志文件：已留存，回复中不要展示路径或文件名" : "日志文件：未提供",
    "",
    "日志片段：",
    truncateMiddle(sanitizeForFinancePrompt(log), 8000),
    "",
    "请最后用 3-5 行中文回复飞书用户：状态、付款银行/单据、执行结果、安全边界；失败时再加一句建议下一步。不要附路径或文件名。"
  ].join("\n");
}

function collectReportImagePaths(report: FailureReportPayload): string[] {
  const result: string[] = [];
  for (const value of [
    report.image_path,
    report.imagePath,
    ...coerceImagePathArray(report.image_paths),
    ...coerceImagePathArray(report.imagePaths)
  ]) {
    const text = typeof value === "string" ? value.trim() : "";
    if (text && !result.includes(text)) {
      result.push(text);
    }
  }
  return result;
}

function coerceImagePathArray(value: unknown): unknown[] {
  if (Array.isArray(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim()) {
    return [value];
  }
  return [];
}

function sanitizeForFinancePrompt(value: string): string {
  return sanitizeFinanceText(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
