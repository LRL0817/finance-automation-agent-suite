# -*- coding: utf-8 -*-
"""USB Hub selection based on the payer in bank_form.json."""
import os
import subprocess
import sys

from zhidan_paths import FINANCE_ROOT

_FORM_PATH = os.path.join(FINANCE_ROOT, "M3直供合同付款数据获取", "bank_form.json")


def _desktop_path(*parts):
    return os.path.join(os.path.expanduser("~"), "Desktop", *parts)


def _first_existing_path(*candidates):
    fallback = None
    for candidate in candidates:
        if not candidate:
            continue
        path = os.path.normpath(os.fspath(candidate))
        if fallback is None:
            fallback = path
        if os.path.exists(path):
            return path
    if fallback is None:
        raise RuntimeError("未配置可用路径候选")
    return fallback


_USB_HUB_CTRL = _first_existing_path(
    os.getenv("ZHIDAN_USB_HUB_CTRL"),
    os.getenv("USB_HUB_CTRL"),
    os.path.join(os.path.dirname(os.path.dirname(FINANCE_ROOT)), "公共", "usbhub", "usbhub", "多口USB控制器软件以及驱动", "hub_ctrl.py"),
    _desktop_path("财务", "公共", "usbhub", "usbhub", "多口USB控制器软件以及驱动", "hub_ctrl.py"),
    _desktop_path("usbhub", "usbhub", "多口USB控制器软件以及驱动", "hub_ctrl.py"),
)
_USB_HUB_COM = os.getenv("USB_HUB_COM", "COM3")
_USB_HUB_PORT_BY_PAYER = {
    # USBHub 物理口映射，只在本流程明确走招行时生效；不要拿它覆盖 M3 主路由。
    "云链跳动": 1,
    "云链跃动": 2,
    "云链悠动": 3,
    "云链智动": 4,
    "云链逸动": 5,
    "云链灵动": 6,
    "云链讯动": 7,
    "云链慧动": 8,
    "云链炫动": 9,
    # 10 口当前是河南讯动/中行 UKey，不属于招行映射。
    "蒙特": 12,
    # 得鲜 001/002 是同一个招行 UKey，差异在制单页付款方账号下拉。
    "得鲜": 16,
    # 来参缘 001/002 是同一个招行 UKey，差异在登录窗口登录名下拉。
    "来参缘": 18,
    "浙江云炫农": 19,
    "云炫农": 19,
}

def _load_bank_form():
    """读取转账表单 JSON；制单和 U 盾选择共用同一份数据。"""
    import json as _json

    try:
        with open(_FORM_PATH, "r", encoding="utf-8") as _f:
            form = _json.load(_f)
        print(f"已加载 bank_form.json: {_FORM_PATH}")
        return form, True
    except Exception as _e:
        print(f"未能加载 {_FORM_PATH}: {_e}，使用内置示例值")
        return {}, False


# 招商银行的支行栏在 U-BANK 单笔转账经办页是禁用的，bank_form.json 里允许留空。
# 其他银行必须显式写出支行/分行/网点全称，否则在校验阶段就 fail-closed，
# 避免进 UI 后用空值撞下拉列表第一条。
_BRANCH_OPTIONAL_BANKS = {"招商银行"}


def _compact(value):
    return "".join(str(value or "").split())


def validate_bank_form(form):
    """对 bank_form.json 做字段级校验。

    fail-closed：任一必填字段缺失或格式异常都返回 (False, errors)。
    调用方应在切 USB Hub、打开 U-BANK、填表前都使用这份校验结果，
    缺字段时直接终止流程，不进入任何 UI 自动化。

    校验规则：
    - 付款单位名称、收方账号、收方户名、开户银行、金额、用途：必填
    - 收方账号：纯数字，长度 8~24（覆盖招行/工行/民生等常见账号位数）
    - 金额：可解析为正数
    - 支行名称：开户银行不在 _BRANCH_OPTIONAL_BANKS 时必填
    """
    errors = []
    if not isinstance(form, dict):
        return False, ["bank_form 不是 dict（JSON 顶层不是对象？）"]

    payer = (form.get("付款单位名称") or form.get("付款单位") or "").strip()
    if not payer:
        errors.append("缺少 付款单位名称")

    payee_acct = (form.get("收方账号") or "").strip()
    if not payee_acct:
        errors.append("缺少 收方账号")
    elif not payee_acct.isdigit():
        errors.append(f"收方账号必须为纯数字: [{payee_acct}]")
    elif not (8 <= len(payee_acct) <= 24):
        errors.append(f"收方账号位数异常({len(payee_acct)}): [{payee_acct}]")

    payee_name = (form.get("收方户名") or "").strip()
    if not payee_name:
        errors.append("缺少 收方户名")

    bank_head = (form.get("开户银行") or "").strip()
    if not bank_head:
        errors.append("缺少 开户银行")

    amount_raw = (form.get("金额") or "").strip() if form.get("金额") is not None else ""
    if not amount_raw:
        errors.append("缺少 金额")
    else:
        try:
            amount_val = float(str(amount_raw).replace(",", ""))
        except (ValueError, TypeError):
            errors.append(f"金额非有效数字: [{amount_raw}]")
        else:
            if amount_val <= 0:
                errors.append(f"金额必须 > 0: [{amount_raw}]")

    purpose = (form.get("用途") or "").strip()
    if not purpose:
        errors.append("缺少 用途")

    branch = (form.get("支行名称") or "").strip()
    bank_head_compact = _compact(bank_head)
    branch_required = bool(bank_head_compact) and bank_head_compact not in _BRANCH_OPTIONAL_BANKS
    if branch_required and not branch:
        errors.append(
            f"开户银行[{bank_head}]要求填写支行，但 支行名称 为空（仅 {sorted(_BRANCH_OPTIONAL_BANKS)} 允许留空）"
        )

    return (len(errors) == 0), errors


def report_bank_form_validation(form):
    """便捷封装：打印校验结果，返回是否通过。"""
    ok, errors = validate_bank_form(form)
    if ok:
        print("bank_form 字段校验通过")
        return True
    print("=" * 60)
    print("[fail-closed] bank_form 字段校验失败，终止执行：")
    for err in errors:
        print(f"  - {err}")
    print("=" * 60)
    return False

def _resolve_usb_hub_port(form):
    force_port = (os.getenv("USB_HUB_FORCE_PORT") or "").strip()
    if force_port:
        # USB_HUB_FORCE_PORT 会绕过付款单位 → U 盾端口的匹配，等于授权脚本拿任意 U 盾
        # 给任意付款单位制单。生产环境绝对不应启用；此处要求显式同意环境变量再放行。
        allow_force = os.getenv("ZHIDAN_ALLOW_FORCE_USB_PORT", "0") == "1"
        if not allow_force:
            print("=" * 60)
            print(f"[fail-closed] 检测到 USB_HUB_FORCE_PORT={force_port}，但未设置 ZHIDAN_ALLOW_FORCE_USB_PORT=1")
            print("[fail-closed] 强制 U 盾端口会绕过付款单位匹配，可能用错 U 盾经办，已拒绝执行")
            print("[fail-closed] 仅在调试 batch 脚本时显式设置 ZHIDAN_ALLOW_FORCE_USB_PORT=1 才允许使用")
            print("=" * 60)
            return None
        try:
            port = int(force_port)
        except ValueError:
            print(f"USB_HUB_FORCE_PORT 配置无效: {force_port}")
            return None
        if not 1 <= port <= 30:
            print(f"USB_HUB_FORCE_PORT 超出范围: {force_port}")
            return None
        print("=" * 60)
        print(f"[警告] 已启用 USB_HUB_FORCE_PORT={port}（ZHIDAN_ALLOW_FORCE_USB_PORT=1）")
        print(f"[警告] 将忽略付款单位匹配，强制切换 USB Hub {port} 口；请确认 U 盾归属正确")
        print("=" * 60)
        return port

    payer_name = (form.get("付款单位名称") or form.get("付款单位") or "").strip()
    if not payer_name:
        print("bank_form.json 缺少付款单位名称，无法选择 U 盾端口")
        return None

    for payer_keyword, port in _USB_HUB_PORT_BY_PAYER.items():
        if payer_keyword in payer_name:
            print(f"付款单位[{payer_name}]匹配[{payer_keyword}]，准备切换 USB Hub {port} 口")
            return port

    print(f"付款单位[{payer_name}]未配置 USB Hub 端口，终止制单以避免用错 U 盾")
    return None

def _switch_usb_hub_for_form(form, form_loaded):
    """U-BANK 启动前按付款单位独占打开对应 U 盾端口。

    bank_form.json 加载失败时默认 fail-closed（return False），调用方应据此终止
    流程。只有显式设置 ZHIDAN_ALLOW_DEMO_MODE=1 时，才允许跳过 USB Hub 切换继续
    运行用于本地 UI 调试 —— 此时不会切换任何 U 盾端口，严禁在该状态下点击「经办」
    执行真实转账提交。
    """
    if not form_loaded:
        if os.getenv("ZHIDAN_ALLOW_DEMO_MODE") == "1":
            print("=" * 60)
            print("[示例模式] ZHIDAN_ALLOW_DEMO_MODE=1：未加载 bank_form.json，跳过 USB Hub 切换")
            print("[示例模式] 仅用于本地 UI 调试，严禁在该状态下点击「经办」执行真实转账提交")
            print("=" * 60)
            return True
        print("未加载到 bank_form.json，默认 fail-closed 终止执行")
        print("如需进入示例 UI 调试（不切换 U 盾），请显式设置环境变量 ZHIDAN_ALLOW_DEMO_MODE=1")
        return False

    port = _resolve_usb_hub_port(form)
    if port is None:
        return False

    if not os.path.exists(_USB_HUB_CTRL):
        print(f"未找到 USB Hub 控制脚本: {_USB_HUB_CTRL}")
        return False

    try:
        proc = subprocess.run(
            [sys.executable, _USB_HUB_CTRL, "only", str(port), "--settle", "2", "--com", _USB_HUB_COM],
            timeout=45,
        )
        print(f"USB Hub {port} 口切换退出码: {proc.returncode}")
        return proc.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"USB Hub {port} 口切换超时")
        return False
    except Exception as e:
        print(f"USB Hub {port} 口切换异常: {e}")
        return False


def _turn_off_usb_hub_ports():
    """U-BANK 关闭后关闭所有 USB Hub 端口，避免 U 盾长时间保持通电。"""
    if not os.path.exists(_USB_HUB_CTRL):
        print(f"未找到 USB Hub 控制脚本，无法关闭 U 盾端口: {_USB_HUB_CTRL}")
        return False

    try:
        proc = subprocess.run(
            [sys.executable, _USB_HUB_CTRL, "all-off", "--com", _USB_HUB_COM],
            timeout=45,
        )
        print(f"USB Hub 全部端口关闭退出码: {proc.returncode}")
        return proc.returncode == 0
    except subprocess.TimeoutExpired:
        print("USB Hub 全部端口关闭超时")
        return False
    except Exception as e:
        print(f"USB Hub 全部端口关闭异常: {e}")
        return False
