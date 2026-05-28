# -*- coding: utf-8 -*-
"""Read the visible CIB corporate company/account balance from the Windows GUI."""

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

import pyautogui
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
    os.getenv("CIB_ZHIDAN_ROOT"),
    SKILL_ROOT.parent / "兴业制单",
    _desktop_path("财务", "兴业", "兴业制单"),
    _desktop_path("兴业制单"),
)

if str(ZHIDAN_ROOT) not in sys.path:
    sys.path.insert(0, str(ZHIDAN_ROOT))

from cib_open_bank.main_flow import run_transfer_flow  # noqa: E402
from cib_open_bank.runtime import all_off_usb_hub_ports  # noqa: E402
from cib_open_bank.windows import close_bank_windows, dismiss_ukey_notice, find_window  # noqa: E402

MONEY_RE = re.compile(r"(?<!\d)-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})(?!\d)")
ACCOUNT_RE = re.compile(r"(?<!\d)(\d{12,})(?!\d)")
COMPANY_RE = re.compile(r"([\u4e00-\u9fffA-Za-z0-9（）()·\-]+有限公司)")
MAIN_TITLE_NEEDLES = ("兴业银行企业网银", "兴业银行", "企业网银")
MAIN_PAGE_HINTS = ("首页", "查询中心", "转账付款", "在线管理")


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


def _title_is_cib_main(title: str) -> bool:
    if not title or "验证网盾密码" in title or "网盾" in title:
        return False
    return any(needle in title for needle in MAIN_TITLE_NEEDLES)


def get_main_window():
    desktop = Desktop(backend="uia")
    candidates = []
    for win in desktop.windows():
        try:
            title = (win.window_text() or "").strip()
            if _title_is_cib_main(title):
                rect = win.rectangle()
                if rect.width() >= 600 and rect.height() >= 400:
                    candidates.append(win)
        except Exception:
            continue
    if candidates:
        candidates.sort(key=lambda w: 0 if "兴业银行企业网银" in (w.window_text() or "") else 1)
        return candidates[0]

    legacy = find_window()
    if not legacy:
        return None
    title = str(getattr(legacy, "title", "") or "")
    if not _title_is_cib_main(title):
        return None
    for win in desktop.windows():
        try:
            if (win.window_text() or "").strip() == title:
                return win
        except Exception:
            pass
    return None


def collect_visible_texts(main_win) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def walk(control, depth: int = 0):
        if depth > 15:
            return
        try:
            if not control.is_visible():
                return
            info = control.element_info
            name = info.name if hasattr(info, "name") and info.name else ""
            control_type = str(info.control_type) if hasattr(info, "control_type") else ""
            class_name = info.class_name if hasattr(info, "class_name") else ""
            rect = control.rectangle()
            if name and name.strip():
                rows.append({"text": name.strip(), "type": control_type, "class": class_name, "rect": _rect_to_list(rect)})
        except Exception:
            pass
        try:
            for child in control.children():
                walk(child, depth + 1)
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


def _main_page_like(main_win) -> bool:
    texts = collect_visible_texts(main_win)
    return any(item["text"].strip() in MAIN_PAGE_HINTS for item in texts)


def dismiss_warm_prompt(wait_seconds: float = 2.0) -> bool:
    end = time.time() + wait_seconds
    while time.time() < end:
        desktop = Desktop(backend="uia")
        for win in desktop.windows():
            try:
                if (win.window_text() or "").strip() != "温馨提示":
                    continue
                for child in win.children():
                    try:
                        if (child.element_info.name or "").strip() == "确定":
                            child.click_input()
                            time.sleep(0.5)
                            return True
                    except Exception:
                        pass
                win.set_focus()
                pyautogui.press("enter")
                time.sleep(0.5)
                return True
            except Exception:
                pass
        time.sleep(0.2)
    return False


def ensure_logged_in(no_login: bool = False):
    dismiss_warm_prompt(wait_seconds=0.8)
    main_win = get_main_window()
    if main_win is not None and _main_page_like(main_win):
        print(f"已检测到兴业主界面: {main_win.window_text()}")
        return main_win
    if no_login:
        raise BalanceQueryError("未找到已登录兴业主界面，且指定了 --no-login")
    print("未发现已登录兴业主界面，复用兴业制单登录流程...")
    run_transfer_flow(stop_after_login=True)
    dismiss_ukey_notice(wait_seconds=3)
    dismiss_warm_prompt(wait_seconds=1.5)
    time.sleep(2)
    main_win = get_main_window()
    if main_win is None:
        raise BalanceQueryError("登录后未找到兴业主界面")
    print(f"登录完成: {main_win.window_text()}")
    return main_win


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
        company = match.group(1)
        if company.startswith("兴业银行"):
            continue
        row = dict(item)
        row["company"] = company
        matches.append(row)
    if not matches:
        return None
    matches.sort(key=lambda item: (item["rect"][1] > 900, item["rect"][1], item["rect"][0]))
    return matches[0]


def find_account(texts: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = []
    for item in texts:
        match = ACCOUNT_RE.search(item["text"])
        if not match:
            continue
        row = dict(item)
        row["account_no"] = match.group(1)
        score = 0 if "常用账号" in item["text"] else 1
        candidates.append((score, item["rect"][1], item["rect"][0], row))
    if not candidates:
        return None
    candidates.sort(key=lambda row: (row[0], row[1], row[2]))
    return candidates[0][3]


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


def _nearest_money_to_label(texts: list[dict[str, Any]], label_item: dict[str, Any]) -> dict[str, Any] | None:
    label_rect = label_item["rect"]
    label_cx, _ = _center(label_rect)
    candidates = []
    for item in texts:
        match = MONEY_RE.search(item["text"])
        if not match:
            continue
        rect = item["rect"]
        if rect[1] < label_rect[1] - 12:
            continue
        dy = rect[1] - label_rect[1]
        if dy > 140:
            continue
        cx, _ = _center(rect)
        dx = abs(cx - label_cx)
        if dx > 260:
            continue
        if rect[0] > label_rect[0] + 380:
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


def _masked_near_label(texts: list[dict[str, Any]], label_item: dict[str, Any]) -> bool:
    label_rect = label_item["rect"]
    label_cx, _ = _center(label_rect)
    for item in texts:
        if "*" not in item["text"]:
            continue
        rect = item["rect"]
        if rect[1] < label_rect[1] - 12 or rect[1] - label_rect[1] > 140:
            continue
        cx, _ = _center(rect)
        if abs(cx - label_cx) <= 260:
            return True
    return False


def click_available_balance_eye(texts: list[dict[str, Any]]) -> bool:
    label_item = _find_label(texts, "可用余额")
    if not label_item:
        return False
    rect = label_item["rect"]
    x = rect[2] + 18
    y = int((rect[1] + rect[3]) / 2)
    pyautogui.click(x, y)
    time.sleep(1.2)
    return True


def extract_balance(texts: list[dict[str, Any]]) -> dict[str, Any]:
    company_item = find_company(texts)
    account_item = find_account(texts)
    label_item = _find_label(texts, "可用余额")
    currency_item = _find_label(texts, "人民币(CNY)") or _find_label(texts, "人民币")
    base = {
        "company": company_item["company"] if company_item else "",
        "company_text": company_item["text"] if company_item else "",
        "account_no": account_item["account_no"] if account_item else "",
        "account_text": account_item["text"] if account_item else "",
        "currency": currency_item["text"] if currency_item else "",
        "company_control": company_item,
        "account_control": account_item,
        "currency_control": currency_item,
        "label_control": label_item,
    }
    if not label_item:
        return {"status": "NOT_FOUND", "available_balance": "", "balance_decimal": None, **base}
    amount_item = _nearest_money_to_label(texts, label_item)
    if amount_item:
        return {
            "status": "OK",
            "balance_label": "可用余额",
            "available_balance": amount_item["amount"],
            "balance_decimal": amount_item["amount_decimal"],
            "balance_text": amount_item["text"],
            "balance_control": amount_item,
            **base,
        }
    if _masked_near_label(texts, label_item):
        return {"status": "MASKED", "balance_label": "可用余额", "available_balance": "", "balance_decimal": None, **base}
    return {"status": "NOT_FOUND", "balance_label": "可用余额", "available_balance": "", "balance_decimal": None, **base}


def read_balance_from_window(main_win, reveal: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    texts = collect_visible_texts(main_win)
    result = extract_balance(texts)
    if reveal and result.get("status") in {"MASKED", "NOT_FOUND"}:
        if click_available_balance_eye(texts):
            main_win = get_main_window() or main_win
            texts = collect_visible_texts(main_win)
            result = extract_balance(texts)
            result["revealed_attempted"] = True
    return result, texts


def query_balance(page: str, no_login: bool, reveal: bool) -> tuple[Any, dict[str, Any], list[dict[str, Any]]]:
    main_win = ensure_logged_in(no_login=no_login)
    attempts = []
    if page in ("current", "auto"):
        attempts.append(None)
    if page in ("home", "auto"):
        attempts.append("首页")
    last_result: dict[str, Any] | None = None
    last_texts: list[dict[str, Any]] = []
    for target in attempts:
        if target:
            print(f"尝试切换到 {target} 后读取余额...")
            click_visible_text(main_win, target)
            time.sleep(3)
            dismiss_warm_prompt(wait_seconds=0.8)
            main_win = get_main_window() or main_win
        else:
            print("读取当前页面余额...")
            time.sleep(1)
        result, texts = read_balance_from_window(main_win, reveal=reveal)
        result["attempt"] = target or "current"
        last_result = result
        last_texts = texts
        if result.get("status") == "OK" or (result.get("company") and result.get("status") == "MASKED"):
            return main_win, result, texts
    assert last_result is not None
    return main_win, last_result, last_texts


def save_outputs(main_win, result: dict[str, Any], texts: list[dict[str, Any]], save_all_texts: bool) -> Path:
    run_dir = SKILL_ROOT / "runs" / f"balance_{_now_stamp()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = run_dir / "screen.png"
    try:
        rect = main_win.rectangle()
        img = pyautogui.screenshot(region=(rect.left, rect.top, rect.width(), rect.height()))
        img.save(screenshot_path)
    except Exception as exc:
        result["screenshot_error"] = str(exc)
        screenshot_path = None
    matched_texts = [
        item for item in texts
        if COMPANY_RE.search(item["text"])
        or ACCOUNT_RE.search(item["text"])
        or MONEY_RE.search(item["text"])
        or any(token in item["text"] for token in ("可用余额", "控制金额", "人民币", "常用账号", "早上好"))
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
    parser = argparse.ArgumentParser(description="Query visible CIB company balance.")
    parser.add_argument("--page", choices=("auto", "current", "home"), default="auto")
    parser.add_argument("--no-login", action="store_true", help="Do not open/login if no main window exists.")
    parser.add_argument("--no-reveal", action="store_true", help="Do not click the available-balance eye icon.")
    parser.add_argument("--save-all-texts", action="store_true", help="Save every visible UIA text item to balance.json.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    exit_code = 1
    try:
        main_win, result, texts = query_balance(page=args.page, no_login=args.no_login, reveal=not args.no_reveal)
        run_dir = save_outputs(main_win, result, texts, args.save_all_texts)
    except Exception as exc:
        print(f"查询失败: {exc}")
    else:
        print("=" * 60)
        print(f"STATUS={result.get('status')}")
        print(f"COMPANY={result.get('company', '')}")
        print(f"ACCOUNT_NO={result.get('account_no', '')}")
        print(f"CURRENCY={result.get('currency', '')}")
        print(f"AVAILABLE_BALANCE={result.get('available_balance', '')}")
        print(f"RUN_DIR={run_dir}")
        print("=" * 60)
        exit_code = 0 if result.get("status") in {"OK", "MASKED"} else 2
    finally:
        print("\n[收尾] 查询结束，关闭兴业网银窗口...")
        try:
            close_bank_windows()
        except Exception as exc:
            print(f"  关闭兴业网银窗口异常: {exc}")
        print("\n[收尾] 查询结束，all-off 断开所有 USB Hub 口...")
        try:
            all_off_usb_hub_ports()
        except Exception as exc:
            print(f"  USB Hub all-off 异常: {exc}")
        try:
            dismiss_ukey_notice(wait_seconds=5)
        except Exception as exc:
            print(f"  关闭 UKey 提示异常: {exc}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
