# -*- coding: utf-8 -*-
"""Crash detection and Firmbank process cleanup for U-BANK automation."""
import ctypes
import os
import subprocess
import threading
import time
from ctypes import wintypes

import win32con
import win32gui

import zhidan_paths  # Ensures the project root is importable before ubank_common.
from ubank_common import click_at, press_keys

UBANK_CRASH_EXIT_CODE = 8
CRASH_DIAG_MODE = os.environ.get("UBANK_CRASH_DIAG") == "1"
_EVENT_SYSTEM_DIALOGSTART = 0x0010
_EVENT_OBJECT_CREATE = 0x8000
_EVENT_OBJECT_SHOW = 0x8002
_EVENT_OBJECT_NAMECHANGE = 0x800C
_WINEVENT_OUTOFCONTEXT = 0x0000
_WINEVENT_SKIPOWNPROCESS = 0x0002
_WINEVENT_FLAGS = _WINEVENT_OUTOFCONTEXT | _WINEVENT_SKIPOWNPROCESS
_PM_REMOVE = 0x0001
_WinEventProcType = ctypes.WINFUNCTYPE(
    None,
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.HWND,
    wintypes.LONG,
    wintypes.LONG,
    wintypes.DWORD,
    wintypes.DWORD,
)

_GLOBAL_WATCHER = None

class UBankCrashDetected(RuntimeError):
    """Raised when the CMB U-BANK native client shows an application error."""

def _is_ubank_crash_title(title):
    title = title or ""
    is_app_error = "应用程序错误" in title or "Application Error" in title
    is_ubank = (
        "Firmbank.exe" in title
        or "招商银行企业银行" in title
        or "企业银行" in title
        or "U-BANK" in title
    )
    return is_app_error and is_ubank

def _find_ubank_crash_dialog():
    """Return the U-BANK crash dialog hwnd/title if Firmbank.exe has crashed."""
    matches = []

    def _enum_handler(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd) or ""
        except Exception:
            return

        if _is_ubank_crash_title(title):
            matches.append((hwnd, title))

    win32gui.EnumWindows(_enum_handler, None)
    return matches[0] if matches else (None, "")

def _dismiss_crash_dialog(hwnd):
    """Dismiss the specific crash dialog so the next retry is not blocked."""
    if not hwnd:
        return

    buttons = []

    def _enum_child(child_hwnd, _):
        try:
            text = (win32gui.GetWindowText(child_hwnd) or "").strip()
            if text in {"确定", "OK"}:
                buttons.append(child_hwnd)
        except Exception:
            pass

    try:
        win32gui.EnumChildWindows(hwnd, _enum_child, None)
        if buttons:
            left, top, right, bottom = win32gui.GetWindowRect(buttons[0])
            click_at((left + right) // 2, (top + bottom) // 2)
        else:
            win32gui.SetForegroundWindow(hwnd)
            press_keys((0x0D, 0), (0x0D, 2))
        time.sleep(0.3)
        if win32gui.IsWindow(hwnd):
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            time.sleep(0.2)
    except Exception as e:
        print(f"关闭招行崩溃弹窗失败: {e}")

def _clear_stale_ubank_crash_dialogs():
    """启动前清理上一次运行遗留的应用错误弹窗。"""
    for _ in range(3):
        hwnd, title = _find_ubank_crash_dialog()
        if not hwnd:
            return
        print(f"清理启动前残留的招行崩溃弹窗: {title}")
        _dismiss_crash_dialog(hwnd)
        time.sleep(0.5)


def _dismiss_ubank_script_error_dialogs():
    """关闭 U-BANK 内嵌网页偶发的“脚本错误”弹窗；无弹窗时不做任何事。"""
    matches = []

    def _enum_handler(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = (win32gui.GetWindowText(hwnd) or "").strip()
        except Exception:
            return

        if title != "脚本错误":
            return

        child_texts = []

        def _enum_child(child_hwnd, _child_arg):
            try:
                text = (win32gui.GetWindowText(child_hwnd) or "").strip()
                if text:
                    child_texts.append(text)
            except Exception:
                pass

        try:
            win32gui.EnumChildWindows(hwnd, _enum_child, None)
        except Exception:
            pass

        joined = "\n".join(child_texts)
        if "receiveThirdPageToMessageCenter" in joined or "Uncaught TypeError" in joined:
            matches.append(hwnd)

    try:
        win32gui.EnumWindows(_enum_handler, None)
    except Exception:
        return False

    dismissed = False
    for hwnd in matches:
        buttons = []

        def _enum_button(child_hwnd, _):
            try:
                text = (win32gui.GetWindowText(child_hwnd) or "").strip()
                if text in {"确定", "OK"}:
                    buttons.append(child_hwnd)
            except Exception:
                pass

        try:
            win32gui.EnumChildWindows(hwnd, _enum_button, None)
            if buttons:
                left, top, right, bottom = win32gui.GetWindowRect(buttons[0])
                click_at((left + right) // 2, (top + bottom) // 2)
            else:
                win32gui.SetForegroundWindow(hwnd)
                press_keys((0x0D, 0), (0x0D, 2))
            dismissed = True
            print("已关闭 U-BANK 脚本错误弹窗")
            time.sleep(0.3)
        except Exception as e:
            print(f"关闭 U-BANK 脚本错误弹窗失败: {e}")

    return dismissed


def _check_ubank_crash(stage="", dismiss=True):
    """检查 U-BANK 是否崩溃。

    全局守护线程 _GLOBAL_WATCHER 已在 20ms 轮询窗口；这里主要读它的 fired 标记。
    极少数场景（守护线程未启动 / 刚启动还没扫到）下，再做一次本地扫描兜底。
    """
    # 1) 守护线程已经检测并灭过弹窗 → 主流程直接 raise 让脚本以 rc=8 终止
    if _GLOBAL_WATCHER is not None and _GLOBAL_WATCHER.fired:
        title = _GLOBAL_WATCHER.fired_title
        where = f"（{stage}）" if stage else ""
        print(f"守护线程已捕获并强杀招行崩溃{where}: {title}")
        raise UBankCrashDetected(title)

    # 2) 兜底：自己再扫一次（守护线程未启动时这是唯一防线）
    hwnd, title = _find_ubank_crash_dialog()
    if not hwnd:
        return

    where = f"（{stage}）" if stage else ""
    print(f"检测到招行客户端崩溃{where}: {title}")
    if dismiss and not CRASH_DIAG_MODE:
        _kill_all_firmbank()
    elif CRASH_DIAG_MODE:
        print("诊断模式已开启：保留崩溃现场，等待 Windows 写入 LocalDump")
    raise UBankCrashDetected(title)

def _sleep_and_check(seconds, stage=""):
    end_ts = time.time() + seconds
    while time.time() < end_ts:
        _check_ubank_crash(stage)
        time.sleep(min(0.5, max(0, end_ts - time.time())))
    _check_ubank_crash(stage)

def _kill_all_firmbank():
    """强杀所有 Firmbank.exe 进程，连同子进程一起。返回是否成功。

    注意：不解码 stdout/stderr —— Windows taskkill 输出是 GBK，但脚本运行在
    `python -X utf8` 下默认按 UTF-8 解码会触发 UnicodeDecodeError。这里只关心
    退出码，bytes 直接丢弃。
    """
    try:
        proc = subprocess.run(
            ["taskkill", "/F", "/T", "/IM", "Firmbank.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return proc.returncode == 0
    except Exception as e:
        print(f"taskkill Firmbank 异常: {e}")
        return False

def _is_firmbank_process_alive():
    """通过 tasklist 判断 Firmbank.exe 进程是否仍存在。"""
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Firmbank.exe", "/FO", "CSV"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return b"Firmbank.exe" in (proc.stdout or b"")
    except Exception:
        return False


def _is_firmbank_alive():
    """判断 Firmbank 是否还活着：优先查窗口，再用进程兜底。"""
    alive = [False]

    def _enum(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd) or ""
        except Exception:
            return
        if "招商银行企业银行" in title or "Firmbank" in title:
            alive[0] = True

    try:
        win32gui.EnumWindows(_enum, None)
    except Exception:
        pass
    return alive[0] or _is_firmbank_process_alive()

class _CrashWatcher:
    """后台守护：发现 Firmbank 崩溃弹窗立刻 taskkill /F /T，
    尽量压缩弹窗可见时间。

    Why: U-BANK 12.0.0.6 在转账经办页关闭路径有 use-after-free，无法治本，
    只能在弹窗刚出现的时候立刻强杀整个进程树，让弹窗连同进程一起消失。
    优先用 SetWinEventHook 监听窗口创建/显示/标题变化，再用 20ms 轮询兜底。
    """

    def __init__(self, poll_interval=0.02):
        self._stop = threading.Event()
        self._fired = threading.Event()
        self._interval = poll_interval
        self._thread = None
        self._fired_title = ""
        self._fire_lock = threading.Lock()
        self._user32 = None
        self._hooks = []
        self._win_event_proc = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        self._install_win_event_hooks()
        try:
            while not self._stop.is_set() and not self.fired:
                self._pump_win_messages()
                if self.fired:
                    return

                hwnd, title = _find_ubank_crash_dialog()
                if hwnd:
                    self._fire(title)
                    return

                self._stop.wait(0.005 if self._hooks else self._interval)
        finally:
            self._uninstall_win_event_hooks()

    def _install_win_event_hooks(self):
        try:
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            self._win_event_proc = _WinEventProcType(self._on_win_event)
            self._user32.SetWinEventHook.argtypes = [
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.HANDLE,
                _WinEventProcType,
                wintypes.DWORD,
                wintypes.DWORD,
                wintypes.DWORD,
            ]
            self._user32.SetWinEventHook.restype = wintypes.HANDLE
            self._user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
            self._user32.UnhookWinEvent.restype = wintypes.BOOL
            self._user32.PeekMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
                wintypes.UINT,
            ]
            self._user32.PeekMessageW.restype = wintypes.BOOL
            self._user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
            self._user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]

            for event_id in (
                _EVENT_SYSTEM_DIALOGSTART,
                _EVENT_OBJECT_CREATE,
                _EVENT_OBJECT_SHOW,
                _EVENT_OBJECT_NAMECHANGE,
            ):
                hook = self._user32.SetWinEventHook(
                    event_id,
                    event_id,
                    None,
                    self._win_event_proc,
                    0,
                    0,
                    _WINEVENT_FLAGS,
                )
                if hook:
                    self._hooks.append(hook)

            if self._hooks:
                print("崩溃守护已安装 WinEventHook（窗口创建/显示/标题变化）")
            else:
                err = ctypes.get_last_error()
                print(f"WinEventHook 安装失败，退回 20ms 轮询: {err}")
        except Exception as e:
            self._hooks = []
            self._win_event_proc = None
            print(f"WinEventHook 安装异常，退回 20ms 轮询: {e}")

    def _uninstall_win_event_hooks(self):
        if not self._user32:
            return
        for hook in self._hooks:
            try:
                self._user32.UnhookWinEvent(hook)
            except Exception:
                pass
        self._hooks = []

    def _pump_win_messages(self):
        if not self._user32:
            return
        try:
            msg = wintypes.MSG()
            while self._user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, _PM_REMOVE):
                self._user32.TranslateMessage(ctypes.byref(msg))
                self._user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            pass

    def _on_win_event(self, _hook, _event, hwnd, _id_object, _id_child, _thread, _time_ms):
        if self._stop.is_set() or self.fired or not hwnd:
            return
        self._handle_possible_dialog(hwnd)

    def _handle_possible_dialog(self, hwnd):
        try:
            title = win32gui.GetWindowText(hwnd) or ""
        except Exception:
            return False
        if not _is_ubank_crash_title(title):
            return False
        self._fire(title)
        return True

    def _fire(self, title):
        with self._fire_lock:
            if self._fired.is_set():
                return False
            self._fired_title = title
            self._fired.set()
        if CRASH_DIAG_MODE:
            print("诊断模式已开启：崩溃守护不强杀 Firmbank，等待 Windows 写入 LocalDump")
        else:
            _kill_all_firmbank()
        return True

    def stop(self, timeout=1.0):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    @property
    def fired(self):
        return self._fired.is_set()

    @property
    def fired_title(self):
        return self._fired_title

def _close_with_confirm(main_win):
    """关闭 U-BANK：直接 taskkill /F /T 整个进程树（与函数名"with_confirm"无关）。

    名字保留只是为了不破坏既有调用点；行为已经是无优雅关闭、无确认弹窗的强杀。
    新的等价别名为 _kill_ubank_process_tree（见函数末尾），调用方都可以使用。

    Why: Firmbank 12.0.0.6 在转账经办页接到 WM_CLOSE 必触发内部 COM 对象的
    use-after-free，弹出错误对话框给用户看。任何"优雅关闭"路径（Alt+F4、点 X、
    导航离开页面）都会触发同一条 cleanup 链，无法避免。守护线程式高频轮询
    强杀的速度也跟不上 Windows 的弹窗渲染速度，用户依然能看到弹窗。

    所以彻底改思路：根本不发 WM_CLOSE，直接对 Firmbank 进程树 taskkill /F /T。
    Windows 直接 TerminateProcess，不走 Firmbank 自己的 cleanup → bug 不被触发
    → 弹窗根本不出现。

    CLAUDE.md 已明确：制单成功路径已经点击「经办」按钮；关闭只是收尾清理。
    所以"优雅关闭"对监控流程而言并非必要功能，taskkill 是合规的关闭手段。
    """
    _ = main_win  # 不再使用，保留参数以维持调用方签名兼容
    print("开始关闭程序...")

    if not _is_firmbank_alive():
        print("Firmbank 已不在运行，无需关闭")
        return

    # 直接强杀整个进程树
    print("发送 taskkill /F /T /IM Firmbank.exe")
    _kill_all_firmbank()

    # 等进程树清干净（Windows 杀子进程有秒级延迟）
    end_ts = time.time() + 6
    while time.time() < end_ts:
        if not _is_firmbank_alive():
            break
        time.sleep(0.2)

    # 仍有残留 → 再发一次（极少数子进程可能逗留较久）
    if _is_firmbank_alive():
        time.sleep(0.5)
        _kill_all_firmbank()
        time.sleep(1)

    if _is_firmbank_alive():
        print("Firmbank 仍未关闭，请人工检查")
    else:
        print("Firmbank 已关闭")


# 名字与行为对齐的别名：实际是 taskkill /F /T，调用方应优先用这个名字。
_kill_ubank_process_tree = _close_with_confirm
