# -*- coding: utf-8 -*-
"""Read the visible CMB U-BANK company balance from the Windows GUI."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pywinauto import Desktop

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


ZHIDAN_ROOT = _first_existing_path(
    os.getenv("ZHIDAN_ROOT"),
    SKILL_ROOT.parent / "招行制单" / "招行",
    _desktop_path("财务", "招行", "招行制单", "招行"),
    _desktop_path("招行制单", "招行"),
)

if str(ZHIDAN_ROOT) not in sys.path:
    sys.path.insert(0, str(ZHIDAN_ROOT))

from ubank_common import (  # noqa: E402
    capture_window_screenshot,
    login_ubank,
    open_ubank,
    wait_for_main_window,
)

MONEY_RE = re.compile(r"(?<!\d)-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})(?!\d)")
COMPANY_RE = re.compile(r"[^\s，,。；;：:|]+有限公司")
MAIN_TITLE_NEEDLES = ("招商银行企业银行", "U-BANK", "招商银行", "企业银行")


class BalanceQueryError(RuntimeError):
    pass


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _rect_to_list(rect: Any) -> list[int]:
    return [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]


def _center(rect_list: list[int]) -> tuple[float, float]:
    left, top, right, bottom = rect_list
    return ((left + right) / 2, (top + bottom) / 2)


def _amount_to_decimal_text(value: str) -> str | None:
    text = (value or "").replace(",", "").strip()
    try:
        return str(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def get_main_window():
    desktop = Desktop(backend="uia")
    candidates = []
    for win in desktop.windows():
        try:
            title = win.window_text()
            if not title or "联机登录" in title:
                continue
            if any(needle in title for needle in MAIN_TITLE_NEEDLES):
                candidates.append(win)
        except Exception:
            continue

    if not candidates:
        return None

    candidates.sort(key=lambda w: 0 if "招商银行企业银行" in w.window_text() else 1)
    return candidates[0]


def ensure_logged_in(no_login: bool = False):
    main_win = get_main_window()
    if main_win is not None:
        print(f"已检测到 U-BANK 主界面: {main_win.window_text()}")
        return main_win

    if no_login:
        raise BalanceQueryError("未找到已登录 U-BANK 主界面，且指定了 --no-login")

    print("未发现已登录主界面，开始打开并登录 U-BANK...")
    if open_ubank() is False:
        raise BalanceQueryError("打开 U-BANK 失败")

    time.sleep(1)
    if not login_ubank():
        raise BalanceQueryError("U-BANK 登录失败")

    main_win = wait_for_main_window(Desktop(backend="uia"))
    if not main_win:
        raise BalanceQueryError("登录后未找到 U-BANK 主界面")

    time.sleep(2)
    print(f"登录完成: {main_win.window_text()}")
    return main_win


def collect_visible_texts(main_win) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def walk(control):
        try:
            if not control.is_visible():
                return
            info = control.element_info
            name = info.name if hasattr(info, "name") and info.name else ""
            control_type = str(info.control_type) if hasattr(info, "control_type") else ""
            class_name = info.class_name if hasattr(info, "class_name") else ""
            rect = control.rectangle()
            if name and name.strip():
                rows.append(
                    {
                        "text": name.strip(),
                        "type": control_type,
                        "class": class_name,
                        "rect": _rect_to_list(rect),
                    }
                )
        except Exception:
            pass

        try:
            for child in control.children():
                walk(child)
        except Exception:
            pass

    walk(main_win)

    seen = set()
    deduped = []
    for row in rows:
        key = (row["text"], tuple(row["rect"]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def click_visible_text(main_win, target: str) -> bool:
    def walk(control) -> bool:
        try:
            info = control.element_info
            name = info.name if hasattr(info, "name") and info.name else ""
            if name.strip() == target and control.is_visible():
                rect = control.rectangle()
                control.click_input(coords=(rect.width() // 2, rect.height() // 2))
                return True
        except Exception:
            pass

        try:
            for child in control.children():
                if walk(child):
                    return True
        except Exception:
            pass
        return False

    try:
        main_win.set_focus()
    except Exception:
        pass
    return walk(main_win)


def find_company(texts: list[dict[str, Any]]) -> dict[str, Any] | None:
    matches = []
    for item in texts:
        match = COMPANY_RE.search(item["text"])
        if not match:
            continue
        company = match.group(0)
        if company.startswith("Ubank用户名"):
            continue
        row = dict(item)
        row["company"] = company
        matches.append(row)

    if not matches:
        return None

    # Prefer business-card area over bottom status bars.
    matches.sort(key=lambda item: (item["rect"][1] > 850, item["rect"][1], item["rect"][0]))
    return matches[0]


def _find_label(texts: list[dict[str, Any]], label: str) -> dict[str, Any] | None:
    exact = [item for item in texts if item["text"] == label]
    if exact:
        exact.sort(key=lambda item: (item["rect"][1], item["rect"][0]))
        return exact[0]

    contains = [item for item in texts if label in item["text"]]
    if contains:
        contains.sort(key=lambda item: (item["rect"][1], item["rect"][0]))
        return contains[0]
    return None


def _nearest_amount_below(texts: list[dict[str, Any]], label_item: dict[str, Any]) -> dict[str, Any] | None:
    label_rect = label_item["rect"]
    label_cx, _ = _center(label_rect)
    candidates = []

    for item in texts:
        match = MONEY_RE.search(item["text"])
        if not match:
            continue

        rect = item["rect"]
        if rect[1] < label_rect[1]:
            continue

        cx, _ = _center(rect)
        dy = rect[1] - label_rect[1]
        dx = abs(cx - label_cx)
        if dy > 120:
            continue
        if dx > 260:
            continue

        candidates.append((dy, dx, item, match.group(0)))

    if not candidates:
        return None

    candidates.sort(key=lambda row: (row[0], row[1]))
    _, _, item, amount = candidates[0]
    row = dict(item)
    row["amount"] = amount
    row["amount_decimal"] = _amount_to_decimal_text(amount)
    return row


def _nearest_integer_below(texts: list[dict[str, Any]], label_item: dict[str, Any]) -> str | None:
    label_rect = label_item["rect"]
    label_cx, _ = _center(label_rect)
    candidates = []
    for item in texts:
        text = item["text"].strip()
        if not text.isdigit():
            continue
        rect = item["rect"]
        if rect[1] < label_rect[1]:
            continue
        cx, _ = _center(rect)
        dy = rect[1] - label_rect[1]
        dx = abs(cx - label_cx)
        if dy <= 120 and dx <= 260:
            candidates.append((dy, dx, text))
    if not candidates:
        return None
    candidates.sort(key=lambda row: (row[0], row[1]))
    return candidates[0][2]


def extract_balance(texts: list[dict[str, Any]]) -> dict[str, Any]:
    company_item = find_company(texts)

    layouts = [
        ("home", "人民币账户实时余额合计(元)"),
        ("workbench", "人民币总资产(元)"),
    ]

    for page_type, label in layouts:
        label_item = _find_label(texts, label)
        if not label_item:
            continue

        amount_item = _nearest_amount_below(texts, label_item)
        if not amount_item:
            continue

        result = {
            "status": "OK",
            "page_type": page_type,
            "company": company_item["company"] if company_item else "",
            "company_text": company_item["text"] if company_item else "",
            "balance_label": label,
            "balance": amount_item["amount"],
            "balance_decimal": amount_item["amount_decimal"],
            "balance_text": amount_item["text"],
            "company_control": company_item,
            "label_control": label_item,
            "balance_control": amount_item,
        }

        if page_type == "home":
            account_label = _find_label(texts, "账户总数(个)")
            if account_label:
                result["account_count"] = _nearest_integer_below(texts, account_label)

        return result

    matched = [
        item
        for item in texts
        if COMPANY_RE.search(item["text"])
        or MONEY_RE.search(item["text"])
        or any(
            token in item["text"]
            for token in ("人民币", "总资产", "实时余额合计", "账户总数", "存款", "理财", "票据")
        )
    ]
    return {
        "status": "NOT_FOUND",
        "page_type": "",
        "company": company_item["company"] if company_item else "",
        "balance": "",
        "balance_decimal": None,
        "matched_texts": matched,
    }


def read_balance_from_current_window(main_win) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    texts = collect_visible_texts(main_win)
    result = extract_balance(texts)
    return result, texts


def query_balance(page: str, no_login: bool) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
    main_win = ensure_logged_in(no_login=no_login)

    attempts = []
    if page in ("current", "auto"):
        attempts.append(None)
    if page in ("home", "auto"):
        attempts.append("首页")
    if page in ("workbench", "auto"):
        attempts.append("工作台")

    last_result: dict[str, Any] | None = None
    last_texts: list[dict[str, Any]] = []

    for target in attempts:
        if target:
            print(f"尝试切换到 {target} 后读取余额...")
            click_visible_text(main_win, target)
            time.sleep(3)
            main_win = get_main_window() or main_win
        else:
            print("读取当前页面余额...")
            time.sleep(1)

        result, texts = read_balance_from_current_window(main_win)
        result["attempt"] = target or "current"
        last_result = result
        last_texts = texts
        if result.get("status") == "OK":
            return main_win, result, texts

    assert last_result is not None
    return main_win, last_result, last_texts


def save_outputs(main_win, result: dict[str, Any], texts: list[dict[str, Any]], save_all_texts: bool) -> Path:
    run_dir = SKILL_ROOT / "runs" / f"balance_{_now_stamp()}"
    run_dir.mkdir(parents=True, exist_ok=True)

    screenshot_path = run_dir / "screen.png"
    try:
        capture_window_screenshot(main_win, str(screenshot_path))
    except Exception as exc:
        result["screenshot_error"] = str(exc)
        screenshot_path = None

    matched_texts = [
        item
        for item in texts
        if COMPANY_RE.search(item["text"])
        or MONEY_RE.search(item["text"])
        or any(
            token in item["text"]
            for token in (
                "人民币",
                "总资产",
                "实时余额合计",
                "账户总数",
                "存款",
                "理财",
                "票据",
                "企业管理",
                "交易明细",
                "账户总表",
            )
        )
    ]

    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "result": result,
        "screenshot": str(screenshot_path) if screenshot_path else "",
        "matched_texts": matched_texts,
    }
    if save_all_texts:
        payload["all_texts"] = texts

    with open(run_dir / "balance.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return run_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Query visible CMB U-BANK company balance.")
    parser.add_argument(
        "--page",
        choices=("auto", "current", "home", "workbench"),
        default="auto",
        help="Where to read from. auto reads current first, then 首页, then 工作台.",
    )
    parser.add_argument("--no-login", action="store_true", help="Do not open/login U-BANK if no main window exists.")
    parser.add_argument("--save-all-texts", action="store_true", help="Save every visible UIA text item to balance.json.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        main_win, result, texts = query_balance(page=args.page, no_login=args.no_login)
        run_dir = save_outputs(main_win, result, texts, args.save_all_texts)
    except Exception as exc:
        print(f"查询失败: {exc}")
        return 1

    print("=" * 60)
    print(f"STATUS={result.get('status')}")
    print(f"PAGE_TYPE={result.get('page_type', '')}")
    print(f"COMPANY={result.get('company', '')}")
    print(f"BALANCE={result.get('balance', '')}")
    if result.get("account_count"):
        print(f"ACCOUNT_COUNT={result.get('account_count')}")
    print(f"RUN_DIR={run_dir}")
    print("=" * 60)
    return 0 if result.get("status") == "OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
