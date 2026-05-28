# -*- coding: utf-8 -*-
from __future__ import annotations

"""USB servo helper for pressing the physical K宝/OK button."""

from .runtime import *


_HOTKEY_ALIASES = {
    "caps": "capslock",
    "caps_lock": "capslock",
    "caps lock": "capslock",
    "num": "numlock",
    "num_lock": "numlock",
    "num lock": "numlock",
    "scroll": "scrolllock",
    "scr": "scrolllock",
    "scr_lock": "scrolllock",
    "scr lock": "scrolllock",
}
_ALLOWED_HOTKEYS = {"capslock", "numlock", "scrolllock"}


def _servo_hotkey() -> str:
    raw = os.getenv("ABC_OK_SERVO_HOTKEY", "scrolllock").strip().lower()
    hotkey = _HOTKEY_ALIASES.get(raw, raw)
    if hotkey not in _ALLOWED_HOTKEYS:
        log.warning(
            "[舵机OK] ABC_OK_SERVO_HOTKEY=%r 不受支持，回退到 scrolllock",
            raw,
        )
        return "scrolllock"
    return hotkey


def trigger_ok_servo(reason: str = "manual") -> bool:
    """
    Trigger the external servo board once by sending its configured lock-key hotkey.

    The board in use is configured by `USB舵机驱动设置 V2.1.exe`; current known setup
    uses CapsLock as the quick-start hotkey. A single key press is intentional:
    pressing again to restore LED state may make the servo press OK a second time.
    """
    if not env_flag("ABC_OK_SERVO_ENABLE", False):
        log.info("[舵机OK] ABC_OK_SERVO_ENABLE=false，跳过舵机 OK 动作（%s）", reason)
        return False

    hotkey = _servo_hotkey()
    settle_s = env_float("ABC_OK_SERVO_SETTLE_S", 1.0)
    old_failsafe = pyautogui.FAILSAFE
    try:
        pyautogui.FAILSAFE = False
        log.warning("[舵机OK] 触发物理 OK：hotkey=%s，reason=%s", hotkey, reason)
        capture_live_screenshot(f"舵机OK触发前_{reason}")
        pyautogui.press(hotkey)
        safe_sleep(settle_s)
        capture_live_screenshot(f"舵机OK触发后_{reason}")
        return True
    except Exception as exc:
        log.error("[舵机OK] 触发失败：%s", type(exc).__name__)
        return False
    finally:
        pyautogui.FAILSAFE = old_failsafe


__all__ = [name for name in globals() if not name.startswith("__")]
