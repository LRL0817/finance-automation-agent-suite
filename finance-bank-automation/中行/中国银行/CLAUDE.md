# CLAUDE.md

This repository is now maintained primarily through `AGENTS.md` and `SKILL.md`.

Key facts:

- `open_boc.py` is only a thin Windows entry point.
- Implementation lives under `boc_automation/`.
- Main orchestration is `boc_automation/app.py`.
- Transfer form logic is `boc_automation/transfer.py`.
- Native certificate, U-shield PIN, public BOC portal cleanup, and unplug notices are in `boc_automation/windows.py`.
- Long-term artifact cleanup is in `boc_automation/artifacts.py`.
- Canonical log: `logs/boc.log`.
- Per-run logs and screenshots: `debug_runs/<run_id>/`.
- Batch logs: `logs/batches/`.

Hard rule: never add automatic payment/confirmation/authorization. Prefill is gated by `BOC_ENABLE_TRANSFER_FILL=1`; first-step order submission is gated by `BOC_ENABLE_ORDER_SUBMIT=1` and must stop after clicking the bottom `提交`; U-shield PIN auto-entry is gated by `BOC_AUTO_USHIELD_PIN=1`.

Read `AGENTS.md` first for current architecture and safety constraints, then `SKILL.md` for the full local operating playbook.
