# -*- coding: utf-8 -*-
"""Unattended M3 extractor + bank dispatcher.

This is the top-level automation process for the M3 payment flow:

1. Run step4_extract.py to scan the latest contract payment rows.
2. step4_extract.py writes/updates runtime/bank_batches/<bank>.json.
3. Reuse monitor_bank_batches.py state logic to dispatch new/changed items
   to the matching bank project through bank_route.py.

The bank UI automation still lives in C:/Users/30112/Desktop/财务; this
script only orchestrates data extraction and routing.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import bank_route
import m3_monitor_control
import monitor_bank_batches as monitor


sys.dont_write_bytecode = True
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
DESKTOP = Path.home() / "Desktop"
STEP4 = ROOT / "step4_extract.py"
DEFAULT_GATEWAY_HOME = DESKTOP / "网关"
DEFAULT_FINANCE_HOME = DESKTOP / "财务"
RUNTIME_DIR = ROOT / "runtime"
SKIPPED_DIR = RUNTIME_DIR / "skipped"
CLEANUP_SCRIPT = DEFAULT_FINANCE_HOME / "公共" / "maintenance" / "cleanup_all.ps1"
CLEANUP_STATE_FILE = RUNTIME_DIR / "m3_cleanup_state.json"
# step4_extract.py 用 step1_open.USER_DATA_DIR = <项目根>/.browser_profile 启动
# Playwright 持久化上下文，Chrome 命令行会带 --user-data-dir=<该路径>。抽取超时/失败后
# 只清命令行包含本项目这个 profile 路径的浏览器进程，绝不误杀用户普通 Chrome。
M3_BROWSER_PROFILE = ROOT / ".browser_profile"


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def python_exe() -> str:
    configured = os.getenv("M3_AUTO_PYTHON") or os.getenv("BANK_ROUTE_PYTHON")
    if configured:
        return configured
    local_appdata = os.getenv("LOCALAPPDATA", "")
    py312 = Path(local_appdata) / "Programs" / "Python" / "Python312" / "python.exe"
    if py312.exists():
        return str(py312)
    return sys.executable


def truthy_env(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def gateway_script() -> Path | None:
    gateway_home = Path(os.getenv("CODEX_GATEWAY_HOME", str(DEFAULT_GATEWAY_HOME)))
    candidate = gateway_home / "scripts" / "run_and_report.py"
    return candidate if candidate.exists() else None


def report_script() -> Path | None:
    gateway_home = Path(os.getenv("CODEX_GATEWAY_HOME", str(DEFAULT_GATEWAY_HOME)))
    candidate = gateway_home / "scripts" / "report_to_codex.py"
    return candidate if candidate.exists() else None


def wrap_with_gateway(args: list[str], *, title: str, source: str, cwd: Path) -> list[str]:
    if not truthy_env("M3_AUTO_REPORT_ERRORS", True):
        return args
    script = gateway_script()
    if script is None:
        return args
    return [
        python_exe(),
        str(script),
        "--project-path",
        str(ROOT),
        "--title",
        title,
        "--source",
        source,
        "--cwd",
        str(cwd),
        "--",
        *args,
    ]


def base_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env.setdefault("FINANCE_HOME", str(DEFAULT_FINANCE_HOME))
    env.setdefault("CODEX_GATEWAY_HOME", str(DEFAULT_GATEWAY_HOME))
    env.setdefault("BANK_ROUTE_REPORT_ERRORS", "1")
    return env


def _iter_json_records(payload: object) -> list[dict]:
    if isinstance(payload, dict):
        for key in ("records", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def collect_recent_unknown_bank_skips(min_mtime: float) -> list[dict]:
    """Collect newly isolated unknown-bank records for finance-facing notification.

    Normal 社保/工资/公积金不可制单隔离 should stay quiet. This only reports
    records that need a business routing rule, e.g. 鑫锐 currently.
    """
    if not SKIPPED_DIR.exists():
        return []
    details: list[dict] = []
    for path in sorted(SKIPPED_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime):
        if path.name in {"latest.json", "skipped_records.json"}:
            continue
        try:
            if path.stat().st_mtime < min_mtime:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - notify as a readable dispatch issue
            details.append(
                {
                    "bank": "未识别银行",
                    "status": "failed",
                    "exit_code": 0,
                    "error": f"M3隔离记录读取失败，需技术同事查看：{type(exc).__name__}",
                    "form": {"业务参考号": path.stem},
                }
            )
            continue
        for record in _iter_json_records(payload):
            reason = str(record.get("M3跳过原因") or "").strip()
            if "未识别付款银行" not in reason:
                continue
            details.append(
                {
                    "bank": record.get("付款银行") or "未识别银行",
                    "status": "failed",
                    "exit_code": 0,
                    "error": reason or "未识别付款银行，已隔离，不写银行队列。",
                    "form": record,
                }
            )
    return details


def m3_extract_timeout_seconds() -> int:
    raw = (os.environ.get("M3_EXTRACT_TIMEOUT_SECONDS") or "900").strip()
    try:
        value = int(raw)
    except ValueError:
        print(f"[配置错误] M3_EXTRACT_TIMEOUT_SECONDS 不是整数: {raw!r}，使用默认 900 秒")
        return 900
    return value


def cleanup_interval_seconds() -> float:
    raw = (os.environ.get("M3_AUTO_CLEANUP_INTERVAL_HOURS") or "24").strip()
    try:
        hours = float(raw)
    except ValueError:
        print(f"[配置错误] M3_AUTO_CLEANUP_INTERVAL_HOURS 不是数字: {raw!r}，使用默认 24 小时")
        hours = 24.0
    return max(hours, 0.0) * 3600.0


def cleanup_timeout_seconds() -> int:
    raw = (os.environ.get("M3_AUTO_CLEANUP_TIMEOUT_SECONDS") or "900").strip()
    try:
        return max(int(raw), 60)
    except ValueError:
        return 900


def read_cleanup_state() -> dict:
    try:
        return json.loads(CLEANUP_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_cleanup_state(data: dict) -> None:
    try:
        CLEANUP_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CLEANUP_STATE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"[清理] 无法写入清理状态: {exc}")


def maybe_run_cleanup(skip: bool = False) -> None:
    """Periodically invoke cleanup_all.ps1 -Apply.

    Best-effort and side-band: any failure (script missing, non-zero exit,
    timeout, subprocess exception) is swallowed so the dispatch loop keeps
    running. Cadence persisted in runtime/m3_cleanup_state.json so frequent
    --once invocations or restarts don't trigger cleanup every time.
    """
    if skip:
        return
    if not truthy_env("M3_AUTO_CLEANUP_ENABLED", True):
        return
    if not CLEANUP_SCRIPT.exists():
        return
    state = read_cleanup_state()
    last_at = float(state.get("last_cleanup_at") or 0.0)
    now = time.time()
    interval = cleanup_interval_seconds()
    if last_at > 0 and (now - last_at) < interval:
        return

    timeout = cleanup_timeout_seconds()
    print(f"[{now_text()}] 触发周期清理 (cleanup_all.ps1 -Apply, 超时 {timeout}s)")
    exit_code: int | None = None
    try:
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(CLEANUP_SCRIPT),
                "-Apply",
            ],
            cwd=str(DESKTOP),
            timeout=timeout,
            check=False,
        )
        exit_code = int(proc.returncode)
        print(f"[{now_text()}] 周期清理结束，退出码 {exit_code}")
    except subprocess.TimeoutExpired:
        exit_code = 124
        print(f"[{now_text()}] 周期清理超时 {timeout}s，已放弃本轮。")
    except Exception as exc:  # noqa: BLE001
        exit_code = -1
        print(f"[{now_text()}] 周期清理异常，已忽略: {type(exc).__name__}: {exc}")
    write_cleanup_state({"last_cleanup_at": now, "last_exit_code": exit_code})


def terminate_process_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        os.kill(pid, 15)
    except OSError:
        pass


def scoped_browser_cleanup(*, reason: str) -> None:
    """抽取超时/失败后清理残留的 M3 浏览器空白窗。

    只结束命令行里包含本项目 `.browser_profile` 路径的 chrome/chromium/msedge 进程，
    绝不误杀用户普通 Chrome。仅在抽取失败/超时后调用（成功不清）。完全 best-effort：
    任何失败只记录日志，绝不抛出，也不改变调用方的原退出码。
    """
    if os.name != "nt":
        return
    profile = str(M3_BROWSER_PROFILE)
    print(f"[{now_text()}] {reason}：清理仅限 M3 .browser_profile 的残留浏览器进程: {profile}")
    # 用 IndexOf + OrdinalIgnoreCase 做大小写不敏感的字面匹配（避免 -like 通配符/
    # 路径中文转义问题，且不受盘符/路径大小写差异影响），并限定进程名为浏览器，
    # profile 路径经环境变量传入规避引号/中文路径问题。
    ps_script = (
        "$p = $env:M3_CLEANUP_PROFILE; "
        "$names = @('chrome.exe','chromium.exe','msedge.exe'); "
        "$killed = @(); "
        "Get-CimInstance Win32_Process -ErrorAction Stop | "
        "Where-Object { $names -contains $_.Name -and $_.CommandLine -and $_.CommandLine.IndexOf($p, [StringComparison]::OrdinalIgnoreCase) -ge 0 } | "
        "ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; $killed += $_.ProcessId } catch {} }; "
        "Write-Output ('M3CLEANUP_KILLED=' + ($killed -join ','))"
    )
    env = os.environ.copy()
    env["M3_CLEANUP_PROFILE"] = profile
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            cwd=str(ROOT),
            env=env,
            timeout=60,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        out = (proc.stdout or "").strip()
        if out:
            print(f"[{now_text()}] M3 浏览器清理结果: {out} (退出码 {proc.returncode})")
        else:
            print(f"[{now_text()}] M3 浏览器清理完成，退出码 {proc.returncode}")
    except subprocess.TimeoutExpired:
        print(f"[{now_text()}] M3 浏览器清理超时 60s，已忽略（不影响原退出码）。")
    except Exception as exc:  # noqa: BLE001 - cleanup 失败只记录，不掩盖原退出码
        print(f"[{now_text()}] M3 浏览器清理异常，已忽略: {type(exc).__name__}: {exc}")


def run_extract(*, no_report: bool = False) -> int:
    args = [python_exe(), "-u", str(STEP4)]
    if not no_report:
        args = wrap_with_gateway(
            args,
            title="M3付款数据抽取失败",
            source="M3 auto extractor",
            cwd=ROOT,
        )
    print(f"\n[{now_text()}] 开始扫描 M3 最新付款数据")
    timeout_seconds = m3_extract_timeout_seconds()
    proc = subprocess.Popen(args, cwd=str(ROOT), env=base_env())
    try:
        code = int(proc.wait(timeout=timeout_seconds if timeout_seconds > 0 else None))
    except subprocess.TimeoutExpired:
        print(
            f"[{now_text()}] M3 抽取超时 {timeout_seconds} 秒，"
            "已停止本轮抽取进程树；本轮不分发银行队列。"
        )
        terminate_process_tree(proc.pid)
        code = 124
    if code == 0:
        print(f"[{now_text()}] M3 抽取完成")
    else:
        print(f"[{now_text()}] M3 抽取失败，退出码 {code}")
        # 抽取超时(124)或非 0 退出后，现场可能残留使用 M3 .browser_profile 的空白窗；
        # 只清这些进程，不误杀用户普通 Chrome。成功路径不触发。
        scoped_browser_cleanup(reason=f"M3 抽取退出码 {code}")
    return code


def format_bank_details(details: list[dict]) -> str:
    if not details:
        return "银行执行：无新增/变更记录"
    lines = []
    for item in details:
        bank = item.get("bank") or "未知银行"
        ref = item.get("ref") or "未取到单据号"
        status = item.get("status") or "unknown"
        code = item.get("exit_code")
        lines.append(f"{bank} {ref}: {status}, code={code}")
    return "\n".join(lines)


def notify_final_result(
    *,
    status: str,
    title: str,
    summary: str,
    log_file: str = "",
    image_paths: list[str] | None = None,
) -> None:
    script = report_script()
    if script is None:
        print("[通知] 网关 report_to_codex.py 不存在，跳过飞书通知")
        return
    success = str(status or "").strip().lower() in {"success", "succeeded"}
    structured_error = ""
    if not success:
        structured_error = bank_route.finance_error_json_line(
            stage="M3自动调度",
            reason=summary or "M3 自动调度失败，未提供明确原因。",
            impact="本轮 M3 调度已停止，不会继续分发银行队列。",
            next_action="请技术同事查看本轮 M3 日志；如需人工处理，请先暂停 M3 监控，处理完后再恢复。",
            safe_state="未自动复核、授权或最终付款。",
        )
    cmd = [
        python_exe(),
        str(script),
        "--project-path",
        str(ROOT),
        "--title",
        title,
        "--status",
        status,
        "--error",
        "\n".join([item for item in (summary, structured_error) if item]),
        "--context",
        f"cwd={ROOT}",
        "--source",
        "M3 auto_extract_dispatch final",
    ]
    if log_file:
        cmd.extend(["--log-file", log_file])
    for image_path in image_paths or []:
        cmd.extend(["--image-path", image_path])
    proc = subprocess.run(cmd, cwd=str(ROOT), text=True, encoding="utf-8", errors="replace", check=False)
    print(f"[通知] 飞书最终通知退出码: {proc.returncode}")


def latest_extract_screenshot(*, min_mtime: float = 0.0) -> str:
    """Return the newest screenshot from the current extraction run, preferring the detail page."""
    if not truthy_env("M3_NOTIFY_SCREENSHOT", True):
        return ""

    for json_path in (
        RUNTIME_DIR / "bank_forms" / "latest_route.json",
        RUNTIME_DIR / "current" / "bank_form.json",
        RUNTIME_DIR / "current" / "latest.json",
    ):
        screenshot = screenshot_from_json(json_path, min_mtime=min_mtime)
        if screenshot:
            return screenshot

    screenshot_root = RUNTIME_DIR / "screenshots"
    if not screenshot_root.exists():
        return ""

    candidates = [
        path
        for path in screenshot_root.rglob("*.png")
        if path.is_file() and path.stat().st_mtime >= min_mtime - 1
    ]
    candidates.sort(key=lambda item: (screenshot_priority(item), item.stat().st_mtime), reverse=True)
    return str(candidates[0]) if candidates else ""


def screenshot_from_json(json_path: Path, *, min_mtime: float) -> str:
    if not json_path.exists():
        return ""
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""

    for key in ("M3抓取详情截图", "M3合同付款列表截图"):
        raw_path = str(payload.get(key) or "").strip()
        if not raw_path:
            continue
        path = Path(raw_path)
        try:
            if path.is_file() and path.stat().st_mtime >= min_mtime - 1:
                return str(path)
        except OSError:
            continue
    return ""


def screenshot_priority(path: Path) -> int:
    name = path.name
    if name.startswith("02_"):
        return 2
    if name.startswith("01_"):
        return 1
    return 0


def run_latest_form(*, dry_run: bool) -> dict:
    """Run the current latest routed form once as a verification fallback."""
    try:
        form, source = bank_route.load_latest_form()
        bank = bank_route.canonical_bank(form.get("付款银行") or form.get("付款银行原文"))
        normalized = bank_route.normalize_form(form, bank)
        print(f"[验证] 使用旧 latest 数据: source={source}")
        print(f"[验证] 付款银行={normalized.get('付款银行')} 业务参考号={normalized.get('业务参考号')}")
        code = bank_route.run_bank_forms(normalized["付款银行"], [normalized], dry_run=dry_run)
        return {
            "bank": normalized.get("付款银行") or bank,
            "ref": normalized.get("业务参考号") or normalized.get("label") or "未取到单据号",
            "status": "dry_run" if dry_run and code == 0 else ("succeeded" if code == 0 else "failed"),
            "exit_code": code,
            "error": bank_route.describe_exit_code(normalized.get("付款银行") or bank, code, dry_run=dry_run),
            "source": "latest",
            "form": normalized,
        }
    except (bank_route.RouteError, OSError, ValueError) as exc:
        print(f"[验证] latest 数据回退失败: {exc}")
        monitor.report_failure(
            title="M3 latest旧数据验证失败",
            error=str(exc),
            context="auto_extract_dispatch --process-latest-when-idle",
            source="M3 auto_extract_dispatch",
        )
        return {
            "bank": "未知银行",
            "ref": "latest",
            "status": "failed",
            "exit_code": 2,
            "error": str(exc),
            "source": "latest",
            "form": {},
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M3 自动抽取付款数据，并按付款银行分流到对应银行制单程序")
    parser.add_argument("--poll-seconds", type=float, default=60.0, help="每轮抽取间隔秒数，默认 60")
    parser.add_argument("--once", action="store_true", help="只抽取和分流一轮后退出")
    parser.add_argument("--no-extract", action="store_true", help="不访问 OA/M3，只处理现有银行队列")
    parser.add_argument("--dry-run-bank", action="store_true", help="只验证银行路由，不启动银行程序")
    parser.add_argument("--run-existing", action="store_true", help="启动时也处理队列里已有【全新】项目（跳过 seed，不再标记 seen）")
    parser.add_argument("--resume-existing", action="store_true", help="崩溃恢复：把上次未完成(running)的项重新处理；不重跑 succeeded/seen，也不影响 failed。也可用 M3_MONITOR_RESUME_EXISTING=1 开启")
    parser.add_argument("--retry-failed", action="store_true", help="同一份 JSON 记录为 failed 且 fingerprint 不变时仍允许重试")
    parser.add_argument("--process-latest-when-idle", action="store_true", help="没有新增/变更队列时，回退使用 latest_route 旧数据跑一笔；主要用于真实流程验证")
    parser.add_argument("--force-lock", action="store_true", help="清理旧监控锁后启动")
    parser.add_argument("--quiet-idle", action="store_true", help="没有新增/变更时不输出空闲日志")
    parser.add_argument("--no-report", action="store_true", help="本进程内不调用网关；仅保留退出码")
    parser.add_argument("--notify-final", action="store_true", help="每个有效轮次结束后，通过 Codex CLI 飞书发送精简成功/失败摘要")
    parser.add_argument("--final-log-file", default="", help="最终通知附带的日志文件路径")
    parser.add_argument("--skip-cleanup", action="store_true", help="本次进程内跳过周期清理（不影响下次启动的时间窗口）")
    args = parser.parse_args(argv)

    if not STEP4.exists():
        print(f"[终止] M3 抽取脚本不存在: {STEP4}")
        return 2

    resume_existing = args.resume_existing or monitor.truthy_env("M3_MONITOR_RESUME_EXISTING", False)

    lock_fd = monitor.acquire_lock(force=args.force_lock)
    try:
        state = monitor.read_state()
        if not args.run_existing:
            seeded = monitor.seed_existing_items(state)
            if seeded:
                print(f"[监控] 已把现有 {seeded} 条队列记录标记为 seen；之后新增/变更才会自动运行。")
                state = monitor.read_state()
        if resume_existing:
            recovered = monitor.recover_interrupted_items(state)
            if recovered:
                print(f"[监控] 崩溃恢复：{recovered} 条上次未完成(running→interrupted)项将重新处理；succeeded/seen/failed 不受影响。")
                state = monitor.read_state()
            else:
                print("[监控] 崩溃恢复：未发现上次未完成(running)项，无需恢复。")

        print(
            f"[监控] 启动 M3 自动调度: root={ROOT} finance={os.getenv('FINANCE_HOME', str(DEFAULT_FINANCE_HOME))} "
            f"dry_run_bank={args.dry_run_bank} once={args.once} poll={args.poll_seconds}s"
        )

        fallback_used = False
        latest_code = 0
        latest_result: dict | None = None
        while True:
            # 人工兜底（pause 检查 #1：每轮开始前）。
            # 暂停期间：本轮不抽取、不分发、不启动银行；--once 模式直接退出 0 并说明已暂停。
            if m3_monitor_control.is_paused():
                reason = m3_monitor_control.pause_reason() or "(未填写)"
                print(
                    f"[{now_text()}] 监控已暂停：原因 {reason}；本轮不抽取、不分发、不启动银行。"
                )
                if args.once:
                    print("[监控] --once 模式检测到 paused，直接退出 0；恢复请用 m3_monitor_control.py resume。")
                    return 0
                time.sleep(max(args.poll_seconds, 5.0))
                continue

            extract_code = 0
            extract_started_at = time.time()
            extract_screenshot = ""
            bank_details: list[dict] = []
            if not args.no_extract:
                extract_code = run_extract(no_report=args.no_report)
                extract_screenshot = latest_extract_screenshot(min_mtime=extract_started_at)

            state = monitor.read_state()
            if extract_code != 0 and not args.no_extract:
                print(f"[{now_text()}] M3 抽取失败，本轮跳过银行队列分发，避免使用旧数据误制单。")
                bank_details = []
            elif m3_monitor_control.is_paused():
                # 人工兜底（pause 检查 #2：M3 抽取完成后、银行队列分发前再确认一次）。
                # 暂停期间不进入银行分发，也不标记 failed，等恢复后下一轮继续。
                reason = m3_monitor_control.pause_reason() or "(未填写)"
                print(
                    f"[{now_text()}] 监控已暂停：原因 {reason}；本轮抽取完成但跳过银行分发，"
                    "不标记失败、不启动银行。"
                )
                bank_details = []
            else:
                bank_details = monitor.process_once_details(state, dry_run=args.dry_run_bank, retry_failed=args.retry_failed)
            ran = len(bank_details)
            skipped_details = (
                collect_recent_unknown_bank_skips(extract_started_at)
                if extract_code == 0 and not args.no_extract
                else []
            )
            if skipped_details:
                print(f"[{now_text()}] 本轮发现 {len(skipped_details)} 条未识别付款银行隔离记录，将发送财务提示。")
            if ran == 0 and not args.quiet_idle:
                print(f"[{now_text()}] 没有新增/变更的银行队列项。")
            if ran == 0 and extract_code == 0 and args.process_latest_when_idle and not fallback_used:
                print(f"[{now_text()}] 没有新增/变更，回退使用 latest_route 旧数据验证一笔。")
                latest_result = run_latest_form(dry_run=args.dry_run_bank)
                latest_code = int(latest_result.get("exit_code") or 0)
                fallback_used = True
            elif ran == 0 and extract_code != 0 and args.process_latest_when_idle and not fallback_used:
                print(f"[{now_text()}] M3 抽取失败，本轮禁止 latest 旧数据回退。")

            summary_details = [*bank_details, *skipped_details]
            if latest_result is not None:
                summary_details.append(latest_result)
            failed_banks = [item for item in summary_details if item.get("status") == "failed" or item.get("exit_code")]
            final_failed = extract_code != 0 or latest_code != 0 or bool(failed_banks)
            should_notify = args.notify_final and (
                args.once or extract_code != 0 or ran > 0 or bool(skipped_details) or latest_code != 0
            )
            if should_notify:
                final_status = "failed" if final_failed else "success"
                summary = "\n".join(
                    [
                        f"抽取退出码: {extract_code}",
                        format_bank_details(summary_details),
                        f"latest回退: 已使用, code={latest_code}" if fallback_used else "latest回退: 未使用",
                    ]
                )
                notification = bank_route.notify_m3_status_report(
                    title="M3自动调度结果",
                    status=final_status,
                    details=summary_details,
                    summary=summary,
                    context="M3/OA 合同付款自动调度跑完后统一通知；包含 M3 信息图、JSON 关键字段、成功/失败状态和问题。",
                    source="M3 auto_extract_dispatch final",
                    dry_run=args.dry_run_bank,
                    log_file=args.final_log_file,
                    image_paths=[extract_screenshot] if extract_screenshot else [],
                )
                print(f"[通知] M3最终通知结果: {notification}")

            maybe_run_cleanup(skip=args.skip_cleanup)

            if args.once:
                if extract_code != 0:
                    return extract_code
                if latest_code != 0:
                    return latest_code
                if failed_banks:
                    return int(failed_banks[0].get("exit_code") or 1)
                return 0

            time.sleep(max(args.poll_seconds, 5.0))
    except KeyboardInterrupt:
        print("\n[监控] 已停止。")
        return 130
    finally:
        monitor.release_lock(lock_fd)


if __name__ == "__main__":
    raise SystemExit(main())
