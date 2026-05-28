# -*- coding: utf-8 -*-
"""
招行U-BANK 公共模块 - 登录、截图、记录保存等共用功能
"""
import ctypes
from ctypes import wintypes
import time
import os
import json
import re
import subprocess
import win32api
import win32con
import win32gui
from datetime import datetime
from dotenv import load_dotenv
from pywinauto import Desktop

# Win32 ComboBox 消息常量（招行登录名下拉是标准 Win32 ComboBox，
# 用消息读写比 UIA ValuePattern 在该窗口上更稳定）
_CB_GETCOUNT = 0x0146
_CB_GETCURSEL = 0x0147
_CB_GETLBTEXT = 0x0148
_CB_GETLBTEXTLEN = 0x0149
_CB_SETCURSEL = 0x014E
_CB_SHOWDROPDOWN = 0x014F
_CB_ERR = -1
_WM_COMMAND = 0x0111
_CBN_SELCHANGE = 1

# 显式声明 user32 函数签名 —— 没有 argtypes 时 ctypes 把 LPARAM 当 C int 处理，
# 64 位下 `ctypes.addressof(buf)` 会触发 `OverflowError: int too long to convert`。
# 这里一次性把 SendMessageW / GetWindowText* 等签名打齐，全部走 _user32.* 调用。
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.SendMessageW.restype = wintypes.LPARAM
_user32.GetParent.argtypes = [wintypes.HWND]
_user32.GetParent.restype = wintypes.HWND
_user32.GetDlgCtrlID.argtypes = [wintypes.HWND]
_user32.GetDlgCtrlID.restype = ctypes.c_int
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextLengthW.restype = ctypes.c_int
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowTextW.restype = ctypes.c_int

# 加载.env配置文件（密码存储在此）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(SCRIPT_DIR, ".env"))
SCREENSHOT_DIR = os.path.join(SCRIPT_DIR, "screenshots")


def ensure_dir(path):
    """确保目录存在"""
    if not os.path.exists(path):
        os.makedirs(path)


def click_at(x, y):
    """在指定坐标处点击鼠标左键"""
    win32api.SetCursorPos((x, y))
    time.sleep(0.1)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0)


def press_keys(*keys_with_action):
    """
    模拟组合按键，如 press_keys((0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2))
    每个元素为 (vk_code, action)，action: 0=down, 2=up
    """
    for vk, action in keys_with_action:
        win32api.keybd_event(vk, 0, action, 0)
        time.sleep(0.05)


def capture_window_screenshot(win, save_path):
    """对窗口进行截图保存"""
    try:
        from PIL import ImageGrab
        rect = win.rectangle()
        screenshot = ImageGrab.grab(bbox=(
            int(rect.left), int(rect.top),
            int(rect.right), int(rect.bottom)
        ))
        screenshot.save(save_path)
        print(f"已截图保存: {save_path}")
        return True
    except ImportError:
        print("PIL未安装，使用备用截图方式...")
        return _capture_backup(save_path)
    except Exception as e:
        print(f"截图失败: {e}")
        return False


def _capture_backup(save_path):
    """备用截图方式：使用win32gui截取整个屏幕"""
    try:
        import win32gui
        import win32ui
        from ctypes import windll
        from PIL import Image

        hwnd = win32gui.GetDesktopWindow()
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width = right - left
        height = bottom - top

        hdesktop = win32gui.GetDesktopWindow()
        hwndDC = win32gui.GetWindowDC(hdesktop)
        mfcDC = win32ui.CreateDCFromHandle(hwndDC)
        saveDC = mfcDC.CreateCompatibleDC()

        saveBitMap = win32ui.CreateBitmap()
        saveBitMap.CreateCompatibleBitmap(mfcDC, width, height)
        saveDC.SelectObject(saveBitMap)

        result = windll.user32.PrintWindow(hdesktop, saveDC.GetSafeHdc(), 0)

        try:
            if result == 1:
                bmpinfo = saveBitMap.GetInfo()
                bmpstr = saveBitMap.GetBitmapBits(True)
                img = Image.frombuffer(
                    'RGB', (bmpinfo['bmWidth'], bmpinfo['bmHeight']), bmpstr, 'raw', 'BGRX', 0, 1
                )
                img.save(save_path)
                print(f"已截图保存(备用方式): {save_path}")
                return True
            else:
                print(f"PrintWindow返回值异常: {result}")
                return False
        finally:
            mfcDC.DeleteDC()
            saveDC.DeleteDC()
            win32gui.ReleaseDC(hdesktop, hwndDC)
            win32gui.DeleteObject(saveBitMap.GetHandle())

    except Exception as e2:
        print(f"备用截图也失败: {e2}")
        return False


def save_record(data, screenshot_path):
    """将数据记录保存为JSON文件"""
    record = {
        "时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "数据": data,
        "截图路径": os.path.basename(screenshot_path) if screenshot_path else None,
    }
    # 保存到记录文件（追加模式）
    records_file = os.path.join(SCREENSHOT_DIR, "records.json")

    records = []
    if os.path.exists(records_file):
        with open(records_file, "r", encoding="utf-8") as f:
            try:
                records = json.load(f)
            except json.JSONDecodeError:
                records = []

    records.append(record)

    with open(records_file, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print("数据记录已保存")
    return record


def open_ubank():
    """打开招行U-BANK应用"""
    lnk_path = os.environ.get("CMB_UBANK_SHORTCUT_PATH")
    if not lnk_path:
        candidates = [
            os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "Desktop", "招行U-BANK.lnk"),
            os.path.join(os.path.expanduser("~"), "Desktop", "招行U-BANK.lnk"),
        ]
        lnk_path = next((path for path in candidates if os.path.exists(path)), candidates[0])
    if not os.path.exists(lnk_path):
        print(f"未找到招行 U-BANK 快捷方式: {lnk_path}")
        print("请在 招行\\.env 中设置 CMB_UBANK_SHORTCUT_PATH，或把快捷方式放到桌面并命名为 招行U-BANK.lnk")
        return False
    os.startfile(lnk_path)
    print("已启动招行U-BANK")
    return True


def login_ubank():
    """自动输入密码并登录 - 密码从.env文件读取，若已登录则跳过"""
    desktop = Desktop(backend="uia")

    # 先检查是否已经登录（直接出现主窗口而非登录窗口）
    for _ in range(10):
        for w in desktop.windows():
            t = w.window_text()
            if ("招商银行" in t or "企业银行" in t) and "联机登录" not in t and t.strip():
                print(f"检测到已登录状态: {t}")
                return True
        time.sleep(1)

    for _ in range(30):
        win = desktop.window(title="联机登录")
        if win.exists(timeout=1):
            break
        time.sleep(1)
    else:
        print("未找到招行U-BANK登录窗口")
        return None

    print(f"已连接到窗口: {win.window_text()}")
    time.sleep(1)

    # 凭据要在登录名选择成功之后才读取。任何选择失败路径都不接触 LOGIN_PWD / CERT_PWD，
    # 也不输入到任何控件、不点击登录。
    if not _select_login_account_if_requested(win):
        print("[fail-closed] 登录名选择失败，终止执行；未读取凭据、未输入密码、未点击登录")
        return False

    login_pwd = os.getenv("LOGIN_PWD", "")
    cert_pwd = os.getenv("CERT_PWD", "")
    if not login_pwd or not cert_pwd:
        print("错误: 未在.env中找到密码配置，请检查.env文件")
        return None

    # 输入登录密码（通过遍历子控件找到ATL:Edit类型的密码框）
    _fill_edit_fields(win, login_pwd, cert_pwd)

    time.sleep(0.5)

    # 点击登录按钮（通过坐标点击，避免COM错误）
    try:
        login_btn = win.child_window(auto_id="2020", class_name="Button")
        if login_btn.exists():
            rect = login_btn.rectangle()
            click_at(rect.mid_point().x, rect.mid_point().y)
            print("已点击登录按钮")
            return True
        else:
            print("未找到登录按钮")
    except Exception as e:
        print(f"点击登录按钮失败: {e}")

    return False


def _select_login_account_if_requested(win):
    """在输入密码之前选择招行 U-BANK 登录窗口的登录名。

    优先级：CMB_LOGIN_ACCOUNT_NAME（精确文本）> CMB_LOGIN_ACCOUNT_INDEX（1-based）。
    两者都未设置时返回 True，不做任何 UI 动作 —— 默认行为完全保持。

    实现以 Win32 ComboBox 消息为主路径（CB_GETCOUNT / CB_GETLBTEXT / CB_SETCURSEL /
    CB_GETCURSEL），招行登录名下拉本身就是标准 Win32 ComboBox，消息读写比 UIA 在
    该窗口上稳定得多（UIA 在桌面层会把无关 ListItem 混进来）。UIA 仅在拿不到 HWND
    时作为退而求其次的极小路径。

    fail-closed：
    - 找不到 ComboBox / 拿不到 HWND 且 UIA fallback 也失败：返回 False
    - 名称未在 Win32 候选里精确命中、或同名多于一条：返回 False
    - 1-based 序号越界或非正整数：返回 False
    - 选择后 CB_GETCURSEL 与目标 index 不一致：返回 False
    - 回读当前值为空、或与目标文本「不严格相等」：返回 False
    """
    target_name = (os.getenv("CMB_LOGIN_ACCOUNT_NAME") or "").strip()
    target_index_raw = (os.getenv("CMB_LOGIN_ACCOUNT_INDEX") or "").strip()
    target_index = None
    if not target_name and target_index_raw:
        try:
            target_index = int(target_index_raw)
        except ValueError:
            print(f"[登录名选择] CMB_LOGIN_ACCOUNT_INDEX 不是有效整数: [{target_index_raw}]")
            return False
        if target_index < 1:
            print(f"[登录名选择] CMB_LOGIN_ACCOUNT_INDEX 必须 >= 1: [{target_index_raw}]")
            return False

    if not target_name and target_index is None:
        return True

    combo = _find_login_account_combo(win)
    if combo is None:
        print("[登录名选择] 在「联机登录」窗口中未找到登录名下拉控件")
        return False

    # 当前显示值快速放行（仅 NAME 模式）：完整制单的 Feishu->网关->Codex CLI 场景里
    # 偶发出现 Win32 候选短暂全空 + UIA 展开也失败的窗口，但 ComboBox 的当前显示值
    # 已经是目标登录名 —— 此时再去扫候选只会徒劳 fail-closed。
    # 安全条件：
    #   1. 仅 NAME 模式启用；INDEX 模式不能由显示值反推下标，所以一律不走此路。
    #   2. 必须严格相等（target.strip() == current.strip()），不允许 substring /
    #      startswith / contains。
    #   3. current 为空 / 不等于目标 → 继续走原 Win32 候选重试 + CB_SHOWDROPDOWN +
    #      UIA fallback 链，最终该 fail-closed 的还是 fail-closed。
    # 此分支不输入密码、不点登录；只是替代"选择"动作本身——目标已经被选中，
    # 后续 login_ubank 才会读 LOGIN_PWD/CERT_PWD。
    if target_name:
        try:
            current_pre = _read_combo_value(combo)
        except Exception as e:
            print(f"[登录名选择] 预读 ComboBox 当前值异常（忽略，继续走候选扫描）: {e}")
            current_pre = ""
        if current_pre and current_pre.strip() == target_name.strip():
            print("[登录名选择] 当前登录名已是目标，跳过下拉选择")
            return True

    hwnd = _combo_hwnd(combo)
    expected_text = ""
    if hwnd:
        # 启动时序兜底：窗口刚出现时 ComboBox 可能还没填充候选（CB_GETCOUNT=0）。
        # 不立即 fail，先短轮询重试；若长时间仍空，再尝试 CB_SHOWDROPDOWN 展开/收起
        # 触发应用层填充；仍为空再走 UIA 窄路径 fallback。任何路径失败都不输入密码。
        items = _combo_items_win32_with_retry(hwnd)
        if items is None:
            # Win32 路径硬错误（CB_GETCOUNT/CB_GETLBTEXT 返回 CB_ERR 或异常）。
            # 切到 UIA 窄路径 fallback，仍在 ComboBox 自身子树里读取候选，绝不扫桌面。
            print("[登录名选择] Win32 读取下拉候选硬错误，切到 UIA 窄路径 fallback")
            return _select_login_account_via_uia(
                combo, target_name=target_name, target_index=target_index
            )
        if not items:
            print("[登录名选择] Win32 等待重试/展开后下拉候选仍为空，切到 UIA 窄路径 fallback")
            return _select_login_account_via_uia(
                combo, target_name=target_name, target_index=target_index
            )
        print(f"[登录名选择] Win32 下拉候选数量: {len(items)}")
        for i, t in enumerate(items, 1):
            print(f"  [{i}] {t}")
        if target_name:
            matches = [i for i, t in enumerate(items) if t == target_name]
            if not matches:
                print(f"[登录名选择] CMB_LOGIN_ACCOUNT_NAME=[{target_name}] 未在 Win32 候选中精确匹配")
                return False
            if len(matches) > 1:
                print(f"[登录名选择] 候选中存在多条同名条目，拒绝处理: {matches}")
                return False
            idx = matches[0]
            expected_text = target_name
        else:
            if target_index > len(items):
                print(f"[登录名选择] CMB_LOGIN_ACCOUNT_INDEX={target_index} 超过候选数量 {len(items)}")
                return False
            idx = target_index - 1
            expected_text = items[idx]

        if not _select_combo_item_win32(hwnd, idx):
            print("[登录名选择] Win32 CB_SETCURSEL 选中失败")
            return False
    else:
        # HWND 不可用时的窄路径 UIA fallback。
        print("[登录名选择] Win32 HWND 不可用，尝试 UIA 窄路径 fallback")
        return _select_login_account_via_uia(
            combo, target_name=target_name, target_index=target_index
        )

    time.sleep(0.3)

    # 回读校验：必须严格等于目标文本；空读回视为失败；不允许 substring / startswith。
    current = _read_combo_value(combo)
    if not current:
        print("[登录名选择] 选择后回读 ComboBox 当前值为空，fail-closed")
        return False
    if current.strip() != expected_text.strip():
        print(f"[登录名选择] 回读[{current}] != 目标[{expected_text}]，fail-closed")
        return False

    print(f"已选择指定招行登录名: [{expected_text}]")
    return True


def _find_login_account_combo(win):
    """在登录窗口里寻找登录名 ComboBox。

    优先匹配类名为 'ComboBox' 的 Win32 控件（这是招行登录名下拉的真实类型）；
    退而求其次再看 UIA control_type 包含 'ComboBox' 的项。返回第一个可见命中。
    """
    try:
        for desc in win.descendants():
            try:
                if not desc.is_visible():
                    continue
                ei = desc.element_info
                cls = (getattr(ei, "class_name", "") or "")
                ctype = str(getattr(ei, "control_type", "") or "")
                if "ComboBox" in cls or "ComboBox" in ctype:
                    return desc
            except Exception:
                continue
    except Exception as e:
        print(f"[登录名选择] 枚举登录窗口控件失败: {e}")
    return None


def _combo_hwnd(combo):
    """尽力从 pywinauto 控件上拿到底层 Win32 HWND。拿不到返回 None。"""
    for attr in ("handle", "_handle"):
        try:
            h = getattr(combo, attr, None)
            if isinstance(h, int) and h:
                return h
        except Exception:
            pass
    try:
        ei = combo.element_info
        h = getattr(ei, "handle", None)
        if isinstance(h, int) and h:
            return h
    except Exception:
        pass
    try:
        wrapper = combo.wrapper_object()
        h = getattr(wrapper, "handle", None)
        if isinstance(h, int) and h:
            return h
    except Exception:
        pass
    return None


def _combo_items_win32(hwnd):
    """用 Win32 CB_GETCOUNT / CB_GETLBTEXTLEN / CB_GETLBTEXT 读所有候选文本。

    失败返回 None；返回空列表表示 ComboBox 当前无候选。
    所有 SendMessageW 都走 `_user32`（已声明 LPARAM 签名），避免 64 位下
    `ctypes.addressof(buf)` 被当成 32 位 int 触发 OverflowError。
    """
    try:
        count = _user32.SendMessageW(hwnd, _CB_GETCOUNT, 0, 0)
        if count == _CB_ERR or count < 0:
            return None
        items = []
        for i in range(count):
            ln = _user32.SendMessageW(hwnd, _CB_GETLBTEXTLEN, i, 0)
            if ln == _CB_ERR or ln < 0:
                return None
            buf = ctypes.create_unicode_buffer(ln + 2)
            ret = _user32.SendMessageW(hwnd, _CB_GETLBTEXT, i, ctypes.addressof(buf))
            if ret == _CB_ERR:
                return None
            items.append(buf.value)
        return items
    except Exception as e:
        print(f"[登录名选择] Win32 读取下拉候选异常: {e}")
        return None


def _combo_items_wait_seconds():
    """登录名 ComboBox 等待候选填充的最长秒数。

    招行 U-BANK 联机登录窗口在某些机器上启动慢，窗口已可见、ComboBox 已挂出，
    但应用层还没把 001/002 登录名灌进 CB —— CB_GETCOUNT 暂时返回 0。
    用环境变量覆盖默认 8s；非法输入回到默认。
    """
    raw = (os.getenv("CMB_LOGIN_ACCOUNT_ITEMS_WAIT_SECONDS") or "").strip()
    if not raw:
        return 8.0
    try:
        v = float(raw)
    except ValueError:
        print(f"[登录名选择] CMB_LOGIN_ACCOUNT_ITEMS_WAIT_SECONDS 不是数字，使用默认 8s: [{raw}]")
        return 8.0
    if v < 0:
        return 0.0
    return v


def _combo_items_win32_with_retry(hwnd):
    """在等待窗口内反复用 Win32 CB_GETCOUNT 读取候选；空时尝试 CB_SHOWDROPDOWN 展开/收起。

    返回值与 `_combo_items_win32` 一致：
      - None：底层硬错误（CB_GETCOUNT 返回 CB_ERR 等），交给上层切 UIA fallback；
      - []：等待 + 展开兜底都到顶仍读不到候选，交给上层切 UIA fallback；
      - 非空 list：读到了候选文本，按现有严格匹配/越界 fail-closed 逻辑继续。

    注意：本函数只负责"把候选读出来"，不做选中、不输入密码；任意分支失败都
    不会让上层在未匹配/未确认前调用 LOGIN_PWD/CERT_PWD。
    """
    deadline = time.time() + _combo_items_wait_seconds()
    tried_expand = False
    last_seen = []
    while True:
        items = _combo_items_win32(hwnd)
        if items is None:
            return None
        if items:
            return items
        last_seen = items
        now = time.time()
        if now >= deadline:
            break
        # 半轮等待之后还空，尝试一次 CB_SHOWDROPDOWN 展开 + 立刻收起，
        # 让应用层"惰性填充"被触发；只触发一次，避免反复弹下拉。
        if not tried_expand and (deadline - now) <= _combo_items_wait_seconds() / 2:
            try:
                print("[登录名选择] Win32 候选为空，尝试 CB_SHOWDROPDOWN 展开/收起触发填充")
                _user32.SendMessageW(hwnd, _CB_SHOWDROPDOWN, 1, 0)
                time.sleep(0.3)
                _user32.SendMessageW(hwnd, _CB_SHOWDROPDOWN, 0, 0)
                tried_expand = True
            except Exception as e:
                print(f"[登录名选择] CB_SHOWDROPDOWN 异常（忽略，继续等待）: {e}")
                tried_expand = True
        time.sleep(0.5)
    return last_seen


def _select_login_account_via_uia(combo, *, target_name, target_index):
    """登录名 ComboBox 的 UIA 窄路径 fallback：仅在 ComboBox 自身子树里查 ListItem。

    保持原有严格语义：
      - CMB_LOGIN_ACCOUNT_NAME 精确相等且唯一；
      - CMB_LOGIN_ACCOUNT_INDEX 1-based、越界 fail-closed；
      - 选中后回读必须严格等于目标文本，空读回视为失败；
      - 任一失败返回 False —— 调用方据此 fail-closed，不读取 LOGIN_PWD/CERT_PWD。
    """
    if not _expand_login_combo(combo):
        print("[登录名选择] UIA 展开下拉失败，终止")
        return False
    time.sleep(0.3)
    items = _collect_combo_listitems_uia(combo)
    if not items:
        print("[登录名选择] UIA fallback 未扫到候选项，终止")
        return False
    if target_name:
        uia_hits = [(itm, txt) for itm, txt in items if txt == target_name]
        if not uia_hits:
            available = ", ".join(t for _, t in items if t)
            print(f"[登录名选择] UIA fallback 未精确命中 [{target_name}]，候选: [{available}]")
            return False
        if len(uia_hits) > 1:
            print("[登录名选择] UIA fallback 候选中存在多条同名条目，拒绝处理")
            return False
        chosen_item, chosen_text = uia_hits[0]
        expected_text = chosen_text
    else:
        if target_index is None or target_index > len(items):
            print(f"[登录名选择] UIA fallback INDEX={target_index} 越界，候选数 {len(items)}")
            return False
        chosen_item, chosen_text = items[target_index - 1]
        if not chosen_text:
            print("[登录名选择] UIA fallback 命中条目无可读文本，fail-closed")
            return False
        expected_text = chosen_text
    if not _invoke_combo_item(chosen_item):
        print(f"[登录名选择] UIA fallback 选中候选项失败: [{expected_text}]")
        return False

    time.sleep(0.3)
    current = _read_combo_value(combo)
    if not current:
        print("[登录名选择] UIA fallback 选择后回读 ComboBox 当前值为空，fail-closed")
        return False
    if current.strip() != expected_text.strip():
        print(f"[登录名选择] UIA fallback 回读[{current}] != 目标[{expected_text}]，fail-closed")
        return False

    print(f"已选择指定招行登录名（UIA fallback）: [{expected_text}]")
    return True


def _select_combo_item_win32(hwnd, index):
    """用 CB_SETCURSEL 选中 0-based index，再用 CB_GETCURSEL 自检；
    向父窗口转发 WM_COMMAND/CBN_SELCHANGE，触发应用层的选中回调。"""
    try:
        ret = _user32.SendMessageW(hwnd, _CB_SETCURSEL, index, 0)
        if ret == _CB_ERR or ret != index:
            print(f"[登录名选择] CB_SETCURSEL 返回 {ret}，与目标 index={index} 不一致")
            return False
        parent = _user32.GetParent(hwnd)
        ctrl_id = _user32.GetDlgCtrlID(hwnd)
        if parent and ctrl_id:
            wparam = ((_CBN_SELCHANGE & 0xFFFF) << 16) | (ctrl_id & 0xFFFF)
            _user32.SendMessageW(parent, _WM_COMMAND, wparam, hwnd)
        cur = _user32.SendMessageW(hwnd, _CB_GETCURSEL, 0, 0)
        if cur != index:
            print(f"[登录名选择] CB_GETCURSEL 回读 {cur} != 目标 {index}")
            return False
        return True
    except Exception as e:
        print(f"[登录名选择] CB_SETCURSEL 异常: {e}")
        return False


def _expand_login_combo(combo):
    """展开 ComboBox（UIA fallback 路径用）：优先 UIA expand，其次点击右侧下拉箭头。"""
    try:
        if hasattr(combo, "expand"):
            combo.expand()
            return True
    except Exception:
        pass
    try:
        rect = combo.rectangle()
        x = int(rect.right - max(8, (rect.right - rect.left) // 8))
        y = int(rect.mid_point().y)
        click_at(x, y)
        time.sleep(0.25)
        return True
    except Exception as e:
        print(f"[登录名选择] 展开下拉失败: {e}")
        return False


def _collect_combo_listitems_uia(combo):
    """UIA fallback：只在 ComboBox 自身子树里收集 ListItem，绝不扫整桌面。

    去重 key 优先用 element_info.runtime_id（UIA 真正的稳定身份），不可用时退到
    (文本, rect) 组合。返回 [(item, text), ...]。
    """
    seen = []

    def _walk(node, depth=0):
        if depth > 6:
            return
        try:
            children = node.children()
        except Exception:
            children = []
        for child in children:
            try:
                ei = child.element_info
                ctype = str(getattr(ei, "control_type", "") or "")
                name = getattr(ei, "name", "") or ""
                if "ListItem" in ctype and child.is_visible():
                    text = (name or child.window_text() or "").strip()
                    seen.append((child, text))
                _walk(child, depth + 1)
            except Exception:
                continue

    try:
        _walk(combo)
    except Exception:
        pass

    unique = []
    seen_keys = set()
    for item, text in seen:
        key = None
        try:
            rid = item.element_info.runtime_id
            if rid:
                key = ("rid", tuple(rid))
        except Exception:
            key = None
        if key is None:
            try:
                handle = item.element_info.handle
                if isinstance(handle, int) and handle:
                    key = ("hwnd", handle)
            except Exception:
                key = None
        if key is None:
            try:
                r = item.rectangle()
                key = ("rect", text, r.left, r.top, r.right, r.bottom)
            except Exception:
                key = ("text", text, len(unique))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique.append((item, text))
    return unique


def _invoke_combo_item(item):
    """选中一个 ListItem：先 UIA select/invoke，再 fallback 到坐标点击。"""
    for method in ("select", "invoke", "click_input"):
        try:
            fn = getattr(item, method, None)
            if callable(fn):
                fn()
                return True
        except Exception:
            continue
    try:
        rect = item.rectangle()
        click_at(rect.mid_point().x, rect.mid_point().y)
        return True
    except Exception as e:
        print(f"[登录名选择] 坐标点击候选项失败: {e}")
        return False


def _read_combo_value(combo):
    """读回 ComboBox 当前显示文本（验证用）。

    招行 U-BANK 在此 ComboBox 上 UIA ValuePattern.CurrentValue / LegacyIAccessible Value
    可读，所以优先走这两条。退而求其次用 Win32 WM_GETTEXT 直接读 HWND 文本，再退到
    子 Edit 的 legacy Value，最后才是 window_text / name 兜底。
    """
    try:
        iface_value = getattr(combo, "iface_value", None)
        if iface_value is not None:
            val = getattr(iface_value, "CurrentValue", "")
            if val:
                return str(val).strip()
    except Exception:
        pass
    try:
        legacy = getattr(combo, "legacy_properties", None)
        if callable(legacy):
            props = legacy()
            if isinstance(props, dict):
                val = props.get("Value", "") or ""
                if val:
                    return str(val).strip()
    except Exception:
        pass
    try:
        hwnd = _combo_hwnd(combo)
        if hwnd:
            ln = _user32.GetWindowTextLengthW(hwnd)
            if ln and ln > 0:
                buf = ctypes.create_unicode_buffer(ln + 2)
                _user32.GetWindowTextW(hwnd, buf, ln + 1)
                if buf.value:
                    return buf.value.strip()
    except Exception:
        pass
    try:
        for child in combo.descendants(control_type="Edit"):
            try:
                legacy = getattr(child, "legacy_properties", None)
                if callable(legacy):
                    props = legacy()
                    if isinstance(props, dict):
                        val = props.get("Value", "") or ""
                        if val:
                            return str(val).strip()
            except Exception:
                continue
    except Exception:
        pass
    try:
        text = (combo.window_text() or "").strip()
        if text:
            return text
    except Exception:
        pass
    try:
        name = getattr(combo.element_info, "name", "") or ""
        if name:
            return name.strip()
    except Exception:
        pass
    return ""


# ===== 付款方账号下拉选择（单笔转账经办页面，进入填收款方信息之前调用） =====
# 与登录窗口「登录名」下拉是两条完全独立的流程：
#   - 登录名：CMB_LOGIN_ACCOUNT_NAME / INDEX；在「联机登录」窗口的 ComboBox 上选。
#   - 付款方账号：CMB_PAYER_ACCOUNT_TEXT / SUFFIX / INDEX；在主窗口「单笔转账经办」
#     页面的付款方账号下拉上选。两者互不影响。
#
# 输入优先级：TEXT > SUFFIX > INDEX。三者都未设置 → 直接 return True，不操作下拉，
# 默认行为完全不变。
#
# fail-closed 触发：
#   - 没找到付款方账号下拉控件，或下拉候选为空；
#   - TEXT 未在候选里精确相等且唯一；
#   - SUFFIX 不是纯数字、候选中按尾号未唯一命中（多于 1 条同尾号 / 0 条命中均拒绝）；
#   - INDEX 非正整数 / 越界；
#   - 选择后回读为空，或回读 != 选定候选文本。
# 选择失败必须让上层抛错终止，绝不能继续填收款方或点击经办/提交。
#
# 日志脱敏：候选 / 命中只打印数字尾号，不打印完整账号、户名、密码、证书密码。

_PAYER_LABEL_KEYWORDS = ("付方账号", "付款方账号", "默认付方账户", "默认付款方账户")

# 候选文本里账号段与说明段的分隔符。U-BANK 付款方账号下拉常见格式：
#   "1109 6079 5610 001, 北京示例公司"
#   "1109 6079 5610 001，北京示例公司2026"
# 公司名/年份里的数字会污染整串数字提取，所以先按这些分隔符把账号段切出来。
_PAYER_ACCOUNT_SEGMENT_SEPS = (",", "，")


def _extract_account_segment(text):
    """切出账号段：按 `,` / `，` 取第一段；没有分隔符时整串都是账号段。"""
    s = str(text or "")
    for sep in _PAYER_ACCOUNT_SEGMENT_SEPS:
        if sep in s:
            return s.split(sep, 1)[0]
    return s


def _payer_account_tail_digits(text, n):
    """从候选文本里抽出末尾 N 位数字，结果用于 SUFFIX 严格相等匹配。

    规则（避免公司名/年份数字污染）：
    1. 优先在「账号段」(`,` / `，` 之前) 抽数字；位数 >= n 则取末 N 位返回。
    2. 没有分隔符时，整串就是账号段；位数 >= n 则取末 N 位返回。
    3. 有分隔符但账号段数字不足 n 位：明确 fail-closed 返回 ""，
       绝不退到公司名一侧的数字凑数。
    """
    if n is None or n <= 0:
        return ""
    segment = _extract_account_segment(text)
    digits = "".join(ch for ch in segment if ch.isdigit())
    if len(digits) >= n:
        return digits[-n:]
    return ""


def _payer_account_mask(text):
    """日志脱敏：基于账号段（同 `_extract_account_segment`）抽末 4 位数字。

    公司名里的数字不参与脱敏字符串，避免日志看到的「尾号」和实际选中账号不一致。
    完全没数字则返回 `***`。
    """
    segment = _extract_account_segment(text)
    digits = "".join(ch for ch in segment if ch.isdigit())
    if not digits:
        return "***"
    tail = digits[-4:] if len(digits) >= 4 else digits
    return f"***{tail}"


def _safe_rect(node):
    try:
        r = node.rectangle()
        return (r.left, r.top, r.right, r.bottom)
    except Exception:
        return None


def _rect_distance(a, b):
    if not a or not b:
        return float("inf")
    ax = (a[0] + a[2]) / 2
    ay = (a[1] + a[3]) / 2
    bx = (b[0] + b[2]) / 2
    by = (b[1] + b[3]) / 2
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _payer_combo_items_text(combo):
    """返回 ComboBox 候选文本列表（**未展开**就能读到的那部分）。

    适用于 Win32 ComboBox（CB_GETLBTEXT）和某些 UIA 控件（ListItem 直接挂在 combo
    子树里）。真实 U-BANK 单笔转账经办页的付款方账号是 CEF 伪 ComboBox，未展开时
    返回空列表是正常的 —— 调用方据此切到 `_payer_collect_expanded_candidates`
    走「点击展开 + 几何区收集」路径。
    """
    hwnd = _combo_hwnd(combo)
    if hwnd:
        items = _combo_items_win32(hwnd)
        if items is not None:
            return list(items), "win32"
    items = []
    try:
        for child in combo.descendants():
            try:
                if not child.is_visible():
                    continue
                ei = child.element_info
                ctype = str(getattr(ei, "control_type", "") or "")
                if "ListItem" in ctype:
                    name = (getattr(ei, "name", "") or child.window_text() or "").strip()
                    if name:
                        items.append(name)
            except Exception:
                continue
    except Exception:
        return None, "uia-failed"
    return items, "uia"


# ===== 付款方账号 CEF/UIA 伪 ComboBox 展开收集路径 =====
# 真实 U-BANK 单笔转账经办页的付款方账号是 CEF 渲染的伪 ComboBox：没有 Win32 HWND，
# 未展开时 combo 子树里也读不到 ListItem。需要点击右侧箭头展开下拉，然后在
# 「主窗口」内 + 「combo 矩形附近」的几何区里收集候选 —— 绝不能扫整桌面，
# 也不能误吸到任务栏、浏览器、业务模式下拉等其它弹层的 ListItem。
#
# 7 口 / 任何特定 UKey 都不写进代码：本路径与 UKey 数量无关，单/多账号场景统一处理。

# 几何区参数（相对 combo.rectangle()）：
#   横向 [combo.left - left_pad, combo.right + right_pad]
#   纵向 [combo.top, combo.bottom + bottom_pad]
# 这是付款方账号 CEF 下拉常见的渲染区域；过窄会漏读账号尾长的候选行，过宽会把
# 表单上的其它账号字段（收方账号一行）也吸进来。
_PAYER_DROPDOWN_LEFT_PAD = 80
_PAYER_DROPDOWN_RIGHT_PAD = 450
_PAYER_DROPDOWN_BOTTOM_PAD = 180

_PAYER_CANDIDATE_CONTROL_TYPES = ("ListItem", "DataItem", "Text")


def _account_segment_digits(text):
    """账号段（`,` / `，` 之前）的全部数字。

    用于候选去重 key 与回读校验。同一个真实账号在 UIA 树上可能同时表现为
    ListItem 和 Text，两条记录的账号段数字串完全一致，会被去重折叠成一条；
    两个不同账号即使尾号相同（罕见但允许），账号段数字串也不会相同，仍会保留为
    两条候选，从而让 SUFFIX 模式正确地按「多条同尾号」fail-closed。
    """
    return "".join(ch for ch in _extract_account_segment(text) if ch.isdigit())


def _rect_in_payer_dropdown_zone(rect, combo_rect):
    """候选矩形中心是否落在 combo 附近的下拉几何区。"""
    if not rect or not combo_rect:
        return False
    cl, ct, cr, cb = combo_rect
    zone_left = cl - _PAYER_DROPDOWN_LEFT_PAD
    zone_right = cr + _PAYER_DROPDOWN_RIGHT_PAD
    zone_top = ct
    zone_bottom = cb + _PAYER_DROPDOWN_BOTTOM_PAD
    rl, rt, rr, rb = rect
    cx = (rl + rr) / 2
    cy = (rt + rb) / 2
    return (zone_left <= cx <= zone_right) and (zone_top <= cy <= zone_bottom)


def _expand_payer_combo_by_click(combo):
    """点击 combo 右侧箭头区域展开下拉。CEF 伪 ComboBox 没有 UIA expand pattern，
    只能靠模拟点击；点完留一点时间给页面渲染下拉。"""
    try:
        rect = combo.rectangle()
        x = int(rect.right - max(10, (rect.right - rect.left) // 8))
        y = int(rect.mid_point().y)
        click_at(x, y)
        time.sleep(0.6)
        return True
    except Exception as e:
        print(f"[付款方账号] 展开伪 ComboBox 失败: {e}")
        return False


def _combo_descendant_runtime_ids(combo):
    """收集 combo 自身子树里所有节点的 runtime_id 集合，用于在「展开后扫描」时
    剔除 combo 自己的子节点（包括当前显示值），避免把 combo 的显示值误当成
    下拉候选。"""
    ids = set()
    try:
        rid = combo.element_info.runtime_id
        if rid:
            ids.add(tuple(rid))
    except Exception:
        pass
    try:
        for d in combo.descendants():
            try:
                rid = d.element_info.runtime_id
                if rid:
                    ids.add(tuple(rid))
            except Exception:
                continue
    except Exception:
        pass
    return ids


def _payer_collect_expanded_candidates(main_win, combo):
    """点击展开后，在「主窗口子树」+「combo 几何区」里收集付款方账号候选。

    候选必须同时满足：
      1. 可见，且 control_type 含 ListItem / DataItem / Text；
      2. 有非空 Name（或 window_text），且 _payer_account_tail_digits 抽得到末 3 位；
      3. rect 中心在 combo 几何区内；
      4. 不在 combo 自身子树里（剔除 combo 的当前显示值）。

    去重 key：账号段完整数字串。两个不同账号即使尾号相同也保留为两条 → 由 SUFFIX
    匹配阶段按多条同尾号 fail-closed。

    返回 [{"text", "digits", "node", "rect"}, ...]。日志全部脱敏。
    """
    combo_rect = _safe_rect(combo)
    if not combo_rect:
        return []
    combo_subtree_ids = _combo_descendant_runtime_ids(combo)
    found = []
    examined = 0

    def _walk(node, depth=0):
        nonlocal examined
        if depth > 22:
            return
        try:
            if not node.is_visible():
                return
        except Exception:
            return
        try:
            ei = node.element_info
            try:
                rid = ei.runtime_id
                if rid and tuple(rid) in combo_subtree_ids:
                    return
            except Exception:
                pass
            ctype = str(getattr(ei, "control_type", "") or "")
            name = (getattr(ei, "name", "") or "").strip()
            if not name:
                try:
                    name = (node.window_text() or "").strip()
                except Exception:
                    name = ""
            if name and any(t in ctype for t in _PAYER_CANDIDATE_CONTROL_TYPES):
                rect = _safe_rect(node)
                if _rect_in_payer_dropdown_zone(rect, combo_rect):
                    if _payer_account_tail_digits(name, 3):
                        examined += 1
                        found.append({
                            "text": name,
                            "digits": _account_segment_digits(name),
                            "node": node,
                            "rect": rect,
                            "ctype": ctype,
                        })
        except Exception:
            pass
        try:
            for child in node.children():
                _walk(child, depth + 1)
        except Exception:
            pass

    try:
        _walk(main_win)
    except Exception as e:
        print(f"[付款方账号] 展开后收集候选失败: {e}")
        return []

    # 去重：相同账号段数字串视为同一候选；同时存在 ListItem / DataItem / Text 时优先保留
    # 可点击类型（ListItem / DataItem），其次保留 Text。
    by_digits = {}
    order = []
    for item in found:
        digits = item["digits"]
        if not digits:
            continue
        if digits not in by_digits:
            by_digits[digits] = item
            order.append(digits)
            continue
        prev = by_digits[digits]
        prev_clickable = ("ListItem" in prev["ctype"]) or ("DataItem" in prev["ctype"])
        cur_clickable = ("ListItem" in item["ctype"]) or ("DataItem" in item["ctype"])
        if cur_clickable and not prev_clickable:
            by_digits[digits] = item
    deduped = [by_digits[d] for d in order]
    print(
        f"[付款方账号] 展开后几何区扫描：原始命中 {examined} 个节点，账号段数字串去重后 {len(deduped)} 条候选"
    )
    return deduped


def _click_payer_candidate_node(item):
    """选中一个展开后的候选节点：优先 UIA invoke/select，退到坐标点击。"""
    node = item["node"]
    for method in ("invoke", "select", "click_input"):
        try:
            fn = getattr(node, method, None)
            if callable(fn):
                fn()
                return True
        except Exception:
            continue
    try:
        r = node.rectangle()
        click_at(r.mid_point().x, r.mid_point().y)
        return True
    except Exception as e:
        print(f"[付款方账号] 候选项坐标点击失败: {e}")
        return False


def _payer_readback_matches(current, chosen_text):
    """选择后回读的严格比较器。

    通过条件（任一）：
      1. 整段文本严格相等（理想情况）；
      2. 账号段完整数字串严格相等（CEF 部分回读情况下用，但仍是严格等值，
         不是 substring / startswith / contains）。
    `current` 为空一律 fail-closed（在调用方处理，本函数仅判等）。
    """
    if not current or not chosen_text:
        return False
    if current.strip() == chosen_text.strip():
        return True
    cur_digits = _account_segment_digits(current)
    cho_digits = _account_segment_digits(chosen_text)
    if cur_digits and cho_digits and cur_digits == cho_digits:
        return True
    return False


# ===== 付款方账号 CEF 显示区几何回读 =====
# 真实 U-BANK 付款方账号是 CEF 渲染的伪 ComboBox：选中变更后 ValuePattern /
# legacy Value / WM_GETTEXT / 子 Edit Value 全部读不到当前显示文本，但页面上
# 视觉确实已经切换到目标账号。这一类情况下，从 combo 显示矩形附近的可见
# Text / DataItem / Edit / ComboBox 节点里读出当前账号文本，仍走严格相等比较。
# 仅在「显式配置了 CMB_PAYER_ACCOUNT_*」之后才会执行；默认 fast-path 不调用本函数。

_PAYER_DISPLAY_PAD = 6  # 显示矩形 ±6 px 容差，吸收 CEF 文本节点轻微外溢
_PAYER_DISPLAY_CONTROL_TYPES = ("Text", "DataItem", "Edit", "ComboBox")


def _rect_intersect_area(a, b):
    """两个矩形的相交面积（不相交返回 0）。"""
    if not a or not b:
        return 0
    al, at, ar, ab = a
    bl, bt, br, bb = b
    iw = min(ar, br) - max(al, bl)
    ih = min(ab, bb) - max(at, bt)
    if iw <= 0 or ih <= 0:
        return 0
    return iw * ih


def _rect_in_payer_display_zone(rect, combo_rect):
    """rect 中心是否落在「combo 显示矩形 ± padding」的紧区。

    比 _rect_in_payer_dropdown_zone（展开下拉用的大区）更紧，仅覆盖 combo 自身
    的显示矩形，避免把下拉的弹出行 / 下方 / 右侧的其它字段误当成「当前选中值」。
    """
    if not rect or not combo_rect:
        return False
    cl, ct, cr, cb = combo_rect
    pad = _PAYER_DISPLAY_PAD
    zone_left = cl - pad
    zone_top = ct - pad
    zone_right = cr + pad
    zone_bottom = cb + pad
    if _rect_intersect_area(rect, (zone_left, zone_top, zone_right, zone_bottom)) <= 0:
        return False
    rl, rt, rr, rb = rect
    cx = (rl + rr) / 2
    cy = (rt + rb) / 2
    return zone_left <= cx <= zone_right and zone_top <= cy <= zone_bottom


def _read_payer_combo_display_text(main_win, combo):
    """付款方账号专用：从 combo 显示矩形附近读「当前显示文本」。

    适用场景：CEF/UIA 伪 ComboBox 选中变更后 `_read_combo_value(combo)` 读不到
    当前值，但页面视觉确实已切换。仅在已显式触发付款方账号选择的代码路径里调用，
    单账号默认场景永远不会进入。

    扫描范围：**只**在 main_win 子树里递归，**只**收集 `_PAYER_DISPLAY_CONTROL_TYPES`
    里的可见控件，**只**接受 rect 与 combo 显示矩形紧贴（中心在 combo 矩形 ±6 px 内
    且与矩形有非零相交）的节点。绝不扫桌面、不扫其它窗口、不读收款方账号 / 金额
    所在区域、不读下拉弹层的候选行。

    日志全部脱敏（`***NNNN`）。
    决策：
    - 把所有命中节点按账号段数字串分组；
    - 只有 1 组 → 返回该组里相交面积最大的代表文本；
    - >= 2 组不同账号段 → 无法判断，返回空串触发 fail-closed；
    - 0 组 → 返回空串。
    """
    combo_rect = _safe_rect(combo)
    if not combo_rect:
        return ""
    found = []  # list[(intersect_area, name, digits)]

    def _walk(node, depth=0):
        if depth > 22:
            return
        try:
            if not node.is_visible():
                return
        except Exception:
            return
        try:
            ei = node.element_info
            ctype = str(getattr(ei, "control_type", "") or "")
            name = (getattr(ei, "name", "") or "").strip()
            if not name:
                try:
                    name = (node.window_text() or "").strip()
                except Exception:
                    name = ""
            if name and any(t in ctype for t in _PAYER_DISPLAY_CONTROL_TYPES):
                # 跳过纯标签节点，如 "付款方账号" / "默认付方账户"
                if not any(k in name for k in _PAYER_LABEL_KEYWORDS):
                    digits = _account_segment_digits(name)
                    if digits:
                        rect = _safe_rect(node)
                        if _rect_in_payer_display_zone(rect, combo_rect):
                            iarea = _rect_intersect_area(rect, combo_rect)
                            found.append((iarea, name, digits))
        except Exception:
            pass
        try:
            for child in node.children():
                _walk(child, depth + 1)
        except Exception:
            pass

    try:
        _walk(main_win)
    except Exception as e:
        print(f"[付款方账号] 显示区几何回读异常: {e}")
        return ""

    if not found:
        return ""

    by_digits = {}
    for iarea, name, digits in found:
        prev = by_digits.get(digits)
        if prev is None or iarea > prev[0]:
            by_digits[digits] = (iarea, name)

    if len(by_digits) > 1:
        print(
            f"[付款方账号] 显示区几何回读在 combo 矩形附近发现 {len(by_digits)} 条不同账号段，"
            "无法判断当前选中，fail-closed"
        )
        return ""

    iarea, name = next(iter(by_digits.values()))
    print(
        f"[付款方账号] 显示区几何回读命中：{_payer_account_mask(name)}"
        f"（与 combo 矩形相交面积 {iarea} px）"
    )
    return name


def _find_payer_account_combo(main_win):
    """在单笔转账经办页面定位「付款方账号」下拉。

    策略：
    1. 收集 main_win 内所有可见 ComboBox 与所有 Name 含付款方账号关键字的元素。
    2. 标签命中优先：找距离任一付款方账号标签最近的 ComboBox（同行 / 上方）。
    3. 退而求其次的启发式（仅在标签策略未命中时启用，且只有 1 个 ComboBox 候选含
       「长数字串」尾号特征时才返回，多于 1 个视为不明确 → 返回 None 触发 fail-closed）。
    """
    combos = []
    labels = []

    def _walk(node, depth=0):
        if depth > 18:
            return
        try:
            if not node.is_visible():
                return
        except Exception:
            return
        try:
            ei = node.element_info
            ctype = str(getattr(ei, "control_type", "") or "")
            cls = str(getattr(ei, "class_name", "") or "")
            name = (getattr(ei, "name", "") or "").strip()
            if "ComboBox" in ctype or "ComboBox" in cls:
                combos.append((node, _safe_rect(node)))
            if name and any(k in name for k in _PAYER_LABEL_KEYWORDS):
                labels.append((node, _safe_rect(node), name))
        except Exception:
            pass
        try:
            for child in node.children():
                _walk(child, depth + 1)
        except Exception:
            pass

    try:
        _walk(main_win)
    except Exception as e:
        print(f"[付款方账号] 枚举主窗口控件失败: {e}")
        return None

    if not combos:
        print("[付款方账号] 主窗口内未找到任何可见 ComboBox")
        return None

    if labels:
        best = None
        best_dist = None
        for label_node, label_rect, _label_name in labels:
            for combo_node, combo_rect in combos:
                d = _rect_distance(label_rect, combo_rect)
                if best_dist is None or d < best_dist:
                    best_dist = d
                    best = combo_node
        if best is not None:
            return best

    # 没有标签命中：按候选项「长数字串尾号」启发，只有 1 个 ComboBox 含账号特征才返回。
    digit_like_candidates = []
    for combo_node, _rect in combos:
        items, _src = _payer_combo_items_text(combo_node)
        if not items:
            continue
        if any(_payer_account_tail_digits(item, 3) for item in items):
            digit_like_candidates.append(combo_node)
    if len(digit_like_candidates) == 1:
        return digit_like_candidates[0]
    if len(digit_like_candidates) > 1:
        print(
            f"[付款方账号] 标签未命中且存在多个账号特征下拉（{len(digit_like_candidates)} 个），拒绝盲选"
        )
    else:
        print("[付款方账号] 标签未命中且无 ComboBox 含账号特征，拒绝盲选")
    return None


def _select_payer_account_if_requested(main_win):
    """在「单笔转账经办」页面、填收款方信息之前选择付款方账号。

    优先级：CMB_PAYER_ACCOUNT_TEXT > CMB_PAYER_ACCOUNT_SUFFIX > CMB_PAYER_ACCOUNT_INDEX。
    三者都未设置 → return True，**不做任何 UI 动作**（默认行为完全不变；
    绝大多数单账号场景完全不受影响，不展开下拉、不读控件、不修改窗口状态）。

    设置后的读取路径：
      1) 先用 `_payer_combo_items_text(combo)` 走 Win32 CB_GETLBTEXT / UIA 子树
         快速路径 —— 对真正的 Win32 ComboBox 有效；
      2) 若读不到候选（真实 U-BANK 付款方账号是 CEF 伪 ComboBox，未展开就是空），
         点击右侧箭头**展开**，再用 `_payer_collect_expanded_candidates(main_win, combo)`
         在主窗口子树 + combo 几何区里收集 ListItem/DataItem/Text 候选，按账号段
         数字串去重 —— 同账号的 ListItem/Text 折叠为一条，两个不同账号即使同尾号
         也保留为两条以触发 fail-closed。

    任意失败 → return False，调用方按 fail-closed 终止：不填收款方、不点经办、不点提交。
    """
    target_text = (os.getenv("CMB_PAYER_ACCOUNT_TEXT") or "").strip()
    target_suffix = (os.getenv("CMB_PAYER_ACCOUNT_SUFFIX") or "").strip()
    target_index_raw = (os.getenv("CMB_PAYER_ACCOUNT_INDEX") or "").strip()

    if not target_text and not target_suffix and not target_index_raw:
        return True

    target_index = None
    if not target_text and not target_suffix and target_index_raw:
        try:
            target_index = int(target_index_raw)
        except ValueError:
            print(f"[付款方账号] CMB_PAYER_ACCOUNT_INDEX 不是有效整数: [{target_index_raw}]")
            return False
        if target_index < 1:
            print(f"[付款方账号] CMB_PAYER_ACCOUNT_INDEX 必须 >= 1: [{target_index_raw}]")
            return False

    if target_suffix and not target_text:
        if not target_suffix.isdigit():
            print(f"[付款方账号] CMB_PAYER_ACCOUNT_SUFFIX 必须是纯数字: [{target_suffix}]")
            return False

    combo = _find_payer_account_combo(main_win)
    if combo is None:
        print("[付款方账号] 未定位到付款方账号下拉，fail-closed")
        return False

    # 候选收集：先尝试不展开就能读到的路径；不行再展开走 CEF 几何区收集。
    candidates = []  # list[dict]: text/digits/node(None for win32)/ctype/source
    source = ""
    items_simple, simple_source = _payer_combo_items_text(combo)
    if items_simple:
        # 不展开就能读到（Win32 ComboBox 或 UIA 子树有 ListItem）。
        # 按账号段数字串去重，保持原始顺序。
        seen = set()
        for raw in items_simple:
            digits = _account_segment_digits(raw)
            if not digits or digits in seen:
                continue
            seen.add(digits)
            candidates.append({
                "text": raw,
                "digits": digits,
                "node": None,
                "ctype": "ComboBoxItem",
                "source": simple_source,
            })
        source = simple_source
    else:
        # CEF/UIA 伪 ComboBox：点击展开，再按几何区收集。
        print("[付款方账号] 未展开时读不到候选，切到 CEF/UIA 展开收集路径")
        if not _expand_payer_combo_by_click(combo):
            return False
        collected = _payer_collect_expanded_candidates(main_win, combo)
        for c in collected:
            c["source"] = "uia-expanded"
        candidates = collected
        source = "uia-expanded"

    if not candidates:
        print(f"[付款方账号] {source} 路径下拉候选为空，fail-closed")
        return False
    print(f"[付款方账号] 候选数: {len(candidates)}（来源: {source}，已脱敏）")
    for i, c in enumerate(candidates, 1):
        print(f"  [{i}] {_payer_account_mask(c['text'])}")

    # 选目标候选
    chosen = None
    chosen_idx_zero_based = None
    if target_text:
        text_hits = [i for i, c in enumerate(candidates) if c["text"] == target_text]
        if not text_hits:
            print("[付款方账号] CMB_PAYER_ACCOUNT_TEXT 未在候选中精确匹配（已脱敏，未打印目标）")
            return False
        if len(text_hits) > 1:
            print("[付款方账号] CMB_PAYER_ACCOUNT_TEXT 在候选中存在多条同名条目，拒绝处理")
            return False
        chosen_idx_zero_based = text_hits[0]
        chosen = candidates[chosen_idx_zero_based]
        print(f"[付款方账号] TEXT 命中：尾号 {_payer_account_mask(chosen['text'])}")
    elif target_suffix:
        n = len(target_suffix)
        suffix_hits = [
            i for i, c in enumerate(candidates)
            if _payer_account_tail_digits(c["text"], n) == target_suffix
        ]
        if not suffix_hits:
            print(f"[付款方账号] 未找到尾号 {target_suffix} 的候选")
            return False
        if len(suffix_hits) > 1:
            print(f"[付款方账号] 存在多条尾号 {target_suffix} 的候选，拒绝处理")
            return False
        chosen_idx_zero_based = suffix_hits[0]
        chosen = candidates[chosen_idx_zero_based]
        print(f"[付款方账号] SUFFIX 命中：尾号 {target_suffix}")
    else:
        if target_index > len(candidates):
            print(f"[付款方账号] CMB_PAYER_ACCOUNT_INDEX={target_index} 超过候选数量 {len(candidates)}")
            return False
        chosen_idx_zero_based = target_index - 1
        chosen = candidates[chosen_idx_zero_based]
        print(f"[付款方账号] INDEX 命中：第 {target_index} 项，尾号 {_payer_account_mask(chosen['text'])}")

    # 选中动作：有 HWND 走 Win32 CB_SETCURSEL；CEF/UIA 路径用候选节点 invoke/click。
    hwnd = _combo_hwnd(combo)
    if hwnd and chosen["node"] is None:
        if not _select_combo_item_win32(hwnd, chosen_idx_zero_based):
            print("[付款方账号] Win32 CB_SETCURSEL 失败，fail-closed")
            return False
    else:
        if chosen["node"] is None:
            print("[付款方账号] CEF/UIA 路径下候选没有可点击节点，fail-closed")
            return False
        if not _click_payer_candidate_node(chosen):
            print("[付款方账号] CEF/UIA 路径选中候选项失败，fail-closed")
            return False

    time.sleep(0.4)

    # 回读校验：先用 _read_combo_value（适合 Win32 ComboBox 与暴露 ValuePattern 的 UIA
    # 控件）；CEF 伪 ComboBox 上 _read_combo_value 常返回空/标签文本，此时切到
    # _read_payer_combo_display_text 在 combo 显示矩形附近做几何回读。两条路径都
    # 走 _payer_readback_matches 的严格相等（整段相等 OR 账号段数字串相等），不允许
    # substring / startswith / contains。
    current = _read_combo_value(combo)
    if current and _payer_readback_matches(current, chosen["text"]):
        readback_source = "value-pattern"
    else:
        if current:
            print(
                f"[付款方账号] _read_combo_value 回读 {_payer_account_mask(current)} 不匹配目标，"
                "切到显示区几何回读"
            )
        else:
            print("[付款方账号] _read_combo_value 回读为空，切到显示区几何回读")
        display = _read_payer_combo_display_text(main_win, combo)
        if not display:
            print("[付款方账号] 显示区几何回读未读到账号文本，fail-closed")
            return False
        if not _payer_readback_matches(display, chosen["text"]):
            print(
                f"[付款方账号] 显示区几何回读 {_payer_account_mask(display)} 与目标 "
                f"{_payer_account_mask(chosen['text'])} 不一致，fail-closed"
            )
            return False
        current = display
        readback_source = "display-geometry"

    if target_suffix and not target_text:
        print(f"已选择付款方账号尾号: {target_suffix}（回读源: {readback_source}）")
    else:
        print(
            f"已选择付款方账号（尾号脱敏）: {_payer_account_mask(chosen['text'])}"
            f"（回读源: {readback_source}）"
        )
    return True


def _fill_edit_fields(win, login_pwd, cert_pwd):
    """遍历登录窗口的ATL:Edit控件，依次输入登录密码和证书密码"""
    edit_count = 0
    for child in win.children():
        try:
            cls = child.element_info.class_name if hasattr(child.element_info, "class_name") else ""
            if "ATL" in cls and child.is_visible():
                for sub in child.children():
                    sub_cls = sub.element_info.class_name if hasattr(sub.element_info, "class_name") else ""
                    if "Edit" in sub_cls:
                        edit_count += 1
                        if edit_count == 1:
                            sub.set_focus()
                            sub.type_keys(login_pwd)
                            print("已输入登录密码")
                        elif edit_count == 2:
                            sub.set_focus()
                            sub.type_keys(cert_pwd)
                            print("已输入证书密码")
                            break
        except Exception:
            continue
    if edit_count == 0:
        print("未找到登录密码输入框")
    elif edit_count < 2:
        print("未找到证书密码输入框")


def _window_rect_text(win):
    try:
        rect = win.rectangle()
        return f"({rect.left},{rect.top},{rect.right},{rect.bottom})"
    except Exception as e:
        return f"<rect unavailable: {e}>"


def _log_main_window_diagnostics(desktop, possible_titles):
    """Log enough state to distinguish slow startup from U-BANK self-exit."""
    print("[主界面诊断] 开始记录当前窗口与 Firmbank 进程状态")

    try:
        windows = desktop.windows()
        print(f"[主界面诊断] UIA 顶层窗口数量: {len(windows)}")
        titled = []
        matched = []
        for w in windows:
            try:
                title = w.window_text()
                if not title or not title.strip():
                    continue
                info = getattr(w, "element_info", None)
                cls = getattr(info, "class_name", "") if info else ""
                ctype = getattr(info, "control_type", "") if info else ""
                visible = w.is_visible()
                row = f"title={title!r}, class={cls!r}, type={ctype!r}, visible={visible}, rect={_window_rect_text(w)}"
                titled.append(row)
                if any(pt in title for pt in possible_titles) or "联机登录" in title:
                    matched.append(row)
            except Exception as e:
                titled.append(f"<window inspect failed: {e}>")

        print("[主界面诊断] UIA 候选/相关窗口:")
        if matched:
            for row in matched[:20]:
                print(f"  - {row}")
        else:
            print("  - <none>")

        print("[主界面诊断] UIA 有标题窗口前20个:")
        if titled:
            for row in titled[:20]:
                print(f"  - {row}")
        else:
            print("  - <none>")
    except Exception as e:
        print(f"[主界面诊断] UIA 窗口枚举失败: {e}")

    try:
        rows = []

        def _enum_cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            if not title or not title.strip():
                return True
            cls = win32gui.GetClassName(hwnd)
            rect = win32gui.GetWindowRect(hwnd)
            rows.append(f"hwnd=0x{hwnd:08X}, title={title!r}, class={cls!r}, rect={rect}")
            return True

        win32gui.EnumWindows(_enum_cb, None)
        related = [r for r in rows if any(pt in r for pt in possible_titles) or "联机登录" in r or "Firmbank" in r]
        print("[主界面诊断] Win32 相关窗口:")
        if related:
            for row in related[:20]:
                print(f"  - {row}")
        else:
            print("  - <none>")
    except Exception as e:
        print(f"[主界面诊断] Win32 窗口枚举失败: {e}")

    try:
        proc = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Firmbank.exe", "/FO", "CSV"],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            timeout=5,
        )
        output = (proc.stdout or "").strip()
        print(f"[主界面诊断] tasklist Firmbank.exe rc={proc.returncode}")
        if output:
            for line in output.splitlines():
                print(f"  {line}")
        else:
            print("  <empty>")
    except Exception as e:
        print(f"[主界面诊断] Firmbank 进程查询失败: {e}")


def wait_for_main_window(desktop):
    """等待主界面窗口加载完成。

    fail-closed：标题必须包含 V12/U-BANK/招商银行/企业银行 之一，且排除登录窗口。
    旧实现命中失败时会退回选"任意可见窗口"，可能让后续的导航点击作用在与
    U-BANK 完全无关的应用上（资源管理器、浏览器），所以改为找不到就返回 None，
    让调用方按失败终止流程。
    """
    possible_titles = ["V12", "U-BANK", "招商银行", "企业银行"]
    main_win = None

    for _ in range(20):
        windows = desktop.windows()
        for w in windows:
            title = w.window_text()
            for pt in possible_titles:
                if pt in title and "联机登录" not in title:
                    main_win = w
                    break
            if main_win:
                break
        if main_win:
            break
        time.sleep(1)

    if main_win:
        print(f"已连接主界面窗口: {main_win.window_text()}")
    else:
        print(
            "[fail-closed] 未找到包含 V12/U-BANK/招商银行/企业银行 的非登录窗口，"
            "拒绝退回'任意可见窗口'兜底"
        )
        _log_main_window_diagnostics(desktop, possible_titles)
    return main_win


def click_control_by_name(main_win, target_name):
    """递归查找指定名称的控件并点击"""

    def _find_and_click(control):
        try:
            name = control.element_info.name if hasattr(control.element_info, "name") and control.element_info.name else ""
            if name and name.strip() == target_name:
                print(f"找到控件: {name}")
                rect = control.rectangle()
                click_at(rect.mid_point().x, rect.mid_point().y)
                print(f"已点击: {target_name}")
                return True
        except Exception:
            pass

        try:
            for child in control.children():
                result = _find_and_click(child)
                if result:
                    return True
        except Exception:
            pass
        return False

    print(f"正在查找: {target_name}...")
    clicked = _find_and_click(main_win)
    if not clicked:
        print(f"未找到控件: {target_name}")
    return clicked


def click_control_by_names(main_win, target_names):
    """按顺序尝试点击多个候选控件名称，命中一个即返回"""
    for name in target_names:
        if click_control_by_name(main_win, name):
            return True
    print(f"候选控件均未命中: {target_names}")
    return False


def _rect_close(a, b, tol=30):
    """判断两个矩形是否近似相同（用于匹配前台窗口）"""
    return (
        abs(a[0] - b[0]) <= tol and
        abs(a[1] - b[1]) <= tol and
        abs(a[2] - b[2]) <= tol and
        abs(a[3] - b[3]) <= tol
    )


def _uia_collect_confirm_candidates(control, out_list, host_rect):
    """递归收集名称含“确认/确定”的可点击控件候选"""
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
            if area > 20000 and not is_std_btn:
                for ch in control.children():
                    _uia_collect_confirm_candidates(ch, out_list, host_rect)
                return
            pri_name = 0 if "确认" in nm else 1
            pri_type = 0 if is_std_btn else (1 if ("Hyperlink" in ctype or "Custom" in ctype) else 2)
            mx, my = rect.mid_point().x, rect.mid_point().y
            hr_left, hr_top, hr_right, _ = host_rect
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


def _try_uia_confirm_click(desktop):
    """仅在前台窗口 UIA 树中查找并点击确认按钮"""
    fg_hwnd = win32gui.GetForegroundWindow()
    if not fg_hwnd:
        return False
    fg_rect = win32gui.GetWindowRect(fg_hwnd)
    candidates = []
    for w in desktop.windows():
        try:
            wr = w.rectangle()
            host_rect = (wr.left, wr.top, wr.right, wr.bottom)
            if not _rect_close(host_rect, fg_rect):
                continue
            _uia_collect_confirm_candidates(w, candidates, host_rect)
        except Exception:
            pass
    if not candidates:
        return False
    candidates.sort(key=lambda t: (t[0], t[1], t[2]))
    _, _, _, x, y, label = candidates[0]
    print(f"UIA 定位到确认控件: {label!r} -> ({x}, {y})")
    click_at(x, y)
    time.sleep(0.35)
    return True


def _click_confirm_button(desktop, step_desc):
    """点击单次确认弹窗（优先 UIA，次选几何点击）"""
    time.sleep(1.0)
    end_ts = time.time() + 12
    while time.time() < end_ts:
        if _try_uia_confirm_click(desktop):
            print(f"{step_desc} 已通过 UIA 点击确认")
            return
        hwnd = win32gui.GetForegroundWindow()
        if hwnd:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = right - left
            height = bottom - top
            if 320 <= width <= 1200 and 160 <= height <= 700:
                x = int(left + width * 0.56)
                y = int(top + height * 0.84)
                print(f"{step_desc} UIA 未命中，几何点击确认: ({x}, {y})")
                click_at(x, y)
                time.sleep(0.2)
                press_keys((0x0D, 0), (0x0D, 2))
                return
        time.sleep(0.25)
    print(f"{step_desc} 监听超时，发送 Enter + Tab 兜底")
    press_keys((0x0D, 0), (0x0D, 2))
    time.sleep(0.3)
    for _ in range(14):
        press_keys((0x09, 0), (0x09, 2))
        time.sleep(0.12)
    press_keys((0x0D, 0), (0x0D, 2))


def close_with_confirm(main_win):
    """关闭 U-BANK 并连续处理两次确认弹窗"""
    print("开始执行退出流程...")
    try:
        main_win.set_focus()
        time.sleep(0.3)
    except Exception:
        pass

    press_keys(
        (0x12, 0), (0x73, 0),  # Alt down, F4 down
        (0x73, 2), (0x12, 2),  # F4 up, Alt up
    )
    print("已发送 Alt+F4")
    time.sleep(1.5)

    desktop = Desktop(backend="uia")
    _click_confirm_button(desktop, "第1次")
    time.sleep(2)
    _click_confirm_button(desktop, "第2次")
    print("退出流程完成")
