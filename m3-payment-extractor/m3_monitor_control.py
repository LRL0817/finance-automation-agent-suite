# -*- coding: utf-8 -*-
"""人工兜底：暂停/恢复/查询 M3 自动监控。

业务口径：当 M3 自动制单出现问题（未识别付款银行、缺截图、金额/账号异常、
银行页面卡住、超时需人工核对待审核队列等）时，财务同事可以通过 Codex CLI
让本脚本暂停 M3 监控，先人工查余额、补资料或手工制单；处理完后再用本脚本
恢复监控。

设计原则（安全闸门，与提交/付款无关）：
  - 只通过一个标记文件 runtime/control/monitor_paused.json 影响监控行为；
  - 不杀任何进程、不触碰银行客户端、不操作 USBHub、不读取 .env；
  - 暂停只影响"下一轮抽取/下一笔分发"——正在执行的银行子流程不会被强杀；
  - succeeded / failed / seen / interrupted 等状态语义不变；
  - 124 超时不自动重试逻辑不变；
  - 不新增自动复核 / 授权 / 付款路径。

命令：
  python m3_monitor_control.py status
  python m3_monitor_control.py pause --reason "人工查余额/补资料"
  python m3_monitor_control.py resume
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


sys.dont_write_bytecode = True
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


ROOT = Path(__file__).resolve().parent
CONTROL_DIR = ROOT / "runtime" / "control"
PAUSE_FILE = CONTROL_DIR / "monitor_paused.json"
ARCHIVE_DIR = CONTROL_DIR / "archive"
MONITOR_LOCK_PATH = ROOT / "runtime" / "monitor" / "auto_bank_monitor.lock"


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def is_paused() -> bool:
    """监控是否处于人工暂停态。

    供 auto_extract_dispatch / monitor_bank_batches 在每轮开始前 / 每笔分发前
    导入调用。只看文件是否存在 + 内容是否标记 paused=true；读不到/损坏时
    一律按"未暂停"处理（fail-open 对暂停而言＝继续正常监控；fail-closed
    对资金安全而言仍由各银行子流程门禁负责）。
    """
    if not PAUSE_FILE.exists():
        return False
    try:
        payload = json.loads(PAUSE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("paused"))


def read_pause_info() -> dict:
    """读取暂停信息（用于日志/通知）。文件不存在或解析失败返回空 dict。"""
    if not PAUSE_FILE.exists():
        return {}
    try:
        payload = json.loads(PAUSE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def pause_reason() -> str:
    info = read_pause_info()
    reason = str(info.get("reason") or "").strip()
    return reason


def _ensure_dirs() -> None:
    CONTROL_DIR.mkdir(parents=True, exist_ok=True)


def _pid_alive(pid: int) -> bool:
    """跨平台粗略判断 pid 是否仍存活；任何异常按"存活"处理（保守，避免
    误判 lock 已僵尸而推荐操作员强行清锁）。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                exit_code = wintypes.DWORD(0)
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return True
                STILL_ACTIVE = 259
                return exit_code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_monitor_lock_pid() -> int | None:
    if not MONITOR_LOCK_PATH.exists():
        return None
    try:
        text = MONITOR_LOCK_PATH.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("pid="):
            try:
                return int(line.split("=", 1)[1].strip())
            except ValueError:
                return None
    return None


def cmd_pause(reason: str) -> int:
    _ensure_dirs()
    reason = (reason or "").strip()
    payload = {
        "paused": True,
        "reason": reason,
        "created_at": now_text(),
        "created_by_pid": os.getpid(),
        "host": os.environ.get("COMPUTERNAME", ""),
    }
    tmp = PAUSE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PAUSE_FILE)
    print(f"[m3-monitor-control] 已暂停 M3 监控；标记文件: {PAUSE_FILE}")
    print(f"  原因: {reason or '(未填写)'}")
    print("  说明: 这是'暂停下一轮/下一笔'的安全控制；正在执行的银行流程不会被强杀。")
    print("        恢复请用: python m3_monitor_control.py resume")
    return 0


def cmd_resume() -> int:
    if not PAUSE_FILE.exists():
        print("[m3-monitor-control] 当前未处于暂停态（标记文件不存在）；无需操作。")
        return 0
    info = read_pause_info()
    _ensure_dirs()
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = ARCHIVE_DIR / f"monitor_paused_{time.strftime('%Y%m%d_%H%M%S')}.json"
    try:
        info_for_archive = dict(info)
        info_for_archive["resumed_at"] = now_text()
        info_for_archive["resumed_by_pid"] = os.getpid()
        archive_path.write_text(
            json.dumps(info_for_archive, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        PAUSE_FILE.unlink()
    except OSError as exc:
        print(f"[m3-monitor-control] 归档/删除暂停文件失败: {exc}")
        return 1
    print(f"[m3-monitor-control] 已恢复 M3 监控；旧暂停信息归档到: {archive_path}")
    print("  下一轮抽取/下一笔分发将按正常监控状态继续处理新增/变更记录。")
    return 0


def cmd_status() -> int:
    paused = is_paused()
    info = read_pause_info()
    lock_exists = MONITOR_LOCK_PATH.exists()
    lock_pid = _read_monitor_lock_pid() if lock_exists else None
    lock_pid_alive = _pid_alive(lock_pid) if lock_pid else False

    print(f"[m3-monitor-control] 状态采集时间: {now_text()}")
    print(f"  标记文件: {PAUSE_FILE}")
    if paused:
        print("  paused=true（M3 监控当前处于人工暂停态）")
        if info.get("reason"):
            print(f"  reason: {info.get('reason')}")
        if info.get("created_at"):
            print(f"  created_at: {info.get('created_at')}")
    else:
        print("  paused=false（M3 监控当前不在人工暂停态）")
    print(f"  monitor_lock 存在: {lock_exists}（{MONITOR_LOCK_PATH}）")
    if lock_exists:
        if lock_pid is None:
            print("  monitor_lock pid: <未识别>")
        else:
            print(f"  monitor_lock pid: {lock_pid}, 进程仍活: {lock_pid_alive}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="人工兜底：暂停/恢复/查询 M3 自动监控（不杀进程、不操作银行、不操作 USBHub）",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    p_pause = sub.add_parser("pause", help="暂停 M3 自动监控（影响下一轮抽取/下一笔分发）")
    p_pause.add_argument("--reason", default="", help="本次暂停的简短原因，会写入暂停文件并出现在状态查询里")

    sub.add_parser("resume", help="恢复 M3 自动监控（删除/归档暂停文件）")
    sub.add_parser("status", help="查询当前暂停状态、monitor lock 是否存在与 pid 是否还活")

    args = parser.parse_args(argv)

    if args.action == "pause":
        return cmd_pause(args.reason)
    if args.action == "resume":
        return cmd_resume()
    if args.action == "status":
        return cmd_status()
    parser.error(f"未知子命令: {args.action}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
