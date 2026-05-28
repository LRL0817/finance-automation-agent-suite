# -*- coding: utf-8 -*-
"""Opening-bank and branch-field automation."""
import os
import sys
import time
from pathlib import Path

import win32gui
from pywinauto import Desktop

from ubank_common import click_at, press_keys
from zhidan_input import find_and_input_text, _type_text_slow, _verify_field_contains, _read_nearby_input_text
from zhidan_regions import PROVINCE_CITIES, city_filter_variants, province_filter_variants
from zhidan_utils import _screenshot


def _add_public_module_dir():
    current = Path(__file__).resolve()
    for parent in current.parents:
        public_dir = parent / "公共"
        if public_dir.is_dir():
            text = str(public_dir)
            if text not in sys.path:
                sys.path.insert(0, text)
            return


_add_public_module_dir()
from bank_branch_matcher import score_branch_candidate, hierarchy_rank  # noqa: E402


class _SimplePoint:
    def __init__(self, x, y):
        self.x = int(x)
        self.y = int(y)


class _SimpleRect:
    def __init__(self, left, top, right, bottom):
        self.left = int(left)
        self.top = int(top)
        self.right = int(right)
        self.bottom = int(bottom)

    def width(self):
        return self.right - self.left

    def height(self):
        return self.bottom - self.top

    def mid_point(self):
        return _SimplePoint((self.left + self.right) / 2, (self.top + self.bottom) / 2)


def _compact_choice_text(value):
    return "".join(str(value or "").split())


def _safe_screenshot_label_part(value, limit=36):
    text = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in str(value or "").strip())
    text = "_".join(part for part in text.split("_") if part)
    return (text or "unknown")[:limit]


def _has_branch_marker(value):
    text = str(value or "")
    return any(marker in text for marker in ("支行", "分行", "营业部", "分理处", "办事处"))


def _ctrl_a_backspace():
    press_keys((0x11, 0), (0x41, 0), (0x41, 2), (0x11, 2))
    time.sleep(0.08)
    press_keys((0x08, 0), (0x08, 2))
    time.sleep(0.12)


_SAFE_BRANCH_ALIASES = (
    # 联行号/网点资料显示“丰台科技园支行”在 U-BANK 中使用“北京科技园支行”全称。
    ("中国工商银行丰台科技园支行", "中国工商银行股份有限公司北京科技园支行"),
)


def _normalize_branch_equivalence_key(value):
    """Conservative branch-name key for verified display-name differences only."""
    text = _compact_choice_text(value)
    for token in ("股份有限公司", "有限责任公司"):
        text = text.replace(token, "")
    text = text.replace("上海市", "上海").replace("北京市", "北京")
    text = text.replace("小微企业专营", "小微")
    if text.endswith("营业部"):
        text = text[: -len("营业部")]
    return text


def _region_prefix_token_map():
    pairs = []
    for province, cities in PROVINCE_CITIES.items():
        for token in province_filter_variants(province):
            pairs.append((token, province))
        for city in cities:
            for token in city_filter_variants(city):
                pairs.append((token, city))
    token_map = {}
    for token, canonical in pairs:
        token = _compact_choice_text(token)
        canonical = _compact_choice_text(canonical)
        if len(token) >= 2 and token not in token_map:
            token_map[token] = canonical
    return token_map


_REGION_PREFIX_TOKEN_MAP = _region_prefix_token_map()
_REGION_PREFIX_TOKENS = tuple(sorted(_REGION_PREFIX_TOKEN_MAP, key=len, reverse=True))
_BANK_PREFIX_MARKERS = ("银行", "信用社")


def _split_bank_prefix_and_tail(value):
    text = _normalize_branch_equivalence_key(value)
    marker_positions = []
    for marker in _BANK_PREFIX_MARKERS:
        idx = text.find(marker)
        if idx >= 0:
            marker_positions.append((idx + len(marker), marker))
    if not marker_positions:
        return "", text
    end, _marker = sorted(marker_positions)[0]
    return text[:end], text[end:]


def _same_bank_prefix(left, right):
    left = _compact_choice_text(left)
    right = _compact_choice_text(right)
    if not left or not right:
        return False
    if left == right:
        return True
    return left.removeprefix("中国") == right.removeprefix("中国")


def _strip_branch_region_prefix(tail):
    text = _compact_choice_text(tail)
    for token in _REGION_PREFIX_TOKENS:
        if not text.startswith(token):
            continue
        rest = text[len(token):]
        if len(rest) >= 4 and _has_branch_marker(rest):
            return rest, _REGION_PREFIX_TOKEN_MAP[token]
    return text, ""


def _same_region_token(left, right):
    if not left or not right:
        return False
    return _compact_choice_text(left) == _compact_choice_text(right)


def _is_region_prefix_insert_equivalent(target, candidate):
    target_bank, target_tail = _split_bank_prefix_and_tail(target)
    candidate_bank, candidate_tail = _split_bank_prefix_and_tail(candidate)
    if not _same_bank_prefix(target_bank, candidate_bank):
        return False

    target_core, target_region = _strip_branch_region_prefix(target_tail)
    candidate_core, candidate_region = _strip_branch_region_prefix(candidate_tail)
    if target_core != candidate_core or not target_core:
        return False
    if not _has_branch_marker(target_core):
        return False
    if target_region and candidate_region:
        return _same_region_token(target_region, candidate_region)
    return bool(target_region or candidate_region)


def _is_safe_branch_equivalent(target, candidate):
    target_norm = _compact_choice_text(target)
    candidate_norm = _compact_choice_text(candidate)
    if target_norm == candidate_norm:
        return True
    for left, right in _SAFE_BRANCH_ALIASES:
        pair = {_compact_choice_text(left), _compact_choice_text(right)}
        if {target_norm, candidate_norm} == pair:
            return True
    if _normalize_branch_equivalence_key(target) == _normalize_branch_equivalence_key(candidate):
        return True
    return _is_region_prefix_insert_equivalent(target, candidate)



def _region_token_variants(value):
    text = _compact_choice_text(value)
    if not text:
        return []
    variants = [text]
    for suffix in (
        "特别行政区",
        "壮族自治区",
        "回族自治区",
        "维吾尔自治区",
        "自治区",
        "自治州",
        "地区",
        "林区",
        "省",
        "市",
        "区",
        "县",
        "旗",
        "盟",
    ):
        if text.endswith(suffix) and len(text) > len(suffix):
            variants.append(text[: -len(suffix)])
    result = []
    for item in variants:
        if item and item not in result:
            result.append(item)
    return result


def _candidate_region_match_level(candidate, region):
    """Return the strongest行政区 level found in a candidate: district > city > province."""
    if not region:
        return ""
    text = _compact_choice_text(candidate)
    if not text:
        return ""

    levels = (
        ("district_branch", (region.get("district"),)),
        ("city_branch", (region.get("city"),)),
        ("province_branch", (region.get("province"),)),
    )
    for level, values in levels:
        for value in values:
            for token in _region_token_variants(value):
                if token and token in text:
                    return level
    return ""


def _candidate_matches_region(candidate, region):
    return bool(_candidate_region_match_level(candidate, region))


def _candidate_matches_bank(candidate, bank_head):
    bank = _compact_choice_text(bank_head)
    text = _compact_choice_text(candidate)
    if not bank:
        return False
    return bank in text or text in bank


def _branch_hierarchy_match(candidate, bank_head="", region=None):
    """Classify safe non-exact hierarchy matches.

    district/city/province_branch = same bank + same行政区 + higher-level 分行/营业部.
    Same-region 支行 candidates are detected only when
    ZHIDAN_ALLOW_SAME_REGION_BRANCH_MATCH=1 is explicitly enabled.
    """
    text = _compact_choice_text(candidate)
    level = _candidate_region_match_level(text, region)
    if not (_candidate_matches_bank(text, bank_head) and level):
        return ""
    if "分行" in text or ("营业部" in text and "支行" not in text):
        return level
    if os.getenv("ZHIDAN_ALLOW_SAME_REGION_BRANCH_MATCH", "0") == "1" and "支行" in text:
        return f"same_{level}"
    return ""


def _branch_hierarchy_rank(kind):
    return {
        "district_branch": 0,
        "city_branch": 10,
        "province_branch": 20,
        "same_district_branch": 30,
        "same_city_branch": 40,
        "same_province_branch": 50,
    }.get(kind, 99)


def _trim_repeated_bank_value(main_win, bank_name):
    """If the combo box appended the same bank twice, delete the repeated suffix."""
    expected_norm = _compact_choice_text(bank_name)
    if not expected_norm:
        return "", False

    actual_text = _read_nearby_input_text(main_win, "开户银行")
    actual_norm = _compact_choice_text(actual_text)
    if (
        len(actual_norm) > len(expected_norm)
        and len(actual_norm) % len(expected_norm) == 0
        and actual_norm == expected_norm * (len(actual_norm) // len(expected_norm))
    ):
        extra_chars = len(actual_norm) - len(expected_norm)
        print(f"开户银行检测到重复拼接 [{actual_text}]，退格删除多余 {extra_chars} 个字符")
        for _ in range(extra_chars):
            press_keys((0x08, 0), (0x08, 2))
            time.sleep(0.06)
        time.sleep(0.6)
        fixed_text = _read_nearby_input_text(main_win, "开户银行")
        print(f"开户银行去重后: {fixed_text}")
        _screenshot("02_开户银行去重后")
        return fixed_text, True

    return actual_text, False


def _verify_merchants_bank_exact(main_win, bank_name):
    """招商银行 must be exactly one bank name; containing it is not enough."""
    if _compact_choice_text(bank_name) != "招商银行":
        return True
    actual_text = _read_nearby_input_text(main_win, "开户银行")
    actual_norm = _compact_choice_text(actual_text)
    if actual_norm == "招商银行":
        return True
    _screenshot("02_招商银行精确校验失败")
    print(f"招商银行精确校验失败：期望 [招商银行]，实际 [{actual_text}]")
    return False


def _click_bank_dropdown_option(main_win):
    """输入开户银行名称后，等待下拉列表出现，点击银行选项确认"""

    def _find_bank_items(control, out_list):
        """递归查找开户银行下拉列表中的选项项"""
        try:
            if not control.is_visible():
                return
        except Exception:
            return
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            ctrl_type = str(control.element_info.control_type) if hasattr(control.element_info, "control_type") else ""
            rect = control.rectangle()
            # 匹配单行高度的银行名称选项（排除容器聚合文本）
            name_len = len(name.strip()) if name else 0
            is_single_item = 2 <= name_len <= 20
            is_single_row = 15 <= rect.height() <= 55 and rect.width() > 60
            if name and is_single_item and is_single_row and (
                "招商" in name or "中国银行" in name or "工商" in name or
                "建设" in name or "农业" in name or "交通" in name or
                "中信" in name or "浦发" in name or "兴业" in name
            ):
                out_list.append((rect, name))
        except Exception:
            pass
        try:
            for child in control.children():
                _find_bank_items(child, out_list)
        except Exception:
            pass

    print("等待开户银行下拉列表...")
    end_time = time.time() + 6
    while time.time() < end_time:
        items = []
        _find_bank_items(main_win, items)
        if items:
            # 按Y坐标排序，取第一个（最上面的选项）
            items.sort(key=lambda x: x[0].top)
            first_rect, first_name = items[0]
            mx = first_rect.mid_point().x
            my = first_rect.mid_point().y
            print(f"找到银行选项: {first_name}，位置({mx}, {my})")
            click_at(mx, my)
            print(f"已选择: {first_name}")
            return True
        time.sleep(0.3)

    print("未检测到开户银行下拉列表选项")
    return False

def _double_click_first_branch_option(main_win):
    """输入支行名称后，等待下拉列表出现，双击第一个选项（定位到'行'字附近）"""

    def _find_branch_dropdown_items(control, out_list):
        """递归查找支行下拉列表中的单独选项项"""
        try:
            if not control.is_visible():
                return
        except Exception:
            return
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            ctrl_type = str(control.element_info.control_type) if hasattr(control.element_info, "control_type") else ""
            cls = control.element_info.class_name if hasattr(control.element_info, "class_name") else ""
            rect = control.rectangle()
            # 匹配单行高度的"支行"文本选项（排除容器聚合文本）
            name_len = len(name.strip()) if name else 0
            # 单个支行名称通常 10~40 字符；容器会包含大量文字（>100字符）
            is_single_item = 5 <= name_len <= 60
            is_single_row = 15 <= rect.height() <= 55 and rect.width() > 80
            if name and "支行" in name and is_single_item and is_single_row:
                out_list.append((rect, name, ctrl_type))
        except Exception:
            pass
        try:
            for child in control.children():
                _find_branch_dropdown_items(child, out_list)
        except Exception:
            pass

    print("等待支行下拉列表...")
    end_time = time.time() + 8
    while time.time() < end_time:
        items = []
        _find_branch_dropdown_items(main_win, items)
        if items:
            # 按Y坐标排序，取第一个（最上面的选项）
            items.sort(key=lambda x: x[0].top)
            first_rect, first_name, first_type = items[0]
            mx = first_rect.mid_point().x + 60  # 偏右，靠近"行"字位置
            my = first_rect.mid_point().y
            print(f"找到支行选项列表，第一项[{first_type}]: {first_name}，位置({mx}, {my})")

            # 双击：第一次点击
            click_at(mx, my)
            time.sleep(0.15)
            # 第二次点击
            click_at(mx, my)
            print(f"已双击选择: {first_name}")
            return True
        time.sleep(0.3)

    print("未检测到支行下拉列表选项")
    return False

def _input_bank_name(main_win, bank_name):
    """专门处理开户银行输入：复用通用查找 -> 逐字键入 -> 点击下拉选项"""
    bank_name = (bank_name or "").strip()
    if not bank_name:
        # fail-closed：空开户银行会让 bank_name[:3]="" 命中下拉里任意条目，
        # 同时 _click_bank_option 的几何兜底也会按坐标点击第一条结果，等同于
        # 把任意银行写进表单。直接终止本次制单。
        print("[fail-closed] 开户银行为空，拒绝触发开户银行下拉/兜底逻辑")
        _screenshot("02_开户银行为空")
        return False
    print(f"开始填写开户银行: {bank_name}")

    # 截图：填写前
    _screenshot("01_开户银行填前")

    # 1. 用通用函数查找并聚焦输入框
    if not find_and_input_text(main_win, "开户银行", ""):
        _screenshot("02_开户银行定位失败")
        print("开户银行输入框定位失败，终止本次制单")
        return False
    time.sleep(0.5)

    current_bank_text = _read_nearby_input_text(main_win, "开户银行")
    bank_already_present = (
        _compact_choice_text(bank_name)
        and _compact_choice_text(bank_name) in _compact_choice_text(current_bank_text)
    )

    # 2. 逐字键入银行名称，不走清空/剪贴板路径。
    if bank_already_present:
        print(f"开户银行已自动带出目标值，跳过键入: {current_bank_text}")
    else:
        _type_text_slow(bank_name, char_delay=0.1)
        print(f"已输入开户银行: {bank_name}")
    time.sleep(1.5)

    # 截图：填入后
    _screenshot("02_开户银行填后")
    bank_text_after_trim, bank_trimmed = _trim_repeated_bank_value(main_win, bank_name)

    # 4. 等待下拉并点击银行选项（而不是直接回车）
    # 招商银行在该 ComboBox 中输入完整名称后已经触发识别；再点下拉会把
    # “招商银行”追加一遍，变成“招商银行招商银行”。
    bank_selected = False
    is_merchants_bank = _compact_choice_text(bank_name) == "招商银行"
    if is_merchants_bank:
        print("开户银行为招商银行，跳过下拉点击以避免重复追加银行名称")
    else:
        bank_selected = _click_bank_option(main_win, bank_name)
    time.sleep(0.5)

    if is_merchants_bank and bank_trimmed:
        print(f"招商银行已从重复值修正，UIA 读值 [{bank_text_after_trim}] 可能回落为标签名，跳过程序化包含校验")
    else:
        if not _verify_field_contains(main_win, "开户银行", bank_name, "02_开户银行校验失败"):
            if not bank_selected:
                return False
            print("开户银行已执行下拉选择，但程序化校验未读到输入框值；继续支行流程")

        if not _verify_merchants_bank_exact(main_win, bank_name):
            return False

    # 5. Tab 切走焦点
    press_keys((0x09, 0), (0x09, 2))
    time.sleep(0.3)

    print(f"开户银行填写完成")
    return True

def _click_bank_option(main_win, bank_name):
    """在开户银行下拉列表中找到并点击银行选项"""
    bank_name = (bank_name or "").strip()
    if not bank_name:
        # fail-closed：bank_name[:3]=="" 会让递归扫描里的 `bank_name[:3] in name`
        # 永远成立，等于在任意下拉里点第一条；几何兜底也会盲点。直接拒绝。
        print("[fail-closed] _click_bank_option 收到空 bank_name，拒绝下拉/坐标兜底")
        return False

    def _click_first_visible_row_by_geometry():
        try:
            hwnd = win32gui.GetForegroundWindow()
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = right - left
            height = bottom - top
            # U-BANK 下拉浮层第一条银行结果位于开户银行输入框下方。
            x = left + int(width * 0.40)
            y = top + int(height * 0.56)
            click_at(x, y)
            print(f"开户银行下拉项 UIA 未命中，已按坐标兜底点击第一条结果: ({x}, {y})")
            return True
        except Exception as e:
            print(f"开户银行下拉项坐标兜底失败: {e}")
            return False

    def _find_bank_items(control, out_list):
        try:
            if not control.is_visible():
                return
        except Exception:
            return
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            ctrl_type = str(control.element_info.control_type) if hasattr(control.element_info, "control_type") else ""
            rect = control.rectangle()
            # 银行名较短(2~25字)，排除输入框本身
            is_input = any(kw in ctrl_type for kw in ["Edit", "Document"])
            name_len = len(name.strip()) if name else 0
            is_single_row = 15 <= rect.height() <= 55 and rect.width() > 60
            if not is_input and name and 2 <= name_len <= 25 and is_single_row and bank_name[:3] in name:
                out_list.append((rect, name))
        except Exception:
            pass
        try:
            for child in control.children():
                _find_bank_items(child, out_list)
        except Exception:
            pass

    end_time = time.time() + 6
    desktop = Desktop(backend="uia")
    while time.time() < end_time:
        items = []
        _find_bank_items(main_win, items)
        try:
            for window in desktop.windows():
                _find_bank_items(window, items)
        except Exception:
            pass
        if items:
            items.sort(key=lambda x: x[0].top)
            r, n = items[0]
            click_at(r.mid_point().x, r.mid_point().y)
            print(f"已选择开户银行下拉项: {n}")
            return True
        time.sleep(0.3)

    print("未找到开户银行下拉项，尝试坐标点击第一条结果")
    if _click_first_visible_row_by_geometry():
        return True
    press_keys((0x0D, 0), (0x0D, 2))
    time.sleep(0.3)
    return False

def _check_skip_branch(main_win):
    """检测支行输入框是否显示'无需填写'提示或被禁用，若是则跳过"""

    def _find_branch_input(control):
        """查找支行名称/联行号输入框"""
        nonlocal branch_edit
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            ctrl_type = str(control.element_info.control_type) if hasattr(control.element_info, "control_type") else ""
            cls = control.element_info.class_name if hasattr(control.element_info, "class_name") else ""
            is_edit = any(kw in cls for kw in ["Edit", "edit"]) or any(kw in ctrl_type for kw in ["Edit", "Document"])
            if is_edit and ("联行号" in name or "支行名称" in name) and control.is_visible():
                rect = control.rectangle()
                if rect.width() > 50 and rect.height() > 5:
                    branch_edit = control
                    return True
        except Exception:
            pass
        try:
            for child in control.children():
                if _find_branch_input(child):
                    return True
        except Exception:
            pass
        return False

    def _scan_nearby_hints(control, rect_hint):
        """扫描支行输入框附近区域的所有可见文本，查找'无需填写'等提示"""
        try:
            if not control.is_visible():
                return
        except Exception:
            return
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            ctrl_type = str(control.element_info.control_type) if hasattr(control.element_info, "control_type") else ""
            cls = control.element_info.class_name if hasattr(control.element_info, "class_name") else ""
            # 只看非编辑类控件（Text、Static、Hyperlink等）
            is_edit = any(kw in cls for kw in ["Edit", "edit"]) or any(kw in ctrl_type for kw in ["Edit", "Document", "ComboBox"])
            if not is_edit and name and name.strip():
                try:
                    r = control.rectangle()
                    # 检查是否在支行框下方（Y坐标接近）且在合理范围内
                    y_diff = abs(r.top - rect_hint[1])
                    if 0 <= y_diff <= 40 and abs(r.left - rect_hint[0]) < 300:
                        if "无需填写" in name or "则无需" in name:
                            print(f"扫描到支行提示文本: {name}")
                            raise StopIteration()
                except StopIteration:
                    raise
                except Exception:
                    pass
        except StopIteration:
            raise
        except Exception:
            pass
        try:
            for child in control.children():
                _scan_nearby_hints(child, rect_hint)
        except StopIteration:
            raise
        except Exception:
            pass

    branch_edit = None
    _find_branch_input(main_win)

    # 方案1：找不到支行输入框 -> 跳过
    if not branch_edit:
        print("未找到支行输入框，跳过")
        return True

    try:
        br_rect = branch_edit.rectangle()

        # 方案2：检测输入框是否被禁用（招商银行时支行框变灰）
        try:
            enabled = branch_edit.is_enabled() if hasattr(branch_edit, "is_enabled") else True
            if not enabled:
                print("支行输入框已禁用，跳过")
                return True
        except Exception:
            pass

        # 方案3：获取输入框值/名称中的提示词
        val = ""
        try:
            val = branch_edit.get_value() if hasattr(branch_edit, "get_value") else ""
        except Exception:
            pass
        name = branch_edit.element_info.name if hasattr(branch_edit.element_info, "name") else ""
        combined = (val + " " + name).lower()

        for h in ["无需填写", "则无需"]:
            if h in combined:
                print(f"支行框内容含'{h}'，跳过")
                return True

        # 方案4：扫描支行框附近的可见文本（找placeholder提示）
        hint_rect = (br_rect.left, br_rect.bottom + 5)
        try:
            _scan_nearby_hints(main_win, hint_rect)
        except StopIteration:
            print("检测到支行区域'无需填写'提示，跳过")
            return True

    except Exception as e:
        print(f"检测支行状态异常: {e}")

    print("支行需要填写")
    return False

def _input_branch_name(main_win, label_text, text_value):
    """精确查找支行输入框并填入：只匹配包含'联行号'的标签，避免误匹配其他字段"""

    def _find_exact_label(control):
        """精确查找支行标签（必须含'联行号'关键字）"""
        nonlocal label_ctrl
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            ctrl_type = str(control.element_info.control_type) if hasattr(control.element_info, "control_type") else ""
            cls = control.element_info.class_name if hasattr(control.element_info, "class_name") else ""
            # 排除输入框本身，只找文本标签
            is_edit = any(kw in cls for kw in ["Edit", "edit"]) or any(kw in ctrl_type for kw in ["Edit", "Document"])
            # 支宽匹配：含"支行名称"或"联行号"即可
            if not is_edit and ("联行号" in name or "支行名称" in name) and control.is_visible():
                rect = control.rectangle()
                if rect.width() > 3 and rect.height() > 3:
                    label_ctrl = control
                    return True
        except Exception:
            pass
        try:
            for child in control.children():
                if _find_exact_label(child):
                    return True
        except Exception:
            pass
        return False

    def _find_nearby_edit(label):
        """从标签旁找编辑框"""
        candidates = []
        try:
            parent = label.parent()
            if parent is None:
                return None
            ly = label.rectangle().top
            for sib in parent.children():
                if not sib.is_visible():
                    continue
                stype = str(sib.element_info.control_type) if hasattr(sib.element_info, "control_type") else ""
                scls = sib.element_info.class_name if hasattr(sib.element_info, "class_name") else ""
                is_edit = any(kw in scls for kw in ["Edit", "edit"]) or any(kw in stype for kw in ["Edit", "ComboBox", "Combo"])
                if is_edit:
                    sr = sib.rectangle()
                    yd = abs(sr.top - ly)
                    candidates.append((sib, yd))
        except Exception:
            pass
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]

    # 查找标签
    label_ctrl = None
    _find_exact_label(main_win)
    if not label_ctrl:
        print(f"未找到支行标签[{label_text}]")
        return False

    # 找输入框
    edit_ctrl = _find_nearby_edit(label_ctrl)
    if not edit_ctrl:
        print("未找到支行输入框")
        return False

    # 点击聚焦 -> 清空 -> 逐字键入
    edit_ctrl.set_focus()
    time.sleep(0.4)
    _ctrl_a_backspace()
    _type_text_slow(text_value, char_delay=0.09)
    time.sleep(0.5)
    print(f"已输入 [{label_text}]: {text_value}")
    return True


def _find_branch_input_rect(main_win):
    def _walk(control):
        try:
            if not control.is_visible():
                return None
        except Exception:
            return None
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            ctrl_type = str(control.element_info.control_type) if hasattr(control.element_info, "control_type") else ""
            cls = control.element_info.class_name if hasattr(control.element_info, "class_name") else ""
            is_edit = any(kw in cls for kw in ["Edit", "edit"]) or any(kw in ctrl_type for kw in ["Edit", "Document", "ComboBox", "Combo"])
            if is_edit and ("联行号" in name or "支行名称" in name):
                rect = control.rectangle()
                if rect.width() > 80 and rect.height() > 10:
                    return rect
        except Exception:
            pass
        try:
            for child in control.children():
                rect = _walk(child)
                if rect is not None:
                    return rect
        except Exception:
            pass
        return None

    return _walk(main_win)


def _click_query_branch_button(main_win):
    """Click the visible 查询支行 button, if U-BANK exposes one."""
    candidates = []
    try:
        main_process_id = main_win.element_info.process_id
    except Exception:
        main_process_id = None

    def _walk(control):
        try:
            if not control.is_visible():
                return
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            ctrl_type = str(control.element_info.control_type) if hasattr(control.element_info, "control_type") else ""
            cls = control.element_info.class_name if hasattr(control.element_info, "class_name") else ""
            rect = control.rectangle()
            if "查询支行" in name and rect.width() > 8 and rect.height() > 8:
                buttonish = "Button" in ctrl_type or "Button" in cls
                area = rect.width() * rect.height()
                candidates.append((0 if buttonish else 1, area, rect.top, rect.left, control, name, ctrl_type, cls, rect))
        except Exception:
            pass
        try:
            for child in control.children():
                _walk(child)
        except Exception:
            pass

    _walk(main_win)
    try:
        desktop = Desktop(backend="uia")
        for window in desktop.windows():
            try:
                if main_process_id is not None and window.element_info.process_id != main_process_id:
                    continue
            except Exception:
                continue
            _walk(window)
    except Exception:
        pass

    if not candidates:
        print("[支行直输策略] 未找到“查询支行”按钮")
        return False

    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    _pri, _area, _top, _left, control, name, ctrl_type, cls, rect = candidates[0]
    print(
        f"[支行直输策略] 找到“查询支行”候选: name={name!r}, type={ctrl_type}, "
        f"class={cls}, rect=({rect.left},{rect.top},{rect.right},{rect.bottom})"
    )
    try:
        if "Button" in ctrl_type or "Button" in cls:
            control.invoke()
            print("[支行直输策略] 已通过 UIA invoke 点击“查询支行”")
            time.sleep(1.0)
            return True
    except Exception as e:
        print(f"[支行直输策略] invoke 查询支行失败，改用坐标点击: {e}")

    click_at(rect.mid_point().x, rect.mid_point().y)
    print("[支行直输策略] 已通过坐标点击“查询支行”")
    time.sleep(1.0)
    return True


def _find_query_dialog_region_controls(main_win):
    """Find province/city controls inside the 查询支行 dialog."""
    label_controls = []
    controls = []
    try:
        main_process_id = main_win.element_info.process_id
    except Exception:
        main_process_id = None

    def _walk(control):
        try:
            if not control.is_visible():
                return
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            ctrl_type = str(control.element_info.control_type) if hasattr(control.element_info, "control_type") else ""
            cls = control.element_info.class_name if hasattr(control.element_info, "class_name") else ""
            rect = control.rectangle()
            if "开户地" in name and 250 <= rect.top <= 380:
                label_controls.append((rect, control, name))
            is_input = any(kw in cls for kw in ["Edit", "edit", "Combo"]) or any(
                kw in ctrl_type for kw in ["Edit", "Document", "ComboBox", "Combo"]
            )
            if is_input and 250 <= rect.top <= 380 and 60 <= rect.width() <= 220 and 18 <= rect.height() <= 45:
                controls.append((rect.left, rect, control, name, ctrl_type, cls))
        except Exception:
            pass
        try:
            for child in control.children():
                _walk(child)
        except Exception:
            pass

    _walk(main_win)
    try:
        desktop = Desktop(backend="uia")
        for window in desktop.windows():
            try:
                if main_process_id is not None and window.element_info.process_id != main_process_id:
                    continue
            except Exception:
                continue
            _walk(window)
    except Exception:
        pass

    filtered = []
    if label_controls:
        label_rect, _label_ctrl, _label_name = sorted(label_controls, key=lambda item: (item[0].top, item[0].left))[0]
        for _left, rect, control, name, ctrl_type, cls in controls:
            same_row = abs(rect.mid_point().y - label_rect.mid_point().y) <= 24
            right_side = label_rect.right <= rect.left <= label_rect.right + 430
            if same_row and right_side:
                filtered.append((rect.left, rect, control, name, ctrl_type, cls))
    else:
        for item in controls:
            _left, rect, _control, name, _ctrl_type, _cls = item
            if _compact_choice_text(name) in {"省", "市"}:
                filtered.append(item)

    unique = []
    seen = set()
    for _left, rect, control, name, ctrl_type, cls in sorted(filtered, key=lambda item: item[0]):
        key = (round(rect.left / 5) * 5, round(rect.top / 5) * 5, round(rect.right / 5) * 5)
        if key in seen:
            continue
        seen.add(key)
        unique.append((rect, control, name, ctrl_type, cls))
    return unique[:2]


def _find_query_branch_dialog_rect(main_win):
    """Locate the 支行检索 modal rectangle, falling back to the known U-BANK modal layout."""
    rects = []
    try:
        main_process_id = main_win.element_info.process_id
    except Exception:
        main_process_id = None

    def _walk(control):
        try:
            if not control.is_visible():
                return
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            rect = control.rectangle()
            if name and any(token in name for token in ("支行检索", "支行关键字", "开户地")):
                if rect.width() > 5 and rect.height() > 5:
                    rects.append(rect)
        except Exception:
            pass
        try:
            for child in control.children():
                _walk(child)
        except Exception:
            pass

    _walk(main_win)
    try:
        desktop = Desktop(backend="uia")
        for window in desktop.windows():
            try:
                if main_process_id is not None and window.element_info.process_id != main_process_id:
                    continue
            except Exception:
                continue
            _walk(window)
    except Exception:
        pass

    if rects:
        left = min(rect.left for rect in rects)
        top = min(rect.top for rect in rects)
        right = max(rect.right for rect in rects)
        bottom = max(rect.bottom for rect in rects)
        width = max(900, right - left)
        height = max(520, bottom - top)
        return _SimpleRect(left - 24, top - 36, left - 24 + width + 48, top - 36 + height + 90)

    try:
        mr = main_win.rectangle()
        mw = mr.width()
        mh = mr.height()
        # The U-BANK 支行检索 modal is centered and scales with the main window.
        return _SimpleRect(
            mr.left + int(mw * 0.224),
            mr.top + int(mh * 0.248),
            mr.left + int(mw * 0.777),
            mr.top + int(mh * 0.816),
        )
    except Exception:
        return None


def _query_dialog_geometry_region_controls(main_win):
    dialog_rect = _find_query_branch_dialog_rect(main_win)
    if dialog_rect is None:
        return []
    dw = dialog_rect.width()
    dh = dialog_rect.height()
    row_y = dialog_rect.top + int(dh * 0.115)
    field_h = max(26, int(dh * 0.05))
    province = _SimpleRect(
        dialog_rect.left + int(dw * 0.525),
        row_y - int(field_h * 0.5),
        dialog_rect.left + int(dw * 0.662),
        row_y + int(field_h * 0.5),
    )
    city = _SimpleRect(
        dialog_rect.left + int(dw * 0.675),
        row_y - int(field_h * 0.5),
        dialog_rect.left + int(dw * 0.807),
        row_y + int(field_h * 0.5),
    )
    return [
        (province, None, "省(geometry)", "ComboBox", "geometry"),
        (city, None, "市(geometry)", "ComboBox", "geometry"),
    ]


def _select_visible_dropdown_value(main_win, variants, control_rect):
    """Select a visible dropdown item matching one of variants near a combo."""
    variant_keys = {_compact_choice_text(v) for v in variants if _compact_choice_text(v)}
    if not variant_keys:
        return False
    candidates = []
    try:
        main_process_id = main_win.element_info.process_id
    except Exception:
        main_process_id = None

    def _walk(control):
        try:
            if not control.is_visible():
                return
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            ctrl_type = str(control.element_info.control_type) if hasattr(control.element_info, "control_type") else ""
            cls = control.element_info.class_name if hasattr(control.element_info, "class_name") else ""
            rect = control.rectangle()
            is_input = any(token in ctrl_type or token in cls for token in ("Edit", "ComboBox", "Document"))
            key = _compact_choice_text(name)
            close_to_combo = (
                control_rect.left - 30 <= rect.left <= control_rect.right + 120
                and control_rect.top - 80 <= rect.top <= control_rect.bottom + 360
            )
            if name and key in variant_keys and not is_input and close_to_combo and rect.width() > 20 and rect.height() > 10:
                candidates.append((rect.top, rect.left, rect, name))
        except Exception:
            pass
        try:
            for child in control.children():
                _walk(child)
        except Exception:
            pass

    _walk(main_win)
    try:
        desktop = Desktop(backend="uia")
        for window in desktop.windows():
            try:
                if main_process_id is not None and window.element_info.process_id != main_process_id:
                    continue
            except Exception:
                continue
            _walk(window)
    except Exception:
        pass

    if not candidates:
        return False
    _top, _left, rect, name = sorted(candidates, key=lambda item: (item[0], item[1]))[0]
    click_at(rect.mid_point().x, rect.mid_point().y)
    print(f"[支行直输策略] 已点击开户地址下拉项: {name}")
    time.sleep(0.5)
    return True


def _input_query_branch_dialog_region(main_win, province="", city=""):
    """Fill province/city filters inside the 支行检索 dialog."""
    province_variants = province_filter_variants(province)
    city_variants = city_filter_variants(city)
    if not province_variants and not city_variants:
        return False

    controls = _find_query_dialog_region_controls(main_win)
    if not controls:
        controls = _query_dialog_geometry_region_controls(main_win)
        if controls:
            print("[支行直输策略] UIA 未暴露查询弹窗省/市控件，改用弹窗相对位置兜底")
        else:
            print("[支行直输策略] 查询支行弹窗未定位到开户地址省/市控件")
            return False

    changed = False
    for idx, (variants, label) in enumerate(((province_variants, "省"), (city_variants, "市"))):
        if not variants or idx >= len(controls):
            continue
        rect, control, name, ctrl_type, cls = controls[idx]
        text = variants[0]
        print(
            f"[支行直输策略] 尝试填写查询弹窗开户地址{label}: {text} "
            f"(控件名={name or '-'}, type={ctrl_type}, class={cls})"
        )
        if control is not None:
            try:
                control.set_focus()
                time.sleep(0.15)
            except Exception:
                pass
        click_at(rect.mid_point().x, rect.mid_point().y)
        time.sleep(0.25)
        _ctrl_a_backspace()
        _type_text_slow(text, char_delay=0.06)
        time.sleep(0.5)
        selected = _select_visible_dropdown_value(main_win, variants, rect)
        if not selected:
            press_keys((0x0D, 0), (0x0D, 2))
            time.sleep(0.5)
        changed = True

    if changed:
        _screenshot("05_查询支行弹窗省市过滤后")
    return changed


def _click_query_branch_dialog_search(main_win):
    """Click the blue 查询 button inside the 支行检索 dialog."""
    candidates = []
    try:
        main_process_id = main_win.element_info.process_id
    except Exception:
        main_process_id = None

    def _walk(control):
        try:
            if not control.is_visible():
                return
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            ctrl_type = str(control.element_info.control_type) if hasattr(control.element_info, "control_type") else ""
            cls = control.element_info.class_name if hasattr(control.element_info, "class_name") else ""
            rect = control.rectangle()
            normalized = _compact_choice_text(name)
            is_buttonish = "Button" in ctrl_type or "Button" in cls
            if normalized == "查询" and is_buttonish and 250 <= rect.top <= 390 and rect.width() > 30 and rect.height() > 15:
                candidates.append((rect.top, -rect.left, rect, control, name, ctrl_type, cls))
        except Exception:
            pass
        try:
            for child in control.children():
                _walk(child)
        except Exception:
            pass

    _walk(main_win)
    try:
        desktop = Desktop(backend="uia")
        for window in desktop.windows():
            try:
                if main_process_id is not None and window.element_info.process_id != main_process_id:
                    continue
            except Exception:
                continue
            _walk(window)
    except Exception:
        pass

    if not candidates:
        dialog_rect = _find_query_branch_dialog_rect(main_win)
        if dialog_rect is None:
            print("[支行直输策略] 查询支行弹窗未找到“查询”按钮")
            return False
        x = dialog_rect.left + int(dialog_rect.width() * 0.949)
        y = dialog_rect.top + int(dialog_rect.height() * 0.115)
        print(f"[支行直输策略] UIA 未找到查询弹窗“查询”按钮，按弹窗相对位置点击: ({x}, {y})")
        click_at(x, y)
        time.sleep(1.2)
        _screenshot("05_查询支行弹窗重新查询后")
        return True
    _top, _neg_left, rect, control, name, ctrl_type, cls = sorted(candidates, key=lambda item: (item[0], item[1]))[0]
    print(
        f"[支行直输策略] 找到查询弹窗“查询”按钮: name={name!r}, type={ctrl_type}, "
        f"class={cls}, rect=({rect.left},{rect.top},{rect.right},{rect.bottom})"
    )
    try:
        control.invoke()
        print("[支行直输策略] 已通过 UIA invoke 点击查询弹窗“查询”")
    except Exception as e:
        print(f"[支行直输策略] invoke 查询弹窗“查询”失败，改用坐标点击: {e}")
        click_at(rect.mid_point().x, rect.mid_point().y)
    time.sleep(1.2)
    _screenshot("05_查询支行弹窗重新查询后")
    return True


def _try_select_query_branch_result(main_win, text_value="", bank_head="", region=None, attempt_label=""):
    """After 查询支行, select a safe matching result from a popup/list if one appears."""
    target_norm = _compact_choice_text(text_value)
    if not target_norm:
        print("[fail-closed] 查询支行结果选择被禁用：text_value 为空")
        return False, False

    allow_region_branch = False
    attempt_suffix = _safe_screenshot_label_part(attempt_label)

    def _shot(label):
        if attempt_suffix:
            return _screenshot(f"{label}_{attempt_suffix}")
        return _screenshot(label)

    try:
        main_process_id = main_win.element_info.process_id
    except Exception:
        main_process_id = None

    def _unique_names(candidates):
        names = []
        seen = set()
        for _rect, name in candidates:
            text = str(name or "").strip()
            key = _compact_choice_text(text)
            if not text or key in seen:
                continue
            seen.add(key)
            names.append(text)
        return names

    def _print_report(prefix, candidates):
        names = _unique_names(candidates)
        exact_names = [name for name in names if _compact_choice_text(name) == target_norm]
        safe_names = [
            name
            for name in names
            if _compact_choice_text(name) != target_norm and _is_safe_branch_equivalent(text_value, name)
        ]
        hierarchy_names = [
            name
            for name in names
            if _compact_choice_text(name) != target_norm
            and not _is_safe_branch_equivalent(text_value, name)
            and _branch_hierarchy_match(name, bank_head, region)
        ]
        print(
            f"{prefix} target=[{text_value}], exact_found={bool(exact_names)}, "
            f"safe_equivalent_found={bool(safe_names)}, hierarchy_found={bool(hierarchy_names)}, "
            f"hierarchy_auto_allowed={allow_region_branch}, candidates={len(names)}"
        )
        for idx, name in enumerate(names[:60], start=1):
            if _compact_choice_text(name) == target_norm:
                mark = " *EXACT*"
            elif _is_safe_branch_equivalent(text_value, name):
                mark = " *SAFE_EQUIV*"
            else:
                hierarchy_kind = _branch_hierarchy_match(name, bank_head, region)
                mark = f" *HIERARCHY:{hierarchy_kind}*" if hierarchy_kind else ""
            print(f"{prefix} candidate#{idx}: {name}{mark}")
        if len(names) > 60:
            print(f"{prefix} candidate_more={len(names) - 60}")

    def _find_result_items(control, out_list):
        try:
            if not control.is_visible():
                return
        except Exception:
            return
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            ctrl_type = str(control.element_info.control_type) if hasattr(control.element_info, "control_type") else ""
            cls = control.element_info.class_name if hasattr(control.element_info, "class_name") else ""
            rect = control.rectangle()
            normalized = _compact_choice_text(name)
            is_input_or_button = any(
                token in ctrl_type or token in cls
                for token in ("Edit", "ComboBox", "Document", "Button")
            )
            is_branch_like = _has_branch_marker(name)
            reasonable = 5 <= len(normalized) <= 80 and 14 <= rect.height() <= 60 and rect.width() >= 80
            not_window_title = normalized not in {"查询支行", "支行名称/联行号"}
            if name and not is_input_or_button and is_branch_like and reasonable and not_window_title:
                out_list.append((rect, name))
        except Exception:
            pass
        try:
            for child in control.children():
                _find_result_items(child, out_list)
        except Exception:
            pass

    print("[支行直输策略] 检测查询支行结果列表...")
    end_time = time.time() + 6
    desktop = Desktop(backend="uia")
    last_items = []
    while time.time() < end_time:
        items = []
        _find_result_items(main_win, items)
        try:
            for window in desktop.windows():
                try:
                    if main_process_id is not None and window.element_info.process_id != main_process_id:
                        continue
                except Exception:
                    continue
                _find_result_items(window, items)
        except Exception:
            pass

        if items:
            items.sort(key=lambda item: (item[0].top, item[0].left))
            last_items = items

        exact_matches = [
            (rect, name)
            for rect, name in items
            if _compact_choice_text(name) == target_norm
        ]
        safe_matches = [
            (rect, name)
            for rect, name in items
            if _compact_choice_text(name) != target_norm and _is_safe_branch_equivalent(text_value, name)
        ]
        hierarchy_matches = [
            (rect, name, _branch_hierarchy_match(name, bank_head, region))
            for rect, name in items
            if _compact_choice_text(name) != target_norm
            and not _is_safe_branch_equivalent(text_value, name)
            and _branch_hierarchy_match(name, bank_head, region)
        ]

        if exact_matches:
            rect, name = sorted(exact_matches, key=lambda item: (item[0].top, item[0].left))[0]
            print(f"[支行直输策略] 查询支行结果命中精确匹配: {name}")
            click_at(rect.mid_point().x, rect.mid_point().y)
            time.sleep(0.15)
            click_at(rect.mid_point().x, rect.mid_point().y)
            print(f"[支行直输策略] 已双击选择查询支行结果: {name}")
            return True, False

        if safe_matches:
            safe_names = _unique_names(safe_matches)
            if len(safe_names) == 1:
                rect, name = sorted(safe_matches, key=lambda item: (item[0].top, item[0].left))[0]
                print(f"[支行直输策略] 查询支行结果命中安全等价匹配: {name}")
                click_at(rect.mid_point().x, rect.mid_point().y)
                time.sleep(0.15)
                click_at(rect.mid_point().x, rect.mid_point().y)
                print(f"[支行直输策略] 已双击选择查询支行安全等价结果: {name}")
                return True, False

        hierarchy_names = _unique_names([(rect, name) for rect, name, _kind in hierarchy_matches])
        if allow_region_branch and len(hierarchy_names) == 1:
            hierarchy_target = hierarchy_names[0]
            rect, name, kind = [
                item for item in hierarchy_matches if _compact_choice_text(item[1]) == _compact_choice_text(hierarchy_target)
            ][0]
            print(f"[支行直输策略] 查询支行结果命中同地区层级匹配({kind}): {name}")
            click_at(rect.mid_point().x, rect.mid_point().y)
            time.sleep(0.15)
            click_at(rect.mid_point().x, rect.mid_point().y)
            print(f"[支行直输策略] 已双击选择查询支行层级匹配结果: {name}")
            return True, False

        time.sleep(0.3)

    _shot("05_查询支行无安全结果")
    if last_items:
        _print_report("[支行直输策略]", last_items)
    else:
        print("[支行直输策略] 查询支行后未检测到可选择的支行结果列表")
    return False, False

def _close_branch_dropdown_if_open(reason=""):
    """Close branch suggestion dropdowns without selecting any candidate."""
    label = f" ({reason})" if reason else ""
    print(f"收起支行候选下拉{label}")
    for _ in range(2):
        press_keys((0x1B, 0), (0x1B, 2))  # Esc
        time.sleep(0.2)

def _try_select_branch_dropdown(main_win, text_value="", attempt_label="", bank_head="", region=None):
    """检测支行下拉列表，只选择真正的选项（严格排除Edit/输入框控件）。

    返回 (ok: bool, fallback_used: bool, match_info: dict)：
    - 精确匹配命中（含村镇银行"开户银行=支行全称"场景）→ (True, False, match_info)
    - 唯一已核验安全等价匹配命中 → (True, False, match_info)
    - 同银行且同区县/市/省的唯一上级网点候选 → (True, False, match_info)
    - 没有精确或安全等价匹配 → 截图、打印候选并 fail-closed，不再选择第一条候选。
    - text_value 为空（无目标） → (False, False, {})，禁止走"选第一条"。
    - 设置 ZHIDAN_BRANCH_PROBE=1 时，只探测并打印候选，不点击任何支行选项。

    所有拒绝路径都打印 [fail-closed] / [支行探测] 日志并截图，便于从 batch log 复核。
    """
    target_norm = _compact_choice_text(text_value)
    if not target_norm:
        print("[fail-closed] 支行下拉自动选择被禁用：text_value 为空")
        _screenshot("05_支行下拉_空目标_拒绝")
        return False, False, {}
    target_has_branch = _has_branch_marker(target_norm)
    probe_mode = os.getenv("ZHIDAN_BRANCH_PROBE", "0") == "1"
    allow_region_branch = os.getenv("ZHIDAN_ALLOW_REGION_BRANCH_MATCH", "1") != "0"
    attempt_suffix = _safe_screenshot_label_part(attempt_label)

    def _shot(label):
        if attempt_suffix:
            return _screenshot(f"{label}_{attempt_suffix}")
        return _screenshot(label)

    def _unique_candidate_names(candidates):
        names = []
        seen = set()
        for _rect, name in candidates:
            text = str(name or "").strip()
            key = _compact_choice_text(text)
            if not text or key in seen:
                continue
            seen.add(key)
            names.append(text)
        return names

    def _print_candidate_report(prefix, all_candidates, branch_candidates):
        all_names = _unique_candidate_names(all_candidates)
        branch_names = _unique_candidate_names(branch_candidates)
        scored_names = [
            (
                name,
                score_branch_candidate(
                    text_value,
                    name,
                    bank_head=bank_head,
                    region=region,
                    allow_hierarchy=True,
                    allow_same_region_branch=os.getenv("ZHIDAN_ALLOW_SAME_REGION_BRANCH_MATCH", "0") == "1",
                    safe_aliases=_SAFE_BRANCH_ALIASES,
                ),
            )
            for name in all_names
        ]
        exact_names = [name for name, score in scored_names if score.kind == "exact"]
        safe_names = [name for name, score in scored_names if score.kind == "safe_equivalent"]
        hierarchy_names = [name for name, score in scored_names if score.accepted_hierarchy]
        print(
            f"{prefix} target=[{text_value}], exact_found={bool(exact_names)}, "
            f"safe_equivalent_found={bool(safe_names)}, "
            f"hierarchy_found={bool(hierarchy_names)}, "
            f"hierarchy_auto_allowed={allow_region_branch}, "
            f"all_candidates={len(all_names)}, branch_candidates={len(branch_names)}"
        )
        for i, (name, score) in enumerate(scored_names[:50], start=1):
            if score.kind == "exact":
                mark = " *EXACT*"
            elif score.kind == "safe_equivalent":
                mark = f" *SAFE_EQUIV:{score.score}*"
            elif score.accepted_hierarchy:
                mark = f" *HIERARCHY:{score.kind}:{score.score}*"
            elif score.kind == "weak":
                mark = f" *WEAK:{score.score}*"
            else:
                mark = ""
            print(f"{prefix} candidate#{i}: {name}{mark}")
        if len(all_names) > 50:
            print(f"{prefix} candidate_more={len(all_names) - 50}")

    def _find_branch_input_rect(control):
        try:
            if not control.is_visible():
                return None
        except Exception:
            return None
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            ctrl_type = str(control.element_info.control_type) if hasattr(control.element_info, "control_type") else ""
            cls = control.element_info.class_name if hasattr(control.element_info, "class_name") else ""
            is_edit = any(kw in cls for kw in ["Edit", "edit"]) or any(kw in ctrl_type for kw in ["Edit", "Document", "ComboBox", "Combo"])
            if is_edit and ("联行号" in name or "支行名称" in name):
                rect = control.rectangle()
                if rect.width() > 80 and rect.height() > 10:
                    return rect
        except Exception:
            pass
        try:
            for child in control.children():
                rect = _find_branch_input_rect(child)
                if rect is not None:
                    return rect
        except Exception:
            pass
        return None

    branch_input_rect = _find_branch_input_rect(main_win)
    try:
        main_process_id = main_win.element_info.process_id
    except Exception:
        main_process_id = None

    def _within_branch_dropdown(rect):
        if branch_input_rect is None:
            return False
        return (
            rect.top >= branch_input_rect.bottom - 8
            and rect.top <= branch_input_rect.bottom + 340
            and rect.left >= branch_input_rect.left - 40
            and rect.left <= branch_input_rect.right + 160
        )

    def _find_valid_branch_items(control, out_list, all_items):
        """递归查找支行下拉选项（排除输入框自身）"""
        try:
            if not control.is_visible():
                return
        except Exception:
            return
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            ctrl_type = str(control.element_info.control_type) if hasattr(control.element_info, "control_type") else ""
            cls = control.element_info.class_name if hasattr(control.element_info, "class_name") else ""
            rect = control.rectangle()
            # 排除输入框自身，避免把当前已输入内容当作下拉候选。
            # 不要把 Document 当作输入框剪枝；U-BANK 的下拉项经常挂在
            # Document 容器下面，提前 return 会把真实选项一起跳过。
            is_input_ctrl = any(kw in ctrl_type for kw in ["Edit"]) or any(kw in cls for kw in ["Edit", "edit"])
            in_branch_dropdown = _within_branch_dropdown(rect)

            # 匹配条件：含"支行"、单行高度、合理宽度、非空名称
            name_len = len(name.strip()) if name else 0
            is_single_item = 5 <= name_len <= 60
            is_single_row = 15 <= rect.height() <= 55 and rect.width() > 80
            if not is_input_ctrl and name and in_branch_dropdown and is_single_item and is_single_row:
                all_items.append((rect, name))
            if not is_input_ctrl and name and in_branch_dropdown and _has_branch_marker(name) and is_single_item and is_single_row:
                out_list.append((rect, name))
        except Exception:
            pass
        try:
            for child in control.children():
                _find_valid_branch_items(child, out_list, all_items)
        except Exception:
            pass

    print("检测支行下拉列表...")
    end_time = time.time() + 6
    desktop = Desktop(backend="uia")
    last_branch_items = []
    last_all_items = []
    if branch_input_rect is None:
        print("[fail-closed] 未定位到支行输入框位置，拒绝扫描全桌面支行候选")
        _screenshot("05_支行下拉_未定位输入框_拒绝")
        return False, False, {}
    while time.time() < end_time:
        items = []
        all_items = []
        _find_valid_branch_items(main_win, items, all_items)
        try:
            for window in desktop.windows():
                try:
                    if main_process_id is not None and window.element_info.process_id != main_process_id:
                        continue
                except Exception:
                    continue
                _find_valid_branch_items(window, items, all_items)
        except Exception:
            pass
        all_items.sort(key=lambda x: x[0].top)
        items.sort(key=lambda x: x[0].top)

        if all_items:
            last_all_items = all_items

        scored_items = [
            (
                rect,
                name,
                score_branch_candidate(
                    text_value,
                    name,
                    bank_head=bank_head,
                    region=region,
                    allow_hierarchy=True,
                    allow_same_region_branch=os.getenv("ZHIDAN_ALLOW_SAME_REGION_BRANCH_MATCH", "0") == "1",
                    safe_aliases=_SAFE_BRANCH_ALIASES,
                ),
            )
            for rect, name in all_items
        ]
        exact_matches = [
            (rect, name, score)
            for rect, name, score in scored_items
            if score.kind == "exact"
        ]
        safe_matches = [
            (rect, name, score)
            for rect, name, score in scored_items
            if score.kind == "safe_equivalent"
        ]
        hierarchy_matches = [
            (rect, name, score.kind, score)
            for rect, name, score in scored_items
            if score.accepted_hierarchy
        ]

        if probe_mode and (all_items or items):
            _shot("05_支行探测_候选列表")
            _print_candidate_report("[支行探测]", all_items, items)
            print("[支行探测] 已完成候选探测，主动终止，不点击支行选项")
            return False, False, {}

        if exact_matches:
            first_rect, first_name, first_score = sorted(exact_matches, key=lambda x: (-x[2].score, x[0].top))[0]
            print(f"支行下拉命中精确匹配: {first_name}")
            mx = first_rect.mid_point().x + 60  # 偏右靠近"行"字
            my = first_rect.mid_point().y
            print(f"找到有效支行选项: {first_name}，位置({mx}, {my})")
            click_at(mx, my)
            time.sleep(0.15)
            click_at(mx, my)  # 双击
            print(f"已双击选择: {first_name}")
            return True, False, {"kind": "exact", "target": text_value, "selected": first_name, "score": first_score.score}

        if safe_matches:
            safe_names = _unique_candidate_names([(rect, name) for rect, name, _score in safe_matches])
            if len(safe_names) == 1:
                first_rect, first_name, first_score = sorted(safe_matches, key=lambda x: (-x[2].score, x[0].top))[0]
                print(
                    f"支行下拉命中安全等价匹配: target=[{text_value}], "
                    f"selected=[{first_name}], score={first_score.score}, reason={first_score.reason}"
                )
                mx = first_rect.mid_point().x + 60
                my = first_rect.mid_point().y
                print(f"找到安全等价支行选项: {first_name}，位置({mx}, {my})")
                click_at(mx, my)
                time.sleep(0.15)
                click_at(mx, my)
                print(f"已双击选择: {first_name}")
                return True, False, {
                    "kind": "safe_equivalent",
                    "target": text_value,
                    "selected": first_name,
                    "score": first_score.score,
                    "reason": first_score.reason,
                }
            print(
                f"[fail-closed] 支行下拉出现多个安全等价候选，拒绝自动选择: "
                f"target=[{text_value}], candidates={safe_names}"
            )
            _shot("05_支行下拉_多安全等价候选_拒绝")
            _close_branch_dropdown_if_open("多安全等价候选")
            return False, False, {}

        if hierarchy_matches:
            hierarchy_matches.sort(key=lambda x: (hierarchy_rank(x[2]), -x[3].score, x[0].top))
            best_rank = hierarchy_rank(hierarchy_matches[0][2])
            best = [item for item in hierarchy_matches if hierarchy_rank(item[2]) == best_rank]
            hierarchy_names = _unique_candidate_names([(rect, name) for rect, name, _kind, _score in best])
            if not allow_region_branch:
                print(
                    f"[fail-closed] 发现同银行/同地区层级候选但按规则不自动选择: "
                    f"target=[{text_value}], candidates={hierarchy_names}, "
                    "不会自动改填外部开户地址省市"
                )
            elif len(hierarchy_names) == 1:
                first_rect, first_name, hierarchy_kind, hierarchy_score = best[0]
                print(
                    f"支行下拉命中银行层级匹配: target=[{text_value}], "
                    f"selected=[{first_name}], kind={hierarchy_kind}, score={hierarchy_score.score}"
                )
                mx = first_rect.mid_point().x + 60
                my = first_rect.mid_point().y
                print(f"找到银行层级支行选项: {first_name}，位置({mx}, {my})")
                click_at(mx, my)
                time.sleep(0.15)
                click_at(mx, my)
                print(f"已双击选择: {first_name}")
                return True, False, {
                    "kind": hierarchy_kind,
                    "target": text_value,
                    "selected": first_name,
                    "score": hierarchy_score.score,
                    "reason": hierarchy_score.reason,
                }
            else:
                print(
                    f"[fail-closed] 支行下拉出现多个同等级层级候选，拒绝自动选择: "
                    f"target=[{text_value}], candidates={hierarchy_names}"
                )
                _shot("05_支行下拉_多层级候选_拒绝")
                _close_branch_dropdown_if_open("多层级候选")
                return False, False, {}

        # 缓存最近一帧带支行关键字的下拉候选，timeout 后用于 fail-closed 审计日志。
        if items:
            last_branch_items = items
        time.sleep(0.3)

    if probe_mode:
        _shot("05_支行探测_无候选")
        _print_candidate_report("[支行探测]", last_all_items, last_branch_items)
        print("[支行探测] 6s 内未发现可用候选，主动终止，不点击支行选项")
        return False, False, {}

    # 6s 内没有精确或安全等价匹配。fail-closed：不能再选第一条候选。
    if last_branch_items:
        _shot("05_支行下拉_无精确或安全等价匹配_拒绝候选")
        _print_candidate_report("[fail-closed] 支行下拉无精确或安全等价匹配", last_all_items, last_branch_items)
        print(
            f"[fail-closed] 未精确或安全等价匹配目标支行[{text_value}]，"
            "拒绝自动选择第一条候选，终止制单"
        )
        _close_branch_dropdown_if_open("无精确或安全等价匹配")
        return False, False, {}

    if target_has_branch:
        print(f"未找到与 [{text_value}] 精确或安全等价匹配的支行下拉项，下拉也没有可审计的候选")
    else:
        print(f"未找到与 [{text_value}] 精确或安全等价匹配的下拉项（目标不含'支行/分行'关键字），下拉也没有可审计的候选")
    _shot("05_支行下拉_无精确或安全等价匹配")
    _close_branch_dropdown_if_open("无候选")
    return False, False, {}

