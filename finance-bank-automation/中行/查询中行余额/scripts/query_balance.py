# -*- coding: utf-8 -*-
"""Query Bank of China corporate banking home-page balance.

Read-only workflow:
- reuse the existing 中国银行 automation helpers for login/certificate/PIN;
- stop at the logged-in home page;
- extract company, account, bank, currency, and current balance from 余额视图;
- close all USBHub ports in finally.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

THIS_DIR = Path(__file__).resolve().parent
SKILL_ROOT = THIS_DIR.parent


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


def _usb_hub_ctrl_path() -> Path:
    return _first_existing_path(
        os.getenv("BOC_USBHUB_CTRL_PATH"),
        os.getenv("USB_HUB_CTRL"),
        SKILL_ROOT.parent.parent / "公共" / "usbhub" / "usbhub" / "多口USB控制器软件以及驱动" / "hub_ctrl.py",
        _desktop_path("财务", "公共", "usbhub", "usbhub", "多口USB控制器软件以及驱动", "hub_ctrl.py"),
        _desktop_path("usbhub", "usbhub", "多口USB控制器软件以及驱动", "hub_ctrl.py"),
    )


BOC_ROOT = _first_existing_path(
    os.getenv("BOC_ROOT"),
    os.getenv("BOC_PROJECT_ROOT"),
    SKILL_ROOT.parent / "中国银行",
    _desktop_path("财务", "中行", "中国银行"),
    _desktop_path("中国银行"),
)
HUB_CTRL = _usb_hub_ctrl_path()
HUB_COM = os.getenv("BOC_USBHUB_COM") or os.getenv("USB_HUB_COM") or "COM3"

if str(BOC_ROOT) not in sys.path:
    sys.path.insert(0, str(BOC_ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from boc_automation.browser import _find_edge_executable, _prepare_chrome_extensions  # noqa: E402
from boc_automation.config import (  # noqa: E402
    BOC_AUTO_USHIELD_PIN,
    BOC_LOGIN_PASSWORD,
    BOC_USBHUB_COM,
    BOC_URL,
    BOC_USHIELD_PIN,
)
from boc_automation.debug import _debug_checkpoint, _setup_stdout_utf8, _wait_full_load  # noqa: E402
from boc_automation.login import (  # noqa: E402
    _click_certificate_login,
    _fill_page_password_and_login,
    _maybe_submit_ushield_password,
)
from boc_automation.proxy import (  # noqa: E402
    _direct_network_preflight,
    _disable_proxy_for_playwright,
    _restore_proxy_env,
)
from boc_automation.usbhub import _power_on_boc_usbhub_port  # noqa: E402
from boc_automation.windows import (  # noqa: E402
    _close_auto_opened_boc_home_tab,
    _close_boc_public_home_windows,
    _close_native_boc_dialogs,
    _close_ushield_unplug_dialogs,
    _confirm_certificate_selection,
    _dismiss_browser_chrome_tooltips,
)

HUB_COM = BOC_USBHUB_COM or HUB_COM


def _now_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


RUN_DIR = SKILL_ROOT / "runs" / f"balance_{_now_id()}"


class _Tee:
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
        return len(data)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def _setup_run_dir_and_logging() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    log = (RUN_DIR / "run.log").open("a", encoding="utf-8", buffering=1)
    sys.stdout = _Tee(sys.stdout, log)
    sys.stderr = _Tee(sys.stderr, log)
    print(f"[余额] 查询中行余额启动，结果目录: {RUN_DIR}", flush=True)
    print("[余额] 只读流程：登录首页 -> 读取余额视图；不进入付款/转账页面", flush=True)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _first_match(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.S)
    return _compact(match.group(1)) if match else ""


def _normalize_amount(value: str) -> str:
    value = _compact(value).replace("￥", "").replace("¥", "")
    return value


def _parse_balance_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    card_text = str(snapshot.get("cardText") or "")
    body_text = str(snapshot.get("bodyText") or "")
    text = card_text or body_text

    company = _first_match(r"账户名称\s*([^\r\n]+?有限公司)", text)
    if not company:
        company = _first_match(r"尊敬的\s*([^，,\r\n]+?有限公司)", body_text)
    if not company:
        company = _first_match(r"([^\s，,。；;：:|]+有限公司)", text)

    account = _first_match(r"账号\s*([0-9*]{6,})", text)
    account_name = _first_match(r"账户名称\s*(.+?)(?=\s*开户行\s)", text)
    if not account_name:
        account_name = _first_match(r"账户名称\s*([^\r\n]+)", text)
    bank = _first_match(r"开户行\s*(.+?)(?=\s*最近交易|\s*余额统计|$)", text)
    if not bank:
        bank = _first_match(r"开户行\s*([^\r\n]+)", text)

    balance_match = re.search(
        r"当前余额\s*(?:\r?\n|\s)*(CNY|RMB|人民币)?\s*([-+]?[0-9][0-9,]*(?:\.[0-9]{1,2})?)",
        text,
        re.S,
    )
    currency = ""
    balance = ""
    if balance_match:
        currency = balance_match.group(1) or ""
        balance = _normalize_amount(balance_match.group(2))

    status = "OK" if company and balance else "NOT_FOUND"
    return {
        "status": status,
        "company": company,
        "account": account,
        "account_name": account_name,
        "bank": bank,
        "currency": currency,
        "balance": balance,
        "card_text": card_text,
        "body_text_excerpt": body_text[:5000],
        "card_candidates": snapshot.get("cardCandidates", []),
    }


def _home_snapshot(page) -> dict[str, Any]:
    return page.evaluate(
        """() => {
            const visible = (el) => {
                const r = el.getBoundingClientRect();
                const s = window.getComputedStyle(el);
                return s.display !== 'none'
                    && s.visibility !== 'hidden'
                    && r.width > 0
                    && r.height > 0;
            };
            const norm = (s) => String(s || '').replace(/\\s+/g, ' ').trim();
            const bodyText = document.body ? document.body.innerText || '' : '';
            const nodes = Array.from(document.querySelectorAll('body *')).filter(visible);
            const cards = nodes
                .map((el) => {
                    const text = norm(el.innerText || el.textContent || '');
                    const r = el.getBoundingClientRect();
                    return { text, area: r.width * r.height, x: r.x, y: r.y, w: r.width, h: r.height };
                })
                .filter((item) =>
                    item.text.includes('余额视图')
                    && item.text.includes('当前余额')
                    && item.text.includes('账号')
                    && item.text.includes('账户名称')
                    && item.area > 1000
                )
                .sort((a, b) => a.area - b.area);
            return {
                url: location.href,
                title: document.title,
                bodyText,
                cardText: cards.length ? cards[0].text : '',
                cardCandidates: cards.slice(0, 5),
            };
        }"""
    )


def wait_for_balance_view(page, timeout_ms: int = 30000) -> dict[str, Any]:
    deadline = time.time() + timeout_ms / 1000
    last_snapshot: dict[str, Any] = {}
    while time.time() < deadline:
        last_snapshot = _home_snapshot(page)
        combined = str(last_snapshot.get("bodyText") or "") + "\n" + str(last_snapshot.get("cardText") or "")
        if "余额视图" in combined and "当前余额" in combined and "账户名称" in combined:
            return last_snapshot
        page.wait_for_timeout(500)
    return last_snapshot


def extract_balance(page) -> dict[str, Any]:
    snapshot = wait_for_balance_view(page)
    result = _parse_balance_snapshot(snapshot)
    result["url"] = snapshot.get("url", "")
    result["title"] = snapshot.get("title", "")
    result["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
    result["run_dir"] = str(RUN_DIR)
    return result


def save_page_screen(page) -> None:
    try:
        page.screenshot(path=str(RUN_DIR / "screen.png"), full_page=False, timeout=8000)
    except Exception as exc:
        print(f"[余额] 保存页面截图失败：{type(exc).__name__}: {exc}", flush=True)


def all_off_usb_hub_ports() -> bool:
    if not HUB_CTRL.exists():
        print(f"[USBHub] 未找到 hub_ctrl.py，无法关闭所有口：{HUB_CTRL}", flush=True)
        return False
    cmd = [sys.executable, str(HUB_CTRL), "all-off", "--com", HUB_COM]
    try:
        completed = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        print(f"[USBHub] 关闭所有口失败：{type(exc).__name__}: {exc}", flush=True)
        return False
    if completed.stdout.strip():
        print(f"[USBHub] stdout: {completed.stdout.strip()}", flush=True)
    if completed.stderr.strip():
        print(f"[USBHub] stderr: {completed.stderr.strip()}", flush=True)
    if completed.returncode != 0:
        print(f"[USBHub] all-off 退出码={completed.returncode}", flush=True)
        return False
    print("[USBHub] 已关闭所有口", flush=True)
    return True


def _build_launch_kwargs(profile_dir: str, extension_source_browser: str) -> dict[str, Any]:
    ext_base = Path(profile_dir) / "Default" / "Extensions"
    ext_dirs = []
    for ext_id in ext_base.iterdir():
        if not ext_id.is_dir():
            continue
        for ver in ext_id.iterdir():
            if (ver / "manifest.json").is_file():
                ext_dirs.append(str(ver))
                break

    launch_kwargs: dict[str, Any] = {
        "user_data_dir": profile_dir,
        "headless": False,
        "viewport": {"width": 1366, "height": 860},
        "locale": "zh-CN",
        "ignore_default_args": ["--disable-extensions"],
        "args": [
            "--no-proxy-server",
            "--proxy-server=direct://",
            "--proxy-bypass-list=*",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=Translate,TranslateUI",
            "--lang=zh-CN",
            f"--load-extension={','.join(ext_dirs)}",
        ],
    }
    if extension_source_browser == "Edge":
        edge_path = _find_edge_executable()
        if not edge_path:
            raise SystemExit("[终止] 找到 Edge 中行扩展，但未找到 msedge.exe")
        launch_kwargs["executable_path"] = edge_path
        print(f"[浏览器] 使用系统 Edge 加载 Edge 扩展：{edge_path}", flush=True)
    else:
        print("[浏览器] 使用 Playwright Chromium 加载扩展", flush=True)
    return launch_kwargs


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("[终止] 本脚本依赖 Windows 原生证书/键盘接口，只支持 Windows。")

    _setup_stdout_utf8()
    _setup_run_dir_and_logging()
    if not BOC_LOGIN_PASSWORD:
        result = {"status": "CONFIG_ERROR", "error": "未配置 BOC_LOGIN_PASSWORD", "run_dir": str(RUN_DIR)}
        _write_json(RUN_DIR / "balance.json", result)
        print(json.dumps(result, ensure_ascii=False))
        return 2

    print("[USBHub] 查询前只开启中行 U盾 10 口", flush=True)
    _power_on_boc_usbhub_port()
    _close_auto_opened_boc_home_tab(wait_seconds=3.0)

    proxy_env = None
    profile_dir = None
    context = None
    page = None
    result: dict[str, Any] | None = None

    try:
        proxy_env = _disable_proxy_for_playwright()
        print("[网络] 已清理进程代理，开始银行直连预检", flush=True)
        _direct_network_preflight()

        profile_dir, extension_source_browser = _prepare_chrome_extensions()
        launch_kwargs = _build_launch_kwargs(profile_dir, extension_source_browser)

        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(**launch_kwargs)
            page = context.pages[0] if context.pages else context.new_page()
            for extra_page in context.pages[1:]:
                try:
                    extra_page.close()
                except Exception:
                    pass

            _dismiss_browser_chrome_tooltips()
            page.goto(BOC_URL, wait_until="domcontentloaded")
            if _close_boc_public_home_windows():
                try:
                    page.bring_to_front()
                except Exception:
                    pass
            _wait_full_load(page, timeout_ms=60000)
            print(f"[已打开] {BOC_URL}", flush=True)

            if not _click_certificate_login(page):
                raise RuntimeError("未找到或未能点击：数字证书登录")
            print("[已点击] 数字证书登录", flush=True)

            cert_ok = _confirm_certificate_selection(wait_seconds=8.0)
            _debug_checkpoint(f"查询余额_证书选择确认结果_{'成功' if cert_ok else '失败'}", page)
            if not cert_ok:
                raise RuntimeError("未能自动确认中行证书选择框")

            pin_submitted = _maybe_submit_ushield_password(
                page,
                BOC_USHIELD_PIN,
                auto_submit=BOC_AUTO_USHIELD_PIN,
            )
            _debug_checkpoint(f"查询余额_U盾PIN处理结果_{'已自动提交' if pin_submitted else '未自动提交'}", page)
            if pin_submitted:
                print("[已输入] U盾 PIN 并提交", flush=True)

            if not _fill_page_password_and_login(page, BOC_LOGIN_PASSWORD):
                raise RuntimeError("未能自动填写登录页用户密码")
            print("[已输入] 登录页用户密码并点击登录", flush=True)

            _wait_full_load(page, timeout_ms=60000)
            _debug_checkpoint("查询余额_网页登录后页面等待完成", page)
            page.wait_for_url("**/#/index", timeout=60000)

            result = extract_balance(page)
            save_page_screen(page)
            _write_json(RUN_DIR / "balance.json", result)

            summary = {
                "status": result.get("status"),
                "company": result.get("company"),
                "account": result.get("account"),
                "account_name": result.get("account_name"),
                "bank": result.get("bank"),
                "currency": result.get("currency"),
                "balance": result.get("balance"),
                "run_dir": str(RUN_DIR),
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
            return 0 if result.get("status") == "OK" else 10
    except Exception as exc:
        print(f"[余额] 查询失败：{type(exc).__name__}: {exc}", flush=True)
        if page is not None:
            save_page_screen(page)
        result = {
            "status": "ERROR",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "run_dir": str(RUN_DIR),
        }
        _write_json(RUN_DIR / "balance.json", result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 1
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if profile_dir:
            shutil.rmtree(profile_dir, ignore_errors=True)
            print(f"[清理] 已删除临时 profile: {profile_dir}", flush=True)
        if proxy_env is not None:
            _restore_proxy_env(proxy_env)
            print("[清理] 已恢复代理环境变量", flush=True)
        _close_native_boc_dialogs()
        _close_boc_public_home_windows()
        all_off_usb_hub_ports()
        _close_ushield_unplug_dialogs(wait_seconds=5.0)
        _close_native_boc_dialogs()
        _close_boc_public_home_windows()


if __name__ == "__main__":
    raise SystemExit(main())
