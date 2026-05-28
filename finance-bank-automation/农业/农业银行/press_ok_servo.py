# -*- coding: utf-8 -*-
from __future__ import annotations

"""Manual test entry for the ABC physical OK servo."""

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SKILL_DIR = ROOT / "skills" / "单笔转账"
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from abc_single_transfer.runtime import load_environment, log
from abc_single_transfer.servo_ok import trigger_ok_servo


def main() -> int:
    parser = argparse.ArgumentParser(description="Trigger the ABC physical OK servo once.")
    parser.add_argument(
        "--press",
        action="store_true",
        help="actually send the configured lock-key hotkey once",
    )
    parser.add_argument(
        "--hotkey",
        choices=("capslock", "numlock", "scrolllock"),
        help="override ABC_OK_SERVO_HOTKEY for this test run",
    )
    args = parser.parse_args()

    load_environment()
    if args.hotkey:
        os.environ["ABC_OK_SERVO_HOTKEY"] = args.hotkey

    if not args.press:
        print("未执行舵机动作。确认设备和机械位置后运行：python press_ok_servo.py --press")
        return 0

    os.environ["ABC_OK_SERVO_ENABLE"] = "true"
    ok = trigger_ok_servo("standalone_test")
    print("OK servo trigger:", "sent" if ok else "failed")
    if not ok:
        log.warning("[舵机OK] 独立测试未发送成功，请检查热键/设备连接/前台权限")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
