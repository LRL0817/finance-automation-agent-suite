# -*- coding: utf-8 -*-
"""Thin entrypoint for the U-BANK transfer-order automation."""
import os
import subprocess
import sys

import zhidan_paths  # Ensures ubank_common is importable.
from pywinauto import Desktop
from ubank_common import (
    open_ubank,
    login_ubank,
    wait_for_main_window,
    click_control_by_name,
)

import zhidan_crash as crash
from zhidan_crash import (
    UBankCrashDetected,
    _CrashWatcher,
    _check_ubank_crash,
    _clear_stale_ubank_crash_dialogs,
    _close_with_confirm,
    _dismiss_ubank_script_error_dialogs,
    _sleep_and_check,
)
from zhidan_form import _click_submit_button, fill_transfer_form
from zhidan_usb_hub import (
    _load_bank_form,
    _switch_usb_hub_for_form,
    _turn_off_usb_hub_ports,
    report_bank_form_validation,
)
from zhidan_utils import _log_display_profile, _screenshot

SUBMIT_CLICK_EXIT_CODE = 9
FORM_VALIDATION_EXIT_CODE = 10
DEMO_MODE_EXIT_CODE = 11
NAVIGATION_EXIT_CODE = 12
POST_SUBMIT_WARNING_EXIT_CODE = 13

# 默认开启测试模式：只点第一次「经办」并截图，跳过第二次「经办」（即不真实提交转账）。
# 仅当显式设置 ZHIDAN_TEST_MODE=0 时才会进入生产分支并点击第二次「经办」。
TEST_MODE_SKIP_SECOND_SUBMIT = os.getenv("ZHIDAN_TEST_MODE", "1") != "0"


def _run_generated_artifact_cleanup():
    if os.getenv("ZHIDAN_AUTO_CLEANUP_ARTIFACTS", "1").strip().lower() in {"0", "false", "no", "off"}:
        return
    cleanup = os.path.join(zhidan_paths.FINANCE_ROOT, "cleanup_generated_artifacts.ps1")
    if not os.path.exists(cleanup):
        return
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        cleanup,
        "-Apply",
        "-KeepBatchDays",
        os.getenv("ZHIDAN_CLEANUP_KEEP_BATCH_DAYS", "30"),
        "-KeepRecentBatches",
        os.getenv("ZHIDAN_CLEANUP_KEEP_RECENT_BATCHES", "100"),
        "-PurgeArchiveDays",
        os.getenv("ZHIDAN_CLEANUP_PURGE_ARCHIVE_DAYS", "90"),
    ]
    try:
        completed = subprocess.run(
            cmd,
            cwd=zhidan_paths.FINANCE_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
        )
        print(f"[清理] 招行运行产物清理退出码: {completed.returncode}")
    except Exception as exc:
        print(f"[清理] 招行运行产物清理失败: {exc}")


def _close_ubank_then_usb_hub(main_win):
    """先关闭 U-BANK，再关闭所有 USB Hub 端口。"""
    try:
        _close_with_confirm(main_win)
    finally:
        _turn_off_usb_hub_ports()


def _close_post_submit_warning_if_any(context, main_win=None):
    """提交后如果出现阻断式温馨提示，只关闭弹窗并终止，不点确认继续。"""
    desktop = Desktop(backend="uia")
    roots = []
    if main_win is not None:
        roots.append(main_win)
    roots.extend(desktop.windows())
    seen_roots = set()
    risk_tokens = (
        "确认继续经办吗",
        "本次经办存在以下风险",
        "账号可用余额不足",
        "可用余额不足",
    )
    for win in roots:
        try:
            try:
                rid = win.handle
            except Exception:
                rid = id(win)
            if rid in seen_roots:
                continue
            seen_roots.add(rid)

            controls = [win]
            try:
                controls.extend(win.descendants())
            except Exception:
                pass

            buttons = []
            texts = []
            for child in controls:
                try:
                    name = (
                        child.window_text()
                        or child.element_info.name
                        or ""
                    ).strip()
                    if name:
                        texts.append(name)
                    if str(child.element_info.control_type) == "Button":
                        buttons.append((name, child))
                except Exception:
                    continue

            joined = "\n".join(texts)
            if not any(token in joined for token in risk_tokens):
                continue

            button_names = {name for name, _ in buttons}
            print(f"[提交后提示] {context} 检测到 U-BANK 温馨提示弹窗，已按风险弹窗处理：关闭弹窗，不点击确认继续")
            print(f"[提交后提示] 弹窗按钮: {sorted(button_names)}")
            _screenshot("提交后温馨提示_已关闭前")
            for target in ("取消", "关闭"):
                for name, button in buttons:
                    if name == target:
                        button.invoke()
                        print(f"[提交后提示] 已点击弹窗按钮: {target}")
                        return True
            try:
                from pywinauto.keyboard import send_keys

                send_keys("{ESC}")
                print("[提交后提示] 未找到取消/关闭按钮，已发送 ESC")
            except Exception as e:
                print(f"[提交后提示] ESC 关闭弹窗失败: {e}")
            return True
        except Exception as e:
            print(f"[提交后提示] 检查温馨提示弹窗异常: {e}")
    return False


def _main_impl():
    """主流程：打开 -> 登录 -> 点击转账支付 -> 点击单笔转账经办 -> 填写表单 -> 到达制单页

    任何中途失败都以非零退出码结束，让 monitor.run_zhidan 把它当失败处理、
    不要标记 seen，下一轮自动重试。
    """

    # 0. 模式提示（默认测试模式：跳过第二次「经办」）
    if TEST_MODE_SKIP_SECOND_SUBMIT:
        print("[模式] ZHIDAN_TEST_MODE=1（默认）：将只点击第一次「经办」并截图，跳过第二次「经办」")
    else:
        print("[模式] ZHIDAN_TEST_MODE=0：生产模式，将继续点击第二次「经办」")

    # 1. 读取表单并切换 U 盾
    form, form_loaded = _load_bank_form()
    _log_display_profile()

    # 1b. 登录名下拉选择是「同一台 U-BANK 挂多个登录名」的双账号特殊场景能力，
    # 不是招行默认必配项。单账号公司不要配置任何登录名字段。
    #
    # 启用边界（任一即可）：
    #   - 当前进程显式设置了 CMB_LOGIN_ACCOUNT_NAME / CMB_LOGIN_ACCOUNT_INDEX；
    #   - bank_form 同时包含登录名值 **和** 强制开关
    #     CMB_REQUIRE_LOGIN_ACCOUNT_SELECTION=true（或别名 招行强制选择登录名=true）。
    #
    # bank_form 即使残留 `招行登录名 / CMB_LOGIN_ACCOUNT_NAME / CMB登录名` 字段，
    # 没有强制开关一律忽略并打印提示，沿用 U-BANK 默认登录名；避免普通单账号
    # 公司因为下拉候选读取失败而中断。
    #
    # 显式 env 优先：当前进程已设 CMB_LOGIN_ACCOUNT_NAME / INDEX 时，bank_form 完全
    # 不参与注入；ubank_common 仍按既有严格 fail-closed 选择目标登录名。
    _CMB_LOGIN_NAME_KEYS = ("招行登录名", "CMB_LOGIN_ACCOUNT_NAME", "CMB登录名")
    _CMB_LOGIN_FORCE_KEYS = ("CMB_REQUIRE_LOGIN_ACCOUNT_SELECTION", "招行强制选择登录名")
    _login_env_explicit = bool(
        os.environ.get("CMB_LOGIN_ACCOUNT_NAME") or os.environ.get("CMB_LOGIN_ACCOUNT_INDEX")
    )
    if _login_env_explicit:
        print("[登录名选择] 当前进程已显式设置 CMB_LOGIN_ACCOUNT_NAME/INDEX，跳过 bank_form 注入")
    elif form_loaded:
        def _truthy_form_flag(raw):
            if isinstance(raw, bool):
                return raw
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                return raw != 0
            if isinstance(raw, str):
                return raw.strip().lower() in {"1", "true", "yes", "on", "是", "真"}
            return False

        _force_enabled = any(
            _truthy_form_flag(form.get(_k)) for _k in _CMB_LOGIN_FORCE_KEYS
        )
        _form_login_value = ""
        _form_login_key = ""
        for _candidate_key in _CMB_LOGIN_NAME_KEYS:
            _candidate_val = form.get(_candidate_key)
            if isinstance(_candidate_val, str) and _candidate_val.strip():
                _form_login_value = _candidate_val.strip()
                _form_login_key = _candidate_key
                break
        if _force_enabled and _form_login_value:
            os.environ["CMB_LOGIN_ACCOUNT_NAME"] = _form_login_value
            print(
                f"[登录名选择] 已从 bank_form[{_form_login_key}] 注入 CMB_LOGIN_ACCOUNT_NAME"
                "（已显式开启强制选择登录名开关）"
            )
        elif _form_login_value:
            print(
                "[登录名选择] bank_form 含登录名字段，但未设置强制选择开关，已忽略；"
                "沿用 U-BANK 默认登录名"
            )
        elif _force_enabled:
            print(
                "[登录名选择] bank_form 设置了强制选择登录名开关但无登录名值，已忽略；"
                "沿用 U-BANK 默认登录名"
            )

    # 1c. 付款方账号下拉（单笔转账经办页面的「付款方账号」下拉，和登录名是两条独立流程）。
    #
    # 优先级：当前进程已显式设置三者之一时，bank_form 完全不注入。
    # 进程未显式设置时，按 SUFFIX > TEXT 顺序从 bank_form 别名注入 ——
    # SUFFIX 是生产主路径（M3→bank_form→suffix），TEXT 仅在调试 / 兜底场景使用。
    # 一旦命中 SUFFIX 就不再尝试 TEXT，避免完整账号文本被同步注入。
    _PAYER_ENV_KEYS = ("CMB_PAYER_ACCOUNT_TEXT", "CMB_PAYER_ACCOUNT_SUFFIX", "CMB_PAYER_ACCOUNT_INDEX")
    _payer_explicit = any(os.environ.get(_k) for _k in _PAYER_ENV_KEYS)
    if form_loaded and not _payer_explicit:
        _suffix_injected = False
        for _candidate_key in ("招行付款方账号尾号", "CMB_PAYER_ACCOUNT_SUFFIX", "CMB付款方账号尾号"):
            _candidate_val = form.get(_candidate_key)
            if isinstance(_candidate_val, (str, int)):
                _candidate_str = str(_candidate_val).strip()
                if _candidate_str:
                    os.environ["CMB_PAYER_ACCOUNT_SUFFIX"] = _candidate_str
                    print(f"[付款方账号] 已从 bank_form[{_candidate_key}] 注入 CMB_PAYER_ACCOUNT_SUFFIX: {_candidate_str}")
                    _suffix_injected = True
                    break
        if not _suffix_injected:
            for _candidate_key in ("招行付款方账号", "CMB_PAYER_ACCOUNT_TEXT", "CMB付款方账号"):
                _candidate_val = form.get(_candidate_key)
                if isinstance(_candidate_val, str) and _candidate_val.strip():
                    os.environ["CMB_PAYER_ACCOUNT_TEXT"] = _candidate_val.strip()
                    print(f"[付款方账号] 已从 bank_form[{_candidate_key}] 注入 CMB_PAYER_ACCOUNT_TEXT（值已脱敏）")
                    break
    elif _payer_explicit:
        print("[付款方账号] 当前进程已显式设置 CMB_PAYER_ACCOUNT_*，跳过 bank_form 注入")

    # bank_form 字段级校验：缺字段直接终止，不进入任何 UI 自动化。
    # form_loaded=False（示例模式）时跳过结构校验，由后续 demo 分支兜底拦截「经办」。
    if form_loaded:
        if not report_bank_form_validation(form):
            sys.exit(FORM_VALIDATION_EXIT_CODE)

    if not _switch_usb_hub_for_form(form, form_loaded):
        print("USB Hub 切换失败或付款单位未配置，终止执行")
        sys.exit(2)

    # 2. 打开应用
    _check_ubank_crash("启动前")
    if open_ubank() is False:
        _close_ubank_then_usb_hub(None)
        sys.exit(2)
    _sleep_and_check(1, "启动后")
    _dismiss_ubank_script_error_dialogs()

    # 3. 登录
    success = login_ubank()
    _check_ubank_crash("登录后")
    _dismiss_ubank_script_error_dialogs()
    if not success:
        print("登录失败，终止执行")
        _close_ubank_then_usb_hub(None)
        sys.exit(3)

    # 4. 等待主界面加载
    desktop = Desktop(backend="uia")
    print("等待主界面加载...")
    main_win = wait_for_main_window(desktop)
    if not main_win:
        print("未找到主界面窗口，终止执行")
        _close_ubank_then_usb_hub(None)
        sys.exit(4)

    _sleep_and_check(10, "等待主界面页面加载")  # 等待页面完全加载
    _dismiss_ubank_script_error_dialogs()
    print("页面加载完成...")

    # 5. 点击顶部导航栏的"转账支付"
    _check_ubank_crash("点击转账支付前")
    _dismiss_ubank_script_error_dialogs()
    if not click_control_by_name(main_win, "转账支付"):
        # fail-closed：未命中导航控件视为环境异常，绝对不能继续往后走经办流程。
        print("[fail-closed] 未找到顶部导航「转账支付」，终止执行")
        _screenshot("nav_转账支付_未找到")
        _close_ubank_then_usb_hub(main_win)
        sys.exit(NAVIGATION_EXIT_CODE)
    _sleep_and_check(2, "等待转账支付菜单")  # 等待转账支付菜单展开/跳转
    _dismiss_ubank_script_error_dialogs()

    # 6. 点击左侧"单笔转账经办"
    _check_ubank_crash("点击单笔转账经办前")
    _dismiss_ubank_script_error_dialogs()
    if not click_control_by_name(main_win, "单笔转账经办"):
        print("[fail-closed] 未找到左侧「单笔转账经办」，终止执行")
        _screenshot("nav_单笔转账经办_未找到")
        _close_ubank_then_usb_hub(main_win)
        sys.exit(NAVIGATION_EXIT_CODE)
    _sleep_and_check(3, "等待单笔转账经办页面")  # 等待单笔转账经办页面加载
    _dismiss_ubank_script_error_dialogs()

    # 7. 验证是否成功到达单笔转账经办页面
    print("验证目标页面...")
    success_flag = False
    try:
        for _ in range(10):
            _check_ubank_crash("验证目标页面")
            windows = desktop.windows()
            for w in windows:
                title = w.window_text()
                if "单笔转账经办" in title or "单笔转账" in title:
                    success_flag = True
                    print(f"已到达目标页面: {title}")
                    break
            if success_flag:
                break
            _sleep_and_check(1, "等待目标页面")
    except Exception as e:
        if isinstance(e, UBankCrashDetected):
            raise
        print(f"验证过程异常: {e}")

    if not success_flag:
        print("未能确认进入单笔转账经办页面")
        _sleep_and_check(1, "目标页面失败后准备退出")
        _close_ubank_then_usb_hub(main_win)
        sys.exit(5)

    print("已成功进入单笔转账经办页面")

    # 8. 填写转账表单
    _sleep_and_check(2, "等待表单控件加载")  # 等待表单控件完全加载
    print("开始填写表单...")
    _check_ubank_crash("填写表单前")
    fill_transfer_form(main_win, form, form_loaded=form_loaded)
    _check_ubank_crash("填写表单后")

    # fail-closed: 示例模式（bank_form.json 未加载）下绝对禁止点击任何「经办」。
    # 只允许填表 + 截图，紧接着关闭 U-BANK / USB Hub，避免占位值在 UI 上撞到任何
    # 真实提交路径。任何放宽都必须显式由调用方提供 bank_form.json。
    if not form_loaded:
        print("=" * 60)
        print("[示例模式 fail-closed] bank_form.json 未加载，禁止点击「经办」")
        print("[示例模式 fail-closed] 已完成填表与截图，直接关闭 U-BANK / USB Hub")
        print("=" * 60)
        _screenshot("demo_skip_submit")
        _close_ubank_then_usb_hub(main_win)
        sys.exit(DEMO_MODE_EXIT_CODE)

    if not _click_submit_button(main_win):
        print("未能点击第一次「经办」按钮，终止执行，避免将未提交流水标记为成功")
        _close_ubank_then_usb_hub(main_win)
        sys.exit(SUBMIT_CLICK_EXIT_CODE)
    _check_ubank_crash("第一次经办按钮点击后")
    _sleep_and_check(3, "第一次经办点击后等待二次经办页面")
    _screenshot("10_第一次经办等待后")
    if _close_post_submit_warning_if_any("第一次经办后", main_win):
        _close_ubank_then_usb_hub(main_win)
        sys.exit(POST_SUBMIT_WARNING_EXIT_CODE)

    if TEST_MODE_SKIP_SECOND_SUBMIT:
        print("[测试模式] 已跳过第二次「经办」，第一次经办后截图完成，准备关闭 U-BANK")
        print("[测试模式] 如需开启生产二次经办，请设置环境变量 ZHIDAN_TEST_MODE=0")
        _close_ubank_then_usb_hub(main_win)
        print("========== 制单测试流程完成 ==========")
        print("已点击第一次经办并截图，第二次经办已跳过，U-BANK 和 USB Hub 已关闭")
        print("======================================")
        return

    print("[生产模式] 开始点击第二次「经办」")
    if not _click_submit_button(main_win, prefer_confirmation=True):
        print("未能点击第二次「经办」按钮，终止执行，避免将未提交流水标记为成功")
        _close_ubank_then_usb_hub(main_win)
        sys.exit(SUBMIT_CLICK_EXIT_CODE)
    _check_ubank_crash("第二次经办按钮点击后")
    _sleep_and_check(5, "第二次经办点击后等待结果")
    _screenshot("11_第二次经办等待后")
    if _close_post_submit_warning_if_any("第二次经办后", main_win):
        _close_ubank_then_usb_hub(main_win)
        sys.exit(POST_SUBMIT_WARNING_EXIT_CODE)
    _close_ubank_then_usb_hub(main_win)
    print("========== 制单生产流程完成 ==========")
    print("已点击第二次经办并截图，U-BANK 和 USB Hub 已关闭")
    print("======================================")

def main():
    """启动全局崩溃守护线程后运行制单主流程。"""
    _clear_stale_ubank_crash_dialogs()

    watcher = _CrashWatcher(poll_interval=0.02)
    crash._GLOBAL_WATCHER = watcher
    watcher.start()
    print("已启动 U-BANK 崩溃守护线程（WinEventHook + 20ms 轮询兜底）")
    if crash.CRASH_DIAG_MODE:
        print("已开启 U-BANK 崩溃诊断模式：发现崩溃时不强杀，LocalDump 目录为 C:\\tmp\\FirmbankDumps")

    try:
        try:
            _main_impl()
        except UBankCrashDetected:
            raise
        except Exception:
            _close_ubank_then_usb_hub(None)
            if watcher.fired:
                raise UBankCrashDetected(watcher.fired_title) from None
            raise

        if watcher.fired:
            raise UBankCrashDetected(watcher.fired_title)
    finally:
        watcher.stop()
        if crash._GLOBAL_WATCHER is watcher:
            crash._GLOBAL_WATCHER = None
        _run_generated_artifact_cleanup()

if __name__ == "__main__":
    try:
        main()
    except UBankCrashDetected as e:
        _turn_off_usb_hub_ports()
        print(f"招行客户端崩溃，制单失败: {e}")
        sys.exit(crash.UBANK_CRASH_EXIT_CODE)
