# -*- coding: utf-8 -*-
from __future__ import annotations

"""企业网银登录后导航到单笔转账。"""

from .runtime import *

def _locate_menu_text(
    page: Page,
    text: str,
    timeout_ms: int = DEFAULT_VISIBLE_TIMEOUT_MS,
    exact_probe_ms: int = 3000,
) -> Locator:
    """
    导航类菜单文本定位：先用 exact 文本匹配避免命中「单笔转账查询/记录」之类
    含相同子串的同级条目；exact 找不到再退回旧的子串 `text=` 选择器，避免
    因为 ARIA/DOM 结构变化导致整个流程坏掉。

    exact 探测使用较短的 `exact_probe_ms`（页面就绪后 exact 命中是毫秒级的，
    不需要拿满整个 timeout），fallback 才使用调用方传入的 `timeout_ms`，
    避免 exact 不命中时白等两倍超时。
    """
    exact_locator = page.get_by_text(text, exact=True).first
    try:
        exact_locator.wait_for(state="visible", timeout=exact_probe_ms)
        return exact_locator
    except (PlaywrightTimeout, PlaywrightError):
        log.warning(
            "[导航] 未通过 exact 文本匹配命中「%s」，退回子串匹配（注意可能命中同名菜单）",
            text,
        )
    fallback = page.locator(f"text={text}").first
    fallback.wait_for(state="visible", timeout=timeout_ms)
    return fallback


def find_active_home_page(
    context: BrowserContext, fallback_page: Page, total_timeout_s: float = 120.0
) -> Page:
    """
    登录后农行常会关闭原登录页并在新标签/窗口打开企业网银主页。
    在 context 内轮询非关闭的页面，返回首个含「付款业务」可见菜单的页；
    超时未找到时若原页已关闭则退回最新存活页面，否则返回 fallback_page。
    """
    log.info("[导航] 在所有标签页中搜索企业网银主页（最长 %.0fs）...", total_timeout_s)
    start = time.time()
    deadline = time.time() + total_timeout_s
    round_no = 0
    poll_log_interval = env_float("DEBUG_POLL_LOG_INTERVAL_S", 2.0)
    next_poll_log = start
    while time.time() < deadline:
        round_no += 1
        try:
            pages = list(context.pages)
        except PlaywrightError:
            return fallback_page
        live_pages = [p for p in pages if not p.is_closed()]
        now = time.time()
        if now >= next_poll_log:
            urls = []
            for p in live_pages[:5]:
                try:
                    urls.append(p.url)
                except PlaywrightError:
                    urls.append("<url unavailable>")
            log.info(
                "[导航] 第 %d 轮搜索主页，存活标签 %d/%d，已等待 %.1fs，剩余 %.1fs，urls=%s",
                round_no,
                len(live_pages),
                len(pages),
                now - start,
                max(0.0, deadline - now),
                " | ".join(urls) if urls else "<none>",
            )
            next_poll_log = now + poll_log_interval
        for p in live_pages:
            try:
                p.get_by_text(PAYMENT_TAB_TEXT, exact=True).first.wait_for(
                    state="visible", timeout=500
                )
            except (PlaywrightTimeout, PlaywrightError):
                continue
            if p is not fallback_page:
                log.info("[导航] 已在新标签页定位到企业网银主页：%s", p.url)
            else:
                log.info("[导航] 主页仍在原标签页")
            return p
        safe_sleep(0.5)
    log.warning(
        "[导航] %.0fs 内未在任何标签页找到「付款业务」可见菜单", total_timeout_s
    )
    if fallback_page.is_closed():
        try:
            live = [p for p in context.pages if not p.is_closed()]
        except PlaywrightError:
            live = []
        if live:
            log.warning("[导航] 原登录页已关闭，退回最新存活页面：%s", live[-1].url)
            return live[-1]
        raise RuntimeError("无可用页面：原登录页已关闭，且 context 内无其他存活页面")
    return fallback_page


def navigate_to_single_transfer(page: Page) -> None:
    """登录成功后，导航到「单笔转账」。以关键元素出现为准，不依赖 networkidle。"""
    log.info("[导航] 等待企业网银主页关键元素（付款业务）...")
    try:
        payment_tab = _locate_menu_text(page, PAYMENT_TAB_TEXT, LOGIN_HOME_TIMEOUT_MS)
    except PlaywrightTimeout as e:
        raise RuntimeError(
            "等待「付款业务」菜单超时，登录可能未完成，请人工检查页面状态"
        ) from e

    log.info("[导航] 点击「%s」", PAYMENT_TAB_TEXT)
    payment_tab.click()

    log.info("[导航] 点击「%s」", SINGLE_TRANSFER_TEXT)
    single_transfer = _locate_menu_text(
        page, SINGLE_TRANSFER_TEXT, DEFAULT_VISIBLE_TIMEOUT_MS
    )
    single_transfer.click()

    # 以「金额」/「收款」相关 input 出现作为单笔转账页面就绪标志
    log.info("[导航] 等待单笔转账表单元素就绪...")
    form_ready = page.locator(
        "input[placeholder*='金额'], input[placeholder*='收款']"
    ).first
    try:
        form_ready.wait_for(state="visible", timeout=LONG_VISIBLE_TIMEOUT_MS)
        log.info("[导航] 已进入单笔转账页面")
    except PlaywrightTimeout:
        log.warning("[导航] 未在预期时间内检测到单笔转账表单元素，请人工确认页面")
