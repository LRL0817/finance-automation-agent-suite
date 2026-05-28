"""General UIA and coordinate fallback helpers."""
import subprocess
import time

import pyautogui

from .runtime import fixed_point, screenshot

def _find_nearby_edit(label_ctrl):
    """从标签的父容器里找距离最近的 Edit/ComboBox 控件"""
    try:
        parent = label_ctrl.parent()
        if parent is None:
            return None
        ly = label_ctrl.rectangle().top
        candidates = []
        for sib in parent.children():
            try:
                if not sib.is_visible():
                    continue
                stype = str(sib.element_info.control_type) if hasattr(sib.element_info, "control_type") else ""
                scls = sib.element_info.class_name if hasattr(sib.element_info, "class_name") else ""
                if any(kw in scls for kw in ["Edit", "edit"]) or any(kw in stype for kw in ["Edit", "Document", "ComboBox", "Combo"]):
                    sr = sib.rectangle()
                    candidates.append((sib, abs(sr.top - ly)))
            except Exception:
                pass
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]
    except Exception:
        return None


def _find_label(main_win, label_text):
    """递归查找标签控件（排除 Edit/Document），名称等于或刚好包含 label_text"""
    result = {"ctrl": None}

    def _walk(control):
        if result["ctrl"]:
            return True
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            ctrl_type = str(control.element_info.control_type) if hasattr(control.element_info, "control_type") else ""
            cls = control.element_info.class_name if hasattr(control.element_info, "class_name") else ""
            is_edit = any(kw in cls for kw in ["Edit", "edit"]) or any(kw in ctrl_type for kw in ["Edit", "Document"])
            if not is_edit and name and control.is_visible():
                s = name.strip().lstrip("*").strip()
                if s == label_text or (label_text in s and len(s) <= len(label_text) + 4):
                    result["ctrl"] = control
                    return True
        except Exception:
            pass
        try:
            for child in control.children():
                if _walk(child):
                    return True
        except Exception:
            pass
        return False

    _walk(main_win)
    return result["ctrl"]


_PS_SET_CLIPBOARD = (
    # Windows PowerShell 5.1 默认按系统 ANSI 解 stdin，必须改成 UTF-8 才能正确传中文。
    "[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false); "
    "$txt = [Console]::In.ReadToEnd(); "
    "Set-Clipboard -Value $txt"
)


def _set_clipboard(value):
    """通过 stdin 传 UTF-8 给 PowerShell；失败 raise 避免粘贴旧剪贴板。"""
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", _PS_SET_CLIPBOARD],
        input=value or "", text=True, encoding="utf-8",
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Set-Clipboard 失败 rc={proc.returncode}: {proc.stderr.strip()}")


def fill_field_by_label(main_win, label_text, value, use_clipboard=False):
    """按标签找邻近输入框，点击聚焦，清空，输入"""
    label = _find_label(main_win, label_text)
    if not label:
        print(f"    未找到标签 [{label_text}]")
        return False
    edit = _find_nearby_edit(label)
    if not edit:
        print(f"    [{label_text}] 找不到输入框")
        return False
    try:
        r = edit.rectangle()
        pyautogui.click(r.mid_point().x, r.mid_point().y)
        time.sleep(0.4)
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyautogui.press("delete")
        time.sleep(0.15)
        if use_clipboard:
            _set_clipboard(value)
            time.sleep(0.2)
            pyautogui.hotkey("ctrl", "v")
        else:
            pyautogui.write(value, interval=0.05)
        print(f"    已填 [{label_text}]: {value}")
        time.sleep(0.4)
        return True
    except Exception as e:
        print(f"    填 [{label_text}] 失败: {e}")
        return False


def _find_option_in_tree(main_win, option_text):
    """在下拉展开后的 UIA 树中查找目标选项控件"""
    found = {"c": None}

    def _walk(control):
        if found["c"]:
            return
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            if name and name.strip() == option_text and control.is_visible():
                r = control.rectangle()
                if r.height() > 5 and r.width() > 10:
                    found["c"] = control
                    return
        except Exception:
            pass
        try:
            for child in control.children():
                _walk(child)
        except Exception:
            pass

    _walk(main_win)
    return found["c"]


def _find_nearby_combo(label_ctrl):
    """从标签的父容器里找距离最近的下拉/组合框控件（Electron 应用可能注册为 Button 或 Edit）"""
    try:
        parent = label_ctrl.parent()
        if parent is None:
            return None
        ly = label_ctrl.rectangle().top
        candidates = []
        for sib in parent.children():
            try:
                if not sib.is_visible():
                    continue
                scls = sib.element_info.class_name if hasattr(sib.element_info, "class_name") else ""
                stype = str(sib.element_info.control_type) if hasattr(sib.element_info, "control_type") else ""
                # 匹配多种可能的下拉控件类型（含 Electron 自定义实现）
                is_combo = (
                    any(kw in stype for kw in ["ComboBox", "Combo", "List", "Select"]) or
                    any(kw in stype for kw in ["Button"]) and ("arrow" in scls.lower() or "drop" in scls.lower()) or
                    any(kw in stype for kw in ["Edit", "Document"])
                )
                if is_combo:
                    sr = sib.rectangle()
                    candidates.append((sib, abs(sr.top - ly)))
            except Exception:
                pass
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]
    except Exception:
        return None


def select_combobox_option(main_win, label_text, option_text):
    """打开下拉，在 UIA 树里找目标项点击（找不到则滚动列表重试）"""
    label = _find_label(main_win, label_text)
    if not label:
        print(f"    未找到标签 [{label_text}]")
        return False

    # 通过标签找旁边的 ComboBox 控件
    combo = _find_nearby_combo(label)

    if combo:
        r = combo.rectangle()
        cx, cy = r.mid_point().x, r.mid_point().y
        print(f"    找到控件 @ ({cx}, {cy}) type={combo.element_info.control_type}")

        # 先用 pywinauto 让控件获得焦点
        try:
            combo.set_focus()
            time.sleep(0.5)
            print(f"    已用 pywinauto set_focus")
        except Exception as e:
            print(f"    set_focus 失败: {e}，回退鼠标点击聚焦")
            pyautogui.click(cx, cy)
            time.sleep(0.5)

        # 用 F4 或 Alt+Down 打开下拉（标准 Windows 下拉快捷键）
        for hotkey in [("f4",), ("alt", "down")]:
            try:
                if len(hotkey) == 1:
                    pyautogui.press(hotkey[0])
                else:
                    pyautogui.hotkey(*hotkey)
                time.sleep(1.5)
                print(f"    已按 {hotkey} 打开下拉 [{label_text}]")
                break
            except Exception:
                continue

        # 检查是否已展开
        opt_ctrl = _find_option_in_tree(main_win, option_text)
        if not opt_ctrl:
            # 最后尝试 click_input
            try:
                combo.click_input()
                time.sleep(1.5)
                print(f"    回退 click_input 打开下拉")
            except Exception:
                pass
    else:
        lr = label.rectangle()
        cx = lr.right + 200
        cy = lr.mid_point().y
        pyautogui.click(cx, cy)
        time.sleep(1.5)
        print(f"    未找到 ComboBox 控件，使用偏移坐标 ({cx}, {cy})")

    # 先在当前可见区域查找
    opt_ctrl = _find_option_in_tree(main_win, option_text)
    if opt_ctrl:
        r = opt_ctrl.rectangle()
        pyautogui.click(r.mid_point().x, r.mid_point().y)
        print(f"    已选 [{label_text}]: {option_text}")
        return True

    # 当前视野没找到，用键盘向下翻页查找（最多按 15 次 Down）
    print(f"    当前视野未找到 [{option_text}]，开始键盘翻页查找...")
    for key_i in range(15):
        pyautogui.press("down")
        time.sleep(0.3)
        opt_ctrl = _find_option_in_tree(main_win, option_text)
        if opt_ctrl:
            r = opt_ctrl.rectangle()
            pyautogui.click(r.mid_point().x, r.mid_point().y)
            print(f"    已选 [{label_text}]: {option_text} (按键{key_i + 1}次后)")
            return True

    # 键盘也找不到，最后尝试鼠标滚轮滚动
    print(f"    键盘未找到，尝试滚轮滚动...")
    scroll_x = cx
    scroll_y = cy + 80
    for scroll_i in range(5):
        pyautogui.moveTo(scroll_x, scroll_y)
        pyautogui.scroll(-300)
        time.sleep(0.6)
        opt_ctrl = _find_option_in_tree(main_win, option_text)
        if opt_ctrl:
            r = opt_ctrl.rectangle()
            pyautogui.click(r.mid_point().x, r.mid_point().y)
            print(f"    已选 [{label_text}]: {option_text} (滚动{scroll_i + 1}次后)")
            return True

    print(f"    下拉中未找到 [{option_text}]")
    return False


def _center_in_bounds(rect, bounds):
    """判断控件中心点是否落在指定屏幕区域内。bounds=(left, top, right, bottom)。"""
    if bounds is None:
        return True
    left, top, right, bottom = bounds
    cx, cy = rect.mid_point().x, rect.mid_point().y
    return left <= cx <= right and top <= cy <= bottom


def click_control_by_name(main_win, target_name, timeout=8, click_bounds=None):
    """在 UIA 树中按名称查找控件并点击；可限制命中区域避免点到同名搜索项。"""

    def _find_and_click(control):
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            if name and name.strip() == target_name.strip():
                rect = control.rectangle()
                if (
                    control.is_visible()
                    and rect.width() > 3
                    and rect.height() > 3
                    and _center_in_bounds(rect, click_bounds)
                ):
                    cx, cy = rect.mid_point().x, rect.mid_point().y
                    pyautogui.click(cx, cy)
                    print(f"  已点击 [{target_name}] @ ({cx}, {cy})")
                    return True
        except Exception:
            pass
        try:
            for child in control.children():
                if _find_and_click(child):
                    return True
        except Exception:
            pass
        return False

    end = time.time() + timeout
    while time.time() < end:
        if _find_and_click(main_win):
            return True
        time.sleep(0.3)
    if click_bounds is None:
        print(f"  未找到控件: {target_name}")
    else:
        print(f"  未在限定区域内找到控件: {target_name}, bounds={click_bounds}")
    return False


def click_next_step(main_win, timeout=5):
    """点击单笔转账表单底部的“下一步”按钮。UIA 找不到时 fail-closed，不再兜底坐标。"""
    if main_win is None:
        screenshot("下一步_main_win为空")
        return False
    try:
        main_win.set_focus()
    except Exception:
        pass
    if click_control_by_name(main_win, "下一步", timeout=timeout):
        return True
    screenshot("下一步_未找到控件")
    return False


def click_submit(main_win, timeout=5):
    """点击信息确认页底部的“提交”按钮。UIA 找不到时 fail-closed，绝不兜底坐标。"""
    if main_win is None:
        screenshot("提交_main_win为空")
        return False
    try:
        main_win.set_focus()
    except Exception:
        pass
    if click_control_by_name(main_win, "提交", timeout=timeout):
        return True
    screenshot("提交_未找到控件")
    return False


def scroll_to_bank_section():
    """把表单滚动位置归一化到收款行区域，避免相对滚动导致固定点漂移。"""
    pyautogui.moveTo(*fixed_point(960, 500))
    pyautogui.scroll(2000)
    time.sleep(0.8)
    screenshot("06_5_回到顶部")
    pyautogui.scroll(-500)
    time.sleep(0.8)
    screenshot("06_5_下翻后")


def scroll_to_form_top():
    """滚回表单顶部，用于核对金额、收款账号和收款户名。"""
    pyautogui.moveTo(*fixed_point(960, 500))
    pyautogui.scroll(2000)
    time.sleep(0.8)
