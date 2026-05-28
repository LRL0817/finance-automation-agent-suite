# -*- coding: utf-8 -*-
"""Legacy confirmation-dialog click helpers kept separate from the main form flow.

LEGACY MODULE — DO NOT IMPORT FROM NEW CODE.

主流程已经改为 taskkill /F /T 强杀 Firmbank 进程树（见 zhidan_crash._close_with_confirm
和别名 _kill_ubank_process_tree）。本模块里的"优雅关闭"helper 仅在需要回退到旧路径时
留用，目前没有被生产/测试代码 import；一旦 Firmbank 12.0.0.6 的 WM_CLOSE 崩溃 bug 修
复，可以整体删除。新代码不要引用这里的任何函数；导航/退出确认都应直接 taskkill。
"""

__legacy__ = True  # 显式标记：本模块不应被新代码 import
import time

import win32gui

from ubank_common import click_at, press_keys
from zhidan_crash import _check_ubank_crash, _sleep_and_check

def _uia_collect_confirm_candidates(control, out_list, host_rect):
    """递归收集名称含「确认/确定」的可点击控件候选（优先小面积按钮类）"""
    try:
        if not control.is_visible():
            return
        ei = control.element_info
        name = ei.name if hasattr(ei, "name") and ei.name else ""
        nm = name.replace(" ", "") if name else ""
        ctype = str(ei.control_type) if hasattr(ei, "control_type") and ei.control_type else ""
        cls = ei.class_name if hasattr(ei, "class_name") and ei.class_name else ""
        rect = control.rectangle()
        w, h = rect.width(), rect.height()
        if w >= 6 and h >= 6 and ("确认" in nm or "确定" in nm):
            area = w * h
            is_std_btn = ("Button" in ctype) or ("SplitButton" in ctype) or ("Button" in cls)
            # 面积过大且非标准按钮时，更像容器文案，只继续扫子节点
            if area > 20000 and not is_std_btn:
                for ch in control.children():
                    _uia_collect_confirm_candidates(ch, out_list, host_rect)
                return
            pri_name = 0 if "确认" in nm else 1
            pri_type = 0 if is_std_btn else (1 if ("Hyperlink" in ctype or "Custom" in ctype) else 2)
            mx, my = rect.mid_point().x, rect.mid_point().y
            # 保护：不点击窗口右上角附近（避免误点关闭“X”）
            hr_left, hr_top, hr_right, hr_bottom = host_rect
            close_zone_x = hr_right - 90
            close_zone_y = hr_top + 50
            if mx >= close_zone_x and my <= close_zone_y:
                return
            out_list.append((pri_name, pri_type, area, mx, my, name.strip()[:48]))
    except Exception:
        pass
    try:
        for ch in control.children():
            _uia_collect_confirm_candidates(ch, out_list, host_rect)
    except Exception:
        pass

def _rect_close(a, b, tol=30):
    """判断两个矩形是否近似相同（用于匹配前台窗口）"""
    return (
        abs(a[0] - b[0]) <= tol and
        abs(a[1] - b[1]) <= tol and
        abs(a[2] - b[2]) <= tol and
        abs(a[3] - b[3]) <= tol
    )

def _try_uia_confirm_click(desktop):
    """仅在前台窗口的 UIA 树中查找并点击「确认/确定」"""
    fg_hwnd = win32gui.GetForegroundWindow()
    if not fg_hwnd:
        return False
    fg_rect = win32gui.GetWindowRect(fg_hwnd)
    candidates = []
    for w in desktop.windows():
        try:
            wr = w.rectangle()
            host_rect = (wr.left, wr.top, wr.right, wr.bottom)
            # 只在前台窗口中查找，避免命中主页面里的“确认”按钮
            if not _rect_close(host_rect, fg_rect):
                continue
            _uia_collect_confirm_candidates(w, candidates, host_rect)
        except Exception:
            pass
    if not candidates:
        return False
    candidates.sort(key=lambda t: (t[0], t[1], t[2]))
    _, _, _, x, y, label = candidates[0]
    print(f"UIA 定位到确认类控件: {label!r} -> ({x}, {y})")
    click_at(x, y)
    time.sleep(0.35)
    return True

def _try_spatial_confirm_click():
    """自绘/Web 弹窗常见布局：主按钮在底部偏右，对前台窗体该位置点击"""
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w = right - left
    h = bottom - top
    if w < 200 or h < 100:
        return
    x = right - int(max(72, w * 0.20))
    y = bottom - int(max(32, h * 0.14))
    print(f"几何点击确认区域（前台窗右下角附近）: ({x}, {y})，窗口约 {w}x{h}")
    click_at(x, y)
    time.sleep(0.3)

def _find_x_close_button(main_win):
    """在主窗口 UIA 树里找右上角的关闭(X)按钮，返回控件或 None。

    招行 U-BANK 12.0.0.6 标题栏是自绘的，但 UIA 一般还是能枚举到 Button 类控件。
    匹配条件：按钮类、宽高合理（小图标按钮）、位置在主窗口右上角带内。
    """
    try:
        win_rect = main_win.rectangle()
    except Exception as e:
        print(f"获取主窗口矩形失败: {e}")
        return None

    win_right = win_rect.right
    win_top = win_rect.top
    candidates = []

    def _walk(control):
        try:
            if not control.is_visible():
                return
        except Exception:
            return
        try:
            ei = control.element_info
            name = (ei.name or "") if hasattr(ei, "name") else ""
            ctype = str(ei.control_type) if hasattr(ei, "control_type") and ei.control_type else ""
            cls = (ei.class_name or "") if hasattr(ei, "class_name") else ""
            rect = control.rectangle()
            w, h = rect.width(), rect.height()

            # 关闭按钮特征：尺寸像图标按钮（10~60px 宽高），且贴在主窗右上角
            in_top_right = (
                rect.right >= win_right - 80
                and rect.top <= win_top + 60
                and rect.right <= win_right + 5
            )
            is_button_like = "Button" in ctype or "Button" in cls or "Close" in cls
            is_close_named = any(kw in name for kw in ["关闭", "Close", "close", "×", "X"])
            small_icon = 8 <= w <= 80 and 8 <= h <= 60

            if in_top_right and small_icon and (is_button_like or is_close_named):
                # 评分：名字直接命中"关闭/Close"优先；越靠右上角越优先
                pri_name = 0 if is_close_named else 1
                pri_pos = (win_right - rect.right) + (rect.top - win_top)
                candidates.append((pri_name, pri_pos, rect, name, ctype, cls))
        except Exception:
            pass
        try:
            for child in control.children():
                _walk(child)
        except Exception:
            pass

    _walk(main_win)
    if not candidates:
        return None
    candidates.sort(key=lambda t: (t[0], t[1]))
    pri_name, pri_pos, rect, name, ctype, cls = candidates[0]
    print(f"UIA 命中 X 按钮候选: name={name!r}, type={ctype}, class={cls}, "
          f"rect=({rect.left},{rect.top},{rect.right},{rect.bottom})")
    return rect

def _click_confirm_button(desktop, button_text, step_desc):
    """
    退出确认弹窗的多策略顺序（嵌入 Web 时 UIA 往往不完整）：
    1) 轮询 UIA 树，找名称含「确认/确定」的按钮类控件并点击
    2) 前台窗体中心点一下抢焦点，再点右下角常见主按钮区，再发 Enter（默认按钮）
    3) 多按几次 Tab 后 Enter，覆盖焦点顺序与控件数量变化的情况
    """
    _ = button_text  # 保留参数与旧调用一致，逻辑已不依赖单一文案匹配 Text
    _sleep_and_check(1.0, f"{step_desc}确认前")

    # 持续监听一小段时间，避免“弹窗刚出现时还未完成 UIA 树注册”
    end_ts = time.time() + 12
    while time.time() < end_ts:
        _check_ubank_crash(f"{step_desc}确认监听")
        if _try_uia_confirm_click(desktop):
            print(f"{step_desc} 已通过 UIA 点击确认")
            _check_ubank_crash(f"{step_desc}确认后")
            return

        # UIA 未命中时，按“前台小弹窗几何主按钮”点击（已实测更稳定）
        hwnd = win32gui.GetForegroundWindow()
        if hwnd:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = right - left
            height = bottom - top
            # 仅对中小弹窗启用，避免误点主界面
            if 320 <= width <= 1200 and 160 <= height <= 700:
                # 关闭确认弹窗中，蓝色“确认”稳定在底部偏中右
                x = int(left + width * 0.56)
                y = int(top + height * 0.84)
                print(f"{step_desc} UIA 未命中，几何点击确认: ({x}, {y}), size=({width}x{height})")
                click_at(x, y)
                _sleep_and_check(0.2, f"{step_desc}几何点击后")
                press_keys((0x0D, 0), (0x0D, 2))
                _check_ubank_crash(f"{step_desc}回车后")
                return

        _sleep_and_check(0.25, f"{step_desc}确认轮询")

    # 最终兜底：仅发送键盘确认，不做额外危险坐标点击
    print(f"{step_desc} 监听超时，改用 Enter + Tab 兜底")
    press_keys((0x0D, 0), (0x0D, 2))
    _sleep_and_check(0.3, f"{step_desc}兜底回车后")
    for _ in range(14):
        press_keys((0x09, 0), (0x09, 2))
        _sleep_and_check(0.12, f"{step_desc}兜底Tab")
    press_keys((0x0D, 0), (0x0D, 2))
    _check_ubank_crash(f"{step_desc}兜底确认后")
    print(f"{step_desc} 兜底确认序列已发送")
