# -*- coding: utf-8 -*-
"""
农行企业网银 - 单笔转账自动化入口。

实现已拆分到 abc_single_transfer 包；保留这个文件是为了兼容原来的启动命令：
    python skills/单笔转账/open_browser.py
"""
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from abc_single_transfer.runner import main


if __name__ == "__main__":
    sys.exit(main())
