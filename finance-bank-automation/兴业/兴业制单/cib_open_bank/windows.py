"""Window discovery and UKey password dialog handling."""
import os
import sys
import time

import pyautogui
import pygetwindow as gw
import win32con
import win32api
import win32gui
import win32process

from .runtime import screenshot


def _printable(value):
    return str(value or "").encode("gbk", "replace").decode("gbk")


_BANK_WINDOW_KEYWORDS = (
    "兴业银行企业网银",
    "兴业银行",
    "企业网银",
    "企业银行",
    "U-BANK",
    "联机登录",
)
_TOOL_WINDOW_MARKERS = (
    "Codex",
    "Windows Terminal",
    "Windows PowerShell",
    "PowerShell",
    "Command Prompt",
    "cmd.exe",
    "python.exe",
    "open_bank.py",
    "兴业单笔转账",
    os.path.basename(os.getcwd()),
)
_EXCLUDED_BANK_WINDOW_KEYWORDS = (
    "兴业管家网盾助手",
    "网盾助手",
    "网盾管理工具",
    "神州融安",
    "验证网盾密码",
)
_CONFIRM_WINDOW_KEYWORDS = ("确认", "退出", "提示")


def _window_hwnd(window):
    return getattr(window, "_hWnd", None) or getattr(window, "_hwnd", None)


def _window_pid(window):
    hwnd = _window_hwnd(window)
    if not hwnd or not win32gui.IsWindow(hwnd):
        return None
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid
    except Exception:
        return None


def _is_current_or_tool_window(window, title):
    hwnd = _window_hwnd(window)
    if hwnd and win32gui.IsWindow(hwnd):
        try:
            if hwnd == win32gui.GetConsoleWindow():
                return True
        except Exception:
            pass
        if _window_pid(window) == win32api.GetCurrentProcessId():
            return True
    return any(marker and marker in title for marker in _TOOL_WINDOW_MARKERS)


def _is_bank_window_title(title):
    return any(keyword in title for keyword in _BANK_WINDOW_KEYWORDS)


def _post_close(window, safe_title, label):
    try:
        hwnd = _window_hwnd(window)
        if hwnd and win32gui.IsWindow(hwnd):
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        else:
            window.close()
    except Exception as exc:
        print(f"  {label}失败: {safe_title!r}: {exc}")


def find_window():
    candidates = []
    for w in gw.getAllWindows():
        if not w.title:
            continue
        t = str(w.title)
        # 排除网盾助手等后台小窗口（坐标异常或尺寸过小说明不是主窗口）
        if any(keyword in t for keyword in _EXCLUDED_BANK_WINDOW_KEYWORDS):
            continue
        # 过滤掉隐藏/最小化到托盘的窗口（left=-32000 是 Windows 隐藏窗口特征）
        if w.left < -1000 or w.width < 200 or w.height < 100:
            continue
        if "兴业银行企业网银" in t:
            return w
        if "企业网银" in t or "U-BANK" in t or "联机登录" in t:
            candidates.append(w)
    if candidates:
        return candidates[0]
    return None


def dismiss_ukey_notice(wait_seconds=3):
    """Dismiss small UKey management notices such as '网盾已经移除'."""
    end = time.time() + wait_seconds
    while time.time() < end:
        for w in gw.getAllWindows():
            title = str(w.title or "")
            if not title:
                continue
            if "网盾管理工具" not in title and "神州融安" not in title:
                continue
            if w.left < -1000 or w.width > 700 or w.height > 400:
                continue
            safe_title = _printable(title)
            print(f"  关闭网盾提示窗口: {safe_title!r}")
            try:
                w.activate()
            except Exception:
                pass
            time.sleep(0.2)
            pyautogui.press("enter")
            time.sleep(0.8)
            return True
        time.sleep(0.3)
    return False


def close_bank_windows(wait_seconds=3):
    """Close U-BANK/bank windows only; do not close this script's terminal."""
    targets = []
    target_pids = set()
    for w in gw.getAllWindows():
        title = str(w.title or "")
        if not title or not _is_bank_window_title(title):
            continue
        if "兴业管家网盾助手" in title or "网盾助手" in title:
            continue
        if w.left < -1000 or w.width < 100 or w.height < 80:
            continue
        if _is_current_or_tool_window(w, title):
            print(f"  跳过运行脚本/工具窗口: {_printable(title)!r}")
            continue
        pid = _window_pid(w)
        if pid:
            target_pids.add(pid)
        targets.append(w)

    if not targets:
        print("  未发现需要关闭的 U-BANK/网银窗口")
        return False

    for w in targets:
        title = str(w.title or "")
        safe_title = _printable(title)
        print(f"  关闭窗口: {safe_title!r}")
        _post_close(w, safe_title, "关闭窗口")
    time.sleep(wait_seconds)

    for w in gw.getAllWindows():
        title = str(w.title or "")
        if not title or not any(kw in title for kw in _CONFIRM_WINDOW_KEYWORDS):
            continue
        if _is_current_or_tool_window(w, title):
            continue
        pid = _window_pid(w)
        if target_pids and pid not in target_pids and not _is_bank_window_title(title):
            continue
        safe_title = _printable(title)
        print(f"  关闭确认弹窗: {safe_title!r}")
        _post_close(w, safe_title, "关闭确认弹窗")
    time.sleep(1)
    return True


def find_ukey_dialog():
    """查找 '验证网盾密码' 对话框窗口（含子窗口）。返回带 left/top/width/height/title/activate 的对象。"""
    # 先找顶层窗口
    for w in gw.getAllWindows():
        if w.title and "验证网盾密码" in str(w.title):
            return w

    # 回退：用 win32 枚举所有顶层和子窗口
    found = []

    def _enum_child(hwnd, _):
        title = win32gui.GetWindowText(hwnd)
        if title and "验证网盾密码" in title and win32gui.IsWindowVisible(hwnd):
            found.append(hwnd)

    def _enum_top(hwnd, _):
        title = win32gui.GetWindowText(hwnd)
        if title and "验证网盾密码" in title and win32gui.IsWindowVisible(hwnd):
            found.append(hwnd)
        win32gui.EnumChildWindows(hwnd, _enum_child, None)

    win32gui.EnumWindows(_enum_top, None)

    if not found:
        password_box = locate_ukey_password_box()
        if not password_box:
            return None

        x, y = password_box

        class _ImageDetectedW:
            pass

        w = _ImageDetectedW()
        w.title = "验证网盾密码(image)"
        w.left = max(0, x - 220)
        w.top = max(0, y - 90)
        w.width = 440
        w.height = 260
        w._hwnd = None
        w.activate = lambda: None
        return w

    hwnd = found[0]
    l, t, r, b = win32gui.GetWindowRect(hwnd)

    class _W:
        pass
    w = _W()
    w.title = win32gui.GetWindowText(hwnd)
    w.left, w.top = l, t
    w.width, w.height = r - l, b - t
    w._hwnd = hwnd
    w.activate = lambda: win32gui.SetForegroundWindow(hwnd)
    return w


def locate_ukey_password_box():
    """Locate the UKey password input by looking at the current screenshot."""
    try:
        img = pyautogui.screenshot().convert("RGB")
        width, height = img.size
        px = img.load()

        def is_title_blue(rgb):
            r, g, b = rgb
            return r < 80 and 95 <= g <= 180 and 130 <= b <= 230

        candidates = []
        for y in range(80, height - 120):
            start = None
            for x in range(0, width):
                if is_title_blue(px[x, y]):
                    if start is None:
                        start = x
                elif start is not None:
                    if x - start >= 300:
                        candidates.append((y, start, x))
                    start = None
            if start is not None and width - start >= 300:
                candidates.append((y, start, width))

        if not candidates:
            return None

        y, left, right = sorted(candidates, key=lambda item: item[2] - item[1], reverse=True)[0]
        same_bar = [item for item in candidates if abs(item[1] - left) < 8 and abs(item[2] - right) < 8]
        top = min(item[0] for item in same_bar)
        bottom = max(item[0] for item in same_bar)

        runs = []
        search_left = left + 160
        search_right = right - 15
        search_top = bottom + 15
        search_bottom = min(bottom + 95, height)
        for yy in range(search_top, search_bottom):
            start = None
            for xx in range(search_left, search_right):
                r, g, b = px[xx, yy]
                is_white = r >= 245 and g >= 245 and b >= 245
                if is_white:
                    if start is None:
                        start = xx
                elif start is not None:
                    if xx - start >= 150:
                        runs.append((yy, start, xx))
                    start = None
            if start is not None and search_right - start >= 150:
                runs.append((yy, start, search_right))

        if not runs:
            return None

        yy, input_left, input_right = sorted(runs, key=lambda item: item[2] - item[1], reverse=True)[0]
        same_input = [item for item in runs if abs(item[1] - input_left) < 8 and abs(item[2] - input_right) < 8]
        input_top = min(item[0] for item in same_input)
        input_bottom = max(item[0] for item in same_input)
        return ((input_left + input_right) // 2, (input_top + input_bottom) // 2)
    except Exception:
        return None


def focus_ukey_password_field(ukey_win):
    """Bring the UKey dialog forward and click the password edit box."""
    try:
        ukey_win.activate()
    except Exception:
        pass
    time.sleep(0.3)
    try:
        located = locate_ukey_password_box()
        if located:
            x, y = located
            print(f"  聚焦网盾密码框 image=({x},{y})")
            pyautogui.click(x, y)
            time.sleep(0.15)
            pyautogui.click(x, y)
            time.sleep(0.2)
            return

        raw_x = ukey_win.left + int(ukey_win.width * 0.68)
        raw_y = ukey_win.top + int(ukey_win.height * 0.22)
        screen_w, screen_h = pyautogui.size()
        metric_w = win32api.GetSystemMetrics(0)
        metric_h = win32api.GetSystemMetrics(1)
        scale_x = screen_w / metric_w if metric_w else 1
        scale_y = screen_h / metric_h if metric_h else 1
        x = int(raw_x * scale_x)
        y = int(raw_y * scale_y)
        print(f"  聚焦网盾密码框 raw=({raw_x},{raw_y}) click=({x},{y}) scale=({scale_x:.3f},{scale_y:.3f})")
        pyautogui.click(x, y)
        time.sleep(0.15)
        pyautogui.click(x, y)
        time.sleep(0.2)
    except Exception:
        pass


def handle_ukey_dialog(ukey_pwd, wait_seconds=10, tag=""):
    """检测并处理网盾密码对话框。若未出现返回 False。"""
    print(f"  检测网盾密码对话框（最多等待{wait_seconds}秒）...")
    ukey_win = None
    for i in range(wait_seconds):
        ukey_win = find_ukey_dialog()
        if ukey_win:
            break
        time.sleep(1)

    if not ukey_win:
        print("  未出现网盾密码对话框，跳过")
        return False

    print(f"  检测到对话框: '{ukey_win.title}' @ ({ukey_win.left}, {ukey_win.top}) {ukey_win.width}x{ukey_win.height}")
    time.sleep(0.8)
    focus_ukey_password_field(ukey_win)

    # 直接输入密码（对话框打开时输入框已聚焦），不点击输入框
    pyautogui.write(ukey_pwd, interval=0.08)
    print(f"  已输入网盾密码 (长度:{len(ukey_pwd)})")
    time.sleep(0.4)
    screenshot(f"网盾_输密码{tag}")

    # 按回车确认
    pyautogui.press("enter")
    time.sleep(1.5)
    screenshot(f"网盾_确认{tag}")
    if find_ukey_dialog():
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("backspace")
        print("  错误: 网盾密码框仍未关闭，停止后续流程")
        sys.exit(1)
    return True
