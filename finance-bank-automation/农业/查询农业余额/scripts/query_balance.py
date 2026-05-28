# -*- coding: utf-8 -*-
"""Read ABC corporate banking home-page company and balance.

Read-only workflow:
- log in to ABC corporate banking using the existing ABC automation helpers;
- stop at the logged-in home page;
- click only the safe "余额" row "显示" button;
- extract company, masked account, currency, and balance.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


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
        os.getenv("ABC_USB_HUB_CTRL"),
        os.getenv("USB_HUB_CTRL"),
        SKILL_ROOT.parent.parent / "公共" / "usbhub" / "usbhub" / "多口USB控制器软件以及驱动" / "hub_ctrl.py",
        _desktop_path("财务", "公共", "usbhub", "usbhub", "多口USB控制器软件以及驱动", "hub_ctrl.py"),
        _desktop_path("usbhub", "usbhub", "多口USB控制器软件以及驱动", "hub_ctrl.py"),
    )


ABC_ROOT = _first_existing_path(
    os.getenv("ABC_ROOT"),
    os.getenv("ABC_PROJECT_ROOT"),
    SKILL_ROOT.parent / "农业银行",
    _desktop_path("财务", "农业", "农业银行"),
    _desktop_path("农业银行"),
)
ABC_SKILL_DIR = ABC_ROOT / "skills" / "单笔转账"
HUB_CTRL = _usb_hub_ctrl_path()
HUB_COM = os.getenv("ABC_USB_HUB_COM", os.getenv("USB_HUB_COM", "COM3")).strip() or "COM3"

if str(ABC_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(ABC_SKILL_DIR))

from abc_single_transfer import runtime as rt
from abc_single_transfer.auth import (
    CERT_AUTO_CONFIRMED,
    CERT_MANUAL_REQUIRED,
    CERT_PASSWORD_WINDOW_DETECTED,
    click_cert_login,
    handle_certificate_selection,
    input_kb_password,
    open_corporate_login_page,
    wait_and_click_continue_login,
    wait_for_kb_password_window,
)
from abc_single_transfer.data import get_kb_password
from abc_single_transfer.navigation import find_active_home_page
from abc_single_transfer.runtime import (
    BrowserContext,
    Page,
    Playwright,
    assert_current_page_allowed,
    debug_checkpoint,
    env_flag,
    env_float,
    get_login_url,
    load_environment,
    log,
    start_live_screenshot_recorder,
    sync_playwright,
)
from abc_single_transfer.usb import prepare_abc_usb12_if_enabled


def _now_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


RUN_DIR = SKILL_ROOT / "runs" / f"balance_{_now_id()}"


def _ensure_run_dir() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _first_match(pattern: str, text: str) -> str:
    m = re.search(pattern, text, re.M)
    return (m.group(1).strip() if m else "")


def _normalize_amount(value: str) -> str:
    value = (value or "").strip()
    value = value.replace("￥", "").replace("¥", "").replace("元", "").strip()
    return value


def _parse_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    card_text = str(snapshot.get("cardText") or "")
    body_text = str(snapshot.get("bodyText") or "")
    text = card_text or body_text

    company = _first_match(r"账户名[:：]\s*([^\r\n]*?有限公司)", text)
    if not company:
        company = _first_match(r"尊敬的\s*([^，,\r\n]*?有限公司)", body_text)
    if not company:
        company = _first_match(r"([^\s，,：:]{2,}有限公司)", text)

    account_masked = _first_match(r"账号[:：]\s*([^\r\n]+?人民币)", text)
    if not account_masked:
        account_masked = _first_match(r"账号[:：]\s*([^\r\n]+)", text)
    account_masked = account_masked.replace("|", "").strip()

    currency = "人民币" if "人民币" in text else ""

    balance = ""
    balance_match = re.search(
        r"余额[:：]?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?|[-]{2,}|[*]{3,})",
        text,
    )
    if balance_match:
        balance = _normalize_amount(balance_match.group(1))

    masked = (not balance) or balance in {"--", "-"} or set(balance) == {"*"}
    status = "OK" if balance and not masked else "MASKED"
    if not company and not balance:
        status = "NOT_FOUND"

    return {
        "status": status,
        "company": company,
        "account_masked": account_masked,
        "currency": currency,
        "balance": balance,
        "card_text": card_text,
        "body_text_excerpt": body_text[:4000],
    }


def _home_snapshot(page: Page) -> dict[str, Any]:
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
                    item.text.includes('账户名')
                    && item.text.includes('账号')
                    && item.text.includes('余额')
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


def wait_for_account_card(page: Page, timeout_ms: int = 20000) -> dict[str, Any]:
    deadline = time.time() + timeout_ms / 1000
    last_snapshot: dict[str, Any] = {}
    while time.time() < deadline:
        last_snapshot = _home_snapshot(page)
        body_text = str(last_snapshot.get("bodyText") or "")
        card_text = str(last_snapshot.get("cardText") or "")
        combined = body_text + "\n" + card_text
        if "账户名" in combined and "账号" in combined and "余额" in combined:
            return last_snapshot
        page.wait_for_timeout(500)
    return last_snapshot


def _click_balance_show(page: Page) -> dict[str, Any]:
    target = page.evaluate(
        """() => {
            const visible = (el) => {
                const r = el.getBoundingClientRect();
                const s = window.getComputedStyle(el);
                return s.display !== 'none'
                    && s.visibility !== 'hidden'
                    && r.width > 0
                    && r.height > 0;
            };
            const norm = (s) => String(s || '').replace(/\\s+/g, '').trim();
            const nodes = Array.from(document.querySelectorAll('body *')).filter(visible);
            const labels = nodes
                .map((el) => ({ el, text: norm(el.innerText || el.textContent || ''), rect: el.getBoundingClientRect() }))
                .filter((item) => item.text.includes('余额') && item.rect.width < 260 && item.rect.height < 80);
            const buttons = nodes
                .map((el) => ({ el, text: norm(el.innerText || el.textContent || ''), rect: el.getBoundingClientRect() }))
                .filter((item) => item.text === '显示' && item.rect.width < 120 && item.rect.height < 80);
            let best = null;
            for (const label of labels) {
                const ly = label.rect.y + label.rect.height / 2;
                const lx = label.rect.x + label.rect.width / 2;
                for (const btn of buttons) {
                    const by = btn.rect.y + btn.rect.height / 2;
                    const bx = btn.rect.x + btn.rect.width / 2;
                    const dy = Math.abs(by - ly);
                    const dx = bx - lx;
                    if (dy <= 35 && dx > 0 && dx < 260) {
                        const score = dy * 10 + dx;
                        if (!best || score < best.score) {
                            best = {
                                score,
                                labelText: label.text,
                                buttonText: btn.text,
                                x: bx,
                                y: by,
                                labelRect: {x: label.rect.x, y: label.rect.y, w: label.rect.width, h: label.rect.height},
                                buttonRect: {x: btn.rect.x, y: btn.rect.y, w: btn.rect.width, h: btn.rect.height},
                            };
                        }
                    }
                }
            }
            return best;
        }"""
    )
    if not target:
        return {"clicked": False, "reason": "balance_show_button_not_found"}
    page.mouse.click(float(target["x"]), float(target["y"]))
    page.wait_for_timeout(1500)
    return {"clicked": True, "target": target}


def extract_balance(page: Page) -> dict[str, Any]:
    before = wait_for_account_card(page)
    click_result = _click_balance_show(page)
    after = wait_for_account_card(page, timeout_ms=8000)
    parsed = _parse_snapshot(after)
    parsed["show_click"] = click_result
    parsed["url"] = after.get("url", "")
    parsed["title"] = after.get("title", "")
    parsed["before_card_text"] = before.get("cardText", "")
    parsed["after_card_candidates"] = after.get("cardCandidates", [])
    return parsed


def save_screen(page: Page) -> None:
    try:
        page.screenshot(path=str(RUN_DIR / "screen.png"), full_page=True, timeout=8000)
    except Exception as exc:
        log.warning("[余额] 保存截图失败: %s", type(exc).__name__)


def all_off_usb_hub_ports() -> bool:
    if not env_flag("ABC_USB12_ALL_OFF_AFTER_RUN", True):
        log.info("[USB] ABC_USB12_ALL_OFF_AFTER_RUN=false，跳过运行后关闭 USB Hub")
        return True
    if not HUB_CTRL.exists():
        log.error("[USB] 未找到 USB Hub 控制脚本，无法关闭所有端口: %s", HUB_CTRL)
        return False
    cmd = [sys.executable, str(HUB_CTRL), "all-off", "--com", HUB_COM]
    try:
        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        log.error("[USB] 关闭所有 USB Hub 端口失败: %s", type(exc).__name__)
        return False
    if result.stdout.strip():
        log.info("[USB] all-off 输出:\n%s", result.stdout.strip())
    if result.stderr.strip():
        log.info("[USB] all-off 日志:\n%s", result.stderr.strip())
    if result.returncode != 0:
        log.error("[USB] all-off 退出码=%s", result.returncode)
        return False
    log.info("[USB] 查询结束后已关闭 USB Hub 所有端口")
    return True


def launch_chromium(playwright: Playwright):
    cert_auto_select_rules = [
        {"pattern": "https://cbank.abchina.com.cn", "filter": {}},
        {"pattern": "https://cbank.abchina.com.cn:443", "filter": {}},
    ]
    if env_flag("ABC_CERT_AUTOSELECT_BROAD", False):
        cert_auto_select_rules.append({"pattern": "https://[*.]abchina.com.cn", "filter": {}})
        log.warning("[安全] ABC_CERT_AUTOSELECT_BROAD=true，已启用 abchina.com.cn 全子域证书自动选择")

    launch_env = dict(os.environ)
    for proxy_env_name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        launch_env.pop(proxy_env_name, None)

    launch_kwargs: dict[str, Any] = {
        "headless": False,
        "args": [
            "--auto-select-certificate-for-urls="
            + json.dumps(cert_auto_select_rules, ensure_ascii=False, separators=(",", ":")),
            "--no-proxy-server",
            "--window-position=0,0",
            "--window-size=1600,900",
        ],
        "env": launch_env,
    }
    browser_channel = os.getenv("BROWSER_CHANNEL", "").strip()
    if browser_channel:
        launch_kwargs["channel"] = browser_channel
        log.info("使用 BROWSER_CHANNEL=%s", browser_channel)
    log.info("已强制 Chromium 不使用代理（--no-proxy-server，并清理代理环境变量）")
    return playwright.chromium.launch(**launch_kwargs)


def main() -> int:
    _ensure_run_dir()
    load_environment()
    log.info("[余额] 查询农业余额启动，结果目录: %s", RUN_DIR)
    log.info("[余额] 只读流程：登录首页 -> 点击余额显示 -> 读取公司与余额；不进入转账页面")

    login_url, _is_direct = get_login_url()
    if not prepare_abc_usb12_if_enabled():
        result = {"status": "USB12_FAILED", "company": "", "balance": "", "run_dir": str(RUN_DIR)}
        _write_json(RUN_DIR / "balance.json", result)
        print(json.dumps(result, ensure_ascii=False))
        return 3

    kb_password = get_kb_password()
    auto_enter_enabled = env_flag("KB_PASSWORD_AUTO_ENTER", True)

    playwright: Playwright | None = None
    browser = None
    context: BrowserContext | None = None
    page: Page | None = None
    rt._live_recorder = start_live_screenshot_recorder()

    try:
        debug_checkpoint("查询余额：准备启动浏览器")
        playwright = sync_playwright().start()
        browser = launch_chromium(playwright)
        context = browser.new_context(viewport={"width": 1600, "height": 900})
        page = context.new_page()
        debug_checkpoint("查询余额：空白浏览器页已创建", page)

        page = open_corporate_login_page(page, context, login_url)
        assert_current_page_allowed(page, "企业网银登录页")
        debug_checkpoint("查询余额：企业网银登录页定位完成", page)

        click_cert_login(page)
        debug_checkpoint("查询余额：已点击证书登录按钮", page, skip_page_screenshot=True)
        cert_status = handle_certificate_selection(page)
        debug_checkpoint(f"查询余额：证书弹窗处理链结束 ({cert_status})", page, skip_page_screenshot=True)

        if cert_status == CERT_PASSWORD_WINDOW_DETECTED:
            kb_window_wait_s = env_float("KB_WINDOW_WAIT_S_QUICK", 5.0)
        elif cert_status == CERT_AUTO_CONFIRMED:
            wait_and_click_continue_login(page)
            debug_checkpoint("查询余额：继续登录提示处理结束", page)
            kb_window_wait_s = env_float("KB_WINDOW_WAIT_S_AUTO", 60.0)
        else:
            log.warning("[余额] 证书弹窗需人工接管：请选择证书/继续登录")
            kb_window_wait_s = env_float("KB_WINDOW_WAIT_S_MANUAL", 240.0)

        auto_entered = False
        if kb_password:
            auto_entered = input_kb_password(kb_password, wait_seconds=kb_window_wait_s)
            kb_password = None
        else:
            log.warning("[余额] 未提供 K 宝密码，请人工输入")
            wait_for_kb_password_window(kb_window_wait_s)

        if auto_entered and auto_enter_enabled:
            log.info("[余额] 已自动输入密码并按 Enter，等待首页")
        elif auto_entered:
            log.info("[余额] K 宝密码已输入，请人工确认登录")
        else:
            log.info("[余额] 请人工完成 K 宝密码与登录")

        debug_checkpoint("查询余额：K 宝密码阶段结束", page, skip_page_screenshot=True)
        page = find_active_home_page(context, page)
        assert_current_page_allowed(page, "企业网银主页")
        debug_checkpoint("查询余额：企业网银主页定位结束", page)

        result = extract_balance(page)
        result["run_dir"] = str(RUN_DIR)
        result["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_screen(page)
        _write_json(RUN_DIR / "balance.json", result)

        summary = {
            "status": result.get("status"),
            "company": result.get("company"),
            "account_masked": result.get("account_masked"),
            "currency": result.get("currency"),
            "balance": result.get("balance"),
            "run_dir": str(RUN_DIR),
        }
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if result.get("status") == "OK" else 10
    except KeyboardInterrupt:
        result = {"status": "INTERRUPTED", "company": "", "balance": "", "run_dir": str(RUN_DIR)}
        _write_json(RUN_DIR / "balance.json", result)
        return 130
    except Exception as exc:
        log.exception("[余额] 发生异常: %s: %s", type(exc).__name__, exc)
        if page is not None:
            save_screen(page)
        result = {
            "status": "ERROR",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "run_dir": str(RUN_DIR),
        }
        _write_json(RUN_DIR / "balance.json", result)
        print(json.dumps(result, ensure_ascii=False))
        return 1
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass
        if rt._live_recorder is not None:
            try:
                rt._live_recorder.stop()
            finally:
                rt._live_recorder = None
        log.info("[余额] 浏览器资源已释放")
        all_off_usb_hub_ports()


if __name__ == "__main__":
    raise SystemExit(main())
