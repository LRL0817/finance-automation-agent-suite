import time

from .debug import _debug_checkpoint
from .keyboard import _ITC_OK, _send_ascii, _send_enter
from .windows import _focus_ushield_pin_input, _submit_ushield_pin_with_soft_keyboard

def _text_visible_in_frames(page, text: str, exact: bool = True, timeout_ms: int = 300) -> bool:
    for frame in page.frames:
        try:
            if frame.get_by_text(text, exact=exact).first.is_visible(timeout=timeout_ms):
                return True
        except Exception:
            pass
    return False


def _click_certificate_login(page, timeout_ms: int = 15000) -> bool:
    """Click the BOC login tab labeled "数字证书登录"."""
    _debug_checkpoint("准备点击数字证书登录", page)
    for frame in page.frames:
        try:
            tab = frame.get_by_text("数字证书登录", exact=True).first
            tab.wait_for(state="visible", timeout=timeout_ms)
            tab.click(no_wait_after=True, timeout=timeout_ms)
            _debug_checkpoint("数字证书登录已点击_Playwright定位", page)
            return True
        except Exception:
            pass

        try:
            if frame.evaluate(
                r"""
            () => {
                const normalize = (text) => (text || '').replace(/\s+/g, '').trim();
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && box.width > 0
                        && box.height > 0;
                };
                const candidates = Array.from(document.querySelectorAll('button,a,div,span,li'))
                    .filter((el) => visible(el) && normalize(el.innerText || el.textContent) === '数字证书登录')
                    .sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width);
                if (!candidates.length) return false;
                candidates[0].click();
                return true;
            }
            """
            ):
                _debug_checkpoint("数字证书登录已点击_JS定位", page)
                return True
        except Exception:
            pass
    _debug_checkpoint("数字证书登录未找到", page)
    return False


def _click_password_input(page, timeout_ms: int = 8000) -> bool:
    """Find the 用户密码 input and click it to focus."""
    _debug_checkpoint("准备聚焦用户密码输入框", page)
    selectors = (
        'input[type="password"]',
        'input[placeholder*="密码"]',
        'input[placeholder*="登录密码"]',
    )
    deadline = time.time() + timeout_ms / 1000
    last_error = None
    while time.time() < deadline:
        for frame in page.frames:
            for sel in selectors:
                try:
                    loc = frame.locator(sel).first
                    if loc.is_visible(timeout=300):
                        loc.scroll_into_view_if_needed(timeout=2000)
                        loc.click(timeout=2000)
                        print(f"[已点击] 用户密码输入框 ({sel})", flush=True)
                        _debug_checkpoint("用户密码输入框已点击", page)
                        return True
                except Exception as exc:
                    last_error = exc
        time.sleep(0.2)

    # Fallback: click just under the "用户密码" label
    for frame in page.frames:
        try:
            label = frame.get_by_text("用户密码", exact=True).first
            box = label.bounding_box()
            if box:
                cx = box["x"] + box["width"] / 2
                cy = box["y"] + box["height"] + 28
                page.mouse.click(cx, cy)
                print(f"[已点击] 用户密码框 (标签下方 {cx:.0f},{cy:.0f})", flush=True)
                _debug_checkpoint("用户密码输入框已坐标点击", page)
                return True
        except Exception as exc:
            last_error = exc
    if last_error:
        print(f"[调试] 用户密码输入框定位失败：{last_error!r}", flush=True)
    _debug_checkpoint("用户密码输入框定位失败", page)
    return False


def _click_login_button(page, timeout_ms: int = 6000) -> bool:
    """Click the red 登录 button on the password-login panel."""
    _debug_checkpoint("准备点击网页登录按钮", page)
    # role-based exact match avoids matching the "密码登录" tab
    deadline = time.time() + timeout_ms / 1000
    last_error = None
    while time.time() < deadline:
        for frame in page.frames:
            try:
                btn = frame.get_by_role("button", name="登录", exact=True).first
                if btn.is_visible(timeout=300):
                    btn.click(timeout=2000)
                    print("[已点击] 登录按钮 (role=button)", flush=True)
                    _debug_checkpoint("网页登录按钮已点击_role", page)
                    return True
            except Exception as exc:
                last_error = exc
        time.sleep(0.2)

    selectors = (
        'button:text-is("登录")',
        '[class*="login-btn"]',
        '[class*="loginBtn"]',
        'div[role="button"]:has-text("登录")',
    )
    for frame in page.frames:
        for sel in selectors:
            try:
                btn = frame.locator(sel).first
                if btn.is_visible(timeout=300):
                    btn.scroll_into_view_if_needed(timeout=2000)
                    btn.click(timeout=2000)
                    print(f"[已点击] 登录按钮 ({sel})", flush=True)
                    _debug_checkpoint("网页登录按钮已点击_selector", page)
                    return True
            except Exception as exc:
                last_error = exc
    if last_error:
        print(f"[调试] 登录按钮定位失败：{last_error!r}", flush=True)
    _debug_checkpoint("网页登录按钮定位失败", page)
    return False


def _fill_page_password_and_login(page, password: str, timeout_ms: int = 15000) -> bool:
    """Click password box → type password → click 登录."""
    _debug_checkpoint("开始网页登录密码流程", page)
    try:
        page.wait_for_url("**netc2.igtb.boc.cn**", timeout=timeout_ms)
    except Exception:
        pass

    # Wait for the 用户密码 label so the form is fully rendered
    deadline = time.time() + timeout_ms / 1000
    password_label_seen = False
    while time.time() < deadline:
        if _text_visible_in_frames(page, "用户密码", exact=True, timeout_ms=500):
            password_label_seen = True
            break
        time.sleep(0.5)
    if password_label_seen:
        _debug_checkpoint("网页登录密码页已出现用户密码", page)
    else:
        _debug_checkpoint("网页登录密码页未检测到用户密码", page)

    try:
        page.bring_to_front()
    except Exception:
        pass

    # Step 1: click the password input to focus it
    if not _click_password_input(page, timeout_ms=8000):
        print("[警告] 未找到用户密码输入框", flush=True)
        _debug_checkpoint("网页登录密码流程失败_未找到输入框", page)
        return False
    time.sleep(0.4)

    # Step 2: clear, then type the password with real key events
    try:
        page.keyboard.press("Control+A")
        time.sleep(0.1)
        page.keyboard.press("Delete")
        time.sleep(0.1)
    except Exception:
        pass

    typed = False
    try:
        page.keyboard.type(password, delay=80)
        typed = True
    except Exception as exc:
        print(f"[调试] Playwright 输入用户密码失败：{exc!r}", flush=True)
    if not typed:
        try:
            _send_ascii(password)
            typed = True
        except Exception as exc:
            print(f"[警告] 原生键盘输入用户密码失败：{exc}", flush=True)
    if not typed:
        print("[警告] 输入用户密码失败", flush=True)
        _debug_checkpoint("网页登录密码流程失败_密码未输入", page)
        return False
    print("[已输入] 用户密码", flush=True)
    _debug_checkpoint("网页登录密码已输入", page)
    time.sleep(0.6)

    # Step 3: click the red 登录 button
    if not _click_login_button(page, timeout_ms=6000):
        print("[警告] 未能点击登录按钮", flush=True)
        _debug_checkpoint("网页登录密码流程失败_登录按钮未点击", page)
        return False
    _debug_checkpoint("网页登录密码流程完成_已点登录", page)
    return True


def _is_page_password_login_ready(page) -> bool:
    """Return true when the netc2 web password-login page is visible."""
    if "netc2.igtb.boc.cn" in page.url:
        return True
    for frame in page.frames:
        try:
            if frame.get_by_text("用户密码", exact=True).first.is_visible(timeout=300):
                return True
        except Exception:
            pass
    return False


def _maybe_submit_ushield_password(
    page,
    pin: str,
    auto_submit: bool = False,
    timeout_s: float = 4.0,
) -> bool:
    """Handle the U-shield PIN step without blind typing by default."""
    _debug_checkpoint("开始U盾PIN处理")

    if auto_submit:
        if not pin:
            print("[跳过] 已启用自动 U盾 PIN，但未配置 BOC_USHIELD_PIN", flush=True)
            _debug_checkpoint("自动U盾PIN跳过_未配置PIN")
            return False
        time.sleep(timeout_s)
        _debug_checkpoint("准备聚焦U盾PIN窗口")
        _focus_ushield_pin_input(wait_seconds=4.0)
        try:
            _debug_checkpoint("准备自动输入U盾PIN")
            if _ITC_OK:
                _send_ascii(pin)
                _send_enter()
            elif not _submit_ushield_pin_with_soft_keyboard(pin):
                print("[U盾] 软键盘输入失败，尝试 SendInput 回退", flush=True)
                _send_ascii(pin)
                _send_enter()
        except Exception as exc:
            print(f"[警告] 自动输入 U盾 PIN 失败：{exc}", flush=True)
            _debug_checkpoint("自动U盾PIN失败")
            return False
        _debug_checkpoint("已提交U盾PIN")
        time.sleep(10)
        _debug_checkpoint("U盾PIN提交后等待完成", page)
        return True

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _is_page_password_login_ready(page):
            _debug_checkpoint("已到网页登录密码页_无需输入U盾PIN", page)
            return False
        time.sleep(0.5)

    if not auto_submit:
        print("[等待] 请在 U盾/证书弹窗中手动输入 PIN 并确认；脚本不会自动盲打 U盾 PIN", flush=True)
        _debug_checkpoint("等待人工输入U盾PIN", page)
        deadline = time.time() + 120
        while time.time() < deadline:
            if _is_page_password_login_ready(page):
                _debug_checkpoint("人工U盾PIN后进入网页登录密码页", page)
                return False
            time.sleep(0.5)
        print("[提示] 仍未检测到网页登录页，后续步骤会继续按网页登录页超时处理", flush=True)
        _debug_checkpoint("人工U盾PIN等待超时", page)
        return False


def _click_payment_transfer(page, timeout_ms: int = 60000) -> bool:
    """登录完成后：点击顶部「付款服务」→「转账汇款」。"""
    _debug_checkpoint("开始进入付款服务转账汇款", page)
    # 等待主页菜单渲染出来（付款服务 文本可见即可）
    deadline = time.time() + timeout_ms / 1000
    payment_loc = None
    payment_frame = None
    while time.time() < deadline:
        for frame in page.frames:
            try:
                loc = frame.get_by_text("付款服务", exact=True).first
                if loc.is_visible(timeout=300):
                    payment_loc = loc
                    payment_frame = frame
                    break
            except Exception:
                continue
        if payment_loc is not None:
            break
        time.sleep(0.5)

    if payment_loc is None:
        print("[警告] 未找到 付款服务 菜单", flush=True)
        _debug_checkpoint("付款服务菜单未找到", page)
        return False
    _debug_checkpoint("付款服务菜单已找到", page)

    try:
        payment_loc.scroll_into_view_if_needed(timeout=2000)
    except Exception:
        pass
    try:
        payment_loc.hover(timeout=3000)
    except Exception:
        pass
    time.sleep(0.3)
    try:
        payment_loc.click(timeout=5000)
        print("[已点击] 付款服务", flush=True)
        _debug_checkpoint("付款服务已点击", page)
    except Exception:
        print("[警告] 点击 付款服务 失败", flush=True)
        _debug_checkpoint("付款服务点击失败", page)
        return False
    time.sleep(0.8)

    # 子菜单中点击「转账汇款」
    end = time.time() + 15
    while time.time() < end:
        frames = [payment_frame] if payment_frame else []
        frames += [f for f in page.frames if f is not payment_frame]
        for frame in frames:
            if frame is None:
                continue
            try:
                sub = frame.get_by_text("转账汇款", exact=True).first
                if sub.is_visible(timeout=300):
                    try:
                        sub.scroll_into_view_if_needed(timeout=1500)
                    except Exception:
                        pass
                    try:
                        sub.click(timeout=5000)
                        print("[已点击] 转账汇款", flush=True)
                        _debug_checkpoint("转账汇款已点击", page)
                        return True
                    except Exception:
                        pass
            except Exception:
                continue
        # 子菜单可能 hover 才出现，再悬停一次
        try:
            payment_loc.hover(timeout=1500)
        except Exception:
            pass
        time.sleep(0.5)

    print("[警告] 未找到 转账汇款 入口", flush=True)
    _debug_checkpoint("转账汇款入口未找到", page)
    return False

