# -*- coding: utf-8 -*-
"""Shared paths for the transfer-order skill."""
import os
import sys

ZHIDAN_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if ZHIDAN_ROOT not in sys.path:
    sys.path.insert(0, ZHIDAN_ROOT)

FINANCE_ROOT = os.path.dirname(ZHIDAN_ROOT)
SCREENSHOT_DIR = os.path.join(ZHIDAN_ROOT, "screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)
