"""Configuration and environment loading for the BOC automation."""

import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BOC_URL = "https://netc1.igtb.boc.cn/#/login-page?redirect=%2Findex"
BOC_EXTENSION_IDS = (
    "nhhdpdhiemjpkaikglglhabjafffdjfo",  # Chrome Web Store
    "cpiogedigcbdifgefmkjpfnampochfca",  # Microsoft Edge Add-ons
)
BOC_EXTENSION_NAME = "BOC Certificate Application Extension"
BOC_UNWANTED_HOME_PREFIXES = (
    "https://www.boc.cn",
    "http://www.boc.cn",
)
BOC_CERT_WINDOW_TITLES = ("选择证书",)
BOC_USHIELD_PIN_WINDOW_TITLES = ("校验用户密码", "U盾", "U 盾", "USBKey", "UKey", "密码", "PIN")
_UIA_CHROME_TITLE_KEYWORDS = ("Chrome", "Chromium", "Edge", "iGTB", "企业网银", "中国银行")
_UIA_CERT_REQUIRED_TOKENS = ("选择证书", "选择用于身份验证")
_UIA_CERT_HOST_TOKENS = (
    "netc2.igtb.boc.cn:443",
    "netc2.igtb.boc.cn",
    "netc1.igtb.boc.cn:443",
    "netc1.igtb.boc.cn",
)
_UIA_CERT_OK_BUTTON_TEXTS = ("确定", "OK")
_UIA_CERT_CANCEL_BUTTON_TEXTS = ("取消", "Cancel")
_UIA_TRAVERSAL_MAX_DEPTH = 25
_UIA_TRAVERSAL_MAX_NODES = 4000

ENV_FILE = ROOT / ".env"
LOG_DIR = ROOT / "logs"
BATCH_LOG_DIR = LOG_DIR / "batches"
LOG_FILE = LOG_DIR / "boc.log"
RUN_ID = f"{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
DEBUG_ROOT = ROOT / "debug_runs"
DEBUG_DIR = DEBUG_ROOT / RUN_ID
DEBUG_SCREENSHOT_DIR = DEBUG_DIR / "screenshots"
DEBUG_PAGE_SCREENSHOT_DIR = DEBUG_DIR / "page_screenshots"
PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


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


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_env_file(ENV_FILE)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_on_by_default(name: str) -> bool:
    return os.environ.get(name, "1").strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


_LEGACY_PASSWORD = os.environ.get("BOC_USHIELD_PASSWORD", "").strip()
BOC_USHIELD_PIN = os.environ.get("BOC_USHIELD_PIN", "").strip()
BOC_LOGIN_PASSWORD = os.environ.get("BOC_LOGIN_PASSWORD", _LEGACY_PASSWORD).strip()
BOC_AUTO_USHIELD_PIN = _env_flag("BOC_AUTO_USHIELD_PIN")
BOC_ENABLE_TRANSFER_FILL = _env_flag("BOC_ENABLE_TRANSFER_FILL")
BOC_ENABLE_ORDER_SUBMIT = _env_flag("BOC_ENABLE_ORDER_SUBMIT")
BOC_RUN_UNTIL = os.environ.get("BOC_RUN_UNTIL", "").strip().lower()
BOC_STOP_AT_LOGIN_PAGE = _env_flag("BOC_STOP_AT_LOGIN_PAGE") or BOC_RUN_UNTIL in {
    "login-page",
    "login_page",
    "open-login",
    "pre-login",
}
BOC_STOP_AFTER_LOGIN = _env_flag("BOC_STOP_AFTER_LOGIN") or BOC_RUN_UNTIL in {
    "login",
    "logged-in",
    "post-login",
    "after-login",
}
BOC_CHROME_PROFILE = os.environ.get("BOC_CHROME_PROFILE", "Default").strip() or "Default"
BOC_CLOSE_ON_FINISH = _env_on_by_default("BOC_CLOSE_ON_FINISH")
BOC_USBHUB_CTRL_PATH = _first_existing_path(
    os.environ.get("BOC_USBHUB_CTRL_PATH"),
    ROOT.parent.parent / "公共" / "usbhub" / "usbhub" / "多口USB控制器软件以及驱动" / "hub_ctrl.py",
    _desktop_path("财务", "公共", "usbhub", "usbhub", "多口USB控制器软件以及驱动", "hub_ctrl.py"),
    _desktop_path("usbhub", "usbhub", "多口USB控制器软件以及驱动", "hub_ctrl.py"),
)
BOC_USBHUB_COM = os.environ.get("BOC_USBHUB_COM", "COM3").strip()
BOC_USBHUB_PORT = os.environ.get("BOC_USBHUB_PORT", "10").strip()
BOC_DIRECT_DNS_SERVER = os.environ.get("BOC_DIRECT_DNS_SERVER", "").strip()
BOC_USBHUB_POWER_ON_START = _env_on_by_default("BOC_USBHUB_POWER_ON_START")
BOC_USBHUB_ALL_OFF_ON_FINISH = _env_on_by_default("BOC_USBHUB_ALL_OFF_ON_FINISH")
try:
    BOC_USBHUB_SETTLE_SECONDS = float(os.environ.get("BOC_USBHUB_SETTLE_SECONDS", "8"))
except ValueError:
    BOC_USBHUB_SETTLE_SECONDS = 8.0

BOC_KEEP_DEBUG_RUNS = _env_int("BOC_KEEP_DEBUG_RUNS", 30)
BOC_KEEP_BATCH_LOGS = _env_int("BOC_KEEP_BATCH_LOGS", 30)
