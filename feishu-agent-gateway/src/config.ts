import dotenv from "dotenv";
import path from "node:path";
import { ensureDirectory, isPathInside, normalizePath } from "./utils/paths.js";
import type { RunnerName } from "./domain/types.js";

dotenv.config();

export type FeishuEventMode = "ws" | "webhook" | "both";
export type FeishuDomainName = "feishu" | "lark";

export interface AppConfig {
  env: string;
  port: number;
  feishu: {
    appId: string;
    appSecret: string;
    domain: FeishuDomainName;
    eventMode: FeishuEventMode;
    encryptKey?: string;
    verificationToken?: string;
  };
  security: {
    allowedUserIds: Set<string>;
    projectWhitelist: string[];
    defaultProjectPath?: string;
    dangerousApprovalEnabled: boolean;
  };
  runner: {
    defaultRunner: RunnerName;
    taskTimeoutMs: number;
    summaryIntervalMs: number;
    streamUpdateIntervalMs: number;
    claudeBin: string;
    claudeMode: "print" | "bare";
    claudeExtraArgs: string[];
    codexBin: string;
    codexSandbox: "read-only" | "workspace-write" | "danger-full-access";
    codexApprovalPolicy: "untrusted" | "on-failure" | "on-request" | "never";
    codexExtraArgs: string[];
    codexAppServerUrl?: string;
    codexAppServerToken?: string;
  };
  video: {
    ffmpegBin: string;
    frameIntervalSeconds: number;
    maxFrames: number;
    frameWidth: number;
    codexBatchSize: number;
  };
  attachments: {
    dir: string;
    retentionMs: number;
    cleanupIntervalMs: number;
  };
  report: {
    token?: string;
    maxBodyBytes: number;
  };
  sqlitePath: string;
}

function optional(value: string | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

function required(name: string): string {
  const value = optional(process.env[name]);
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

function parseNumber(name: string, fallback: number): number {
  const raw = optional(process.env[name]);
  if (!raw) {
    return fallback;
  }

  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`Invalid positive number for ${name}: ${raw}`);
  }

  return parsed;
}

function parseList(raw: string | undefined, separator: RegExp): string[] {
  return (raw ?? "")
    .split(separator)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseEventMode(raw: string | undefined): FeishuEventMode {
  const value = (raw ?? "ws").trim().toLowerCase();
  if (value === "ws" || value === "webhook" || value === "both") {
    return value;
  }
  throw new Error(`Invalid FEISHU_EVENT_MODE: ${raw}`);
}

function parseDomain(raw: string | undefined): FeishuDomainName {
  const value = (raw ?? "feishu").trim().toLowerCase();
  if (value === "feishu" || value === "lark") {
    return value;
  }
  throw new Error(`Invalid FEISHU_APP_DOMAIN: ${raw}`);
}

function parseRunner(raw: string | undefined): RunnerName {
  const value = (raw ?? "claude").trim().toLowerCase();
  if (value === "claude" || value === "codex") {
    return value;
  }
  throw new Error(`Invalid DEFAULT_RUNNER: ${raw}`);
}

function parseClaudeMode(raw: string | undefined): "print" | "bare" {
  const value = (raw ?? "print").trim().toLowerCase();
  if (value === "print" || value === "bare") {
    return value;
  }
  throw new Error(`Invalid CLAUDE_MODE: ${raw}`);
}

function parseCodexSandbox(raw: string | undefined): "read-only" | "workspace-write" | "danger-full-access" {
  const value = (raw ?? "workspace-write").trim().toLowerCase();
  if (value === "read-only" || value === "workspace-write" || value === "danger-full-access") {
    return value;
  }
  throw new Error(`Invalid CODEX_SANDBOX: ${raw}`);
}

function parseCodexApprovalPolicy(
  raw: string | undefined
): "untrusted" | "on-failure" | "on-request" | "never" {
  const value = (raw ?? "never").trim().toLowerCase();
  if (value === "untrusted" || value === "on-failure" || value === "on-request" || value === "never") {
    return value;
  }
  throw new Error(`Invalid CODEX_APPROVAL_POLICY: ${raw}`);
}

function parseBoolean(raw: string | undefined, fallback: boolean): boolean {
  if (!raw) {
    return fallback;
  }

  return ["1", "true", "yes", "on"].includes(raw.trim().toLowerCase());
}

function parseArgList(raw: string | undefined): string[] {
  const value = optional(raw);
  if (!value) {
    return [];
  }

  // MVP-friendly parsing: quote-aware shell parsing is intentionally avoided.
  return value.split(/\s+/).filter(Boolean);
}

export function loadConfig(): AppConfig {
  const allowedUserIds = new Set(parseList(process.env.ALLOWED_FEISHU_USER_IDS, /,/));

  const projectWhitelist = parseList(process.env.PROJECT_WHITELIST, /[;|]/).map(normalizePath);
  const defaultProjectPath = optional(process.env.DEFAULT_PROJECT_PATH)
    ? normalizePath(process.env.DEFAULT_PROJECT_PATH as string)
    : undefined;

  if (defaultProjectPath) {
    if (!ensureDirectory(defaultProjectPath)) {
      throw new Error(`DEFAULT_PROJECT_PATH is not a directory: ${defaultProjectPath}`);
    }

    const allowed = projectWhitelist.some((root) => isPathInside(defaultProjectPath, root));
    if (!allowed) {
      throw new Error("DEFAULT_PROJECT_PATH must be inside PROJECT_WHITELIST");
    }
  }

  return {
    env: process.env.NODE_ENV ?? "development",
    port: parseNumber("PORT", 3000),
    feishu: {
      appId: required("FEISHU_APP_ID"),
      appSecret: required("FEISHU_APP_SECRET"),
      domain: parseDomain(process.env.FEISHU_APP_DOMAIN),
      eventMode: parseEventMode(process.env.FEISHU_EVENT_MODE),
      encryptKey: optional(process.env.FEISHU_APP_ENCRYPT_KEY),
      verificationToken: optional(process.env.FEISHU_APP_VERIFICATION_TOKEN)
    },
    security: {
      allowedUserIds,
      projectWhitelist,
      defaultProjectPath,
      dangerousApprovalEnabled: parseBoolean(process.env.DANGEROUS_APPROVAL_ENABLED, true)
    },
    runner: {
      defaultRunner: parseRunner(process.env.DEFAULT_RUNNER),
      taskTimeoutMs: parseNumber("TASK_TIMEOUT_MS", 15 * 60 * 1000),
      summaryIntervalMs: parseNumber("SUMMARY_INTERVAL_MS", 30 * 1000),
      streamUpdateIntervalMs: parseNumber("STREAM_UPDATE_INTERVAL_MS", 3000),
      claudeBin: optional(process.env.CLAUDE_BIN) ?? "claude",
      claudeMode: parseClaudeMode(process.env.CLAUDE_MODE),
      claudeExtraArgs: parseArgList(process.env.CLAUDE_EXTRA_ARGS),
      codexBin: optional(process.env.CODEX_BIN) ?? "codex",
      codexSandbox: parseCodexSandbox(process.env.CODEX_SANDBOX),
      codexApprovalPolicy: parseCodexApprovalPolicy(process.env.CODEX_APPROVAL_POLICY),
      codexExtraArgs: parseArgList(process.env.CODEX_EXTRA_ARGS),
      codexAppServerUrl: optional(process.env.CODEX_APP_SERVER_URL),
      codexAppServerToken: optional(process.env.CODEX_APP_SERVER_TOKEN)
    },
    video: {
      ffmpegBin: optional(process.env.FFMPEG_BIN) ?? "ffmpeg",
      frameIntervalSeconds: parseNumber("VIDEO_FRAME_INTERVAL_SECONDS", 1),
      maxFrames: parseNumber("VIDEO_MAX_FRAMES", 300),
      frameWidth: parseNumber("VIDEO_FRAME_WIDTH", 1280),
      codexBatchSize: parseNumber("VIDEO_CODEX_BATCH_SIZE", 20)
    },
    attachments: {
      dir: path.resolve(optional(process.env.ATTACHMENT_DIR) ?? "./data/attachments"),
      retentionMs: parseNumber("ATTACHMENT_RETENTION_HOURS", 24) * 60 * 60 * 1000,
      cleanupIntervalMs: parseNumber("ATTACHMENT_CLEANUP_INTERVAL_MINUTES", 60) * 60 * 1000
    },
    report: {
      token: optional(process.env.REPORT_TOKEN),
      maxBodyBytes: parseNumber("REPORT_MAX_BODY_BYTES", 256 * 1024)
    },
    sqlitePath: path.resolve(optional(process.env.SQLITE_PATH) ?? "./data/gateway.sqlite")
  };
}

