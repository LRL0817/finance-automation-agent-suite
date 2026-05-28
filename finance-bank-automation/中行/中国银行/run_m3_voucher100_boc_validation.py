import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent
DEBUG_ROOT = ROOT / "debug_runs"
DEFAULT_INPUT = DEBUG_ROOT / "boc_m3_voucher100_runlist_dedup.json"
DEFAULT_TIMEOUT_SECONDS = int(os.environ.get("BOC_M3_JOB_TIMEOUT_SECONDS", "600"))
DEFAULT_MAX_ATTEMPTS = int(os.environ.get("BOC_M3_MAX_ATTEMPTS", "2"))
DEFAULT_BETWEEN_SECONDS = int(os.environ.get("BOC_M3_BETWEEN_JOBS_SECONDS", "10"))


def _mask_account(account: str) -> str:
    digits = re.sub(r"\D+", "", account or "")
    if len(digits) <= 8:
        return digits
    return f"{digits[:4]}...{digits[-4:]}"


def _latest_run_dir(after_ts: float | None = None) -> Path | None:
    if not DEBUG_ROOT.is_dir():
        return None
    candidates = []
    for path in DEBUG_ROOT.iterdir():
        if not path.is_dir():
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if after_ts is not None and mtime + 1 < after_ts:
            continue
        candidates.append((mtime, path))
    if not candidates:
        return None
    return max(candidates)[1]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _tail_interesting(log_text: str, max_lines: int = 18) -> list[str]:
    markers = (
        "[完成]",
        "[失败]",
        "[终止]",
        "[错误]",
        "[警告]",
        "转账表单",
        "下拉候选",
        "开户行",
        "行号",
        "未提交",
        "会话",
        "登录页",
    )
    lines = [line for line in log_text.splitlines() if any(m in line for m in markers)]
    return lines[-max_lines:]


def _classify(returncode: int | None, log_text: str, timed_out: bool) -> tuple[bool, bool, str]:
    if timed_out:
        return False, True, "timeout"
    if "[完成] 转账表单必填项已填写（未提交）" in log_text:
        return returncode == 0, False, "filled_not_submitted"
    retry_markers = (
        "会话已失效",
        "其它浏览器登录",
        "其他浏览器登录",
        "about:blank",
        "ERR_TIMED_OUT",
        "TimeoutError",
        "等待中行登录页",
        "证书选择框",
        "U盾 PIN",
        "转账表单预填失败_未提交 url=https://netc2.igtb.boc.cn/#/login-page",
        "转账汇款表单填写不完整 url=https://netc2.igtb.boc.cn/#/login-page",
    )
    if any(marker in log_text for marker in retry_markers):
        return False, True, "transient_or_session"
    if "[失败]" in log_text or "[终止]" in log_text or returncode not in (0, None):
        return False, False, "business_or_form_failure"
    return False, False, "unknown"


def _run_one(index: int, total: int, item: dict, max_attempts: int, timeout_seconds: int) -> dict:
    env = os.environ.copy()
    env.update(
        {
            "BOC_ENABLE_TRANSFER_FILL": "1",
            "BOC_ENABLE_ORDER_SUBMIT": "0",
            "BOC_CLOSE_ON_FINISH": "1",
            # 默认不自动输入 U盾 PIN：必须由操作人显式设置 BOC_AUTO_USHIELD_PIN=1 开启。
            "BOC_AUTO_USHIELD_PIN": env.get("BOC_AUTO_USHIELD_PIN", "0"),
            # 不硬编码端口：遵循环境变量/config，默认沿用中行文档端口 10。
            "BOC_USBHUB_PORT": env.get("BOC_USBHUB_PORT", "10"),
            "BOC_USBHUB_POWER_ON_START": "1",
            "BOC_USBHUB_ALL_OFF_ON_FINISH": "1",
            "BOC_KEEP_DEBUG_RUNS": env.get("BOC_KEEP_DEBUG_RUNS", "160"),
            "BOC_PAYEE_NAME": item.get("name", ""),
            "BOC_PAYEE_ACCOUNT": item.get("account_digits") or item.get("account", ""),
            "BOC_PAYEE_BANK": item.get("bank", ""),
            "BOC_PAYEE_BANK_CODE": "",
            "BOC_PAYEE_TYPE": item.get("payee_type") or "单位",
            "BOC_PAYMENT_AMOUNT": "0.01",
        }
    )
    env.pop("BOC_ENABLE_ORDER_SUBMIT", None)
    env["BOC_ENABLE_ORDER_SUBMIT"] = "0"

    attempts = []
    final = None
    for attempt in range(1, max(1, max_attempts) + 1):
        before = time.time()
        print(
            f"\n========== M3-BOC {index:03d}/{total:03d} attempt={attempt} seq={item.get('seq')} ==========",
            flush=True,
        )
        print(
            f"{item.get('ref')} | {item.get('name')} | {_mask_account(env['BOC_PAYEE_ACCOUNT'])} | {item.get('bank')} | amount=0.01 | usb={env['BOC_USBHUB_PORT']} | submit=off",
            flush=True,
        )
        timed_out = False
        returncode = None
        try:
            completed = subprocess.run(
                [sys.executable, "open_boc.py"],
                cwd=str(ROOT),
                env=env,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
            returncode = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True

        run_dir = _latest_run_dir(before)
        log_text = _read_text(run_dir / "run.log") if run_dir else ""
        ok, retryable, reason = _classify(returncode, log_text, timed_out)
        screenshots = []
        page_screenshots = []
        if run_dir:
            screenshots = [str(p) for p in sorted((run_dir / "screenshots").glob("*.png"))[-6:]]
            page_screenshots = [str(p) for p in sorted((run_dir / "page_screenshots").glob("*.png"))[-6:]]
        attempt_result = {
            "attempt": attempt,
            "ok": ok,
            "retryable": retryable,
            "reason": reason,
            "returncode": returncode,
            "timed_out": timed_out,
            "run_dir": str(run_dir) if run_dir else "",
            "run_log": str(run_dir / "run.log") if run_dir else "",
            "screenshots": screenshots,
            "page_screenshots": page_screenshots,
            "interesting_log_tail": _tail_interesting(log_text),
        }
        attempts.append(attempt_result)
        print(
            f"[M3-BOC结果] {index:03d}/{total:03d} {'OK' if ok else 'FAIL'} reason={reason} retryable={retryable} run={attempt_result['run_dir']}",
            flush=True,
        )
        if ok or not retryable or attempt >= max_attempts:
            final = attempt_result
            break
        time.sleep(20)

    return {
        "index": index,
        "item": item,
        "ok": bool(final and final["ok"]),
        "final_reason": final["reason"] if final else "unknown",
        "attempts": attempts,
    }


def _select_items(items: list[dict], start: int, limit: int | None, indices: list[int]) -> list[tuple[int, dict]]:
    numbered = list(enumerate(items, 1))
    if indices:
        wanted = set(indices)
        numbered = [(idx, item) for idx, item in numbered if idx in wanted]
    else:
        numbered = [(idx, item) for idx, item in numbered if idx >= start]
        if limit is not None:
            numbered = numbered[:limit]
    return numbered


def main() -> int:
    parser = argparse.ArgumentParser(description="Run BOC fill-only validation for M3 voucher100 records.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--index", type=int, action="append", default=[])
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--between-seconds", type=int, default=DEFAULT_BETWEEN_SECONDS)
    args = parser.parse_args()

    input_path = Path(args.input)
    data = json.loads(input_path.read_text(encoding="utf-8"))
    items = data.get("items", data if isinstance(data, list) else [])
    selected = _select_items(items, args.start, args.limit, args.index)
    if not selected:
        print("[终止] 没有可验证记录", flush=True)
        return 2

    started = time.strftime("%Y%m%d_%H%M%S")
    summary_path = DEBUG_ROOT / f"boc_m3_voucher100_validation_{started}.json"
    summary = {
        "input": str(input_path),
        "started_at": started,
        "submit": "off",
        "amount": "0.01",
        "usb_port": os.environ.get("BOC_USBHUB_PORT", "10"),
        "total_selected": len(selected),
        "results": [],
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[M3-BOC] summary={summary_path}", flush=True)

    for pos, (item_index, item) in enumerate(selected, 1):
        result = _run_one(item_index, len(items), item, args.max_attempts, args.timeout_seconds)
        result["selected_pos"] = pos
        summary["results"].append(result)
        summary["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        summary["ok_count"] = sum(1 for r in summary["results"] if r.get("ok"))
        summary["fail_count"] = sum(1 for r in summary["results"] if not r.get("ok"))
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(max(0, args.between_seconds))

    ok_all = all(r.get("ok") for r in summary["results"])
    print(
        f"[M3-BOC汇总] ok={summary.get('ok_count', 0)} fail={summary.get('fail_count', 0)} summary={summary_path}",
        flush=True,
    )
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
