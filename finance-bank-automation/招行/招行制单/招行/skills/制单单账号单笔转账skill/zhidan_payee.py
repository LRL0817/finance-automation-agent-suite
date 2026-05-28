# -*- coding: utf-8 -*-
"""Beneficiary namebook/search popup handling."""
import time

from ubank_common import click_at, press_keys
from zhidan_input import _click_safe_blank_to_close_popup, _compact_digits, _compact_text

def _close_transient_payee_popup(reason):
    """Close the beneficiary search/namebook popup if it is still open."""
    print(f"收起收方名册/搜索结果浮层: {reason}")
    _click_safe_blank_to_close_popup()
    for _ in range(2):
        press_keys((0x1B, 0), (0x1B, 2))  # Esc
        time.sleep(0.2)

def _select_payee_lookup_candidate(main_win, payee_acct, payee_name):
    """If the beneficiary namebook popup appears, double-click the matching row."""
    acct_key = _compact_digits(payee_acct)
    name_key = _compact_text(payee_name)

    try:
        host_rect = main_win.rectangle()
    except Exception:
        host_rect = None

    candidates = []
    header_seen_names = set()
    header_names = {"搜索结果", "收方账号", "收方名称", "收方编号", "开户行", "支行名称", "联行号"}
    excluded_names = {
        "智能补全收方信息",
        "保存收方信息",
        "查询名册",
        "关联发票",
        "开户银行",
        "支行名称/联行号",
        "收方户名",
    }

    def _is_in_payee_popup_area(rect):
        if host_rect is None:
            return rect.left >= 650 and 350 <= rect.top <= 760
        w = max(1, host_rect.width())
        h = max(1, host_rect.height())
        rx = (rect.mid_point().x - host_rect.left) / w
        ry = (rect.mid_point().y - host_rect.top) / h
        return 0.35 <= rx <= 0.82 and 0.40 <= ry <= 0.72

    def _walk(control):
        try:
            if not control.is_visible():
                return
        except Exception:
            return

        try:
            ei = control.element_info
            name = (ei.name or "").strip() if hasattr(ei, "name") else ""
            ctrl_type = str(ei.control_type) if hasattr(ei, "control_type") and ei.control_type else ""
            cls = (ei.class_name or "") if hasattr(ei, "class_name") else ""
            rect = control.rectangle()
            in_popup_area = _is_in_payee_popup_area(rect)
            if in_popup_area and name in header_names:
                header_seen_names.add(name)

            is_input = any(kw in ctrl_type for kw in ["Edit", "Document", "ComboBox"]) or any(
                kw in cls for kw in ["Edit", "ComboBox"]
            )
            name_digits = _compact_digits(name)
            match_acct = acct_key and acct_key in _compact_digits(name)
            compact_name = _compact_text(name)
            match_name = (
                name_key
                and len(compact_name) >= 4
                and (compact_name in name_key or name_key in compact_name)
            )
            looks_like_payee_row_data = (
                len(name_digits) >= 6
                or match_name
                or ("银行" in name and name not in {"开户银行", "查询支行"})
            )
            if (
                name
                and not is_input
                and name not in header_names
                and name not in excluded_names
                and looks_like_payee_row_data
                and rect.width() > 20
                and 12 <= rect.height() <= 70
                and in_popup_area
            ):
                priority = 0 if match_acct else 1 if match_name else 2
                candidates.append((priority, rect.top, rect.left, rect, name, ctrl_type, cls))
        except Exception:
            pass

        try:
            for child in control.children():
                _walk(child)
        except Exception:
            pass

    _walk(main_win)
    strong_popup = len(header_seen_names) >= 2
    # priority: 0=账号命中，1=户名命中，2=既未命中账号也未命中户名（疑似无关数据行）。
    # 自动点击名册必须至少有账号或户名命中，避免顶部坐标排序点到完全无关的收款人。
    matching_candidates = [item for item in candidates if item[0] < 2]

    if not matching_candidates:
        # 即便浮层确实存在，只要没匹配命中就不点击：误点错误收款人比走正常开户银行
        # 流程的代价大得多。这里收起浮层，让后续走正常开户银行/支行填写流程。
        if header_seen_names or strong_popup:
            _close_transient_payee_popup("名册浮层未发现匹配账号/户名候选，跳过名册带出")
        else:
            print("未发现收方名册/搜索结果浮层，按正常开户银行流程继续")
        return False

    # 优先级（账号命中 < 户名命中）排首位，再按 top/left 排序选最靠上的命中行。
    matching_candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    _, _, _, rect, name, ctrl_type, cls = matching_candidates[0]
    x = rect.mid_point().x
    y = rect.mid_point().y
    print(
        f"发现收方名册下拉框，双击账号/户名命中候选: {name!r}, type={ctrl_type}, class={cls}, "
        f"rect=({rect.left},{rect.top},{rect.right},{rect.bottom}), center=({x},{y})"
    )
    click_at(x, y)
    time.sleep(0.15)
    click_at(x, y)
    time.sleep(1.0)
    return True
