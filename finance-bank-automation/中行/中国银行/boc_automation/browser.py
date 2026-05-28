import json
import os
import shutil
import tempfile
from pathlib import Path

from .config import BOC_CHROME_PROFILE, BOC_EXTENSION_IDS, BOC_EXTENSION_NAME

def _find_edge_executable() -> str | None:
    candidates = (
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    )
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def _prepare_chrome_extensions() -> tuple[str, str]:
    """Create a temp profile and copy only the BOC certificate extension."""
    local_appdata = os.environ.get("LOCALAPPDATA")
    if not local_appdata:
        raise SystemExit("[终止] 未找到 LOCALAPPDATA 环境变量")
    extension_roots = (
        (
            "Chrome",
            Path(local_appdata)
            / "Google" / "Chrome" / "User Data" / BOC_CHROME_PROFILE / "Extensions",
        ),
        (
            "Edge",
            Path(local_appdata)
            / "Microsoft" / "Edge" / "User Data" / BOC_CHROME_PROFILE / "Extensions",
        ),
    )

    source_ext_dir = None
    source_browser = None
    searched = []
    for browser_name, ext_root in extension_roots:
        searched.append(str(ext_root))
        if not ext_root.is_dir():
            continue
        for ext_id in BOC_EXTENSION_IDS:
            candidate = ext_root / ext_id
            if candidate.is_dir():
                source_ext_dir = candidate
                source_browser = browser_name
                break
        if source_ext_dir is not None:
            break

    if source_ext_dir is None:
        raise SystemExit(
            "[终止] 未找到中行证书扩展。请先在 Chrome 或 Edge 中安装 "
            f"{BOC_EXTENSION_NAME}。已搜索：{'; '.join(searched)}"
        )

    versions = [
        ver for ver in source_ext_dir.iterdir()
        if ver.is_dir() and (ver / "manifest.json").is_file()
    ]
    if not versions:
        raise SystemExit(f"[终止] 中行证书扩展目录缺少 manifest.json：{source_ext_dir}")

    selected = max(versions, key=lambda ver: ver.stat().st_mtime)
    tmp = Path(tempfile.mkdtemp(prefix="boc_playwright_"))
    try:
        dst = tmp / "Default" / "Extensions" / source_ext_dir.name / selected.name
        shutil.copytree(selected, dst)
        manifest = selected / "manifest.json"
        m = json.loads(manifest.read_text(encoding="utf-8"))
        print(
            f"[扩展] {m.get('name', BOC_EXTENSION_NAME)} ({selected.name}, {source_browser}, id={source_ext_dir.name})",
            flush=True,
        )
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    _write_chrome_preferences(tmp)
    return str(tmp), source_browser or "Chrome"


def _write_chrome_preferences(profile_dir: Path) -> None:
    """Persist Chrome prefs in the temporary Playwright profile."""
    default_dir = profile_dir / "Default"
    default_dir.mkdir(parents=True, exist_ok=True)
    prefs_path = default_dir / "Preferences"
    prefs = {
        "browser": {
            "enable_spellchecking": False,
        },
        "intl": {
            "accept_languages": "zh-CN,zh",
        },
        "translate": {
            "enabled": False,
            "blocked_languages": ["en"],
            "site_blacklist": [
                "netc1.igtb.boc.cn",
                "netc2.igtb.boc.cn",
            ],
        },
        "translate_blocked_languages": ["en"],
        "translate_site_blacklist": [
            "netc1.igtb.boc.cn",
            "netc2.igtb.boc.cn",
        ],
    }
    prefs_path.write_text(
        json.dumps(prefs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
