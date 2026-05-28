# -*- coding: utf-8 -*-
"""
Open the USB Hub ports needed by the ABC flow.

This does not stop ABC background services. The legacy behavior that closes
the U-key driver's auto-opened ABC public homepage tab is now opt-in only via
ABC_USB_CLOSE_AUTO_HOME_TAB=true.

Default ports on the current 30-port USB Hub:
  - 29: ABC U-key
  - 30: external OK servo / auto-clicker
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
import importlib.util
from pathlib import Path

import pyautogui

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional convenience
    load_dotenv = None

try:
    import win32clipboard
    import win32con
    import win32gui
except ImportError as exc:  # pragma: no cover - Windows-only helper
    raise SystemExit("需要 pywin32: pip install pywin32") from exc


SCRIPT_DIR = Path(__file__).resolve().parent


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


HUB_CTRL = _first_existing_path(
    os.getenv("ABC_USB_HUB_CTRL"),
    os.getenv("USB_HUB_CTRL"),
    SCRIPT_DIR.parent.parent / "公共" / "usbhub" / "usbhub" / "多口USB控制器软件以及驱动" / "hub_ctrl.py",
    _desktop_path("财务", "公共", "usbhub", "usbhub", "多口USB控制器软件以及驱动", "hub_ctrl.py"),
    _desktop_path("usbhub", "usbhub", "多口USB控制器软件以及驱动", "hub_ctrl.py"),
)
HUB_COM = os.getenv("ABC_USB_HUB_COM", os.getenv("USB_HUB_COM", "COM3")).strip() or "COM3"
ABC_UKEY_PORT = os.getenv("ABC_USB_HUB_UKEY_PORT", "29").strip() or "29"
ABC_OK_SERVO_USB_PORT = os.getenv("ABC_OK_SERVO_USB_PORT", "30").strip() or "30"
ABC_PORTS_RAW = os.getenv("ABC_USB_HUB_PORTS", f"{ABC_UKEY_PORT},{ABC_OK_SERVO_USB_PORT}")
RUN_ID = f"{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
ARTIFACT_ROOT = SCRIPT_DIR / "artifacts" / "usb12"
LOG_DIR = ARTIFACT_ROOT / "logs"
DEBUG_RUN_DIR = ARTIFACT_ROOT / "debug_runs" / f"usb_{RUN_ID}"
DEBUG_SCREENSHOT_DIR = DEBUG_RUN_DIR / "screenshots"

UNWANTED_HOME_PREFIXES = (
    "https://www.abchina.com.cn/cn",
    "http://www.abchina.com.cn/cn",
)


def _configure_logging() -> logging.Logger:
    logger = logging.getLogger("abc_usb")
    if logger.handlers:
        return logger

    DEBUG_RUN_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "[%(asctime)s][%(levelname)s] %(message)s", "%H:%M:%S"
    )
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    for path in (LOG_DIR / "open_abc_usb12.log", DEBUG_RUN_DIR / "run.log"):
        file_handler = logging.FileHandler(path, mode="a", encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


log = _configure_logging()


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        log.warning("%s=%r 不是有效整数，使用默认值 %d", name, raw, default)
        return default
    return max(minimum, value)


def cleanup_old_artifacts() -> None:
    if not env_flag("USB12_AUTO_CLEAN_ARTIFACTS", True):
        log.info("[清理] USB12_AUTO_CLEAN_ARTIFACTS=false，跳过 USB 运行产物清理")
        return
    debug_root = ARTIFACT_ROOT / "debug_runs"
    if not debug_root.exists():
        return
    retain_runs = env_int("USB12_ARTIFACT_RETAIN_RUNS", 20)
    max_age_days = env_int("USB12_ARTIFACT_MAX_AGE_DAYS", 30)
    now = time.time()
    candidates = []
    for path in debug_root.iterdir():
        if not path.is_dir() or path.resolve() == DEBUG_RUN_DIR.resolve():
            continue
        candidates.append((path.stat().st_mtime, path))
    candidates.sort(reverse=True)
    removed = 0
    for index, (mtime, path) in enumerate(candidates):
        if index < retain_runs and now - mtime <= max_age_days * 24 * 60 * 60:
            continue
        try:
            if debug_root.resolve() not in path.resolve().parents:
                log.warning("[清理] 跳过异常路径: %s", path)
                continue
            shutil.rmtree(path)
            removed += 1
        except OSError as exc:
            log.warning("[清理] 删除 USB 旧运行目录失败: %s (%s)", path, type(exc).__name__)
    if removed:
        log.info("[清理] 已删除 %d 个 USB 旧运行目录", removed)
    else:
        log.info("[清理] 无需删除 USB 旧运行目录")


def _safe_artifact_name(label: str) -> str:
    keep = []
    for ch in label:
        keep.append(ch if ch.isalnum() or ch in "-_." else "_")
    return ("".join(keep).strip("._") or "checkpoint")[:80]


def safe_screen_capture(label: str) -> None:
    if not env_flag("ENABLE_SCREENSHOT"):
        return
    try:
        DEBUG_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = DEBUG_SCREENSHOT_DIR / f"{_safe_artifact_name(label)}.png"
        pyautogui.screenshot(str(path))
        log.info("已保存屏幕截图: %s", path)
    except Exception as exc:
        log.warning("屏幕截图失败: %s", type(exc).__name__)


def _get_clipboard_text() -> str:
    win32clipboard.OpenClipboard()
    try:
        if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return ""
        return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def _set_clipboard_text(text: str) -> None:
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def _foreground_title() -> str:
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return ""
    return win32gui.GetWindowText(hwnd)


def _abc_browser_windows() -> list[tuple[int, str]]:
    windows: list[tuple[int, str]] = []

    def _collect(hwnd: int, _extra: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd) or ""
        if "中国农业银行" in title:
            windows.append((hwnd, title))
        return True

    win32gui.EnumWindows(_collect, None)
    return windows


def _read_foreground_browser_url() -> str:
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


def _read_browser_url_from_window(hwnd: int) -> str:
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.2)
    except Exception as exc:
        log.warning("[窗口检测] 激活农行浏览器窗口失败: %s", type(exc).__name__)
    return _read_foreground_browser_url()


def _is_unwanted_home_url(url: str) -> bool:
    normalized = url.rstrip("/")
    return any(normalized.startswith(prefix.rstrip("/")) for prefix in UNWANTED_HOME_PREFIXES)


def close_auto_opened_home_tab(wait_seconds: float = 8.0) -> bool:
    log.info("[排错] USB 辅助脚本运行目录: %s", DEBUG_RUN_DIR)
    deadline = time.time() + wait_seconds
    round_no = 0
    while time.time() < deadline:
        round_no += 1
        windows = _abc_browser_windows()
        foreground_title = _foreground_title()
        log.info(
            "[窗口检测] 第 %d 轮，前台标题: %s；农行浏览器窗口=%d 个",
            round_no,
            foreground_title or "<empty>",
            len(windows),
        )
        if not windows:
            time.sleep(0.3)
            continue

        saw_non_home = False
        for hwnd, title in windows:
            log.info("[窗口检测] 检查农行浏览器窗口: %s", title)
            url = _read_browser_url_from_window(hwnd)
            log.info("[窗口检测] 第 %d 轮，读取到 URL: %s", round_no, url or "<empty>")
            if _is_unwanted_home_url(url):
                pyautogui.hotkey("ctrl", "w")
                log.info("已关闭 U 盾自动打开的农行官网标签: %s", url)
                safe_screen_capture("usb_closed_home_tab")
                return True
            saw_non_home = True

        if saw_non_home:
            log.info("检测到农行窗口，但没有匹配官网首页 URL，暂不关闭")
            safe_screen_capture("usb_detected_non_home_tab")
            return False
        time.sleep(0.3)

    log.info("未检测到需要关闭的农行官网首页标签")
    safe_screen_capture("usb_no_home_tab_detected")
    return False


def parse_required_ports(raw: str) -> list[str]:
    ports: list[str] = []
    for part in re.split(r"[,;，、\s]+", raw or ""):
        text = part.strip()
        if not text:
            continue
        if not text.isdigit():
            raise SystemExit(f"USB Hub 端口必须是数字: {text!r}")
        port_no = int(text)
        if not 1 <= port_no <= 30:
            raise SystemExit(f"USB Hub 端口超出范围 1-30: {port_no}")
        port = str(port_no)
        if port not in ports:
            ports.append(port)
    if not ports:
        raise SystemExit(f"未配置 ABC_USB_HUB_PORTS，至少需要 {ABC_UKEY_PORT} 口")
    if ABC_UKEY_PORT not in ports:
        raise SystemExit(f"农行制单必须包含 {ABC_UKEY_PORT} 口 U 盾，拒绝继续")
    return ports


def _load_hub_ctrl_module():
    spec = importlib.util.spec_from_file_location("abc_hub_ctrl_runtime", HUB_CTRL)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载 USB Hub 控制脚本: {HUB_CTRL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _format_hub_response(resp: dict) -> dict:
    return {bank: data.hex() for bank, data in resp.items()}


def set_required_ports_exactly(ports: list[str]) -> None:
    """
    绿精灵协议没有可靠的状态读取。不能用多次独立 CLI 调用逐个 off/on，
    因为每次新进程都会从默认 mask 开始，最后一次 on 会把其它口又带开。
    这里在同一个 USBHub 实例里一次性构造 bank0/bank1 的最终 mask。
    """
    hub_ctrl = _load_hub_ctrl_module()
    required = {int(port) for port in ports}
    hub = hub_ctrl.USBHub(HUB_COM)
    hub.mask = {bank: 0x00 for bank in hub_ctrl.BANKS}
    planned = []
    for port_no, (bank, bit) in sorted(hub_ctrl.PORT_BIT.items()):
        if port_no in required:
            hub.mask[bank] &= ~(1 << bit) & 0xFF
            planned.append(f"{port_no}:on")
        else:
            hub.mask[bank] |= 1 << bit
            planned.append(f"{port_no}:off")
    log.info("USB Hub 最终端口计划: %s", ", ".join(planned))
    resp = hub._send(hub_ctrl.BANKS)
    log.info("USB Hub 精确端口响应: %s", _format_hub_response(resp))


def open_required_ports() -> None:
    if not HUB_CTRL.exists():
        raise SystemExit(f"未找到 USB Hub 控制脚本: {HUB_CTRL}")
    ports = parse_required_ports(ABC_PORTS_RAW)
    exact_ports = env_flag("ABC_USB_HUB_EXACT_PORTS", True)
    log.info("打开 USB Hub 端口: ports=%s, com=%s", ",".join(ports), HUB_COM)
    if exact_ports:
        log.info(
            "精确端口模式：在同一协议会话内只保留 %s 通电；不执行 all-off",
            ",".join(ports),
        )
    else:
        log.warning(
            "ABC_USB_HUB_EXACT_PORTS=false 仍使用单会话最终 mask，避免逐个 on/off 把其它口带开"
        )
    set_required_ports_exactly(ports)
    time.sleep(0.3)
    log.info("USB Hub 端口已打开: %s", ",".join(ports))


def main() -> int:
    if load_dotenv is not None:
        load_dotenv(SCRIPT_DIR / ".env")
    log.info("[排错] 本次运行 ID: %s", RUN_ID)
    log.info("[排错] 本次运行日志: %s", DEBUG_RUN_DIR / "run.log")
    cleanup_old_artifacts()
    safe_screen_capture("usb_before_open_port")
    open_required_ports()
    if env_flag("ABC_USB_CLOSE_AUTO_HOME_TAB", False):
        close_auto_opened_home_tab(wait_seconds=10.0)
        safe_screen_capture("usb_after_close_home_tab")
    else:
        log.info("ABC_USB_CLOSE_AUTO_HOME_TAB=false，跳过关闭农行官网首页标签")
        safe_screen_capture("usb_after_open_ports")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
