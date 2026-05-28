import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Report an automation failure to the Feishu Codex gateway.")
    parser.add_argument("--project-path", required=True)
    parser.add_argument("--title", default="自动化制单失败")
    parser.add_argument("--status", default="failed")
    parser.add_argument("--error", default="")
    parser.add_argument("--context", default="")
    parser.add_argument("--log", default="")
    parser.add_argument("--log-file", default="")
    parser.add_argument("--image-path", action="append", default=[], help="Attach an image file path to the Feishu/Codex report. Can be repeated.")
    parser.add_argument("--source", default="")
    parser.add_argument("--gateway-url", default=os.getenv("GATEWAY_REPORT_URL", "http://127.0.0.1:3000/agent/report"))
    parser.add_argument("--env-file", default=str(Path(__file__).resolve().parents[1] / ".env"))
    parser.add_argument(
        "--ignore-disabled-flag",
        action="store_true",
        help="Send this explicit report even when data/reporting_disabled.flag or REPORT_TO_CODEX_DISABLED would normally suppress reports.",
    )
    args = parser.parse_args()

    if not args.ignore_disabled_flag:
        disabled = reporting_disabled_reason(Path(args.env_file))
        if disabled:
            print(f"report skipped: {disabled}")
            return 0

    env = load_env(Path(args.env_file))
    token = os.environ.get("REPORT_TOKEN") or env.get("REPORT_TOKEN")
    if not token:
        print("REPORT_TOKEN is missing", file=sys.stderr)
        return 2

    log_text = args.log
    if args.log_file and not log_text:
        log_text = read_tail(Path(args.log_file), max_chars=20000)
    log_text = redact_secrets(log_text)

    image_paths = list(args.image_path)
    fallback_image = find_current_m3_screenshot(args, log_text)
    if fallback_image and fallback_image not in image_paths:
        image_paths.append(fallback_image)
        print(f"attached current M3 screenshot: {fallback_image}")

    payload = {
        "project_path": args.project_path,
        "title": args.title,
        "status": args.status,
        "error": redact_secrets(args.error),
        "context": redact_secrets(args.context),
        "log": log_text,
        "log_file": args.log_file,
        "image_paths": image_paths,
        "source": args.source,
    }

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        args.gateway_url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            print(response.read().decode("utf-8"))
            return 0
    except Exception as exc:
        print(f"report failed: {exc}", file=sys.stderr)
        return 1


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def reporting_disabled_reason(env_file: Path) -> str:
    configured = os.getenv("REPORT_TO_CODEX_DISABLED", "").strip()
    if configured and configured.lower() not in {"0", "false", "no", "off"}:
        return "REPORT_TO_CODEX_DISABLED is enabled"

    gateway_root = env_file.resolve().parent
    flag = gateway_root / "data" / "reporting_disabled.flag"
    if flag.exists():
        try:
            reason = flag.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            reason = ""
        return reason or f"{flag} exists"

    return ""


def read_tail(path: Path, max_chars: int) -> str:
    if not path.exists():
        return f"log file not found: {path}"

    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def find_current_m3_screenshot(args: argparse.Namespace, log_text: str) -> str:
    if args.image_path:
        return ""
    if not truthy_env("M3_NOTIFY_SCREENSHOT", True):
        return ""
    if not is_m3_report(args, log_text):
        return ""

    for path in screenshot_paths_from_log(log_text):
        if path.is_file():
            return str(path)

    return ""


def is_m3_report(args: argparse.Namespace, log_text: str) -> bool:
    text = "\n".join(
        [
            str(args.project_path or ""),
            str(args.title or ""),
            str(args.source or ""),
            str(args.context or ""),
            str(args.error or ""),
            log_text[:4000],
        ]
    ).lower()
    return "m3" in text or "直供合同付款" in text or "合同付款" in text


def resolve_m3_root(args: argparse.Namespace) -> Path:
    configured = os.getenv("M3_ROOT") or os.getenv("M3_PAYMENT_ROOT") or os.getenv("M3_EXTRACT_ROOT")
    if configured:
        return Path(configured)

    project_path = Path(args.project_path)
    if project_path.name == "M3直供合同付款数据获取":
        return project_path

    return Path.home() / "Desktop" / "M3直供合同付款数据获取"


def screenshot_paths_from_log(log_text: str) -> list[Path]:
    if not log_text:
        return []
    matches = re.findall(r"([A-Za-z]:\\[^\r\n\"<>|]+?\.png)", log_text)
    paths: list[Path] = []
    for raw_path in matches:
        text = raw_path.strip().rstrip("，,。.；;)")
        if not text:
            continue
        path = Path(text)
        name = path.name
        if (
            "M3" not in str(path)
            and "合同付款" not in str(path)
            and not name.startswith(("00_OA_M3", "01_", "02_", "smoke_"))
        ):
            continue
        if path not in paths:
            paths.append(path)
    return paths


def truthy_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def redact_secrets(text: str) -> str:
    if not text:
        return text

    patterns = (
        r"(?i)\b((?:LOGIN|CERT|PASSWORD|PASS|PWD|PIN|SECRET|TOKEN|KEY|APP_SECRET|USHIELD)[A-Z0-9_ -]*\s*[:=]\s*)([^\s,;]+)",
        r"(?i)\b(BOC_LOGIN_PASSWORD|BOC_USHIELD_PIN|LOGIN_PWD|CERT_PWD)\s*[:=]\s*([^\s,;]+)",
    )
    redacted = text
    for pattern in patterns:
        redacted = re.sub(pattern, r"\1<redacted>", redacted)
    return redacted


if __name__ == "__main__":
    raise SystemExit(main())

