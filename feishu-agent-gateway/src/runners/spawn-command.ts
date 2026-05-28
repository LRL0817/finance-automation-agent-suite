import fs from "node:fs";
import path from "node:path";

interface PreparedSpawnCommand {
  command: string;
  args: string[];
  display: string;
  /** The bin resolved from PATH (with extension on Windows when applicable). */
  resolvedBin: string;
  /** True if Windows-only cmd.exe wrap was applied for .cmd / .bat. */
  windowsCmdWrapped: boolean;
  /** Diagnostic hints surfaced to the runner if it later needs to wrap an error. */
  diagnosticHints: string[];
}

const WINDOWS_EXECUTABLE_EXTENSIONS = [".cmd", ".exe", ".bat", ".com"];

export function prepareSpawnCommand(bin: string, args: string[]): PreparedSpawnCommand {
  if (process.platform !== "win32") {
    return {
      command: bin,
      args,
      display: formatDisplayCommand(bin, args),
      resolvedBin: bin,
      windowsCmdWrapped: false,
      diagnosticHints: []
    };
  }

  const resolvedBin = resolveWindowsCommand(bin);
  const extension = path.extname(resolvedBin).toLowerCase();
  const hints: string[] = [];
  if (!fileExists(resolvedBin) && resolvedBin === bin) {
    hints.push(
      `Windows PATH 解析未在 PATH 中找到 \"${bin}\" 对应的 .cmd/.exe/.bat/.com；spawn 大概率会 ENOENT`
    );
  }

  if (extension === ".cmd" || extension === ".bat") {
    return {
      command: "cmd.exe",
      args: ["/d", "/s", "/c", resolvedBin, ...args],
      display: formatDisplayCommand(resolvedBin, args),
      resolvedBin,
      windowsCmdWrapped: true,
      diagnosticHints: hints
    };
  }

  return {
    command: resolvedBin,
    args,
    display: formatDisplayCommand(resolvedBin, args),
    resolvedBin,
    windowsCmdWrapped: false,
    diagnosticHints: hints
  };
}

/** Pre-flight cwd/bin check. Returns null when everything looks spawnable;
 * otherwise returns a human-readable, *non-secret* reason string. */
export function preflightSpawnEnvironment(opts: {
  prepared: PreparedSpawnCommand;
  cwd: string;
}): string | null {
  const { prepared, cwd } = opts;
  if (!cwd || typeof cwd !== "string") {
    return "cwd 为空或不是字符串";
  }
  try {
    const stat = fs.statSync(cwd);
    if (!stat.isDirectory()) {
      return `cwd 存在但不是目录: ${cwd}`;
    }
  } catch (err: any) {
    return `cwd 不可用 (${err?.code ?? err?.errno ?? err?.message ?? "unknown"}): ${cwd}`;
  }
  // 在 Windows 上，如果 resolveWindowsCommand 没改写过 bin（resolvedBin == 原 bin）
  // 且 resolvedBin 不是绝对路径且 PATH 里也找不到对应 .cmd/.exe/.bat —— 提前 fail-fast，
  // 比让 libuv 抛 ENOENT/EPERM 更可定位。
  if (process.platform === "win32") {
    const bin = prepared.resolvedBin;
    const hasSep = bin.includes("\\") || bin.includes("/");
    const ext = path.extname(bin).toLowerCase();
    if (hasSep && !fileExists(bin)) {
      return `runner bin 路径不存在: ${bin}`;
    }
    if (hasSep && ext && !WINDOWS_EXECUTABLE_EXTENSIONS.includes(ext)) {
      return `runner bin 扩展名不在白名单 (${WINDOWS_EXECUTABLE_EXTENSIONS.join(", ")}): ${bin}`;
    }
  } else {
    // POSIX: 只有绝对路径才校验存在性，其它走 PATH。
    if (path.isAbsolute(prepared.resolvedBin) && !fileExists(prepared.resolvedBin)) {
      return `runner bin 路径不存在: ${prepared.resolvedBin}`;
    }
  }
  return null;
}

/** Wrap a spawn-time error (from child.on('error')) into a user-friendly message
 * that surfaces command / cwd / node version / platform without leaking env vars,
 * tokens, or secrets. Suitable to throw / reject directly to upstream handlers. */
export function formatSpawnError(opts: {
  prepared: PreparedSpawnCommand;
  cwd: string;
  err: unknown;
  source?: string;
}): string {
  const { prepared, cwd, err } = opts;
  const source = opts.source ?? "child_process.spawn";
  const e = err as Partial<{
    code: string;
    errno: number;
    syscall: string;
    path: string;
    message: string;
  }>;
  const code = e?.code ?? "(no code)";
  const advice = adviceForErrorCode(code);
  const lines = [
    `${source} 失败：code=${code} errno=${e?.errno ?? "?"} syscall=${e?.syscall ?? "?"}`,
    `  command       : ${prepared.command}`,
    `  resolvedBin   : ${prepared.resolvedBin}`,
    `  display       : ${prepared.display}`,
    `  cwd           : ${cwd}`,
    `  cmd-wrapped   : ${prepared.windowsCmdWrapped}`,
    `  node          : ${process.version}`,
    `  platform      : ${process.platform} (${process.arch})`,
    `  pid           : ${process.pid}`,
  ];
  if (prepared.diagnosticHints.length > 0) {
    lines.push("  prepare-hints :");
    for (const h of prepared.diagnosticHints) lines.push(`    - ${h}`);
  }
  if (advice.length > 0) {
    lines.push("  建议检查      :");
    for (const a of advice) lines.push(`    - ${a}`);
  }
  if (e?.message) lines.push(`  原始 message  : ${e.message}`);
  return lines.join("\n");
}

function adviceForErrorCode(code: string): string[] {
  switch (code) {
    case "EPERM":
      return [
        "Windows 上 EPERM 通常意味着 ERROR_ACCESS_DENIED：被 AppLocker / 防病毒 / SRP 拦截，或试图把目录当 exe 跑，或 cwd 无访问权限。",
        "确认 runner bin 路径是一个真正可执行的 .exe / .cmd / .bat，且当前用户对它有读+执行权限。",
        "确认 cwd 是一个**可访问**的实际目录，不是被防勒索保护策略锁住的目录（如 OneDrive 文件夹保护、Windows 受控文件夹）。",
        "如果 bin 是 .cmd/.bat，让 prepareSpawnCommand 包到 cmd.exe（已是当前行为）；不要绕过这一层。"
      ];
    case "EACCES":
      return [
        "目标文件存在但当前用户没有执行权限。检查 NTFS ACL（icacls bin-path）或杀软策略。"
      ];
    case "ENOENT":
      return [
        "bin 路径找不到或 cwd 不存在。先确认 prepareSpawnCommand.resolvedBin 是绝对路径，cwd 真实存在。",
        "Windows PATH 里没找到对应 .cmd/.exe/.bat/.com 也会落到 ENOENT。"
      ];
    case "EINVAL":
      return [
        "Node 20+ 因 CVE-2024-27980 直接 spawn .bat / .cmd 会抛 EINVAL；本仓库统一用 cmd.exe /d /s /c 包裹，不要绕过。"
      ];
    default:
      return [];
  }
}

function resolveWindowsCommand(bin: string): string {
  const hasPathSeparator = bin.includes("\\") || bin.includes("/");
  if (hasPathSeparator || path.isAbsolute(bin)) {
    return resolveWindowsPathCommand(bin);
  }

  const pathValue = process.env.PATH ?? process.env.Path ?? "";
  for (const directory of pathValue.split(path.delimiter)) {
    if (!directory) {
      continue;
    }

    const resolved = resolveWindowsPathCommand(path.join(directory, bin));
    if (resolved !== path.join(directory, bin) || fileExists(resolved)) {
      return resolved;
    }
  }

  return bin;
}

function resolveWindowsPathCommand(commandPath: string): string {
  const extension = path.extname(commandPath);
  if (extension) {
    return commandPath;
  }

  for (const executableExtension of WINDOWS_EXECUTABLE_EXTENSIONS) {
    const candidate = `${commandPath}${executableExtension}`;
    if (fileExists(candidate)) {
      return candidate;
    }
  }

  return commandPath;
}

function fileExists(filePath: string): boolean {
  try {
    return fs.statSync(filePath).isFile();
  } catch {
    return false;
  }
}

function formatDisplayCommand(command: string, args: string[]): string {
  return [command, ...args].map(formatDisplayArg).join(" ");
}

function formatDisplayArg(arg: string): string {
  if (!arg || /[\s"]/u.test(arg)) {
    return `"${arg.replaceAll('"', '\\"')}"`;
  }

  return arg;
}
