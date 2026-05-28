"""Fill the ten screenshot transfers one by one without submitting."""

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

from cib_open_bank.batch_data import TRANSFERS
from cib_open_bank.housekeeping import (
    archive_existing_batch_screenshots,
    print_results,
    tidy_batch_debug_screenshots,
)
from cib_open_bank.main_flow import run_transfer_flow
from cib_open_bank.runtime import SCREENSHOT_DIR, all_off_usb_hub_ports, set_screenshot_prefix
from cib_open_bank.windows import close_bank_windows, dismiss_ukey_notice


def _parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Archive existing batch_*.png first so all ten transfers run again.",
    )
    parser.add_argument(
        "--keep-debug",
        action="store_true",
        help="Keep intermediate batch debug screenshots in screenshots root.",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    if args.fresh:
        print_results(archive_existing_batch_screenshots(), dry_run=False)

    try:
        for index, transfer in enumerate(TRANSFERS, start=1):
            prefix = f"batch_{index:02d}_"
            top_screenshot = Path(SCREENSHOT_DIR) / f"{prefix}06_填单完成_上半页_关闭前.png"
            lower_screenshot = Path(SCREENSHOT_DIR) / f"{prefix}06_填单完成_下半页_关闭前.png"
            if top_screenshot.exists() and lower_screenshot.exists():
                print(f"跳过第 {index}/{len(TRANSFERS)} 笔，已存在上下页截图: {top_screenshot}, {lower_screenshot}")
                continue

            print("\n" + "#" * 60)
            print(f"开始第 {index}/{len(TRANSFERS)} 笔：{transfer['label']}")
            print("#" * 60)
            try:
                run_transfer_flow(
                    transfer=transfer,
                    fill_only=True,
                    screenshot_prefix=prefix,
                )
            finally:
                set_screenshot_prefix("")
                print("\n[批量收尾] 关闭 U-BANK/网银窗口...")
                close_bank_windows()
                print("\n[批量收尾] all-off 断开所有 U 盾口...")
                all_off_usb_hub_ports()
                dismiss_ukey_notice(wait_seconds=5)
    finally:
        if not args.keep_debug:
            print_results(tidy_batch_debug_screenshots(), dry_run=False)

    print("\n十笔填单截图流程已完成。")


if __name__ == "__main__":
    main()
