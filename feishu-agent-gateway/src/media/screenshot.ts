import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdir, stat } from "node:fs/promises";
import path from "node:path";

export interface ScreenshotCapture {
  path: string;
  bytes: number;
  width: number;
  height: number;
}

const SCREENSHOT_TIMEOUT_MS = 15_000;
const MIN_USEFUL_SCREENSHOT_BYTES = 10 * 1024;

export async function captureDesktopScreenshot(attachmentDir: string): Promise<ScreenshotCapture> {
  await mkdir(attachmentDir, { recursive: true });
  const filePath = path.join(attachmentDir, `desktop_${timestampForFilename()}_${randomUUID()}.png`);
  const result = await captureWithPowerShell(filePath);
  const file = await stat(result.path);

  if (file.size < MIN_USEFUL_SCREENSHOT_BYTES) {
    throw new Error("截图文件过小，可能是黑屏或当前进程无法访问可见桌面。");
  }

  return {
    ...result,
    bytes: file.size
  };
}

function captureWithPowerShell(outputPath: string): Promise<ScreenshotCapture> {
  return new Promise((resolve, reject) => {
    let stdout = "";
    let stderr = "";
    let timedOut = false;

    const child = spawn(
      "powershell.exe",
      ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", SCREENSHOT_SCRIPT],
      {
        env: {
          ...process.env,
          SCREENSHOT_OUTPUT_PATH: outputPath
        },
        windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"]
      }
    );

    const timeout = setTimeout(() => {
      timedOut = true;
      child.kill();
    }, SCREENSHOT_TIMEOUT_MS);

    child.stdout.on("data", (chunk: Buffer | string) => {
      stdout += chunk.toString();
    });

    child.stderr.on("data", (chunk: Buffer | string) => {
      stderr += chunk.toString();
    });

    child.on("error", (error) => {
      clearTimeout(timeout);
      reject(error);
    });

    child.on("close", (code) => {
      clearTimeout(timeout);
      if (timedOut) {
        reject(new Error("截图超时。"));
        return;
      }

      if (code !== 0) {
        reject(new Error(`截图失败：${stderr.trim() || stdout.trim() || `PowerShell code=${code}`}`));
        return;
      }

      const parsed = parseScreenshotJson(stdout);
      if (!parsed) {
        reject(new Error(`截图失败：无法解析截图结果。${stderr.trim() || stdout.trim()}`));
        return;
      }

      resolve(parsed);
    });
  });
}

function parseScreenshotJson(stdout: string): ScreenshotCapture | null {
  const line = stdout
    .split(/\r?\n/)
    .map((item) => item.trim())
    .find((item) => item.startsWith("{") && item.endsWith("}"));
  if (!line) {
    return null;
  }

  try {
    const parsed = JSON.parse(line) as unknown;
    if (!isRecord(parsed)) {
      return null;
    }

    if (
      typeof parsed.path !== "string" ||
      typeof parsed.bytes !== "number" ||
      typeof parsed.width !== "number" ||
      typeof parsed.height !== "number"
    ) {
      return null;
    }

    return {
      path: parsed.path,
      bytes: parsed.bytes,
      width: parsed.width,
      height: parsed.height
    };
  } catch {
    return null;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function timestampForFilename(): string {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

const SCREENSHOT_SCRIPT = String.raw`
$ErrorActionPreference = "Stop"
try {
  $out = $env:SCREENSHOT_OUTPUT_PATH
  if (-not $out) {
    throw "SCREENSHOT_OUTPUT_PATH is empty."
  }

  $dir = Split-Path -Parent $out
  New-Item -ItemType Directory -Force -Path $dir | Out-Null

  Add-Type -AssemblyName System.Windows.Forms
  Add-Type -AssemblyName System.Drawing

  $bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
  $bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)

  try {
    $graphics.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bounds.Size)
    $bitmap.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
  } finally {
    if ($graphics) { $graphics.Dispose() }
    if ($bitmap) { $bitmap.Dispose() }
  }

  $item = Get-Item -LiteralPath $out
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  [Console]::WriteLine((@{
    path = $item.FullName
    bytes = [int64]$item.Length
    width = [int]$bounds.Width
    height = [int]$bounds.Height
  } | ConvertTo-Json -Compress))
} catch {
  [Console]::Error.WriteLine($_.Exception.Message)
  exit 1
}
`;
