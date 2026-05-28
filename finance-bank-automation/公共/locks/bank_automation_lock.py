# -*- coding: utf-8 -*-
"""跨项目「银行自动化全局锁」。

为什么要这把锁
---------------
``来参缘定时调拨`` 与 ``M3 24 小时监控生产银行制单`` 都会启动银行客户端、
切 USB Hub、抢占桌面焦点；如果它们并发运行，会在 U-BANK / 浏览器窗口、
UKey 与 USBHub 上互相打架，可能导致误操作。
故新增一把**跨项目互斥锁**：任何要启动真实银行子流程的入口，进入"真实
动作"前必须先 acquire 同一个全局锁，离开时（finally）必须释放自己拥有的
那把。

边界
----
- 锁只保护"会去开银行 / 切 USBHub / 操作 UKey 的真实路径"。
- ``dry-run`` / ``M3_DISABLE_BANK_INVOCATION=1`` / 纯 preflight / 纯金额计算
  等不打开银行的路径**不需要**这把锁。
- 锁拿不到时是 fail-closed —— 不等待、不强抢、不抢占别人的 USBHub。
- 释放锁时只允许删除 owner+pid 都匹配的锁，避免误删别人的锁。
- 锁文件位置（默认）：``C:\\Users\\30112\\Desktop\\财务\\runtime\\locks\\
  bank_automation.lock``。该目录已被仓库 ``.gitignore`` 覆盖。

锁文件 JSON 结构
----------------
::

    {
      "owner":       "laicanyuan_transfer" | "m3_bank_route",
      "pid":         <int>,
      "started_at":  ISO-8601 时间字符串,
      "purpose":     人类可读的用途，例如 "来参缘定时调拨" / "M3银行制单路由",
      "run_dir":     可选证据目录字符串
    }

设计要点
--------
- 原子创建：``open(..., "x")`` 等价于 ``O_CREAT|O_EXCL``，竞争窗口很小。
- ``pid_checker`` 可注入：方便测试用 ``lambda p: True/False`` 模拟 active /
  stale，**不**依赖外部 ``tasklist`` 或 ``psutil``。
- Windows-only 的 ``ctypes`` 路径仅在没有显式注入 ``pid_checker`` 时使用；
  其它平台保守认为锁是 active（fail-closed）—— 这与系统设计意图一致：
  本套件只跑在 Windows 11 的桌面上。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


# 不要在 import 阶段假设环境变量；把路径解析延迟到首次调用。
_ENV_FINANCE_HOME = "FINANCE_HOME"
_DEFAULT_FINANCE_HOME = Path(r"C:\Users\30112\Desktop\财务")
_RUNTIME_SUBDIR = ("runtime", "locks")
_LOCK_FILE_NAME = "bank_automation.lock"

OWNER_LAICANYUAN = "laicanyuan_transfer"
OWNER_M3_BANK_ROUTE = "m3_bank_route"
KNOWN_OWNERS = (OWNER_LAICANYUAN, OWNER_M3_BANK_ROUTE)


class LockBusy(RuntimeError):
    """全局锁已被某个**活跃** pid 持有；调用方应 fail-closed。

    ``prev`` 是上一把锁的 JSON 字典（owner / pid / started_at / purpose /
    run_dir），可能为 ``None``（锁文件无法解析时按"活跃锁"保守处理）。
    """

    def __init__(self, prev: dict | None, message: str):
        super().__init__(message)
        self.prev = prev or {}


class LockTakeoverFailed(RuntimeError):
    """检测到 stale 锁但删除 / 重建失败；调用方应 fail-closed。"""


def default_lock_path() -> Path:
    """默认锁路径：``<FINANCE_HOME>/runtime/locks/bank_automation.lock``。

    ``FINANCE_HOME`` 环境变量优先；未设置时回落到 ``C:\\Users\\30112\\
    Desktop\\财务``（与现有 M3 / 招行制单等模块的默认一致）。
    """
    base_raw = os.environ.get(_ENV_FINANCE_HOME)
    if base_raw and base_raw.strip():
        base = Path(base_raw.strip())
    else:
        base = _DEFAULT_FINANCE_HOME
    return base.joinpath(*_RUNTIME_SUBDIR, _LOCK_FILE_NAME)


def _pid_is_running(pid: int) -> bool:
    """Windows 上判断给定 pid 是否仍是活跃进程。

    与 ``run_laicanyuan_transfer._pid_is_running`` 同一套语义，但本模块
    自包含：走 ``kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`` +
    ``GetExitCodeProcess``，避免依赖外部 ``tasklist``。任何打不开 / 拿不到
    信息的情况一律返回 False，让调用方按 stale 锁处理 —— 比保守 True 更
    安全：保守 True 会把误判的"实际已死"的锁永久卡住。
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return False
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except Exception:
        return False
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        if not ok:
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _build_payload(
    *, owner: str, pid: int, started_at: str, purpose: str, run_dir: str | None
) -> dict[str, Any]:
    return {
        "owner": owner,
        "pid": pid,
        "started_at": started_at,
        "purpose": purpose,
        "run_dir": run_dir or "",
    }


def _read_existing(lock_path: Path) -> dict | None:
    try:
        with lock_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _format_busy(prev: dict | None) -> str:
    if not isinstance(prev, dict):
        return "全局银行自动化锁已被持有，但锁文件无法解析（按活跃锁处理）"
    return (
        "全局银行自动化锁已被持有："
        f"owner={prev.get('owner')!r} pid={prev.get('pid')!r} "
        f"started_at={prev.get('started_at')!r} purpose={prev.get('purpose')!r} "
        f"run_dir={prev.get('run_dir')!r}"
    )


def acquire(
    *,
    owner: str,
    purpose: str,
    my_pid: int | None = None,
    started_at: str | None = None,
    run_dir: str | os.PathLike | None = None,
    lock_path: Path | None = None,
    pid_checker: Callable[[int], bool] | None = None,
) -> tuple[str, dict | None]:
    """获取全局银行自动化锁。

    返回:
        ("acquired", None)         —— 无锁，已写入新锁
        ("stole_stale", prev_dict) —— 旧锁 pid 已不在，已删除并替换

    抛 ``LockBusy``（建议调用方按 fail-closed 处理，不要等待）:
        - 现存锁的 pid 仍存活
        - 现存锁无法解析（保守按活跃锁处理）

    抛 ``LockTakeoverFailed``:
        - 检测到 stale 锁但删除 / 重建失败 / 被其它进程抢占
    """
    if owner not in KNOWN_OWNERS:
        # 未注册的 owner 名字不影响功能（写哪都行），但记录一行警告，
        # 避免拼写错误导致 release 阶段不匹配。
        # 注意：用 print 而不是 raise —— release 仍然按 owner+pid 严格匹配。
        print(
            f"[bank_automation_lock] warning: 未知的 owner {owner!r}；"
            f"已知 owner: {KNOWN_OWNERS}"
        )
    if my_pid is None:
        my_pid = os.getpid()
    if started_at is None:
        started_at = datetime.now().isoformat(timespec="seconds")
    if lock_path is None:
        lock_path = default_lock_path()
    if pid_checker is None:
        pid_checker = _pid_is_running

    payload = _build_payload(
        owner=owner,
        pid=my_pid,
        started_at=started_at,
        purpose=purpose,
        run_dir=str(run_dir) if run_dir is not None else None,
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # 原子创建（O_CREAT|O_EXCL 语义）。文件不存在时直接成功。
    try:
        with open(lock_path, "x", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return ("acquired", None)
    except FileExistsError:
        pass

    prev = _read_existing(lock_path)
    if prev is None:
        # 锁文件存在但无法解析 → 按活跃锁处理，绝不静默删除别人的状态。
        raise LockBusy(prev=None, message=_format_busy(prev))

    prev_pid = prev.get("pid")
    if isinstance(prev_pid, int) and pid_checker(prev_pid):
        raise LockBusy(prev=prev, message=_format_busy(prev))

    # stale: pid 已不在。先记下旧锁内容，再删除并原子重建。
    try:
        lock_path.unlink()
    except Exception as exc:  # noqa: BLE001
        raise LockTakeoverFailed(
            f"检测到 stale 全局锁 (pid={prev_pid})，但删除失败: {exc}"
        )
    try:
        with open(lock_path, "x", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except FileExistsError:
        raise LockTakeoverFailed("stale 全局锁删除后又被其它实例抢占，拒绝重入")
    return ("stole_stale", prev)


def release(
    *,
    owner: str,
    my_pid: int | None = None,
    lock_path: Path | None = None,
) -> bool:
    """仅在锁内容的 ``owner`` **且** ``pid`` 与自己匹配时才删除锁文件。

    返回是否实际删除了文件。任何"锁不属于自己"的情况都返回 False —— 不
    动别人的锁。锁文件不存在或无法解析也返回 False。
    """
    if my_pid is None:
        my_pid = os.getpid()
    if lock_path is None:
        lock_path = default_lock_path()

    prev = _read_existing(lock_path)
    if prev is None:
        return False
    if prev.get("owner") != owner:
        return False
    if prev.get("pid") != my_pid:
        return False
    try:
        lock_path.unlink()
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def peek(lock_path: Path | None = None) -> dict | None:
    """读取当前全局锁文件内容（不修改）；不存在或无法解析返回 ``None``。"""
    if lock_path is None:
        lock_path = default_lock_path()
    return _read_existing(lock_path)


def is_held_by_active_pid(
    *,
    lock_path: Path | None = None,
    pid_checker: Callable[[int], bool] | None = None,
) -> tuple[bool, dict | None]:
    """诊断式查询：当前是否有活跃 pid 持有这把全局锁？

    返回 ``(True, prev)`` 表示有活跃 owner；``(False, prev)`` 表示无锁或
    锁是 stale 的。不修改任何文件。
    """
    if pid_checker is None:
        pid_checker = _pid_is_running
    prev = peek(lock_path)
    if not isinstance(prev, dict):
        return (False, None)
    prev_pid = prev.get("pid")
    if isinstance(prev_pid, int) and pid_checker(prev_pid):
        return (True, prev)
    return (False, prev)


__all__ = [
    "LockBusy",
    "LockTakeoverFailed",
    "OWNER_LAICANYUAN",
    "OWNER_M3_BANK_ROUTE",
    "KNOWN_OWNERS",
    "default_lock_path",
    "acquire",
    "release",
    "peek",
    "is_held_by_active_pid",
]
