# -*- coding: utf-8 -*-
from __future__ import annotations

"""USB Hub 农行 U 盾与 OK 自动点击器准备工具。"""

from .runtime import *

# ---------------- 主流程 ----------------
def _wait_for_ctrl_c(reason: str) -> None:
    log.info(reason)
    log.info("（按 Ctrl+C 关闭浏览器并退出）")
    try:
        while True:
            safe_sleep(1)
    except KeyboardInterrupt:
        log.info("收到 Ctrl+C，准备退出")


def prepare_abc_usb12_if_enabled() -> bool:
    """
    当前 30 口 Hub 默认使用 29 口农行 K 宝。
    若需要外置 OK 自动点击器，默认同时打开 30 口。

    这里在启动网银浏览器前调用根目录的 open_abc_usb12.py：
      - 不执行 only/all-off，只打开配置的必要端口（默认 29,30）；
      - 清理 U 盾驱动可能自动打开的农行官网首页标签。
    如果切换失败，主流程直接中止，避免未知 U 盾状态导致付方账户跑偏。
    """
    if not env_flag("ABC_USB12_PREPARE", True):
        log.warning("[USB] ABC_USB12_PREPARE=false，跳过农行 U 盾端口准备；请确认当前付方账户来源")
        return True

    helper_path = os.getenv("ABC_USB12_HELPER_PATH", DEFAULT_USB12_HELPER_PATH).strip()
    if not helper_path:
        helper_path = DEFAULT_USB12_HELPER_PATH
    helper_path = os.path.abspath(helper_path)
    if not os.path.exists(helper_path):
        log.error("[USB] 未找到农行 U 盾辅助脚本: %s", helper_path)
        return False

    log.info("[USB] 付方账户固定使用 29 口 U 盾，登录前准备 USB Hub 必要端口: %s", helper_path)
    helper_env = dict(os.environ)
    if "ABC_USB_CLOSE_AUTO_HOME_TAB" not in helper_env:
        helper_env["ABC_USB_CLOSE_AUTO_HOME_TAB"] = "true"
        log.info("[USB] 未显式设置 ABC_USB_CLOSE_AUTO_HOME_TAB，自动关闭 U 盾驱动弹出的农行官网首页")
    try:
        result = subprocess.run(
            [sys.executable, helper_path],
            cwd=ABC_ROOT,
            env=helper_env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        log.error("[USB] 农行 USB 必要端口准备超时，已中止本次网银登录")
        return False
    except OSError as e:
        log.error("[USB] 农行 USB 必要端口准备启动失败（%s）: %s", type(e).__name__, helper_path)
        return False

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if stdout:
        log.info("[USB] 农行 USB 辅助脚本输出:\n%s", stdout)
    if stderr:
        log.info("[USB] 农行 USB 辅助脚本日志:\n%s", stderr)
    if result.returncode != 0:
        log.error("[USB] 农行 USB 必要端口准备失败，退出码=%s，已中止本次网银登录", result.returncode)
        return False

    log.info("[USB] 农行 USB 必要端口已准备完成，后续登录/付方账户均以 29 口 U 盾为准")
    return True


__all__ = [name for name in globals() if not name.startswith("__")]
