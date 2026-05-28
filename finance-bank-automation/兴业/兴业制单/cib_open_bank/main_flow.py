"""Top-level CIB enterprise banking automation flow."""
import hashlib
import os
import sys
import time

import pyautogui
from pywinauto import Desktop

from .form_controls import (
    click_control_by_name,
    click_field_dropdown,
    click_match_fill_button,
    click_next_step,
    click_submit,
    fill_field_by_label,
    pick_branch_option,
    scroll_to_form_top,
    scroll_to_bank_section,
)
from .runtime import (
    COORD_SCALE,
    SHORTCUT_PATH,
    SOURCE_DPI_SCALE,
    TARGET_DPI_SCALE,
    all_off_usb_hub_ports,
    click_fixed,
    fixed_point,
    get_active_window_origin,
    load_config,
    screenshot,
    select_usb_hub_port,
    set_active_window_origin,
    set_screenshot_prefix,
)
from .windows import (
    close_bank_windows,
    dismiss_ukey_notice,
    find_ukey_dialog,
    find_window,
    focus_ukey_password_field,
    handle_ukey_dialog,
)

# 出厂占位数据：故意不是真实收款方。每个字段都含 _PLACEHOLDER_TOKENS
# 之一，使 _has_placeholder_transfer_data() 返回 True，从而即使误设
# CIB_ALLOW_SUBMIT=1 也会被占位闸门拦截、不会提交。真实提交前必须由
# 调用方/操作人用真实数据替换全部字段。
DEFAULT_TRANSFER = {
    "label": "SAMPLE_请勿用于真实提交",
    "amount": "0.01",
    "acct_no": "000000000000000",
    "acct_name": "示例收款方公司",
    "bank": "示例银行",
    "branch_full": "示例银行示例支行",
    "branch_queries": ["示例支行", "示例银行"],
    "purpose": "请勿用于真实提交",
}


_FINGERPRINT_FIELDS = ("amount", "acct_no", "acct_name", "bank", "branch_full", "purpose")
_PLACEHOLDER_TOKENS = ("示例", "000000000000000", "请勿用于真实提交")


def compute_transfer_fingerprint(transfer):
    """根据 transfer 关键字段生成 sha256 前 12 位指纹，用作审计留痕。"""
    parts = [str(transfer.get(field, "") or "").strip() for field in _FINGERPRINT_FIELDS]
    payload = "|".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def _has_placeholder_transfer_data(transfer):
    """Avoid fast submission when DEFAULT_TRANSFER is still a template."""
    required = ("amount", "acct_no", "acct_name", "bank", "branch_full", "purpose")
    values = [str(transfer.get(field, "") or "").strip() for field in required]
    if any(not value for value in values):
        return True
    return any(token in value for value in values for token in _PLACEHOLDER_TOKENS)


def _normalize_amount(value):
    """金额规范化：去掉逗号、人民币符号、元、空格，便于做相等性比较。"""
    text = str(value or "")
    for token in (",", "，", "¥", "￥", "¥", "　", "元", " ", "\t", "\n"):
        text = text.replace(token, "")
    return text.strip()


def _digits_only(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


_NEGATIVE_KEYWORDS = ("错误", "失败", "未通过", "重新", "异常")


def _locate_login_card():
    """Find the white login card on the current CIB login screen."""
    try:
        img = pyautogui.screenshot().convert("RGB")
    except Exception:
        return None

    width, height = img.size
    px = img.load()
    runs = []
    min_x = max(width // 2, 700)
    max_x = width - 80
    min_y = 120
    max_y = max(min_y, height - 120)

    def is_white(rgb):
        r, g, b = rgb
        return r >= 248 and g >= 248 and b >= 248

    for y in range(min_y, max_y, 3):
        start = None
        for x in range(min_x, max_x):
            if is_white(px[x, y]):
                if start is None:
                    start = x
            elif start is not None:
                if x - start >= 260:
                    runs.append((y, start, x))
                start = None
        if start is not None and max_x - start >= 260:
            runs.append((y, start, max_x))

    groups = []
    for y, left, right in runs:
        matched = None
        for group in groups:
            if abs(left - group["left"]) <= 24 and abs(right - group["right"]) <= 24:
                matched = group
                break
        if matched is None:
            matched = {"lefts": [], "rights": [], "ys": [], "left": left, "right": right}
            groups.append(matched)
        matched["lefts"].append(left)
        matched["rights"].append(right)
        matched["ys"].append(y)
        matched["left"] = sorted(matched["lefts"])[len(matched["lefts"]) // 2]
        matched["right"] = sorted(matched["rights"])[len(matched["rights"]) // 2]

    candidates = []
    for group in groups:
        left = sorted(group["lefts"])[len(group["lefts"]) // 2]
        right = sorted(group["rights"])[len(group["rights"]) // 2]
        top = min(group["ys"])
        bottom = max(group["ys"])
        card_w = right - left
        card_h = bottom - top
        if 280 <= card_w <= 520 and card_h >= 260 and (left + right) / 2 > width * 0.55:
            candidates.append((card_h * card_w, left, top, right, bottom))

    if not candidates:
        return None

    _, left, top, right, bottom = max(candidates)
    # The QR-code folded corner shortens some top-row white runs, so grouping
    # by both left and right can miss the real card top. Re-anchor by the left
    # edge only after the main card body is identified.
    same_left_rows = [
        (y, row_left, row_right)
        for y, row_left, row_right in runs
        if abs(row_left - left) <= 24
        and 260 <= row_right - row_left <= 520
        and (row_left + row_right) / 2 > width * 0.55
    ]
    if same_left_rows:
        top = min(y for y, _, _ in same_left_rows)
        bottom = max(bottom, max(y for y, _, _ in same_left_rows))
    return left, top, right, bottom


def _collect_visible_text(control, texts=None, depth=0, max_depth=14):
    """递归收集 UIA 子树中可见控件的 name 文本，用于确认页字段校验。"""
    if texts is None:
        texts = []
    if depth > max_depth:
        return texts
    try:
        if not control.is_visible():
            return texts
    except Exception:
        return texts
    try:
        name = control.element_info.name if hasattr(control.element_info, "name") else ""
        if name:
            texts.append(name.strip())
    except Exception:
        pass
    try:
        for child in control.children():
            _collect_visible_text(child, texts, depth + 1, max_depth)
    except Exception:
        pass
    return texts


def _verify_confirmation_page(main_win, transfer):
    """二次校验：确认页文本必须包含 amount/acct_no/acct_name/bank/purpose，
    且不得命中负面关键词（错误/失败/未通过/重新/异常）。

    - 金额比较前规范化（去逗号、¥/￥、元、空格等）。
    - 账号比较前只保留数字。
    - 其它字段做朴素子串包含。
    返回 (ok, reason, blob_preview)。
    """
    texts = _collect_visible_text(main_win)
    blob = "\n".join(texts)

    for kw in _NEGATIVE_KEYWORDS:
        if kw in blob:
            return False, f"negative_keyword:{kw}", blob

    amount = transfer.get("amount", "")
    if not amount:
        return False, "amount", blob
    if _normalize_amount(amount) not in _normalize_amount(blob):
        return False, "amount", blob

    acct_no = transfer.get("acct_no", "")
    if not acct_no:
        return False, "acct_no", blob
    if _digits_only(acct_no) not in _digits_only(blob):
        return False, "acct_no", blob

    for key in ("acct_name", "bank", "purpose"):
        value = transfer.get(key, "")
        if not value:
            return False, key, blob
        if value not in blob:
            return False, key, blob

    return True, None, blob


def _login_candidate_rank(name, login_name):
    """Return a match rank for login candidates; lower is better."""
    text = (name or "").strip()
    wanted = (login_name or "").strip()
    if not text:
        return None

    text_upper = text.upper()
    wanted_upper = wanted.upper()
    if wanted_upper and text_upper == wanted_upper:
        return 0
    if wanted_upper and text_upper.startswith(wanted_upper):
        return 1
    if text_upper.startswith("AJ"):
        return 2
    return None


def _find_login_name_candidates(login_name, dropdown_xy):
    """在已打开的 UIA 候选列表里查找 AJ 开头的登录名候选控件。

    通过 dropdown 中心点做空间过滤，避免命中输入框内同名文字或屏幕其它区域。
    返回按匹配质量和距离 dropdown 远近排序的 (control, rect, name) 列表。
    """
    dx, dy = dropdown_xy
    desktop = Desktop(backend="uia")
    matches = []

    def _walk(control, depth=0, max_depth=14):
        if depth > max_depth:
            return
        try:
            if not control.is_visible():
                return
        except Exception:
            return
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            rank = _login_candidate_rank(name, login_name)
            if rank is not None:
                rect = control.rectangle()
                if rect.width() > 5 and rect.height() > 5:
                    cx = rect.mid_point().x
                    cy = rect.mid_point().y
                    # 候选项必须落在 dropdown 下方且横向不太偏，避免命中输入框文本本身
                    if cy >= dy + 10 and cy <= dy + 280 and abs(cx - dx) <= 280:
                        score = abs(cx - dx) + (cy - dy)
                        matches.append((rank, score, control, rect, name.strip()))
        except Exception:
            pass
        try:
            for child in control.children():
                _walk(child, depth + 1, max_depth)
        except Exception:
            pass

    for w in desktop.windows():
        try:
            title = (w.window_text() or "").strip()
            if not title:
                continue
            if "兴业" in title or "U-BANK" in title.upper() or "企业网银" in title or "联机登录" in title:
                _walk(w)
        except Exception:
            pass

    matches.sort(key=lambda x: (x[0], x[1]))
    return [(ctrl, rect, name) for _, _, ctrl, rect, name in matches]


def _click_login_name_candidate(login_name, dropdown_xy, attempts=3, between=0.4):
    """重复几次 UIA 扫描，命中即点击；找不到返回 False（让调用方走 fallback）。"""
    for i in range(attempts):
        cands = _find_login_name_candidates(login_name, dropdown_xy)
        if cands:
            ctrl, rect, actual_name = cands[0]
            cx, cy = rect.mid_point().x, rect.mid_point().y
            try:
                pyautogui.click(cx, cy)
                print(f"    UIA 命中候选 [{actual_name}] @ ({cx}, {cy})")
                return True
            except Exception as e:
                print(f"    UIA 候选点击失败: {e}")
                return False
        if i < attempts - 1:
            time.sleep(between)
    return False


_MAIN_PAGE_HINTS = ("转账付款", "查询中心")


def _main_page_visible():
    """登录后兴业主窗口里出现 _MAIN_PAGE_HINTS 任一可见控件即视为已离开登录页。"""
    desktop = Desktop(backend="uia")
    hit = {"v": False}

    def _walk(control, depth=0, max_depth=14):
        if hit["v"] or depth > max_depth:
            return
        try:
            if not control.is_visible():
                return
        except Exception:
            return
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            if name and name.strip() in _MAIN_PAGE_HINTS:
                rect = control.rectangle()
                if rect.width() > 3 and rect.height() > 3:
                    hit["v"] = True
                    return
        except Exception:
            pass
        try:
            for child in control.children():
                if hit["v"]:
                    return
                _walk(child, depth + 1, max_depth)
        except Exception:
            pass

    for w in desktop.windows():
        if hit["v"]:
            break
        try:
            title = (w.window_text() or "").strip()
            if not title:
                continue
            if "兴业" in title or "U-BANK" in title.upper() or "企业网银" in title:
                _walk(w)
        except Exception:
            pass
    return hit["v"]


def _wait_login_progressed(timeout=15):
    """点登录后等待登录被接受。

    判定优先级：
    1) 登录后网盾密码弹窗出现 -> True；
    2) 兴业主窗口里出现登录后才有的导航/菜单文本（_MAIN_PAGE_HINTS）-> True；
    仅仅登录卡片消失但还没渲染出主页面菜单的过渡态，不算成功，继续等待。

    超时仍未满足任一条件则返回 False。
    """
    end = time.time() + timeout
    while time.time() < end:
        if find_ukey_dialog():
            return True
        if _main_page_visible():
            return True
        time.sleep(0.5)
    return False


def _login_coords(win):
    card = _locate_login_card()
    if card:
        left, top, right, bottom = card
        card_w = right - left
        card_h = bottom - top
        coords = {
            "dropdown": (left + round(card_w * 0.50), top + round(card_h * 0.24)),
            "dropdown_arrow": (right - round(card_w * 0.12), top + round(card_h * 0.24)),
            "password": (left + round(card_w * 0.50), top + round(card_h * 0.33)),
            "checkbox": (left + round(card_w * 0.09), top + round(card_h * 0.38)),
            "login_btn": (left + round(card_w * 0.50), top + round(card_h * 0.51)),
        }
        option_offset = max(34, min(48, round(card_h * 0.073)))
        print(f"  Login card detected: ({left},{top})-({right},{bottom}); coords={coords}")
        return coords, option_offset

    if os.environ.get("CIB_LOGIN_CARD_COORD_FALLBACK", "") != "1":
        print(
            "  错误: 登录卡片未识别，且未启用 CIB_LOGIN_CARD_COORD_FALLBACK=1 坐标兜底，"
            "停止流程以避免在错误位置输入登录名/密码。"
        )
        screenshot("03_0_登录卡片未识别_停止")
        sys.exit(1)

    screen_w, screen_h = pyautogui.size()
    left = getattr(win, "left", 0) if win else 0
    top = getattr(win, "top", 0) if win else 0
    win_w = getattr(win, "width", screen_w) if win else screen_w
    win_h = getattr(win, "height", screen_h) if win else screen_h
    coords = {
        "dropdown": (left + round(win_w * 0.75), top + round(win_h * 0.304)),
        "dropdown_arrow": (left + round(win_w * 0.84), top + round(win_h * 0.304)),
        "password": (left + round(win_w * 0.75), top + round(win_h * 0.351)),
        "checkbox": (left + round(win_w * 0.66), top + round(win_h * 0.378)),
        "login_btn": (left + round(win_w * 0.75), top + round(win_h * 0.448)),
    }
    option_offset = max(34, min(48, round(win_h * 0.039)))
    print(f"  Login card not detected; using window-relative coords={coords} (CIB_LOGIN_CARD_COORD_FALLBACK=1 兜底)")
    return coords, option_offset


def run_transfer_flow(stop_after_login=False, transfer=None, fill_only=False, screenshot_prefix=""):
    transfer = transfer or DEFAULT_TRANSFER
    set_screenshot_prefix(screenshot_prefix)
    config = load_config()
    ukey_pwd = config.get("LOGIN_PWD", "")   # .env 第一项：网盾密码
    cert_pwd = config.get("CERT_PWD", "")    # .env 第二项：登录页密码
    login_name = config.get("LOGIN_NAME") or os.environ.get("CIB_LOGIN_NAME", "AJBGNP")

    if not ukey_pwd or not cert_pwd:
        print("  错误: .env 中 LOGIN_PWD 或 CERT_PWD 为空，停止流程以避免 UKey 锁定/无效登录")
        sys.exit(1)

    print("=" * 50)
    print("兴业银行网银自动登录")
    print("=" * 50)

    # ========== [0a] 真实提交模式干净启动 ==========
    # CIB_ALLOW_SUBMIT=1 的真实流程：先关掉残留银行窗口、断开所有 U 盾口，
    # 再 only 通电目标端口、打开快捷方式，避免上轮残留状态干扰。
    # 仅在完整提交流程下生效，不影响 login_only / batch_fill_only。
    real_submit_mode = (
        os.environ.get("CIB_ALLOW_SUBMIT", "") == "1"
        and not stop_after_login
        and not fill_only
    )
    if real_submit_mode:
        print("\n[0a] 真实提交模式：清理残留银行窗口...")
        try:
            close_bank_windows()
        except Exception as exc:
            print(f"  close_bank_windows 异常（继续清理 USB）：{exc}")
        print("[0a] 真实提交模式：all-off 断开所有 U 盾口...")
        try:
            all_off_usb_hub_ports()
        except Exception as exc:
            print(f"  all_off_usb_hub_ports 异常（继续）：{exc}")
        time.sleep(1.5)

    # ========== [0] 选择 U 盾所在 USB Hub 端口 ==========
    print("\n[0] 选择 USB Hub 端口...")
    select_usb_hub_port()

    # ========== [1] 打开应用 ==========
    print("\n[1] 打开网银应用...")
    win = find_window()
    if not win:
        print(f"  未找到窗口，打开快捷方式: {SHORTCUT_PATH}")
        os.startfile(SHORTCUT_PATH)
        for i in range(30):
            time.sleep(1)
            win = find_window()
            if win:
                break
            print(f"  等待窗口... ({i+1}s)")
        if not win:
            print("  错误: 打开快捷方式后仍未找到窗口")
            sys.exit(1)
        time.sleep(3)

    print(f"  窗口: '{win.title}' @ ({win.left}, {win.top}) {win.width}x{win.height}")
    set_active_window_origin((win.left, win.top))
    print(f"  坐标换算: 源缩放={SOURCE_DPI_SCALE}, 目标缩放={TARGET_DPI_SCALE}, 比例={COORD_SCALE:.3f}, 窗口原点={get_active_window_origin()}")
    try:
        win.activate()
    except Exception as e:
        print(f"  窗口激活异常，继续执行: {e}")
    if not win.visible:
        win.show()
    time.sleep(1)
    screenshot("01_开始前")

    # ========== [2] 网盾密码（启动阶段，可能不出现） ==========
    print("\n[2] 检测网盾密码对话框...")
    time.sleep(2)

    def type_with_capslock(s, interval=0.1):
        caps_on = False
        for ch in s:
            need_caps = ch.isalpha() and ch.isupper()
            if need_caps != caps_on:
                pyautogui.press("capslock")
                time.sleep(0.1)
                caps_on = need_caps
            pyautogui.write(ch.lower() if ch.isalpha() else ch, interval=0)
            time.sleep(interval)
        if caps_on:
            pyautogui.press("capslock")
            time.sleep(0.1)

    ukey_win = None
    for i in range(5):
        ukey_win = find_ukey_dialog()
        if ukey_win:
            break
        time.sleep(1)

    if ukey_win:
        print(f"  检测到网盾密码对话框: '{ukey_win.title}'，开始输入密码...")
        focus_ukey_password_field(ukey_win)
        type_with_capslock(ukey_pwd)
        print(f"  已输入网盾密码 (长度:{len(ukey_pwd)})")
        time.sleep(0.4)
        screenshot("网盾_输密码_启动")
        pyautogui.press("enter")
        time.sleep(1.5)
        screenshot("网盾_确认_启动")
        if find_ukey_dialog():
            if _locate_login_card():
                print("  网盾密码框已关闭，登录卡片已出现；忽略残留弹窗检测")
            else:
                pyautogui.hotkey("ctrl", "a")
                pyautogui.press("backspace")
                print("  错误: 网盾密码框仍未关闭，停止后续流程")
                sys.exit(1)
    else:
        print("  未检测到网盾密码对话框，跳过")

    # 进入登录页前等待登录卡片渲染稳定
    print("  等待登录卡片渲染稳定...")
    time.sleep(2.5)

    # ========== [3] 登录页流程 ==========
    print("\n[3] 登录页流程...")
    # 坐标基于 1920x1080 完整截图测量（登录卡片位于右侧）
    coords, option_offset = _login_coords(win)

    LOGIN_MAX_ATTEMPTS = 2

    def _select_login_name(attempt_no):
        """优先 UIA 文本匹配候选；失败再回退到旧的输入+坐标策略。"""
        print(f"  [3.1] 选择登录名 [{login_name}]（UIA 优先）...")
        pyautogui.click(*coords["dropdown"])
        time.sleep(0.9)
        screenshot(f"03_1a_打开下拉_第{attempt_no}次")

        if _click_login_name_candidate(login_name, coords["dropdown"]):
            time.sleep(0.8)
            screenshot(f"03_1b_UIA命中_第{attempt_no}次")
            return "uia_direct"

        print("    UIA 未命中候选项，回退手动输入登录名...")
        pyautogui.click(*coords["dropdown"])
        time.sleep(0.4)
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyautogui.press("backspace")
        time.sleep(0.1)
        pyautogui.write(login_name, interval=0.05)
        time.sleep(0.8)
        screenshot(f"03_1c_手动输入_第{attempt_no}次")

        if _click_login_name_candidate(login_name, coords["dropdown"]):
            time.sleep(0.8)
            screenshot(f"03_1d_UIA命中(输入后)_第{attempt_no}次")
            return "uia_after_input"

        if os.environ.get("CIB_LOGIN_COORD_FALLBACK", "") != "1":
            print("    UIA 未精确命中候选项，且未启用 CIB_LOGIN_COORD_FALLBACK=1 坐标兜底，停止流程")
            screenshot("03_1_登录名候选未精确命中_停止")
            sys.exit(1)

        opt_x = coords["dropdown"][0]
        opt_y = coords["dropdown"][1] + option_offset
        pyautogui.click(opt_x, opt_y)
        time.sleep(1.0)
        screenshot(f"03_1e_坐标兜底_第{attempt_no}次")
        return "coord_fallback"

    for attempt in range(1, LOGIN_MAX_ATTEMPTS + 1):
        print(f"\n  === 登录尝试 #{attempt}/{LOGIN_MAX_ATTEMPTS} ===")
        method = _select_login_name(attempt)
        print(f"  [3.1.结果] 选择方式: {method}")

        print(f"  [3.2] 点击密码框并输入密码（第{attempt}次）...")
        pyautogui.click(*coords["password"])
        time.sleep(0.5)
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyautogui.press("backspace")
        time.sleep(0.1)
        type_with_capslock(cert_pwd, interval=0.05)
        print(f"    已输入登录密码 (长度:{len(cert_pwd)})")
        time.sleep(0.5)
        screenshot(f"03_2_输密码_第{attempt}次")

        print(f"  [3.3] 勾选同意复选框（第{attempt}次）...")
        pyautogui.click(*coords["checkbox"])
        time.sleep(0.8)
        screenshot(f"03_3_勾同意_第{attempt}次")

        print(f"  [3.4] 点击登录按钮（第{attempt}次）...")
        pyautogui.click(*coords["login_btn"])
        time.sleep(2)
        screenshot(f"03_4_点登录_第{attempt}次")

        print("  [3.5] 等待登录被接受（网盾后续弹窗或登录卡片消失）...")
        if _wait_login_progressed(timeout=12):
            print(f"  登录被接受（第{attempt}次尝试成功）")
            screenshot(f"03_5_登录成功_第{attempt}次")
            break

        if attempt >= LOGIN_MAX_ATTEMPTS:
            print(f"  错误: 登录{LOGIN_MAX_ATTEMPTS}次后仍停留登录页，停止流程（不进入填表）")
            screenshot("03_5_登录失败_停止")
            sys.exit(1)

        print(f"  警告: 第{attempt}次登录后仍停留登录页，准备清空密码框、重新选登录名后再试一次...")
        screenshot(f"03_5_登录未成功_准备重试_第{attempt}次")
        pyautogui.click(*coords["password"])
        time.sleep(0.3)
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyautogui.press("backspace")
        time.sleep(0.3)

    # ========== [4] 登录后若再次出现网盾密码对话框 ==========
    print("\n[4] 处理登录后可能出现的网盾密码对话框...")
    main_page_ready = False
    for _ in range(16):
        if _main_page_visible():
            main_page_ready = True
            break
        time.sleep(0.5)
    if main_page_ready:
        print("  已进入网银主页面，跳过登录后网盾弹窗处理")
    else:
        handle_ukey_dialog(ukey_pwd, wait_seconds=10, tag="_登录后")
    dismiss_ukey_notice(wait_seconds=3)
    if stop_after_login:
        print("\n[4.1] 已登录完成，保留 U-BANK/网银窗口和 U 盾连接")
        screenshot("04_登录完成")
        return

    # ========== [5] 进入单笔转账（UIA 按控件名识别） ==========
    print("\n[5] 导航到 转账付款 -> 单笔转账 ...")
    time.sleep(2)
    desktop = Desktop(backend="uia")
    main_win = None
    for w in desktop.windows():
        try:
            if w.window_text() and "兴业" in w.window_text():
                main_win = w
                break
        except Exception:
            pass
    if main_win is None:
        print("  UIA 未找到兴业主窗口，回退坐标点击")
        click_fixed(368, 167, "转账付款")
        time.sleep(1.2)
        screenshot("05_1_转账付款")
        click_fixed(162, 289, "单笔转账")
    else:
        print("  [5.1] 点击 '转账付款' ...")
        click_control_by_name(main_win, "转账付款")
        time.sleep(1.2)
        screenshot("05_1_转账付款")

        print("  [5.2] 点击 '单笔转账' ...")
        menu_left_top = fixed_point(100, 190)
        menu_right_bottom = fixed_point(320, 410)
        single_transfer_bounds = (*menu_left_top, *menu_right_bottom)
        if not click_control_by_name(
            main_win,
            "单笔转账",
            timeout=3,
            click_bounds=single_transfer_bounds,
        ):
            click_fixed(162, 289, "单笔转账")
    time.sleep(2)
    screenshot("05_2_单笔转账")

    # ========== [6] 填写转账表单 ==========
    print("\n[6] 填写转账表单...")
    time.sleep(6)

    desktop_uia = Desktop(backend="uia")
    main_win = None
    for w in desktop_uia.windows():
        try:
            if w.window_text() and "兴业" in w.window_text():
                main_win = w
                break
        except Exception:
            pass
    if main_win is None:
        print("  错误: 进入填表阶段后未找到兴业主窗口，停止流程")
        screenshot("06_0_主窗口未找到_停止")
        sys.exit(1)
    else:
        AMOUNT = transfer.get("amount", "")
        ACCT_NO = transfer.get("acct_no", "")
        ACCT_NAME = transfer.get("acct_name", "")
        BANK = transfer.get("bank", "")
        BRANCH_FULL = transfer.get("branch_full", "")
        BRANCH_QUERY = transfer.get("branch_queries") or transfer.get("branch_query", BRANCH_FULL)
        PURPOSE_TEXT = transfer.get("purpose", "合同付款")
        TRANSFER_LABEL = transfer.get("label", "")

        print(f"  当前填单: {TRANSFER_LABEL}")
        print(f"    金额={AMOUNT} 收款账号={ACCT_NO}")
        print(f"    收款户名={ACCT_NAME}")
        print(f"    开户行={BRANCH_FULL}")

        print("  [6.1] 金额")
        if not fill_field_by_label(main_win, "金额", AMOUNT, use_clipboard=False):
            print("  错误: 未找到金额输入框，可能未进入单笔转账页面，停止后续下一步/提交")
            screenshot("06_1_金额未找到_停止")
            sys.exit(1)
        screenshot("06_1_金额")

        print("  [6.2] 选 单位账户")
        if not click_control_by_name(main_win, "单位账户", timeout=3):
            print("  错误: 未找到 [单位账户] 控件，停止后续下一步/提交")
            screenshot("06_2_单位账户未找到_停止")
            sys.exit(1)
        time.sleep(0.5)

        print("  [6.3] 收款账号")
        if not fill_field_by_label(main_win, "收款账号", ACCT_NO, use_clipboard=False):
            print("  错误: 收款账号填充失败，停止后续下一步/提交")
            screenshot("06_3_收款账号未填_停止")
            sys.exit(1)

        print("  [6.4] 收款户名")
        if not fill_field_by_label(main_win, "收款户名", ACCT_NAME, use_clipboard=True):
            print("  错误: 收款户名填充失败，停止后续下一步/提交")
            screenshot("06_4_收款户名未填_停止")
            sys.exit(1)
        screenshot("06_4_户名")

        print("  [6.5] 收款行（按银行大类精确匹配）")
        scroll_to_bank_section()
        # 策略1：智能匹配结果的 '填入' 按钮（同行必须精确命中 BANK 银行大类）
        if click_match_fill_button(
            main_win,
            expected_bank=BANK,
            timeout=3,
        ):
            print("    策略1 成功：使用智能匹配")
            time.sleep(1.0)
        else:
            print("    策略2：面板搜索")
            if not click_field_dropdown(main_win, "收款行", verify_bank=BANK, max_retry=3):
                print("  错误: 未能打开收款行搜索面板（panel 中找不到银行 logo），停止后续下一步/提交")
                screenshot("06_5_收款行面板未打开_停止")
                sys.exit(1)
            time.sleep(0.8)
            screenshot("06_5a_面板")
            if not pick_branch_option(main_win, BRANCH_QUERY, BANK):
                print("  错误: 未能精确选择银行大类 [{}]，停止后续下一步/提交".format(BANK))
                screenshot("06_5_收款行未选中_停止")
                sys.exit(1)
        screenshot("06_5b_选完支行")
        # 刷新 UIA 树快照
        time.sleep(1)
        for w in desktop_uia.windows():
            try:
                if w.window_text() and "兴业" in w.window_text():
                    main_win = w
                    break
            except Exception:
                pass

        print("  [6.6] 用途 (直接填写)")
        if not fill_field_by_label(main_win, "用途", PURPOSE_TEXT, use_clipboard=True):
            print("  错误: 用途填充失败，停止后续下一步/提交")
            screenshot("06_6_用途未填_停止")
            sys.exit(1)
        screenshot("06_6_用途")

        screenshot("06_全部填完")

        if fill_only:
            scroll_to_form_top()
            screenshot("06_填单完成_上半页_关闭前")
            scroll_to_bank_section()
            screenshot("06_填单完成_下半页_关闭前")
            screenshot("06_填单完成_关闭前")
            print("  [安全模式] 已填完并截图；跳过 下一步/提交。")
            return

        allow_submit = os.environ.get("CIB_ALLOW_SUBMIT", "") == "1"
        audit_fingerprint = compute_transfer_fingerprint(transfer)
        print(f"  [审计] 当前 transfer fingerprint = {audit_fingerprint}（仅留痕，不作为提交闸门）")

        def _abort_before_submit(tag):
            scroll_to_form_top()
            screenshot(f"06_7_未提交_上半页_{tag}")
            scroll_to_bank_section()
            screenshot(f"06_7_未提交_下半页_{tag}")

        if not allow_submit:
            print("  [安全闸门] 未设置 CIB_ALLOW_SUBMIT=1，已跳过 下一步/提交。")
            _abort_before_submit("无ALLOW_SUBMIT")
            return

        if _has_placeholder_transfer_data(transfer):
            print("  [制单闸门] 当前 transfer 含空值或占位模板内容，已跳过 下一步/提交。")
            _abort_before_submit("占位数据")
            return

        print("  [快速制单] CIB_ALLOW_SUBMIT=1，确认无需逐笔指纹；继续执行 下一步/提交。")

        print("  [6.7] 点击下一步")
        if not click_next_step(main_win):
            print("  错误: 未找到 [下一步] 按钮，停止流程（不进入提交页）")
            screenshot("06_7_下一步未点_停止")
            sys.exit(1)
        time.sleep(2)
        screenshot("06_7_下一步")

        # 刷新 UIA 树到确认页，下一步后页面已切换
        time.sleep(1)
        confirm_win = None
        for w in Desktop(backend="uia").windows():
            try:
                if w.window_text() and "兴业" in w.window_text():
                    confirm_win = w
                    break
            except Exception:
                pass
        if confirm_win is None:
            print("  错误: 下一步后未找到兴业主窗口，无法二次校验，停止流程")
            screenshot("06_8_确认页主窗口丢失")
            sys.exit(1)

        ok, reason, _blob = _verify_confirmation_page(confirm_win, transfer)
        if not ok:
            print(f"  错误: 确认页二次校验失败，原因=[{reason}]，停止流程（不点提交）")
            safe_reason = str(reason or "unknown").replace(":", "_").replace("/", "_")
            screenshot(f"06_8_确认页校验失败_{safe_reason}")
            sys.exit(1)
        print("  [6.8] 确认页二次校验通过：amount/acct_no/acct_name/bank/purpose 全部命中且无负面词")
        screenshot("06_8_确认页校验通过")

        print("  [6.9] 点击提交")
        if not click_submit(confirm_win):
            print("  错误: 未找到 [提交] 按钮，停止流程（已进入信息确认页，请人工确认是否提交）")
            screenshot("06_9_提交未点_停止")
            sys.exit(1)
        time.sleep(2)
        screenshot("06_9_提交")

    # 退出前全量截图，便于排查表单填写结果
    time.sleep(1)
    screenshot("06_退出前")

    print("\n[7] 制单流程结束，准备收尾")
    screenshot("07_收尾前")

    print("\n" + "=" * 50)
    print("执行完毕!")
    print("=" * 50)


def main():
    try:
        run_transfer_flow()
    finally:
        print("\n[8] 关闭 U-BANK/网银窗口...")
        close_bank_windows()
        print("\n[9] U-BANK/网银关闭后 all-off 断开所有 U 盾口...")
        all_off_usb_hub_ports()
        dismiss_ukey_notice(wait_seconds=5)
        set_screenshot_prefix("")


def login_only_main():
    """登录-only 入口。

    成功路径：run_transfer_flow(stop_after_login=True) 走到 [4.1] 正常 return，
    保留 U-BANK/网银窗口和 UKey 连接（不做收尾）。

    失败路径：流程中途任何 sys.exit / 异常 / 中断（包括 KeyboardInterrupt），
    都必须 close_bank_windows + all_off_usb_hub_ports + dismiss_ukey_notice，
    避免 UKey 继续通电或银行窗口悬挂。
    """
    success = False
    try:
        run_transfer_flow(stop_after_login=True)
        success = True
    finally:
        try:
            if not success:
                print("\n[login_only 失败收尾] 关闭 U-BANK/网银窗口...")
                try:
                    close_bank_windows()
                except Exception as exc:
                    print(f"  close_bank_windows 异常: {exc}")
                print("\n[login_only 失败收尾] all-off 断开所有 U 盾口...")
                try:
                    all_off_usb_hub_ports()
                except Exception as exc:
                    print(f"  all_off_usb_hub_ports 异常: {exc}")
                try:
                    dismiss_ukey_notice(wait_seconds=5)
                except Exception as exc:
                    print(f"  dismiss_ukey_notice 异常: {exc}")
        finally:
            set_screenshot_prefix("")
