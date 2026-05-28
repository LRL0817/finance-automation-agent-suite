import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import * as lark from "@larksuiteoapi/node-sdk";
import type { AppConfig } from "../config.js";
import { logger } from "../logger.js";
import { truncateMiddle } from "../utils/text.js";

interface TenantTokenCache {
  token: string;
  expiresAtMs: number;
}

export class FeishuMessageClient {
  private readonly client: lark.Client;
  private readonly attachmentDir: string;
  private tenantToken?: TenantTokenCache;
  private videoFrameExtractorAvailable?: boolean;

  constructor(private readonly config: AppConfig) {
    this.attachmentDir = config.attachments.dir;
    const larkAny = lark as unknown as {
      AppType: { SelfBuild: unknown };
      Domain: { Feishu: unknown; Lark: unknown };
      LoggerLevel: { fatal: unknown; info: unknown };
    };

    this.client = new lark.Client({
      appId: config.feishu.appId,
      appSecret: config.feishu.appSecret,
      appType: larkAny.AppType.SelfBuild,
      domain: config.feishu.domain === "lark" ? larkAny.Domain.Lark : larkAny.Domain.Feishu,
      loggerLevel: larkAny.LoggerLevel.fatal
    } as never);
  }

  async sendText(chatId: string, text: string): Promise<string | null> {
    const body = {
      params: {
        receive_id_type: "chat_id"
      },
      data: {
        receive_id: chatId,
        content: JSON.stringify({ text: truncateMiddle(text, 9000) }),
        msg_type: "text"
      }
    };

    const messageApi = this.resolveMessageApi();
    const response = await withFeishuRetry(() => messageApi.create(body), "sendText");
    const messageId = extractMessageId(response);
    logger.info("Sent Feishu message", { chatId, messageId });
    return messageId;
  }

  async updateText(messageId: string, text: string): Promise<void> {
    const body = {
      path: {
        message_id: messageId
      },
      data: {
        content: JSON.stringify({ text: truncateMiddle(text, 9000) }),
        msg_type: "text"
      }
    };

    const messageApi = this.resolveMessageApi();
    await withFeishuRetry(() => messageApi.update(body), "updateText");
    logger.info("Updated Feishu message", { messageId });
  }

  async sendImage(chatId: string, imagePath: string): Promise<string | null> {
    const imageKey = await this.uploadImage(imagePath);
    const body = {
      params: {
        receive_id_type: "chat_id"
      },
      data: {
        receive_id: chatId,
        content: JSON.stringify({ image_key: imageKey }),
        msg_type: "image"
      }
    };

    const messageApi = this.resolveMessageApi();
    const response = await withFeishuRetry(() => messageApi.create(body), "sendImage");
    const messageId = extractMessageId(response);
    logger.info("Sent Feishu image message", { chatId, messageId, imagePath });
    return messageId;
  }

  async sendFile(chatId: string, filePath: string): Promise<string | null> {
    const fileKey = await this.uploadFile(filePath);
    const body = {
      params: {
        receive_id_type: "chat_id"
      },
      data: {
        receive_id: chatId,
        content: JSON.stringify({ file_key: fileKey }),
        msg_type: "file"
      }
    };

    const messageApi = this.resolveMessageApi();
    const response = await withFeishuRetry(() => messageApi.create(body), "sendFile");
    const messageId = extractMessageId(response);
    logger.info("Sent Feishu file message", { chatId, messageId, filePath });
    return messageId;
  }

  async downloadMessageImage(messageId: string, imageKey: string): Promise<string> {
    return this.downloadMessageResource(messageId, imageKey, "image", ".jpg");
  }

  async downloadMessageVideo(messageId: string, videoKey: string): Promise<string> {
    return this.downloadMessageResource(messageId, videoKey, "video", ".mp4");
  }

  async canExtractVideoFrames(): Promise<boolean> {
    if (this.videoFrameExtractorAvailable !== undefined) {
      return this.videoFrameExtractorAvailable;
    }

    this.videoFrameExtractorAvailable = await new Promise<boolean>((resolve) => {
      const child = spawn(this.ffmpegBin(), ["-version"], {
        windowsHide: true,
        stdio: "ignore"
      });
      child.on("error", () => resolve(false));
      child.on("close", (code) => resolve(code === 0));
    });

    return this.videoFrameExtractorAvailable;
  }

  async extractVideoFrames(videoPath: string): Promise<string[]> {
    const available = await this.canExtractVideoFrames();
    if (!available) {
      throw new Error("未找到 ffmpeg，无法从视频抽帧。");
    }

    const baseName = path.basename(videoPath, path.extname(videoPath));
    const frameDir = path.join(this.attachmentDir, `${baseName}_frames`);
    await mkdir(frameDir, { recursive: true });
    const outputPattern = path.join(frameDir, "frame_%05d.jpg");

    await runProcess(this.ffmpegBin(), [
      "-hide_banner",
      "-loglevel",
      "error",
      "-y",
      "-i",
      videoPath,
      "-vf",
      `fps=1/${this.config.video.frameIntervalSeconds},scale=${this.config.video.frameWidth}:-2`,
      "-q:v",
      "3",
      "-frames:v",
      String(this.config.video.maxFrames),
      outputPattern
    ]);

    const frames = (await readdir(frameDir))
      .filter((name) => /^frame_\d+\.jpg$/i.test(name))
      .sort()
      .map((name) => path.join(frameDir, name));

    if (frames.length === 0) {
      throw new Error("视频抽帧失败：没有生成任何图片。");
    }

    logger.info("Extracted video frames", { videoPath, frameCount: frames.length });
    return frames;
  }

  private async downloadMessageResource(
    messageId: string,
    resourceKey: string,
    type: "image" | "video",
    fallbackExtension: string
  ): Promise<string> {
    const token = await this.getTenantAccessToken();
    const endpoint = new URL(
      `/open-apis/im/v1/messages/${encodeURIComponent(messageId)}/resources/${encodeURIComponent(resourceKey)}`,
      this.openPlatformBaseUrl()
    );
    endpoint.searchParams.set("type", type);

    const response = await fetch(endpoint, {
      headers: {
        authorization: `Bearer ${token}`
      }
    });

    if (!response.ok) {
      const bodyText = await response.text().catch(() => "");
      throw new Error(
        `下载飞书${type === "image" ? "图片" : "视频"}失败：HTTP ${response.status}${bodyText ? ` ${truncateMiddle(bodyText, 500)}` : ""}`
      );
    }

    const buffer = Buffer.from(await response.arrayBuffer());
    if (buffer.length === 0) {
      throw new Error(`下载飞书${type === "image" ? "图片" : "视频"}失败：返回内容为空。`);
    }

    await mkdir(this.attachmentDir, { recursive: true });
    const extension = extensionFromContentType(response.headers.get("content-type"), fallbackExtension);
    const filePath = path.join(
      this.attachmentDir,
      `${sanitizeFilename(messageId)}_${sanitizeFilename(resourceKey)}${extension}`
    );
    await writeFile(filePath, buffer);
    logger.info(`Downloaded Feishu ${type}`, { messageId, resourceKey, filePath });
    return filePath;
  }

  private async uploadImage(imagePath: string): Promise<string> {
    const token = await this.getTenantAccessToken();
    const buffer = await readFile(imagePath);
    const { body, boundary } = buildImageUploadMultipart(imagePath, buffer);
    const endpoint = new URL("/open-apis/im/v1/images", this.openPlatformBaseUrl());

    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        authorization: `Bearer ${token}`,
        "content-type": `multipart/form-data; boundary=${boundary}`,
        "content-length": String(body.length)
      },
      body
    });
    const bodyText = await response.text();

    if (!response.ok) {
      throw new Error(`上传飞书图片失败：HTTP ${response.status}${bodyText ? ` ${truncateMiddle(bodyText, 500)}` : ""}`);
    }

    const parsed = parseJsonObject(bodyText);
    const code = typeof parsed.code === "number" ? parsed.code : 0;
    if (code !== 0) {
      const message = typeof parsed.msg === "string" ? parsed.msg : bodyText;
      throw new Error(`上传飞书图片失败：${truncateMiddle(message, 500)}`);
    }

    const imageKey =
      isRecord(parsed.data) && typeof parsed.data.image_key === "string"
        ? parsed.data.image_key
        : null;
    if (!imageKey) {
      throw new Error(`上传飞书图片失败：响应缺少 image_key。${truncateMiddle(bodyText, 500)}`);
    }

    return imageKey;
  }

  private async uploadFile(filePath: string): Promise<string> {
    const token = await this.getTenantAccessToken();
    const buffer = await readFile(filePath);
    const { body, boundary } = buildFileUploadMultipart(filePath, buffer);
    const endpoint = new URL("/open-apis/im/v1/files", this.openPlatformBaseUrl());

    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        authorization: `Bearer ${token}`,
        "content-type": `multipart/form-data; boundary=${boundary}`,
        "content-length": String(body.length)
      },
      body
    });
    const bodyText = await response.text();

    if (!response.ok) {
      throw new Error(`上传飞书文件失败：HTTP ${response.status}${bodyText ? ` ${truncateMiddle(bodyText, 500)}` : ""}`);
    }

    const parsed = parseJsonObject(bodyText);
    const code = typeof parsed.code === "number" ? parsed.code : 0;
    if (code !== 0) {
      const message = typeof parsed.msg === "string" ? parsed.msg : bodyText;
      throw new Error(`上传飞书文件失败：${truncateMiddle(message, 500)}`);
    }

    const fileKey =
      isRecord(parsed.data) && typeof parsed.data.file_key === "string"
        ? parsed.data.file_key
        : null;
    if (!fileKey) {
      throw new Error(`上传飞书文件失败：响应缺少 file_key。${truncateMiddle(bodyText, 500)}`);
    }

    return fileKey;
  }

  private resolveMessageApi(): {
    create: (body: unknown) => Promise<unknown>;
    update: (body: unknown) => Promise<unknown>;
  } {
    const clientAny = this.client as unknown as {
      im?: {
        v1?: {
          message?: {
            create?: (body: unknown) => Promise<unknown>;
            update?: (body: unknown) => Promise<unknown>;
          };
        };
        message?: {
          create?: (body: unknown) => Promise<unknown>;
          update?: (body: unknown) => Promise<unknown>;
        };
      };
    };

    const api = clientAny.im?.v1?.message ?? clientAny.im?.message;
    if (!api?.create || !api.update) {
      throw new Error("当前 @larksuiteoapi/node-sdk 未暴露 im.message create/update API。");
    }

    return api as {
      create: (body: unknown) => Promise<unknown>;
      update: (body: unknown) => Promise<unknown>;
    };
  }

  private async getTenantAccessToken(): Promise<string> {
    const now = Date.now();
    if (this.tenantToken && this.tenantToken.expiresAtMs > now + 60_000) {
      return this.tenantToken.token;
    }

    const endpoint = new URL(
      "/open-apis/auth/v3/tenant_access_token/internal",
      this.openPlatformBaseUrl()
    );
    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "content-type": "application/json; charset=utf-8"
      },
      body: JSON.stringify({
        app_id: this.config.feishu.appId,
        app_secret: this.config.feishu.appSecret
      })
    });
    const bodyText = await response.text();

    if (!response.ok) {
      throw new Error(`获取飞书 tenant_access_token 失败：HTTP ${response.status}`);
    }

    const body = parseJsonObject(bodyText);
    const token =
      typeof body.tenant_access_token === "string"
        ? body.tenant_access_token
        : isRecord(body.data) && typeof body.data.tenant_access_token === "string"
          ? body.data.tenant_access_token
          : null;
    const expireSeconds =
      typeof body.expire === "number"
        ? body.expire
        : isRecord(body.data) && typeof body.data.expire === "number"
          ? body.data.expire
          : 7200;

    if (!token) {
      throw new Error(`获取飞书 tenant_access_token 失败：${truncateMiddle(bodyText, 500)}`);
    }

    this.tenantToken = {
      token,
      expiresAtMs: now + Math.max(60, expireSeconds - 120) * 1000
    };
    return token;
  }

  private openPlatformBaseUrl(): string {
    return this.config.feishu.domain === "lark"
      ? "https://open.larksuite.com"
      : "https://open.feishu.cn";
  }

  private ffmpegBin(): string {
    return this.config.video.ffmpegBin;
  }
}

function extractMessageId(response: unknown): string | null {
  if (!isRecord(response)) {
    return null;
  }

  const data = response.data;
  if (!isRecord(data)) {
    return null;
  }

  return typeof data.message_id === "string" ? data.message_id : null;
}

async function withFeishuRetry<T>(operation: () => Promise<T>, label: string): Promise<T> {
  const attempts = Math.max(1, Number(process.env.FEISHU_SEND_RETRIES ?? "3"));
  let lastError: unknown;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      return await operation();
    } catch (error) {
      lastError = error;
      if (attempt >= attempts) {
        break;
      }
      const waitMs = 1000 * attempt;
      logger.warn(`Feishu ${label} failed, retrying`, {
        attempt,
        attempts,
        waitMs,
        error: safeErrorMessage(error)
      });
      await sleep(waitMs);
    }
  }
  throw lastError;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function safeErrorMessage(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error);
  return raw
    .replace(/("app_secret"\s*:\s*")[^"]+/gi, "$1[hidden]")
    .replace(/(app_secret=)[^\s&]+/gi, "$1[hidden]")
    .replace(/(Authorization:\s*Bearer\s+)[^\s]+/gi, "$1[hidden]");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseJsonObject(text: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(text) as unknown;
    return isRecord(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function extensionFromContentType(contentType: string | null, fallback: string): string {
  const normalized = contentType?.toLowerCase() ?? "";
  if (normalized.includes("png")) {
    return ".png";
  }
  if (normalized.includes("webp")) {
    return ".webp";
  }
  if (normalized.includes("gif")) {
    return ".gif";
  }
  if (normalized.includes("mp4")) {
    return ".mp4";
  }
  if (normalized.includes("quicktime")) {
    return ".mov";
  }
  return fallback;
}

function buildImageUploadMultipart(imagePath: string, image: Buffer): { body: Buffer; boundary: string } {
  const boundary = `----feishu-agent-gateway-${randomUUID()}`;
  const filename = escapeMultipartFilename(path.basename(imagePath));
  const contentType = imageContentType(imagePath);
  const header = [
    `--${boundary}`,
    'Content-Disposition: form-data; name="image_type"',
    "",
    "message",
    `--${boundary}`,
    `Content-Disposition: form-data; name="image"; filename="${filename}"`,
    `Content-Type: ${contentType}`,
    "",
    ""
  ].join("\r\n");
  const footer = `\r\n--${boundary}--\r\n`;

  return {
    boundary,
    body: Buffer.concat([Buffer.from(header, "utf8"), image, Buffer.from(footer, "utf8")])
  };
}

function buildFileUploadMultipart(filePath: string, file: Buffer): { body: Buffer; boundary: string } {
  const boundary = `----feishu-agent-gateway-${randomUUID()}`;
  const filename = escapeMultipartFilename(path.basename(filePath));
  const header = [
    `--${boundary}`,
    'Content-Disposition: form-data; name="file_type"',
    "",
    "stream",
    `--${boundary}`,
    'Content-Disposition: form-data; name="file_name"',
    "",
    filename,
    `--${boundary}`,
    `Content-Disposition: form-data; name="file"; filename="${filename}"`,
    "Content-Type: application/octet-stream",
    "",
    ""
  ].join("\r\n");
  const footer = `\r\n--${boundary}--\r\n`;

  return {
    boundary,
    body: Buffer.concat([Buffer.from(header, "utf8"), file, Buffer.from(footer, "utf8")])
  };
}

function imageContentType(imagePath: string): string {
  const extension = path.extname(imagePath).toLowerCase();
  switch (extension) {
    case ".jpg":
    case ".jpeg":
      return "image/jpeg";
    case ".png":
      return "image/png";
    case ".gif":
      return "image/gif";
    case ".webp":
      return "image/webp";
    default:
      return "application/octet-stream";
  }
}

function escapeMultipartFilename(filename: string): string {
  return filename.replace(/[\r\n"]/g, "_");
}

function sanitizeFilename(value: string): string {
  return value.replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 120) || "image";
}

function runProcess(command: string, args: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    let stderr = "";
    const child = spawn(command, args, {
      windowsHide: true,
      stdio: ["ignore", "ignore", "pipe"]
    });

    child.stderr.on("data", (chunk: Buffer | string) => {
      stderr += chunk.toString();
    });
    child.on("error", reject);
    child.on("close", (code, signal) => {
      if (code === 0) {
        resolve();
        return;
      }

      reject(
        new Error(
          `${command} 退出异常：code=${code ?? "null"}, signal=${signal ?? "null"}${stderr ? `\n${truncateMiddle(stderr, 800)}` : ""}`
        )
      );
    });
  });
}

