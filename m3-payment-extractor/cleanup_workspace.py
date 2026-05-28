# -*- coding: utf-8 -*-
"""Keep this workspace tidy by migrating and cleaning known runtime artifacts.

Default mode is a dry run. Use --apply to perform the planned changes.
"""
from __future__ import annotations

import argparse
import shutil
import time
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = ROOT / "runtime"
ARCHIVE_DIR = RUNTIME_DIR / "archive"

LEGACY_FILES = {
    ROOT / "latest.json": RUNTIME_DIR / "current" / "latest.json",
    ROOT / "bank_form.json": RUNTIME_DIR / "current" / "bank_form.json",
}

LEGACY_DIRS = {
    ROOT / "bank_forms": RUNTIME_DIR / "bank_forms",
    ROOT / "bank_batches": RUNTIME_DIR / "bank_batches",
    ROOT / "bank_runs": RUNTIME_DIR / "bank_runs",
}

LEGACY_ARCHIVE_ONLY_DIRS = [
    ROOT / "data",
]

@dataclass
class Action:
    kind: str
    source: Path
    target: Path | None = None


def resolved(path: Path) -> Path:
    return path.resolve(strict=False)


def assert_inside_root(path: Path) -> None:
    root = resolved(ROOT)
    target = resolved(path)
    if target != root and root not in target.parents:
        raise RuntimeError(f"拒绝处理工作区外路径: {target}")


def archive_target(source: Path, stamp: str) -> Path:
    return ARCHIVE_DIR / f"legacy_{stamp}" / source.name


def plan_migration() -> list[Action]:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    actions: list[Action] = []
    for source, target in {**LEGACY_FILES, **LEGACY_DIRS}.items():
        if not source.exists():
            continue
        chosen_target = target if not target.exists() else archive_target(source, stamp)
        actions.append(Action("move", source, chosen_target))
    for source in LEGACY_ARCHIVE_ONLY_DIRS:
        if source.exists():
            actions.append(Action("move", source, archive_target(source, stamp)))
    return actions


def plan_pycache_clean() -> list[Action]:
    actions: list[Action] = []
    for path in ROOT.rglob("__pycache__"):
        if path.is_dir():
            actions.append(Action("remove_dir", path))
    for path in ROOT.rglob("*.pyc"):
        if path.is_file():
            actions.append(Action("remove_file", path))
    return actions


def plan_archive_prune(keep_archives: int) -> list[Action]:
    if keep_archives <= 0 or not ARCHIVE_DIR.exists():
        return []
    archives = sorted(
        [p for p in ARCHIVE_DIR.iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [Action("remove_dir", path) for path in archives[keep_archives:]]


def plan_runtime_prune(keep_screenshot_runs: int, keep_screenshot_days: int) -> list[Action]:
    actions: list[Action] = []
    screenshot_root = RUNTIME_DIR / "screenshots"
    if keep_screenshot_runs >= 0 and screenshot_root.exists():
        runs = sorted(
            [p for p in screenshot_root.iterdir() if p.is_dir()],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        cutoff = time.time() - max(1, keep_screenshot_days) * 86400
        for index, path in enumerate(runs):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0
            if index < keep_screenshot_runs or mtime >= cutoff:
                continue
            actions.append(Action("remove_dir", path))
    return actions


def apply_action(action: Action, apply: bool) -> None:
    assert_inside_root(action.source)
    if action.target is not None:
        assert_inside_root(action.target)

    if action.kind == "move":
        assert action.target is not None
        print(f"MOVE  {action.source} -> {action.target}")
        if apply:
            action.target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(action.source), str(action.target))
    elif action.kind == "remove_dir":
        print(f"RMDIR {action.source}")
        if apply:
            shutil.rmtree(action.source)
    elif action.kind == "remove_file":
        print(f"DEL   {action.source}")
        if apply:
            action.source.unlink(missing_ok=True)
    else:
        raise RuntimeError(f"未知清理动作: {action.kind}")


def main() -> int:
    parser = argparse.ArgumentParser(description="整理 M3 查询工作区的运行产物")
    parser.add_argument("--apply", action="store_true", help="执行清理；不加则只预览")
    parser.add_argument("--no-migrate", action="store_true", help="不迁移旧版根目录产物")
    parser.add_argument("--keep-archives", type=int, default=20, help="保留最近 N 个 runtime/archive 归档")
    parser.add_argument("--keep-screenshot-runs", type=int, default=500, help="至少保留最近 N 个 runtime/screenshots 运行目录")
    parser.add_argument("--keep-screenshot-days", type=int, default=7, help="至少保留最近 N 天 runtime/screenshots 运行目录")
    args = parser.parse_args()

    actions: list[Action] = []
    if not args.no_migrate:
        actions.extend(plan_migration())
    actions.extend(plan_pycache_clean())
    actions.extend(plan_archive_prune(args.keep_archives))
    actions.extend(plan_runtime_prune(args.keep_screenshot_runs, args.keep_screenshot_days))

    if not actions:
        print("工作区已经干净，没有需要处理的运行产物。")
        return 0

    print("执行模式: " + ("apply" if args.apply else "dry-run"))
    for action in actions:
        apply_action(action, args.apply)
    if not args.apply:
        print("这是预览，没有改动文件。确认后可运行: python cleanup_workspace.py --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
