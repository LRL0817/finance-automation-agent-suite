import argparse
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a command and report non-zero exits to the Feishu Codex gateway."
    )
    parser.add_argument("--project-path", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--source", default="")
    parser.add_argument("--context", default="")
    parser.add_argument("--cwd", default="")
    parser.add_argument("--log-file", default="")
    parser.add_argument("--image-path", action="append", default=[], help="Attach an image file path when the command fails. Can be repeated.")
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to run. Use -- before the command.",
    )
    args = parser.parse_args()

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("missing command after --", file=sys.stderr)
        return 2

    cwd = Path(args.cwd).resolve() if args.cwd else Path(args.project_path).resolve()
    log_file = Path(args.log_file) if args.log_file else default_log_file(args.title)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    return_code = run_command(command, cwd, log_file)
    if return_code == 0:
        return 0

    report_failure(
        project_path=args.project_path,
        title=args.title,
        error=f"Command exited with code {return_code}: {format_command(command)}",
        context=args.context or f"cwd={cwd}",
        log_file=log_file,
        image_paths=args.image_path,
        source=args.source or Path(command[0]).name,
    )
    return return_code


def run_command(command: list[str], cwd: Path, log_file: Path) -> int:
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    with log_file.open("a", encoding="utf-8", buffering=1) as log:
        log.write(f"\n========== run_and_report {started} ==========\n")
        log.write(f"cwd: {cwd}\n")
        log.write(f"command: {format_command(command)}\n\n")
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except Exception as exc:
            message = f"failed to start command: {type(exc).__name__}: {exc}"
            print(message, file=sys.stderr)
            log.write(message + "\n")
            return 127

        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="")
            log.write(line)

        return_code = proc.wait()
        log.write(f"\nexit_code: {return_code}\n")
        return return_code


def report_failure(
    *,
    project_path: str,
    title: str,
    error: str,
    context: str,
    log_file: Path,
    image_paths: list[str],
    source: str,
) -> None:
    script = Path(__file__).resolve().with_name("report_to_codex.py")
    cmd = [
        sys.executable,
        str(script),
        "--project-path",
        project_path,
        "--title",
        title,
        "--error",
        error,
        "--context",
        context,
        "--log-file",
        str(log_file),
        "--source",
        source,
    ]
    for image_path in image_paths:
        cmd.extend(["--image-path", image_path])
    print("\n[run_and_report] command failed; reporting to Codex through Feishu gateway...")
    proc = subprocess.run(cmd, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        print(f"[run_and_report] report failed with code {proc.returncode}", file=sys.stderr)


def default_log_file(title: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in title)[:48]
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return Path(__file__).resolve().parents[1] / "data" / "reports" / f"{stamp}_{safe}.log"


def format_command(command: list[str]) -> str:
    return " ".join(quote_arg(part) for part in command)


def quote_arg(value: str) -> str:
    if not value:
        return '""'
    if any(ch.isspace() for ch in value) or '"' in value:
        return '"' + value.replace('"', '\\"') + '"'
    return value


if __name__ == "__main__":
    raise SystemExit(main())
