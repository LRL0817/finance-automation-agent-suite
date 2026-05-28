# -*- coding: utf-8 -*-
"""Run bank workflows from the per-bank queues under bank_batches/."""
from __future__ import annotations

from bank_route import run_batch_cli


if __name__ == "__main__":
    raise SystemExit(run_batch_cli())
