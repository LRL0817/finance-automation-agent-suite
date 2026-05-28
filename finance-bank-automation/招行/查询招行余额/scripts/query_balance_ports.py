# -*- coding: utf-8 -*-
"""Read CMB U-BANK balances by sweeping confirmed CMB USB Hub ports.

This script is read-only for U-BANK. It powers one USB Hub port at a time,
logs in, reads the visible company/balance via UIA, saves per-run diagnostics,
then closes U-BANK and powers off the hub before moving on.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import query_balance as balance_query

SKILL_ROOT = Path(__file__).resolve().parents[1]


def _desktop_path(*parts: str) -> Path:
    return Path.home() / "Desktop" / Path(*parts)


def _first_existing_path(*candidates: Path | str | None) -> Path:
    fallback: Path | None = None
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if fallback is None:
            fallback = path
        if path.exists():
            return path
    if fallback is None:
        raise RuntimeError("未配置可用路径候选")
    return fallback


USB_HUB_CTRL = _first_existing_path(
    os.getenv("USB_HUB_CTRL"),
    os.getenv("ZHIDAN_USB_HUB_CTRL"),
    SKILL_ROOT.parent.parent / "公共" / "usbhub" / "usbhub" / "多口USB控制器软件以及驱动" / "hub_ctrl.py",
    _desktop_path("财务", "公共", "usbhub", "usbhub", "多口USB控制器软件以及驱动", "hub_ctrl.py"),
    _desktop_path("usbhub", "usbhub", "多口USB控制器软件以及驱动", "hub_ctrl.py"),
)
USB_HUB_COM = os.getenv("USB_HUB_COM", "COM3")
DEFAULT_SETTLE_SECONDS = 2

PORT_LABELS = {
    1: "云链跳动",
    2: "云链跃动",
    3: "云链悠动",
    4: "云链智动",
    5: "云链逸动",
    6: "云链灵动",
    7: "云链讯动",
    8: "云链慧动",
    9: "云链炫动",
    12: "蒙特-招行",
    16: "得鲜-招行(001/002同UKey)",
    18: "来参缘-招行(001/002同UKey)",
    19: "云炫农-招行",
}
DEFAULT_SWEEP_PORTS = sorted(PORT_LABELS)
ALLOWED_PORTS = set(PORT_LABELS)


class SweepError(RuntimeError):
    pass


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _run_hub_command(args: list[str], timeout: int = 45) -> subprocess.CompletedProcess[str]:
    if not USB_HUB_CTRL.exists():
        raise SweepError(f"未找到 USB Hub 控制脚本: {USB_HUB_CTRL}")

    cmd = [sys.executable, str(USB_HUB_CTRL), *args, "--com", USB_HUB_COM]
    proc = subprocess.run(
        cmd,
        timeout=timeout,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if proc.stdout:
        print(proc.stdout.strip())
    if proc.returncode != 0:
        raise SweepError(f"USB Hub 命令失败 rc={proc.returncode}: {' '.join(args)}")
    return proc


def switch_to_port(port: int, settle_seconds: int) -> None:
    print(f"切换 USB Hub：仅打开 {port} 口，等待 {settle_seconds}s")
    _run_hub_command(["only", str(port), "--settle", str(settle_seconds)])


def turn_all_ports_off() -> None:
    print("关闭 USB Hub 全部端口")
    _run_hub_command(["all-off"])


def kill_ubank() -> None:
    """Close U-BANK without touching any in-app transfer flow."""
    subprocess.run(
        ["taskkill", "/F", "/T", "/IM", "Firmbank.exe"],
        timeout=20,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1)


def _load_balance_payload(run_dir: Path) -> dict[str, Any]:
    try:
        with open(run_dir / "balance.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def query_one_port(
    port: int,
    *,
    page: str,
    settle_seconds: int,
    not_found_retries: int,
    not_found_wait: int,
    save_all_texts: bool,
    keep_ubank_open: bool,
) -> dict[str, Any]:
    label = PORT_LABELS.get(port, "")
    started = datetime.now().isoformat(timespec="seconds")
    record: dict[str, Any] = {
        "port": port,
        "expected_keyword": label,
        "started_at": started,
        "status": "ERROR",
        "company": "",
        "balance": "",
        "balance_decimal": None,
        "page_type": "",
        "run_dir": "",
        "screenshot": "",
        "error": "",
    }

    print("=" * 72)
    print(f"[{port}口] 开始查询，预期关键词: {label or '-'}")
    print("=" * 72)

    main_win = None
    try:
        kill_ubank()
        turn_all_ports_off()
        switch_to_port(port, settle_seconds)

        main_win, result, texts = balance_query.query_balance(page=page, no_login=False)
        for retry in range(1, not_found_retries + 1):
            if result.get("status") == "OK":
                break
            print(f"[{port}口] 暂未读到余额，等待页面加载后重读 {retry}/{not_found_retries}...")
            time.sleep(not_found_wait)
            main_win = balance_query.get_main_window() or main_win
            result, texts = balance_query.read_balance_from_current_window(main_win)
            result["attempt"] = f"retry_current_{retry}"

        run_dir = balance_query.save_outputs(main_win, result, texts, save_all_texts)
        payload = _load_balance_payload(run_dir)

        record.update(
            {
                "status": result.get("status", ""),
                "company": result.get("company", ""),
                "balance": result.get("balance", ""),
                "balance_decimal": result.get("balance_decimal"),
                "page_type": result.get("page_type", ""),
                "attempt": result.get("attempt", ""),
                "run_dir": str(run_dir),
                "screenshot": payload.get("screenshot", ""),
            }
        )
        print(
            f"[{port}口] STATUS={record['status']} "
            f"COMPANY={record['company']} BALANCE={record['balance']}"
        )
    except Exception as exc:
        record["error"] = str(exc)
        print(f"[{port}口] 查询失败: {exc}")
    finally:
        record["finished_at"] = datetime.now().isoformat(timespec="seconds")
        if not keep_ubank_open:
            try:
                kill_ubank()
            except Exception as exc:
                record["close_error"] = str(exc)
                print(f"[{port}口] 关闭 U-BANK 异常: {exc}")
        try:
            turn_all_ports_off()
        except Exception as exc:
            record["hub_off_error"] = str(exc)
            print(f"[{port}口] USB Hub 断电异常: {exc}")

    return record


def save_summary(records: list[dict[str, Any]], sweep_dir: Path) -> None:
    sweep_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "usb_hub_com": USB_HUB_COM,
        "usb_hub_ctrl": str(USB_HUB_CTRL),
        "records": records,
    }
    with open(sweep_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    fields = [
        "port",
        "expected_keyword",
        "status",
        "company",
        "balance",
        "balance_decimal",
        "page_type",
        "attempt",
        "run_dir",
        "screenshot",
        "error",
        "started_at",
        "finished_at",
    ]
    with open(sweep_dir / "summary.csv", "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def _parse_ports(value: str) -> list[int]:
    ports: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            ports.extend(range(int(left), int(right) + 1))
        else:
            ports.append(int(part))
    invalid = [port for port in ports if port not in ALLOWED_PORTS]
    if invalid:
        allowed = ",".join(str(port) for port in sorted(ALLOWED_PORTS))
        raise argparse.ArgumentTypeError(f"仅允许已确认的招行端口 {allowed}，收到: {invalid}")
    return list(dict.fromkeys(ports))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sweep CMB U-BANK balance from confirmed USB Hub ports.")
    parser.add_argument("--ports", type=_parse_ports, default=DEFAULT_SWEEP_PORTS, help="端口列表，如 1-9 或 12,16,18,19；10 是河南讯动/中行，不扫；11/13/14/15/17 是其它银行 UKey")
    parser.add_argument(
        "--page",
        choices=("auto", "current", "home", "workbench"),
        default="auto",
        help="余额读取页面，默认 auto。",
    )
    parser.add_argument("--settle", type=int, default=DEFAULT_SETTLE_SECONDS, help="切换 USB 口后的等待秒数。")
    parser.add_argument("--not-found-retries", type=int, default=5, help="未读到余额时，原页面等待重读次数。")
    parser.add_argument("--not-found-wait", type=int, default=3, help="每次重读前等待秒数。")
    parser.add_argument("--save-all-texts", action="store_true", help="每个口保存完整 UIA 文本。")
    parser.add_argument("--keep-ubank-open", action="store_true", help="调试用：每个口完成后不关闭 U-BANK。")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    sweep_dir = SKILL_ROOT / "runs" / f"ports_{_now_stamp()}"
    records: list[dict[str, Any]] = []

    print(f"本次巡检端口: {','.join(str(port) for port in args.ports)}")
    print(f"汇总目录: {sweep_dir}")

    try:
        for port in args.ports:
            record = query_one_port(
                port,
                page=args.page,
                settle_seconds=args.settle,
                not_found_retries=args.not_found_retries,
                not_found_wait=args.not_found_wait,
                save_all_texts=args.save_all_texts,
                keep_ubank_open=args.keep_ubank_open,
            )
            records.append(record)
            save_summary(records, sweep_dir)
    finally:
        try:
            if not args.keep_ubank_open:
                kill_ubank()
            turn_all_ports_off()
        finally:
            save_summary(records, sweep_dir)

    print("=" * 72)
    print("USBHub 余额巡检汇总")
    print("=" * 72)
    for record in records:
        print(
            f"{record['port']:>2}口 {record['expected_keyword']:<6} "
            f"{record['status']:<9} {record['company']} {record['balance']} "
            f"{record.get('error', '')}"
        )
    print(f"SUMMARY_DIR={sweep_dir}")

    return 0 if records and all(r.get("status") == "OK" for r in records) else 2


if __name__ == "__main__":
    raise SystemExit(main())
