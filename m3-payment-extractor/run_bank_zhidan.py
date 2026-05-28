# -*- coding: utf-8 -*-
"""Run one classified M3 payment JSON through the matching bank workflow."""
from __future__ import annotations

from bank_route import run_single_cli


if __name__ == "__main__":
    raise SystemExit(run_single_cli())
