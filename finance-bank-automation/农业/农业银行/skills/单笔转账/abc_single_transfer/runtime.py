# -*- coding: utf-8 -*-
"""
农行企业网银 - 单笔转账自动化（默认仅自动填表；单条提交测试需显式门禁）

主入口：
    python skills/单笔转账/open_browser.py

设计要点：
- DOM 内事件由 Playwright 处理；OS 级（证书弹窗 / K 宝密码框）由 pyautogui 兜底。
- 默认仅做导航 + 填表；只有 ABC_ALLOW_SUBMIT_ONCE=true 且本次仅 1 条数据时才点击提交一次。
- 二次确认、验证码等步骤不自动处理；如需物理 OK，必须显式启用外置点击器开关。
- 敏感信息（密码、账号）不会以明文出现在日志中。
"""
from __future__ import annotations

import getpass
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional
from urllib.parse import urlparse

import pyautogui
from dotenv import load_dotenv
from playwright.sync_api import (
    BrowserContext,
    Error as PlaywrightError,
    Locator,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeout,
    sync_playwright,
)


# ---------------- 路径与常量 ----------------
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_DIR = os.path.dirname(PACKAGE_DIR)
ABC_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, "..", ".."))
ENV_PATH = os.path.join(ABC_ROOT, ".env")
DEFAULT_TRANSFER_DATA_PATH = os.path.join(ABC_ROOT, "transfer_data.json")
DEFAULT_USB12_HELPER_PATH = os.path.join(ABC_ROOT, "open_abc_usb12.py")
RUN_ID = f"{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
ARTIFACT_ROOT = os.path.join(ABC_ROOT, "artifacts", "单笔转账")
LOG_DIR = os.path.join(ARTIFACT_ROOT, "logs")
DEBUG_ROOT = os.path.join(ARTIFACT_ROOT, "debug_runs")
DEBUG_RUN_DIR = os.path.join(DEBUG_ROOT, RUN_ID)
DEBUG_SCREENSHOT_DIR = os.path.join(DEBUG_RUN_DIR, "screenshots")
LIVE_SCREENSHOT_DIR = os.path.join(DEBUG_RUN_DIR, "live_screenshots")

DEFAULT_HOME_URL = "https://www.abchina.com.cn/cn/"
CORPORATE_LOGIN_LINK_TEXTS = ("企业网银登录", "企业网上银行登录", "企业网银")
# 真实 M3 项目在桌面根目录（~/Desktop/M3直供合同付款数据获取），不在
# 财务\农业 下。旧写法用 os.path.dirname(ABC_ROOT) 会偏到
# 财务\农业\M3直供合同付款数据获取（不存在）。直接运行农行脚本时按桌面根
# 兜底；M3_TRANSFER_DATA_DIR / M3_BANK_FORM_PATH 环境变量仍优先（见 data.py）。
DEFAULT_M3_TRANSFER_DATA_DIR = os.path.join(
    os.path.expanduser("~"), "Desktop", "M3直供合同付款数据获取"
)

CERT_LOGIN_BUTTON_SELECTOR = "#m-kbbtn-new"

DEFAULT_NETWORKIDLE_TIMEOUT_MS = 15000
DEFAULT_VISIBLE_TIMEOUT_MS = 15000
LONG_VISIBLE_TIMEOUT_MS = 30000
LOGIN_HOME_TIMEOUT_MS = 120000
LOGIN_PAGE_GOTO_TIMEOUT_MS = 30000

KB_PASSWORD_TYPE_INTERVAL_S = 0.08

PAYMENT_TAB_TEXT = "付款业务"
SINGLE_TRANSFER_TEXT = "单笔转账"

# K 宝密码窗口可能的标题（不同插件版本/中英空格混排）
KB_WINDOW_TITLES = ("验证K宝密码", "验证 K 宝密码", "K宝密码", "K 宝密码")

# handle_certificate_selection 返回值
CERT_AUTO_CONFIRMED = "auto_confirmed"
CERT_PASSWORD_WINDOW_DETECTED = "password_window_detected"
CERT_MANUAL_REQUIRED = "manual_required"

# 转账数据字段名
F_ACCOUNT = "收款账号"
F_NAME = "收款户名"
F_BANK = "收款方开户行"
F_BRANCH = "支行名称"
F_AMOUNT = "金额"
F_PURPOSE = "用途"
BATCH_LABEL_KEY = "__batch_label"
BATCH_SOURCE_KEY = "__batch_source"

TRANSFER_FIELD_ALIASES = {
    F_ACCOUNT: ("收款账号", "收款方账号", "收方账号", "银行账户", "账号"),
    F_NAME: ("收款户名", "收款方户名", "收方户名", "收款单位名称", "收款方名称", "户名"),
    F_BANK: ("收款方开户行", "开户银行", "开户行", "收款银行"),
    F_BRANCH: ("支行名称", "开户支行", "收款方支行", "开户网点"),
    F_AMOUNT: ("金额", "申请金额", "转账金额"),
    F_PURPOSE: ("用途", "申请说明", "备注", "摘要"),
}

TRANSFER_ENV_FIELD_NAMES = {
    F_ACCOUNT: ("TRANSFER_ACCOUNT", "TRANSFER_PAYEE_ACCOUNT"),
    F_NAME: ("TRANSFER_NAME", "TRANSFER_PAYEE_NAME"),
    F_BANK: ("TRANSFER_BANK", "TRANSFER_PAYEE_BANK"),
    F_BRANCH: ("TRANSFER_BRANCH", "TRANSFER_PAYEE_BRANCH"),
    F_AMOUNT: ("TRANSFER_AMOUNT",),
    F_PURPOSE: ("TRANSFER_PURPOSE",),
}

COMMON_BANK_NAMES = (
    "中国农业银行",
    "中国工商银行",
    "中国建设银行",
    "中国银行",
    "交通银行",
    "招商银行",
    "民生银行",
    "中信银行",
    "兴业银行",
    "平安银行",
    "光大银行",
    "浦发银行",
    "广发银行",
    "华夏银行",
    "徽商银行",
    "北京银行",
    "上海银行",
    "渤海银行",
    "浙商银行",
    "城市商业银行",
    "农村商业银行",
    "农村信用合作社",
    "农村合作银行",
    "村镇银行",
    "中国邮政储蓄银行",
)

# 校验正则
ACCOUNT_RE = re.compile(r"^\d{8,32}$")
AMOUNT_RE = re.compile(r"^\d+(\.\d{1,2})?$")
AMOUNT_WITH_COMMAS_RE = re.compile(r"^\d{1,3}(,\d{3})+(\.\d{1,2})?$")

# 仅允许 abchina.com.cn 及其子域，避免被钓鱼链接污染
# 注意：host 白名单仅能挡住明显的钓鱼域名，无法防御"合法子域 + 开放重定向"
# 之类的间接钓鱼。最终落地页是否真的是企业网银登录页，仍需人工核对。
ALLOWED_LOGIN_HOST_SUFFIX = ".abchina.com.cn"
ALLOWED_LOGIN_HOST = "abchina.com.cn"


# ---------------- 日志 ----------------
def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _configure_logging() -> logging.Logger:
    logger = logging.getLogger("abc_transfer")
    if not logger.handlers:
        formatter = logging.Formatter(
            "[%(asctime)s][%(levelname)s] %(message)s", "%H:%M:%S"
        )
        if getattr(sys, "stderr", None) is not None:
            handler = logging.StreamHandler()
            handler.setFormatter(formatter)
            logger.addHandler(handler)

        _ensure_dir(LOG_DIR)
        file_handler = logging.FileHandler(
            os.path.join(LOG_DIR, "abc_transfer.log"),
            mode="a",
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        _ensure_dir(DEBUG_RUN_DIR)
        run_file_handler = logging.FileHandler(
            os.path.join(DEBUG_RUN_DIR, "run.log"),
            mode="a",
            encoding="utf-8",
        )
        run_file_handler.setFormatter(formatter)
        logger.addHandler(run_file_handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


log = _configure_logging()
_debug_step = 0
_live_recorder: Optional["LiveScreenshotRecorder"] = None


def has_interactive_console() -> bool:
    return bool(getattr(sys, "stdin", None) and sys.stdin.isatty())


def wait_for_manual_login_prompt(prompt: str) -> bool:
    if has_interactive_console():
        try:
            input(prompt)
            return True
        except (EOFError, KeyboardInterrupt):
            log.info("人工确认被中断，按 Ctrl+C 退出")
            return False
    log.info("%s 当前为无控制台后台模式，将直接等待企业网银首页元素出现。", prompt)
    return True


# ---------------- 小工具 ----------------
def mask_account(account: str, keep_tail: int = 4) -> str:
    if not account:
        return ""
    if len(account) <= keep_tail:
        return "*" * len(account)
    return "*" * (len(account) - keep_tail) + account[-keep_tail:]


def mask_name(name: str) -> str:
    if not name:
        return ""
    if len(name) <= 1:
        return name + "***"
    return name[0] + "***"


def safe_sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def env_flag(name: str, default: bool = False) -> bool:
    """统一读取布尔型 .env 开关。仅 'true/1/yes/on' 视为真，其余视为默认/假。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        log.warning("%s=%r 不是有效数字，使用默认值 %.1f", name, raw, default)
        return default
    return max(0.1, value)


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


def _safe_artifact_name(label: str) -> str:
    normalized = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", label, flags=re.UNICODE)
    normalized = normalized.strip("._")
    return normalized[:80] or "checkpoint"


def _foreground_window_title() -> str:
    try:
        import win32gui
    except ImportError:
        return ""
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return ""
        return win32gui.GetWindowText(hwnd)
    except Exception:
        return ""


def _write_text_line(path: str, line: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _capture_screen_with_powershell(path: str) -> bool:
    """用 .NET CopyFromScreen 抓整屏，能覆盖原生弹窗/前台应用。"""
    _ensure_dir(os.path.dirname(path))
    ps_path = path.replace("'", "''")
    command = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$bmp.Save('{ps_path}', [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose()
$bmp.Dispose()
"""
    kwargs = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 10,
    }
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        **kwargs,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        log.warning(
            "[运行截图] PowerShell 截图失败 returncode=%s stdout=%r stderr=%r",
            result.returncode,
            stdout[:300],
            stderr[:300],
        )
        return False
    return os.path.exists(path)


class LiveScreenshotRecorder:
    """运行期间持续保留整屏截图，方便把前台焦点与日志时间线对齐。"""

    def __init__(self, interval_s: float, max_files: int) -> None:
        self.interval_s = interval_s
        self.max_files = max_files
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._seq = 0
        self._manifest = os.path.join(LIVE_SCREENSHOT_DIR, "manifest.csv")
        _ensure_dir(LIVE_SCREENSHOT_DIR)
        _write_text_line(
            self._manifest,
            "seq,timestamp,label,foreground_title,path",
        )

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name="abc-live-screenshots",
            daemon=True,
        )
        self._thread.start()
        log.info(
            "[运行截图] 已启动：目录=%s，间隔=%.1fs，最多保留=%d 张",
            LIVE_SCREENSHOT_DIR,
            self.interval_s,
            self.max_files,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=15)
        self.capture("stop")
        log.info("[运行截图] 已停止：%s", LIVE_SCREENSHOT_DIR)

    def capture(self, label: str) -> Optional[str]:
        with self._lock:
            self._seq += 1
            seq = self._seq
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        safe_label = _safe_artifact_name(label)
        filename = f"{seq:04d}_{timestamp}_{safe_label}.png"
        path = os.path.join(LIVE_SCREENSHOT_DIR, filename)
        foreground_title = _foreground_window_title()
        try:
            if not _capture_screen_with_powershell(path):
                log.warning("[运行截图] 截图文件未生成：%s", path)
                return None
            manifest_title = foreground_title.replace('"', '""')
            _write_text_line(
                self._manifest,
                f'{seq},"{timestamp}","{safe_label}","{manifest_title}","{path}"',
            )
            log.info(
                "[运行截图] 已保存 %s（前台=%s）",
                os.path.basename(path),
                foreground_title or "<empty>",
            )
            self._trim_old_files()
            return path
        except Exception as exc:
            log.warning("[运行截图] 保存失败: %s", type(exc).__name__)
            return None

    def _run(self) -> None:
        self.capture("live_start")
        while not self._stop.wait(self.interval_s):
            self.capture("live")

    def _trim_old_files(self) -> None:
        try:
            files = sorted(
                (
                    os.path.join(LIVE_SCREENSHOT_DIR, name)
                    for name in os.listdir(LIVE_SCREENSHOT_DIR)
                    if name.lower().endswith(".png")
                ),
                key=os.path.getmtime,
            )
            overflow = len(files) - self.max_files
            for path in files[: max(0, overflow)]:
                try:
                    os.remove(path)
                except OSError:
                    pass
        except OSError:
            pass


def start_live_screenshot_recorder() -> Optional[LiveScreenshotRecorder]:
    # 默认关闭：整屏截图会把账号/金额/户名/桌面/K 宝窗口落盘，仅在排错时显式开启。
    if not env_flag("ENABLE_LIVE_SCREENSHOTS", False):
        log.info(
            "[运行截图] ENABLE_LIVE_SCREENSHOTS 未启用（默认关闭），不会写入整屏截图"
        )
        return None
    recorder = LiveScreenshotRecorder(
        interval_s=env_float("LIVE_SCREENSHOT_INTERVAL_S", 2.0),
        max_files=env_int("LIVE_SCREENSHOT_MAX_FILES", 300, minimum=10),
    )
    recorder.start()
    return recorder


def capture_live_screenshot(label: str) -> Optional[str]:
    if _live_recorder is None:
        return None
    return _live_recorder.capture(label)


def _page_brief(page: Optional[Page]) -> str:
    if page is None:
        return "page=None"
    try:
        if page.is_closed():
            return "page=closed"
        return f"url={page.url}"
    except PlaywrightError as exc:
        return f"page_state={type(exc).__name__}"


def debug_checkpoint(
    label: str,
    page: Optional[Page] = None,
    force_screenshot: bool = False,
    skip_page_screenshot: bool = False,
) -> None:
    """
    记录一轮排错节点；page.screenshot 只有 ENABLE_SCREENSHOT=true 时才会落盘。

    skip_page_screenshot=True 用于证书 / K 宝阶段：Chrome 内嵌的原生「选择证书」
    模态会阻塞所有 Playwright 协议调用，page.screenshot() 会一直等到模态被关闭、
    甚至抛 Timeout，从而把整个证书状态机卡死在排错路径上。整屏 live screenshot
    走 PowerShell 抓屏，与 Playwright 无关，不受此参数影响。
    """
    global _debug_step
    _debug_step += 1
    log.info("[排错] 第 %03d 轮：%s（%s）", _debug_step, label, _page_brief(page))
    if env_flag("LIVE_SCREENSHOT_ON_CHECKPOINT", False):
        capture_live_screenshot(f"checkpoint_{_debug_step:03d}_{label}")

    if not env_flag("ENABLE_SCREENSHOT"):
        return
    if page is None:
        return
    if skip_page_screenshot:
        return
    if not (force_screenshot or env_flag("SCREENSHOT_ON_EACH_STEP", True)):
        return
    filename = f"{_debug_step:03d}_{_safe_artifact_name(label)}.png"
    safe_screenshot(
        page,
        os.path.join(DEBUG_SCREENSHOT_DIR, filename),
        label=f"第 {_debug_step:03d} 轮 {label}",
    )


# ---------------- 配置加载 ----------------
def load_environment() -> None:
    if os.path.exists(ENV_PATH):
        load_dotenv(ENV_PATH)
        log.info("已加载 .env: %s", ENV_PATH)
    else:
        log.warning("未发现 .env: %s（仍可继续运行，但需要交互式输入参数）", ENV_PATH)


def _validate_login_url(url: str) -> str:
    """
    校验 ABC_LOGIN_URL：必须是 https，host 必须是 abchina.com.cn 或其子域。
    校验失败抛 ValueError，避免被钓鱼链接污染。
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"ABC_LOGIN_URL 必须使用 https: {url!r}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError(f"ABC_LOGIN_URL 缺少 host: {url!r}")
    if host != ALLOWED_LOGIN_HOST and not host.endswith(ALLOWED_LOGIN_HOST_SUFFIX):
        raise ValueError(
            f"ABC_LOGIN_URL host 必须为 {ALLOWED_LOGIN_HOST} 或其子域名，实际: {host!r}"
        )
    return url


def assert_current_page_allowed(page: Page, context_label: str) -> None:
    """
    在每次 page.goto / 新标签页捕获 / 切换登录页之后调用：
    校验当前 page.url 必须是 https + abchina.com.cn / 子域；
    失败直接抛 RuntimeError，由 main 兜底中止流程，避免在被劫持/被钓鱼的页面上点击或输入。
    """
    try:
        url = page.url
    except PlaywrightError as e:
        raise RuntimeError(
            f"[安全] 无法读取 {context_label} 的 URL: {type(e).__name__}"
        ) from e
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise RuntimeError(
            f"[安全] {context_label} 当前 URL scheme 不是 https，已中止流程: {url!r}"
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise RuntimeError(f"[安全] {context_label} 当前 URL 缺少 host: {url!r}")
    if host != ALLOWED_LOGIN_HOST and not host.endswith(ALLOWED_LOGIN_HOST_SUFFIX):
        raise RuntimeError(
            f"[安全] {context_label} 当前 host={host!r} 不在 ABC 白名单内，已中止流程"
        )
    log.info("[安全] %s host=%s 已通过白名单校验", context_label, host)


def get_login_url() -> tuple[str, bool]:
    """
    返回 (url, is_direct_login_page)。
    is_direct_login_page=True 表示该 URL 直接是登录页，无需点击「企业网银登录」入口。
    """
    url = os.getenv("ABC_LOGIN_URL", "").strip()
    if url:
        _validate_login_url(url)
        log.info("使用 ABC_LOGIN_URL 直达登录页（已通过 host 校验）")
        return url, True
    log.info("未配置 ABC_LOGIN_URL，回退到首页 %s 后点击企业网银登录", DEFAULT_HOME_URL)
    return DEFAULT_HOME_URL, False




# ---------------- 截图 ----------------
def safe_screenshot(page: Optional[Page], path: str, label: str = "截图") -> None:
    """
    转账页面截图可能含敏感信息（账号/金额/客户名等），默认不截图。
    仅当 ENABLE_SCREENSHOT=true 时才会写入磁盘。
    """
    if not env_flag("ENABLE_SCREENSHOT"):
        log.info("ENABLE_SCREENSHOT 未启用，已跳过%s（避免敏感信息落盘）", label)
        return
    if page is None:
        log.warning("%s失败：当前没有可截图的页面", label)
        return
    try:
        if page.is_closed():
            log.warning("%s失败：页面已关闭", label)
            return
        _ensure_dir(os.path.dirname(path))
        page.screenshot(path=path, full_page=True, timeout=5000)
        log.info("已保存%s: %s", label, path)
    except (PlaywrightError, PlaywrightTimeout) as exc:
        log.warning("%s失败: %s", label, type(exc).__name__)

# ---------------- 运行产物清理 ----------------
def _path_inside(child: str, parent: str) -> bool:
    child_abs = os.path.abspath(child)
    parent_abs = os.path.abspath(parent)
    try:
        return os.path.commonpath([child_abs, parent_abs]) == parent_abs
    except ValueError:
        return False


def cleanup_old_runtime_artifacts() -> None:
    """
    长期保留策略：运行产物统一写到 artifacts/单笔转账，并在每次启动时清理旧 run。
    默认保留最近 20 个 run，同时删除超过 30 天的 run；可用 .env 调整：
      - ABC_AUTO_CLEAN_ARTIFACTS=false 关闭自动清理
      - ABC_ARTIFACT_RETAIN_RUNS=20 调整保留数量
      - ABC_ARTIFACT_MAX_AGE_DAYS=30 调整保留天数
    """
    if not env_flag("ABC_AUTO_CLEAN_ARTIFACTS", True):
        log.info("[清理] ABC_AUTO_CLEAN_ARTIFACTS=false，跳过运行产物清理")
        return
    if not os.path.isdir(DEBUG_ROOT):
        return

    retain_runs = env_int("ABC_ARTIFACT_RETAIN_RUNS", 20, minimum=1)
    max_age_days = env_int("ABC_ARTIFACT_MAX_AGE_DAYS", 30, minimum=1)
    now = time.time()
    max_age_seconds = max_age_days * 24 * 60 * 60

    run_dirs = []
    for name in os.listdir(DEBUG_ROOT):
        path = os.path.join(DEBUG_ROOT, name)
        if not os.path.isdir(path):
            continue
        if os.path.abspath(path) == os.path.abspath(DEBUG_RUN_DIR):
            continue
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        run_dirs.append((mtime, path))

    run_dirs.sort(reverse=True)
    remove_paths = []
    for index, (mtime, path) in enumerate(run_dirs):
        too_many = index >= retain_runs
        too_old = now - mtime > max_age_seconds
        if too_many or too_old:
            remove_paths.append(path)

    removed = 0
    for path in remove_paths:
        if not _path_inside(path, DEBUG_ROOT):
            log.warning("[清理] 跳过异常路径（不在 DEBUG_ROOT 内）: %s", path)
            continue
        try:
            shutil.rmtree(path)
            removed += 1
        except OSError as exc:
            log.warning("[清理] 删除旧运行目录失败: %s (%s)", path, type(exc).__name__)
    if removed:
        log.info(
            "[清理] 已删除 %d 个旧运行目录（保留最近 %d 个，最长 %d 天）",
            removed,
            retain_runs,
            max_age_days,
        )
    else:
        log.info(
            "[清理] 无需删除旧运行目录（保留最近 %d 个，最长 %d 天）",
            retain_runs,
            max_age_days,
        )


__all__ = [name for name in globals() if not name.startswith("__")]
