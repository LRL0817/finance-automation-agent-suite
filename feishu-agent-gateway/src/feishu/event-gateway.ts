import http from "node:http";
import crypto from "node:crypto";
import * as lark from "@larksuiteoapi/node-sdk";
import type { AppConfig } from "../config.js";
import type { CommandController } from "../commands/controller.js";
import type { IncomingFeishuMessage } from "../domain/types.js";
import { logger } from "../logger.js";
import type { ReportController } from "../reports/report-controller.js";

export class FeishuEventGateway {
  private readonly dispatcher: unknown;
  private server?: http.Server;
  private wsClient?: unknown;

  constructor(
    private readonly config: AppConfig,
    private readonly controller: CommandController,
    private readonly reportController?: ReportController
  ) {
    this.dispatcher = this.createDispatcher();
  }

  start(): void {
    if (this.config.feishu.eventMode === "ws" || this.config.feishu.eventMode === "both") {
      this.startWebSocket();
    }

    if (
      this.config.feishu.eventMode === "webhook" ||
      this.config.feishu.eventMode === "both" ||
      this.config.env === "development"
    ) {
      this.startHttpServer();
    }
  }

  async stop(): Promise<void> {
    await new Promise<void>((resolve) => {
      if (!this.server) {
        resolve();
        return;
      }
      this.server.close(() => resolve());
    });
  }

  private createDispatcher(): unknown {
    const larkAny = lark as unknown as {
      EventDispatcher: new (options: Record<string, unknown>) => {
        register: (handlers: Record<string, (data: unknown) => Promise<unknown>>) => unknown;
      };
    };

    const dispatcherOptions: Record<string, unknown> = {};
    if (this.config.feishu.encryptKey) {
      dispatcherOptions.encryptKey = this.config.feishu.encryptKey;
    }
    if (this.config.feishu.verificationToken) {
      dispatcherOptions.verificationToken = this.config.feishu.verificationToken;
    }

    return new larkAny.EventDispatcher(dispatcherOptions).register({
      "im.message.receive_v1": async (data: unknown) => {
        const message = extractMessage(data);
        logger.info("Received Feishu message", {
          chatId: message.chatId,
          userId: message.userId,
          messageId: message.messageId,
          messageType: message.messageType,
          imageCount: message.imageKeys.length,
          videoCount: message.videoKeys.length
        });
        await this.controller.handleMessage(message);
        return {};
      }
    });
  }

  private startWebSocket(): void {
    const larkAny = lark as unknown as {
      WSClient: new (options: Record<string, unknown>) => { start: (options: Record<string, unknown>) => void };
      Domain: { Feishu: unknown; Lark: unknown };
      LoggerLevel: { info: unknown };
    };

    this.wsClient = new larkAny.WSClient({
      appId: this.config.feishu.appId,
      appSecret: this.config.feishu.appSecret,
      domain: this.config.feishu.domain === "lark" ? larkAny.Domain.Lark : larkAny.Domain.Feishu,
      loggerLevel: larkAny.LoggerLevel.info
    });

    (this.wsClient as { start: (options: Record<string, unknown>) => void }).start({
      eventDispatcher: this.dispatcher
    });
    logger.info("Feishu WebSocket event gateway started");
  }

  private startHttpServer(): void {
    const larkAny = lark as unknown as {
      adaptDefault: (
        path: string,
        dispatcher: unknown,
        options?: Record<string, unknown>
      ) => http.RequestListener;
    };

    const webhookHandler = larkAny.adaptDefault("/webhook/event", this.dispatcher, {
      autoChallenge: true
    });

    this.server = http.createServer((request, response) => {
      const url = new URL(request.url ?? "/", `http://${request.headers.host ?? "localhost"}`);

      if (url.pathname === "/healthz") {
        response.writeHead(200, { "content-type": "application/json" });
        response.end(JSON.stringify({ ok: true }));
        return;
      }

      if (url.pathname === "/webhook/event") {
        webhookHandler(request, response);
        return;
      }

      if (url.pathname === "/agent/report") {
        void this.handleReportRequest(request, response);
        return;
      }

      response.writeHead(404, { "content-type": "application/json" });
      response.end(JSON.stringify({ error: "not_found" }));
    });

    this.server.listen(this.config.port, () => {
      logger.info(`HTTP server listening on :${this.config.port}`);
      logger.info(`Feishu webhook path: /webhook/event`);
    });
  }

  private async handleReportRequest(
    request: http.IncomingMessage,
    response: http.ServerResponse
  ): Promise<void> {
    if (request.method !== "POST") {
      writeJson(response, 405, { ok: false, error: "method_not_allowed" });
      return;
    }

    if (!this.reportController) {
      writeJson(response, 503, { ok: false, error: "report_controller_not_configured" });
      return;
    }

    if (!this.isReportAuthorized(request)) {
      writeJson(response, 401, { ok: false, error: "unauthorized" });
      return;
    }

    try {
      const bodyText = await readBody(request, this.config.report.maxBodyBytes);
      const body = JSON.parse(bodyText) as unknown;
      const result = this.reportController.handleFailureReport(body);
      writeJson(response, result.status, result.body);
    } catch (error) {
      const message = error instanceof Error ? error.message : "bad_request";
      const status = message.includes("too large") ? 413 : 400;
      writeJson(response, status, { ok: false, error: message });
    }
  }

  private isReportAuthorized(request: http.IncomingMessage): boolean {
    const expected = this.config.report.token;
    if (!expected) {
      return false;
    }

    const authorization = request.headers.authorization;
    const bearerToken = authorization?.startsWith("Bearer ")
      ? authorization.slice("Bearer ".length).trim()
      : undefined;
    const headerToken = request.headers["x-report-token"];
    const actual = bearerToken ?? (Array.isArray(headerToken) ? headerToken[0] : headerToken);

    return typeof actual === "string" && timingSafeEqual(actual, expected);
  }
}

function readBody(request: http.IncomingMessage, maxBytes: number): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let total = 0;

    request.on("data", (chunk: Buffer) => {
      total += chunk.length;
      if (total > maxBytes) {
        reject(new Error("request body too large"));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });

    request.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    request.on("error", reject);
  });
}

function writeJson(
  response: http.ServerResponse,
  statusCode: number,
  body: Record<string, unknown>
): void {
  response.writeHead(statusCode, { "content-type": "application/json" });
  response.end(JSON.stringify(body));
}

function timingSafeEqual(left: string, right: string): boolean {
  const leftBuffer = Buffer.from(left);
  const rightBuffer = Buffer.from(right);
  if (leftBuffer.length !== rightBuffer.length) {
    return false;
  }

  return crypto.timingSafeEqual(leftBuffer, rightBuffer);
}

function extractMessage(data: unknown): IncomingFeishuMessage {
  const event = data as {
    sender?: { sender_id?: { user_id?: string; open_id?: string; union_id?: string } };
    message?: {
      chat_id?: string;
      message_id?: string;
      content?: string;
      message_type?: string;
    };
  };

  const chatId = event.message?.chat_id;
  const userId =
    event.sender?.sender_id?.user_id ??
    event.sender?.sender_id?.open_id ??
    event.sender?.sender_id?.union_id;

  if (!chatId || !userId) {
    throw new Error("飞书事件缺少 chat_id 或 sender_id。");
  }

  const messageType = event.message?.message_type;
  const parsedContent = parseMessageContent(event.message?.content);

  return {
    chatId,
    userId,
    messageId: event.message?.message_id,
    messageType,
    text: extractText(parsedContent),
    imageKeys: extractImageKeys(parsedContent),
    videoKeys: extractVideoKeys(parsedContent, messageType)
  };
}

function parseMessageContent(content: string | undefined): unknown {
  if (!content) {
    return null;
  }

  try {
    return JSON.parse(content) as unknown;
  } catch {
    return content;
  }
}

function extractText(parsedContent: unknown): string {
  if (!parsedContent) {
    return "";
  }

  if (typeof parsedContent === "string") {
    return parsedContent;
  }

  if (!isRecord(parsedContent)) {
    return "";
  }

  if (typeof parsedContent.text === "string") {
    return parsedContent.text;
  }

  const richText = collectTextFragments(parsedContent).join("").trim();
  if (richText) {
    return richText;
  }

  return "";
}

function collectTextFragments(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.flatMap((item) => collectTextFragments(item));
  }

  if (!isRecord(value)) {
    return [];
  }

  const fragments: string[] = [];
  if (value.tag === "text" && typeof value.text === "string") {
    fragments.push(value.text);
  }

  for (const child of Object.values(value)) {
    fragments.push(...collectTextFragments(child));
  }

  return fragments;
}

function extractImageKeys(parsedContent: unknown): string[] {
  const keys = new Set<string>();

  const visit = (value: unknown): void => {
    if (Array.isArray(value)) {
      for (const item of value) {
        visit(item);
      }
      return;
    }

    if (!isRecord(value)) {
      return;
    }

    if (typeof value.image_key === "string" && value.image_key.trim()) {
      keys.add(value.image_key.trim());
    }

    for (const child of Object.values(value)) {
      visit(child);
    }
  };

  visit(parsedContent);
  return [...keys];
}

function extractVideoKeys(parsedContent: unknown, messageType: string | undefined): string[] {
  const keys = new Set<string>();

  const visit = (value: unknown): void => {
    if (Array.isArray(value)) {
      for (const item of value) {
        visit(item);
      }
      return;
    }

    if (!isRecord(value)) {
      return;
    }

    const looksLikeVideo =
      messageType === "video" ||
      value.tag === "video" ||
      typeof value.duration === "number" ||
      typeof value.duration_ms === "number";
    if (looksLikeVideo && typeof value.file_key === "string" && value.file_key.trim()) {
      keys.add(value.file_key.trim());
    }

    for (const child of Object.values(value)) {
      visit(child);
    }
  };

  visit(parsedContent);
  return [...keys];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

