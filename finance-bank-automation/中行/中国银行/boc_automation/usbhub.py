import subprocess
import sys

from .config import (
    BOC_USBHUB_ALL_OFF_ON_FINISH,
    BOC_USBHUB_COM,
    BOC_USBHUB_CTRL_PATH,
    BOC_USBHUB_PORT,
    BOC_USBHUB_POWER_ON_START,
    BOC_USBHUB_SETTLE_SECONDS,
)
from .debug import _debug_checkpoint

def _run_usbhub_command(*args: str, timeout_s: float = 30.0) -> bool:
    if not BOC_USBHUB_CTRL_PATH.is_file():
        print(f"[USBHub] 未找到 hub_ctrl.py：{BOC_USBHUB_CTRL_PATH}", flush=True)
        return False
    if not BOC_USBHUB_COM:
        print("[USBHub] 未配置 BOC_USBHUB_COM，跳过 USBHub 操作", flush=True)
        return False

    cmd = [sys.executable, str(BOC_USBHUB_CTRL_PATH), *args, "--com", BOC_USBHUB_COM]
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except Exception as exc:
        print(f"[USBHub] 命令执行失败 {' '.join(args)}：{type(exc).__name__}: {exc}", flush=True)
        return False

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if stdout:
        print(f"[USBHub] stdout: {stdout}", flush=True)
    if stderr:
        print(f"[USBHub] stderr: {stderr}", flush=True)
    if completed.returncode != 0:
        print(f"[USBHub] 命令返回失败码 {completed.returncode}: {' '.join(args)}", flush=True)
        return False
    return True


def _power_on_boc_usbhub_port() -> bool:
    if not (BOC_USBHUB_POWER_ON_START and BOC_USBHUB_PORT):
        return False
    _debug_checkpoint(f"USBHub准备只开启{BOC_USBHUB_PORT}口")
    ok = _run_usbhub_command(
        "only",
        BOC_USBHUB_PORT,
        "--settle",
        str(int(BOC_USBHUB_SETTLE_SECONDS)),
        timeout_s=max(20.0, BOC_USBHUB_SETTLE_SECONDS + 15.0),
    )
    if ok:
        print(f"[USBHub] 已只开启 {BOC_USBHUB_PORT} 口", flush=True)
        _debug_checkpoint(f"USBHub已只开启{BOC_USBHUB_PORT}口")
    return ok


def _power_off_all_usbhub_ports() -> bool:
    if not BOC_USBHUB_ALL_OFF_ON_FINISH:
        return False
    _debug_checkpoint("USBHub准备关闭所有口")
    ok = _run_usbhub_command("all-off", timeout_s=30.0)
    if ok:
        print("[USBHub] 已关闭所有口", flush=True)
        _debug_checkpoint("USBHub已关闭所有口")
    return ok
