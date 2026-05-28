"""Workspace housekeeping for generated banking automation artifacts."""

from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = PROJECT_ROOT / "screenshots"
ARCHIVE_DIR = SCREENSHOT_DIR / "archive"
REVIEWS_DIR = SCREENSHOT_DIR / "reviews"

BATCH_FINAL_SUFFIXES = (
    "06_填单完成_上半页_关闭前.png",
    "06_填单完成_下半页_关闭前.png",
)


@dataclass
class HousekeepingResult:
    action: str
    path: Path
    destination: Path | None = None

    def describe(self) -> str:
        if self.destination:
            return f"{self.action}: {self.path} -> {self.destination}"
        return f"{self.action}: {self.path}"


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _ensure_inside_workspace(path: Path) -> Path:
    resolved = path.resolve()
    root = PROJECT_ROOT.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"Refusing to touch path outside workspace: {resolved}") from exc
    return resolved


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 2
    while True:
        candidate = parent / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _move_file(path: Path, destination_dir: Path, dry_run: bool) -> HousekeepingResult:
    src = _ensure_inside_workspace(path)
    dest_dir = _ensure_inside_workspace(destination_dir)
    dest = _unique_path(dest_dir / src.name)
    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
    return HousekeepingResult("move", src, dest)


def _move_dir(path: Path, destination_dir: Path, dry_run: bool) -> HousekeepingResult:
    src = _ensure_inside_workspace(path)
    dest_dir = _ensure_inside_workspace(destination_dir)
    dest = _unique_path(dest_dir / src.name)
    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
    return HousekeepingResult("move-dir", src, dest)


def _move_dir_to(path: Path, destination: Path, dry_run: bool) -> HousekeepingResult:
    src = _ensure_inside_workspace(path)
    dest = _unique_path(_ensure_inside_workspace(destination))
    if not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
    return HousekeepingResult("move-dir", src, dest)


def _remove_dir(path: Path, dry_run: bool) -> HousekeepingResult:
    target = _ensure_inside_workspace(path)
    if not dry_run:
        shutil.rmtree(target)
    return HousekeepingResult("remove-dir", target)


def _remove_file(path: Path, dry_run: bool) -> HousekeepingResult:
    target = _ensure_inside_workspace(path)
    if not dry_run:
        target.unlink()
    return HousekeepingResult("remove-file", target)


def is_batch_final_screenshot(path: Path) -> bool:
    name = path.name
    return (
        name.startswith("batch_")
        and name.endswith(BATCH_FINAL_SUFFIXES)
        and len(name) >= len("batch_00_") + len(BATCH_FINAL_SUFFIXES[0])
    )


def archive_existing_batch_screenshots(dry_run: bool = False) -> list[HousekeepingResult]:
    """Archive all current batch screenshots before a fresh ten-transfer test."""
    if not SCREENSHOT_DIR.exists():
        return []
    destination = ARCHIVE_DIR / f"batch_{_timestamp()}"
    return [
        _move_file(path, destination, dry_run)
        for path in sorted(SCREENSHOT_DIR.glob("batch_*.png"))
    ]


def tidy_batch_debug_screenshots(dry_run: bool = False) -> list[HousekeepingResult]:
    """Keep only the 20 final batch verification screenshots in screenshots root."""
    if not SCREENSHOT_DIR.exists():
        return []
    destination = ARCHIVE_DIR / f"batch_debug_{_timestamp()}"
    results = []
    for path in sorted(SCREENSHOT_DIR.glob("batch_*.png")):
        if is_batch_final_screenshot(path):
            continue
        results.append(_move_file(path, destination, dry_run))
    return results


def archive_misc_root_screenshots(dry_run: bool = False) -> list[HousekeepingResult]:
    """Move single-run/debug PNGs out of screenshots root."""
    if not SCREENSHOT_DIR.exists():
        return []
    destination = ARCHIVE_DIR / f"single_debug_{_timestamp()}"
    results = []
    for path in sorted(SCREENSHOT_DIR.glob("*.png")):
        if path.name.startswith("batch_"):
            continue
        results.append(_move_file(path, destination, dry_run))
    return results


def organize_legacy_archive_dirs(dry_run: bool = False) -> list[HousekeepingResult]:
    """Move old screenshots/archive_batch_* folders under screenshots/archive."""
    if not SCREENSHOT_DIR.exists():
        return []
    results = []
    for path in sorted(SCREENSHOT_DIR.glob("archive_batch_*")):
        if path.is_dir():
            results.append(_move_dir(path, ARCHIVE_DIR, dry_run))
    return results


def organize_review_contact_sheets(dry_run: bool = False) -> list[HousekeepingResult]:
    source = SCREENSHOT_DIR / "_review_contact_sheets"
    if not source.exists():
        return []
    destination = REVIEWS_DIR / f"contact_sheets_{_timestamp()}"
    return [_move_dir_to(source, destination, dry_run)]


def clean_python_caches(dry_run: bool = False) -> list[HousekeepingResult]:
    results: list[HousekeepingResult] = []
    cache_dirs = [path for path in sorted(PROJECT_ROOT.rglob("__pycache__")) if path.is_dir()]
    for path in cache_dirs:
        results.append(_remove_dir(path, dry_run))
    for path in sorted(PROJECT_ROOT.rglob("*.py[co]")):
        if any(parent in path.parents for parent in cache_dirs):
            continue
        if path.exists():
            results.append(_remove_file(path, dry_run))
    return results


def maintain_workspace(dry_run: bool = False) -> list[HousekeepingResult]:
    results: list[HousekeepingResult] = []
    results.extend(organize_legacy_archive_dirs(dry_run=dry_run))
    results.extend(organize_review_contact_sheets(dry_run=dry_run))
    results.extend(tidy_batch_debug_screenshots(dry_run=dry_run))
    results.extend(archive_misc_root_screenshots(dry_run=dry_run))
    results.extend(clean_python_caches(dry_run=dry_run))
    return results


def print_results(results: list[HousekeepingResult], dry_run: bool) -> None:
    mode = "DRY RUN" if dry_run else "APPLIED"
    print(f"[housekeeping] {mode}: {len(results)} action(s)")
    for item in results:
        print("  " + item.describe())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Keep the CIB automation workspace tidy.")
    parser.add_argument("--apply", action="store_true", help="Apply changes. Default is dry-run.")
    parser.add_argument("--fresh-batch", action="store_true", help="Archive all current batch_*.png files.")
    parser.add_argument("--tidy-batch", action="store_true", help="Archive batch debug screenshots only.")
    args = parser.parse_args(argv)

    dry_run = not args.apply
    if args.fresh_batch:
        results = archive_existing_batch_screenshots(dry_run=dry_run)
    elif args.tidy_batch:
        results = tidy_batch_debug_screenshots(dry_run=dry_run)
    else:
        results = maintain_workspace(dry_run=dry_run)
    print_results(results, dry_run=dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
