import ctypes
import time

# Native Chrome certificate chooser is outside the page DOM, so Playwright keyboard
# events may not reach it. Use the same Enter strategy as the ICBC script:
# Interception HID keyboard first, SendInput scancode fallback.
_ITC_KB_SLOT = -1
try:
    import interception as _itc  # type: ignore
    from interception.inputs import _g_context as _itc_ctx  # type: ignore

    for _i in range(10):
        try:
            _hwid = _itc_ctx.devices[_i].get_HWID()
        except Exception:
            _hwid = None
        if _hwid and _itc_ctx.is_keyboard(_i) and "HID\\VID_" in _hwid:
            _ITC_KB_SLOT = _i
            break
    if _ITC_KB_SLOT >= 0:
        _itc.set_devices(keyboard=_ITC_KB_SLOT)
        _ITC_OK = True
    else:
        _ITC_OK = False
except Exception:
    _itc = None
    _ITC_OK = False

_USER32 = ctypes.WinDLL("user32", use_last_error=True)
_VK_RETURN = 0x0D
_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_SCANCODE = 0x0008
_KEYEVENTF_EXTENDEDKEY = 0x0001
_MAPVK_VK_TO_VSC = 0


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", _KEYBDINPUT),
        ("mi", _MOUSEINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("u", _INPUT_UNION),
    ]


def _send_scancode(vk: int) -> None:
    scan = _USER32.MapVirtualKeyW(vk, _MAPVK_VK_TO_VSC)
    extended = vk in (0x0D,) and False
    down = _INPUT(type=_INPUT_KEYBOARD)
    down.u.ki = _KEYBDINPUT(
        wVk=0,
        wScan=scan,
        dwFlags=_KEYEVENTF_SCANCODE | (_KEYEVENTF_EXTENDEDKEY if extended else 0),
        time=0,
        dwExtraInfo=None,
    )
    up = _INPUT(type=_INPUT_KEYBOARD)
    up.u.ki = _KEYBDINPUT(
        wVk=0,
        wScan=scan,
        dwFlags=_KEYEVENTF_SCANCODE | _KEYEVENTF_KEYUP
        | (_KEYEVENTF_EXTENDEDKEY if extended else 0),
        time=0,
        dwExtraInfo=None,
    )
    _USER32.SendInput(1, ctypes.byref(down), ctypes.sizeof(_INPUT))
    time.sleep(0.05)
    _USER32.SendInput(1, ctypes.byref(up), ctypes.sizeof(_INPUT))
    time.sleep(0.08)


def _send_enter() -> None:
    if _ITC_OK:
        _itc.press("enter")
        time.sleep(0.08)
        return
    _send_scancode(_VK_RETURN)


def _send_ascii(text: str) -> None:
    """Send text with the native keyboard backend without silently mutating it."""
    if _ITC_OK:
        _itc.write(text, interval=0.05)
        return
    if not text.isascii() or any(not (ch.isdigit() or "a" <= ch <= "z") for ch in text):
        raise RuntimeError(
            "SendInput fallback only supports lowercase ASCII letters and digits safely"
        )
    for ch in text:
        if ch.isalpha():
            vk = ord(ch.upper())
        elif ch.isdigit():
            vk = ord(ch)
        else:
            continue
        _send_scancode(vk)
        time.sleep(0.05)
