import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

export async function detectChangedFiles(projectPath: string): Promise<string[]> {
  try {
    const { stdout } = await execFileAsync("git", ["status", "--short"], {
      cwd: projectPath,
      timeout: 5000,
      windowsHide: true
    });

    return stdout
      .split(/\r?\n/)
      .map((line) => line.trimEnd())
      .filter(Boolean)
      .map((line) => line.slice(3).trim())
      .filter(Boolean);
  } catch {
    return [];
  }
}

export function summarizeTestResult(output: string): string {
  const lines = output
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);

  const interesting = lines.filter((line) =>
    /(test|tests|passed|passing|failed|failing|success|error|vitest|jest|pytest|npm ERR|pnpm ERR|yarn ERR|✓|✗)/i.test(
      line
    )
  );

  if (interesting.length === 0) {
    return "未检测到自动测试结果。";
  }

  return interesting.slice(-12).join("\n");
}
