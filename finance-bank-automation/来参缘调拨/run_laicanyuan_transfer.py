# -*- coding: utf-8 -*-
"""来参缘 → 兴业 内部调拨 runner.

只做经办/制单进入「待审核」队列后停止。绝不自动复核 / 授权 / 确认 / 最终付款。

业务边界：
- 招行 U-BANK 登录名固定为「来参缘潘思婷002」。
- 来参缘 001/002 是同一个招行 UKey，物理端口固定为 18；差异只在登录窗口切换账号。
- 默认 `ZHIDAN_TEST_MODE=1`；仅当父进程 `LAICANYUAN_TRANSFER_PRODUCTION=1` 时 runner 才会
  把子进程 `ZHIDAN_TEST_MODE` 设为 `0`。父进程随手设 `ZHIDAN_TEST_MODE=0` 但没设
  `LAICANYUAN_TRANSFER_PRODUCTION=1` 时，runner 仍然强制把子进程改回 `1`。
- 金额规则：按余额向下取整到「万元」转出，万元以下零头留在 002 账户。
  例如 109021.98 → 100000；余额 < 10000 时 fail-closed。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from pathlib import Path
from typing import Any

RUNNER_DIR = Path(__file__).resolve().parent
FINANCE_ROOT = RUNNER_DIR.parent
CMB_ZHIDAN_ROOT = FINANCE_ROOT / "招行" / "招行制单" / "招行"
CMB_ZHIDAN_PROJECT_ROOT = FINANCE_ROOT / "招行" / "招行制单"
QUERY_BALANCE_PROJECT = FINANCE_ROOT / "招行" / "查询招行余额"
QUERY_BALANCE_SCRIPT = QUERY_BALANCE_PROJECT / "scripts" / "query_balance.py"
ZHIDAN_ENTRY = CMB_ZHIDAN_ROOT / "skills" / "制单单账号单笔转账skill" / "制单.py"
BANK_FORM_PATH = CMB_ZHIDAN_PROJECT_ROOT / "M3直供合同付款数据获取" / "bank_form.json"
USB_HUB_CTRL = FINANCE_ROOT / "公共" / "usbhub" / "usbhub" / "多口USB控制器软件以及驱动" / "hub_ctrl.py"

# 单实例锁，仅约束本 runner 自己，绝不接触其它银行/M3/网关任务。
RUNTIME_DIR = RUNNER_DIR / "runtime"
LOCK_PATH = RUNTIME_DIR / "laicanyuan_transfer.lock"

# 全局银行自动化锁（跨项目）：与 M3 银行制单路由互斥，避免并发抢窗口/UKey。
# 模块在 财务\公共\locks\bank_automation_lock.py，下面延迟 import 以避免
# 在 --dry-run-amount-only 这种"不开银行"的单元测试路径里也强依赖。
_GLOBAL_LOCK_MODULE_DIR = FINANCE_ROOT / "公共" / "locks"

LOGIN_ACCOUNT_NAME = "来参缘潘思婷002"
LAICANYUAN_CMB_USB_HUB_PORT_DEFAULT = "18"
LAICANYUAN_CMB_USB_HUB_PORT_ENV = "LAICANYUAN_CMB_USB_HUB_PORT"
LEGACY_LAICANYUAN_USB_HUB_PORT_ENV = "LAICANYUAN_USB_HUB_PORT"
LAICANYUAN_ALLOWED_CMB_USB_HUB_PORTS = {LAICANYUAN_CMB_USB_HUB_PORT_DEFAULT}
KNOWN_NON_LAICANYUAN_USB_HUB_PORT_REASONS = {
    "10": "河南讯动/中行 UKey",
    "11": "蒙特农行 UKey",
    "12": "蒙特招行 UKey",
    "13": "蒙选农行 UKey",
    "14": "巡鲜农行 UKey",
    "15": "得鲜农行 UKey",
    "16": "得鲜招行 UKey",
    "17": "来参缘兴业 UKey",
    "19": "云炫农招行 UKey",
    "30": "农行外置 OK 自动点击器",
}
TRANSFER_UNIT_RMB = Decimal("10000")
# 历史兼容名：旧版测试/文档曾引用这个常量名。
# 现在真实业务规则是"按万元取整转出，万元以下零头留在 002 账户"。
RESERVED_AMOUNT_RMB = TRANSFER_UNIT_RMB
PRODUCTION_GATE_ENV = "LAICANYUAN_TRANSFER_PRODUCTION"

# query_balance.py 子进程参数。默认 auto：自动尝试 current → 首页 → 工作台，
# 来参缘 002 登录后首页是空白，余额其实在 工作台 上的「人民币总资产(元)」。
# 通过 LAICANYUAN_BALANCE_PAGE 可强制改成 home / workbench / current（仅用于排障）。
DEFAULT_BALANCE_PAGE = "auto"
BALANCE_PAGE_CHOICES = ("auto", "current", "home", "workbench")

HOME_BALANCE_LABEL = "人民币账户实时余额合计(元)"
WORKBENCH_BALANCE_LABEL = "人民币总资产(元)"
BALANCE_LABEL_WHITELIST = (HOME_BALANCE_LABEL, WORKBENCH_BALANCE_LABEL)

# workbench 上「存款(元)」金额必须等于「人民币总资产(元)」，否则可能存在 理财/票据/信用证
# 等非现金资产，按总资产推可调拨金额会高估。找不到 存款 标签或其金额、解析失败、与总资产
# 不一致 —— 全部 fail-closed（视为"无法证明人民币总资产全部为可用存款"）。
_DEPOSIT_LABEL_CANDIDATES = ("存款(元)", "存款（元）", "存款")
_NON_CASH_LABEL_CANDIDATES = ("理财(元)", "理财（元）", "理财", "票据(元)", "票据（元）", "票据")

# 资产卡片几何容差：U-BANK 工作台资产卡片是"标签 + 金额"竖排，金额可能在标签上方（实测
# 来参缘 002）也可能在下方（query_balance 旧布局假设）。两种布局都要支持。
# 水平：金额与标签的中心点 x 距离不超过 80 px（卡片宽度通常 ~180 px）。
# 垂直：金额顶/底边与标签底/顶边的 gap 不超过 80 px（实测 ~8 px，留 10x 余量给布局微调）。
_ASSET_CARD_HORIZONTAL_TOL = 80
_ASSET_CARD_VERTICAL_GAP_MAX = 80

# 招行余额数字识别：和 查询招行余额\scripts\query_balance.py 的 MONEY_RE 保持一致 ——
# 整数千分位逗号或裸整数 + 必须的小数两位。不接受 1 位小数或 3 位小数，避免乱抓表中其它数字。
_MONEY_RE = re.compile(r"(?<!\d)-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})(?!\d)")

# Exit codes (avoid collision with 制单.py's enumerated codes 2/3/4/5/8/9/10/11/12).
EXIT_OK = 0
EXIT_BAD_PAYEE_CONFIG = 20
EXIT_BAD_USB_HUB = 21
EXIT_BALANCE_READ_FAILED = 22
EXIT_BALANCE_AMBIGUOUS = 23
EXIT_BALANCE_NOT_ENOUGH = 24
EXIT_AMOUNT_TOO_SMALL = 25
EXIT_BANK_FORM_BACKUP_FAILED = 26
EXIT_LOCK_HELD = 27
# 全局银行自动化锁已被另一个活跃 owner（如 M3 银行路由）持有：本轮直接跳过，
# 不打开 U-BANK / 不切 USB Hub / 不写 bank_form。这一退出码专门给定时任务
# 用作"本轮被全局锁互斥跳过"的明确信号，方便外部观察是 busy 还是其它故障。
EXIT_GLOBAL_LOCK_BUSY = 28


class FailClosed(RuntimeError):
    def __init__(self, exit_code: int, message: str):
        super().__init__(message)
        self.exit_code = exit_code


def resolve_laicanyuan_usb_hub_port() -> str:
    """Resolve the fixed CMB UKey port for this runner.

    来参缘 001/002 是同一个招行 UKey，物理端口固定为 18；001/002
    的差异只在 U-BANK 登录窗口选择不同登录名。显式 env 仅用于排障，
    且必须仍为 18，避免把其它公司/银行的 UKey 当作来参缘招行盾。
    """
    raw = (
        os.environ.get(LAICANYUAN_CMB_USB_HUB_PORT_ENV)
        or os.environ.get(LEGACY_LAICANYUAN_USB_HUB_PORT_ENV)
        or LAICANYUAN_CMB_USB_HUB_PORT_DEFAULT
    ).strip()
    if not raw.isdigit():
        raise FailClosed(EXIT_BAD_USB_HUB, f"{LAICANYUAN_CMB_USB_HUB_PORT_ENV} 必须是 1-30 的数字端口，实际 [{raw}]")
    port = int(raw)
    if not 1 <= port <= 30:
        raise FailClosed(EXIT_BAD_USB_HUB, f"{LAICANYUAN_CMB_USB_HUB_PORT_ENV} 超出 1-30 范围: {raw}")
    if str(port) not in LAICANYUAN_ALLOWED_CMB_USB_HUB_PORTS:
        reason = KNOWN_NON_LAICANYUAN_USB_HUB_PORT_REASONS.get(str(port), "非来参缘招行 UKey")
        raise FailClosed(
            EXIT_BAD_USB_HUB,
            f"{LAICANYUAN_CMB_USB_HUB_PORT_ENV}={port} 被拒绝：{port} 口是{reason}；来参缘招行 001/002 固定使用 18 口。",
        )
    return str(port)


def _pid_is_running(pid: int) -> bool:
    """Windows 上判断给定 pid 是否仍是活跃进程。

    走 `kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` +
    `GetExitCodeProcess`，避免依赖外部 `tasklist`。任何打不开/拿不到信息的情况
    一律保守返回 False（让调用方按 stale 锁处理，然后由 acquire_lock 再次原子建锁）。
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
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
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


def acquire_lock(
    lock_path: Path,
    *,
    my_pid: int,
    run_dir: Path,
    started_at: str,
    pid_checker=_pid_is_running,
) -> tuple[str, dict | None]:
    """获取来参缘调拨单实例锁。

    返回：
      ("acquired", None)         —— 无现存锁，已写入新锁
      ("stole_stale", prev_dict) —— 现存锁是 stale（pid 已不在），已替换为新锁

    抛 FailClosed(EXIT_LOCK_HELD)：
      - 现存锁的 pid 仍在跑（活跃实例）
      - 现存锁无法解析（保守按活跃锁处理）
      - 锁文件删除失败 / stale 后被其它实例抢占

    `pid_checker` 可注入用于测试（默认走 _pid_is_running 检查真实 Windows 进程）。
    """
    payload = {"pid": my_pid, "started_at": started_at, "run_dir": str(run_dir)}
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # 原子创建（O_CREAT|O_EXCL 语义）。文件不存在时直接成功，避免常见竞争。
    try:
        with open(lock_path, "x", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return ("acquired", None)
    except FileExistsError:
        pass

    # 现存锁：解析并判断 active vs stale。
    try:
        with open(lock_path, "r", encoding="utf-8") as f:
            prev = json.load(f)
    except Exception as exc:
        raise FailClosed(
            EXIT_LOCK_HELD,
            (
                f"已存在 lock 文件但无法解析: {lock_path}: {exc}（按活跃锁处理，"
                "拒绝重入；如确认无其它实例，请人工删除该锁文件）"
            ),
        )

    prev_pid = prev.get("pid") if isinstance(prev, dict) else None
    if isinstance(prev_pid, int) and pid_checker(prev_pid):
        raise FailClosed(
            EXIT_LOCK_HELD,
            (
                f"已有来参缘调拨实例运行中，拒绝重入：pid={prev_pid} "
                f"started_at={prev.get('started_at') if isinstance(prev, dict) else None} "
                f"run_dir={prev.get('run_dir') if isinstance(prev, dict) else None}"
            ),
        )

    # stale: pid 已不在。删除旧锁并原子重建。
    try:
        lock_path.unlink()
    except Exception as exc:
        raise FailClosed(
            EXIT_LOCK_HELD,
            f"检测到 stale lock (pid={prev_pid})，但删除失败: {exc}",
        )
    try:
        with open(lock_path, "x", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except FileExistsError:
        raise FailClosed(
            EXIT_LOCK_HELD,
            "stale lock 删除后又被其它实例抢占，拒绝重入",
        )
    return ("stole_stale", prev if isinstance(prev, dict) else None)


def release_lock(lock_path: Path, *, my_pid: int) -> bool:
    """仅在锁内容的 pid 仍属于自己时才删除。返回是否实际删除了文件。"""
    try:
        with open(lock_path, "r", encoding="utf-8") as f:
            prev = json.load(f)
    except FileNotFoundError:
        return False
    except Exception:
        # 读不出锁文件，保守不动它，避免误删别人的锁。
        return False
    if isinstance(prev, dict) and prev.get("pid") == my_pid:
        try:
            lock_path.unlink()
            return True
        except Exception:
            return False
    return False


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _ensure_runs_dir() -> Path:
    run_dir = RUNNER_DIR / "runs" / f"laicanyuan_{_now_stamp()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


class TeeLogger:
    """同时写入文件与 stdout 的小型 logger（不动 logging 模块，方便子进程也走 stdout）。"""

    def __init__(self, run_dir: Path):
        self.path = run_dir / "run.log"
        self._fh = open(self.path, "a", encoding="utf-8")

    def log(self, line: str) -> None:
        stamped = f"[{datetime.now().isoformat(timespec='seconds')}] {line}"
        print(stamped)
        self._fh.write(stamped + "\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


def parse_balance_text(raw: str) -> Decimal:
    """把页面读到的余额文本严格解析为 Decimal。

    - 去除前后空白、千位逗号、全角逗号。
    - 不接受空字符串、非数字、含字母。
    - 解析失败抛 InvalidOperation/ValueError，由调用方转 FailClosed。
    """
    if raw is None:
        raise InvalidOperation("balance text is None")
    text = str(raw).strip().replace(",", "").replace("，", "")
    if not text:
        raise InvalidOperation("balance text is empty")
    return Decimal(text)


def compute_transferable(balance: Decimal, unit: Decimal = TRANSFER_UNIT_RMB) -> Decimal:
    """按"余额向下取整到万元"的规则计算可转金额。

    - balance < unit → 抛 FailClosed（exit 24，余额不足 1 万，本轮不转）。
    - balance >= unit 时返回 floor(balance / unit) * unit。
    - 例如 109021.98 → 100000，万元以下零头留在 002 账户。
    """
    if balance < unit:
        raise FailClosed(
            EXIT_BALANCE_NOT_ENOUGH,
            f"余额不足 1 万元，不调拨：balance={balance} < unit={unit}",
        )
    units = (balance / unit).to_integral_value(rounding=ROUND_FLOOR)
    transferable = units * unit
    return transferable


def load_payee_config(log: TeeLogger) -> dict[str, str]:
    cfg_path = RUNNER_DIR / "laicanyuan_payee.local.json"
    if not cfg_path.exists():
        raise FailClosed(
            EXIT_BAD_PAYEE_CONFIG,
            f"未找到真实收方配置: {cfg_path}（应为 .gitignore 忽略的本地文件，参考 laicanyuan_payee.example.json）",
        )
    try:
        with cfg_path.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as exc:
        raise FailClosed(EXIT_BAD_PAYEE_CONFIG, f"读取 {cfg_path} 失败: {exc}")
    if not isinstance(cfg, dict):
        raise FailClosed(EXIT_BAD_PAYEE_CONFIG, "收方配置 JSON 顶层必须是 object")

    required = ("付款单位名称", "收方户名", "开户银行", "支行名称", "收方账号")
    missing = [k for k in required if not str(cfg.get(k, "")).strip()]
    if missing:
        raise FailClosed(EXIT_BAD_PAYEE_CONFIG, f"收方配置缺字段: {missing}")

    payee_acct = str(cfg["收方账号"]).strip()
    if not payee_acct.isdigit():
        raise FailClosed(EXIT_BAD_PAYEE_CONFIG, "收方账号必须为纯数字（不允许空格/逗号/小数）")
    if not (8 <= len(payee_acct) <= 24):
        raise FailClosed(EXIT_BAD_PAYEE_CONFIG, f"收方账号位数异常: {len(payee_acct)}")

    purpose = str(cfg.get("用途", "") or "").strip() or "转款"

    log.log(f"已加载收方配置: {cfg_path.name}")
    log.log(f"  付款单位名称: {cfg['付款单位名称']}")
    log.log(f"  收方户名: {cfg['收方户名']}")
    log.log(f"  开户银行: {cfg['开户银行']}")
    log.log(f"  支行名称: {cfg['支行名称']}")
    log.log(f"  收方账号末 4 位: ***{payee_acct[-4:]}")
    log.log(f"  用途: {purpose}")
    return {
        "付款单位名称": str(cfg["付款单位名称"]).strip(),
        "收方户名": str(cfg["收方户名"]).strip(),
        "开户银行": str(cfg["开户银行"]).strip(),
        "支行名称": str(cfg["支行名称"]).strip(),
        "收方账号": payee_acct,
        "用途": purpose,
    }


def build_subprocess_env(production: bool, cmb_usb_hub_port: str) -> dict[str, str]:
    """生成 runner 调用子进程时使用的 env。

    强制写入的 key（不论父进程是什么）：
      CMB_LOGIN_ACCOUNT_NAME=来参缘潘思婷002
      USB_HUB_FORCE_PORT=18
      ZHIDAN_ALLOW_FORCE_USB_PORT=1
      ZHIDAN_TEST_MODE = "0" if production else "1"
    """
    env = dict(os.environ)
    env["CMB_LOGIN_ACCOUNT_NAME"] = LOGIN_ACCOUNT_NAME
    env["USB_HUB_FORCE_PORT"] = cmb_usb_hub_port
    env["ZHIDAN_ALLOW_FORCE_USB_PORT"] = "1"
    env["ZHIDAN_TEST_MODE"] = "0" if production else "1"
    # 避免父进程偷渡其它付款方账号下拉控制项。该流程只用登录名 002，不必动「付款方账号」下拉。
    for k in ("CMB_PAYER_ACCOUNT_TEXT", "CMB_PAYER_ACCOUNT_SUFFIX", "CMB_PAYER_ACCOUNT_INDEX"):
        env.pop(k, None)
    return env


def usb_hub_only(port: str, log: TeeLogger) -> None:
    if not USB_HUB_CTRL.exists():
        raise FailClosed(EXIT_BAD_USB_HUB, f"未找到 USB Hub 控制脚本: {USB_HUB_CTRL}")
    com = os.environ.get("USB_HUB_COM", "COM3")
    log.log(f"[USB] 切到 {port} 口 ({com}) ...")
    try:
        proc = subprocess.run(
            [sys.executable, str(USB_HUB_CTRL), "only", port, "--settle", "2", "--com", com],
            timeout=45,
        )
    except subprocess.TimeoutExpired:
        raise FailClosed(EXIT_BAD_USB_HUB, f"USB Hub {port} 口切换超时")
    log.log(f"[USB] 切换 {port} 口退出码: {proc.returncode}")
    if proc.returncode != 0:
        raise FailClosed(EXIT_BAD_USB_HUB, f"USB Hub {port} 口切换失败 (rc={proc.returncode})")


def usb_hub_all_off(log: TeeLogger) -> None:
    if not USB_HUB_CTRL.exists():
        log.log(f"[USB] 未找到 hub_ctrl.py，跳过 all-off: {USB_HUB_CTRL}")
        return
    com = os.environ.get("USB_HUB_COM", "COM3")
    try:
        proc = subprocess.run(
            [sys.executable, str(USB_HUB_CTRL), "all-off", "--com", com],
            timeout=45,
        )
        log.log(f"[USB] all-off 退出码: {proc.returncode}")
    except Exception as exc:
        log.log(f"[USB] all-off 异常: {exc}")


def kill_ubank(log: TeeLogger) -> None:
    """taskkill /F /T /IM Firmbank.exe；与 zhidan_crash._close_with_confirm 的兜底做法一致。"""
    try:
        proc = subprocess.run(
            ["taskkill", "/F", "/T", "/IM", "Firmbank.exe"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
        )
        log.log(f"[U-BANK] taskkill Firmbank.exe rc={proc.returncode}")
    except Exception as exc:
        log.log(f"[U-BANK] taskkill 失败: {exc}")


def latest_balance_json(after_ts: float) -> Path | None:
    runs_root = QUERY_BALANCE_PROJECT / "runs"
    if not runs_root.exists():
        return None
    candidates = []
    for child in runs_root.iterdir():
        if not child.is_dir() or not child.name.startswith("balance_"):
            continue
        balance_json = child / "balance.json"
        if not balance_json.exists():
            continue
        # 只接受本次启动之后产生的目录，避免读到旧结果。
        if balance_json.stat().st_mtime < after_ts - 1:
            continue
        candidates.append((balance_json.stat().st_mtime, balance_json))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][1]


def _rect_center(rect: list[int]) -> tuple[float, float]:
    if not rect or len(rect) < 4:
        return (0.0, 0.0)
    return ((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)


def _rects_equal(left, right) -> bool:
    if left is None or right is None:
        return False
    try:
        return list(left) == list(right)
    except Exception:
        return False


def _collect_label_items(all_texts: list[dict], label_text: str) -> list[dict]:
    """从 all_texts 里收集所有 `text == label_text` 的项（去重相同 rect）。"""
    if not isinstance(all_texts, list):
        return []
    out: list[dict] = []
    seen_rects: set[tuple[int, int, int, int]] = set()
    for item in all_texts:
        if not isinstance(item, dict):
            continue
        if (item.get("text") or "").strip() != label_text:
            continue
        rect = item.get("rect") or [0, 0, 0, 0]
        key = tuple(rect[:4])
        if key in seen_rects:
            continue
        seen_rects.add(key)
        out.append(item)
    return out


def _pick_main_card_label(
    labels: list[dict],
    *,
    anchor_rect: list[int] | None,
) -> dict | None:
    """多个同名 label 时，按到 anchor_rect（通常是 balance_label_rect）的几何距离选最近一个。

    没给 anchor_rect 就保持原顺序（兼容老测试，里面通常只有 1 个 label）。
    """
    if not labels:
        return None
    if anchor_rect is None:
        return labels[0]
    anchor_cx, anchor_cy = _rect_center(anchor_rect)
    def _dist2(item: dict) -> float:
        cx, cy = _rect_center(item.get("rect") or [0, 0, 0, 0])
        return (cx - anchor_cx) ** 2 + (cy - anchor_cy) ** 2
    return min(labels, key=_dist2)


class _AmbiguousAmounts(Exception):
    """同卡片几何内找到多个不同金额候选；调用方应 fail-closed。"""


def _find_amount_same_card(
    all_texts: list[dict],
    label_item: dict,
    *,
    exclude_rects: list[list[int]] | None = None,
) -> tuple[Decimal, str] | None:
    """以 `label_item` 为锚，在同卡片几何范围内寻找唯一金额。

    同卡片约束：
      - 水平：金额中心 x 与 label 中心 x 距离 ≤ _ASSET_CARD_HORIZONTAL_TOL
      - 垂直：金额完全在 label 上方且 gap ≤ _ASSET_CARD_VERTICAL_GAP_MAX，
              或完全在下方且 gap ≤ _ASSET_CARD_VERTICAL_GAP_MAX
    `exclude_rects` 用于排除已知非分项金额（例如「人民币总资产(元)」的主金额 rect）。

    返回：
      (Decimal, "above-label")   —— 金额唯一，且在 label 上方
      (Decimal, "below-label")   —— 金额唯一，且在 label 下方
      None                       —— 同卡片几何内没有任何金额候选
    抛 _AmbiguousAmounts：候选数 ≥ 2 且 Decimal 值不全相同。
    """
    if not isinstance(all_texts, list) or not isinstance(label_item, dict):
        return None
    label_rect = label_item.get("rect") or [0, 0, 0, 0]
    if len(label_rect) < 4:
        return None
    label_cx, _ = _rect_center(label_rect)
    label_top, label_bottom = label_rect[1], label_rect[3]
    excludes = exclude_rects or []

    above: list[tuple[float, float, Decimal, list[int]]] = []
    below: list[tuple[float, float, Decimal, list[int]]] = []

    for item in all_texts:
        if not isinstance(item, dict) or item is label_item:
            continue
        text = item.get("text") or ""
        if not text:
            continue
        match = _MONEY_RE.search(text)
        if not match:
            continue
        rect = item.get("rect") or [0, 0, 0, 0]
        if len(rect) < 4:
            continue
        if any(_rects_equal(rect, ex) for ex in excludes):
            continue
        cx = (rect[0] + rect[2]) / 2
        dx = abs(cx - label_cx)
        if dx > _ASSET_CARD_HORIZONTAL_TOL:
            continue
        amount_top, amount_bottom = rect[1], rect[3]
        try:
            amount = parse_balance_text(match.group(0))
        except (InvalidOperation, ValueError):
            continue
        if amount_bottom <= label_top:  # 金额在 label 上方
            gap = label_top - amount_bottom
            if 0 <= gap <= _ASSET_CARD_VERTICAL_GAP_MAX:
                above.append((gap, dx, amount, list(rect)))
        elif amount_top >= label_bottom:  # 金额在 label 下方
            gap = amount_top - label_bottom
            if 0 <= gap <= _ASSET_CARD_VERTICAL_GAP_MAX:
                below.append((gap, dx, amount, list(rect)))
        # 与 label 矩形纵向有重叠的金额（罕见）一律不接受，避免误把 label 内的数字当金额

    if not above and not below:
        return None

    distinct_amounts: set[Decimal] = set()
    for _gap, _dx, amt, _r in above + below:
        distinct_amounts.add(amt)
    if len(distinct_amounts) > 1:
        raise _AmbiguousAmounts(
            f"同卡片几何内找到 {len(distinct_amounts)} 个不同金额: {sorted(distinct_amounts)}"
        )

    # 同一个金额（可能来自 above 与 below 两个 UIA 节点）：上方优先（U-BANK 工作台实测布局）。
    if above:
        above.sort(key=lambda c: (c[0], c[1]))
        return (above[0][2], "above-label")
    below.sort(key=lambda c: (c[0], c[1]))
    return (below[0][2], "below-label")


def workbench_deposit_check(
    all_texts: list[dict],
    expected_balance: Decimal,
    *,
    balance_label_rect: list[int] | None = None,
    balance_amount_rect: list[int] | None = None,
) -> tuple[str, Decimal | None, str | None]:
    """workbench 上证明「存款金额」== 「人民币总资产(元)」。

    在多个同名 `存款` label 候选中，优先挑离 `balance_label_rect`（总资产主标签）
    几何最近的一条 —— 那才是主资产卡片上的 存款。然后在该 label 的同卡片几何内找唯一金额。

    返回:
      ("match",     deposit_decimal, "above-label"|"below-label") —— 存款 == 总资产
      ("mismatch",  deposit_decimal, "above-label"|"below-label") —— 存款 != 总资产
      ("ambiguous", None,            None)                        —— 多个不同金额候选
      ("not_found", None,            None)                        —— 找不到 存款 label 或其同卡片金额
    """
    label_items: list[dict] = []
    for candidate in _DEPOSIT_LABEL_CANDIDATES:
        label_items.extend(_collect_label_items(all_texts, candidate))
    if not label_items:
        return ("not_found", None, None)

    chosen_label = _pick_main_card_label(label_items, anchor_rect=balance_label_rect)
    if chosen_label is None:
        return ("not_found", None, None)

    excludes: list[list[int]] = []
    if balance_amount_rect:
        excludes.append(list(balance_amount_rect))
    try:
        result = _find_amount_same_card(all_texts, chosen_label, exclude_rects=excludes)
    except _AmbiguousAmounts:
        return ("ambiguous", None, None)
    if result is None:
        return ("not_found", None, None)

    deposit, source = result
    if deposit == expected_balance:
        return ("match", deposit, source)
    return ("mismatch", deposit, source)


def workbench_non_cash_assets_nonzero(
    all_texts: list[dict],
    *,
    balance_label_rect: list[int] | None = None,
    balance_amount_rect: list[int] | None = None,
) -> list[tuple[str, Decimal, str]]:
    """扫描 workbench 上 `理财` / `票据` 等非现金资产标签的同卡片金额。

    返回所有 **非零** 命中：[(label_text, decimal_amount, source), ...]。空列表表示
    全部为 0（或没有这些标签 / 没法唯一识别）。调用方据此决定是否 fail-closed。
    """
    out: list[tuple[str, Decimal, str]] = []
    excludes: list[list[int]] = []
    if balance_amount_rect:
        excludes.append(list(balance_amount_rect))
    zero = Decimal("0")
    seen: set[tuple] = set()
    for label_text in _NON_CASH_LABEL_CANDIDATES:
        label_items = _collect_label_items(all_texts, label_text)
        if not label_items:
            continue
        chosen_label = _pick_main_card_label(label_items, anchor_rect=balance_label_rect)
        if chosen_label is None:
            continue
        try:
            r = _find_amount_same_card(all_texts, chosen_label, exclude_rects=excludes)
        except _AmbiguousAmounts:
            # 该非现金资产标签找到多个不同金额候选，自身就视为异常布局，归入 fail-closed 信号。
            out.append((label_text, Decimal("-1"), "ambiguous"))
            continue
        if r is None:
            continue
        amount, source = r
        if amount == zero:
            continue
        # 同一标签可能有多个变体（"理财" / "理财(元)"）映射到同一金额 rect，避免重复报。
        key = (label_text.split("(")[0].split("（")[0], amount, source)
        if key in seen:
            continue
        seen.add(key)
        out.append((label_text, amount, source))
    return out


def validate_balance_payload(
    payload: dict,
    *,
    required_company: str,
    audit_lines: list[str] | None = None,
) -> Decimal:
    """对 query_balance.py 写出的 balance.json 做严格校验，返回 Decimal 余额。

    fail-closed 触发（任一发生都抛 FailClosed）：
    - `result.status` 非 OK
    - `page_type` 不在 {"home","workbench"}
    - `balance_label` 不在白名单（或与 page_type 不配对）
    - `company` 为空 / 与 required_company 不严格相等
    - `balance_decimal` 缺失 / 解析为 Decimal 失败 / 解析后为负
    - home 路径：account_count 缺失 / 非整数 / != 1
    - workbench 路径：找不到「存款」标签或其同卡片金额；同卡片几何内有多个不同金额候选；
      存款金额 != 人民币总资产；或 理财 / 票据 等非现金资产同卡片金额非零

    audit_lines（如果给）会被逐行 append 校验过程的关键事实，方便 runtime 日志和单元测试共用。
    """

    def _emit(line: str) -> None:
        if audit_lines is not None:
            audit_lines.append(line)

    if not isinstance(payload, dict):
        raise FailClosed(EXIT_BALANCE_READ_FAILED, "balance.json 顶层不是 object")

    result = payload.get("result") or {}
    all_texts = payload.get("all_texts") or []

    status = result.get("status")
    page_type = (result.get("page_type") or "").strip()
    company = (result.get("company") or "").strip()
    balance_label = (result.get("balance_label") or "").strip()
    balance_raw = result.get("balance") or ""
    balance_decimal_text = result.get("balance_decimal")
    account_count_raw = result.get("account_count")
    attempt = result.get("attempt") or ""

    _emit(f"[校验] attempt={attempt} status={status} page_type={page_type}")
    _emit(f"[校验] company={company}")
    _emit(f"[校验] balance_label={balance_label}")
    _emit(
        f"[校验] balance(原文)={balance_raw} "
        f"balance_decimal={balance_decimal_text} account_count={account_count_raw}"
    )

    if status != "OK":
        raise FailClosed(EXIT_BALANCE_READ_FAILED, f"余额读取 status 非 OK: {status}")
    if page_type not in ("home", "workbench"):
        raise FailClosed(
            EXIT_BALANCE_AMBIGUOUS,
            f"page_type 必须是 home 或 workbench，实际 page_type=[{page_type}]",
        )
    if balance_label not in BALANCE_LABEL_WHITELIST:
        raise FailClosed(
            EXIT_BALANCE_AMBIGUOUS,
            f"balance_label 不在白名单（允许 {BALANCE_LABEL_WHITELIST}），实际 [{balance_label}]",
        )
    if page_type == "home" and balance_label != HOME_BALANCE_LABEL:
        raise FailClosed(
            EXIT_BALANCE_AMBIGUOUS,
            f"page_type=home 但 balance_label=[{balance_label}] 不是 [{HOME_BALANCE_LABEL}]",
        )
    if page_type == "workbench" and balance_label != WORKBENCH_BALANCE_LABEL:
        raise FailClosed(
            EXIT_BALANCE_AMBIGUOUS,
            f"page_type=workbench 但 balance_label=[{balance_label}] 不是 [{WORKBENCH_BALANCE_LABEL}]",
        )

    expected_company = (required_company or "").strip()
    if not expected_company:
        raise FailClosed(
            EXIT_BALANCE_AMBIGUOUS, "调用方未提供期望公司名（required_company 为空）"
        )
    if not company:
        raise FailClosed(EXIT_BALANCE_AMBIGUOUS, "balance.json 中 company 为空，无法确认登录用户")
    if company != expected_company:
        raise FailClosed(
            EXIT_BALANCE_AMBIGUOUS,
            f"company 不一致：读到 [{company}]，期望 [{expected_company}]",
        )

    if not balance_decimal_text:
        raise FailClosed(EXIT_BALANCE_AMBIGUOUS, "balance_decimal 为空，余额格式异常")
    try:
        balance = parse_balance_text(balance_decimal_text)
    except (InvalidOperation, ValueError) as exc:
        raise FailClosed(EXIT_BALANCE_AMBIGUOUS, f"余额无法解析为 Decimal: {exc}")
    if balance < 0:
        raise FailClosed(EXIT_BALANCE_AMBIGUOUS, f"余额为负数: {balance}")

    if page_type == "home":
        if account_count_raw is None or str(account_count_raw).strip() == "":
            raise FailClosed(
                EXIT_BALANCE_AMBIGUOUS,
                "首页未读到 账户总数(个)，无法确认 002 登录名下唯一付款账户",
            )
        try:
            account_count = int(str(account_count_raw).strip())
        except ValueError:
            raise FailClosed(EXIT_BALANCE_AMBIGUOUS, f"账户总数非整数: {account_count_raw}")
        if account_count != 1:
            raise FailClosed(
                EXIT_BALANCE_AMBIGUOUS,
                f"002 登录名下的账户数 = {account_count}，无法唯一锁定付款账户（要求 == 1）",
            )
        _emit(f"[校验] home + account_count=1 通过；余额 balance={balance}")
        return balance

    # page_type == "workbench"
    _emit(
        "[校验] workbench 使用人民币总资产作为余额来源；已校验 company；未读取到账户总数。"
    )

    label_control = result.get("label_control") if isinstance(result.get("label_control"), dict) else None
    balance_control = result.get("balance_control") if isinstance(result.get("balance_control"), dict) else None
    balance_label_rect = label_control.get("rect") if label_control else None
    balance_amount_rect = balance_control.get("rect") if balance_control else None
    _emit(
        f"[校验] workbench 锚点：label_control.rect={balance_label_rect} "
        f"balance_control.rect={balance_amount_rect}"
    )

    deposit_status, deposit_amount, deposit_source = workbench_deposit_check(
        all_texts,
        balance,
        balance_label_rect=balance_label_rect,
        balance_amount_rect=balance_amount_rect,
    )

    if deposit_status == "ambiguous":
        raise FailClosed(
            EXIT_BALANCE_AMBIGUOUS,
            (
                "workbench「存款」标签同卡片几何内发现多个不同金额候选，无法唯一判断；"
                "拒绝调拨，避免把别的卡片或别的资产分项误当存款"
            ),
        )
    if deposit_status == "not_found":
        raise FailClosed(
            EXIT_BALANCE_AMBIGUOUS,
            (
                "workbench 未能定位「存款(元)」标签或读出其同卡片金额：无法证明人民币总资产"
                "全部为可用存款，拒绝调拨；可能含 理财 / 票据 / 信用证 / 信用卡 等非现金资产。"
                "若工作台版本变更导致存款标签缺失，请人工核对后再人工触发本流程。"
            ),
        )
    if deposit_status == "mismatch":
        raise FailClosed(
            EXIT_BALANCE_AMBIGUOUS,
            (
                f"workbench 存款={deposit_amount}（来源={deposit_source}）与 "
                f"人民币总资产={balance} 不一致，无法证明人民币总资产全部为可用存款，"
                "拒绝调拨；可能含 理财 / 票据 / 信用证 / 信用卡 等非现金资产"
            ),
        )
    # match
    _emit(
        f"[校验] workbench 存款金额定位来源={deposit_source}；"
        f"存款={deposit_amount} == 人民币总资产={balance}"
    )

    # 防御性深一层：理财 / 票据 等非现金资产必须全部为 0；只要发现一项非零（或同卡片
    # 几何内出现 ambiguous 金额）就 fail-closed，即使数学上 存款==总资产 也不放行 ——
    # 这种情况通常意味着工作台布局非预期。
    non_cash = workbench_non_cash_assets_nonzero(
        all_texts,
        balance_label_rect=balance_label_rect,
        balance_amount_rect=balance_amount_rect,
    )
    if non_cash:
        details = ", ".join(
            f"{lbl}={amt}（{src}）" for lbl, amt, src in non_cash
        )
        raise FailClosed(
            EXIT_BALANCE_AMBIGUOUS,
            (
                f"workbench 检测到非现金资产非零或布局异常：{details}；"
                "虽然存款数值与总资产匹配，但同时存在其它非零资产，拒绝调拨"
            ),
        )
    _emit("[校验] workbench 理财 / 票据 等非现金资产同卡片金额均为 0 或未出现，通过")
    return balance


def read_balance_via_subprocess(
    env: dict[str, str],
    log: TeeLogger,
    run_dir: Path,
    *,
    required_company: str,
    page_arg: str,
) -> Decimal:
    if not QUERY_BALANCE_SCRIPT.exists():
        raise FailClosed(
            EXIT_BALANCE_READ_FAILED, f"未找到 query_balance.py: {QUERY_BALANCE_SCRIPT}"
        )
    if page_arg not in BALANCE_PAGE_CHOICES:
        raise FailClosed(
            EXIT_BALANCE_READ_FAILED,
            f"query_balance --page 取值非法 [{page_arg}]，允许 {BALANCE_PAGE_CHOICES}",
        )

    started = time.time()
    log.log(
        f"[余额] 调起子进程: {QUERY_BALANCE_SCRIPT} --page {page_arg} --save-all-texts"
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(QUERY_BALANCE_SCRIPT),
            "--page",
            page_arg,
            "--save-all-texts",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    (run_dir / "query_balance.stdout.log").write_text(proc.stdout or "", encoding="utf-8")
    log.log(f"[余额] query_balance 退出码: {proc.returncode}")
    if proc.returncode != 0:
        raise FailClosed(
            EXIT_BALANCE_READ_FAILED, f"query_balance 子进程失败 rc={proc.returncode}"
        )

    balance_json_path = latest_balance_json(started)
    if balance_json_path is None:
        raise FailClosed(
            EXIT_BALANCE_READ_FAILED, "未在 query_balance/runs/ 下找到本次 balance.json"
        )
    log.log(f"[余额] 读取结果文件: {balance_json_path}")

    try:
        shutil.copyfile(balance_json_path, run_dir / "query_balance.json")
    except Exception as exc:
        log.log(f"[余额] 复制结果文件失败（不致命）: {exc}")

    with balance_json_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    audit: list[str] = []
    try:
        balance = validate_balance_payload(
            payload, required_company=required_company, audit_lines=audit
        )
    finally:
        for line in audit:
            log.log(line)
    return balance


def write_bank_form(payee: dict[str, str], amount: Decimal, log: TeeLogger, run_dir: Path) -> Path:
    BANK_FORM_PATH.parent.mkdir(parents=True, exist_ok=True)
    if BANK_FORM_PATH.exists():
        try:
            shutil.copyfile(BANK_FORM_PATH, run_dir / "bank_form_prev_backup.json")
            log.log(f"[bank_form] 已备份旧 bank_form.json 到 {run_dir / 'bank_form_prev_backup.json'}")
        except Exception as exc:
            raise FailClosed(EXIT_BANK_FORM_BACKUP_FAILED, f"备份旧 bank_form.json 失败: {exc}")

    biz_ref = f"LCY-{_now_stamp()}"
    form = {
        "付款单位名称": payee["付款单位名称"],
        "招行登录名": LOGIN_ACCOUNT_NAME,
        "CMB_LOGIN_ACCOUNT_NAME": LOGIN_ACCOUNT_NAME,
        "收方账号": payee["收方账号"],
        "收方户名": payee["收方户名"],
        "开户银行": payee["开户银行"],
        "支行名称": payee["支行名称"],
        "金额": str(amount),
        "用途": payee["用途"],
        "业务参考号": biz_ref,
        "_来源": "来参缘调拨 runner",
        "_runner": str(RUNNER_DIR.name),
    }
    with BANK_FORM_PATH.open("w", encoding="utf-8") as f:
        json.dump(form, f, ensure_ascii=False, indent=2)
    log.log(f"[bank_form] 已写入 {BANK_FORM_PATH}")
    log.log(f"[bank_form] 金额={form['金额']} 业务参考号={biz_ref}")

    snapshot = run_dir / "bank_form_used.json"
    with snapshot.open("w", encoding="utf-8") as f:
        json.dump(form, f, ensure_ascii=False, indent=2)
    return snapshot


def invoke_zhidan(env: dict[str, str], log: TeeLogger, run_dir: Path) -> int:
    if not ZHIDAN_ENTRY.exists():
        raise FailClosed(EXIT_BALANCE_READ_FAILED, f"未找到制单入口: {ZHIDAN_ENTRY}")
    log.log(f"[制单] 调起 {ZHIDAN_ENTRY}")
    log.log(
        f"[制单] 子进程 env: CMB_LOGIN_ACCOUNT_NAME=[{env.get('CMB_LOGIN_ACCOUNT_NAME')}] "
        f"USB_HUB_FORCE_PORT={env.get('USB_HUB_FORCE_PORT')} "
        f"ZHIDAN_ALLOW_FORCE_USB_PORT={env.get('ZHIDAN_ALLOW_FORCE_USB_PORT')} "
        f"ZHIDAN_TEST_MODE={env.get('ZHIDAN_TEST_MODE')}"
    )
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(ZHIDAN_ENTRY)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    (run_dir / "zhidan.stdout.log").write_text(proc.stdout or "", encoding="utf-8")
    log.log(f"[制单] 退出码: {proc.returncode}")
    return proc.returncode


def determine_production_intent(log: TeeLogger) -> bool:
    """父进程必须显式 `LAICANYUAN_TRANSFER_PRODUCTION=1` 才允许生产模式。"""
    raw = (os.environ.get(PRODUCTION_GATE_ENV) or "").strip()
    if raw == "1":
        log.log(f"[模式] {PRODUCTION_GATE_ENV}=1 -> 生产模式（子进程 ZHIDAN_TEST_MODE=0）")
        return True
    if raw not in ("", "0"):
        log.log(
            f"[模式] {PRODUCTION_GATE_ENV} 取值非法 [{raw}]，按测试模式处理"
        )
    elif os.environ.get("ZHIDAN_TEST_MODE", "1").strip() == "0":
        log.log(
            "[模式] 检测到父进程 ZHIDAN_TEST_MODE=0 但 "
            f"{PRODUCTION_GATE_ENV} 未设为 1，runner 仍把子进程强制改回 1（测试模式）"
        )
    else:
        log.log(f"[模式] {PRODUCTION_GATE_ENV} 未设置 -> 测试模式（子进程 ZHIDAN_TEST_MODE=1）")
    return False


def main_impl(args: argparse.Namespace, log: TeeLogger, run_dir: Path) -> int:
    log.log("=" * 60)
    log.log("来参缘调拨 runner 启动")
    log.log(f"  run_dir: {run_dir}")
    log.log(f"  bank_form 路径: {BANK_FORM_PATH}")
    log.log(f"  query_balance: {QUERY_BALANCE_SCRIPT}")
    log.log(f"  制单 entry: {ZHIDAN_ENTRY}")
    log.log("=" * 60)

    payee = load_payee_config(log)
    production = determine_production_intent(log)

    if args.dry_run_amount_only:
        log.log("[--dry-run-amount-only] 只算金额，不开 USB 不开 U-BANK 不写 bank_form")
        balance = parse_balance_text(args.fake_balance)
        log.log(f"[--dry-run-amount-only] 给定余额 balance={balance}")
        transferable = compute_transferable(balance)
        if transferable < 1:
            raise FailClosed(EXIT_AMOUNT_TOO_SMALL, f"按万元取整后可调拨金额异常: {transferable}")
        log.log(f"[--dry-run-amount-only] 按 {TRANSFER_UNIT_RMB} 元为单位向下取整 -> 可调拨 {transferable} 元")
        return EXIT_OK

    cmb_usb_hub_port = resolve_laicanyuan_usb_hub_port()
    log.log(f"[USB] 来参缘招行 UKey 固定端口: {cmb_usb_hub_port}")
    env = build_subprocess_env(production, cmb_usb_hub_port)

    page_arg = (os.environ.get("LAICANYUAN_BALANCE_PAGE") or DEFAULT_BALANCE_PAGE).strip() or DEFAULT_BALANCE_PAGE
    if page_arg not in BALANCE_PAGE_CHOICES:
        raise FailClosed(
            EXIT_BALANCE_READ_FAILED,
            f"LAICANYUAN_BALANCE_PAGE 取值非法 [{page_arg}]，允许 {BALANCE_PAGE_CHOICES}",
        )
    log.log(f"[余额] LAICANYUAN_BALANCE_PAGE 解析为 --page {page_arg}（默认 {DEFAULT_BALANCE_PAGE}）")

    try:
        usb_hub_only(cmb_usb_hub_port, log)
        try:
            balance = read_balance_via_subprocess(
                env,
                log,
                run_dir,
                required_company=payee["付款单位名称"],
                page_arg=page_arg,
            )
        finally:
            kill_ubank(log)
            usb_hub_all_off(log)

        log.log(f"[余额识别] 可用余额 balance = {balance} 元")
        log.log(f"[余额识别] 金额规则: 按 {TRANSFER_UNIT_RMB} 元为单位向下取整转出，万元以下零头留在 002 账户")
        transferable = compute_transferable(balance)
        log.log(f"[余额识别] 计算可调拨金额 transferable = {transferable} 元（向下取整到万元）")
        if transferable < 1:
            raise FailClosed(
                EXIT_AMOUNT_TOO_SMALL,
                f"按万元取整后可调拨金额异常，停止：transferable={transferable} balance={balance}",
            )

        write_bank_form(payee, transferable, log, run_dir)

    except FailClosed:
        raise
    except Exception as exc:
        log.log(f"[runner] 余额读取阶段未预期异常: {exc}")
        kill_ubank(log)
        usb_hub_all_off(log)
        raise FailClosed(EXIT_BALANCE_READ_FAILED, f"余额读取阶段失败: {exc}")

    if args.emit_bank_form_only:
        log.log("[--emit-bank-form-only] 已写入 bank_form.json，跳过 制单.py 调用")
        return EXIT_OK

    rc = invoke_zhidan(env, log, run_dir)
    return rc


def main() -> int:
    parser = argparse.ArgumentParser(description="来参缘 → 兴业 内部调拨 runner")
    parser.add_argument(
        "--emit-bank-form-only",
        action="store_true",
        help="读余额、写 bank_form.json 后停止，不调用 制单.py（用于联调）",
    )
    parser.add_argument(
        "--dry-run-amount-only",
        action="store_true",
        help="只跑收方配置加载 + 金额计算单元，不动 USB Hub / U-BANK / bank_form。需要配合 --fake-balance",
    )
    parser.add_argument(
        "--fake-balance",
        default="0",
        help="仅 --dry-run-amount-only 使用：模拟余额字符串（例 10000.50）",
    )
    args = parser.parse_args()

    run_dir = _ensure_runs_dir()
    log = TeeLogger(run_dir)
    try:
        rc = _run_with_lock(args, log, run_dir)
        log.log(f"runner 退出码: {rc}")
        return rc
    except FailClosed as exc:
        log.log("=" * 60)
        log.log(f"[fail-closed] {exc}")
        log.log(f"[fail-closed] 退出码: {exc.exit_code}")
        log.log("=" * 60)
        return exc.exit_code
    except KeyboardInterrupt:
        log.log("[runner] 被 Ctrl+C 中断")
        return 130
    finally:
        log.close()


def _import_global_lock_module():
    """延迟 import 财务\\公共\\locks\\bank_automation_lock。

    成功时返回 (module, default_path)；模块/目录不存在时返回 (None, None)。
    调用方必须 fail-closed：这把锁是与 M3 24 小时监控互斥的安全边界，
    缺失时不能继续打开银行或切 USBHub。
    """
    if not _GLOBAL_LOCK_MODULE_DIR.exists():
        return None, None
    if str(_GLOBAL_LOCK_MODULE_DIR) not in sys.path:
        sys.path.insert(0, str(_GLOBAL_LOCK_MODULE_DIR))
    try:
        import bank_automation_lock as mod  # type: ignore
    except Exception:
        return None, None
    try:
        default_path = mod.default_lock_path()
    except Exception:
        default_path = None
    return mod, default_path


def _run_with_lock(args: argparse.Namespace, log: TeeLogger, run_dir: Path) -> int:
    """用单实例锁 + 全局银行自动化锁包住实际的主流程。

    顺序：
      1. 先拿"来参缘 runner 本地单实例锁"，防止本项目自己被并发触发。
      2. 再拿"跨项目全局银行自动化锁"，与 M3 银行制单路由互斥。

    --dry-run-amount-only 不接触 USB Hub / U-BANK / bank_form，跳过这两把锁，
    确保即便有真实运行在跑，也能并行做纯函数级别的金额校验排障。
    """
    if args.dry_run_amount_only:
        log.log("[lock] --dry-run-amount-only 跳过单实例锁与全局银行自动化锁")
        return main_impl(args, log, run_dir)

    my_pid = os.getpid()
    started_at = datetime.now().isoformat(timespec="seconds")
    log.log(f"[lock] pid={my_pid} 准备获取单实例锁: {LOCK_PATH}")
    action, prev = acquire_lock(
        LOCK_PATH,
        my_pid=my_pid,
        run_dir=run_dir,
        started_at=started_at,
    )
    if action == "stole_stale":
        prev_pid = prev.get("pid") if isinstance(prev, dict) else None
        prev_started = prev.get("started_at") if isinstance(prev, dict) else None
        log.log(
            f"[lock] 检测到 stale lock 并已清理：旧 pid={prev_pid} started_at={prev_started}"
        )
    else:
        log.log("[lock] 已获取单实例锁")

    try:
        global_mod, global_lock_path = _import_global_lock_module()
        if global_mod is None:
            log.log(
                "[global-lock] 未找到 公共\\locks\\bank_automation_lock 模块，"
                "无法与 M3/其它银行入口互斥；本轮 fail-closed，未启动 U-BANK / USB Hub。"
            )
            raise FailClosed(
                EXIT_GLOBAL_LOCK_BUSY,
                "未找到全局银行自动化锁模块，拒绝在无互斥保护下运行",
            )

        log.log(f"[global-lock] 准备获取全局银行自动化锁: {global_lock_path}")
        try:
            g_action, g_prev = global_mod.acquire(
                owner=global_mod.OWNER_LAICANYUAN,
                purpose="来参缘定时调拨",
                my_pid=my_pid,
                started_at=started_at,
                run_dir=str(run_dir),
            )
        except global_mod.LockBusy as exc:
            log.log(
                "[global-lock] 银行自动化忙，本轮跳过，未启动 U-BANK / USB Hub。"
            )
            log.log(f"[global-lock] 现有 owner: {exc.prev}")
            raise FailClosed(EXIT_GLOBAL_LOCK_BUSY, f"全局银行自动化锁忙: {exc}")
        except global_mod.LockTakeoverFailed as exc:
            log.log(f"[global-lock] stale 全局锁接管失败：{exc}")
            raise FailClosed(EXIT_GLOBAL_LOCK_BUSY, f"全局银行自动化锁接管失败: {exc}")

        if g_action == "stole_stale":
            log.log(
                f"[global-lock] 检测到 stale 全局锁并已清理：prev={g_prev}"
            )
        else:
            log.log("[global-lock] 已获取全局银行自动化锁")

        try:
            return main_impl(args, log, run_dir)
        finally:
            if global_mod.release(
                owner=global_mod.OWNER_LAICANYUAN, my_pid=my_pid
            ):
                log.log(f"[global-lock] 已释放全局银行自动化锁 (pid={my_pid})")
            else:
                log.log(
                    "[global-lock] 释放全局银行自动化锁 noop（锁文件已不属于本进程或已被外部清理）"
                )
    finally:
        if release_lock(LOCK_PATH, my_pid=my_pid):
            log.log(f"[lock] 已释放单实例锁 (pid={my_pid})")
        else:
            log.log(f"[lock] 释放单实例锁 noop（锁文件已不属于本进程或已被外部删除）")


if __name__ == "__main__":
    raise SystemExit(main())
