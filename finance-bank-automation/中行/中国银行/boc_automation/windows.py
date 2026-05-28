import time

from .config import (
    BOC_CERT_WINDOW_TITLES,
    BOC_UNWANTED_HOME_PREFIXES,
    BOC_USHIELD_PIN_WINDOW_TITLES,
    _UIA_CERT_CANCEL_BUTTON_TEXTS,
    _UIA_CERT_HOST_TOKENS,
    _UIA_CERT_OK_BUTTON_TEXTS,
    _UIA_CERT_REQUIRED_TOKENS,
    _UIA_CHROME_TITLE_KEYWORDS,
    _UIA_TRAVERSAL_MAX_DEPTH,
    _UIA_TRAVERSAL_MAX_NODES,
)
from .debug import _debug_checkpoint
from .keyboard import _send_ascii, _send_enter

def _foreground_window_title() -> str:
    try:
        import win32gui  # type: ignore

        hwnd = win32gui.GetForegroundWindow()
        return win32gui.GetWindowText(hwnd) if hwnd else ""
    except Exception:
        return ""


def _get_clipboard_text() -> str:
    import win32clipboard  # type: ignore
    import win32con  # type: ignore

    win32clipboard.OpenClipboard()
    try:
        if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return ""
        return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def _set_clipboard_text(text: str) -> None:
    import win32clipboard  # type: ignore

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text)
    finally:
        win32clipboard.CloseClipboard()


def _read_foreground_browser_url() -> str:
    import pyautogui  # type: ignore

    old_clipboard = _get_clipboard_text()
    try:
        pyautogui.hotkey("ctrl", "l")
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "c")
        time.sleep(0.1)
        return _get_clipboard_text().strip()
    finally:
        pyautogui.press("esc")
        time.sleep(0.05)
        _set_clipboard_text(old_clipboard)


def _is_unwanted_boc_home_url(url: str) -> bool:
    normalized = url.rstrip("/")
    if any(normalized.startswith(prefix.rstrip("/")) for prefix in BOC_UNWANTED_HOME_PREFIXES):
        return True
    return "loginCAFailSolution" in normalized or "loginCAFailSolution".lower() in normalized.lower()


def _close_auto_opened_boc_home_tab(wait_seconds: float = 4.0) -> bool:
    """Close the public BOC homepage auto-opened by the U-shield driver, if it is foreground."""
    _debug_checkpoint("检查并关闭U盾自动打开的中行官网")
    try:
        import pyautogui  # type: ignore  # noqa: F401
        import win32clipboard  # type: ignore  # noqa: F401
        import win32gui  # type: ignore  # noqa: F401
    except Exception as exc:
        print(f"[提示] 缺少窗口辅助库，无法自动关闭中行官网弹窗：{type(exc).__name__}", flush=True)
        return False

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        title = _foreground_window_title()
        if (
            "中国银行" not in title
            and "Bank of China" not in title
            and "常见解决方案" not in title
            and "登录失败" not in title
            and "Login failed" not in title
        ):
            time.sleep(0.3)
            continue
        try:
            url = _read_foreground_browser_url()
        except Exception as exc:
            print(f"[提示] 读取前台浏览器 URL 失败：{type(exc).__name__}", flush=True)
            return False
        if _is_unwanted_boc_home_url(url):
            import pyautogui  # type: ignore

            pyautogui.hotkey("ctrl", "w")
            print(f"[已关闭] U盾自动打开的中行官网标签: {url}", flush=True)
            _debug_checkpoint("已关闭U盾自动打开的中行官网")
            return True
        print(f"[保留] 检测到中行窗口但不是官网首页: {url or '<empty>'}", flush=True)
        return False
    return False


def _close_boc_public_home_windows() -> int:
    """Close public BOC helper windows that can confuse the next certificate login."""
    try:
        import win32con  # type: ignore
        import win32gui  # type: ignore
    except Exception as exc:
        print(f"[清理] win32gui 不可用，无法关闭中行官网窗口：{type(exc).__name__}", flush=True)
        return 0

    targets = []

    def enum_window(hwnd, _extra) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd) or ""
        # Only close helper/failure pages, not the active iGTB enterprise banking page.
        if (
            "中国银行网站_全球门户首页" in title
            or ("Bank of China" in title and "iGTB" not in title)
            or "常见解决方案" in title
            or "登录失败" in title
            or "Login failed" in title
            or "loginCAFailSolution" in title
        ):
            targets.append((hwnd, title))

    try:
        win32gui.EnumWindows(enum_window, None)
    except Exception as exc:
        print(f"[清理] 枚举中行官网窗口失败：{type(exc).__name__}", flush=True)
        return 0

    for hwnd, title in targets:
        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            print(f"[清理] 已关闭中行官网窗口：{title}", flush=True)
        except Exception as exc:
            print(f"[清理] 关闭中行官网窗口失败 {title}: {type(exc).__name__}", flush=True)
    if targets:
        time.sleep(1.0)
    return len(targets)


def _dismiss_browser_chrome_tooltips() -> None:
    """Dismiss native browser/tooltips so desktop debug screenshots stay readable."""
    try:
        import pyautogui  # type: ignore
    except Exception:
        return
    try:
        pyautogui.press("esc")
        width, height = pyautogui.size()
        pyautogui.moveTo(max(200, width // 2), max(200, height // 2), duration=0)
        time.sleep(0.15)
    except Exception:
        pass


def _close_native_boc_dialogs() -> int:
    """Close native BOC certificate/PIN notice dialogs that may outlive the browser."""
    try:
        import win32con  # type: ignore
        import win32gui  # type: ignore
    except Exception:
        return 0

    keywords = (
        "选择证书",
        "检验用户密码",
        "校验用户密码",
        "温馨提示",
    )
    targets = []

    def enum_window(hwnd, _extra) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd) or ""
        if title and any(keyword in title for keyword in keywords):
            targets.append((hwnd, title))

    try:
        win32gui.EnumWindows(enum_window, None)
    except Exception:
        return 0
    for hwnd, title in targets:
        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            print(f"[清理] 已关闭原生弹窗：{title}", flush=True)
        except Exception as exc:
            print(f"[清理] 关闭原生弹窗失败 {title}: {type(exc).__name__}", flush=True)
    if targets:
        time.sleep(0.5)
    return len(targets)


def _close_ushield_unplug_dialogs(wait_seconds: float = 5.0) -> int:
    """Close native notices raised after powering off/removing the U-shield."""
    try:
        from pywinauto import Desktop  # type: ignore
    except Exception as exc:
        print(f"[清理] pywinauto 不可用，无法清理U盾拔出提示：{type(exc).__name__}", flush=True)
        return 0

    title_keywords = (
        "U盾",
        "U 盾",
        "USBKey",
        "UKey",
        "中国银行",
        "证书",
        "安全控件",
        "温馨提示",
        "提示",
    )
    body_keywords = (
        "拔出",
        "移除",
        "未插入",
        "不存在",
        "断开",
        "请插入",
        "已拔出",
        "USBKey",
        "U盾",
        "UKey",
    )
    closed = 0
    deadline = time.time() + max(0.0, wait_seconds)
    seen: set[int] = set()

    while time.time() < deadline:
        matched = []
        try:
            windows = Desktop(backend="uia").windows()
        except Exception as exc:
            print(f"[清理] 枚举U盾拔出提示失败：{type(exc).__name__}", flush=True)
            break

        for win in windows:
            try:
                handle = int(win.handle)
            except Exception:
                handle = 0
            if handle in seen:
                continue
            try:
                title = win.window_text() or ""
            except Exception:
                title = ""
            text = title
            try:
                parts = []
                for child in win.descendants()[:80]:
                    value = ""
                    try:
                        value = child.window_text() or ""
                    except Exception:
                        pass
                    if value:
                        parts.append(value)
                if parts:
                    text += "\n" + "\n".join(parts)
            except Exception:
                pass

            compact = text.replace(" ", "")
            title_hit = any(keyword in title for keyword in title_keywords)
            body_hit = any(keyword in compact for keyword in body_keywords)
            if title_hit and body_hit:
                matched.append((handle, win, title or "<untitled>"))

        if not matched:
            time.sleep(0.3)
            continue

        for handle, win, title in matched:
            try:
                seen.add(handle)
                button_clicked = False
                for name in ("确定", "关闭", "OK", "Close"):
                    try:
                        btn = win.child_window(title=name, control_type="Button")
                        if btn.exists(timeout=0.2):
                            btn.click_input()
                            button_clicked = True
                            break
                    except Exception:
                        continue
                if not button_clicked:
                    win.close()
                closed += 1
                print(f"[清理] 已关闭U盾拔出提示：{title}", flush=True)
            except Exception as exc:
                print(f"[清理] 关闭U盾拔出提示失败 {title}: {type(exc).__name__}", flush=True)
        time.sleep(0.5)

    return closed


def _focus_native_window_by_title(title_keywords, wait_seconds: float = 2.0) -> bool:
    try:
        import win32gui  # type: ignore
    except Exception as exc:
        print(f"[原生窗口] win32gui 不可用，无法聚焦窗口：{type(exc).__name__}", flush=True)
        return False

    keywords = tuple(title_keywords)
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        matched = []

        def enum_window(hwnd, _extra) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if title and any(keyword in title for keyword in keywords):
                matched.append((hwnd, title))

        win32gui.EnumWindows(enum_window, None)
        if matched:
            hwnd, title = matched[0]
            try:
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.1)
                if win32gui.GetForegroundWindow() == hwnd:
                    print(f"[原生窗口] 已聚焦: {title}", flush=True)
                    return True
            except Exception as exc:
                print(f"[原生窗口] 聚焦失败 {title}: {type(exc).__name__}", flush=True)
        time.sleep(0.2)
    return False


def _collect_uia_descendants_text_and_buttons(root):
    texts = []
    ok_btn = None
    cancel_btn = None
    cert_item = None
    visited = 0

    def node_text(ctrl) -> str:
        try:
            wt = ctrl.window_text() or ""
        except Exception:
            wt = ""
        try:
            name = ctrl.element_info.name or ""
        except Exception:
            name = ""
        return (wt + " " + name).strip()

    def control_type(ctrl) -> str:
        try:
            return ctrl.element_info.control_type or ""
        except Exception:
            return ""

    stack = [(root, 0)]
    while stack:
        if visited >= _UIA_TRAVERSAL_MAX_NODES:
            break
        ctrl, depth = stack.pop()
        visited += 1
        if depth > _UIA_TRAVERSAL_MAX_DEPTH:
            continue
        text = node_text(ctrl)
        if text:
            texts.append(text)
        ctype = control_type(ctrl)
        if cert_item is None and ctype != "Button":
            normalized = text.replace(" ", "")
            if any(token in normalized for token in ("CFCA", "OCA", "95566")):
                cert_item = ctrl
        if ctype == "Button":
            stripped = text.strip()
            if ok_btn is None and any(t == stripped or t in stripped for t in _UIA_CERT_OK_BUTTON_TEXTS):
                ok_btn = ctrl
            elif cancel_btn is None and any(t == stripped or t in stripped for t in _UIA_CERT_CANCEL_BUTTON_TEXTS):
                cancel_btn = ctrl
        try:
            children = ctrl.children()
        except Exception:
            children = []
        for child in children:
            stack.append((child, depth + 1))
    return " | ".join(texts), ok_btn, cancel_btn, cert_item, visited


def _click_selected_certificate_then_enter(win, ok_btn, cert_item) -> bool:
    """Focus the selected certificate row first, then press Enter like a human would."""
    _debug_checkpoint("证书操作_准备聚焦证书窗口")
    try:
        win.set_focus()
        _debug_checkpoint("证书操作_证书窗口已聚焦")
    except Exception:
        _debug_checkpoint("证书操作_证书窗口聚焦失败_继续尝试")
        pass

    if cert_item is not None:
        try:
            _debug_checkpoint("证书操作_准备点击证书项")
            cert_item.click_input()
            _debug_checkpoint("证书操作_证书项已点击")
            time.sleep(0.2)
            _debug_checkpoint("证书操作_准备发送证书项回车")
            _send_enter()
            _debug_checkpoint("证书操作_证书项回车已发送")
            print("[证书 UIA] 已点击证书项并按 Enter", flush=True)
            return True
        except Exception as exc:
            print(f"[证书 UIA] 点击证书项失败，改用坐标兜底：{type(exc).__name__}", flush=True)
            _debug_checkpoint("证书操作_点击证书项失败_准备坐标兜底")

    # Coordinate fallback for Edge's embedded cert dialog: click the area above OK.
    try:
        rect = ok_btn.rectangle()
        x = max(10, rect.left - 180)
        y = max(10, rect.top - 120)
        import pyautogui  # type: ignore

        _debug_checkpoint("证书操作_准备点击证书列表兜底区域")
        pyautogui.click(x, y)
        _debug_checkpoint("证书操作_已点击证书列表兜底区域")
        time.sleep(0.2)
        _debug_checkpoint("证书操作_准备发送兜底回车")
        _send_enter()
        _debug_checkpoint("证书操作_兜底回车已发送")
        print("[证书 UIA] 已点击证书列表区域并按 Enter", flush=True)
        return True
    except Exception as exc:
        print(f"[证书 UIA] 证书列表坐标兜底失败：{type(exc).__name__}", flush=True)
        _debug_checkpoint("证书操作_证书列表兜底失败")
    return False


def _certificate_dialog_still_present(win) -> bool:
    try:
        blob, ok_btn, cancel_btn, _cert_item, _visited = _collect_uia_descendants_text_and_buttons(win)
    except Exception:
        return False
    has_cert_label = any(tok in blob for tok in _UIA_CERT_REQUIRED_TOKENS)
    has_boc_host = any(tok in blob for tok in _UIA_CERT_HOST_TOKENS)
    has_buttons = ok_btn is not None and cancel_btn is not None
    return bool(has_cert_label and has_boc_host and has_buttons)


def _wait_certificate_dialog_closed(win, timeout_seconds: float = 6.0) -> bool:
    deadline = time.time() + max(0.0, timeout_seconds)
    while time.time() < deadline:
        if not _certificate_dialog_still_present(win):
            return True
        if _ushield_pin_window_present():
            _debug_checkpoint("证书操作_检测到U盾PIN窗口_视为证书已接受")
            return True
        time.sleep(0.2)
    return False


def _wait_ushield_pin_window_present(wait_seconds: float = 3.0) -> bool:
    deadline = time.time() + max(0.0, wait_seconds)
    while time.time() < deadline:
        if _ushield_pin_window_present():
            _debug_checkpoint("证书操作_检测到U盾PIN窗口_视为证书已接受")
            return True
        time.sleep(0.2)
    return False


def _ushield_pin_window_present() -> bool:
    try:
        from pywinauto import Desktop  # type: ignore
    except Exception:
        return False
    for backend in ("uia", "win32"):
        try:
            windows = list(Desktop(backend=backend).windows())
        except Exception:
            continue
        for win in windows:
            if _is_probable_ushield_pin_window(win):
                return True
    return False


def _is_ignored_ushield_candidate_title(title: str) -> bool:
    title = title or ""
    ignored_tokens = (
        "Codex",
        "Microsoft Edge",
        "Google Chrome",
        "Chrome",
        "中行网银USBKey数字安全证书管理工具",
        "USBKey状态",
        "修改USBKey名称",
        "关于中行网银USBKey",
    )
    return any(token in title for token in ignored_tokens)


def _is_ushield_pin_window_title(title: str) -> bool:
    title = title or ""
    if _is_ignored_ushield_candidate_title(title):
        return False
    if "校验用户密码" in title:
        return True
    has_key = any(token in title for token in ("USBKey", "U盾", "U 盾", "UKey"))
    has_password = any(token in title for token in ("密码", "PIN", "pin"))
    return bool(has_key and has_password)


def _window_rect_size(win) -> tuple[int, int]:
    try:
        rect = win.rectangle()
        return max(0, int(rect.right - rect.left)), max(0, int(rect.bottom - rect.top))
    except Exception:
        return 0, 0


def _window_text_blob_limited(win, max_children: int = 120) -> str:
    parts = []
    try:
        title = win.window_text() or ""
        if title:
            parts.append(title)
    except Exception:
        pass
    try:
        for child in list(win.descendants())[:max_children]:
            text = ""
            try:
                text = child.window_text() or ""
            except Exception:
                pass
            if not text:
                try:
                    text = child.element_info.name or ""
                except Exception:
                    pass
            if text:
                parts.append(text)
    except Exception:
        pass
    return " | ".join(parts)


def _has_edit_descendant(win) -> bool:
    try:
        for child in win.descendants():
            try:
                if getattr(child.element_info, "control_type", "") == "Edit":
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _is_probable_ushield_pin_window(win) -> bool:
    try:
        title = win.window_text() or ""
    except Exception:
        title = ""
    if _is_ignored_ushield_candidate_title(title):
        return False
    if _is_ushield_pin_window_title(title):
        return True

    width, height = _window_rect_size(win)
    # The real BOC USBKey PIN dialog is a small native modal. This geometry
    # guard keeps browser/Codex/terminal windows with copied log text out.
    if not (220 <= width <= 560 and 90 <= height <= 280):
        return False

    blob = _window_text_blob_limited(win).replace(" ", "")
    if not blob:
        return False
    if any(token in blob for token in ("中行网银USBKey数字安全证书管理工具", "USBKey状态", "修改USBKey名称")):
        return False
    has_pin_prompt = any(token in blob for token in ("输入USBKey密码", "USBKey密码", "校验用户密码"))
    has_dialog_controls = "软键盘" in blob or ("确定" in blob and "取消" in blob)
    if not (has_pin_prompt and has_dialog_controls):
        return False
    # Prefer an actual edit control, but do not require it: some native dialogs
    # expose the password input only through win32 children.
    return True


def _click_center_by_rect(rect, label: str) -> bool:
    try:
        import pyautogui  # type: ignore
    except Exception as exc:
        print(f"[证书 UIA] pyautogui 不可用，无法坐标点击 {label}：{type(exc).__name__}", flush=True)
        _debug_checkpoint(f"证书操作_坐标点击{label}失败_pyautogui不可用")
        return False
    try:
        x = int((rect.left + rect.right) / 2)
        y = int((rect.top + rect.bottom) / 2)
        _debug_checkpoint(f"证书操作_准备坐标点击{label}_{x}_{y}")
        pyautogui.click(x, y)
        _debug_checkpoint(f"证书操作_已坐标点击{label}_{x}_{y}")
        print(f"[证书 UIA] 已坐标点击 {label}: {x},{y}", flush=True)
        return True
    except Exception as exc:
        print(f"[证书 UIA] 坐标点击 {label} 失败：{type(exc).__name__}", flush=True)
        _debug_checkpoint(f"证书操作_坐标点击{label}失败")
        return False


def _click_certificate_ok_button_until_closed(win, ok_btn, cert_item=None) -> bool:
    for method in ("invoke", "click_input"):
        try:
            _debug_checkpoint(f"证书操作_准备{method}点击确定")
            getattr(ok_btn, method)()
            _debug_checkpoint(f"证书操作_{method}点击确定已执行")
            print(f"[证书 UIA] 已通过 {method} 点击确定", flush=True)
            if _wait_certificate_dialog_closed(win):
                _debug_checkpoint(f"证书操作_{method}点击确定后弹窗已关闭")
                return True
            _debug_checkpoint(f"证书操作_{method}点击确定后弹窗仍在")
        except Exception as exc:
            print(f"[证书 UIA] {method} 点击确定失败：{type(exc).__name__}", flush=True)
            _debug_checkpoint(f"证书操作_{method}点击确定失败")

    for action_name, action in (
        ("按钮聚焦回车", lambda: (ok_btn.set_focus(), _send_enter())),
        ("窗口聚焦回车", lambda: (win.set_focus(), _send_enter())),
    ):
        try:
            _debug_checkpoint(f"证书操作_准备{action_name}")
            action()
            _debug_checkpoint(f"证书操作_{action_name}已执行")
            print(f"[证书 UIA] 已尝试 {action_name}", flush=True)
            if _wait_certificate_dialog_closed(win):
                _debug_checkpoint(f"证书操作_{action_name}后弹窗已关闭")
                return True
            _debug_checkpoint(f"证书操作_{action_name}后弹窗仍在")
        except Exception as exc:
            print(f"[证书 UIA] {action_name}失败：{type(exc).__name__}", flush=True)
            _debug_checkpoint(f"证书操作_{action_name}失败")

    try:
        _debug_checkpoint("证书操作_准备重新聚焦证书窗口")
        win.set_focus()
        _debug_checkpoint("证书操作_证书窗口重新聚焦完成")
    except Exception:
        _debug_checkpoint("证书操作_证书窗口重新聚焦失败")
        pass
    if cert_item is not None:
        try:
            if _click_center_by_rect(cert_item.rectangle(), "证书行"):
                time.sleep(0.2)
        except Exception as exc:
            print(f"[证书 UIA] 获取证书行坐标失败：{type(exc).__name__}", flush=True)
    try:
        if _click_center_by_rect(ok_btn.rectangle(), "确定按钮") and _wait_certificate_dialog_closed(win):
            return True
        if _wait_ushield_pin_window_present(wait_seconds=3.0):
            return True
    except Exception as exc:
        print(f"[证书 UIA] 获取确定按钮坐标失败：{type(exc).__name__}", flush=True)

    try:
        rect = win.rectangle()
        class _Rect:
            left = rect.right - 142
            right = rect.right - 50
            top = rect.bottom - 58
            bottom = rect.bottom - 18
        if _click_center_by_rect(_Rect, "确定按钮兜底区域") and _wait_certificate_dialog_closed(win):
            _debug_checkpoint("证书操作_确定按钮兜底区域点击后弹窗已关闭")
            return True
        if _wait_ushield_pin_window_present(wait_seconds=3.0):
            return True
        _debug_checkpoint("证书操作_确定按钮兜底区域点击后弹窗仍在")
    except Exception as exc:
        print(f"[证书 UIA] 证书窗口坐标兜底失败：{type(exc).__name__}", flush=True)
        _debug_checkpoint("证书操作_证书窗口坐标兜底失败")
    return False


def _confirm_chrome_certificate_dialog_via_uia(wait_seconds: float = 5.0) -> bool:
    _debug_checkpoint("开始UIA识别证书选择框")
    try:
        from pywinauto import Desktop  # type: ignore
    except Exception as exc:
        print(f"[证书 UIA] pywinauto 不可用：{type(exc).__name__}", flush=True)
        return False

    deadline = time.time() + max(0.0, wait_seconds)
    diagnostics = []
    scan_checkpoint_done = False
    while time.time() < deadline:
        try:
            top_windows = list(Desktop(backend="uia").windows())
        except Exception as exc:
            print(f"[证书 UIA] 枚举窗口失败：{type(exc).__name__}", flush=True)
            _debug_checkpoint("证书操作_UIA枚举窗口失败")
            return False
        if not scan_checkpoint_done:
            _debug_checkpoint(f"证书操作_UIA已枚举窗口_{len(top_windows)}个")
            scan_checkpoint_done = True

        for win in top_windows:
            try:
                title = win.window_text() or ""
            except Exception:
                title = ""
            title_cert_candidate = any(
                k in title
                for k in (
                    *_UIA_CHROME_TITLE_KEYWORDS,
                    "Windows 安全",
                    "选择证书",
                    "身份验证",
                    "Certificate",
                    "certificate",
                )
            )
            if title and not title_cert_candidate:
                continue
            try:
                blob, ok_btn, cancel_btn, cert_item, visited = _collect_uia_descendants_text_and_buttons(win)
            except Exception:
                continue
            has_cert_label = any(tok in blob for tok in _UIA_CERT_REQUIRED_TOKENS)
            has_boc_host = any(tok in blob for tok in _UIA_CERT_HOST_TOKENS)
            has_buttons = ok_btn is not None and cancel_btn is not None
            has_cert_material = cert_item is not None or any(tok in blob for tok in ("CFCA", "OCA", "95566"))
            is_edge_cert_dialog = has_cert_label and has_boc_host and has_buttons
            is_windows_cert_dialog = (
                ("Windows 安全" in title or "选择证书" in blob or "身份验证" in blob)
                and has_buttons
                and has_cert_material
            )
            if (has_cert_label or has_boc_host or has_buttons or "证书" in title) and len(diagnostics) < 12:
                diagnostics.append(
                    f"title={title or '<无标题>'}, cert={has_cert_label}, "
                    f"host={has_boc_host}, buttons={has_buttons}, cert_item={cert_item is not None}, "
                    f"windows_cert={is_windows_cert_dialog}, nodes={visited}"
                )
            if not (is_edge_cert_dialog or is_windows_cert_dialog):
                continue
            print(f"[证书 UIA] 命中中行证书选择框，扫描节点 {visited}，点击证书项后回车", flush=True)
            _debug_checkpoint(
                f"证书操作_UIA命中证书选择框_edge={int(is_edge_cert_dialog)}_windows={int(is_windows_cert_dialog)}"
            )
            if _click_selected_certificate_then_enter(win, ok_btn, cert_item):
                _debug_checkpoint("证书操作_已完成证书项点击回车")
                if _wait_certificate_dialog_closed(win):
                    print("[证书 UIA] 证书选择框已关闭", flush=True)
                    _debug_checkpoint("证书操作_证书项回车后弹窗已关闭")
                    return True
                print("[证书 UIA] 证书选择框仍在，改为显式点击确定", flush=True)
                _debug_checkpoint("证书操作_证书项回车后弹窗仍在_准备点击确定")
            if _click_certificate_ok_button_until_closed(win, ok_btn, cert_item):
                _debug_checkpoint("证书操作_证书选择框已确认关闭")
                return True
            if _wait_ushield_pin_window_present(wait_seconds=3.0):
                return True
            if _certificate_dialog_still_present(win):
                print("[证书 UIA] 点击后证书选择框仍未关闭", flush=True)
                _debug_checkpoint("证书操作_所有点击后证书框仍未关闭")
                return False
            _debug_checkpoint("证书操作_证书框不再存在")
            return True
        time.sleep(0.3)
    print(f"[证书 UIA] {wait_seconds:.1f}s 内未检测到中行证书选择框", flush=True)
    if diagnostics:
        print("[证书 UIA] 最近候选窗口：" + " ; ".join(diagnostics[-6:]), flush=True)
    _debug_checkpoint("证书操作_等待超时未检测到证书框")
    return False


def _confirm_certificate_selection(wait_seconds: float = 15.0) -> bool:
    """Confirm the native/embedded certificate chooser only after it is identified."""
    _debug_checkpoint("证书选择确认开始")
    time.sleep(1.2)
    if _confirm_chrome_certificate_dialog_via_uia(wait_seconds=wait_seconds):
        return True
    if _focus_native_window_by_title(BOC_CERT_WINDOW_TITLES, wait_seconds=1.5):
        _send_enter()
        print("[已确认] 原生证书选择窗口", flush=True)
        _debug_checkpoint("已通过原生窗口标题确认")
        return True
    print("[警告] 未能自动确认中行证书选择框，请人工点击「确定」", flush=True)
    _debug_checkpoint("证书选择确认失败")
    return False


def _focus_ushield_pin_input(wait_seconds: float = 3.0) -> bool:
    """Focus the native U-shield PIN edit box before sending keys."""
    deadline = time.time() + max(0.0, wait_seconds)

    try:
        from pywinauto import Desktop  # type: ignore
    except Exception:
        Desktop = None

    while time.time() < deadline:
        if Desktop is not None:
            try:
                for win in Desktop(backend="uia").windows():
                    try:
                        title = win.window_text() or ""
                    except Exception:
                        title = ""
                    if not _is_probable_ushield_pin_window(win):
                        continue
                    try:
                        win.set_focus()
                    except Exception:
                        pass
                    try:
                        for child in win.descendants():
                            ctype = getattr(child.element_info, "control_type", "")
                            if ctype == "Edit":
                                child.click_input()
                                print(f"[U盾] 已聚焦 PIN 输入框：{title or '<无标题>'}", flush=True)
                                return True
                    except Exception:
                        pass
                    print(f"[U盾] 已聚焦 PIN 窗口：{title or '<无标题>'}", flush=True)
                    return True
            except Exception as exc:
                print(f"[U盾] UIA 聚焦 PIN 窗口失败：{type(exc).__name__}", flush=True)

        if _focus_native_window_by_title(("校验用户密码", "USBKey密码"), wait_seconds=0.5):
            print("[U盾] 已通过窗口标题聚焦 PIN 窗口", flush=True)
            return True
        time.sleep(0.2)

    print("[U盾] 未找到可聚焦的 PIN 窗口，将按当前焦点尝试输入", flush=True)
    return False


def _find_ushield_pin_win32_window():
    try:
        from pywinauto import Desktop  # type: ignore
    except Exception as exc:
        print(f"[U盾软键盘] pywinauto 不可用：{type(exc).__name__}", flush=True)
        return None

    try:
        for win in Desktop(backend="win32").windows():
            if _is_probable_ushield_pin_window(win):
                return win
    except Exception as exc:
        print(f"[U盾软键盘] 枚举 PIN 窗口失败：{type(exc).__name__}", flush=True)
    return None


def _soft_keyboard_buttons(win) -> dict[str, object]:
    buttons = {}
    try:
        children = list(win.children())
    except Exception:
        children = []
    for child in children:
        try:
            if child.friendly_class_name() != "Button":
                continue
            text = (child.window_text() or "").strip()
            if text:
                buttons[text] = child
        except Exception:
            continue
    return buttons


def _submit_ushield_pin_with_soft_keyboard(pin: str) -> bool:
    """Use the bank PIN dialog's own randomized soft keyboard when SendInput is blocked."""
    if not pin:
        return False
    if not all(ch.isdigit() or ("a" <= ch <= "z") for ch in pin):
        print("[U盾软键盘] 当前仅支持小写字母和数字 PIN", flush=True)
        return False

    win = _find_ushield_pin_win32_window()
    if win is None:
        print("[U盾软键盘] 未找到 PIN 窗口", flush=True)
        return False

    try:
        win.set_focus()
    except Exception:
        pass

    buttons = _soft_keyboard_buttons(win)
    if not any(ch in buttons for ch in pin):
        soft_button = buttons.get("软键盘")
        if soft_button is None:
            print("[U盾软键盘] 未找到“软键盘”按钮", flush=True)
            return False
        try:
            soft_button.click_input()
            print("[U盾软键盘] 已打开软键盘", flush=True)
        except Exception as exc:
            print(f"[U盾软键盘] 点击“软键盘”失败：{type(exc).__name__}", flush=True)
            return False
        deadline = time.time() + 2.0
        while time.time() < deadline:
            buttons = _soft_keyboard_buttons(win)
            if all(ch in buttons for ch in set(pin)):
                break
            time.sleep(0.1)

    buttons = _soft_keyboard_buttons(win)
    backspace = buttons.get("<- back")
    if backspace is not None:
        for _ in range(len(pin) + 2):
            try:
                backspace.click_input()
                time.sleep(0.03)
            except Exception:
                break

    for ch in pin:
        buttons = _soft_keyboard_buttons(win)
        key = buttons.get(ch)
        if key is None:
            print(f"[U盾软键盘] 未找到软键盘按键：{ch}", flush=True)
            return False
        try:
            key.click_input()
            time.sleep(0.08)
        except Exception as exc:
            print(f"[U盾软键盘] 点击按键失败 {ch}: {type(exc).__name__}", flush=True)
            return False

    buttons = _soft_keyboard_buttons(win)
    ok_button = buttons.get("确定")
    if ok_button is None:
        print("[U盾软键盘] 未找到“确定”按钮", flush=True)
        return False
    try:
        ok_button.click_input()
        print("[U盾软键盘] 已通过软键盘输入 PIN 并点击确定", flush=True)
        return True
    except Exception as exc:
        print(f"[U盾软键盘] 点击“确定”失败：{type(exc).__name__}", flush=True)
        return False
