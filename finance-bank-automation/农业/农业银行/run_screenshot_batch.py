# -*- coding: utf-8 -*-
"""Run ABC single-transfer fill checks for the 10 manually read screenshots."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BATCH_PATH = ROOT / "screenshot_transfer_batch.json"
OPEN_BROWSER_PATH = ROOT / "skills" / "单笔转账" / "open_browser.py"


def main() -> int:
    os.environ.setdefault("TRANSFER_BATCH_PATH", str(BATCH_PATH))
    os.environ.setdefault("ABC_USB12_PREPARE", "true")
    os.environ.setdefault("ABC_USB_HUB_PORTS", "29,30")
    os.environ.setdefault("ABC_OK_SERVO_ENABLE", "true")
    os.environ.setdefault("ABC_OK_SERVO_AFTER_KB_PASSWORD", "true")
    os.environ.setdefault("ABC_OK_SERVO_HOTKEY", "scrolllock")
    spec = importlib.util.spec_from_file_location("abc_open_browser", OPEN_BROWSER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载农行脚本: {OPEN_BROWSER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main())
