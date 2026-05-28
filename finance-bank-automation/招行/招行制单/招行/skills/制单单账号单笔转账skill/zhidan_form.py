# -*- coding: utf-8 -*-
"""Top-level transfer-form filling sequence."""
import os
import re
import subprocess
import sys
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from zhidan_bank import (
    _check_skip_branch,
    _click_query_branch_button,
    _input_bank_name,
    _input_branch_name,
    _is_safe_branch_equivalent,
    _try_select_query_branch_result,
    _try_select_branch_dropdown,
)
from zhidan_crash import _check_ubank_crash, _sleep_and_check
from zhidan_input import (
    _click_ratio_and_paste,
    _dismiss_transfer_overlays,
    _read_nearby_input_text,
    find_and_input_text,
)
from zhidan_payee import _close_transient_payee_popup, _select_payee_lookup_candidate
from zhidan_regions import infer_city_region_from_text, infer_province_from_city
from zhidan_submit import _click_submit_button  # Re-exported for the entrypoint.
from zhidan_utils import _screenshot, _trace_fill_step

# 提交前字段校验里识别 placeholder/标签回落值用的关键词。
_PLACEHOLDER_TOKENS = (
    "请输入",
    "请选择",
    "请填写",
    "请录入",
    "无需填写",
    "则无需",
)
_MANUAL_REVIEW_ENV = "ZHIDAN_ALLOW_MANUAL_SCREENSHOT_REVIEW"

_BRANCH_REGION_ROUTE_KINDS = {
    "district_branch": "区县",
    "city_branch": "城市",
    "province_branch": "省份",
    "same_district_branch": "同区县支行",
    "same_city_branch": "同城市支行",
    "same_province_branch": "同省份支行",
}

_DIRECT_MUNICIPALITIES = ("北京市", "上海市", "天津市", "重庆市")
_BRANCH_NAME_MARKERS = ("支行", "分行", "营业部", "分理处", "办事处")
_M3_REGION_ROUTE_IMAGE_KEYS = (
    ("M3合同付款列表截图", "合同付款列表截图"),
    ("M3抓取详情截图", "付款详情截图"),
)
_TRANSFER_FORM_READY_LABELS = ("收方账号", "收方户名", "开户银行", "金额", "用途")


def _collect_visible_control_names(root, limit=500):
    names = []

    def _walk(control):
        if len(names) >= limit:
            return
        try:
            if not control.is_visible():
                return
        except Exception:
            pass
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") else ""
            if name and str(name).strip():
                names.append(str(name).strip())
        except Exception:
            pass
        try:
            for child in control.children():
                _walk(child)
                if len(names) >= limit:
                    break
        except Exception:
            pass

    _walk(root)
    return names


def _transfer_form_ready_score(main_win):
    names = _collect_visible_control_names(main_win)
    compact_names = ["".join(name.split()) for name in names]
    joined = "\n".join(compact_names)
    hits = [label for label in _TRANSFER_FORM_READY_LABELS if any(label in name for name in compact_names)]
    has_business_mode = "业务模式" in joined or "支付自动标准模式" in joined
    has_payer_account = "付方账号" in joined or "默认付方账户" in joined
    return len(set(hits)), bool(has_business_mode and has_payer_account), names[:80]


def _wait_transfer_form_ready(main_win, timeout_seconds=45):
    """Wait until the single-transfer form is actually rendered before typing.

    U-BANK can expose the tab/window before the web form finishes loading. If we
    type into coordinate fallbacks during that spinner state, the keystrokes are
    lost but later screenshots look like a normal empty form. Requiring two
    consecutive ready samples keeps the safety boundary before any field input.
    """
    print(f"等待单笔转账表单字段渲染，最多 {timeout_seconds} 秒...")
    deadline = time.monotonic() + timeout_seconds
    consecutive_ready = 0
    last_sample = []
    while time.monotonic() < deadline:
        _check_ubank_crash("fill: 等待单笔转账表单渲染")
        score, has_context, sample = _transfer_form_ready_score(main_win)
        last_sample = sample
        if score >= 4 and has_context:
            consecutive_ready += 1
            print(f"单笔转账表单字段已可见 {consecutive_ready}/2: matched={score}")
            if consecutive_ready >= 2:
                _sleep_and_check(1, "fill: 单笔转账表单渲染后稳定等待")
                return
        else:
            consecutive_ready = 0
            print(f"等待表单字段渲染中: matched={score}, context={has_context}")
        _sleep_and_check(1, "fill: 等待表单字段渲染")

    _screenshot("00_单笔转账表单加载超时")
    print("表单加载超时前可见控件样本:")
    for name in last_sample[:40]:
        print(f"  [visible] {name}")
    raise RuntimeError("单笔转账表单字段未渲染完成，已停止，避免在加载页误输入")


def _input_screenshot_label(label):
    text = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in str(label or "").strip())
    text = "_".join(part for part in text.split("_") if part)
    return text or "字段"


def _input_or_abort(main_win, label, text_value, fallback_ratio, input_mode="type"):
    """关键字段输入：UIA 命中失败回退到坐标，仍失败则截图并 raise。

    fail-closed 原因：收方账号 / 收方户名 / 金额 / 用途 任一字段没真正写进
    U-BANK 表单，「经办」按钮按下时银行端会以缺字段拒绝，但脚本可能已经进了
    后续流程并误判成功，又或者填错位置导致字段错位。所以哪怕坐标兜底也输入
    失败，必须立刻终止本次制单，让调度方按失败重试。
    """
    if find_and_input_text(main_win, label, text_value, input_mode=input_mode):
        _screenshot(f"input_after_{_input_screenshot_label(label)}")
        return
    cx, cy = fallback_ratio
    if _click_ratio_and_paste(cx, cy, text_value, label, input_mode=input_mode):
        _screenshot(f"input_after_{_input_screenshot_label(label)}")
        return
    _screenshot(f"input_abort_{label}")
    raise RuntimeError(
        f"关键字段[{label}]输入失败：UIA 与坐标兜底均未成功，已截图并终止本次制单"
    )


def _digits_only(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _compact_ws(value):
    return "".join(str(value or "").split())


# 收方户名规范化：把中文全角括号和英文半角括号视为等价，仍去掉所有空白。
# 银行端有时把 (北京) 显示成 （北京），属于显示风格差异，不应当成字段错位。
_BRACKET_FOLD = {
    "（": "(",
    "）": ")",
}


def _fold_brackets(value):
    s = str(value or "")
    for k, v in _BRACKET_FOLD.items():
        s = s.replace(k, v)
    return s


def _compact_ws_fold_brackets(value):
    return _compact_ws(_fold_brackets(value))


def _normalize_amount(value):
    """金额规范化：剥逗号/空白后转 Decimal，无法解析时返回 None。"""
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace("，", "").replace(" ", "")
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError, TypeError):
        return None


def _looks_like_placeholder(actual, label=""):
    """判断 UIA 读到的值是不是 placeholder / 标签回落（视为"没读到值"）。"""
    a = _compact_ws(actual)
    if not a:
        return True
    # 标签名直接当成值（编辑控件 element_info.name 没值时常见）
    if label and a == _compact_ws(label):
        return True
    for token in _PLACEHOLDER_TOKENS:
        if _compact_ws(token) in a:
            return True
    return False


def _verify_account(target, actual):
    target_digits = _digits_only(target)
    actual_digits = _digits_only(actual)
    if not target_digits:
        return f"收方账号 目标为空（validate 应已堵掉），actual=[{actual}]"
    if not actual_digits:
        return f"收方账号 UIA 读不到数字，actual=[{actual}]"
    if actual_digits != target_digits:
        return f"收方账号 不等：期望[{target_digits}], 实际[{actual_digits}]"
    return None


def _verify_payee_name(target, actual):
    # 规范化时把全角 （） 折成半角 ()，避免 U-BANK 显示风格差异（例如
    # 示例公司(北京)科技有限公司 vs 示例公司（北京）科技有限公司）被误判。
    a = _compact_ws_fold_brackets(actual)
    t = _compact_ws_fold_brackets(target)
    if not t:
        return f"收方户名 目标为空（validate 应已堵掉），actual=[{actual}]"
    if _looks_like_placeholder(actual, "收方户名"):
        return f"收方户名 UIA 读到 placeholder/标签：actual=[{actual}]"
    # 收方户名是银行端强校验字段，不能用包含关系放行；否则
    # "张三张三" 这类重复追加会在提交前漏过，最终被银行拒绝。
    if a == t:
        return None
    if t and a.count(t) >= 2:
        return f"收方户名 重复追加：期望[{target}], 实际[{actual}]"
    return f"收方户名 不匹配（要求规范化后严格相等，全/半角括号视为等价）：期望[{target}], 实际[{actual}]"


def _verify_bank_head(target, actual, allow_manual_review):
    """开户银行专用校验：招商银行特殊路径 + 普通银行包含校验。"""
    t = _compact_ws(target)
    a = _compact_ws(actual)
    if not t:
        return f"开户银行 目标为空（validate 应已堵掉），actual=[{actual}]"

    is_cmb = t == "招商银行"

    if is_cmb:
        # _trim_repeated_bank_value 已修过"招商银行招商银行"重复拼接，但 UIA
        # 读值会回落成"开户银行"等标签。截图肉眼可见正确，UIA 不可信。
        # 严格通过：actual_norm 恰好是单个"招商银行"。
        if a == "招商银行":
            return None
        if a.count("招商银行") >= 2:
            # 退格去重失败的极端情况，必须截图复核。
            return f"招商银行 UIA 读到重复拼接：actual=[{actual}]"
        if allow_manual_review:
            print(
                f"[人工复核放行] 招商银行 UIA 读值不可信[{actual}]，"
                f"已显式开启 {_MANUAL_REVIEW_ENV}=1，请人工核对 06_全部填完 截图"
            )
            return None
        return (
            f"招商银行 UIA 读值不可信[{actual}]；如确认 06_全部填完 截图无误，"
            f"再设置 {_MANUAL_REVIEW_ENV}=1 重跑"
        )

    # 普通银行：至少应能 UIA 读到目标银行名。
    if _looks_like_placeholder(actual, "开户银行"):
        return f"开户银行 UIA 读到 placeholder/标签：actual=[{actual}]"
    if t in a:
        return None
    # 兜底：U-BANK 偶尔把全称简化成短名（典型场景：村镇银行系统把
    # "桂林国民村镇银行有限责任公司" 读回成 "村镇银行"）。如果 actual 是非空
    # 非 placeholder，且规范化后是 target 规范化后的子串，允许通过并打印提示。
    # 注意支行已在 _try_select_branch_dropdown 里精确选中目标银行的全称分支，
    # 这里只是开户银行字段的显示读回差异，并非真正的银行错位。
    if a and a in t:
        print(
            f"[开户银行兜底] U-BANK 读回短名[{actual}]，目标[{target}]，"
            f"已放行，请复核截图"
        )
        return None
    return f"开户银行 不匹配：期望规范化包含[{target}], 实际[{actual}]"


def _verify_branch(target, actual, bank_head="", region=None, branch_match_info=None):
    """支行/联行号：严格等值、已核验安全等价名，或已通知的支行下拉层级路由。"""
    t = _compact_ws(target)
    a = _compact_ws(actual)
    if not t:
        return f"支行 目标为空（validate 应已堵掉），actual=[{actual}]"
    if _looks_like_placeholder(actual, "支行名称/联行号"):
        return f"支行 UIA 读到 placeholder/标签：actual=[{actual}]"
    if a != t and not _is_safe_branch_equivalent(target, actual):
        if _is_branch_region_route_info(branch_match_info):
            selected = branch_match_info.get("selected") or ""
            selected_norm = _compact_ws(selected)
            if not branch_match_info.get("notified"):
                return (
                    f"支行下拉层级路由未确认已通知网关：target=[{target}], "
                    f"selected=[{selected}], actual=[{actual}]"
                )
            if a == selected_norm:
                kind = branch_match_info.get("kind") or ""
                label = _BRANCH_REGION_ROUTE_KINDS.get(kind, kind)
                print(
                    f"[支行层级路由] 提交前校验放行：target=[{target}], "
                    f"actual=[{actual}], kind={label}"
                )
                return None
            return (
                f"支行下拉层级路由读回不一致：target=[{target}], "
                f"selected=[{selected}], actual=[{actual}]"
            )
        return f"支行 不等（要求精确或安全等价）：期望[{target}], 实际[{actual}]"
    if a != t:
        print(f"[支行安全等价] 提交前校验放行：target=[{target}], actual=[{actual}]")
    return None


def _verify_amount(target, actual):
    target_amt = _normalize_amount(target)
    actual_amt = _normalize_amount(actual)
    if target_amt is None:
        return f"金额 目标无法规范化[{target}]"
    if target_amt <= 0:
        return f"金额 目标非正数[{target}]"
    if actual_amt is None:
        return f"金额 UIA 读值无法规范化[{actual}]"
    if actual_amt != target_amt:
        return f"金额 不等：期望[{target_amt}], 实际[{actual_amt}]"
    return None


def _verify_purpose(target, actual):
    t = _compact_ws(target)
    a = _compact_ws(actual)
    if not t:
        return f"用途 目标为空（validate 应已堵掉），actual=[{actual}]"
    if _looks_like_placeholder(actual, "用途"):
        return f"用途 UIA 读到 placeholder/标签：actual=[{actual}]"
    if a != t:
        return f"用途 不等（要求精确等值）：期望[{target}], 实际[{actual}]"
    return None


def _first_form_value(form, keys):
    for key in keys:
        value = str((form or {}).get(key) or "").strip()
        if value:
            return value
    return ""


def _region_variants(value):
    text = _compact_ws(value)
    if not text:
        return []
    variants = [text]
    for suffix in ("壮族自治区", "回族自治区", "维吾尔自治区", "自治区", "自治州", "地区", "省", "市", "区", "县"):
        if text.endswith(suffix) and len(text) > len(suffix):
            variants.append(text[: -len(suffix)])
            break
    result = []
    for item in variants:
        if item and item not in result:
            result.append(item)
    return result


def _append_region_token(tokens, value):
    for token in _region_variants(value):
        if token and token not in tokens:
            tokens.append(token)


def _extract_explicit_region(text, bank_head=""):
    compact = _compact_ws(text)
    for token in (
        bank_head,
        "股份有限公司",
        "有限责任公司",
        "中国银行",
        "中国工商银行",
        "中国农业银行",
        "中国建设银行",
        "招商银行",
    ):
        token = _compact_ws(token)
        if token:
            compact = compact.replace(token, "")
    province = ""
    city = ""
    district = ""
    province_match = re.search(r"([\u4e00-\u9fff]{2,9}?(?:省|自治区))", compact)
    city_match = re.search(r"([\u4e00-\u9fff]{2,9}?(?:自治州|地区|盟|市))", compact)
    district_match = re.search(r"([\u4e00-\u9fff]{2,9}?(?:自治县|林区|区|县|旗))", compact)
    if province_match:
        province = province_match.group(1)
    if city_match:
        city = city_match.group(1)
    if district_match:
        district = district_match.group(1)
    for municipality in _DIRECT_MUNICIPALITIES:
        short = municipality[:-1]
        if municipality in compact or short in compact:
            province = province or municipality
            city = city or municipality
            break
    return province, city, district


def _derive_prefix_region_tokens(branch_full, bank_head):
    """Last-resort location tokens from the branch-name prefix, without hard-coded cities."""
    core = _strip_known_branch_parts(branch_full, bank_head, {})
    tokens = []
    if re.search(r"(省|市|区|县|自治州|地区|盟)", core):
        return tokens
    for size in (2, 3):
        if len(core) >= size + 2:
            prefix = core[:size]
            if prefix not in tokens:
                tokens.append(prefix)
    return tokens


def _infer_opening_region(form, branch_full, bank_head=""):
    province = _first_form_value(form, ("开户省", "开户地址省", "省", "province"))
    city = _first_form_value(form, ("开户市", "开户地址市", "市", "city"))
    district = _first_form_value(form, ("开户区县", "开户地址区县", "区县", "district", "county"))
    text = " ".join(str(value or "") for value in (branch_full, province, city, district))

    parsed_province, parsed_city, parsed_district = _extract_explicit_region(text, bank_head)
    province = province or parsed_province
    city = city or parsed_city
    district = district or parsed_district
    if not city:
        inferred_province, inferred_city = infer_city_region_from_text(text)
        province = province or inferred_province
        city = city or inferred_city
    if city and not province:
        province = infer_province_from_city(city)

    tokens = []
    for value in (province, city, district):
        _append_region_token(tokens, value)
    return {"province": province, "city": city, "district": district, "tokens": tokens}


def _strip_known_branch_parts(value, bank_head="", region=None):
    text = _compact_ws(value)
    for token in (
        bank_head,
        "股份有限公司",
        "有限责任公司",
        "中国银行",
        "中国工商银行",
        "中国农业银行",
        "中国建设银行",
        "招商银行",
    ):
        token = _compact_ws(token)
        if token:
            text = text.replace(token, "")
    region = region or {}
    for token in (region.get("province"), region.get("city"), region.get("district")):
        token = _compact_ws(token)
        if token:
            text = text.replace(token, "").replace(token.rstrip("省市区县"), "")
    for suffix in ("支行", "分行", "营业部", "分理处", "办事处"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text


def _append_unique(items, value):
    value = str(value or "").strip()
    if not value:
        return
    key = _compact_ws(value)
    if key and all(_compact_ws(item) != key for item in items):
        items.append(value)


def _safe_screenshot_label_part(value, limit=32):
    text = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in str(value or "").strip())
    text = "_".join(part for part in text.split("_") if part)
    return (text or "unknown")[:limit]


def _build_branch_attempts(form, bank_head, branch_full):
    region = _infer_opening_region(form, branch_full, bank_head)
    attempts = []
    # 先查完整支行名；若下拉无正确项，再仍在同一个支行输入框里用
    # 区县/市/省让 U-BANK 自己返回上级网点候选。
    _append_unique(attempts, branch_full)
    for key in ("district", "city", "province"):
        value = region.get(key)
        for token in _region_variants(value):
            _append_unique(attempts, f"{bank_head}{token}")
    return attempts, region


def _city_county_filter_value(region):
    # U-BANK 的第二个开户地址框显示“市/县”；优先用市，直辖市也填市。
    return region.get("city") or region.get("district") or ""


def _is_branch_region_route_info(branch_match_info):
    if not isinstance(branch_match_info, dict):
        return False
    return branch_match_info.get("kind") in _BRANCH_REGION_ROUTE_KINDS


def _existing_image_path(raw_path):
    path = Path(str(raw_path or "").strip())
    if not str(path):
        return ""
    try:
        if path.is_file():
            return str(path)
    except OSError:
        return ""
    return ""


def _collect_branch_region_notify_images(form, screenshot_path=""):
    image_paths = []
    m3_image_paths = []
    missing_m3_labels = []

    def add_image(path_text):
        if path_text and path_text not in image_paths:
            image_paths.append(path_text)

    add_image(_existing_image_path(screenshot_path))
    for key, label in _M3_REGION_ROUTE_IMAGE_KEYS:
        image_path = _existing_image_path((form or {}).get(key))
        if image_path:
            add_image(image_path)
            m3_image_paths.append(image_path)
        else:
            missing_m3_labels.append(label)

    return image_paths, m3_image_paths, missing_m3_labels


def _require_m3_images_for_region_route():
    if os.getenv("ZHIDAN_REQUIRE_M3_IMAGES_FOR_REGION_BRANCH", "1") == "0":
        return False
    return os.getenv("ZHIDAN_TEST_MODE", "1") == "0"


def _notify_branch_region_route_or_abort(form, branch_match_info, screenshot_path=""):
    """Notify through the gateway when the branch dropdown uses a region-level route."""
    if not _is_branch_region_route_info(branch_match_info):
        return branch_match_info
    if branch_match_info.get("notified"):
        return branch_match_info
    if os.getenv("ZHIDAN_NOTIFY_REGION_BRANCH_MATCH", "1") == "0":
        if os.getenv("ZHIDAN_REQUIRE_REGION_BRANCH_NOTIFY", "1") == "1":
            raise RuntimeError("支行下拉已使用层级路由，但通知开关被关闭，终止制单")
        return branch_match_info

    form = form or {}
    kind = branch_match_info.get("kind") or ""
    level_label = _BRANCH_REGION_ROUTE_KINDS.get(kind, kind)
    target = branch_match_info.get("target") or form.get("支行名称") or ""
    selected = branch_match_info.get("selected") or ""
    payee = form.get("收方户名") or "未取到收款方"
    amount = form.get("金额") or "未取到金额"
    message = (
        f"招行制单使用支行下拉层级路由；收款方：{payee}；金额：{amount}；"
        f"目标支行：{target}；下拉选择：{selected}；层级：{level_label}。"
    )
    image_paths, m3_image_paths, missing_m3_labels = _collect_branch_region_notify_images(
        form,
        screenshot_path,
    )
    if _require_m3_images_for_region_route() and missing_m3_labels:
        raise RuntimeError(
            "真实制单使用支行下拉层级路由时，必须随飞书提醒发送 M3 付款信息截图；"
            f"当前缺少：{'、'.join(missing_m3_labels)}，终止制单"
        )

    gateway_home = Path(os.getenv("CODEX_GATEWAY_HOME", str(Path.home() / "Desktop" / "网关")))
    script = gateway_home / "scripts" / "report_to_codex.py"
    required = os.getenv("ZHIDAN_REQUIRE_REGION_BRANCH_NOTIFY", "1") == "1"
    if not script.exists():
        print("[飞书通知] 网关脚本不存在，无法上报支行层级路由")
        if required:
            raise RuntimeError("支行下拉已使用层级路由，但网关脚本不存在，终止制单")
        return branch_match_info

    project_root = Path(__file__).resolve().parents[3]
    cmd = [
        sys.executable,
        str(script),
        "--project-path",
        str(project_root),
        "--title",
        "招行支行层级路由提醒",
        "--status",
        "warning",
        "--error",
        message,
        "--context",
        (
            "完整支行名下拉没有精确或安全等价项，已在同一个支行下拉框选择同银行同地区的唯一上级网点；"
            "仍按原安全开关执行。"
            + (
                " 已附 M3 合同付款列表截图和付款详情截图，网关会随飞书提醒发送图片。"
                if len(m3_image_paths) == len(_M3_REGION_ROUTE_IMAGE_KEYS)
                else " 本次未带齐 M3 截图，仅允许在测试模式下继续。"
            )
        ),
        "--source",
        "招行支行下拉层级路由",
    ]
    for path in image_paths:
        cmd.extend(["--image-path", path])

    attempts = max(1, int(os.getenv("ZHIDAN_REGION_BRANCH_NOTIFY_ATTEMPTS", "6") or "6"))
    retry_seconds = max(0.0, float(os.getenv("ZHIDAN_REGION_BRANCH_NOTIFY_INTERVAL", "10") or "10"))
    last_error = ""
    for attempt in range(1, attempts + 1):
        try:
            completed = subprocess.run(
                cmd,
                cwd=str(project_root),
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=30,
                check=False,
            )
        except Exception as exc:
            last_error = str(exc)
            print(f"[飞书通知] 支行层级路由通知第 {attempt}/{attempts} 次异常: {exc}")
        else:
            stdout = completed.stdout.strip()
            stderr = completed.stderr.strip()
            last_error = stderr or stdout or f"exit={completed.returncode}"
            print(f"[飞书通知] 支行层级路由通知第 {attempt}/{attempts} 次退出码: {completed.returncode}")
            if stdout:
                print(f"[飞书通知] stdout: {stdout[:500]}")
            if stderr:
                print(f"[飞书通知] stderr: {stderr[:500]}")
            if completed.returncode == 0:
                updated = dict(branch_match_info)
                updated["notified"] = True
                return updated

        if attempt < attempts:
            print(f"[飞书通知] 支行层级路由通知未成功，{retry_seconds:g}s 后重试")
            time.sleep(retry_seconds)

    if required:
        raise RuntimeError(f"支行下拉已使用层级路由，但飞书网关通知失败，终止制单：{last_error}")
    return branch_match_info


def _try_direct_branch_query(main_win, bank_head, branch_name, region):
    """Experimental fail-closed path: type full branch name, then click 查询支行."""
    if os.getenv("ZHIDAN_DIRECT_BRANCH_QUERY", "0") != "1":
        return False

    print(
        "[支行直输策略] 已启用 ZHIDAN_DIRECT_BRANCH_QUERY=1；"
        "将直输完整支行名并点击“查询支行”验证"
    )
    if not _input_branch_name(main_win, "支行名称/联行号", branch_name):
        _screenshot("05_支行直输_输入失败")
        raise RuntimeError("支行直输策略：支行输入框定位/输入失败，终止制单")
    time.sleep(1.0)
    _check_ubank_crash("fill: 支行直输完整名称后")
    _screenshot("04_支行直输_完整支行名")

    query_clicked = _click_query_branch_button(main_win)
    _check_ubank_crash("fill: 查询支行按钮点击后")
    if query_clicked:
        time.sleep(2.0)
        _screenshot("05_查询支行点击后")
        selected, branch_dropdown_fallback = _try_select_query_branch_result(
            main_win,
            branch_name,
            bank_head=bank_head,
            region=region,
            attempt_label="direct_query",
        )
        _check_ubank_crash("fill: 查询支行结果处理后")
        if selected:
            _screenshot("05_查询支行结果选择后")
            return True
        if branch_dropdown_fallback:
            _screenshot("05_查询支行兜底_禁止继续")
            raise RuntimeError(f"查询支行结果使用第一条候选兜底，已禁止继续：target=[{branch_name}]")
    else:
        _screenshot("05_查询支行按钮未找到")

    if query_clicked and os.getenv("ZHIDAN_ALLOW_DIRECT_BRANCH_TEXT_ONLY", "0") != "1":
        print("[支行直输策略] 查询支行未选到安全结果，默认禁止仅凭输入框文字继续")
        return False

    actual_branch = _read_nearby_input_text(main_win, "支行名称/联行号")
    err = _verify_branch(branch_name, actual_branch, bank_head, region)
    if err:
        print(f"[支行直输策略] 直输后读回校验未通过：{err}")
        _screenshot("05_支行直输校验失败")
        return False

    print("[支行直输策略] 直输后读回校验通过；继续让第一次经办做银行端格式验证")
    _screenshot("05_支行直输校验通过")
    return True


def verify_filled_form_or_abort(
    main_win,
    form,
    *,
    skip_branch,
    payee_lookup_selected,
    payee_lookup_info=None,
    branch_dropdown_fallback=False,
    branch_match_info=None,
):
    """提交前对关键字段做规范化校验。任一项不符合 → 截图 + RuntimeError。

    与 _input_or_abort 的区别：那个只保证"输入动作成功"，这里是真正读回字段
    现值与目标做规范化比较，能拦住"旧值未清空导致追加""焦点跑偏写错框""支行
    UIA 没拿到精确匹配但坐标兜底点了相邻项"等场景。

    fail-closed 规则：
    - 收方账号：纯数字相等
    - 收方户名：规范化后严格相等，全/半角括号视为等价
    - 开户银行：普通银行规范化包含；actual 为 target 子串时按短名兜底放行；
      招商银行严格等于"招商银行"，否则需要显式 ZHIDAN_ALLOW_MANUAL_SCREENSHOT_REVIEW=1
    - 支行：精确等值或唯一已核验安全等价显示名；
      branch_dropdown_fallback=True 视为失败。支行没有精确或安全等价匹配时不能靠
      第一条候选继续制单，因为相似地名会导致跨城市错选。
    - 金额：Decimal 等值
    - 用途：精确等值
    - payee_lookup_selected=True 时表示已点击收方账号处的常用/名册候选；账号/户名
      通过校验后，支行以 U-BANK 名册当前读回值为权威，M3 支行差异仅做审计记录。
    - skip_branch=True（招商银行的支行栏被禁用）跳过支行校验。
    """
    failures = []

    target_acct = (form.get("收方账号") or "").strip()
    target_name = (form.get("收方户名") or "").strip()
    target_bank = (form.get("开户银行") or "").strip()
    target_branch = (form.get("支行名称") or "").strip()
    target_amount = form.get("金额")
    target_purpose = (form.get("用途") or "").strip()
    target_region = _infer_opening_region(form, target_branch, target_bank)

    allow_manual = os.getenv(_MANUAL_REVIEW_ENV, "0") == "1"

    actual_acct = _read_nearby_input_text(main_win, "收方账号")
    acct_err = _verify_account(target_acct, actual_acct)
    if acct_err:
        failures.append(acct_err)

    actual_name = _read_nearby_input_text(main_win, "收方户名")
    name_err = _verify_payee_name(target_name, actual_name)
    if name_err:
        failures.append(name_err)
    payee_identity_verified = not acct_err and not name_err

    if payee_lookup_selected:
        print(
            "提交前校验：已点击收方账号常用/名册候选；"
            "账号/户名通过后，以 U-BANK 名册支行为准"
        )

    actual_bank = _read_nearby_input_text(main_win, "开户银行")
    err = _verify_bank_head(target_bank, actual_bank, allow_manual)
    if err:
        failures.append(err)

    if not skip_branch:
        actual_branch = _read_nearby_input_text(main_win, "支行名称/联行号")
        if branch_dropdown_fallback:
            failures.append(
                f"支行下拉使用第一条候选兜底，已禁止继续：target=[{target_branch}], actual=[{actual_branch}]"
            )
        else:
            branch_target = target_branch
            active_branch_match_info = branch_match_info
            if payee_lookup_selected and payee_lookup_info and payee_identity_verified:
                brought_branch = (payee_lookup_info or {}).get("branch") or ""
                if _looks_like_placeholder(actual_branch, "支行名称/联行号"):
                    failures.append(f"名册支行 UIA 读到 placeholder/标签：actual=[{actual_branch}]")
                    brought_branch = ""
                if brought_branch and _compact_ws(brought_branch) != _compact_ws(actual_branch):
                    print(
                        f"[名册支行读回变化] 双击后读值=[{brought_branch}]，"
                        f"提交前读值=[{actual_branch}]，以提交前读值为准"
                    )
                if not _looks_like_placeholder(actual_branch, "支行名称/联行号"):
                    branch_target = actual_branch
                    active_branch_match_info = None
                    if _compact_ws(target_branch) != _compact_ws(actual_branch):
                        print(
                            f"[名册支行修正] M3 支行=[{target_branch}]，"
                            f"U-BANK 名册支行=[{actual_branch}]；账号/户名已校验，按名册支行继续"
                        )
                        _screenshot("07_名册支行与M3不一致_按名册")
            err = _verify_branch(
                branch_target,
                actual_branch,
                target_bank,
                target_region,
                branch_match_info=active_branch_match_info,
            )
            if err:
                failures.append(err)
    else:
        print("提交前校验：支行栏已判定无需填写，跳过支行字段校验")

    actual_amount_text = _read_nearby_input_text(main_win, "金额(￥)")
    err = _verify_amount(target_amount, actual_amount_text)
    if err:
        failures.append(err)

    actual_purpose = _read_nearby_input_text(main_win, "用途")
    err = _verify_purpose(target_purpose, actual_purpose)
    if err:
        failures.append(err)

    if failures:
        _screenshot("07_提交前校验失败")
        details = "\n  - ".join(failures)
        msg = (
            "提交前字段校验失败（已截图 07_提交前校验失败），不会点击「经办」：\n  - "
            + details
        )
        print(msg)
        raise RuntimeError(msg)

    print("提交前字段校验全部通过，可以进入「经办」点击")


def _fill_opening_info_manually(main_win, bank_head, branch_full, form=None):
    """按 bank_form.json 手工填写开户银行与支行，返回支行状态。"""
    if not _input_bank_name(main_win, bank_head):
        raise RuntimeError("开户银行填写或校验失败，终止制单")
    _check_ubank_crash("fill: 开户银行 后")

    branch_name = branch_full
    _screenshot("03_支行检测")
    print("开始检测支行是否需要填写...")

    skip_branch = _check_skip_branch(main_win)
    _check_ubank_crash("fill: 支行 skip 检测后")
    branch_dropdown_fallback = False
    branch_match_info = {}
    probe_mode = os.getenv("ZHIDAN_BRANCH_PROBE", "0") == "1"

    if skip_branch:
        print("检测到支行无需填写，已跳过支行步骤")
        _screenshot("05_支行跳过")
    else:
        if not (branch_name or "").strip():
            _screenshot("05_支行必填_空值")
            raise RuntimeError(
                f"开户银行[{bank_head}]要求填写支行，但 支行名称 为空，终止制单"
            )
        attempts, region = _build_branch_attempts(form or {}, bank_head, branch_name)
        region_text = ", ".join(
            part for part in (region.get("district"), region.get("city"), region.get("province")) if part
        )

        branch_clicked = False
        for attempt_no, query in enumerate(attempts or [branch_name], start=1):
            attempt_label = f"{attempt_no:02d}_{_safe_screenshot_label_part(query)}"
            if attempt_no == 1:
                print(f"[支行单次策略] 输入完整支行名: {query}")
            else:
                print(f"[支行路由策略] 在支行下拉框按地区路由输入: {query}")
            branch_ok = _input_branch_name(main_win, "支行名称/联行号", query)
            _check_ubank_crash("fill: 支行 输入后")
            if not branch_ok:
                _screenshot(f"05_支行输入失败_{attempt_no:02d}")
                raise RuntimeError("支行输入框定位/输入失败，终止制单")
            time.sleep(1.5)
            _check_ubank_crash("fill: 支行 输入后等待")
            _screenshot(f"04_支行输入后_{attempt_label}")
            branch_clicked, branch_dropdown_fallback, branch_match_info = _try_select_branch_dropdown(
                main_win,
                branch_name,
                attempt_label=attempt_label,
                bank_head=bank_head,
                region=region,
            )
            _check_ubank_crash("fill: 支行 下拉检测后")
            if branch_clicked and _is_branch_region_route_info(branch_match_info):
                label = _BRANCH_REGION_ROUTE_KINDS.get(branch_match_info.get("kind"), branch_match_info.get("kind"))
                print(
                    f"[支行路由策略] 下拉框未命中完整支行，已命中同银行同地区上级网点: "
                    f"{branch_match_info.get('selected')} ({label})"
                )
                break
            if branch_clicked:
                print(f"[支行单次策略] 下拉框命中目标支行: {query}")
                break
            if probe_mode:
                break
            print(f"[支行路由策略] 本次下拉未命中正确支行/地区上级网点: {query}")

        if not branch_clicked:
            if probe_mode:
                _screenshot("05_支行探测完成_终止")
                raise RuntimeError(
                    f"支行探测已完成，主动终止，不点击经办：target=[{branch_name}]"
                )
            if not region_text:
                _screenshot("05_支行无匹配_终止")
                raise RuntimeError(
                    f"支行下拉无与 [{branch_name}] 精确、安全等价或同地区上级网点匹配的项，终止制单"
                )
            _screenshot("05_支行无匹配_终止")
            raise RuntimeError(
                f"支行下拉无与 [{branch_name}] 精确、安全等价或同地区上级网点匹配的项，"
                f"已识别地区[{region_text}]，但按规则不再改填外部开户地址省市，终止制单"
            )
        if _is_branch_region_route_info(branch_match_info):
            route_screenshot = _screenshot("05_支行下拉层级路由已选择")
            actual_branch = _read_nearby_input_text(main_win, "支行名称/联行号")
            err = _verify_branch(
                branch_name,
                actual_branch,
                bank_head,
                region,
                branch_match_info={**branch_match_info, "notified": True},
            )
            if err:
                _screenshot("05_支行层级路由读回失败")
                raise RuntimeError(f"支行下拉层级路由读回校验失败：{err}")
            branch_match_info = _notify_branch_region_route_or_abort(
                form or {},
                branch_match_info,
                screenshot_path=route_screenshot or "",
            )
        if branch_dropdown_fallback:
            _screenshot("05_支行兜底_禁止继续")
            raise RuntimeError(
                f"支行下拉使用第一条候选兜底，已禁止继续：target=[{branch_name}]"
            )
        _screenshot("05_支行选择后")

    _trace_fill_step("支行阶段结束，稳定等待 3s 开始")
    _sleep_and_check(3, "fill: 支行阶段结束稳定等待")
    _trace_fill_step("支行阶段结束，稳定等待 3s 完成")
    return skip_branch, branch_dropdown_fallback, branch_match_info


def _auto_refill_incomplete_fields(
    main_win,
    form,
    *,
    payee_lookup_selected,
    payee_lookup_info=None,
    skip_branch,
    branch_dropdown_fallback,
    branch_match_info,
):
    """提交前发现字段仍是空/placeholder 时，自动按 JSON 手工补填一次。"""
    target_acct = (form.get("收方账号") or "").strip()
    target_name = (form.get("收方户名") or "").strip()
    target_bank = (form.get("开户银行") or "").strip()
    target_branch = (form.get("支行名称") or "").strip()
    target_amount = form.get("金额")
    target_purpose = (form.get("用途") or "").strip()

    refilled = False

    actual_acct = _read_nearby_input_text(main_win, "收方账号")
    if _looks_like_placeholder(actual_acct, "收方账号"):
        print(f"[自动重填] 收方账号信息不全，重新手填: actual=[{actual_acct}]")
        _input_or_abort(main_win, "收方账号", target_acct, (0.545, 0.557))
        refilled = True

    actual_name = _read_nearby_input_text(main_win, "收方户名")
    if _looks_like_placeholder(actual_name, "收方户名"):
        print(f"[自动重填] 收方户名信息不全，重新手填: actual=[{actual_name}]")
        _input_or_abort(main_win, "收方户名", target_name, (0.545, 0.616))
        refilled = True

    actual_bank = _read_nearby_input_text(main_win, "开户银行")
    opening_incomplete = _looks_like_placeholder(actual_bank, "开户银行")
    if not skip_branch and target_branch:
        actual_branch = _read_nearby_input_text(main_win, "支行名称/联行号")
        opening_incomplete = opening_incomplete or _looks_like_placeholder(
            actual_branch, "支行名称/联行号"
        )
    else:
        actual_branch = ""

    if opening_incomplete:
        print(
            "[自动重填] 开户信息不全，按 bank_form.json 重新手填开户银行/支行；"
            f"开户银行读值=[{actual_bank}], 支行读值=[{actual_branch}]"
        )
        _screenshot("06_信息不全_开户信息重填前")
        payee_lookup_selected = False
        payee_lookup_info = None
        skip_branch, branch_dropdown_fallback, branch_match_info = _fill_opening_info_manually(
            main_win, target_bank, target_branch, form
        )
        refilled = True

    actual_amount = _read_nearby_input_text(main_win, "金额(￥)")
    if _looks_like_placeholder(actual_amount, "金额(￥)"):
        print(f"[自动重填] 金额信息不全，重新手填: actual=[{actual_amount}]")
        _input_or_abort(main_win, "金额(￥)", target_amount, (0.545, 0.843))
        refilled = True

    actual_purpose = _read_nearby_input_text(main_win, "用途")
    if _looks_like_placeholder(actual_purpose, "用途"):
        print(f"[自动重填] 用途信息不全，重新手填: actual=[{actual_purpose}]")
        _input_or_abort(main_win, "用途", target_purpose, (0.525, 0.897))
        refilled = True

    if refilled:
        _sleep_and_check(1, "自动重填后稳定等待")
        _screenshot("06_信息不全_自动重填后")

    return payee_lookup_selected, skip_branch, branch_dropdown_fallback, branch_match_info, payee_lookup_info, refilled


def _payee_lookup_brought_required_info(main_win, context, target_bank=""):
    """Return brought field values when the namebook row filled recipient/opening-bank fields."""
    print(f"{context}，检查名册是否已带出户名/开户银行/支行")
    _screenshot("02_收方名册双击带出后")
    _sleep_and_check(1, "fill: 收方名册双击带出后稳定等待")

    brought_name = _read_nearby_input_text(main_win, "收方户名")
    brought_bank = _read_nearby_input_text(main_win, "开户银行")
    brought_branch = _read_nearby_input_text(main_win, "支行名称/联行号")
    branch_optional = _compact_ws(target_bank) == "招商银行" and _compact_ws(brought_bank) == "招商银行"
    incomplete = (
        _looks_like_placeholder(brought_name, "收方户名")
        or _looks_like_placeholder(brought_bank, "开户银行")
        or (not branch_optional and _looks_like_placeholder(brought_branch, "支行名称/联行号"))
    )
    if incomplete:
        print(
            "收方名册未完整带出户名/开户银行/支行，回退手工填写路径；"
            f"户名读值=[{brought_name}], 开户银行读值=[{brought_bank}], 支行读值=[{brought_branch}]"
        )
        _screenshot("02_收方名册未完整带出_转手工")
        _close_transient_payee_popup("名册未完整带出，回退手工收方信息")
        return None

    if branch_optional:
        print("收方名册已带出户名/开户银行；收方开户银行为招商银行，支行栏无需填写，直接进入金额")
    else:
        print("收方名册已完整带出户名/开户银行/支行，跳过收方户名、开户银行、支行手工输入，直接进入金额")
    return {
        "source": "ubank_payee_lookup",
        "name": brought_name,
        "bank": brought_bank,
        "branch": brought_branch,
        "branch_optional": branch_optional,
    }


def _try_autofill_payee_from_namebook(main_win, payee_acct, payee_name, target_bank, context):
    """Generic common-contact strategy: double-click a matching row if present."""
    if os.getenv("ZHIDAN_FORCE_MANUAL_OPENING_INFO") == "1":
        print("已设置 ZHIDAN_FORCE_MANUAL_OPENING_INFO=1，跳过收方账号常用/名册候选点击")
        _close_transient_payee_popup("手工开户信息路径")
        return None
    if not _select_payee_lookup_candidate(main_win, payee_acct, payee_name):
        return None
    return _payee_lookup_brought_required_info(main_win, context, target_bank=target_bank)


def fill_transfer_form(main_win, form=None, form_loaded=False):
    """
    填写单笔转账经办表单
    优先从当前财务目录下 M3直供合同付款数据获取\\bank_form.json 读取字段；
    若不存在则仅在 ZHIDAN_ALLOW_DEMO_MODE=1 的调试路径使用内置示例值；
    正常入口会在打开 U-BANK 前 fail-closed。
    """
    _form = form or {}

    if form_loaded:
        payee_acct = _form.get("收方账号", "")
        payee_name = _form.get("收方户名", "")
        bank_head = _form.get("开户银行", "")
        branch_full = _form.get("支行名称", "")
        amount = _form.get("金额", "")
        purpose = _form.get("用途", "")
    else:
        # 示例模式：bank_form.json 未加载时使用的占位值。占位本身会被银行表单的
        # 字段格式校验拒绝（账号含字母、金额 0 等），即便误点经办也无法真实提交。
        # 强提示：本分支严禁用于真实经办。
        print("=" * 60)
        print("[示例模式警告] 未加载 bank_form.json，正在使用内置占位值")
        print("[示例模式警告] 这些占位值不是真实账号/姓名，仅用于本地 UI 调试")
        print("[示例模式警告] 严禁在该分支下点击「经办」执行真实转账提交")
        print("=" * 60)
        payee_acct = _form.get("收方账号") or "DEMO_ACCOUNT_NEVER_SUBMIT"
        payee_name = _form.get("收方户名") or "示例占位_严禁真实提交"
        bank_head = _form.get("开户银行") or "示例银行_严禁真实提交"
        branch_full = _form.get("支行名称") or "示例支行_严禁真实提交"
        amount = _form.get("金额") or "0"
        purpose = _form.get("用途") or "示例用途_严禁真实提交"

    _check_ubank_crash("fill: 进入 fill_transfer_form")
    _dismiss_transfer_overlays()
    _check_ubank_crash("fill: dismiss_overlays 后")
    _wait_transfer_form_ready(main_win)

    # 在填收款方信息之前选择付款方账号下拉（同一 UKey 下挂多账号场景）。
    # 默认无环境变量时 helper 直接 return True 不操作下拉；任意失败 fail-closed。
    from ubank_common import _select_payer_account_if_requested
    if not _select_payer_account_if_requested(main_win):
        _screenshot("00_付款方账号选择失败")
        raise RuntimeError(
            "付款方账号选择失败：未填收款方信息，未点击经办/提交，终止本次制单"
        )
    _check_ubank_crash("fill: 付款方账号选择后")

    # 收方账号（关键字段，输入失败必须终止）
    _input_or_abort(main_win, "收方账号", payee_acct, (0.545, 0.557))
    _check_ubank_crash("fill: 收方账号 后")

    # 通用策略：收方账号输入后，如果出现匹配的常用联系人/名册行，就双击让
    # U-BANK 自动带出户名、开户银行、支行/联行号；带出完整时直接进入金额。
    payee_lookup_info = _try_autofill_payee_from_namebook(
        main_win,
        payee_acct,
        payee_name,
        bank_head,
        "已在收方账号后双击收方名册候选",
    )
    payee_lookup_selected = bool(payee_lookup_info)
    _check_ubank_crash("fill: 收方名册浮层处理后")

    if not payee_lookup_selected:
        # 收方户名（关键字段，输入失败必须终止）。只有名册没有完整带出时才手工输入。
        _input_or_abort(main_win, "收方户名", payee_name, (0.545, 0.616))
        _check_ubank_crash("fill: 收方户名 后")

        payee_lookup_info = _try_autofill_payee_from_namebook(
            main_win,
            payee_acct,
            payee_name,
            bank_head,
            "已在收方户名后双击收方名册候选",
        )
        payee_lookup_selected = bool(payee_lookup_info)
        if not payee_lookup_selected:
            print("手工开户信息路径：继续手工填写开户银行/支行")
        _check_ubank_crash("fill: 收方户名/名册回退处理后")

    # 提交前校验需要知道是否真的填了支行：默认"未跳过"，进入支行分支后再覆盖。
    skip_branch = bool(payee_lookup_info and payee_lookup_info.get("branch_optional"))
    # 历史兼容标记：如旧路径走到"第一条"兜底，提交前校验必须失败。
    branch_dropdown_fallback = False
    branch_match_info = {}

    if not payee_lookup_selected:
        skip_branch, branch_dropdown_fallback, branch_match_info = _fill_opening_info_manually(
            main_win, bank_head, branch_full, _form
        )

    # ---- 转账信息 ----

    # 金额（关键字段，输入失败必须终止）
    _trace_fill_step("金额 输入开始")
    _input_or_abort(main_win, "金额(￥)", amount, (0.545, 0.843))
    _trace_fill_step("金额 输入函数返回后")
    _check_ubank_crash("fill: 金额 后")
    _sleep_and_check(1, "fill: 金额 后 1s 稳定等待")
    _trace_fill_step("金额 后 1s 稳定等待完成")

    # 用途（关键字段，输入失败必须终止）
    _trace_fill_step("用途 输入开始")
    _input_or_abort(main_win, "用途", purpose, (0.525, 0.897), input_mode="paste")
    _trace_fill_step("用途 输入函数返回后")
    _check_ubank_crash("fill: 用途 后")
    _sleep_and_check(1, "fill: 用途 后 1s 稳定等待")
    _trace_fill_step("用途 后 1s 稳定等待完成")

    if payee_lookup_selected:
        print("收方名册已双击选中并带出信息，截图前不再额外 ESC/点击收起，避免干扰金额/用途读回")
    else:
        print("未命中收方名册，跳过截图前浮层收起，避免普通填单路径额外点击/ESC")
    _check_ubank_crash("fill: 截图前浮层收起后")

    _screenshot("06_全部填完")
    _check_ubank_crash("fill: 全部填完 截图后")

    # 提交前字段校验：fail-closed 守住"输入动作成功 != 字段值正确"。
    # form_loaded=False 走的是占位值，主流程在 _click_submit_button 之前就会
    # sys.exit(DEMO_MODE_EXIT_CODE)，这里不再重复校验。
    if form_loaded:
        (
            payee_lookup_selected,
            skip_branch,
            branch_dropdown_fallback,
            branch_match_info,
            payee_lookup_info,
            auto_refilled,
        ) = _auto_refill_incomplete_fields(
            main_win,
            _form,
            payee_lookup_selected=payee_lookup_selected,
            payee_lookup_info=payee_lookup_info,
            skip_branch=skip_branch,
            branch_dropdown_fallback=branch_dropdown_fallback,
            branch_match_info=branch_match_info,
        )
        if auto_refilled:
            print("[自动重填] 已完成信息不全字段的手工补填，继续提交前校验")
        verify_filled_form_or_abort(
            main_win,
            _form,
            skip_branch=skip_branch,
            payee_lookup_selected=payee_lookup_selected,
            payee_lookup_info=payee_lookup_info,
            branch_dropdown_fallback=branch_dropdown_fallback,
            branch_match_info=branch_match_info,
        )

