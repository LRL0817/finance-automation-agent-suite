import { spawn } from "node:child_process";
import type { AgentRunner, RunnerContext, RunnerResult } from "../domain/types.js";
import { detectChangedFiles, summarizeTestResult } from "./helpers.js";
import { formatSpawnError, prepareSpawnCommand, preflightSpawnEnvironment } from "./spawn-command.js";

interface ClaudeRunnerOptions {
  bin: string;
  mode: "print" | "bare";
  extraArgs: string[];
}

export class ClaudeRunner implements AgentRunner {
  readonly name = "claude" as const;

  constructor(private readonly options: ClaudeRunnerOptions) {}

  async run(context: RunnerContext): Promise<RunnerResult> {
    const args =
      this.options.mode === "bare"
        ? [...this.options.extraArgs, "--bare", context.prompt]
        : [...this.options.extraArgs, "-p", context.prompt];

    const output = await new Promise<string>((resolve, reject) => {
      let combined = "";
      const preparedCommand = prepareSpawnCommand(this.options.bin, args);

      const preflightReason = preflightSpawnEnvironment({
        prepared: preparedCommand,
        cwd: context.projectPath
      });
      if (preflightReason) {
        reject(
          new Error(
            `Claude runner 启动前置校验失败：${preflightReason}\n` +
              formatSpawnError({
                prepared: preparedCommand,
                cwd: context.projectPath,
                err: { code: "PRECHECK", message: preflightReason },
                source: "Claude runner preflight"
              })
          )
        );
        return;
      }

      const child = spawn(preparedCommand.command, preparedCommand.args, {
        cwd: context.projectPath,
        env: process.env,
        windowsHide: true,
        signal: context.signal
      });

      const append = (chunk: Buffer | string): void => {
        const text = chunk.toString();
        combined += text;
        context.onOutput(text);
      };

      child.stdout.on("data", append);
      child.stderr.on("data", append);

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
              source: "Claude runner spawn"
            })
          )
        );
      });

      child.on("close", (code, signal) => {
        if (context.signal.aborted) {
          reject(new Error("任务已取消。"));
          return;
        }

        if (code === 0) {
          resolve(combined.trim());
          return;
        }

        reject(
          new Error(
            `Claude runner 退出异常：code=${code ?? "null"}, signal=${signal ?? "null"}\n${combined.trim()}`
          )
        );
      });
    });

    return {
      output,
      changedFiles: await detectChangedFiles(context.projectPath),
      testResult: summarizeTestResult(output)
    };
  }
}
