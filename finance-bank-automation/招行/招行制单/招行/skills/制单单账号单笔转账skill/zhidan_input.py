# -*- coding: utf-8 -*-
"""Low-level UI input helpers for the U-BANK transfer form."""
import os
import time

import win32api
import win32clipboard
import win32con
import win32gui
from pywinauto.keyboard import send_keys

import zhidan_paths  # Ensures the project root is importable before ubank_common.
from ubank_common import click_at, press_keys
from zhidan_crash import UBankCrashDetected, _check_ubank_crash
from zhidan_utils import _screenshot, _trace_fill_step

def _is_debug_input_label(target_label):
    return "金额" in target_label or "用途" in target_label

def _click_dropdown_option(control, target_text):
    """递归在下拉列表中查找并点击目标选项"""
    try:
        name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
        if name and name.strip() == target_text.strip() and control.is_visible():
            rect = control.rectangle()
            if rect.width() > 5 and rect.height() > 5:
                click_at(rect.mid_point().x, rect.mid_point().y)
                print(f"已选择下拉项: {target_text}")
                return True
    except Exception:
        pass
    try:
        for child in control.children():
            if _click_dropdown_option(child, target_text):
                return True
    except Exception:
        pass
    return False

def find_and_input_text(main_win, target_label, text_value, input_mode="type"):
    """根据标签名模糊查找相邻的文本输入框并输入内容"""
    debug_input = _is_debug_input_label(target_label)
    use_clipboard = input_mode == "paste"

    def _trace(step):
        if debug_input:
            _trace_fill_step(f"{target_label}: {step}")

    def _check(step):
        if debug_input:
            _check_ubank_crash(f"fill-debug: {target_label} {step}")

    # 先用模糊匹配查找标签控件（标签名中包含目标关键字即可）
    label_control = None

    def _find_label(control):
        nonlocal label_control
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            # 模糊匹配：目标关键字在标签名中，或标签名在目标关键字中
            if name and name.strip() and (
                target_label.strip() in name or
                name.strip() in target_label.strip() or
                target_label.replace(" ", "")[:3] in name.replace(" ", "")
            ):
                label_control = control
                return True
        except Exception:
            pass
        try:
            for child in control.children():
                if _find_label(child):
                    return True
        except Exception:
            pass
        return False

    print(f"正在查找标签: {target_label}")
    _trace("开始查找标签")
    _find_label(main_win)
    _check("标签查找后")

    # 如果模糊匹配没找到，尝试遍历所有可见文本控件打印调试信息
    if not label_control:
        all_labels = []

        def _collect_texts(control):
            try:
                name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
                ctrl_type = control.element_info.control_type if hasattr(control.element_info, "control_type") else ""
                cls = control.element_info.class_name if hasattr(control.element_info, "class_name") else ""
                if name and name.strip() and ("Text" in ctrl_type or "Static" in cls):
                    all_labels.append(f"[{ctrl_type}|{cls}] {name.strip()}")
            except Exception:
                pass
            try:
                for child in control.children():
                    _collect_texts(child)
            except Exception:
                pass

        _collect_texts(main_win)
        print(f"  未精确找到: {target_label}，当前页面可见文本控件:")
        for lbl in all_labels[:50]:
            print(f"  {lbl}")

    if not label_control:
        print(f"未找到标签: {target_label}")
        return False

    try:
        label_rect = label_control.rectangle()
        _trace(
            f"命中标签 rect=({label_rect.left},{label_rect.top},"
            f"{label_rect.right},{label_rect.bottom})"
        )
    except Exception:
        _trace("命中标签但读取 rect 失败")

    # 找到标签后，在其父容器中找可编辑的输入框
    def _find_nearby_edit(label_ctrl):
        """从标签控件的父容器中找到可编辑的输入框"""
        candidates = []
        try:
            parent = label_ctrl.parent()
            if parent is None:
                return None
            # 获取标签的位置信息
            label_rect = label_ctrl.rectangle()
            label_y = label_rect.top

            for sibling in parent.children():
                if not sibling.is_visible():
                    continue
                cls_name = sibling.element_info.class_name if hasattr(sibling.element_info, "class_name") else ""
                ctrl_type = sibling.element_info.control_type if hasattr(sibling.element_info, "control_type") else ""
                sname = sibling.element_info.name if hasattr(sibling.element_info, "name") and sibling.element_info.name else ""

                # 匹配编辑类控件
                is_edit = any(kw in cls_name for kw in ["Edit", "edit"]) or any(kw in str(ctrl_type) for kw in ["Edit", "Document", "ComboBox"])
                if is_edit:
                    srect = sibling.rectangle()
                    # 优先选择与标签在同一行或附近（Y坐标接近）且是空输入框的
                    is_empty = not sname or len(sname.strip()) < 2 or "输入" in sname or "请输" in sname or "选择" in sname
                    y_diff = abs(srect.top - label_y)
                    candidates.append((sibling, y_diff, is_empty))
        except Exception:
            pass

        if not candidates:
            return None
        # 排序：优先选Y距离近的、空框的
        candidates.sort(key=lambda x: (x[1], not x[2]))
        return candidates[0][0]

    edit_ctrl = _find_nearby_edit(label_control)
    _check("输入框查找后")
    if edit_ctrl:
        try:
            pre_focus_rect = None
            try:
                pre_focus_rect = edit_ctrl.rectangle()
                _trace(
                    f"命中输入框 rect=({pre_focus_rect.left},{pre_focus_rect.top},"
                    f"{pre_focus_rect.right},{pre_focus_rect.bottom})"
                )
            except Exception:
                _trace("命中输入框但读取 rect 失败")

            _trace("set_focus 前")
            edit_ctrl.set_focus()
            time.sleep(0.3)  # 加长等待确保聚焦完成
            _trace("set_focus 后")
            _check("set_focus 后")
            # 判断控件类型，选择合适的输入方式
            ctrl_type = edit_ctrl.element_info.control_type if hasattr(edit_ctrl.element_info, "control_type") else ""
            cls_name = edit_ctrl.element_info.class_name if hasattr(edit_ctrl.element_info, "class_name") else ""
            _trace(f"控件类型 type={ctrl_type}, class={cls_name}")

            # 下拉框/组合框
            is_combo = "Combo" in ctrl_type or "Combo" in cls_name or "ComboBox" in str(ctrl_type)

            if is_combo:
                rect = edit_ctrl.rectangle()
                rect_bad = (
                    rect.width() <= 5
                    or rect.height() <= 5
                    or (rect.mid_point().x == 0 and rect.mid_point().y == 0)
                )
                if rect_bad:
                    # 招行 ComboBox set_focus 后 UIA rect 偶发塌陷为 (0,0,0,0)，
                    # 回退到 set_focus 前缓存的 rect 再点。
                    if (
                        pre_focus_rect is not None
                        and pre_focus_rect.width() > 5
                        and pre_focus_rect.height() > 5
                        and not (pre_focus_rect.mid_point().x == 0 and pre_focus_rect.mid_point().y == 0)
                    ):
                        _trace(
                            f"Combo rect 塌陷，回退 set_focus 前 rect=({pre_focus_rect.left},"
                            f"{pre_focus_rect.top},{pre_focus_rect.right},{pre_focus_rect.bottom})"
                        )
                        rect = pre_focus_rect
                    else:
                        _trace(
                            f"Combo rect 不可信，放弃 UIA 输入 rect=({rect.left},{rect.top},"
                            f"{rect.right},{rect.bottom})"
                        )
                        return False
                _trace(f"Combo 点击前 center=({rect.mid_point().x},{rect.mid_point().y})")
                click_at(rect.mid_point().x, rect.mid_point().y)
                time.sleep(0.5)
                _trace("Combo 点击后")
                _check("Combo 点击后")

            if text_value:
                if use_clipboard:
                    _trace("跳过清空字段，剪贴板粘贴前")
                    if not _paste_text_via_clipboard(text_value):
                        return False
                    _trace("剪贴板粘贴后")
                    _check("剪贴板粘贴后")
                else:
                    _trace("跳过清空字段，直接输入")
                    _trace("慢速键入前")
                    _type_text_slow(text_value, char_delay=0.09 if "金额" not in target_label else 0.12)
                    time.sleep(0.4)
                    _trace("慢速键入后")
                    _check("慢速键入后")
            else:
                _trace("空值跳过清空字段")

            action = "粘贴" if use_clipboard else "输入"
            print(f"已{action} [{target_label}]: {text_value}")
            _trace("输入函数即将返回 True")
            _check("输入函数返回前")
            return True
        except UBankCrashDetected:
            raise
        except Exception as e:
            # 打印详细错误和控件信息用于调试
            try:
                c_type = edit_ctrl.element_info.control_type if hasattr(edit_ctrl.element_info, "control_type") else ""
                c_cls = edit_ctrl.element_info.class_name if hasattr(edit_ctrl.element_info, "class_name") else ""
                print(f"  控件详情: type={c_type}, class={c_cls}")
            except Exception:
                pass
            print(f"输入失败 [{target_label}]: {e}")
    else:
        print(f"未找到 {target_label} 对应的输入框")

    return False

def _read_nearby_input_text(main_win, target_label):
    """读取标签附近输入框当前值，用于填表后的程序化校验。"""
    label_control = None

    def _find_label(control):
        nonlocal label_control
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            if name and name.strip() and (
                target_label.strip() in name
                or name.strip() in target_label.strip()
                or target_label.replace(" ", "")[:3] in name.replace(" ", "")
            ):
                label_control = control
                return True
        except Exception:
            pass

        try:
            for child in control.children():
                if _find_label(child):
                    return True
        except Exception:
            pass
        return False

    def _find_nearby_edit(label_ctrl):
        candidates = []
        try:
            parent = label_ctrl.parent()
            if parent is None:
                return None
            label_y = label_ctrl.rectangle().top
            for sibling in parent.children():
                if not sibling.is_visible():
                    continue
                cls_name = sibling.element_info.class_name if hasattr(sibling.element_info, "class_name") else ""
                ctrl_type = sibling.element_info.control_type if hasattr(sibling.element_info, "control_type") else ""
                is_edit = any(kw in cls_name for kw in ["Edit", "edit"]) or any(
                    kw in str(ctrl_type) for kw in ["Edit", "Document", "ComboBox"]
                )
                if is_edit:
                    srect = sibling.rectangle()
                    candidates.append((sibling, abs(srect.top - label_y)))
        except Exception:
            pass

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]

    def _control_text(control):
        values = []
        for attr in ("get_value", "window_text"):
            try:
                getter = getattr(control, attr, None)
                if getter:
                    value = getter()
                    if value:
                        values.append(str(value))
            except Exception:
                pass
        try:
            value = control.iface_value.CurrentValue
            if value:
                values.append(str(value))
        except Exception:
            pass
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") else ""
            if name:
                values.append(str(name))
        except Exception:
            pass

        values = [v.strip() for v in values if v and v.strip()]
        if not values:
            return ""

        def _is_label_fallback(value):
            value_norm = value.replace("*", "").replace("：", "").replace(":", "")
            value_norm = "".join(value_norm.split())
            label_norm = target_label.replace("*", "").replace("：", "").replace(":", "")
            label_norm = "".join(label_norm.split())
            return bool(value_norm) and (value_norm == label_norm or value_norm in label_norm)

        non_label_values = [v for v in values if not _is_label_fallback(v)]
        if non_label_values:
            non_label_values.sort(key=len, reverse=True)
            return non_label_values[0]

        values.sort(key=len, reverse=True)
        return values[0]

    _find_label(main_win)
    if not label_control:
        print(f"校验失败：未找到标签 [{target_label}]")
        return ""

    edit_ctrl = _find_nearby_edit(label_control)
    if not edit_ctrl:
        print(f"校验失败：未找到 [{target_label}] 对应的输入框")
        return ""

    return _control_text(edit_ctrl)

def _verify_field_contains(main_win, target_label, expected_text, screenshot_label):
    expected_norm = _compact_text(expected_text)
    actual_text = _read_nearby_input_text(main_win, target_label)
    actual_norm = _compact_text(actual_text)
    print(f"校验字段 [{target_label}]: 期望包含 [{expected_text}], 实际 [{actual_text}]")
    if expected_norm and expected_norm in actual_norm:
        return True

    _screenshot(screenshot_label)
    print(f"字段校验失败 [{target_label}]，已保存截图: {screenshot_label}")
    return False

def select_radio_by_name(main_win, group_name, option_name):
    """根据选项名称点击对应的单选按钮"""

    def _find_and_click_radio(control):
        """递归查找包含目标文字的单选按钮并点击"""
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            if name and option_name in name:
                ctrl_type = control.element_info.control_type if hasattr(control.element_info, "control_type") else ""
                rect = control.rectangle()

                if control.is_visible() and rect.width() > 5 and rect.height() > 5:
                    # RadioButton 或 RadioButton类型的控件
                    if "Radio" in ctrl_type or "RadioButton" in str(ctrl_type):
                        click_at(rect.mid_point().x, rect.mid_point().y)
                        print(f"已选中: {option_name}")
                        return True
                    # 也可能是普通文本控件，直接点击
                    else:
                        click_at(rect.mid_point().x, rect.mid_point().y)
                        print(f"已选中(文本点击): {option_name}")
                        return True
        except Exception:
            pass

        try:
            for child in control.children():
                result = _find_and_click_radio(child)
                if result:
                    return True
        except Exception:
            pass
        return False

    print(f"正在选择: {group_name} -> {option_name}")
    _find_and_click_radio(main_win)

def _click_foreground_client(cx_ratio, cy_ratio):
    """在前台窗口客户区按比例点击，用于抢焦点（比例 0~1，相对窗口宽高）"""
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return False
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w = right - left
    h = bottom - top
    if w < 80 or h < 60:
        return False
    x = int(left + w * cx_ratio)
    y = int(top + h * cy_ratio)
    click_at(x, y)
    time.sleep(0.2)
    return True

def _wheel_foreground_client(cx_ratio, cy_ratio, clicks):
    """在前台窗口客户区按比例滚轮滚动；不点击任何控件。clicks<0 向下。"""
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return False
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w = right - left
    h = bottom - top
    if w < 80 or h < 60:
        return False
    x = int(left + w * cx_ratio)
    y = int(top + h * cy_ratio)
    win32api.SetCursorPos((x, y))
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_WHEEL, 0, 0, int(clicks * 120), 0)
    time.sleep(0.2)
    return True

def _type_ascii_text_slow(text_value, char_delay=0.12):
    """逐字符键入 ASCII 字段，避免触发网页控件的 paste/onchange 快路径。"""
    key_map = {
        ".": 0xBE,
        ",": 0xBC,
        "-": 0xBD,
        " ": 0x20,
    }
    for ch in text_value or "":
        if "0" <= ch <= "9":
            vk = ord(ch)
        elif ch in key_map:
            vk = key_map[ch]
        else:
            raise ValueError(f"慢速键入只支持 ASCII 数字金额字符，遇到: {ch!r}")
        press_keys((vk, 0), (vk, 2))
        time.sleep(char_delay)

def _type_text_slow(text_value, char_delay=0.09):
    """逐字符键入文本；中文通过 VK_PACKET 发送，不走剪贴板。"""
    text = text_value or ""
    if text and all(("0" <= ch <= "9") or ch in ".,- " for ch in text):
        _type_ascii_text_slow(text, char_delay=char_delay)
        return

    literal_key_map = {
        "(": (0x10, 0x39),  # Shift+9
        ")": (0x10, 0x30),  # Shift+0
    }
    for ch in text:
        literal_key = literal_key_map.get(ch)
        if literal_key:
            shift_vk, char_vk = literal_key
            press_keys((shift_vk, 0), (char_vk, 0), (char_vk, 2), (shift_vk, 2))
        else:
            send_keys(ch, pause=0, with_spaces=True, vk_packet=True)
        time.sleep(char_delay)

def _read_clipboard_text():
    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT), True
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass
    return "", False

def _write_clipboard_text(text_value):
    try:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text_value or "")
        finally:
            win32clipboard.CloseClipboard()
        return True
    except Exception as e:
        print(f"写入剪贴板失败: {e}")
        return False

def _paste_text_via_clipboard(text_value):
    """设置剪贴板后发送 Ctrl+V；不做 Ctrl+A/Backspace 清空。"""
    old_text, has_old_text = _read_clipboard_text()
    if not _write_clipboard_text(text_value):
        return False
    try:
        time.sleep(0.12)
        press_keys((0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2))  # Ctrl+V
        time.sleep(0.45)
        return True
    finally:
        if has_old_text:
            _write_clipboard_text(old_text)

def _click_ratio_and_paste(cx_ratio, cy_ratio, text_value, field_name, input_mode="type"):
    """U-BANK 页面 UIA 标签不稳定时，按窗口比例点击输入框并输入/粘贴。"""
    debug_input = _is_debug_input_label(field_name)
    use_clipboard = input_mode == "paste"
    if debug_input:
        _trace_fill_step(f"{field_name}: 坐标兜底点击前 ratio=({cx_ratio},{cy_ratio})")
    if not _click_foreground_client(cx_ratio, cy_ratio):
        print(f"{field_name} 坐标兜底失败：未找到前台窗口")
        return False
    if debug_input:
        action = "粘贴" if use_clipboard else "键入"
        _trace_fill_step(f"{field_name}: 坐标兜底点击后，{action}前")
        _check_ubank_crash(f"fill-debug: {field_name} 坐标兜底点击后")
    # U-BANK 的 Chrome/原生桥接对 Ctrl+A + Backspace 很敏感，曾在金额框清空后崩溃。
    # 坐标兜底也只做聚焦后输入/粘贴，不清空。
    time.sleep(0.15)
    if use_clipboard:
        if not _paste_text_via_clipboard(text_value):
            return False
    else:
        _type_text_slow(text_value, char_delay=0.09 if "金额" not in field_name else 0.12)
    time.sleep(0.4)
    if debug_input:
        _trace_fill_step(f"{field_name}: 坐标兜底输入后")
        _check_ubank_crash(f"fill-debug: {field_name} 坐标兜底输入后")
    print(f"已通过坐标兜底输入 [{field_name}]: {text_value or ''}")
    return True

def _dismiss_transfer_overlays():
    """关闭小招提醒/引导遮罩，避免表单变灰导致控件不可定位。"""
    print("清理页面遮罩/提醒...")
    for _ in range(2):
        press_keys((0x1B, 0), (0x1B, 2))  # Esc
        time.sleep(0.25)
    if os.getenv("ZHIDAN_OVERLAY_CLICK_FALLBACK", "0") == "1":
        print("已启用 ZHIDAN_OVERLAY_CLICK_FALLBACK=1，执行遮罩坐标兜底点击")
        for x_ratio, y_ratio in [(0.285, 0.700), (0.960, 0.515), (0.960, 0.675)]:
            _click_foreground_client(x_ratio, y_ratio)
            time.sleep(0.25)

def _compact_digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())

def _compact_text(value):
    return "".join(str(value or "").split())

def _click_safe_blank_to_close_popup():
    """Click a non-input title area to blur web popovers without changing form data."""
    _click_foreground_client(0.255, 0.165)
    time.sleep(0.25)
