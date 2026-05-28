"""Temporary Edge policies used to avoid the native client certificate picker."""

import json
import os
import sys

EDGE_POLICY_KEY = r"Software\Policies\Microsoft\Edge"
AUTO_CERT_KEY = EDGE_POLICY_KEY + r"\AutoSelectCertificateForUrls"
BOC_CERT_POLICY_VALUES = {
    "9010": "https://netc1.igtb.boc.cn",
    "9011": "https://netc2.igtb.boc.cn",
}


def _env_flag_on(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in {"0", "false", "no", "off"}


def _build_cert_filter() -> dict:
    raw = os.environ.get("BOC_EDGE_CERT_POLICY_FILTER", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except Exception as exc:
            print(f"[Edge证书策略] BOC_EDGE_CERT_POLICY_FILTER 不是有效 JSON，改用默认过滤：{type(exc).__name__}", flush=True)

    cert_filter: dict[str, dict[str, str]] = {}
    issuer_cn = os.environ.get("BOC_CERT_ISSUER_CN", "").strip()
    subject_cn = os.environ.get("BOC_CERT_SUBJECT_CN", "").strip()
    if issuer_cn:
        cert_filter.setdefault("ISSUER", {})["CN"] = issuer_cn
    if subject_cn:
        cert_filter.setdefault("SUBJECT", {})["CN"] = subject_cn
    return cert_filter


def ensure_boc_edge_certificate_auto_select_policy() -> None:
    """Install a narrow HKCU Edge policy so BOC client cert selection is automatic."""
    if sys.platform != "win32":
        return
    # 默认关闭：写 HKCU 注册表策略属侵入性操作（会改当前用户的 Edge 证书自动
    # 选择策略并关闭多证书提示），必须由操作人显式 BOC_ENABLE_EDGE_CERT_AUTO_SELECT=1
    # 开启。未开启时不写任何注册表，回退到 Windows 原生证书选择框（人工选证书）。
    if not _env_flag_on("BOC_ENABLE_EDGE_CERT_AUTO_SELECT", "0"):
        print(
            "[Edge证书策略] 默认关闭（未设置 BOC_ENABLE_EDGE_CERT_AUTO_SELECT=1）："
            "不写 HKCU 注册表，使用原生证书选择框兜底",
            flush=True,
        )
        return

    try:
        import winreg
    except Exception as exc:
        print(f"[Edge证书策略] winreg 不可用，继续使用原生弹窗兜底：{type(exc).__name__}", flush=True)
        return

    cert_filter = _build_cert_filter()
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, AUTO_CERT_KEY, 0, winreg.KEY_SET_VALUE) as key:
            for value_name, pattern in BOC_CERT_POLICY_VALUES.items():
                rule = json.dumps(
                    {"pattern": pattern, "filter": cert_filter},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, rule)
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, EDGE_POLICY_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "PromptOnMultipleMatchingCertificates", 0, winreg.REG_DWORD, 0)
        print("[Edge证书策略] 已为中行 netc1/netc2 启用客户端证书自动选择", flush=True)
    except PermissionError:
        print("[Edge证书策略] 写入 HKCU Edge 策略被拒绝，继续使用原生弹窗兜底", flush=True)
    except Exception as exc:
        print(f"[Edge证书策略] 写入失败，继续使用原生弹窗兜底：{type(exc).__name__}", flush=True)
