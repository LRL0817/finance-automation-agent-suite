// Spawn smoke for gateway diagnostic. Reuses dist/runners/spawn-command.js.
// Runs 3 cases:
//   1. cmd /c echo gateway-smoke
//   2. python -c "print('gateway-smoke')"
//   3. nonexistent command
// Prints what prepareSpawnCommand returns and what child_process.spawn does.
// Does NOT touch Feishu, banks, M3, or any production code path.

import { spawn } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const __filename = fileURLToPath(import.meta.url);
const here = path.dirname(__filename);
const distSpawnCommandPath = path.resolve(here, "..", "dist", "runners", "spawn-command.js");
const distSpawnCommandUrl = pathToFileURL(distSpawnCommandPath).href;

const {
  prepareSpawnCommand,
  preflightSpawnEnvironment,
  formatSpawnError
} = await import(distSpawnCommandUrl);

async function runCase(label, bin, args, cwd) {
  const effectiveCwd = cwd ?? process.cwd();
  console.log("=".repeat(60));
  console.log(`[case] ${label}`);
  console.log(`  input bin   : ${JSON.stringify(bin)}`);
  console.log(`  input args  : ${JSON.stringify(args)}`);
  const prepared = prepareSpawnCommand(bin, args);
  console.log(`  prepared.command : ${JSON.stringify(prepared.command)}`);
  console.log(`  prepared.args    : ${JSON.stringify(prepared.args)}`);
  console.log(`  prepared.display : ${prepared.display}`);
  console.log(`  prepared.resolvedBin : ${JSON.stringify(prepared.resolvedBin)}`);
  console.log(`  prepared.windowsCmdWrapped : ${prepared.windowsCmdWrapped}`);
  console.log(`  prepared.diagnosticHints : ${JSON.stringify(prepared.diagnosticHints)}`);
  console.log(`  cwd              : ${effectiveCwd}`);
  const preflightReason = preflightSpawnEnvironment({ prepared, cwd: effectiveCwd });
  console.log(`  preflight        : ${preflightReason ?? "OK"}`);
  if (preflightReason) {
    console.log("  formattedError (preflight):");
    console.log(
      formatSpawnError({
        prepared,
        cwd: effectiveCwd,
        err: { code: "PRECHECK", message: preflightReason },
        source: "smoke preflight"
      })
        .split("\n")
        .map((l) => "    " + l)
        .join("\n")
    );
    return { code: null, signal: null, spawnError: null, preflightReason };
  }

  return await new Promise((resolve) => {
    const stdout = [];
    const stderr = [];
    let spawnError = null;

    const child = spawn(prepared.command, prepared.args, {
      cwd: effectiveCwd,
      env: process.env,
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"]
    });

    child.stdout.on("data", (chunk) => stdout.push(chunk.toString()));
    child.stderr.on("data", (chunk) => stderr.push(chunk.toString()));

    child.on("error", (err) => {
      spawnError = err;
    });

    child.on("close", (code, signal) => {
      console.log(`  exit code    : ${code}`);
      console.log(`  signal       : ${signal}`);
      if (spawnError) {
        console.log(`  spawn error  : name=${spawnError.name} code=${spawnError.code} errno=${spawnError.errno} syscall=${spawnError.syscall} path=${spawnError.path}`);
        console.log(`  spawn error.message: ${spawnError.message}`);
        console.log("  formattedError (spawn):");
        console.log(
          formatSpawnError({
            prepared,
            cwd: effectiveCwd,
            err: spawnError,
            source: "smoke spawn"
          })
            .split("\n")
            .map((l) => "    " + l)
            .join("\n")
        );
      } else {
        console.log("  spawn error  : (none)");
      }
      const stdoutText = stdout.join("").trim();
      const stderrText = stderr.join("").trim();
      console.log(`  stdout       : ${JSON.stringify(stdoutText)}`);
      console.log(`  stderr       : ${JSON.stringify(stderrText)}`);
      resolve({ code, signal, spawnError });
    });
  });
}

console.log(`node      : ${process.version}`);
console.log(`platform  : ${process.platform}`);
console.log(`arch      : ${process.arch}`);
console.log(`pid       : ${process.pid}`);
console.log(`cwd       : ${process.cwd()}`);
console.log("");

await runCase("cmd /c echo gateway-smoke", "cmd", ["/c", "echo", "gateway-smoke"]);
await runCase('python -c "print(\'gateway-smoke\')"', "python", ["-c", "print('gateway-smoke')"]);
await runCase("codex --version (real runner bin)", "codex", ["--version"]);
await runCase("nonexistent command", "definitely-not-a-real-binary-9999", ["--help"]);
await runCase(
  "bogus cwd (preflight should fail-fast)",
  "cmd",
  ["/c", "echo", "should-not-print"],
  "C:\\Users\\30112\\Desktop\\__no_such_dir_for_smoke__"
);

console.log("=".repeat(60));
console.log("[done] smoke complete");
