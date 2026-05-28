import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

from .artifacts import prepare_artifact_layout
from .browser import _find_edge_executable, _prepare_chrome_extensions
from .config import *
from .config import _LEGACY_PASSWORD
from .debug import _debug_checkpoint, _setup_file_logging, _setup_stdout_utf8, _wait_full_load
from .edge_policy import ensure_boc_edge_certificate_auto_select_policy
from .keyboard import _ITC_KB_SLOT, _ITC_OK
from .login import (
    _click_certificate_login,
    _click_payment_transfer,
    _fill_page_password_and_login,
    _maybe_submit_ushield_password,
)
from .proxy import (
    _build_host_resolver_rules,
    _direct_network_preflight,
    _disable_proxy_for_playwright,
    _restore_proxy_env,
)
from .transfer import _fill_transfer_form, _submit_transfer_order
from .usbhub import _power_off_all_usbhub_ports, _power_on_boc_usbhub_port
from .windows import (
    _close_auto_opened_boc_home_tab,
    _close_boc_public_home_windows,
    _close_native_boc_dialogs,
    _close_ushield_unplug_dialogs,
    _confirm_certificate_selection,
    _dismiss_browser_chrome_tooltips,
)

_OPEN_LOGIN_ARGS = {"--open-login", "--login-page", "--stop-at-login-page"}
_LOGIN_ONLY_ARGS = {"--login-only", "--stop-after-login"}
_KEEP_OPEN_ARGS = {"--keep-open"}
_CLOSE_ON_FINISH_ARGS = {"--close-on-finish"}
_OPEN_LOGIN_VALUES = {"login-page", "login_page", "open-login", "pre-login"}
_LOGIN_ONLY_VALUES = {"login", "logged-in", "post-login", "after-login"}


@dataclass(frozen=True)
class RuntimeOptions:
    stop_at_login_page: bool
    stop_after_login: bool
    close_on_finish: bool
    power_on_usbhub: bool
    all_off_usbhub: bool


def _resolve_runtime_options() -> RuntimeOptions:
    """Resolve env plus lightweight CLI flags."""
    stop_at_login_page = BOC_STOP_AT_LOGIN_PAGE
    stop_after_login = BOC_STOP_AFTER_LOGIN
    open_login_from_cli = False
    login_only_from_cli = False
    keep_open_from_cli = False
    close_from_cli = False

    for raw_arg in sys.argv[1:]:
        arg = raw_arg.strip().lower()
        if arg in _OPEN_LOGIN_ARGS:
            stop_at_login_page = True
            stop_after_login = False
            open_login_from_cli = True
        elif arg in _LOGIN_ONLY_ARGS:
            stop_after_login = True
            login_only_from_cli = True
        elif arg.startswith("--run-until="):
            value = arg.partition("=")[2].strip()
            if value in _OPEN_LOGIN_VALUES:
                stop_at_login_page = True
                stop_after_login = False
                open_login_from_cli = True
            elif value in _LOGIN_ONLY_VALUES:
                stop_after_login = True
                login_only_from_cli = True
        elif arg in _KEEP_OPEN_ARGS:
            keep_open_from_cli = True
        elif arg in _CLOSE_ON_FINISH_ARGS:
            close_from_cli = True

    close_on_finish = BOC_CLOSE_ON_FINISH
    if open_login_from_cli or login_only_from_cli or keep_open_from_cli:
        close_on_finish = False
    if close_from_cli:
        close_on_finish = True

    power_on_usbhub = BOC_USBHUB_POWER_ON_START and not stop_at_login_page
    all_off_usbhub = BOC_USBHUB_ALL_OFF_ON_FINISH and not stop_at_login_page
    return RuntimeOptions(
        stop_at_login_page=stop_at_login_page,
        stop_after_login=stop_after_login,
        close_on_finish=close_on_finish,
        power_on_usbhub=power_on_usbhub,
        all_off_usbhub=all_off_usbhub,
    )


def main() -> None:
    _setup_stdout_utf8()
    prepare_artifact_layout()
    _setup_file_logging(include_console=bool(sys.stdout and sys.stdout.isatty()))
    _debug_checkpoint("脚本启动")
    options = _resolve_runtime_options()

    if _ITC_OK:
        print(f"[输入后端] Interception 驱动（键盘 slot={_ITC_KB_SLOT}）", flush=True)
    else:
        print("[输入后端] SendInput 回退（证书弹窗可能被拦截）", flush=True)

    if _LEGACY_PASSWORD and "BOC_LOGIN_PASSWORD" not in os.environ:
        print("[兼容] 使用旧变量 BOC_USHIELD_PASSWORD 作为网页登录密码；建议改为 BOC_LOGIN_PASSWORD", flush=True)
    if BOC_AUTO_USHIELD_PIN:
        print("[风险开关] 已启用 BOC_AUTO_USHIELD_PIN=1，将尝试自动输入 U盾 PIN", flush=True)
    else:
        print("[安全] U盾 PIN 默认由用户在原生弹窗中手动输入", flush=True)
    if BOC_ENABLE_TRANSFER_FILL:
        print("[风险开关] 已启用 BOC_ENABLE_TRANSFER_FILL=1，将预填测试转账字段但不会提交", flush=True)
    else:
        print("[安全] 未启用转账填表，登录后只进入转账汇款页面", flush=True)
    if BOC_ENABLE_ORDER_SUBMIT:
        print("[风险开关] 已启用 BOC_ENABLE_ORDER_SUBMIT=1，将点击制单提交按钮但不会继续确认/支付", flush=True)
    else:
        print("[安全] 未启用制单提交，填表后不会点击提交按钮", flush=True)
    if options.stop_at_login_page:
        print("[流程模式] 只打开登录页：不会点击数字证书登录、不会输入 PIN/密码", flush=True)
    if options.stop_after_login:
        print("[流程模式] 登录后停止：不会进入付款服务、转账汇款、填表或制单", flush=True)
    if options.close_on_finish:
        print("[清理策略] BOC_CLOSE_ON_FINISH=1，主流程结束后自动关闭本次浏览器和中行弹窗", flush=True)
    else:
        print("[清理策略] BOC_CLOSE_ON_FINISH=0，主流程结束后保持浏览器打开", flush=True)
    if options.stop_at_login_page and BOC_USBHUB_POWER_ON_START:
        print("[USBHub] 登录页模式不触碰 USBHub，不会开启 U盾口", flush=True)
    if options.power_on_usbhub:
        print(
            f"[USBHub] 启动前将只开启 {BOC_USBHUB_PORT} 口（{BOC_USBHUB_COM}）",
            flush=True,
        )
        _power_on_boc_usbhub_port()
    if options.all_off_usbhub:
        print("[USBHub] 主流程结束后将关闭所有 USBHub 口", flush=True)
    _debug_checkpoint("启动参数检查完成")
    _close_auto_opened_boc_home_tab(wait_seconds=3.0)

    proxy_env = None
    profile_dir = None
    context = None
    try:
        proxy_env = _disable_proxy_for_playwright()
        _debug_checkpoint("代理环境已临时清理")
        resolved_boc_hosts = _direct_network_preflight()
        _debug_checkpoint("银行直连网络预检完成")
        ensure_boc_edge_certificate_auto_select_policy()
        _debug_checkpoint("Edge证书自动选择策略已检查")
        profile_dir, extension_source_browser = _prepare_chrome_extensions()
        _debug_checkpoint("中行证书扩展已复制到临时profile")

        # 收集所有扩展版本目录路径
        ext_base = Path(profile_dir) / "Default" / "Extensions"
        ext_dirs = []
        for ext_id in ext_base.iterdir():
            if not ext_id.is_dir():
                continue
            for ver in ext_id.iterdir():
                if (ver / "manifest.json").is_file():
                    ext_dirs.append(str(ver))
                    break

        launch_kwargs = {
            "user_data_dir": profile_dir,
            "headless": False,
            "viewport": {"width": 1366, "height": 860},
            "locale": "zh-CN",
            "ignore_default_args": ["--disable-extensions"],
            "args": [
                "--no-proxy-server",
                "--proxy-server=direct://",
                "--proxy-bypass-list=*",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=Translate,TranslateUI",
                "--lang=zh-CN",
                f"--load-extension={','.join(ext_dirs)}",
            ],
        }
        host_resolver_rules = _build_host_resolver_rules(resolved_boc_hosts)
        if host_resolver_rules:
            launch_kwargs["args"].append(f"--host-resolver-rules={host_resolver_rules}")
            print(f"[浏览器] 已固定中行域名解析：{host_resolver_rules}", flush=True)
        if extension_source_browser == "Edge":
            edge_path = _find_edge_executable()
            if not edge_path:
                raise SystemExit("[终止] 找到 Edge 中行扩展，但未找到 msedge.exe")
            launch_kwargs["executable_path"] = edge_path
            print(f"[浏览器] 使用系统 Edge 加载 Edge 扩展：{edge_path}", flush=True)
        else:
            print("[浏览器] 使用 Playwright Chromium 加载扩展", flush=True)
        _debug_checkpoint("准备启动Playwright浏览器")

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(**launch_kwargs)
            _debug_checkpoint("Playwright浏览器已启动")
            page = context.pages[0] if context.pages else context.new_page()
            for extra_page in context.pages[1:]:
                try:
                    extra_page.close()
                except Exception:
                    pass
            _dismiss_browser_chrome_tooltips()
            _debug_checkpoint("已整理浏览器页面", page)
            page.goto(BOC_URL, wait_until="domcontentloaded")
            if _close_boc_public_home_windows():
                try:
                    page.bring_to_front()
                except Exception:
                    pass
            _dismiss_browser_chrome_tooltips()
            _debug_checkpoint("已发起打开中行登录页", page)
            _wait_full_load(page, timeout_ms=60000)
            if _close_boc_public_home_windows():
                try:
                    page.bring_to_front()
                except Exception:
                    pass
                _debug_checkpoint("已清理浏览器启动带出的中行官网", page)
            _dismiss_browser_chrome_tooltips()
            print(f"[已打开] {BOC_URL}", flush=True)
            _debug_checkpoint("中行登录页加载完成", page)
            if options.stop_at_login_page:
                print("[停止] 已打开中行登录页，本次不点击数字证书登录", flush=True)
                _debug_checkpoint("登录页停止_未点击数字证书登录", page)
            elif _click_certificate_login(page):
                print("[已点击] 数字证书登录", flush=True)
                cert_ok = _confirm_certificate_selection(wait_seconds=6.0)
                _debug_checkpoint(f"证书选择确认结果_{'成功' if cert_ok else '失败'}", page)
                if not cert_ok:
                    print("[终止] 证书选择未确认成功，停止后续 U盾 PIN、登录和填表", flush=True)
                    _debug_checkpoint("证书选择失败_停止后续操作", page)
                    raise SystemExit(3)
                pin_submitted = _maybe_submit_ushield_password(
                    page,
                    BOC_USHIELD_PIN,
                    auto_submit=BOC_AUTO_USHIELD_PIN,
                )
                _debug_checkpoint(f"U盾PIN处理结果_{'已自动提交' if pin_submitted else '未自动提交'}", page)
                if pin_submitted:
                    print("[已输入] U盾 PIN 并提交", flush=True)

                if BOC_LOGIN_PASSWORD:
                    _debug_checkpoint("准备填写网页登录密码", page)
                    if _fill_page_password_and_login(page, BOC_LOGIN_PASSWORD):
                        print("[已输入] 登录页用户密码并点击登录", flush=True)
                        _debug_checkpoint("网页登录密码已提交", page)
                        _wait_full_load(page, timeout_ms=60000)
                        _debug_checkpoint("网页登录后页面等待完成", page)
                        if options.stop_after_login:
                            print("[停止] 已登录到企业网银页面，本次不进入付款服务/转账汇款", flush=True)
                            _debug_checkpoint("登录后停止_未进入付款服务", page)
                        elif _click_payment_transfer(page, timeout_ms=60000):
                            print("[已进入] 转账汇款", flush=True)
                            _debug_checkpoint("已进入转账汇款页面", page)
                            _wait_full_load(page, timeout_ms=60000)
                            _debug_checkpoint("转账汇款页面等待完成", page)
                            if BOC_ENABLE_TRANSFER_FILL:
                                if not _fill_transfer_form(page, timeout_ms=60000):
                                    print("[警告] 转账表单未完整填写（未提交）", flush=True)
                                    _debug_checkpoint("转账表单预填失败_未提交", page)
                                else:
                                    _debug_checkpoint("转账表单预填成功_未提交", page)
                                    if BOC_ENABLE_ORDER_SUBMIT:
                                        if _submit_transfer_order(page, timeout_ms=30000):
                                            print("[已执行] 制单提交按钮已点击；不继续确认/支付/授权", flush=True)
                                            _debug_checkpoint("制单提交已点击_停止后续操作", page)
                                        else:
                                            print("[警告] 未能点击制单提交按钮", flush=True)
                                            _debug_checkpoint("制单提交按钮点击失败", page)
                            else:
                                print(
                                    "[跳过] 未设置 BOC_ENABLE_TRANSFER_FILL=1，不自动填写资金字段",
                                    flush=True,
                                )
                                _debug_checkpoint("已跳过转账表单填写", page)
                        else:
                            print("[警告] 未能进入 转账汇款", flush=True)
                            _debug_checkpoint("进入转账汇款失败", page)
                    else:
                        print("[警告] 未能自动填写登录页用户密码", flush=True)
                        _debug_checkpoint("网页登录密码自动化失败", page)
                else:
                    print("[跳过] 未配置 BOC_LOGIN_PASSWORD，请手动完成网页登录密码", flush=True)
                    _debug_checkpoint("未配置网页登录密码_等待人工处理", page)
            else:
                print("[警告] 未找到或未能点击：数字证书登录", flush=True)
                _debug_checkpoint("数字证书登录点击失败", page)
            print(f"[完成] 企业网银页面：{page.url}", flush=True)
            if options.close_on_finish:
                _debug_checkpoint("主流程结束_准备自动清理", page)
                print("[清理策略] 主流程结束，自动关闭本次浏览器。", flush=True)
            else:
                _debug_checkpoint("主流程结束_浏览器保持打开", page)
                print("浏览器保持打开，关闭窗口或按 Ctrl+C 退出。", flush=True)
                try:
                    while context.browser.is_connected():
                        time.sleep(1)
                except KeyboardInterrupt:
                    pass
    finally:
        _debug_checkpoint("开始清理浏览器和临时profile")
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if profile_dir:
            shutil.rmtree(profile_dir, ignore_errors=True)
            print(f"[清理] 已删除临时 profile: {profile_dir}", flush=True)
        if proxy_env is not None:
            _restore_proxy_env(proxy_env)
            print("[清理] 已恢复代理环境变量", flush=True)
        if options.close_on_finish:
            _close_native_boc_dialogs()
            _close_boc_public_home_windows()
        if options.all_off_usbhub:
            _power_off_all_usbhub_ports()
            _close_ushield_unplug_dialogs(wait_seconds=5.0)
            _close_native_boc_dialogs()
            _close_boc_public_home_windows()
