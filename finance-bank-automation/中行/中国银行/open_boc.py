"""Entry point for the Bank of China corporate e-banking automation."""

import subprocess
import sys
from pathlib import Path

if sys.platform != "win32":
    raise SystemExit("[终止] 本脚本依赖 Windows 原生证书/键盘接口，只支持 Windows。")

sys.dont_write_bytecode = True

from boc_automation.app import main


def _run_global_artifact_cleanup() -> None:
    cleanup = Path(__file__).resolve().parents[2] / "公共" / "maintenance" / "cleanup_after_automation.ps1"
    if not cleanup.is_file():
        return
    try:
        subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(cleanup),
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    finally:
        _run_global_artifact_cleanup()
