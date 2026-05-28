"""Bank branch picker helpers for the transfer form."""
import sys
import time
from pathlib import Path

import pyautogui

from .runtime import click_fixed, screenshot
from .ui_helpers import _find_label, _set_clipboard


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
from bank_branch_matcher import normalize_bank_text, score_bank_category_candidate  # noqa: E402


def _simplify(value):
    """去掉常见公司后缀和空白，便于做支行/银行的对比。"""
    return normalize_bank_text(value)

def _panel_has_bank(main_win, bank_name):
    """检测银行 logo 面板中是否已经露出指定银行名。"""
    found = {"ok": False}

    def _walk(control):
        if found["ok"]:
            return
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            if name and name.strip() == bank_name and control.is_visible():
                found["ok"] = True
                return
        except Exception:
            pass
        try:
            for child in control.children():
                _walk(child)
        except Exception:
            pass

    _walk(main_win)
    return found["ok"]


def dump_bank_debug_controls(main_win, stage, keywords=None, limit=80):
    """打印收款行相关的可见 UIA 控件，辅助定位下拉/查询/填入问题。"""
    if main_win is None:
        print(f"    [收款行调试:{stage}] main_win=None")
        return
    if keywords is None:
        keywords = [
            "收款行", "开户", "银行",
            "查询", "搜索", "关键", "行号", "填入", "确定", "选择",
        ]
    rows = []

    def _walk(control, depth=0):
        try:
            if not control.is_visible():
                return
            info = control.element_info
            name = info.name if hasattr(info, "name") and info.name else ""
            ctrl_type = str(info.control_type) if hasattr(info, "control_type") else ""
            cls = info.class_name if hasattr(info, "class_name") else ""
            if name and any(k in name for k in keywords):
                r = control.rectangle()
                rows.append((depth, name.strip(), ctrl_type, cls, r.left, r.top, r.right, r.bottom))
        except Exception:
            pass
        try:
            for child in control.children():
                _walk(child, depth + 1)
        except Exception:
            pass

    _walk(main_win)
    print(f"    [收款行调试:{stage}] 匹配控件 {len(rows)} 个")
    for depth, name, ctrl_type, cls, left, top, right, bottom in rows[:limit]:
        print(f"      depth={depth} name={name!r} type={ctrl_type} class={cls} rect=({left},{top},{right},{bottom})")
    if len(rows) > limit:
        print(f"      ... 还有 {len(rows) - limit} 个控件未打印")


def is_bank_panel_open(main_win):
    """判断收款行搜索面板是否仍然展开。"""
    found = {"ok": False}

    def _walk(control):
        if found["ok"]:
            return
        try:
            if not control.is_visible():
                return
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            if name and ("查询结果" in name or "请输入关键词或完整行号查询" in name):
                found["ok"] = True
                return
        except Exception:
            pass
        try:
            for child in control.children():
                _walk(child)
        except Exception:
            pass

    _walk(main_win)
    return found["ok"]


def click_field_dropdown(main_win, label_text, verify_bank=None, max_retry=3):
    label = _find_label(main_win, label_text)
    if not label:
        print(f"    未找到标签 [{label_text}]")
        return False
    # 以标签位置为基准：标签右侧 200 像素是下拉框中心区域
    try:
        lr = label.rectangle()
    except Exception:
        return False
    cx = lr.right + 200
    cy = lr.mid_point().y
    print(f"    标签 [{label_text}] rect=({lr.left},{lr.top},{lr.right},{lr.bottom}) -> 点 ({cx}, {cy})")
    screenshot("bank_debug_01_下拉前")
    dump_bank_debug_controls(main_win, "下拉前")
    for attempt in range(max_retry):
        try:
            pyautogui.click(cx, cy)
            print(f"    点击下拉 尝试{attempt + 1}")
            time.sleep(1.2)
            screenshot(f"bank_debug_02_下拉后_尝试{attempt + 1}")
            dump_bank_debug_controls(main_win, f"下拉后_尝试{attempt + 1}")
            if is_bank_panel_open(main_win):
                return True
            if not verify_bank or _panel_has_bank(main_win, verify_bank):
                return True
            print(f"    面板未打开或未露出 [{verify_bank}]，重试")
        except Exception as e:
            print(f"    点击异常: {e}")
    return False


def _collect_row_texts(
    main_win,
    btn_rect,
    y_tolerance=14,
    x_left_extent=700,
    x_right_padding=20,
):
    """收集 [填入] 按钮所属结果行附近的可见文本控件名称。

    限制范围（避免误扫整窗口同 y 行）：
      - y 中心：abs(text.cy - btn.cy) <= y_tolerance
      - x 中心：btn.left - x_left_extent <= text.cx <= btn.left + x_right_padding
        即只看按钮左侧约一行宽度内的文本，按钮右侧只留极小余量
      - 排除按钮自身文字 "填入"

    返回该范围内的所有控件名（顺序按 UIA 树遍历），不含 None/空白。
    """
    btn_cy = btn_rect.mid_point().y
    btn_left = btn_rect.left
    x_min = btn_left - x_left_extent
    x_max = btn_left + x_right_padding
    print(
        f"    [_collect_row_texts] 局部扫描范围 y={btn_cy}±{y_tolerance}, "
        f"x=[{x_min}, {x_max}] (btn.left={btn_left})"
    )

    texts = []

    def _walk(control):
        try:
            if not control.is_visible():
                return
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            if name:
                clean = name.strip()
                if clean and clean != "填入":
                    r = control.rectangle()
                    if r.width() > 0 and r.height() > 0:
                        cx = r.mid_point().x
                        cy = r.mid_point().y
                        if abs(cy - btn_cy) <= y_tolerance and x_min <= cx <= x_max:
                            texts.append(clean)
        except Exception:
            pass
        try:
            for child in control.children():
                _walk(child)
        except Exception:
            pass

    _walk(main_win)
    return texts


def click_match_fill_button(main_win, expected_bank=None, timeout=5):
    """找 '智能匹配结果' 行旁的 '填入' 按钮并点击（fail-closed，按银行大类精确校验）。

    校验策略：
      - 收集每个 [填入] 同行可见文本，逐个 _simplify() 与 _simplify(expected_bank) 做等值判断
      - 仅当存在 唯一一行 同行精确命中目标银行大类（即与 expected_bank 完全同名）才点击该行
      - 0 命中：继续轮询直至超时
      - >1 命中（多个 [填入] 行同时精确命中）：截图后 return False
      - 宽松包含（如 "<目标银行>XYZ"）、仅出现支行/相似文本，都不算命中
    """
    target_bank_simple = _simplify(expected_bank or "")
    if not target_bank_simple:
        print("    [填入] 校验：未提供目标银行 (expected_bank)，拒绝点击避免误命中")
        return False

    end = time.time() + timeout
    attempt = 0
    while time.time() < end:
        attempt += 1
        fill_buttons = []

        def _walk(control):
            try:
                if not control.is_visible():
                    return
                name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
                if name and name.strip() == "填入":
                    r = control.rectangle()
                    if r.width() > 3 and r.height() > 3:
                        fill_buttons.append((control, r))
            except Exception:
                pass
            try:
                for child in control.children():
                    _walk(child)
            except Exception:
                pass

        _walk(main_win)

        matched_rows = []  # [(btn, btn_rect, row_texts, exact_hits)]
        for btn, btn_rect in fill_buttons:
            row_texts = _collect_row_texts(main_win, btn_rect)
            exact_hits = [
                t
                for t in row_texts
                if score_bank_category_candidate(expected_bank, t).accepted_exact_or_safe
            ]
            print(
                f"    候选 [填入] rect=({btn_rect.left},{btn_rect.top},{btn_rect.right},{btn_rect.bottom}) "
                f"同行={row_texts} 精确银行命中={exact_hits}"
            )
            if exact_hits:
                matched_rows.append((btn, btn_rect, row_texts, exact_hits))

        if len(matched_rows) == 1:
            btn, btn_rect, row_texts, exact_hits = matched_rows[0]
            screenshot("bank_debug_填入按钮_点击前")
            pyautogui.click(btn_rect.mid_point().x, btn_rect.mid_point().y)
            print(
                f"    已点 [填入] @ ({btn_rect.mid_point().x}, {btn_rect.mid_point().y}) "
                f"唯一精确同行银行命中={exact_hits}"
            )
            time.sleep(0.6)
            screenshot("bank_debug_填入按钮_点击后")
            return True
        if len(matched_rows) > 1:
            print(
                f"    多个 [填入] 同行精确命中目标银行 [{expected_bank}] (rows={len(matched_rows)})，停止以避免误点"
            )
            screenshot("bank_debug_多行精确命中")
            return False

        if not fill_buttons and attempt in (1, 4, 8):
            print(f"    第{attempt}次未找到 [填入]，打印可见控件快照")
            dump_bank_debug_controls(main_win, f"找填入_第{attempt}次")
        time.sleep(0.4)
    print(f"    未发现唯一精确命中目标银行 [{expected_bank}] 的 [智能匹配 -> 填入]")
    return False


def pick_branch_option(main_win, keyword, target_bank):
    """在收款行弹出面板里选择银行大类（fail-closed）。

    业务规则：收款行不需要精确到支行，最终填入的应是银行大类（target_bank 即
    业务方传入的银行大类全名）。本函数把 UIA 树里 _simplify(name) ==
    _simplify(target_bank) 的可见控件视为精确命中。

    命中数处理：
      - 1 → 点击（同行有"填入"按钮则点按钮，否则点银行文本本身）
      - 0 → 试着用 keywords 触发搜索后再次查找；全部尝试失败 → return False
      - >1 → 截图 + return False，绝不模糊提交

    keyword 仅作搜索关键字辅助；最终成功标准只能是 target_bank 精确同名。
    """
    raw_keywords = list(keyword) if isinstance(keyword, (list, tuple)) else [keyword]
    keywords = []
    if target_bank:
        keywords.append(target_bank)
    for kw in raw_keywords:
        if kw and kw not in keywords:
            keywords.append(kw)
    keywords = [kw for kw in keywords if kw]
    print(f"    收款行（银行大类）搜索 keywords={keywords!r}, target_bank={target_bank!r}")
    screenshot("bank_debug_03_进入银行大类搜索函数")
    dump_bank_debug_controls(main_win, "进入银行大类搜索函数")

    def _click_target_fill(stage):
        target_simple = _simplify(target_bank or "")
        if not target_simple:
            print(f"    {stage}: target_bank 为空，拒绝点击")
            return False

        exact_targets = []
        fill_buttons = []

        def _walk(control):
            try:
                if not control.is_visible():
                    return
                name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
                if name:
                    clean_name = name.strip()
                    if clean_name:
                        r = control.rectangle()
                        if r.width() > 0 and r.height() > 0:
                            score = score_bank_category_candidate(target_bank, clean_name)
                            if score.accepted_exact_or_safe:
                                exact_targets.append((control, r, clean_name, score))
                            elif clean_name == "填入":
                                fill_buttons.append((control, r))
            except Exception:
                pass
            try:
                for child in control.children():
                    _walk(child)
            except Exception:
                pass

        _walk(main_win)

        if not exact_targets:
            return False
        if is_bank_panel_open(main_win):
            # When the bank panel is open, UIA can still expose the form's
            # smart-match row behind it. Prefer visible choices inside the
            # panel body and ignore the obscured row near the field itself.
            panel_targets = [
                item for item in exact_targets
                if item[1].top >= 360 and item[1].left >= 680
            ]
            if panel_targets:
                exact_targets = panel_targets
        if len(exact_targets) > 1:
            names = [item[2] for item in exact_targets]
            print(f"    {stage}: 出现 {len(exact_targets)} 个精确同名银行大类 {names}，停止以避免误点")
            screenshot(f"bank_debug_多个精确银行_{stage}")
            return False

        exact_targets.sort(key=lambda item: (-item[3].score, item[1].top, item[1].left))
        _, target_rect, hit_name, hit_score = exact_targets[0]

        same_row_buttons = [
            (ctrl, rect)
            for ctrl, rect in fill_buttons
            if abs(rect.mid_point().y - target_rect.mid_point().y) <= 14
            and rect.left >= target_rect.right - 10
        ]
        if same_row_buttons:
            _, click_rect = sorted(same_row_buttons, key=lambda item: item[1].left)[0]
            label = "同行填入"
        else:
            click_rect = target_rect
            label = "目标银行文本"

        print(
            f"    {stage}: 命中目标银行大类({hit_name!r})，"
            f"score={hit_score.score}, reason={hit_score.reason}，点击{label} "
            f"rect=({click_rect.left},{click_rect.top},{click_rect.right},{click_rect.bottom})"
        )
        pyautogui.click(click_rect.mid_point().x, click_rect.mid_point().y)
        time.sleep(1.0)
        screenshot(f"bank_debug_命中目标银行_{stage}")
        if is_bank_panel_open(main_win) and click_rect != target_rect:
            print("    点击同行填入后面板仍展开，改点目标银行文本")
            pyautogui.click(target_rect.mid_point().x, target_rect.mid_point().y)
            time.sleep(1.0)
            screenshot(f"bank_debug_命中目标银行文本_{stage}")
        if is_bank_panel_open(main_win):
            print("    点击目标银行大类后面板仍展开")
            return False
        return True

    if _click_target_fill("搜索前"):
        return True

    # 找搜索框
    search = None

    def _find_search(control):
        nonlocal search
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            ctrl_type = str(control.element_info.control_type) if hasattr(control.element_info, "control_type") else ""
            cls = control.element_info.class_name if hasattr(control.element_info, "class_name") else ""
            is_edit = any(kw in cls for kw in ["Edit", "edit"]) or any(kw in ctrl_type for kw in ["Edit", "Document"])
            if is_edit and control.is_visible() and ("关键" in name or "行号" in name or "查询" in name):
                search = control
                return True
        except Exception:
            pass
        try:
            for child in control.children():
                if _find_search(child):
                    return True
        except Exception:
            pass
        return False

    _find_search(main_win)
    if not search:
        print("    未找到收款行搜索框")
        screenshot("bank_debug_04_未找到搜索框")
        dump_bank_debug_controls(main_win, "未找到搜索框")
        return False

    for query_index, query_text in enumerate(keywords, start=1):
        try:
            r = search.rectangle()
            print(f"    搜索尝试{query_index}: {query_text!r}")
            print(f"    找到搜索框 name={search.element_info.name!r} type={search.element_info.control_type} rect=({r.left},{r.top},{r.right},{r.bottom})")
            pyautogui.click(r.mid_point().x, r.mid_point().y)
            time.sleep(0.3)
            screenshot("bank_debug_05_点击搜索框")
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.1)
            pyautogui.press("delete")
            time.sleep(0.1)
            _set_clipboard(query_text)
            time.sleep(0.2)
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.8)
            screenshot("bank_debug_06_输入搜索关键字")
            dump_bank_debug_controls(main_win, f"输入搜索关键字后_{query_index}")
            # 点击 '查询' 按钮
            query_btn = {"c": None}

            def _walk_q(control):
                if query_btn["c"]:
                    return
                try:
                    name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
                    if name and name.strip() == "查询" and control.is_visible():
                        r = control.rectangle()
                        if r.width() > 3 and r.height() > 3:
                            query_btn["c"] = control
                            return
                except Exception:
                    pass
                try:
                    for child in control.children():
                        _walk_q(child)
                except Exception:
                    pass

            _walk_q(main_win)
            if query_btn["c"]:
                r = query_btn["c"].rectangle()
                print(f"    找到 [查询] rect=({r.left},{r.top},{r.right},{r.bottom})")
                screenshot("bank_debug_07_查询按钮_点击前")
                pyautogui.click(r.mid_point().x, r.mid_point().y)
                print("    已点 [查询]")
            else:
                print("    未找到 [查询] 按钮，回车查询")
                pyautogui.press("enter")
            time.sleep(1.8)
            screenshot("bank_debug_08_查询后")
            dump_bank_debug_controls(main_win, f"查询后_{query_index}")
        except Exception as e:
            print(f"    搜索框输入失败: {e}")
            screenshot("bank_debug_搜索框输入失败")
            continue

        if _click_target_fill(f"查询后_{query_index}"):
            return True

    print(f"    未能通过任何关键字精确选择目标银行大类 [{target_bank}]")
    return False
