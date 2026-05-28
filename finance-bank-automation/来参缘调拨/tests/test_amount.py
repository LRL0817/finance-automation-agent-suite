# -*- coding: utf-8 -*-
"""来参缘调拨 - 纯函数测试。

覆盖：
- parse_balance_text / compute_transferable 的边界
- validate_balance_payload 在 home / workbench 两个分支的严格校验
- workbench 上的「存款 vs 总资产」一致性兜底

只测纯函数。不开 USB Hub、不开 U-BANK、不写 bank_form、不调 制单.py。

跑法：
    python C:\\Users\\30112\\Desktop\\财务\\来参缘调拨\\tests\\test_amount.py
退出码 0 = 全部通过，非 0 = 至少一条失败。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# 测试也覆盖跨项目的全局银行自动化锁；模块位于财务/公共/locks。
_GLOBAL_LOCK_MOD_DIR = Path(__file__).resolve().parents[2] / "公共" / "locks"
if _GLOBAL_LOCK_MOD_DIR.exists():
    sys.path.insert(0, str(_GLOBAL_LOCK_MOD_DIR))

from run_laicanyuan_transfer import (  # noqa: E402
    EXIT_BALANCE_AMBIGUOUS,
    EXIT_BALANCE_NOT_ENOUGH,
    EXIT_BALANCE_READ_FAILED,
    EXIT_LOCK_HELD,
    FailClosed,
    HOME_BALANCE_LABEL,
    RESERVED_AMOUNT_RMB,
    TRANSFER_UNIT_RMB,
    WORKBENCH_BALANCE_LABEL,
    acquire_lock,
    compute_transferable,
    parse_balance_text,
    release_lock,
    resolve_laicanyuan_usb_hub_port,
    validate_balance_payload,
    workbench_deposit_check,
)

try:
    import bank_automation_lock as global_lock  # noqa: E402
except Exception:  # pragma: no cover
    global_lock = None


# 测试不依赖真实公司名；validate_balance_payload 只做字符串严格相等，
# 用一个占位名即可覆盖「相等 / 不相等 / 空」三类分支。真实公司名只在
# gitignored 的 laicanyuan_payee.local.json 里维护。
REQUIRED_COMPANY = "测试占位公司有限公司"


_failures: list[str] = []


def _check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  OK  {label}")
    else:
        msg = f"  FAIL {label}{(': ' + detail) if detail else ''}"
        print(msg)
        _failures.append(label)


def _expect_fail_closed(label: str, expected_code: int | None, fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except FailClosed as exc:
        if expected_code is None or exc.exit_code == expected_code:
            _check(label, True)
        else:
            _check(label, False, f"got exit_code={exc.exit_code} ({exc})")
    else:
        _check(label, False, "未触发 fail-closed")


def _expect_ok(label: str, fn, *args, **kwargs) -> object | None:
    try:
        out = fn(*args, **kwargs)
    except FailClosed as exc:
        _check(label, False, f"unexpected fail-closed: {exc}")
        return None
    _check(label, True)
    return out


# ---------------------------------------------------------------------------
# parse_balance_text
# ---------------------------------------------------------------------------


def test_parse_balance_text() -> None:
    print("[parse_balance_text]")
    _check("strip commas", parse_balance_text("10,123.45") == Decimal("10123.45"))
    _check("strip 全角逗号", parse_balance_text("10，000.00") == Decimal("10000.00"))
    _check("plain integer", parse_balance_text("9876") == Decimal("9876"))
    _check("plain decimal", parse_balance_text("9876.50") == Decimal("9876.50"))
    _check("surrounding spaces", parse_balance_text("  100.00 ") == Decimal("100.00"))

    for bad in ("", " ", "abc", "1,2.3.4", None):
        try:
            parse_balance_text(bad)
        except (InvalidOperation, ValueError):
            _check(f"reject {bad!r}", True)
        else:
            _check(f"reject {bad!r}", False, "未抛异常")


# ---------------------------------------------------------------------------
# compute_transferable 边界
# ---------------------------------------------------------------------------


def test_compute_transferable_fail_closed() -> None:
    print("[compute_transferable fail-closed]")
    for bad in ("9999.99", "0", "0.01", "-1"):
        _expect_fail_closed(
            f"balance={bad} -> fail-closed",
            EXIT_BALANCE_NOT_ENOUGH,
            compute_transferable,
            Decimal(bad),
        )


def test_compute_transferable_floor() -> None:
    print("[compute_transferable floor 到万元]")
    cases = [
        ("10000", "10000"),
        ("10000.00", "10000"),
        ("10000.99", "10000"),
        ("10001", "10000"),
        ("19999.99", "10000"),
        ("20000", "20000"),
        ("50000", "50000"),
        ("74021.98", "70000"),
        ("100000.00", "100000"),
        ("109021.98", "100000"),
    ]
    for balance_text, expected in cases:
        balance = Decimal(balance_text)
        result = _expect_ok(
            f"balance={balance_text} -> transferable={expected}",
            compute_transferable,
            balance,
        )
        if result is None:
            continue
        _check(
            f"  transferable 与期望相等 ({balance_text})",
            result == Decimal(expected),
            f"got {result}",
        )
        _check(
            f"  transferable 无小数 ({balance_text})",
            "." not in str(result),
            f"got {result}",
        )


def test_compute_transferable_below_unit() -> None:
    print("[compute_transferable 不足万元不转]")
    _expect_fail_closed(
        "balance=9999.99 -> fail-closed",
        EXIT_BALANCE_NOT_ENOUGH,
        compute_transferable,
        Decimal("9999.99"),
    )


def test_transfer_unit_constant() -> None:
    print("[万元取整单位常量]")
    _check("TRANSFER_UNIT_RMB == 10000", TRANSFER_UNIT_RMB == Decimal("10000"))
    _check("RESERVED_AMOUNT_RMB 兼容旧名 == 10000", RESERVED_AMOUNT_RMB == Decimal("10000"))


def test_resolve_laicanyuan_usb_hub_port() -> None:
    print("[resolve_laicanyuan_usb_hub_port]")
    keys = ("LAICANYUAN_CMB_USB_HUB_PORT", "LAICANYUAN_USB_HUB_PORT")
    saved = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ.pop(key, None)
        _check("missing env uses fixed port 18", resolve_laicanyuan_usb_hub_port() == "18")

        os.environ["LAICANYUAN_CMB_USB_HUB_PORT"] = "10"
        _expect_fail_closed(
            "port 10 is BOC/河南讯动 -> fail-closed",
            21,
            resolve_laicanyuan_usb_hub_port,
        )

        for blocked_port in ("11", "12", "13", "14", "15", "16", "17", "19", "29", "30"):
            os.environ["LAICANYUAN_CMB_USB_HUB_PORT"] = blocked_port
            _expect_fail_closed(
                f"port {blocked_port} is not 来参缘 CMB UKey -> fail-closed",
                21,
                resolve_laicanyuan_usb_hub_port,
            )

        os.environ["LAICANYUAN_CMB_USB_HUB_PORT"] = "abc"
        _expect_fail_closed(
            "non numeric port -> fail-closed",
            21,
            resolve_laicanyuan_usb_hub_port,
        )

        os.environ["LAICANYUAN_CMB_USB_HUB_PORT"] = "31"
        _expect_fail_closed(
            "out of range port -> fail-closed",
            21,
            resolve_laicanyuan_usb_hub_port,
        )

        os.environ["LAICANYUAN_CMB_USB_HUB_PORT"] = "18"
        _check("explicit port 18 accepted", resolve_laicanyuan_usb_hub_port() == "18")

        os.environ.pop("LAICANYUAN_CMB_USB_HUB_PORT", None)
        os.environ["LAICANYUAN_USB_HUB_PORT"] = "18"
        _check("legacy local env accepted only for 18", resolve_laicanyuan_usb_hub_port() == "18")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# validate_balance_payload - home / workbench 分支
# ---------------------------------------------------------------------------


def _make_home_payload(
    *,
    balance_decimal="50000.00",
    balance="50,000.00",
    account_count="1",
    company=REQUIRED_COMPANY,
    label=HOME_BALANCE_LABEL,
    status="OK",
    all_texts=None,
) -> dict:
    return {
        "result": {
            "status": status,
            "page_type": "home",
            "company": company,
            "balance_label": label,
            "balance": balance,
            "balance_decimal": balance_decimal,
            "account_count": account_count,
            "attempt": "首页",
        },
        "all_texts": all_texts or [],
    }


def _make_workbench_payload(
    *,
    balance_decimal="74021.98",
    balance="74,021.98",
    company=REQUIRED_COMPANY,
    label=WORKBENCH_BALANCE_LABEL,
    status="OK",
    all_texts=None,
    label_control=None,
    balance_control=None,
) -> dict:
    return {
        "result": {
            "status": status,
            "page_type": "workbench",
            "company": company,
            "balance_label": label,
            "balance": balance,
            "balance_decimal": balance_decimal,
            "attempt": "工作台",
            "label_control": label_control,
            "balance_control": balance_control,
        },
        "all_texts": all_texts or [],
    }


def _deposit_texts(deposit_text: str) -> list[dict]:
    """旧布局（金额在标签下方）的最小 all_texts，用于回归 below-label 路径。"""
    return [
        {"text": "存款(元)", "type": "Text", "class": "", "rect": [100, 300, 200, 320]},
        {"text": deposit_text, "type": "Text", "class": "", "rect": [100, 340, 200, 360]},
        # 噪声：理财 同卡片金额为 0
        {"text": "理财(元)", "type": "Text", "class": "", "rect": [400, 300, 500, 320]},
        {"text": "0.00", "type": "Text", "class": "", "rect": [400, 340, 500, 360]},
    ]


# 实测来参缘 002 工作台几何（用占位金额；脱敏不含真实账号/户名/支行）。
# 来源：runs/laicanyuan_20260520_171719/query_balance.json 同步过来的 rect。
_WORKBENCH_TOTAL_LABEL_RECT = [244, 640, 352, 656]
_WORKBENCH_TOTAL_AMOUNT_RECT = [244, 661, 322, 679]
_WORKBENCH_DEPOSIT_AMOUNT_RECT = [245, 712, 306, 726]
_WORKBENCH_DEPOSIT_LABEL_RECT = [245, 734, 269, 748]
_WORKBENCH_LICAI_AMOUNT_RECT = [424, 712, 452, 726]
_WORKBENCH_LICAI_LABEL_RECT = [424, 734, 449, 748]
_WORKBENCH_PIAOJU_AMOUNT_RECT = [604, 712, 631, 726]
_WORKBENCH_PIAOJU_LABEL_RECT = [604, 734, 629, 748]


def _real_workbench_texts(
    *,
    total="74,021.98",
    deposit="74,021.98",
    licai="0.00",
    piaoju="0.00",
    extra_deposit_label_rect: list[int] | None = None,
    extra_deposit_amount_rect: list[int] | None = None,
    extra_deposit_amount_text: str | None = None,
) -> list[dict]:
    """构造一份与本次实测几何一致的 all_texts（金额在 存款 标签上方的卡片布局）。

    可选 `extra_*` 参数用于构造"另一张卡片上也有 存款 label / 金额"的歧义场景。
    """
    items: list[dict] = [
        {"text": "人民币总资产(元)", "rect": list(_WORKBENCH_TOTAL_LABEL_RECT)},
        {"text": total, "rect": list(_WORKBENCH_TOTAL_AMOUNT_RECT)},
        {"text": deposit, "rect": list(_WORKBENCH_DEPOSIT_AMOUNT_RECT)},
        {"text": licai, "rect": list(_WORKBENCH_LICAI_AMOUNT_RECT)},
        {"text": piaoju, "rect": list(_WORKBENCH_PIAOJU_AMOUNT_RECT)},
        {"text": "存款", "rect": list(_WORKBENCH_DEPOSIT_LABEL_RECT)},
        {"text": "理财", "rect": list(_WORKBENCH_LICAI_LABEL_RECT)},
        {"text": "票据", "rect": list(_WORKBENCH_PIAOJU_LABEL_RECT)},
    ]
    if extra_deposit_label_rect is not None:
        items.append({"text": "存款", "rect": list(extra_deposit_label_rect)})
    if extra_deposit_amount_rect is not None and extra_deposit_amount_text is not None:
        items.append({"text": extra_deposit_amount_text, "rect": list(extra_deposit_amount_rect)})
    return items


def _real_workbench_payload(**texts_kwargs) -> dict:
    return _make_workbench_payload(
        balance_decimal="74021.98",
        balance="74,021.98",
        all_texts=_real_workbench_texts(**texts_kwargs),
        label_control={"rect": list(_WORKBENCH_TOTAL_LABEL_RECT)},
        balance_control={"rect": list(_WORKBENCH_TOTAL_AMOUNT_RECT)},
    )


def test_validate_home_ok() -> None:
    print("[validate_balance_payload home]")
    payload = _make_home_payload(balance_decimal="50000.00", balance="50,000.00")
    audit: list[str] = []
    out = _expect_ok(
        "home + account_count=1 + 白名单 label -> 通过",
        validate_balance_payload,
        payload,
        required_company=REQUIRED_COMPANY,
        audit_lines=audit,
    )
    _check("  返回 Decimal('50000.00')", out == Decimal("50000.00"), f"got {out}")
    _check(
        "  audit 中包含 home account_count=1 通过",
        any("home + account_count=1 通过" in line for line in audit),
    )


def test_validate_home_account_count_not_one() -> None:
    print("[validate_balance_payload home account_count!=1]")
    for bad in ("2", "0", " ", "X"):
        _expect_fail_closed(
            f"home account_count=[{bad}] fail-closed",
            EXIT_BALANCE_AMBIGUOUS,
            validate_balance_payload,
            _make_home_payload(account_count=bad),
            required_company=REQUIRED_COMPANY,
        )
    _expect_fail_closed(
        "home account_count=None fail-closed",
        EXIT_BALANCE_AMBIGUOUS,
        validate_balance_payload,
        _make_home_payload(account_count=None),
        required_company=REQUIRED_COMPANY,
    )


def test_validate_workbench_ok_below_label_layout() -> None:
    print("[validate_balance_payload workbench 旧布局：金额在标签下方]")
    payload = _make_workbench_payload(
        balance_decimal="74021.98",
        balance="74,021.98",
        all_texts=_deposit_texts("74,021.98"),
    )
    audit: list[str] = []
    out = _expect_ok(
        "存款金额在标签下方 + 存款==总资产 -> 通过",
        validate_balance_payload,
        payload,
        required_company=REQUIRED_COMPANY,
        audit_lines=audit,
    )
    _check("  返回 Decimal('74021.98')", out == Decimal("74021.98"), f"got {out}")
    _check(
        "  audit 写明定位来源=below-label",
        any("below-label" in line for line in audit),
    )
    _check(
        "  audit 写明 存款 == 人民币总资产",
        any("存款=74021.98 == 人民币总资产=74021.98" in line for line in audit),
    )


def test_validate_workbench_ok_above_label_layout_real_geometry() -> None:
    """复刻 runs/laicanyuan_20260520_171719/query_balance.json 的几何 ——
    存款金额在 存款 标签上方，理财 / 票据 同卡片金额均为 0。"""
    print("[validate_balance_payload workbench 新布局：金额在标签上方（实测几何）]")
    payload = _real_workbench_payload()
    audit: list[str] = []
    out = _expect_ok(
        "above-label 布局 + 存款==总资产 + 理财/票据=0 -> 通过",
        validate_balance_payload,
        payload,
        required_company=REQUIRED_COMPANY,
        audit_lines=audit,
    )
    _check("  返回 Decimal('74021.98')", out == Decimal("74021.98"), f"got {out}")
    _check(
        "  audit 写明定位来源=above-label",
        any("above-label" in line for line in audit),
    )
    _check(
        "  audit 写明 存款 == 总资产",
        any("存款=74021.98 == 人民币总资产=74021.98" in line for line in audit),
    )
    _check(
        "  audit 写明 理财/票据 非现金资产为 0 通过",
        any("理财 / 票据" in line and ("均为 0" in line or "通过" in line) for line in audit),
    )
    _check(
        "  audit 写明 workbench 锚点 (label_control / balance_control rect)",
        any("workbench 锚点" in line and "balance_control.rect" in line for line in audit),
    )


def test_validate_workbench_fail_closed_without_deposit_label() -> None:
    print("[validate_balance_payload workbench + 存款标签缺失 -> fail-closed]")
    payload = _make_workbench_payload(all_texts=[])
    _expect_fail_closed(
        "workbench + 找不到 存款 标签 -> fail-closed",
        EXIT_BALANCE_AMBIGUOUS,
        validate_balance_payload,
        payload,
        required_company=REQUIRED_COMPANY,
    )

    # 还要覆盖：标签在，但下方没有可解析金额（被噪声替换）
    only_label_texts = [
        {"text": "存款(元)", "type": "Text", "class": "", "rect": [100, 300, 200, 320]},
        # 这条不是合法 MONEY_RE（只有 1 位小数）
        {"text": "abc 1.2", "type": "Text", "class": "", "rect": [100, 340, 200, 360]},
    ]
    _expect_fail_closed(
        "workbench + 存款标签在但金额无法解析 -> fail-closed",
        EXIT_BALANCE_AMBIGUOUS,
        validate_balance_payload,
        _make_workbench_payload(all_texts=only_label_texts),
        required_company=REQUIRED_COMPANY,
    )


def test_validate_workbench_deposit_mismatch() -> None:
    print("[validate_balance_payload workbench + 存款 != 总资产]")
    # 旧布局
    payload_old = _make_workbench_payload(
        balance_decimal="74021.98",
        balance="74,021.98",
        all_texts=_deposit_texts("70,000.00"),
    )
    _expect_fail_closed(
        "below-label 布局：存款 != 总资产 -> fail-closed",
        EXIT_BALANCE_AMBIGUOUS,
        validate_balance_payload,
        payload_old,
        required_company=REQUIRED_COMPANY,
    )
    # 新布局
    payload_new = _real_workbench_payload(total="74,021.98", deposit="70,000.00")
    _expect_fail_closed(
        "above-label 布局：存款 != 总资产 -> fail-closed",
        EXIT_BALANCE_AMBIGUOUS,
        validate_balance_payload,
        payload_new,
        required_company=REQUIRED_COMPANY,
    )


def test_validate_workbench_non_cash_assets_nonzero() -> None:
    print("[validate_balance_payload workbench + 理财/票据 非零 -> fail-closed]")
    # 理财 = 1000.00（虽然这种数学上意味着 存款 应该 != 总资产，
    # 但我们的几何识别不依赖数学，先单独验证非现金资产拦截路径）
    payload_licai = _real_workbench_payload(deposit="74,021.98", licai="1,000.00")
    _expect_fail_closed(
        "理财 != 0 -> fail-closed（即使存款==总资产）",
        EXIT_BALANCE_AMBIGUOUS,
        validate_balance_payload,
        payload_licai,
        required_company=REQUIRED_COMPANY,
    )
    # 票据 = 500.00
    payload_piaoju = _real_workbench_payload(deposit="74,021.98", piaoju="500.00")
    _expect_fail_closed(
        "票据 != 0 -> fail-closed",
        EXIT_BALANCE_AMBIGUOUS,
        validate_balance_payload,
        payload_piaoju,
        required_company=REQUIRED_COMPANY,
    )


def test_validate_workbench_ambiguous_deposit_amounts() -> None:
    """同一张资产卡片的存款标签上方有 74021.98，下方又被布局变更塞了一个 70000.00，
    两个不同金额都在同卡片几何范围内 → 不能唯一判断 → fail-closed。"""
    print("[validate_balance_payload workbench + 同卡片多金额候选 -> fail-closed]")
    # 在 存款 label (rect y=[734,748]) 下方 ~10 px 处补一个金额 [245, 758, 306, 772]
    payload = _real_workbench_payload(
        extra_deposit_amount_rect=[245, 758, 306, 772],
        extra_deposit_amount_text="70,000.00",
    )
    _expect_fail_closed(
        "同卡片几何内多个不同金额 -> fail-closed (ambiguous)",
        EXIT_BALANCE_AMBIGUOUS,
        validate_balance_payload,
        payload,
        required_company=REQUIRED_COMPANY,
    )


def test_validate_workbench_main_card_picked_when_multiple_deposit_labels() -> None:
    """多张资产卡片各自有自己的 存款 label：选离 balance_label 最近那张。"""
    print("[validate_balance_payload workbench + 多个存款 label，按距离选主卡片]")
    # 实测里在 y=763 也有一行 存款 / 理财 / 票据 子标签（不带金额），geometric 距离更远
    payload = _real_workbench_payload(
        extra_deposit_label_rect=[621, 763, 649, 779],
    )
    audit: list[str] = []
    out = _expect_ok(
        "多 存款 label -> 选主卡片 above-label 金额 -> 通过",
        validate_balance_payload,
        payload,
        required_company=REQUIRED_COMPANY,
        audit_lines=audit,
    )
    _check("  返回 Decimal('74021.98')", out == Decimal("74021.98"), f"got {out}")
    _check("  audit 写明 above-label", any("above-label" in line for line in audit))


def test_validate_workbench_company_mismatch() -> None:
    print("[validate_balance_payload workbench + company 不一致]")
    payload = _make_workbench_payload(company="某 测试 公司")
    _expect_fail_closed(
        "workbench company 不一致 fail-closed",
        EXIT_BALANCE_AMBIGUOUS,
        validate_balance_payload,
        payload,
        required_company=REQUIRED_COMPANY,
    )


def test_validate_workbench_company_empty() -> None:
    print("[validate_balance_payload workbench + company 为空]")
    payload = _make_workbench_payload(company="")
    _expect_fail_closed(
        "workbench company 为空 fail-closed",
        EXIT_BALANCE_AMBIGUOUS,
        validate_balance_payload,
        payload,
        required_company=REQUIRED_COMPANY,
    )


def test_validate_label_not_whitelisted() -> None:
    print("[validate_balance_payload 余额 label 非白名单]")
    payload = _make_workbench_payload(label="人民币某某未知标签(元)")
    _expect_fail_closed(
        "workbench label 非白名单 fail-closed",
        EXIT_BALANCE_AMBIGUOUS,
        validate_balance_payload,
        payload,
        required_company=REQUIRED_COMPANY,
    )

    # workbench 上拿到了 home 的 label 也算非配对，应 fail-closed
    payload2 = _make_workbench_payload(label=HOME_BALANCE_LABEL)
    _expect_fail_closed(
        "workbench 但 label 是 home 的 fail-closed",
        EXIT_BALANCE_AMBIGUOUS,
        validate_balance_payload,
        payload2,
        required_company=REQUIRED_COMPANY,
    )

    # home 上拿到了 workbench 的 label 也 fail-closed
    payload3 = _make_home_payload(label=WORKBENCH_BALANCE_LABEL)
    _expect_fail_closed(
        "home 但 label 是 workbench 的 fail-closed",
        EXIT_BALANCE_AMBIGUOUS,
        validate_balance_payload,
        payload3,
        required_company=REQUIRED_COMPANY,
    )


def test_validate_balance_decimal_missing_or_illegal() -> None:
    print("[validate_balance_payload balance_decimal 缺失/非法]")
    for bad in (None, "", "abc", "1.2.3"):
        _expect_fail_closed(
            f"workbench balance_decimal=[{bad}] fail-closed",
            EXIT_BALANCE_AMBIGUOUS,
            validate_balance_payload,
            _make_workbench_payload(balance_decimal=bad),
            required_company=REQUIRED_COMPANY,
        )


def test_validate_status_not_ok() -> None:
    print("[validate_balance_payload status 非 OK]")
    for bad_status in ("NOT_FOUND", "ERROR", "", None):
        _expect_fail_closed(
            f"status={bad_status} fail-closed",
            EXIT_BALANCE_READ_FAILED,
            validate_balance_payload,
            _make_workbench_payload(status=bad_status),
            required_company=REQUIRED_COMPANY,
        )


def test_validate_required_company_blank() -> None:
    print("[validate_balance_payload required_company 为空]")
    _expect_fail_closed(
        "required_company 为空 fail-closed",
        EXIT_BALANCE_AMBIGUOUS,
        validate_balance_payload,
        _make_workbench_payload(),
        required_company="   ",
    )


def test_workbench_deposit_check_unit() -> None:
    print("[workbench_deposit_check 单元]")
    balance = Decimal("100.00")
    # match (旧布局，below-label)
    status, deposit, source = workbench_deposit_check(_deposit_texts("100.00"), balance)
    _check(
        "存款=100.00 == 总资产=100.00（below-label）",
        status == "match" and deposit == balance and source == "below-label",
        f"got status={status} deposit={deposit} source={source}",
    )
    # match (新布局，above-label)：用实测几何
    status, deposit, source = workbench_deposit_check(
        _real_workbench_texts(deposit="100.00", total="100.00"),
        balance,
        balance_label_rect=list(_WORKBENCH_TOTAL_LABEL_RECT),
        balance_amount_rect=list(_WORKBENCH_TOTAL_AMOUNT_RECT),
    )
    _check(
        "存款=100.00 == 总资产=100.00（above-label，实测几何）",
        status == "match" and deposit == balance and source == "above-label",
        f"got status={status} deposit={deposit} source={source}",
    )
    # mismatch
    status, deposit, source = workbench_deposit_check(_deposit_texts("90.00"), balance)
    _check(
        "存款=90.00 != 总资产=100.00 -> mismatch",
        status == "mismatch" and deposit == Decimal("90.00") and source == "below-label",
    )
    # not_found - 空文本
    status, deposit, source = workbench_deposit_check([], balance)
    _check(
        "无 all_texts -> not_found",
        status == "not_found" and deposit is None and source is None,
    )
    # ambiguous - 同卡片几何内多个不同金额
    ambig_texts = _real_workbench_texts(
        deposit="100.00",
        total="100.00",
        extra_deposit_amount_rect=[245, 758, 306, 772],
        extra_deposit_amount_text="80.00",
    )
    status, deposit, source = workbench_deposit_check(
        ambig_texts,
        balance,
        balance_label_rect=list(_WORKBENCH_TOTAL_LABEL_RECT),
        balance_amount_rect=list(_WORKBENCH_TOTAL_AMOUNT_RECT),
    )
    _check(
        "多金额候选 -> ambiguous",
        status == "ambiguous" and deposit is None and source is None,
        f"got status={status} deposit={deposit} source={source}",
    )


# ---------------------------------------------------------------------------
# 单实例锁
# ---------------------------------------------------------------------------


def _read_lock(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_acquire_lock_no_existing() -> None:
    print("[acquire_lock 无现存锁]")
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "test.lock"
        action, prev = acquire_lock(
            lock,
            my_pid=99999,
            run_dir=Path(tmp),
            started_at="2026-05-20T16:00:00",
            pid_checker=lambda p: False,
        )
        _check("无锁 -> acquired", action == "acquired" and prev is None)
        _check("锁文件已写入", lock.exists())
        payload = _read_lock(lock)
        _check("锁文件包含 pid", payload.get("pid") == 99999)
        _check("锁文件包含 started_at", payload.get("started_at") == "2026-05-20T16:00:00")
        _check("锁文件包含 run_dir", payload.get("run_dir") == str(Path(tmp)))


def test_acquire_lock_active_pid_blocks() -> None:
    print("[acquire_lock active pid 拒绝重入]")
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "test.lock"
        prev_payload = {"pid": 42, "started_at": "prev", "run_dir": str(Path(tmp))}
        lock.write_text(json.dumps(prev_payload), encoding="utf-8")
        _expect_fail_closed(
            "active pid 锁 -> EXIT_LOCK_HELD",
            EXIT_LOCK_HELD,
            acquire_lock,
            lock,
            my_pid=99999,
            run_dir=Path(tmp),
            started_at="now",
            pid_checker=lambda p: True,  # 模拟 pid=42 仍存活
        )
        _check("活跃锁不被覆盖", _read_lock(lock).get("pid") == 42)


def test_acquire_lock_stale_pid_takes_over() -> None:
    print("[acquire_lock stale pid 安全删除并续接]")
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "test.lock"
        prev_payload = {"pid": 42, "started_at": "prev", "run_dir": str(Path(tmp))}
        lock.write_text(json.dumps(prev_payload), encoding="utf-8")
        action, prev = acquire_lock(
            lock,
            my_pid=99999,
            run_dir=Path(tmp),
            started_at="now",
            pid_checker=lambda p: False,  # pid=42 已不在
        )
        _check("stale -> stole_stale", action == "stole_stale")
        _check("返回原锁 prev_pid=42", isinstance(prev, dict) and prev.get("pid") == 42)
        new_payload = _read_lock(lock)
        _check("新锁 pid 已写入", new_payload.get("pid") == 99999)
        _check("新锁 started_at=now", new_payload.get("started_at") == "now")


def test_acquire_lock_unparseable_lock_treated_active() -> None:
    print("[acquire_lock 锁文件无法解析 -> 当活跃锁拒绝]")
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "test.lock"
        lock.write_text("not a json {{{{", encoding="utf-8")
        _expect_fail_closed(
            "损坏锁 -> EXIT_LOCK_HELD",
            EXIT_LOCK_HELD,
            acquire_lock,
            lock,
            my_pid=99999,
            run_dir=Path(tmp),
            started_at="now",
            pid_checker=lambda p: False,
        )


def test_release_lock_only_when_mine() -> None:
    print("[release_lock 只删自己的锁]")
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "test.lock"
        # 别人的锁
        lock.write_text(json.dumps({"pid": 42}), encoding="utf-8")
        deleted = release_lock(lock, my_pid=99999)
        _check("别人的锁不删", deleted is False and lock.exists())

        # 自己的锁
        lock.write_text(json.dumps({"pid": 99999}), encoding="utf-8")
        deleted = release_lock(lock, my_pid=99999)
        _check("自己的锁已删", deleted is True and not lock.exists())

        # 锁不存在
        deleted = release_lock(lock, my_pid=99999)
        _check("锁不存在 release 返回 False", deleted is False)


def test_lock_lifecycle_after_release_other_can_acquire() -> None:
    print("[lock 生命周期：释放后另一个 pid 可再次 acquire]")
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "test.lock"
        action1, _ = acquire_lock(
            lock, my_pid=1001, run_dir=Path(tmp), started_at="t1", pid_checker=lambda p: False
        )
        _check("pid=1001 acquired", action1 == "acquired")
        deleted = release_lock(lock, my_pid=1001)
        _check("pid=1001 释放", deleted is True)

        action2, _ = acquire_lock(
            lock, my_pid=1002, run_dir=Path(tmp), started_at="t2", pid_checker=lambda p: False
        )
        _check("pid=1002 acquired", action2 == "acquired")
        new_payload = _read_lock(lock)
        _check("锁现在属于 pid=1002", new_payload.get("pid") == 1002)


# ---------------------------------------------------------------------------
# 全局银行自动化锁（跨项目，财务/公共/locks/bank_automation_lock.py）
# ---------------------------------------------------------------------------


def test_global_lock_acquire_no_existing() -> None:
    print("[global_lock acquire 无现存锁]")
    if global_lock is None:
        _check("global_lock 模块已 import", False, "未找到 财务/公共/locks/bank_automation_lock.py")
        return
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "bank_automation.lock"
        action, prev = global_lock.acquire(
            owner=global_lock.OWNER_LAICANYUAN,
            purpose="单元测试",
            my_pid=12345,
            started_at="2026-05-21T10:00:00",
            run_dir=str(Path(tmp)),
            lock_path=lock,
            pid_checker=lambda p: False,
        )
        _check("无锁 -> acquired", action == "acquired" and prev is None)
        _check("锁文件已写入", lock.exists())
        with lock.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        _check("锁文件 owner=laicanyuan_transfer", payload.get("owner") == global_lock.OWNER_LAICANYUAN)
        _check("锁文件 pid=12345", payload.get("pid") == 12345)
        _check("锁文件 purpose=单元测试", payload.get("purpose") == "单元测试")
        _check("锁文件 run_dir 写入", payload.get("run_dir") == str(Path(tmp)))


def test_global_lock_active_pid_blocks() -> None:
    print("[global_lock active pid 拒绝重入]")
    if global_lock is None:
        _check("global_lock 模块已 import", False)
        return
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "bank_automation.lock"
        prev_payload = {
            "owner": global_lock.OWNER_M3_BANK_ROUTE,
            "pid": 42,
            "started_at": "prev",
            "purpose": "M3银行制单路由",
            "run_dir": str(Path(tmp)),
        }
        lock.write_text(json.dumps(prev_payload), encoding="utf-8")
        try:
            global_lock.acquire(
                owner=global_lock.OWNER_LAICANYUAN,
                purpose="来参缘单元测试",
                my_pid=99999,
                started_at="now",
                lock_path=lock,
                pid_checker=lambda p: True,  # pid=42 仍存活
            )
        except global_lock.LockBusy as exc:
            _check("活跃锁 -> LockBusy", True)
            _check("LockBusy.prev.pid=42", exc.prev.get("pid") == 42)
            _check("LockBusy.prev.owner=m3", exc.prev.get("owner") == global_lock.OWNER_M3_BANK_ROUTE)
        else:
            _check("活跃锁 -> LockBusy", False, "未抛 LockBusy")
        # 活跃锁不应被覆盖
        with lock.open("r", encoding="utf-8") as f:
            data = json.load(f)
        _check("活跃锁不被覆盖", data.get("pid") == 42)


def test_global_lock_stale_pid_takeover() -> None:
    print("[global_lock stale pid 接管]")
    if global_lock is None:
        _check("global_lock 模块已 import", False)
        return
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "bank_automation.lock"
        prev_payload = {
            "owner": global_lock.OWNER_M3_BANK_ROUTE,
            "pid": 42,
            "started_at": "prev",
            "purpose": "M3银行制单路由",
            "run_dir": str(Path(tmp)),
        }
        lock.write_text(json.dumps(prev_payload), encoding="utf-8")
        action, prev = global_lock.acquire(
            owner=global_lock.OWNER_LAICANYUAN,
            purpose="单元测试",
            my_pid=99999,
            started_at="now",
            lock_path=lock,
            pid_checker=lambda p: False,  # pid=42 已不在
        )
        _check("stale -> stole_stale", action == "stole_stale")
        _check("stole_stale 返回 prev.pid=42", isinstance(prev, dict) and prev.get("pid") == 42)
        with lock.open("r", encoding="utf-8") as f:
            new_payload = json.load(f)
        _check("新锁 pid=99999", new_payload.get("pid") == 99999)
        _check("新锁 owner=laicanyuan_transfer", new_payload.get("owner") == global_lock.OWNER_LAICANYUAN)


def test_global_lock_unparseable_treated_busy() -> None:
    print("[global_lock 损坏锁文件 -> 当活跃锁拒绝]")
    if global_lock is None:
        _check("global_lock 模块已 import", False)
        return
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "bank_automation.lock"
        lock.write_text("not a json {{{{", encoding="utf-8")
        try:
            global_lock.acquire(
                owner=global_lock.OWNER_LAICANYUAN,
                purpose="单元测试",
                my_pid=99999,
                started_at="now",
                lock_path=lock,
                pid_checker=lambda p: False,
            )
        except global_lock.LockBusy:
            _check("损坏锁 -> LockBusy", True)
        else:
            _check("损坏锁 -> LockBusy", False, "未抛 LockBusy")


def test_global_lock_release_only_own_owner_and_pid() -> None:
    print("[global_lock release 只删 owner+pid 都匹配的锁]")
    if global_lock is None:
        _check("global_lock 模块已 import", False)
        return
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "bank_automation.lock"

        # 1) 别人的 owner，不删。
        lock.write_text(
            json.dumps({"owner": global_lock.OWNER_M3_BANK_ROUTE, "pid": 1001}),
            encoding="utf-8",
        )
        deleted = global_lock.release(
            owner=global_lock.OWNER_LAICANYUAN, my_pid=1001, lock_path=lock
        )
        _check("owner 不匹配 -> 不删", deleted is False and lock.exists())

        # 2) 同 owner，但 pid 不同，不删。
        lock.write_text(
            json.dumps({"owner": global_lock.OWNER_LAICANYUAN, "pid": 2002}),
            encoding="utf-8",
        )
        deleted = global_lock.release(
            owner=global_lock.OWNER_LAICANYUAN, my_pid=9999, lock_path=lock
        )
        _check("pid 不匹配 -> 不删", deleted is False and lock.exists())

        # 3) owner 和 pid 都匹配，删除。
        lock.write_text(
            json.dumps({"owner": global_lock.OWNER_LAICANYUAN, "pid": 3003}),
            encoding="utf-8",
        )
        deleted = global_lock.release(
            owner=global_lock.OWNER_LAICANYUAN, my_pid=3003, lock_path=lock
        )
        _check("owner+pid 匹配 -> 已删", deleted is True and not lock.exists())

        # 4) 锁不存在
        deleted = global_lock.release(
            owner=global_lock.OWNER_LAICANYUAN, my_pid=3003, lock_path=lock
        )
        _check("锁不存在 release -> False", deleted is False)


def test_global_lock_lifecycle_finally_release() -> None:
    print("[global_lock 生命周期：拿锁 -> 释放 -> 另一个 owner 可再拿]")
    if global_lock is None:
        _check("global_lock 模块已 import", False)
        return
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "bank_automation.lock"

        action1, _ = global_lock.acquire(
            owner=global_lock.OWNER_LAICANYUAN,
            purpose="A",
            my_pid=111,
            started_at="t1",
            lock_path=lock,
            pid_checker=lambda p: False,
        )
        _check("owner=laicanyuan acquired", action1 == "acquired")
        rel = global_lock.release(
            owner=global_lock.OWNER_LAICANYUAN, my_pid=111, lock_path=lock
        )
        _check("finally 释放 -> True", rel is True)
        _check("锁文件已删除", not lock.exists())

        action2, _ = global_lock.acquire(
            owner=global_lock.OWNER_M3_BANK_ROUTE,
            purpose="B",
            my_pid=222,
            started_at="t2",
            lock_path=lock,
            pid_checker=lambda p: False,
        )
        _check("owner=m3 acquired", action2 == "acquired")
        with lock.open("r", encoding="utf-8") as f:
            data = json.load(f)
        _check("现在 owner=m3_bank_route", data.get("owner") == global_lock.OWNER_M3_BANK_ROUTE)


def test_global_lock_is_held_by_active_pid() -> None:
    print("[global_lock is_held_by_active_pid 诊断]")
    if global_lock is None:
        _check("global_lock 模块已 import", False)
        return
    with tempfile.TemporaryDirectory() as tmp:
        lock = Path(tmp) / "bank_automation.lock"

        held, prev = global_lock.is_held_by_active_pid(lock_path=lock)
        _check("无锁 -> not held", held is False and prev is None)

        lock.write_text(
            json.dumps({"owner": global_lock.OWNER_LAICANYUAN, "pid": 7}),
            encoding="utf-8",
        )
        held, prev = global_lock.is_held_by_active_pid(
            lock_path=lock, pid_checker=lambda p: True
        )
        _check("active pid -> held", held is True and isinstance(prev, dict))

        held, prev = global_lock.is_held_by_active_pid(
            lock_path=lock, pid_checker=lambda p: False
        )
        _check("stale pid -> not held but prev 返回", held is False and isinstance(prev, dict))


def main() -> int:
    test_parse_balance_text()
    test_compute_transferable_fail_closed()
    test_compute_transferable_floor()
    test_compute_transferable_below_unit()
    test_transfer_unit_constant()
    test_resolve_laicanyuan_usb_hub_port()
    test_validate_home_ok()
    test_validate_home_account_count_not_one()
    test_validate_workbench_ok_below_label_layout()
    test_validate_workbench_ok_above_label_layout_real_geometry()
    test_validate_workbench_fail_closed_without_deposit_label()
    test_validate_workbench_deposit_mismatch()
    test_validate_workbench_non_cash_assets_nonzero()
    test_validate_workbench_ambiguous_deposit_amounts()
    test_validate_workbench_main_card_picked_when_multiple_deposit_labels()
    test_validate_workbench_company_mismatch()
    test_validate_workbench_company_empty()
    test_validate_label_not_whitelisted()
    test_validate_balance_decimal_missing_or_illegal()
    test_validate_status_not_ok()
    test_validate_required_company_blank()
    test_workbench_deposit_check_unit()
    test_acquire_lock_no_existing()
    test_acquire_lock_active_pid_blocks()
    test_acquire_lock_stale_pid_takes_over()
    test_acquire_lock_unparseable_lock_treated_active()
    test_release_lock_only_when_mine()
    test_lock_lifecycle_after_release_other_can_acquire()
    test_global_lock_acquire_no_existing()
    test_global_lock_active_pid_blocks()
    test_global_lock_stale_pid_takeover()
    test_global_lock_unparseable_treated_busy()
    test_global_lock_release_only_own_owner_and_pid()
    test_global_lock_lifecycle_finally_release()
    test_global_lock_is_held_by_active_pid()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} case(s) failed")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
