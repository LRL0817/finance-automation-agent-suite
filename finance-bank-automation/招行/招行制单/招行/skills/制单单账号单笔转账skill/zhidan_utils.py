# -*- coding: utf-8 -*-
"""Small shared utilities for display logging and screenshots."""
import ctypes
import os
import subprocess
import time
from datetime import datetime

import win32api
import win32gui

from zhidan_paths import SCREENSHOT_DIR as _SCREENSHOT_DIR

TARGET_SCREEN_WIDTH = 1920
TARGET_SCREEN_HEIGHT = 1080
TARGET_SCALE = "100%"

def _enable_dpi_awareness():
    """Use real screen pixels so UIA rectangles and fallback clicks match 100% DPI machines."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware.
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_enable_dpi_awareness()

def _trace_fill_step(stage):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[填表TRACE {ts}] {stage}", flush=True)


def _bring_ubank_to_front_for_screenshot():
    candidates = []

    def _enum(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd) or ""
            if any(token in title for token in ("招商银行企业银行", "U-BANK", "V12")):
                candidates.append(hwnd)
        except Exception:
            pass

    try:
        win32gui.EnumWindows(_enum, None)
        if not candidates:
            return
        hwnd = candidates[0]
        win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.2)
    except Exception:
        pass

def _log_display_profile():
    width = win32api.GetSystemMetrics(0)
    height = win32api.GetSystemMetrics(1)
    print(
        f"当前屏幕: {width}x{height}; 目标适配: "
        f"{TARGET_SCREEN_WIDTH}x{TARGET_SCREEN_HEIGHT}, 缩放 {TARGET_SCALE}"
    )
    if width != TARGET_SCREEN_WIDTH or height != TARGET_SCREEN_HEIGHT:
        print("提示: 坐标兜底按前台窗口比例计算，非目标分辨率也可尝试，但建议新电脑设为 1920x1080/100%")

def _screenshot(label):
    """截取前台窗口截图保存到本地"""
    try:
        _bring_ubank_to_front_for_screenshot()
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        ts = datetime.now().strftime("%H%M%S")
        filename = f"{ts}_{label}.png"
        filepath = os.path.join(_SCREENSHOT_DIR, filename)
        # 使用powershell截图（兼容性最好）
        cmd = f'''
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$bmp.Save("{filepath.replace("\\", "/")}")
$g.Dispose()
$bmp.Dispose()
Write-Output "OK"
'''
        result = subprocess.run(
            ["powershell", "-command", cmd],
            capture_output=True, text=True, encoding="utf-8", timeout=10
        )
        if result.returncode != 0:
            print(f"截图失败 (returncode={result.returncode}): {filename}")
            if result.stdout and result.stdout.strip():
                print(f"  stdout: {result.stdout.strip()}")
            if result.stderr and result.stderr.strip():
                print(f"  stderr: {result.stderr.strip()}")
            return None
        if not os.path.exists(filepath):
            print(f"截图失败：文件未生成 {filepath}")
            if result.stdout and result.stdout.strip():
                print(f"  stdout: {result.stdout.strip()}")
            if result.stderr and result.stderr.strip():
                print(f"  stderr: {result.stderr.strip()}")
            return None
        print(f"已截图: {filename}")
        return filepath
    except Exception as e:
        print(f"截图失败: {e}")
        return None
