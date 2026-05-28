import type { AppConfig } from "../config.js";
import type { AgentRunner, RunnerName } from "../domain/types.js";
import { ClaudeRunner } from "./claude-runner.js";
import { CodexRunner } from "./codex-runner.js";

export function createRunners(config: AppConfig): Record<RunnerName, AgentRunner> {
  return {
    claude: new ClaudeRunner({
      bin: config.runner.claudeBin,
      mode: config.runner.claudeMode,
      extraArgs: config.runner.claudeExtraArgs
    }),
    codex: new CodexRunner({
      bin: config.runner.codexBin,
      sandbox: config.runner.codexSandbox,
      approvalPolicy: config.runner.codexApprovalPolicy,
      extraArgs: config.runner.codexExtraArgs,
      allowedProjectPaths: config.security.projectWhitelist,
      maxImagesPerTurn: config.video.codexBatchSize,
      appServerUrl: config.runner.codexAppServerUrl,
      token: config.runner.codexAppServerToken
    })
  };
}

