import ctypes
import os
import sys
import time
from pathlib import Path

from .config import DEBUG_DIR, DEBUG_PAGE_SCREENSHOT_DIR, DEBUG_SCREENSHOT_DIR, LOG_FILE

_DEBUG_STEP = 0

def _setup_stdout_utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _setup_file_logging(include_console: bool = False) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        original_stdout = sys.stdout
        original_stderr = sys.stderr
        log = LOG_FILE.open("a", encoding="utf-8", buffering=1)
        run_log = (DEBUG_DIR / "run.log").open("a", encoding="utf-8", buffering=1)

        class _Tee:
            def __init__(self, *streams):
                self.streams = streams

            def write(self, data):
                for stream in self.streams:
                    stream.write(data)
                return len(data)

            def flush(self):
                for stream in self.streams:
                    stream.flush()

        stdout_streams = (log, run_log)
        stderr_streams = (log, run_log)
        if include_console:
            stdout_streams = (original_stdout, log, run_log)
            stderr_streams = (original_stderr, log, run_log)
        sys.stdout = _Tee(*stdout_streams)
        sys.stderr = _Tee(*stderr_streams)
        print("\n========== BOC open ==========", flush=True)
        print(f"[调试] 本次运行目录: {DEBUG_DIR}", flush=True)
    except Exception:
        pass


def _debug_enabled() -> bool:
    return os.environ.get("BOC_DEBUG_SCREENSHOTS", "1").strip().lower() not in {"0", "false", "no", "off"}


def _debug_page_enabled() -> bool:
    return os.environ.get("BOC_DEBUG_PAGE_SCREENSHOTS", "1").strip().lower() not in {"0", "false", "no", "off"}


def _safe_artifact_name(label: str) -> str:
    keep = []
    for ch in label:
        keep.append(ch if ch.isalnum() or ch in "-_." else "_")
    return ("".join(keep).strip("._") or "checkpoint")[:90]


def _capture_desktop_with_gdi(path: Path) -> None:
    from ctypes import wintypes

    from PIL import Image  # type: ignore

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.BitBlt.argtypes = [
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_ulong,
    ]
    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    left = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
    top = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
    width = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
    height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
    if width <= 0 or height <= 0:
        raise OSError(f"invalid virtual screen size: {width}x{height}")

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_ulong),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", ctypes.c_ushort),
            ("biBitCount", ctypes.c_ushort),
            ("biCompression", ctypes.c_ulong),
            ("biSizeImage", ctypes.c_ulong),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", ctypes.c_ulong),
            ("biClrImportant", ctypes.c_ulong),
        ]

    class _BITMAPINFO(ctypes.Structure):
        _fields_ = [
            ("bmiHeader", _BITMAPINFOHEADER),
            ("bmiColors", ctypes.c_ulong * 3),
        ]

    hdc = user32.GetDC(None)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, width, height)
    old_obj = gdi32.SelectObject(memdc, bmp)
    try:
        if not gdi32.BitBlt(memdc, 0, 0, width, height, hdc, left, top, 0x00CC0020):
            raise ctypes.WinError(ctypes.get_last_error())
        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        buffer = ctypes.create_string_buffer(width * height * 4)
        rows = gdi32.GetDIBits(memdc, bmp, 0, height, buffer, ctypes.byref(bmi), 0)
        if rows != height:
            raise ctypes.WinError(ctypes.get_last_error())
        image = Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1)
        image.save(path)
    finally:
        if old_obj:
            gdi32.SelectObject(memdc, old_obj)
        if bmp:
            gdi32.DeleteObject(bmp)
        if memdc:
            gdi32.DeleteDC(memdc)
        if hdc:
            user32.ReleaseDC(None, hdc)


def _capture_desktop_screenshot(path: Path) -> None:
    errors = []
    try:
        import pyautogui  # type: ignore

        pyautogui.screenshot(str(path))
        return
    except Exception as exc:
        errors.append(f"pyautogui={type(exc).__name__}: {exc}")

    try:
        from PIL import ImageGrab  # type: ignore

        image = ImageGrab.grab(all_screens=True)
        image.save(path)
        return
    except Exception as exc:
        errors.append(f"ImageGrab={type(exc).__name__}: {exc}")

    try:
        _capture_desktop_with_gdi(path)
        return
    except Exception as exc:
        errors.append(f"GDI={type(exc).__name__}: {exc}")
    raise RuntimeError("; ".join(errors))


def _debug_checkpoint(label: str, page=None) -> None:
    global _DEBUG_STEP
    _DEBUG_STEP += 1
    url = ""
    if page is not None:
        try:
            url = page.url
        except Exception:
            url = "<page unavailable>"
    print(f"[排错] 第 {_DEBUG_STEP:03d} 步：{label}" + (f" url={url}" if url else ""), flush=True)
    if not _debug_enabled():
        return
    try:
        DEBUG_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = DEBUG_SCREENSHOT_DIR / f"{_DEBUG_STEP:03d}_{time.strftime('%H%M%S')}_{_safe_artifact_name(label)}.png"
        _capture_desktop_screenshot(path)
        print(f"[排错] 截图: {path}", flush=True)
    except Exception as exc:
        print(f"[排错] 截图失败: {type(exc).__name__}: {exc}", flush=True)

    if page is not None and _debug_page_enabled():
        try:
            DEBUG_PAGE_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            page_path = DEBUG_PAGE_SCREENSHOT_DIR / f"{_DEBUG_STEP:03d}_{time.strftime('%H%M%S')}_{_safe_artifact_name(label)}.png"
            page.screenshot(path=str(page_path), full_page=False, timeout=5000)
            print(f"[排错] 页面截图: {page_path}", flush=True)
        except Exception as exc:
            print(f"[排错] 页面截图失败: {type(exc).__name__}", flush=True)


def _wait_full_load(page, timeout_ms: int = 60000) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass


def _safe_sleep(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)
