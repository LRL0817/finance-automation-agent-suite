"""Runtime configuration, coordinates, screenshots, and USB Hub selection."""
import os
import sys
import time

import pyautogui

# Export target defaults are 1920x1080 with Windows scaling at 100%.
# Original fallback coordinates were measured at 125%, then scaled by 100/125.
SOURCE_DPI_SCALE = float(os.environ.get("CIB_SOURCE_DPI_SCALE", "1.25"))
TARGET_DPI_SCALE = float(os.environ.get("CIB_TARGET_DPI_SCALE", "1.0"))
COORD_SCALE = TARGET_DPI_SCALE / SOURCE_DPI_SCALE
REFERENCE_WINDOW_ORIGIN = (87, 17)
_ACTIVE_WINDOW_ORIGIN = REFERENCE_WINDOW_ORIGIN


def fixed_point(x, y):
    ox, oy = _ACTIVE_WINDOW_ORIGIN
    rx, ry = REFERENCE_WINDOW_ORIGIN
    return (
        ox + round((x - rx) * COORD_SCALE),
        oy + round((y - ry) * COORD_SCALE),
    )


def click_fixed(x, y, label="固定点"):
    cx, cy = fixed_point(x, y)
    pyautogui.click(cx, cy)
    print(f"  已点击 [{label}] @ ({cx}, {cy}) <= 原125%点 ({x}, {y})")
    return cx, cy


def set_active_window_origin(origin):
    global _ACTIVE_WINDOW_ORIGIN
    _ACTIVE_WINDOW_ORIGIN = origin


def get_active_window_origin():
    return _ACTIVE_WINDOW_ORIGIN


_CIB_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(_CIB_ROOT, ".env")
SHORTCUT_PATH = os.environ.get(
    "CIB_SHORTCUT_PATH",
    os.path.join(os.path.expanduser("~"), "Desktop", "兴业银行企业网银.lnk"),
)
SCREENSHOT_DIR = os.path.join(_CIB_ROOT, "screenshots")


def _desktop_path(*parts):
    return os.path.join(os.path.expanduser("~"), "Desktop", *parts)


def _first_existing_path(*candidates):
    fallback = None
    for candidate in candidates:
        if not candidate:
            continue
        path = os.path.normpath(os.fspath(candidate))
        if fallback is None:
            fallback = path
        if os.path.exists(path):
            return path
    if fallback is None:
        raise SystemExit("未配置可用路径候选")
    return fallback


USB_HUB_CTRL_DIR = _first_existing_path(
    os.environ.get("USB_HUB_CTRL_DIR"),
    os.path.join(os.path.dirname(os.path.dirname(_CIB_ROOT)), "公共", "usbhub", "usbhub", "多口USB控制器软件以及驱动"),
    _desktop_path("财务", "公共", "usbhub", "usbhub", "多口USB控制器软件以及驱动"),
    _desktop_path("usbhub", "usbhub", "多口USB控制器软件以及驱动"),
)
# USB Hub defaults: COM/settle time follow the stress-test script; current CIB UKey is port 17.
USB_HUB_COMPANY_PORTS = {
    "兴业银行-风飞格公司": 17,
}
USB_HUB_COM_PORT = os.environ.get("CIB_USB_HUB_COM", os.environ.get("USB_HUB_COM", "COM3"))
DEFAULT_USB_HUB_COMPANY = os.environ.get("CIB_USB_HUB_COMPANY", "兴业银行-风飞格公司")
USB_HUB_ENABLED = os.environ.get("CIB_USB_HUB_ENABLED", "1").strip().lower() not in {
    "0", "false", "no", "off"
}
USB_HUB_SETTLE_SECONDS = float(os.environ.get("CIB_USB_HUB_SETTLE_SECONDS", "10.0"))


_SCREENSHOT_PREFIX = ""


def set_screenshot_prefix(prefix):
    """设置当前流程的截图文件名前缀，避免批量场景里多笔互相覆盖。"""
    global _SCREENSHOT_PREFIX
    _SCREENSHOT_PREFIX = prefix or ""


def screenshot(name):
    if not os.path.exists(SCREENSHOT_DIR):
        os.makedirs(SCREENSHOT_DIR)
    path = os.path.join(SCREENSHOT_DIR, f"{_SCREENSHOT_PREFIX}{name}.png")
    pyautogui.screenshot(path)
    print(f"  [截图] {path}")


def load_config():
    config = {}
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#") or not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            config[key.strip()] = value.strip()
    return config


def _get_usb_hub_target():
    company = DEFAULT_USB_HUB_COMPANY
    port = USB_HUB_COMPANY_PORTS.get(company)
    if port is None:
        raise SystemExit(f"USB Hub 未配置公司端口: {company}")

    port_override = os.environ.get("CIB_USB_HUB_PORT")
    if port_override:
        try:
            port = int(port_override)
        except ValueError as exc:
            raise SystemExit(f"CIB_USB_HUB_PORT 必须是 1-30 的整数，当前为: {port_override}") from exc

    if port < 1 or port > 30:
        raise SystemExit(f"USB Hub 端口必须是 1-30，当前为: {port}")
    return company, port


def _load_usb_hub_class():
    if not os.path.exists(os.path.join(USB_HUB_CTRL_DIR, "hub_ctrl.py")):
        raise SystemExit(f"未找到 USB Hub 控制脚本: {USB_HUB_CTRL_DIR}\\hub_ctrl.py")

    if USB_HUB_CTRL_DIR not in sys.path:
        sys.path.insert(0, USB_HUB_CTRL_DIR)

    try:
        from hub_ctrl import USBHub
    except ImportError as exc:
        raise SystemExit("无法加载 hub_ctrl.py，请先安装依赖: pip install pyserial") from exc
    return USBHub


def select_usb_hub_port():
    """制单开始前 only 打开兴业银行风飞格公司对应 USB Hub 17 口。"""
    if not USB_HUB_ENABLED:
        print("  USB Hub: 已按 CIB_USB_HUB_ENABLED 跳过端口切换")
        return

    company, port = _get_usb_hub_target()
    USBHub = _load_usb_hub_class()
    print(f"  USB Hub: {company} 使用 {port} 口，COM={USB_HUB_COM_PORT}，正在 only 通电...")
    hub = None
    try:
        hub = USBHub(USB_HUB_COM_PORT)
        hub.only(port)
    except Exception as exc:
        raise SystemExit(f"USB Hub 切换到 {port} 口失败: {exc}") from exc
    finally:
        if hub is not None:
            try:
                hub.close()
            except Exception:
                pass
    time.sleep(USB_HUB_SETTLE_SECONDS)
    print(f"  USB Hub: 已 only 打开 {port} 口 ({company})")


def all_off_usb_hub_ports():
    """收尾时 all-off 关闭所有 U 盾口。"""
    if not USB_HUB_ENABLED:
        print("  USB Hub: 已按 CIB_USB_HUB_ENABLED 跳过 all-off")
        return False

    hub = None
    try:
        USBHub = _load_usb_hub_class()
        hub = USBHub(USB_HUB_COM_PORT)
        hub.all_off()
        print(f"  USB Hub: 已 all-off 关闭所有 U 盾口，COM={USB_HUB_COM_PORT}")
        return True
    except BaseException as exc:
        print(f"  USB Hub: all-off 失败: {exc}")
        return False
    finally:
        if hub is not None:
            try:
                hub.close()
            except Exception:
                pass
