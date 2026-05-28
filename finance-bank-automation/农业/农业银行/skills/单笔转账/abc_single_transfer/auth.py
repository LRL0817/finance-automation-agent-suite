# -*- coding: utf-8 -*-
from __future__ import annotations

"""登录页、证书弹窗与 K 宝密码窗口处理。"""

from .runtime import *

# ---------------- 浏览器流程 ----------------
def _is_direct_login_url(url: str) -> bool:
    parsed = urlparse(url)
    fragment = parsed.fragment or ""
    path = parsed.path or ""
    return "login" in fragment.lower() or "login" in path.lower()


def _stop_page_loading(page: Page) -> None:
    try:
        page.evaluate("window.stop()")
    except PlaywrightError:
        try:
            page.keyboard.press("Escape")
        except PlaywrightError:
            pass


def _login_page_dom_snapshot(page: Page) -> dict:
    try:
        return page.evaluate(
            """() => {
                const body = document.body;
                const text = body ? String(body.innerText || '').replace(/\\s+/g, '') : '';
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && rect.width > 0
                        && rect.height > 0;
                };
                return {
                    readyState: document.readyState,
                    title: document.title,
                    textLength: text.length,
                    elementCount: document.querySelectorAll('*').length,
                    visibleElementCount: Array.from(document.querySelectorAll('body *')).filter(visible).length,
                    bodyChildCount: body ? body.children.length : 0,
                };
            }"""
        )
    except PlaywrightError as e:
        return {"error": type(e).__name__}


def _login_page_looks_blank(snapshot: dict) -> bool:
    if snapshot.get("error"):
        return False
    text_length = int(snapshot.get("textLength", 0) or 0)
    visible_count = int(snapshot.get("visibleElementCount", 0) or 0)
    body_children = int(snapshot.get("bodyChildCount", 0) or 0)
    # ABC sometimes leaves an empty Vue shell in the body while the page is
    # visually blank. In that state bodyChildCount can vary, but there is no
    # visible content and no login button, so it still needs a fresh-tab retry.
    if text_length == 0 and visible_count == 0:
        return True
    return text_length <= 3 and visible_count <= 3 and body_children <= 12


def _new_page_after_blank_login(context: BrowserContext, old_page: Page) -> Page:
    try:
        old_page.close(run_before_unload=False)
    except PlaywrightError:
        pass
    new_page = context.new_page()
    debug_checkpoint("登录页白屏后已新建标签页", new_page)
    return new_page


def _goto_login_url_with_retries(page: Page, context: BrowserContext, url: str) -> Page:
    max_attempts = env_int("ABC_LOGIN_GOTO_RETRIES", 3, minimum=1)
    goto_timeout_ms = env_int(
        "ABC_LOGIN_GOTO_TIMEOUT_MS", LOGIN_PAGE_GOTO_TIMEOUT_MS, minimum=5000
    )
    direct_login = _is_direct_login_url(url)

    for attempt in range(1, max_attempts + 1):
        log.info("正在打开: %s（第 %d/%d 次）", url, attempt, max_attempts)
        goto_timed_out = False
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=goto_timeout_ms)
        except PlaywrightTimeout:
            goto_timed_out = True
            log.warning(
                "登录页 page.goto 等待 domcontentloaded 超时（%dms，第 %d/%d 次），将检查是否白屏",
                goto_timeout_ms,
                attempt,
                max_attempts,
            )
            _stop_page_loading(page)

        assert_current_page_allowed(page, "初始页 page.goto")
        try:
            page.wait_for_load_state("networkidle", timeout=DEFAULT_NETWORKIDLE_TIMEOUT_MS)
        except PlaywrightTimeout:
            log.warning("networkidle 等待超时（可继续）")

        if direct_login:
            try:
                page.locator(CERT_LOGIN_BUTTON_SELECTOR).wait_for(
                    state="visible", timeout=5000
                )
                if goto_timed_out:
                    log.info("登录页 goto 虽超时，但证书登录按钮已出现，继续流程")
                return page
            except (PlaywrightTimeout, PlaywrightError):
                snapshot = _login_page_dom_snapshot(page)
                looks_blank = _login_page_looks_blank(snapshot)
                log.warning(
                    "登录页未检测到证书登录按钮：readyState=%s, textLength=%s, visibleElementCount=%s, bodyChildCount=%s, blank=%s",
                    snapshot.get("readyState"),
                    snapshot.get("textLength"),
                    snapshot.get("visibleElementCount"),
                    snapshot.get("bodyChildCount"),
                    looks_blank,
                )
                if looks_blank and attempt < max_attempts:
                    debug_checkpoint(f"登录页白屏，准备重开标签页重试_{attempt}", page)
                    page = _new_page_after_blank_login(context, page)
                    continue
                if looks_blank:
                    debug_checkpoint("登录页多次白屏后仍未恢复", page)
                    raise RuntimeError(
                        "农行登录页多次白屏，已中止本轮；请重新运行或稍后再试"
                    )
        return page

    return page


def open_corporate_login_page(page: Page, context: BrowserContext, url: str) -> Page:
    """
    打开企业网银登录页。url 可以是登录页本身，也可以是首页（再点击「企业网银登录」入口）。
    返回最终承载登录页的 Page（可能是新标签页）。
    """
    page = _goto_login_url_with_retries(page, context, url)
    assert_current_page_allowed(page, "初始页 page.goto")

    # 若当前页已有证书登录按钮，则视为直达登录页
    try:
        page.locator(CERT_LOGIN_BUTTON_SELECTOR).wait_for(state="visible", timeout=2000)
        log.info("当前页已是企业网银登录页（检测到证书登录按钮）")
        return page
    except PlaywrightTimeout:
        pass

    log.info("尝试在页面上查找企业网银登录入口...")
    entry_locator: Optional[Locator] = None
    matched_text: Optional[str] = None
    for text in CORPORATE_LOGIN_LINK_TEXTS:
        try:
            candidate = page.get_by_text(text, exact=True).first
            candidate.wait_for(state="visible", timeout=3000)
            entry_locator = candidate
            matched_text = text
            break
        except (PlaywrightTimeout, PlaywrightError):
            continue
    if entry_locator is None:
        raise RuntimeError(
            "未找到企业网银登录入口，请检查页面或在 .env 中配置 ABC_LOGIN_URL"
        )
    log.info("命中登录入口文案：%s", matched_text)

    try:
        with context.expect_page(timeout=10000) as new_page_info:
            entry_locator.click()
        new_page = new_page_info.value
        new_page.wait_for_load_state("domcontentloaded")
        assert_current_page_allowed(new_page, "新标签页（企业网银登录）")
        try:
            new_page.wait_for_load_state(
                "networkidle", timeout=DEFAULT_NETWORKIDLE_TIMEOUT_MS
            )
        except PlaywrightTimeout:
            log.warning("登录页 networkidle 超时（可继续）")
        log.info("已切换到新标签页（企业网银登录页）")
        return new_page
    except PlaywrightTimeout:
        log.info("未捕获到新标签页，使用当前页继续")
        page.wait_for_load_state("domcontentloaded")
        assert_current_page_allowed(page, "登录入口点击后当前页")
        return page


def click_cert_login(page: Page) -> None:
    log.info("[证书登录] 等待按钮可见...")
    btn = page.locator(CERT_LOGIN_BUTTON_SELECTOR)
    btn.wait_for(state="visible", timeout=DEFAULT_VISIBLE_TIMEOUT_MS)
    close_tip_dialog_if_needed(page)
    log.info("[证书登录] 点击 %s", CERT_LOGIN_BUTTON_SELECTOR)
    # 后续是 Chrome 原生证书弹窗，不会触发常规 navigation 事件
    btn.click(no_wait_after=True)


# 只有这些上下文标识词出现在可见弹窗容器内时，才允许我们点击「确定/继续登录/我已知晓」。
# 不允许在整页随便点「确定」——单笔转账整页的提交按钮也叫「确定/下一步」，全局点击极易误提交。
TIP_DIALOG_CONTEXT_TOKENS = ("温馨提示", "继续登录", "已知晓", "请知悉")
TIP_DIALOG_BUTTON_TEXTS = ("继续登录", "我已知晓", "确定")
TIP_DIALOG_WRAPPER_SELECTOR = (
    ".el-dialog__wrapper:visible, .el-message-box__wrapper:visible, "
    "[role='dialog']:visible, [role='alertdialog']:visible"
)


def _click_tip_button_by_text(page: Page, quiet: bool = False) -> bool:
    """
    在可见的弹窗容器（.el-dialog__wrapper / .el-message-box__wrapper / role=dialog）
    内点击温馨提示按钮。**永远不会** 在整页范围按文本点 "确定"——这一点是有意的：
    单笔转账整页的提交按钮也叫「确定/下一步」，全局点击会触发提交。
    """
    wrappers = page.locator(TIP_DIALOG_WRAPPER_SELECTOR)
    try:
        count = wrappers.count()
    except PlaywrightError:
        return False
    if count == 0:
        if not quiet:
            log.info("[弹窗] 无可见弹窗容器，跳过文本点击")
        return False

    for i in range(count):
        wrapper = wrappers.nth(i)
        try:
            if not wrapper.is_visible():
                continue
            wrapper_text = wrapper.inner_text(timeout=1000)
        except (PlaywrightTimeout, PlaywrightError):
            continue
        # 上下文校验：弹窗内必须含「温馨提示/继续登录/已知晓」之一才允许点击。
        if not any(token in wrapper_text for token in TIP_DIALOG_CONTEXT_TOKENS):
            continue
        for text in TIP_DIALOG_BUTTON_TEXTS:
            for inner_selector in (
                f"button:has-text('{text}')",
                f".el-button:has-text('{text}')",
                f"[role='button']:has-text('{text}')",
            ):
                try:
                    button = wrapper.locator(inner_selector).last
                    button.wait_for(state="visible", timeout=500)
                    capture_live_screenshot(f"准备点击弹窗内按钮_{text}")
                    button.click(timeout=1500, force=True)
                    log.info("[弹窗] 已在温馨提示弹窗内点击「%s」", text)
                    capture_live_screenshot(f"已点击弹窗内按钮_{text}")
                    return True
                except (PlaywrightTimeout, PlaywrightError):
                    continue

    if not quiet:
        log.info("[弹窗] 可见弹窗容器内未命中温馨提示按钮")
    return False


_BROWSER_FOREGROUND_KEYWORDS = (
    "Chrome",
    "Chromium",
    "Edge",
    "农行",
    "农业银行",
    "企业网银",
    "企业网上银行",
    "Agricultural Bank",
)


def _foreground_is_browser_or_abc() -> tuple[bool, str]:
    """前台窗口是否看起来像浏览器或农行相关窗口。返回 (是否匹配, 当前标题)。"""
    title = _foreground_window_title()
    if not title:
        return False, ""
    return any(keyword in title for keyword in _BROWSER_FOREGROUND_KEYWORDS), title


def _click_continue_login_screen_fallback(reason: str) -> bool:
    """
    网页「温馨提示」DOM 未命中时的整屏坐标兜底。
    坐标来自 1920x1080/Chrome 前台截图：继续登录按钮位于屏幕约 (950, 590)。
    默认关闭：坐标点击会落在屏幕固定位置，前台不是浏览器/农行时极易误点别的应用。
    """
    if not env_flag("CONTINUE_LOGIN_COORD_FALLBACK", False):
        log.info("[弹窗] CONTINUE_LOGIN_COORD_FALLBACK 未启用（默认关闭），跳过坐标兜底")
        return False
    is_browser, title = _foreground_is_browser_or_abc()
    if not is_browser:
        log.warning(
            "[弹窗] 前台窗口标题=%r 不像浏览器/农行窗口，拒绝执行「继续登录」坐标点击",
            title or "<empty>",
        )
        return False
    try:
        width, height = pyautogui.size()
        x = round(width * env_float("CONTINUE_LOGIN_COORD_X_RATIO", 0.495))
        y = round(height * env_float("CONTINUE_LOGIN_COORD_Y_RATIO", 0.546))
        log.warning(
            "[弹窗] DOM 未命中，前台=%r，执行「继续登录」坐标兜底：%s，x=%s, y=%s",
            title,
            reason,
            x,
            y,
        )
        capture_live_screenshot("继续登录坐标兜底_点击前")
        old_failsafe = pyautogui.FAILSAFE
        try:
            pyautogui.FAILSAFE = False
            pyautogui.click(x, y)
        finally:
            pyautogui.FAILSAFE = old_failsafe
        safe_sleep(0.3)
        capture_live_screenshot("继续登录坐标兜底_点击后")
        return True
    except Exception as exc:
        log.warning("[弹窗] 「继续登录」坐标兜底失败: %s", type(exc).__name__)
        return False


def close_tip_dialog_if_needed(page: Page, quiet: bool = False) -> bool:
    """关闭可能出现的网页内「温馨提示」弹窗。返回是否关闭过。
    quiet=True 时仅在真正点掉弹窗才记日志，便于在轮询里反复调用。"""
    if not quiet:
        log.info("[弹窗] 检查是否存在温馨提示...")
    if _click_tip_button_by_text(page, quiet=quiet):
        return True

    wrappers = page.locator(".el-dialog__wrapper, .el-message-box__wrapper")
    try:
        count = wrappers.count()
    except PlaywrightError:
        return False
    if count == 0:
        if not quiet:
            log.info("[弹窗] 无网页内提示弹窗")
        return False

    for i in range(count):
        wrapper = wrappers.nth(i)
        try:
            if not wrapper.is_visible():
                continue
            text = wrapper.inner_text(timeout=1000)
            if "温馨提示" not in text:
                continue
            ok_btn = wrapper.locator(
                "button:has-text('继续登录'), .el-button:has-text('继续登录'), "
                "button:has-text('我已知晓'), .el-button:has-text('我已知晓'), "
                "button:has-text('确定'), .el-button:has-text('确定')"
            ).first
            ok_btn.click(timeout=3000)
            log.info("[弹窗] 已关闭温馨提示")
            return True
        except (PlaywrightTimeout, PlaywrightError):
            continue
    if not quiet:
        log.info("[弹窗] 没有需要关闭的温馨提示")
    return False


def wait_and_click_continue_login(page: Page, max_wait_s: float = 30.0) -> bool:
    """
    证书 OK 之后、K 宝密码窗口之前，农行有时会弹出含「继续登录」按钮的「温馨提示」。
    旧逻辑只在 handle_certificate_selection 里扫一次页面，扫得太早会错过弹窗时机。
    这里轮询最多 max_wait_s 秒，每秒静默检查一次，命中就点掉并返回 True。
    """
    log.info("[继续登录] 轮询等待「继续登录」温馨提示（最多 %.0fs）...", max_wait_s)
    start = time.time()
    deadline = time.time() + max_wait_s
    round_no = 0
    poll_log_interval = env_float("DEBUG_POLL_LOG_INTERVAL_S", 2.0)
    next_poll_log = start
    while time.time() < deadline:
        round_no += 1
        now = time.time()
        if now >= next_poll_log:
            log.info(
                "[继续登录] 第 %d 轮检查，已等待 %.1fs，剩余 %.1fs",
                round_no,
                now - start,
                max(0.0, deadline - now),
            )
            next_poll_log = now + poll_log_interval
        try:
            if close_tip_dialog_if_needed(page, quiet=True):
                log.info("[继续登录] 第 %d 轮命中并关闭温馨提示", round_no)
                return True
            if round_no == 1 and env_flag("CONTINUE_LOGIN_COORD_FALLBACK", False):
                if _click_continue_login_screen_fallback("继续登录轮询第 1 轮未通过 DOM 命中"):
                    log.info("[继续登录] 第 %d 轮已执行坐标兜底", round_no)
                    return True
        except (PlaywrightTimeout, PlaywrightError) as e:
            log.warning("[继续登录] 轮询出错（%s），跳过本次", type(e).__name__)
        safe_sleep(1.0)
    log.info("[继续登录] %.0fs 内未发现温馨提示，继续推进", max_wait_s)
    return False


def click_confirm_in_certificate_dialog(page: Page) -> bool:
    """通过 DOM 尝试点击证书弹窗中的「确定」。仅适用于网页模态版本。"""
    log.info("[证书弹窗] 尝试 DOM 点击确定...")
    selectors = [
        ".el-dialog__wrapper:visible .el-button--primary:visible",
        ".el-dialog:visible .el-button--primary:visible",
        "[role='dialog']:visible button:has-text('确定')",
        ".el-dialog__wrapper:visible button:has-text('确定')",
    ]
    for idx, selector in enumerate(selectors, start=1):
        try:
            log.info("[证书弹窗] DOM 选择器 %d/%d: %s", idx, len(selectors), selector)
            button = page.locator(selector).last
            button.wait_for(state="visible", timeout=2000)
            button.click(timeout=2000, force=True)
            log.info("[证书弹窗] DOM 点击成功")
            return True
        except (PlaywrightTimeout, PlaywrightError) as e:
            log.info("[证书弹窗] 选择器 %d 未命中（%s）", idx, type(e).__name__)
            continue

    log.info(
        "[证书弹窗] DOM 未命中；跳过 JS 兜底，避免 Chrome 原生证书模态框阻塞 Playwright"
    )
    return False


def press_enter_once(log_prefix: str) -> bool:
    """只发送一次 Enter；临时关闭 PyAutoGUI fail-safe，避免鼠标在角落导致登录流程中断。"""
    old_failsafe = pyautogui.FAILSAFE
    try:
        pyautogui.FAILSAFE = False
        before_title = _foreground_window_title()
        log.info("%s Enter 发送前前台窗口: %s", log_prefix, before_title or "<empty>")
        capture_live_screenshot(f"{log_prefix}_Enter前")
        pyautogui.press("enter")
        safe_sleep(0.2)
        after_title = _foreground_window_title()
        log.info("%s Enter 已发送；发送后前台窗口: %s", log_prefix, after_title or "<empty>")
        capture_live_screenshot(f"{log_prefix}_Enter后")
        return True
    except Exception as e:
        log.warning("%s Enter 发送失败: %s", log_prefix, type(e).__name__)
        capture_live_screenshot(f"{log_prefix}_Enter失败")
        return False
    finally:
        pyautogui.FAILSAFE = old_failsafe


def fallback_press_enter() -> bool:
    """
    Chrome 原生证书弹窗不在 DOM 中，用 OS 级 Enter 接受默认证书。
    必须先确认存在「选择证书」窗口，否则不按 Enter，避免把回车送到任意焦点窗口。
    只发送 1 次 Enter，避免连按引发后续意外。返回是否实际发送过 Enter。
    """
    if not focus_native_window_by_title(("选择证书",), wait_seconds=2.0):
        log.warning("[Enter 兜底] 未确认「选择证书」窗口，已放弃自动按 Enter（请人工处理）")
        return False
    log.info("[Enter 兜底] 已聚焦选择证书窗口，发送 1 次 Enter")
    return press_enter_once("[Enter 兜底]")


def focus_native_window_by_title(
    title_keywords: Iterable[str], wait_seconds: float = 2.0
) -> bool:
    """按窗口标题查找并聚焦原生弹窗，避免 Playwright 在浏览器原生模态框上卡住。"""
    try:
        import win32gui
    except ImportError:
        log.warning(
            "[原生窗口] pywin32/win32gui 不可用，无法自动确认原生证书窗口或 K 宝密码窗口；"
            "本次将放弃自动按 Enter 与自动输入密码，请人工处理。"
            "建议执行 `pip install pywin32` 安装后再运行。"
        )
        return False

    keywords = tuple(title_keywords)
    start = time.time()
    deadline = time.time() + wait_seconds
    round_no = 0
    poll_log_interval = env_float("DEBUG_POLL_LOG_INTERVAL_S", 2.0)
    next_poll_log = start
    while time.time() < deadline:
        round_no += 1
        matched: list[tuple[int, str]] = []

        def enum_window(hwnd, _extra) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if title and any(keyword in title for keyword in keywords):
                matched.append((hwnd, title))

        win32gui.EnumWindows(enum_window, None)
        now = time.time()
        if now >= next_poll_log:
            log.info(
                "[原生窗口] 第 %d 轮查找 %s，命中 %d 个，剩余 %.1fs",
                round_no,
                "/".join(keywords),
                len(matched),
                max(0.0, deadline - now),
            )
            next_poll_log = now + poll_log_interval
        if matched:
            hwnd, title = matched[0]
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception as e:
                # Windows 对前台权限有限制（其他进程持有前台等），失败时不能假设已聚焦。
                # 不静默吞掉，记录后继续轮询，避免把按键/密码送到错误窗口。
                log.warning(
                    "[原生窗口] SetForegroundWindow 失败（%s）: %s；继续等待",
                    type(e).__name__,
                    title,
                )
            else:
                # 短暂让出 CPU，等系统真的把窗口提到前台
                safe_sleep(0.1)
                try:
                    fg_hwnd = win32gui.GetForegroundWindow()
                except Exception as e:
                    log.warning(
                        "[原生窗口] GetForegroundWindow 失败（%s），无法确认前台",
                        type(e).__name__,
                    )
                    fg_hwnd = None
                if fg_hwnd == hwnd:
                    log.info("[原生窗口] 命中并聚焦窗口: %s", title)
                    return True
                log.warning(
                    "[原生窗口] 命中窗口但未能聚焦（当前前台 hwnd=%s, 目标 hwnd=%s）: %s；"
                    "继续等待，若最终仍无法聚焦则放弃自动输入/按键",
                    fg_hwnd,
                    hwnd,
                    title,
                )
        safe_sleep(0.2)
    return False


def press_enter_after_cert_login_without_title() -> bool:
    """
    刚点击「证书登录」后的受控无标题 Enter（默认关闭，需显式 opt-in）。

    业务事实：点击 #m-kbbtn-new 之后，Chrome 弹出的证书选择框默认按钮就是「确定」，
    且此时浏览器是脚本刚激活的前台窗口；理论上只发送 1 次 Enter 即可接受当前证书。
    但是无标题 Enter 是有真实风险的：如果在我们发 Enter 时 K 宝密码窗口已经弹出，
    Enter 会被密码窗口吞掉，可能让 K 宝以"空密码"提交一次，消耗仅有的尝试机会。
    因此这条路径默认关闭，必须显式 CERT_ENTER_AFTER_CLICK=true 才会执行。

    安全约束：
      - 默认 False：调用前会先短查 K 宝密码窗口，若已经存在 K 宝窗口则拒发 Enter。
      - 仅由 handle_certificate_selection 在 click_cert_login(page) 成功之后调用。
      - 只发送 1 次 Enter，press_enter_once 内部不循环；本函数也不重试。

    返回值：是否实际发送了 Enter。
    """
    if not env_flag("CERT_ENTER_AFTER_CLICK", False):
        log.info(
            "[证书弹窗] CERT_ENTER_AFTER_CLICK 未启用（默认关闭），跳过无标题 Enter；"
            "若所有自动路径均失败将回退到 manual_required"
        )
        return False
    # 进入无标题 Enter 前再做一次保护：K 宝密码窗口可能此时已经在前台，
    # 此时再按 Enter 会以空密码触发提交，浪费 K 宝尝试次数。
    if focus_native_window_by_title(KB_WINDOW_TITLES, wait_seconds=0.5):
        log.warning(
            "[证书弹窗] 即将发无标题 Enter 前检测到 K 宝密码窗口，立即放弃 Enter，"
            "改由调用方进入密码输入路径，避免误把空密码提交给 K 宝"
        )
        return False
    log.info(
        "[证书弹窗] 证书登录后受控无标题 Enter：未通过窗口标题确认原生证书窗口，"
        "也未检测到 K 宝窗口，按业务约定发送 1 次 Enter 接受默认证书"
    )
    sent = press_enter_once("[证书弹窗]")
    if sent:
        log.info("[证书弹窗] 受控无标题 Enter 已发送（仅 1 次，不循环）")
    else:
        log.warning("[证书弹窗] 受控无标题 Enter 发送失败，可能需要人工接管")
    return sent


def wait_for_kb_or_late_certificate_window_after_enter(
    page: Page, total_wait_s: float, kb_after_cert_wait_s: float
) -> str:
    """
    受控无标题 Enter 发出后，不立刻恢复 DOM 调用；继续只轮询原生窗口。
    这样能同时覆盖两种真实现场：
      1) Enter 已经确认默认证书，K 宝密码窗口很快出现；
      2) Enter 发早了，Chrome「选择证书」窗口几十秒后才出现。
    """
    if total_wait_s <= 0:
        return ""
    try:
        import win32gui
    except ImportError:
        log.warning("[证书弹窗] win32gui 不可用，无法继续监听晚到证书/K 宝窗口")
        return ""

    def find_windows(keywords: Iterable[str]) -> list[tuple[int, str]]:
        matched: list[tuple[int, str]] = []
        terms = tuple(keywords)

        def enum_window(hwnd, _extra) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if title and any(keyword in title for keyword in terms):
                matched.append((hwnd, title))

        win32gui.EnumWindows(enum_window, None)
        return matched

    def focus_window(hwnd: int, title: str, label: str) -> bool:
        try:
            win32gui.SetForegroundWindow(hwnd)
            safe_sleep(0.1)
            if win32gui.GetForegroundWindow() == hwnd:
                log.info("%s 命中并聚焦窗口: %s", label, title)
                return True
        except Exception as e:
            log.warning("%s 聚焦失败（%s）: %s", label, type(e).__name__, title)
            return False
        log.warning("%s 命中但未能确认前台: %s", label, title)
        return False

    start = time.time()
    deadline = start + total_wait_s
    poll_log_interval = env_float("DEBUG_POLL_LOG_INTERVAL_S", 2.0)
    next_poll_log = start
    round_no = 0
    log.info(
        "[证书弹窗] 受控 Enter 后继续仅轮询原生窗口（最多 %.0fs），等待 K 宝或晚到证书窗口",
        total_wait_s,
    )
    while time.time() < deadline:
        round_no += 1
        kb_matches = find_windows(KB_WINDOW_TITLES)
        if kb_matches:
            hwnd, title = kb_matches[0]
            if focus_window(hwnd, title, "[证书弹窗]"):
                log.info("[证书弹窗] 受控 Enter 后已进入 K 宝密码窗口")
                debug_checkpoint("受控 Enter 后进入 K 宝密码窗口", page, skip_page_screenshot=True)
                return CERT_PASSWORD_WINDOW_DETECTED

        cert_matches = find_windows(("选择证书",))
        if cert_matches:
            hwnd, title = cert_matches[0]
            if focus_window(hwnd, title, "[证书弹窗]"):
                log.info("[证书弹窗] 检测到晚到的原生选择证书窗口，发送 Enter")
                if not press_enter_once("[证书弹窗-延迟证书]"):
                    debug_checkpoint(
                        "晚到证书弹窗 Enter 发送失败", page, skip_page_screenshot=True
                    )
                    return CERT_MANUAL_REQUIRED
                debug_checkpoint("晚到证书弹窗已按 Enter", page, skip_page_screenshot=True)
                if focus_native_window_by_title(KB_WINDOW_TITLES, wait_seconds=kb_after_cert_wait_s):
                    log.info("[证书弹窗] 晚到证书确认后已进入 K 宝密码窗口")
                    debug_checkpoint(
                        "晚到证书确认后进入 K 宝密码窗口", page, skip_page_screenshot=True
                    )
                    return CERT_PASSWORD_WINDOW_DETECTED
                return CERT_AUTO_CONFIRMED

        now = time.time()
        if now >= next_poll_log:
            log.info(
                "[证书弹窗] 受控 Enter 后第 %d 轮原生窗口观察，K宝=%d，选择证书=%d，剩余 %.1fs",
                round_no,
                len(kb_matches),
                len(cert_matches),
                max(0.0, deadline - now),
            )
            next_poll_log = now + poll_log_interval
        safe_sleep(0.2)

    log.info("[证书弹窗] 受控 Enter 后原生窗口观察结束，未检测到 K 宝或晚到证书窗口")
    return ""


_UIA_CHROME_TITLE_KEYWORDS = ("Chrome", "Chromium", "Edge")
_UIA_CERT_REQUIRED_TOKENS = ("选择证书",)
_UIA_CERT_HOST_TOKENS = (
    "cbank.abchina.com.cn:443",
    "cbank.abchina.com.cn",
)
_UIA_CERT_OK_BUTTON_TEXTS = ("确定", "OK")
_UIA_CERT_CANCEL_BUTTON_TEXTS = ("取消", "Cancel")
_UIA_TRAVERSAL_MAX_DEPTH = 25
_UIA_TRAVERSAL_MAX_NODES = 4000


def _collect_uia_descendants_text_and_buttons(
    root,
):
    """
    深度受限地遍历 UIA 子树，收集（拼接的所有文本，OK 按钮控件，Cancel 按钮控件）。
    单个控件的取值都用 try/except 包住——UIA 在 Chrome 内嵌弹窗里偶尔会抛
    ElementNotEnabled / TimeoutError / COMError，遇到一个异常时不该让整次扫描崩溃。
    任何按钮/文本明细都不会写到日志里，避免泄露证书序列号或主体。
    """
    texts: list[str] = []
    ok_btn = None
    cancel_btn = None
    visited = 0

    def _node_text(ctrl) -> str:
        try:
            wt = ctrl.window_text() or ""
        except Exception:
            wt = ""
        try:
            name = ctrl.element_info.name or ""
        except Exception:
            name = ""
        return (wt + " " + name).strip()

    def _control_type(ctrl) -> str:
        try:
            return ctrl.element_info.control_type or ""
        except Exception:
            return ""

    stack: list[tuple[object, int]] = [(root, 0)]
    while stack:
        if visited >= _UIA_TRAVERSAL_MAX_NODES:
            break
        ctrl, depth = stack.pop()
        visited += 1
        if depth > _UIA_TRAVERSAL_MAX_DEPTH:
            continue
        text = _node_text(ctrl)
        if text:
            texts.append(text)
        ctype = _control_type(ctrl)
        if ctype == "Button":
            stripped = text.strip()
            if ok_btn is None and any(t == stripped or t in stripped for t in _UIA_CERT_OK_BUTTON_TEXTS):
                ok_btn = ctrl
            elif cancel_btn is None and any(
                t == stripped or t in stripped for t in _UIA_CERT_CANCEL_BUTTON_TEXTS
            ):
                cancel_btn = ctrl
        try:
            children = ctrl.children()
        except Exception:
            children = []
        for child in children:
            stack.append((child, depth + 1))
    blob = " | ".join(texts)
    return blob, ok_btn, cancel_btn, visited


def confirm_chrome_certificate_dialog_via_uia(wait_seconds: float = 3.0) -> bool:
    """
    使用 pywinauto 的 UIA 后端识别并确认 Chrome / Edge 内嵌的「选择证书」模态框。

    业务背景：某些 Chrome / Chrome for Testing 版本下，证书选择框不会作为独立的
    top-level 窗口存在（顶层窗口标题仍然是浏览器主窗口标题，例如
    「中国农业银行企业金融服务平台 - Google Chrome for Testing」），导致基于
    `win32gui.GetWindowText` 的标题枚举命中 0 个；但 UIA 树里仍能看到证书弹窗。

    判定条件（必须同时满足才允许自动确认，缺一不点）：
      - UIA 文本中出现「选择证书」（_UIA_CERT_REQUIRED_TOKENS）
      - UIA 文本中出现 cbank.abchina.com.cn 或带 :443
        （_UIA_CERT_HOST_TOKENS）
      - 同时存在「确定」与「取消」两个 Button 控件
    任何一个不满足就不会发出确认动作，避免把当前页面里的别的「确定」当成证书确定。

    成功路径：
      1) 优先调用 OK 按钮的 `invoke()`（UIA InvokePattern）。
      2) 失败再尝试 `click_input()`（鼠标移动到按钮中心点击）。
      3) 两者都失败、但前台仍是匹配到的 Chrome 窗口时，发送 1 次 Enter 兜底
         （焦点已经在弹窗，default 按钮就是「确定」）。

    日志只记录是否检测到、是否点击成功，**不输出**证书主体、序列号、签发者等
    任何明细，避免敏感信息落盘。

    pywinauto 缺失或 UIA 调用本身失败时直接返回 False，由调用方决定下一步。
    """
    try:
        from pywinauto import Desktop  # type: ignore
    except ImportError:
        log.info(
            "[证书 UIA] pywinauto 不可用，跳过 UIA 证书检测；"
            "如需此自动路径请执行 `pip install pywinauto`"
        )
        return False
    except Exception as e:
        log.warning("[证书 UIA] 加载 pywinauto 失败（%s），跳过 UIA 证书检测", type(e).__name__)
        return False

    deadline = time.time() + max(0.0, wait_seconds)
    poll_log_interval = env_float("DEBUG_POLL_LOG_INTERVAL_S", 2.0)
    next_poll_log = time.time()
    round_no = 0
    log.info(
        "[证书 UIA] 开始 UIA 证书弹窗检测，最多 %.1fs（不输出证书明细）", wait_seconds
    )
    while time.time() < deadline:
        round_no += 1
        try:
            desktop = Desktop(backend="uia")
            top_windows = list(desktop.windows())
        except Exception as e:
            log.warning("[证书 UIA] 枚举 UIA 顶层窗口失败（%s），放弃 UIA 路径", type(e).__name__)
            return False

        chrome_windows: list[tuple[object, str]] = []
        for win in top_windows:
            try:
                title = win.window_text() or ""
            except Exception:
                title = ""
            if title and any(k in title for k in _UIA_CHROME_TITLE_KEYWORDS):
                chrome_windows.append((win, title))

        now = time.time()
        if now >= next_poll_log:
            log.info(
                "[证书 UIA] 第 %d 轮，候选 Chrome/Edge 窗口=%d，剩余 %.1fs",
                round_no,
                len(chrome_windows),
                max(0.0, deadline - now),
            )
            next_poll_log = now + poll_log_interval

        for win, title in chrome_windows:
            try:
                blob, ok_btn, cancel_btn, visited = _collect_uia_descendants_text_and_buttons(win)
            except Exception as e:
                log.warning(
                    "[证书 UIA] 遍历窗口 UIA 子树失败（%s），跳过该窗口", type(e).__name__
                )
                continue
            has_cert_label = any(tok in blob for tok in _UIA_CERT_REQUIRED_TOKENS)
            has_cbank = any(tok in blob for tok in _UIA_CERT_HOST_TOKENS)
            has_buttons = ok_btn is not None and cancel_btn is not None
            if not (has_cert_label and has_cbank and has_buttons):
                # 命中条件不全：不要按 Enter，不要点页面其他确定按钮
                continue
            log.info(
                "[证书 UIA] 命中 Chrome 内嵌证书弹窗（含「选择证书」+ cbank.abchina.com.cn + 确定 + 取消，"
                "扫描节点 %d），尝试通过 UIA 点击确定",
                visited,
            )
            # 先 invoke
            try:
                ok_btn.invoke()
                log.info("[证书 UIA] 已通过 UIA invoke 点击「确定」")
                return True
            except Exception as e_invoke:
                log.warning(
                    "[证书 UIA] UIA invoke 失败（%s），改用 click_input", type(e_invoke).__name__
                )
            # 再 click_input（鼠标点击）
            try:
                ok_btn.click_input()
                log.info("[证书 UIA] 已通过 UIA click_input 点击「确定」")
                return True
            except Exception as e_click:
                log.warning(
                    "[证书 UIA] UIA click_input 失败（%s）", type(e_click).__name__
                )
            # UIA 路径都失败：仅在前台仍是当前 Chrome 窗口时发 1 次 Enter
            fg_title = _foreground_window_title()
            if fg_title and any(k in fg_title for k in _UIA_CHROME_TITLE_KEYWORDS):
                log.info(
                    "[证书 UIA] UIA 点击失败但前台仍是 Chrome/Edge 窗口，发送 1 次 Enter 兜底"
                )
                return press_enter_once("[证书 UIA]")
            log.warning(
                "[证书 UIA] UIA 点击失败且前台已非 Chrome/Edge 窗口（%s），放弃自动确认",
                fg_title or "<empty>",
            )
            return False
        safe_sleep(0.3)

    log.info("[证书 UIA] %.1fs 内未检测到符合条件的 Chrome 内嵌证书弹窗", wait_seconds)
    return False


def click_certificate_ok_after_cert_login_without_title() -> bool:
    """
    刚点击「证书登录」后的坐标兜底。

    Chrome 的证书选择框在当前农行页面上稳定居中，且「确定」按钮位于弹窗右下。
    当窗口标题检测和 Enter 都不可用时，仅在这个登录上下文中点击一次该区域。
    可用 ALLOW_CERT_CLICK_WITHOUT_TITLE=false 关闭。
    """
    if not env_flag("ALLOW_CERT_CLICK_WITHOUT_TITLE", False):
        log.info("[证书弹窗] ALLOW_CERT_CLICK_WITHOUT_TITLE 未启用，跳过无标题坐标点击兜底（默认关闭，避免误点页面）")
        return False
    old_failsafe = pyautogui.FAILSAFE
    try:
        pyautogui.FAILSAFE = False
        width, height = pyautogui.size()
        x = round(width * 0.4875)
        y = round(height * 0.339)
        log.warning(
            "[证书弹窗] 未能通过标题确认窗口，尝试点击证书「确定」按钮区域（x=%s, y=%s）",
            x,
            y,
        )
        pyautogui.click(x, y)
        return True
    except Exception as e:
        log.warning("[证书弹窗] 坐标点击证书「确定」区域失败: %s", type(e).__name__)
        return False
    finally:
        pyautogui.FAILSAFE = old_failsafe


def handle_certificate_selection(page: Page) -> str:
    """
    证书弹窗状态机（按顺序）：
      路径 A：top-level「选择证书」原生窗口检测 → focus + Enter
      K 宝预检：若 K 宝密码窗口已经出现，直接返回 CERT_PASSWORD_WINDOW_DETECTED
      路径 UIA：通过 pywinauto UIA 后端识别 Chrome 内嵌证书弹窗，UIA 点「确定」
      路径 B：CERT_ENTER_AFTER_CLICK=true 时的受控无标题 Enter（默认关闭）
      路径 B2：ALLOW_CERT_CLICK_WITHOUT_TITLE=true 时的坐标兜底（默认关闭）
      其它：返回 CERT_MANUAL_REQUIRED

    历史路径 C（close_tip_dialog_if_needed + click_confirm_in_certificate_dialog）
    与路径 D（fallback_press_enter）已**移除**：
      - Chrome 内嵌的原生证书模态会阻塞所有 Playwright 协议调用，DOM 路径会卡死；
      - fallback_press_enter 内部再做一次「选择证书」标题检测（与路径 A 重复），
        本身就解决不了 top-level 标题不暴露的核心问题，留着只会拖慢失败路径。
    取而代之的是 UIA 识别 + 显式 opt-in 的 Enter / 坐标兜底，识别失败一律走人工接管。

    返回值语义：
      - CERT_AUTO_CONFIRMED：某条自动路径成功（Enter 已发送或 UIA 已 invoke），
        Chrome 原生证书模态此时应当已消失，调用方可以安全做 DOM 轮询。
      - CERT_PASSWORD_WINDOW_DETECTED：已检测到 K 宝密码窗口，证书阶段彻底完成；
        调用方应跳过「继续登录」DOM 轮询，直接进入密码输入。
      - CERT_MANUAL_REQUIRED：所有自动路径均失败，且 Chrome 内嵌证书模态可能仍在
        前台阻塞 Playwright；调用方**禁止**做任何 DOM 调用（包括 close_tip / 截图），
        必须改用纯原生窗口轮询等待 K 宝密码窗口出现，等于由人工在浏览器中点完
        证书「确定」并进入 K 宝阶段后才恢复后续流程。
    """
    safe_sleep(1.0)  # 给弹窗一个出现的窗口
    debug_checkpoint("证书弹窗处理开始", page, skip_page_screenshot=True)
    cert_initial_window_wait_s = env_float("CERT_INITIAL_WINDOW_WAIT_S", 2.0)
    cert_uia_wait_s = env_float("CERT_UIA_WAIT_S", 3.0)
    cert_after_enter_watch_s = env_float("CERT_WINDOW_WAIT_S", 75.0)
    kb_after_cert_wait_s = env_float("KB_WINDOW_WAIT_AFTER_CERT_S", 8.0)

    # 路径 A：原生「选择证书」窗口已经存在并被成功聚焦 → 直接发送 Enter。
    log.info("[证书弹窗] 路径 A：先短等原生选择证书窗口（最多 %.0fs）", cert_initial_window_wait_s)
    if focus_native_window_by_title(("选择证书",), wait_seconds=cert_initial_window_wait_s):
        log.info("[证书弹窗] 原生选择证书窗口已聚焦，发送 Enter 接受当前证书")
        if not press_enter_once("[证书弹窗]"):
            log.warning("[证书弹窗] 原生窗口已聚焦但 Enter 发送失败，转为人工接管")
            debug_checkpoint("证书弹窗 Enter 发送失败", page, skip_page_screenshot=True)
            return CERT_MANUAL_REQUIRED
        debug_checkpoint("证书弹窗已按 Enter", page, skip_page_screenshot=True)
        safe_sleep(1.0)
        if focus_native_window_by_title(KB_WINDOW_TITLES, wait_seconds=kb_after_cert_wait_s):
            log.info("[证书弹窗] 路径 A 已进入 K 宝密码窗口，跳过后续路径")
            debug_checkpoint("已进入 K 宝密码窗口", page, skip_page_screenshot=True)
            return CERT_PASSWORD_WINDOW_DETECTED
        return CERT_AUTO_CONFIRMED

    # K 宝预检：若 K 宝密码窗口已经出现，证书阶段就完成了；继续按 Enter 会以
    # 空密码提交并消耗 K 宝有限的尝试次数。
    if focus_native_window_by_title(KB_WINDOW_TITLES, wait_seconds=0.5):
        log.info(
            "[证书弹窗] 路径 A 后预检：已观察到 K 宝密码窗口，跳过 UIA / Enter / 坐标"
        )
        debug_checkpoint("路径 A 后已检测到 K 宝密码窗口", page, skip_page_screenshot=True)
        return CERT_PASSWORD_WINDOW_DETECTED

    # 路径 UIA：top-level 标题没暴露「选择证书」的 Chrome / Edge 版本，从 UIA 树识别。
    log.info("[证书弹窗] 路径 UIA：尝试通过 UIA 识别 Chrome 内嵌证书弹窗")
    if confirm_chrome_certificate_dialog_via_uia(wait_seconds=cert_uia_wait_s):
        debug_checkpoint("证书弹窗 UIA 已确认", page, skip_page_screenshot=True)
        safe_sleep(1.0)
        if focus_native_window_by_title(KB_WINDOW_TITLES, wait_seconds=kb_after_cert_wait_s):
            log.info("[证书弹窗] UIA 确认后已进入 K 宝密码窗口")
            debug_checkpoint("UIA 确认后进入 K 宝密码窗口", page, skip_page_screenshot=True)
            return CERT_PASSWORD_WINDOW_DETECTED
        return CERT_AUTO_CONFIRMED

    # UIA 后再做一次 K 宝预检，UIA 扫描期间 K 宝可能刚好弹出来。
    if focus_native_window_by_title(KB_WINDOW_TITLES, wait_seconds=0.5):
        log.info("[证书弹窗] UIA 后预检：观察到 K 宝密码窗口，跳过 Enter / 坐标")
        debug_checkpoint("UIA 后已检测到 K 宝密码窗口", page, skip_page_screenshot=True)
        return CERT_PASSWORD_WINDOW_DETECTED

    # 路径 B：CERT_ENTER_AFTER_CLICK=true 显式启用时才发受控 Enter（默认 false）。
    if press_enter_after_cert_login_without_title():
        debug_checkpoint("证书弹窗无标题 Enter 已尝试", page, skip_page_screenshot=True)
        status = wait_for_kb_or_late_certificate_window_after_enter(
            page,
            total_wait_s=cert_after_enter_watch_s,
            kb_after_cert_wait_s=kb_after_cert_wait_s,
        )
        if status:
            return status
        return CERT_AUTO_CONFIRMED

    # 路径 B2：ALLOW_CERT_CLICK_WITHOUT_TITLE=true 显式启用时才坐标点击（默认 false）。
    if click_certificate_ok_after_cert_login_without_title():
        debug_checkpoint("证书弹窗坐标点击已尝试", page, skip_page_screenshot=True)
        safe_sleep(1.5)
        if focus_native_window_by_title(KB_WINDOW_TITLES, wait_seconds=kb_after_cert_wait_s):
            log.info("[证书弹窗] 坐标点击后已进入 K 宝密码窗口")
            debug_checkpoint("坐标点击后进入 K 宝密码窗口", page, skip_page_screenshot=True)
            return CERT_PASSWORD_WINDOW_DETECTED
        return CERT_AUTO_CONFIRMED

    # 全部自动路径失败：必须停止任何 Playwright DOM 调用，否则会被 Chrome 内嵌的
    # 原生证书模态阻塞。调用方负责仅用原生窗口轮询等待 K 宝窗口出现。
    log.warning(
        "[证书弹窗] 自动处理全部失败：top-level 检测、K 宝预检、UIA 识别、CERT_ENTER_AFTER_CLICK 均未命中。"
        "Chrome 内嵌证书模态框可能仍在前台阻塞 Playwright，脚本将停止 DOM 轮询，"
        "改用原生窗口检测等待 K 宝密码窗口出现。请在浏览器中：1) 选择证书并点击「确定」；"
        "2) 若出现「继续登录」温馨提示请手动点击。"
    )
    debug_checkpoint("证书弹窗自动处理失败", page, skip_page_screenshot=True)
    return CERT_MANUAL_REQUIRED


def wait_for_kb_password_window(wait_seconds: float) -> bool:
    """
    仅通过原生窗口标题轮询 K 宝密码窗口；不触碰 Playwright，因此即使 Chrome 内嵌
    证书模态仍在前台阻塞 DOM，本检查也能继续运行。命中后会顺带把窗口提到前台。
    """
    log.info(
        "[K 宝] 仅通过原生窗口标题轮询 K 宝密码窗口（最多 %.0fs，期间不做 DOM 调用）...",
        wait_seconds,
    )
    return focus_native_window_by_title(KB_WINDOW_TITLES, wait_seconds=wait_seconds)


def input_kb_password(password: str, wait_seconds: float = 10.0) -> bool:
    """
    K 宝密码框由硬件厂商插件渲染，不在 DOM 中。
    必须先确认「验证 K 宝密码」原生窗口存在并已聚焦，否则放弃输入避免把密码送到任意窗口。
    默认自动按 Enter 继续登录；可通过 KB_PASSWORD_AUTO_ENTER=false 关闭。
    返回是否实际完成了自动输入。
    """
    if not password:
        log.warning("[密码] 密码为空，跳过自动输入")
        return False
    if not password.isascii():
        # pyautogui.write 在中文/全角等非 ASCII 字符上会发出错误的按键，
        # 错误的密码会消耗 K 宝的有限尝试次数，导致 USB Key 锁卡。
        log.error(
            "[密码] K 宝密码包含非 ASCII 字符，pyautogui.write 可能发送错误按键，"
            "已放弃自动输入；请在浏览器弹出的 K 宝窗口中手动输入"
        )
        return False
    log.info("[密码] 准备自动输入 K 宝密码（不会显示具体字符）")
    if not wait_for_kb_password_window(wait_seconds):
        log.warning(
            "[密码] 未在 %.0fs 内确认 K 宝密码窗口，已放弃自动输入；"
            "请在浏览器弹出的 K 宝窗口中手动输入",
            wait_seconds,
        )
        return False
    log.info("[密码] 已确认 K 宝密码窗口，开始输入...")
    try:
        pyautogui.write(password, interval=KB_PASSWORD_TYPE_INTERVAL_S)
        if env_flag("KB_PASSWORD_AUTO_ENTER", True):
            safe_sleep(0.3)
            pyautogui.press("enter")
            log.info("[密码] 已自动按 Enter（KB_PASSWORD_AUTO_ENTER=true）")
        else:
            log.info("[密码] 已输入字符，未自动按 Enter（KB_PASSWORD_AUTO_ENTER=false）")
        return True
    except Exception as e:
        log.error("[密码] 自动输入失败: %s（请人工输入）", type(e).__name__)
        return False

