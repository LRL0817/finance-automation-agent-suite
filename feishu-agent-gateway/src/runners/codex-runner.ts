import { spawn } from "node:child_process";
import type { AgentRunner, RunnerContext, RunnerResult } from "../domain/types.js";
import { detectChangedFiles, summarizeTestResult } from "./helpers.js";
import { formatSpawnError, prepareSpawnCommand, preflightSpawnEnvironment } from "./spawn-command.js";

interface CodexRunnerOptions {
  bin: string;
  sandbox: "read-only" | "workspace-write" | "danger-full-access";
  approvalPolicy: "untrusted" | "on-failure" | "on-request" | "never";
  extraArgs: string[];
  allowedProjectPaths: string[];
  maxImagesPerTurn: number;
  appServerUrl?: string;
  token?: string;
}

interface CodexRunResponse {
  output?: string;
  changed_files?: string[];
  changedFiles?: string[];
  test_result?: string;
  testResult?: string;
}

interface CliTurnResult {
  output: string;
  runnerThreadId: string | null;
}

export class CodexRunner implements AgentRunner {
  readonly name = "codex" as const;

  constructor(private readonly options: CodexRunnerOptions) {}

  async run(context: RunnerContext): Promise<RunnerResult> {
    if (this.options.appServerUrl) {
      return this.runViaAppServer(context);
    }

    return this.runViaCli(context);
  }

  private async runViaCli(context: RunnerContext): Promise<RunnerResult> {
    const imagePaths = context.imagePaths ?? [];
    if (imagePaths.length <= this.options.maxImagesPerTurn) {
      const turn = await this.runSingleCliTurn(context, context.prompt, imagePaths, context.runnerThreadId ?? null);
      return {
        output: turn.output,
        changedFiles: [],
        testResult: "Codex CLI 对话模式未单独提取测试结果。",
        runnerThreadId: turn.runnerThreadId
      };
    }

    return this.runChunkedCliTurns(context, imagePaths);
  }

  private async runChunkedCliTurns(context: RunnerContext, imagePaths: string[]): Promise<RunnerResult> {
    const chunks = chunkArray(imagePaths, this.options.maxImagesPerTurn);
    let runnerThreadId = context.runnerThreadId ?? null;
    const chunkOutputs: string[] = [];

    for (let index = 0; index < chunks.length; index += 1) {
      const chunk = chunks[index];
      const prompt = buildMediaChunkPrompt(
        context.prompt,
        index,
        chunks.length,
        chunk.length,
        this.options.maxImagesPerTurn,
        context.videoFrameIntervalSeconds
      );
      const turn = await this.runSingleCliTurn(context, prompt, chunk, runnerThreadId);
      runnerThreadId = turn.runnerThreadId;
      chunkOutputs.push(turn.output);
    }

    const finalPrompt = [
      "请基于刚才所有批次的视频逐秒观察记录，回答用户原始问题。",
      "要求：保留关键时间点；如果有连续操作、弹窗、报错、按钮状态变化，请按时间线说明。",
      "",
      "用户原始问题：",
      context.prompt
    ].join("\n");
    const finalTurn = await this.runSingleCliTurn(context, finalPrompt, [], runnerThreadId);

    return {
      output: finalTurn.output || chunkOutputs.join("\n\n"),
      changedFiles: [],
      testResult: "Codex CLI 视频分批视觉分析未单独提取测试结果。",
      runnerThreadId: finalTurn.runnerThreadId ?? runnerThreadId
    };
  }

  private async runSingleCliTurn(
    context: RunnerContext,
    prompt: string,
    imagePaths: string[],
    runnerThreadId: string | null
  ): Promise<CliTurnResult> {
    const addDirArgs = this.options.allowedProjectPaths.flatMap((projectPath) =>
      samePath(projectPath, context.projectPath) ? [] : ["--add-dir", projectPath]
    );
    const imageArgs = imagePaths.map((imagePath) => `--image=${imagePath}`);

    const args = runnerThreadId
      ? [
          "--ask-for-approval",
          this.options.approvalPolicy,
          "--sandbox",
          this.options.sandbox,
          "exec",
          "resume",
          "--json",
          "--skip-git-repo-check",
          ...imageArgs,
          ...this.options.extraArgs,
          runnerThreadId
        ]
      : [
          "--ask-for-approval",
          this.options.approvalPolicy,
          "exec",
          "--json",
          "--cd",
          context.projectPath,
          ...addDirArgs,
          "--sandbox",
          this.options.sandbox,
          "--skip-git-repo-check",
          ...imageArgs,
          ...this.options.extraArgs
        ];

    let parsedRunnerThreadId = runnerThreadId;
    const output = await new Promise<string>((resolve, reject) => {
      let stdoutBuffer = "";
      let assistantOutput = "";
      let stderrOutput = "";
      const preparedCommand = prepareSpawnCommand(this.options.bin, args);

      const preflightReason = preflightSpawnEnvironment({
        prepared: preparedCommand,
        cwd: context.projectPath
      });
      if (preflightReason) {
        reject(
          new Error(
            `Codex CLI 启动前置校验失败：${preflightReason}\n` +
              formatSpawnError({
                prepared: preparedCommand,
                cwd: context.projectPath,
                err: { code: "PRECHECK", message: preflightReason },
                source: "Codex CLI preflight"
              })
          )
        );
        return;
      }

      const child = spawn(preparedCommand.command, preparedCommand.args, {
        cwd: context.projectPath,
        env: process.env,
        windowsHide: true,
        signal: context.signal,
        stdio: ["pipe", "pipe", "pipe"]
      });

      child.stdin.on("error", () => {
        // The child may close stdin early after accepting the prompt.
      });
      child.stdin.end(prompt, "utf-8");

      const appendAssistant = (text: string): void => {
        assistantOutput += text;
        context.onOutput(text);
      };

      child.stdout.on("data", (chunk: Buffer | string) => {
        stdoutBuffer += chunk.toString();
        const lines = stdoutBuffer.split(/\r?\n/);
        stdoutBuffer = lines.pop() ?? "";

        for (const line of lines) {
          const event = parseJsonLine(line);
          if (!event) {
            continue;
          }

          const parsedThreadId = getThreadId(event);
          if (parsedThreadId) {
            parsedRunnerThreadId = parsedThreadId;
          }

          const message = getUserVisibleMessage(event);
          if (message) {
            appendAssistant(message);
          }
        }
      });

      child.stderr.on("data", (chunk: Buffer | string) => {
        stderrOutput += chunk.toString();
      });

      child.on("error", (error) => {
        if (context.signal.aborted) {
          reject(new Error("任务已取消。"));
          return;
        }
        reject(
          new Error(
            formatSpawnError({
              prepared: preparedCommand,
              cwd: context.projectPath,
              err: error,
              source: "Codex CLI spawn"
            })
          )
        );
      });

      child.on("close", (code, signal) => {
        if (stdoutBuffer.trim()) {
          const event = parseJsonLine(stdoutBuffer);
          const parsedThreadId = event ? getThreadId(event) : null;
          if (parsedThreadId) {
            parsedRunnerThreadId = parsedThreadId;
          }
          const message = event ? getUserVisibleMessage(event) : stdoutBuffer;
          if (message) {
            appendAssistant(message);
          }
        }

        if (context.signal.aborted) {
          reject(new Error("任务已取消。"));
          return;
        }

        if (code === 0) {
          resolve(assistantOutput.trim());
          return;
        }

        reject(
          new Error(
            `Codex CLI 退出异常：code=${code ?? "null"}, signal=${signal ?? "null"}\n${assistantOutput.trim()}\n${stderrOutput.trim()}`
          )
        );
      });
    });

    return {
      output,
      runnerThreadId: parsedRunnerThreadId
    };
  }

  private async runViaAppServer(context: RunnerContext): Promise<RunnerResult> {
    const endpoint = new URL("/api/runs", this.options.appServerUrl);
    const response = await fetch(endpoint, {
      method: "POST",
      signal: context.signal,
      headers: {
        "content-type": "application/json",
        ...(this.options.token ? { authorization: `Bearer ${this.options.token}` } : {})
      },
      body: JSON.stringify({
        session_id: context.sessionId,
        task_id: context.taskId,
        user_id: context.userId,
        project_path: context.projectPath,
        prompt: context.prompt,
        image_paths: context.imagePaths ?? [],
        video_frame_interval_seconds: context.videoFrameIntervalSeconds
      })
    });

    const bodyText = await response.text();
    context.onOutput(bodyText);

    if (!response.ok) {
      throw new Error(`Codex app-server 请求失败：HTTP ${response.status}\n${bodyText}`);
    }

    const body = parseJson(bodyText);
    const output = body.output ?? bodyText;
    const changedFiles =
      body.changed_files ?? body.changedFiles ?? (await detectChangedFiles(context.projectPath));
    const testResult = body.test_result ?? body.testResult ?? summarizeTestResult(output);

    return {
      output,
      changedFiles,
      testResult,
      runnerThreadId: context.runnerThreadId
    };
  }
}

function parseJsonLine(line: string): unknown | null {
  const trimmed = line.trim();
  if (!trimmed) {
    return null;
  }

  try {
    return JSON.parse(trimmed) as unknown;
  } catch {
    return null;
  }
}

function getThreadId(event: unknown): string | null {
  if (!isRecord(event)) {
    return null;
  }

  return typeof event.thread_id === "string" ? event.thread_id : null;
}

function getUserVisibleMessage(event: unknown): string | null {
  if (!isRecord(event)) {
    return null;
  }

  if (event.type === "item.completed" && isRecord(event.item)) {
    const item = event.item;
    if (item.type === "agent_message" && typeof item.text === "string") {
      return item.text;
    }

    if (item.type === "command_execution" && typeof item.output === "string") {
      return item.output;
    }
  }

  if (event.type === "error" && typeof event.message === "string") {
    if (isTransientCodexReconnectMessage(event.message)) {
      return null;
    }
    return `\n[Codex error] ${event.message}\n`;
  }

  if (event.type === "turn.failed" && isRecord(event.error) && typeof event.error.message === "string") {
    return `\n[Codex failed] ${event.error.message}\n`;
  }

  return null;
}

function isTransientCodexReconnectMessage(message: string): boolean {
  return /^Reconnecting\.\.\. \d+\/\d+ \(timeout waiting for child process to exit\)$/i.test(message.trim());
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function samePath(left: string, right: string): boolean {
  const normalizedLeft = left.replace(/[\\/]+$/, "");
  const normalizedRight = right.replace(/[\\/]+$/, "");

  if (process.platform === "win32") {
    return normalizedLeft.toLowerCase() === normalizedRight.toLowerCase();
  }

  return normalizedLeft === normalizedRight;
}

function parseJson(text: string): CodexRunResponse {
  try {
    return JSON.parse(text) as CodexRunResponse;
  } catch {
    return { output: text };
  }
}

function chunkArray<T>(items: T[], size: number): T[][] {
  const safeSize = Math.max(1, Math.floor(size));
  const chunks: T[][] = [];
  for (let index = 0; index < items.length; index += safeSize) {
    chunks.push(items.slice(index, index + safeSize));
  }
  return chunks;
}

function buildMediaChunkPrompt(
  originalPrompt: string,
  chunkIndex: number,
  totalChunks: number,
  chunkSize: number,
  batchSize: number,
  frameIntervalSeconds: number | undefined
): string {
  const firstFrameNumber = chunkIndex * batchSize + 1;
  const lastFrameNumber = firstFrameNumber + chunkSize - 1;
  const timeHint = frameIntervalSeconds
    ? `这些附件按顺序对应视频约第 ${formatSeconds((firstFrameNumber - 1) * frameIntervalSeconds)} 到第 ${formatSeconds((lastFrameNumber - 1) * frameIntervalSeconds)}，每张间隔约 ${frameIntervalSeconds} 秒。`
    : `这些附件按顺序是第 ${firstFrameNumber} 到第 ${lastFrameNumber} 张图片。`;

  return [
    `这是视频/多图分析的第 ${chunkIndex + 1}/${totalChunks} 批。${timeHint}`,
    "请只处理本批附件，逐张观察并记录细节；不要急着给最终结论。",
    "输出格式尽量按时间点/图片序号列出，重点记录界面文字、按钮、弹窗、报错、鼠标或窗口变化、异常现象。",
    "",
    "用户原始问题：",
    originalPrompt
  ].join("\n");
}

function formatSeconds(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds));
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}:${rest.toString().padStart(2, "0")}`;
}
