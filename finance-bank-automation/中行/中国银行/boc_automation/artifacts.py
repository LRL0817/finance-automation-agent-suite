"""Long-term artifact hygiene for logs and debug screenshots."""

import shutil
from pathlib import Path

from .config import BATCH_LOG_DIR, BOC_KEEP_BATCH_LOGS, BOC_KEEP_DEBUG_RUNS, DEBUG_DIR, DEBUG_ROOT, LOG_DIR, LOG_FILE, ROOT


def _safe_inside_workspace(path: Path) -> Path:
    resolved = path.resolve()
    workspace = ROOT.resolve()
    if resolved != workspace and workspace not in resolved.parents:
        raise RuntimeError(f"refusing to manage path outside workspace: {resolved}")
    return resolved


def _move_legacy_batch_logs() -> None:
    BATCH_LOG_DIR.mkdir(parents=True, exist_ok=True)
    for path in ROOT.glob("batch_*.log"):
        if not path.is_file():
            continue
        target = BATCH_LOG_DIR / path.name
        if target.exists():
            target = BATCH_LOG_DIR / f"{path.stem}_{path.stat().st_mtime_ns}{path.suffix}"
        try:
            path.replace(target)
            print(f"[整理] 已移动批量日志: {path.name} -> {target}", flush=True)
        except Exception as exc:
            print(f"[整理] 移动批量日志失败 {path.name}: {type(exc).__name__}", flush=True)


def _move_legacy_boc_log() -> None:
    legacy = ROOT / "boc.log"
    if not legacy.is_file():
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    target = LOG_FILE
    if target.exists():
        target = LOG_DIR / f"boc_legacy_{int(legacy.stat().st_mtime)}.log"
    try:
        legacy.replace(target)
        print(f"[整理] 已移动旧总日志: {legacy.name} -> {target}", flush=True)
    except Exception as exc:
        print(f"[整理] 移动旧总日志失败 {legacy.name}: {type(exc).__name__}", flush=True)


def _remove_pycache_dirs() -> None:
    for path in ROOT.rglob("__pycache__"):
        try:
            resolved = _safe_inside_workspace(path)
            shutil.rmtree(resolved, ignore_errors=True)
            print(f"[整理] 已清理 Python 缓存目录: {resolved}", flush=True)
        except Exception as exc:
            print(f"[整理] 清理 Python 缓存失败 {path}: {type(exc).__name__}", flush=True)


def _prune_dirs(root: Path, keep: int, current: Path | None = None) -> None:
    if keep <= 0 or not root.is_dir():
        return
    current_resolved = current.resolve() if current else None
    dirs = [p for p in root.iterdir() if p.is_dir()]
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for path in dirs[keep:]:
        try:
            resolved = _safe_inside_workspace(path)
            if current_resolved and resolved == current_resolved:
                continue
            shutil.rmtree(resolved, ignore_errors=True)
            print(f"[整理] 已删除旧调试目录: {resolved}", flush=True)
        except Exception as exc:
            print(f"[整理] 删除旧调试目录失败 {path}: {type(exc).__name__}", flush=True)


def _prune_files(root: Path, pattern: str, keep: int) -> None:
    if keep <= 0 or not root.is_dir():
        return
    files = [p for p in root.glob(pattern) if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files[keep:]:
        try:
            path = _safe_inside_workspace(path)
            path.unlink(missing_ok=True)
            print(f"[整理] 已删除旧日志: {path}", flush=True)
        except Exception as exc:
            print(f"[整理] 删除旧日志失败 {path}: {type(exc).__name__}", flush=True)


def prepare_artifact_layout() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_ROOT.mkdir(parents=True, exist_ok=True)
    BATCH_LOG_DIR.mkdir(parents=True, exist_ok=True)
    _move_legacy_boc_log()
    _move_legacy_batch_logs()
    _prune_dirs(DEBUG_ROOT, BOC_KEEP_DEBUG_RUNS, current=DEBUG_DIR)
    _prune_files(BATCH_LOG_DIR, "batch_*.log", BOC_KEEP_BATCH_LOGS)
    _remove_pycache_dirs()
