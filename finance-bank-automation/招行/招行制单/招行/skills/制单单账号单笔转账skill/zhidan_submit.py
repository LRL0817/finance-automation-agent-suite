# -*- coding: utf-8 -*-
"""Submit-button discovery and click flow for the transfer form."""
import time

from ubank_common import click_at, press_keys
from zhidan_crash import _check_ubank_crash
from zhidan_input import _wheel_foreground_client
from zhidan_utils import _screenshot

def _collect_submit_button_candidates(main_win):
    """遍历 UIA 树，定位真正的「经办」按钮候选；只读控件，不点击。

    页面顶部标题、页签和面包屑里也会出现「单笔转账经办」。这些不是提交按钮，
    所以这里仅把可见且文案规整后等于「经办」的控件列为可点击候选。
    """
    candidates = []
    related = []

    def _walk(control):
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            ctrl_type = str(control.element_info.control_type) if hasattr(control.element_info, "control_type") else ""
            cls = control.element_info.class_name if hasattr(control.element_info, "class_name") else ""
            visible = control.is_visible()
            rect = control.rectangle()
        except Exception:
            name = ""
            ctrl_type = ""
            cls = ""
            visible = False
            rect = None

        if name and "经办" in name and rect is not None:
            w = rect.width()
            h = rect.height()
            if visible and w > 5 and h > 5:
                stripped = name.strip()
                normalized = "".join(stripped.split())
                is_buttonish = "Button" in ctrl_type or "Button" in cls or "button" in cls.lower()
                item = {
                    "name": stripped,
                    "normalized": normalized,
                    "type": ctrl_type,
                    "class": cls,
                    "rect": rect,
                    "center": (rect.mid_point().x, rect.mid_point().y),
                    "control": control,
                    "buttonish": is_buttonish,
                }
                if normalized == "经办":
                    priority = 0 if is_buttonish else 1
                    # 同名候选里优先选更像按钮的，其次选更靠下的，避开顶部导航区。
                    candidates.append((priority, -rect.top, rect.left, item))
                else:
                    related.append(item)

        try:
            for child in control.children():
                _walk(child)
        except Exception:
            pass

    _walk(main_win)
    candidates.sort(key=lambda t: (t[0], t[1], t[2]))
    return [item for _, _, _, item in candidates], related

def _print_submit_probe(stage, main_win, prefer_confirmation=False):
    candidates, related = _collect_submit_button_candidates(main_win)
    if candidates:
        print(f"{stage}: 找到 {len(candidates)} 个「经办」按钮候选")
        for idx, item in enumerate(candidates[:5], start=1):
            rect = item["rect"]
            cx, cy = item["center"]
            print(
                f"  候选#{idx}: name={item['name']!r}, type={item['type']}, "
                f"class={item['class']}, rect=({rect.left},{rect.top},{rect.right},{rect.bottom}), "
                f"center=({cx},{cy})"
            )
        selected = candidates[0]
        if prefer_confirmation and len(candidates) > 1:
            bottom_top = candidates[0]["rect"].top
            for item in candidates[1:]:
                if item["rect"].top < bottom_top - 10:
                    selected = item
                    break
        rect = selected["rect"]
        cx, cy = selected["center"]
        print(
            f"{stage}: 选中「经办」候选 name={selected['name']!r}, "
            f"rect=({rect.left},{rect.top},{rect.right},{rect.bottom}), center=({cx},{cy})"
        )
        return selected

    print(f"{stage}: 未找到可见的「经办」按钮候选")
    for item in related[:5]:
        rect = item["rect"]
        cx, cy = item["center"]
        print(
            f"  排除的相关控件: name={item['name']!r}, type={item['type']}, "
            f"class={item['class']}, rect=({rect.left},{rect.top},{rect.right},{rect.bottom}), "
            f"center=({cx},{cy})"
        )
    return None

def _has_submit_validation_error(main_win):
    """点击经办后检查页面是否仍因必填/格式问题停在表单。"""
    needles = ("请填写正确的经办信息", "请填写正确", "经办信息")
    hits = []

    def _walk(control):
        try:
            if not control.is_visible():
                return
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            if any(needle in name for needle in needles):
                hits.append(name.strip())
                return
        except Exception:
            pass

        try:
            for child in control.children():
                _walk(child)
        except Exception:
            pass

    _walk(main_win)
    if hits:
        print(f"经办后检测到页面校验提示: {hits[0]}")
        return True
    return False

def _wait_for_submit_validation_error(main_win, timeout=2.5):
    end_ts = time.time() + timeout
    while time.time() < end_ts:
        _check_ubank_crash("经办后校验提示检测")
        if _has_submit_validation_error(main_win):
            _screenshot("10_经办校验失败")
            return True
        time.sleep(0.3)
    return False

def _click_submit_candidate(item, stage_desc):
    rect = item["rect"]
    cx, cy = item["center"]
    print(
        f"{stage_desc}: 准备点击「经办」按钮 name={item['name']!r}, "
        f"type={item['type']}, class={item['class']}, "
        f"rect=({rect.left},{rect.top},{rect.right},{rect.bottom}), center=({cx},{cy})"
    )

    control = item.get("control")
    if control is not None and item.get("buttonish"):
        try:
            control.invoke()
            print(f"{stage_desc}: 已通过 UIA invoke 点击「经办」按钮")
            time.sleep(0.8)
            return True
        except Exception as e:
            print(f"{stage_desc}: UIA invoke 点击失败，改用坐标点击: {e}")

    click_at(cx, cy)
    print(f"{stage_desc}: 已通过坐标点击「经办」按钮")
    time.sleep(0.8)
    return True

def _click_submit_button(main_win, prefer_confirmation=False):
    """填表后滚动到底部并点击真正的「经办」按钮。"""
    print("开始定位并点击「经办」按钮...")
    _check_ubank_crash("点击经办前")
    _screenshot("07_经办点击_滚动前")
    best = _print_submit_probe("滚动前", main_win, prefer_confirmation=prefer_confirmation)
    if best:
        if _click_submit_candidate(best, "滚动前"):
            _screenshot("09_经办点击后")
            if _wait_for_submit_validation_error(main_win):
                return False
            return True

    try:
        main_win.set_focus()
    except Exception:
        pass

    # 先用 End/PageDown，再用滚轮，把页面底部按钮区带入可见区域。
    for _ in range(2):
        press_keys((0x23, 0), (0x23, 2))  # End
        time.sleep(0.25)
        _check_ubank_crash("点击经办 End 滚动")
    for _ in range(2):
        press_keys((0x22, 0), (0x22, 2))  # PageDown
        time.sleep(0.25)
        _check_ubank_crash("点击经办 PageDown 滚动")
    for _ in range(6):
        _wheel_foreground_client(0.70, 0.72, -5)
        _check_ubank_crash("点击经办 滚轮滚动")

    _screenshot("08_经办点击_滚动后")
    best = _print_submit_probe("滚动后", main_win, prefer_confirmation=prefer_confirmation)
    if best:
        rect = best["rect"]
        cx, cy = best["center"]
        print(f"经办按钮最终定位: rect=({rect.left},{rect.top},{rect.right},{rect.bottom}), center=({cx},{cy})")
        if _click_submit_candidate(best, "滚动后"):
            _screenshot("09_经办点击后")
            if _wait_for_submit_validation_error(main_win):
                return False
            return True
    else:
        print("经办按钮最终定位失败：滚动到底部后仍未在 UIA 树中发现可见候选，已保存截图供排查")
    return False
