# -*- coding: utf-8 -*-
"""Monitor per-bank M3 queues and run the matching bank workflow on new items."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import bank_route
import m3_monitor_control
from bank_route import RouteError


sys.dont_write_bytecode = True
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
DEFAULT_GATEWAY_HOME = Path.home() / "Desktop" / "网关"
MONITOR_DIR = ROOT / "runtime" / "monitor"
STATE_PATH = MONITOR_DIR / "auto_bank_monitor_state.json"
LOCK_PATH = MONITOR_DIR / "auto_bank_monitor.lock"


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def truthy_env(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def report_failure(*, title: str, error: str, context: str, source: str) -> None:
    if not truthy_env("M3_MONITOR_REPORT_ERRORS", True):
        return
    gateway_home = Path(os.getenv("CODEX_GATEWAY_HOME", str(DEFAULT_GATEWAY_HOME)))
    script = gateway_home / "scripts" / "report_to_codex.py"
    if not script.exists():
        print(f"[监控] 网关上报脚本不存在，跳过飞书上报: {script}")
        return
    structured_error = bank_route.finance_error_json_line(
        stage="M3银行队列监控",
        reason=error or "M3 银行队列监控失败，未提供明确原因。",
        impact="该队列项已停止处理，不会继续启动下一步银行动作。",
        next_action="请先让 Codex CLI 暂停 M3 监控；财务或技术同事处理后再恢复监控。",
        safe_state="未自动复核、授权或最终付款。",
    )
    cmd = [
        bank_route.python_exe(),
        str(script),
        "--project-path",
        str(ROOT),
        "--title",
        title,
        "--error",
        "\n".join([item for item in (error, structured_error) if item]),
        "--context",
        context,
        "--source",
        source,
    ]
    proc = subprocess.run(cmd, cwd=str(ROOT), text=True, encoding="utf-8", errors="replace", check=False)
    if proc.returncode != 0:
        print(f"[监控] 飞书上报失败，退出码 {proc.returncode}")


def read_state() -> dict:
    if not STATE_PATH.exists():
        return {"version": 1, "items": {}}
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        archive = STATE_PATH.with_suffix(f".bad_{time.strftime('%Y%m%d_%H%M%S')}.json")
        STATE_PATH.replace(archive)
        print(f"[监控] 状态文件损坏，已归档: {archive}")
        return {"version": 1, "items": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "items": {}}
    payload.setdefault("version", 1)
    payload.setdefault("items", {})
    if not isinstance(payload["items"], dict):
        payload["items"] = {}
    return payload


def write_state(state: dict) -> None:
    MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def item_ref(item: dict) -> str:
    return bank_route.first_value(item, "业务参考号", "label", "单据编号", "合同编号")


def item_fingerprint(item: dict) -> str:
    blob = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def item_key(bank: str, item: dict) -> str:
    ref = item_ref(item)
    if ref:
        return f"{bank}|{ref}"
    return f"{bank}|sha256:{item_fingerprint(item)}"


def item_int_value(item: dict, *keys: str, default: int = 999999) -> int:
    for key in keys:
        value = item.get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return default


def item_schedule_key(bank: str, item: dict) -> tuple:
    """Global cross-bank order: M3 scan batch + M3 list sequence, then stable fallback."""
    batch = str(item.get("M3调度批次") or "").strip()
    sequence = item_int_value(item, "M3调度序号", "M3列表序号")
    list_position = item_int_value(item, "M3列表序号")
    bank_index = list(bank_route.BANK_QUEUE_FILES).index(bank) if bank in bank_route.BANK_QUEUE_FILES else 999
    missing_schedule = 0 if batch else 1
    return (missing_schedule, batch, sequence, list_position, bank_index, item_ref(item), item_fingerprint(item))


def iter_queue_items() -> list[tuple[str, dict]]:
    result: list[tuple[str, dict]] = []
    for bank in bank_route.BANK_QUEUE_FILES:
        items = bank_route.load_bank_queue(bank)
        for item in items:
            result.append((bank, item))
    result.sort(key=lambda pair: item_schedule_key(pair[0], pair[1]))
    return result


def seed_existing_items(state: dict) -> int:
    count = 0
    for bank, item in iter_queue_items():
        key = item_key(bank, item)
        fingerprint = item_fingerprint(item)
        if key in state["items"]:
            continue
        state["items"][key] = {
            "bank": bank,
            "ref": item_ref(item),
            "fingerprint": fingerprint,
            "status": "seen",
            "updated_at": now_text(),
            "note": "monitor started with existing queue item",
        }
        count += 1
    if count:
        write_state(state)
    return count


def recover_interrupted_items(state: dict) -> int:
    """崩溃恢复：把上一进程死亡时仍停在 running 的项标记为 interrupted。

    监控为单实例（有锁保证），因此持久化在 state 里的 running 一定是上一个
    进程在 mark_state(running) 之后、写最终状态之前被杀/崩溃留下的「未完成
    项」，应视为 interrupted 并允许重新处理。只动 running，不碰
    succeeded/seen/failed，因此不会整批重跑历史成功/已见数据。
    """
    count = 0
    for key, record in list(state.get("items", {}).items()):
        if isinstance(record, dict) and record.get("status") == "running":
            record["status"] = "interrupted"
            record["updated_at"] = now_text()
            record["note"] = "previous monitor process died while running; will reprocess"
            count += 1
    if count:
        write_state(state)
    return count


def should_run(record: dict | None, fingerprint: str, retry_failed: bool) -> bool:
    if record is None:
        return True
    if record.get("fingerprint") != fingerprint:
        return True
    # 崩溃中断项：进程重启后仍 running 的会先被 recover 成 interrupted，
    # 视为未完成、可重新处理（fingerprint 不变也要重跑这一项）。
    if record.get("status") == "interrupted":
        return True
    if retry_failed and record.get("status") == "failed":
        return True
    return False


def mark_state(state: dict, key: str, bank: str, item: dict, status: str, *, exit_code: int | None = None, error: str = "") -> None:
    state["items"][key] = {
        "bank": bank,
        "ref": item_ref(item),
        "schedule": {
            "batch": item.get("M3调度批次", ""),
            "sequence": item.get("M3调度序号", ""),
            "list_position": item.get("M3列表序号", ""),
        },
        "fingerprint": item_fingerprint(item),
        "status": status,
        "updated_at": now_text(),
        "exit_code": exit_code,
        "error": error,
    }
    write_state(state)


def process_once_details(state: dict, *, dry_run: bool, retry_failed: bool) -> list[dict]:
    details: list[dict] = []
    for bank, item in iter_queue_items():
        # 人工兜底：每个银行 item 处理前检查暂停标记。
        # 暂停时停止继续处理本轮队列；不把当前/后续项标记为 failed / running，
        # 也不动 succeeded / seen / interrupted —— 恢复后下一轮按原状态继续。
        if m3_monitor_control.is_paused():
            reason = m3_monitor_control.pause_reason() or "(未填写)"
            print(
                f"[{now_text()}] 监控已暂停：原因 {reason}；停止继续处理银行队列。"
                "已处理项保留既有状态；未处理项不会被标记 failed / running。"
            )
            break

        key = item_key(bank, item)
        fingerprint = item_fingerprint(item)
        record = state["items"].get(key)
        if not should_run(record, fingerprint, retry_failed):
            continue

        ref = item_ref(item) or key
        print(f"\n[{now_text()}] 发现新增/变更: {bank} {ref}")
        mark_state(state, key, bank, item, "running")
        try:
            code = bank_route.run_bank_forms(bank, [item], dry_run=dry_run)
        except (RouteError, OSError, json.JSONDecodeError) as exc:
            print(f"[监控] 运行失败: {exc}")
            mark_state(state, key, bank, item, "failed", exit_code=2, error=str(exc))
            details.append({"bank": bank, "ref": ref, "status": "failed", "exit_code": 2, "error": str(exc), "form": item})
            report_failure(
                title="M3银行路由失败",
                error=str(exc),
                context=f"bank={bank}; ref={ref}; key={key}",
                source="M3 monitor_bank_batches",
            )
            continue

        status = "dry_run" if dry_run and code == 0 else ("succeeded" if code == 0 else "failed")
        error = bank_route.describe_exit_code(bank, code, dry_run=dry_run)
        mark_state(state, key, bank, item, status, exit_code=code, error=error)
        details.append({"bank": bank, "ref": ref, "status": status, "exit_code": code, "error": error, "form": item})
        if code != 0:
            print(f"[监控] {bank} {ref} 退出码 {code}，已记录为 failed，不会重复跑同一份 JSON。")
            if not bank_route.truthy_env("BANK_ROUTE_REPORT_ERRORS", True):
                report_failure(
                    title="M3银行制单失败",
                    error=error or f"{bank} 制单失败，退出码 {code}",
                    context=f"bank={bank}; ref={ref}; key={key}",
                    source="M3 monitor_bank_batches",
                )
        else:
            print(f"[监控] {bank} {ref} 已记录为 {status}。")
    return details


def process_once(state: dict, *, dry_run: bool, retry_failed: bool) -> int:
    return len(process_once_details(state, dry_run=dry_run, retry_failed=retry_failed))


def acquire_lock(force: bool = False) -> int:
    MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    if force and LOCK_PATH.exists():
        LOCK_PATH.unlink()
    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(f"监控锁已存在: {LOCK_PATH}。确认没有监控进程后可加 --force-lock。") from exc
    os.write(fd, f"pid={os.getpid()}\nstarted_at={now_text()}\n".encode("utf-8"))
    return fd


def release_lock(fd: int) -> None:
    try:
        os.close(fd)
    finally:
        try:
            LOCK_PATH.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="监听 runtime/bank_batches，有新增就自动运行对应银行制单")
    parser.add_argument("--poll-seconds", type=float, default=5.0, help="轮询间隔秒数，默认 5")
    parser.add_argument("--once", action="store_true", help="只扫描一次后退出")
    parser.add_argument("--dry-run", action="store_true", help="只验证路由，不启动银行程序")
    parser.add_argument("--run-existing", action="store_true", help="启动时也处理队列里已有【全新】项目（跳过 seed，不再标记 seen）")
    parser.add_argument("--resume-existing", action="store_true", help="崩溃恢复：把上次未完成(running)的项重新处理；不重跑 succeeded/seen，也不影响 failed。也可用 M3_MONITOR_RESUME_EXISTING=1 开启")
    parser.add_argument("--retry-failed", action="store_true", help="同一份 JSON 记录为 failed 且 fingerprint 不变时仍允许重试")
    parser.add_argument("--force-lock", action="store_true", help="清理旧锁后启动")
    parser.add_argument("--quiet-idle", action="store_true", help="没有新增时不输出空闲日志")
    args = parser.parse_args()

    resume_existing = args.resume_existing or truthy_env("M3_MONITOR_RESUME_EXISTING", False)

    lock_fd = acquire_lock(force=args.force_lock)
    try:
        state = read_state()
        if not args.run_existing:
            seeded = seed_existing_items(state)
            if seeded:
                print(f"[监控] 已把现有 {seeded} 条队列记录标记为 seen；之后新增/变更才会自动运行。")
                state = read_state()
        if resume_existing:
            recovered = recover_interrupted_items(state)
            if recovered:
                print(f"[监控] 崩溃恢复：{recovered} 条上次未完成(running→interrupted)项将重新处理；succeeded/seen/failed 不受影响。")
                state = read_state()
            else:
                print("[监控] 崩溃恢复：未发现上次未完成(running)项，无需恢复。")

        print(
            f"[监控] 启动: queue={bank_route.BANK_BATCH_DIR} "
            f"dry_run={args.dry_run} once={args.once} poll={args.poll_seconds}s"
        )
        while True:
            ran = process_once(state, dry_run=args.dry_run, retry_failed=args.retry_failed)
            if ran == 0 and not args.quiet_idle:
                print(f"[{now_text()}] 没有新增队列项。")
            state = read_state()
            if args.once:
                return 0
            time.sleep(max(args.poll_seconds, 1.0))
    except KeyboardInterrupt:
        print("\n[监控] 已停止。")
        return 130
    finally:
        release_lock(lock_fd)


if __name__ == "__main__":
    raise SystemExit(main())
