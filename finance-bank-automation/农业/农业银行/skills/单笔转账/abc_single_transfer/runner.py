# -*- coding: utf-8 -*-
from __future__ import annotations

"""农行单笔转账自动化主流程。"""

from . import runtime as rt
from .runtime import *
from .data import *
from .auth import *
from .navigation import *
from .form import *
from .usb import *
from .servo_ok import *

def main() -> int:
    load_environment()
    log.info("[排错] 本次运行 ID: %s", RUN_ID)
    log.info("[排错] 本次运行日志: %s", os.path.join(DEBUG_RUN_DIR, "run.log"))
    if env_flag("ENABLE_LIVE_SCREENSHOTS", False):
        log.info("[运行截图] 本次运行整屏截图目录: %s", LIVE_SCREENSHOT_DIR)
    else:
        log.info("[运行截图] 整屏截图默认关闭，需要排错时设置 ENABLE_LIVE_SCREENSHOTS=true")
    if env_flag("ENABLE_SCREENSHOT"):
        log.info("[排错] 阶段截图目录: %s", DEBUG_SCREENSHOT_DIR)
    else:
        log.info("[排错] 阶段截图未启用；需要时在 .env 中设置 ENABLE_SCREENSHOT=true")
    debug_checkpoint("配置加载完成")
    cleanup_old_runtime_artifacts()
    stop_after_login = env_flag("ABC_STOP_AFTER_LOGIN", False)

    login_url, _is_direct = get_login_url()

    if not prepare_abc_usb12_if_enabled():
        return 3

    transfer_image_path = os.getenv("TRANSFER_IMAGE_PATH", "").strip()

    transfer_batch: Optional[list[dict]] = None
    transfer_data: Optional[dict] = None
    if stop_after_login:
        log.warning("ABC_STOP_AFTER_LOGIN=true：本次只登录到企业网银主页，跳过全部转账数据加载和制单导航")
    else:
        try:
            transfer_batch = load_configured_transfer_batch()
            if transfer_batch:
                log_transfer_batch_summary(transfer_batch)
                if env_flag("ABC_ALLOW_SUBMIT_ONCE", False) and len(transfer_batch) != 1:
                    log.error("ABC_ALLOW_SUBMIT_ONCE=true 时必须且只能加载 1 条转账数据，当前 %d 条", len(transfer_batch))
                    return 2
            else:
                transfer_data = load_configured_transfer_data()
                if transfer_data:
                    log_transfer_summary(transfer_data)
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
            log.error("加载转账数据失败：%s", e)
            log.error("转账数据已存在但加载/校验失败，为安全起见不继续登录网银。")
            return 2

    if transfer_image_path:
        if os.path.exists(transfer_image_path):
            log.info(
                "TRANSFER_IMAGE_PATH 仅作为人工核对参考截图路径，脚本不会自动识别图片内容"
            )
        else:
            log.warning(
                "TRANSFER_IMAGE_PATH 指向的文件不存在: %s（已忽略）", transfer_image_path
            )

    kb_password = get_kb_password()
    transfer_kb_password = (
        kb_password if env_flag("ABC_TRANSFER_KB_PASSWORD_AFTER_TRADE", False) else ""
    )
    auto_enter_enabled = env_flag("KB_PASSWORD_AUTO_ENTER", True)

    playwright: Optional[Playwright] = None
    browser = None
    context: Optional[BrowserContext] = None
    page: Optional[Page] = None
    rt._live_recorder = start_live_screenshot_recorder()

    try:
        debug_checkpoint("准备启动浏览器")
        log.info("启动 Chromium（必须 headful，K 宝插件与原生证书弹窗依赖头部窗口）")
        playwright = sync_playwright().start()
        launch_kwargs: dict = {"headless": False}
        # 默认仅企业网银主域名（cbank.abchina.com.cn）走证书自动选择；
        # 广域规则 https://[*.]abchina.com.cn 风险更大（任何 abchina 子域都能拿证书），
        # 必须显式 ABC_CERT_AUTOSELECT_BROAD=true 才会启用。
        cert_auto_select_rules = [
            {
                "pattern": "https://cbank.abchina.com.cn",
                "filter": {},
            },
            {
                "pattern": "https://cbank.abchina.com.cn:443",
                "filter": {},
            },
        ]
        if env_flag("ABC_CERT_AUTOSELECT_BROAD", False):
            cert_auto_select_rules.append(
                {
                    "pattern": "https://[*.]abchina.com.cn",
                    "filter": {},
                }
            )
            log.warning(
                "[安全] ABC_CERT_AUTOSELECT_BROAD=true，已启用 abchina.com.cn 全子域证书自动选择"
            )
        launch_kwargs["args"] = [
            "--auto-select-certificate-for-urls="
            + json.dumps(cert_auto_select_rules, ensure_ascii=False, separators=(",", ":")),
            "--no-proxy-server",
            "--window-position=0,0",
            "--window-size=1600,900",
        ]
        launch_env = dict(os.environ)
        for proxy_env_name in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
        ):
            launch_env.pop(proxy_env_name, None)
        launch_kwargs["env"] = launch_env
        log.info(
            "已启用 Chrome 证书自动选择策略（默认仅 cbank.abchina.com.cn；"
            "如需扩展到全 abchina 子域，设置 ABC_CERT_AUTOSELECT_BROAD=true）"
        )
        log.info("已强制 Chromium 不使用代理（--no-proxy-server，并清理代理环境变量）")
        browser_channel = os.getenv("BROWSER_CHANNEL", "").strip()
        if browser_channel:
            launch_kwargs["channel"] = browser_channel
            log.info("使用 BROWSER_CHANNEL=%s", browser_channel)
        browser = playwright.chromium.launch(**launch_kwargs)
        context = browser.new_context(viewport={"width": 1600, "height": 900})
        page = context.new_page()
        debug_checkpoint("空白浏览器页已创建", page)

        page = open_corporate_login_page(page, context, login_url)
        assert_current_page_allowed(page, "企业网银登录页")
        debug_checkpoint("企业网银登录页定位完成", page)
        click_cert_login(page)
        # click_cert_login 之后 Chrome 内嵌证书模态会立刻拉起，page.screenshot 会
        # 被它阻塞到超时；证书阶段的所有 debug_checkpoint 都跳过 page 截图。
        debug_checkpoint("已点击证书登录按钮", page, skip_page_screenshot=True)
        cert_status = handle_certificate_selection(page)
        debug_checkpoint(
            f"证书弹窗处理链结束 ({cert_status})", page, skip_page_screenshot=True
        )

        # 根据证书弹窗状态决定后续流程：DOM 是否安全？K 宝窗口最多等多久？
        if cert_status == CERT_PASSWORD_WINDOW_DETECTED:
            log.info("[流程] K 宝密码窗口已就绪，跳过「继续登录」DOM 轮询")
            kb_window_wait_s = env_float("KB_WINDOW_WAIT_S_QUICK", 5.0)
        elif cert_status == CERT_AUTO_CONFIRMED:
            wait_and_click_continue_login(page)
            debug_checkpoint("继续登录提示处理结束", page)
            kb_window_wait_s = env_float("KB_WINDOW_WAIT_S_AUTO", 60.0)
        else:  # CERT_MANUAL_REQUIRED
            log.warning("=" * 60)
            log.warning("[流程] 证书弹窗自动处理失败，进入人工接管模式：")
            log.warning("  1) 请在浏览器中手动选择证书并点击「确定」")
            log.warning("  2) 若出现「继续登录」温馨提示，请手动点击「继续登录」")
            log.warning("  3) K 宝密码窗口出现后脚本将自动接管输入（若已配置密码）")
            log.warning("=" * 60)
            if env_flag("ABC_CLICK_CONTINUE_ON_CERT_MANUAL", True):
                log.info(
                    "[继续登录] 证书自动处理失败后，先短查网页「继续登录」提示；"
                    "若命中则自动点击后继续等待 K 宝密码窗口"
                )
                try:
                    wait_and_click_continue_login(
                        page,
                        max_wait_s=env_float(
                            "ABC_CONTINUE_ON_CERT_MANUAL_WAIT_S", 5.0
                        ),
                    )
                    debug_checkpoint(
                        "证书自动处理失败后继续登录提示处理结束",
                        page,
                        skip_page_screenshot=True,
                    )
                except (PlaywrightError, PlaywrightTimeout) as exc:
                    log.warning(
                        "[继续登录] 证书自动处理失败后的网页提示处理异常（%s），"
                        "继续转入原生窗口等待",
                        type(exc).__name__,
                    )
            log.info(
                "[流程] 已暂停所有 DOM 轮询（避免被原生证书模态阻塞）；"
                "改为仅用原生窗口检测等待 K 宝密码窗口"
            )
            kb_window_wait_s = env_float("KB_WINDOW_WAIT_S_MANUAL", 240.0)

        auto_entered = False
        if kb_password:
            auto_entered = input_kb_password(kb_password, wait_seconds=kb_window_wait_s)
            kb_password = None  # 解绑局部引用，尽量缩短生命周期
        else:
            log.warning(
                "未提供 K 宝密码，请在浏览器中手动输入；"
                "脚本会先用原生窗口轮询等待 K 宝窗口出现，确认证书阶段已完成再恢复 DOM 操作"
            )
            if not wait_for_kb_password_window(kb_window_wait_s):
                log.warning(
                    "[K 宝] %.0fs 内未检测到 K 宝密码窗口；"
                    "可能仍需在浏览器中人工完成证书选择/继续登录后才能继续",
                    kb_window_wait_s,
                )

        # 除非密码自动输入成功并允许 KB_PASSWORD_AUTO_ENTER，否则暂停等人工确认。
        if auto_entered and auto_enter_enabled:
            log.info("已自动输入密码并按 Enter，等待登录推进...")
            if env_flag("ABC_OK_SERVO_AFTER_KB_PASSWORD", False):
                log.warning(
                    "[舵机OK] ABC_OK_SERVO_AFTER_KB_PASSWORD 已不再用于登录阶段，"
                    "登录 K 宝密码提交后不会触发物理 OK；舵机只允许在交易 K 宝密码提交后触发"
                )
            if env_flag("ABC_CLICK_CONTINUE_AFTER_KB_OK", True):
                continue_wait_s = env_float("ABC_CONTINUE_AFTER_KB_OK_WAIT_S", 8.0)
                log.info("[继续登录] K 宝确认后补查网页「继续登录」弹窗")
                wait_and_click_continue_login(page, max_wait_s=continue_wait_s)
        elif auto_entered:
            if not wait_for_manual_login_prompt(
                "K 宝密码已输入，请人工确认登录，看到企业网银首页后继续..."
            ):
                return 130
        else:
            if not wait_for_manual_login_prompt(
                "未自动输入 K 宝密码（窗口未确认或被跳过），请在浏览器中人工完成密码与登录，"
                "看到企业网银首页后继续..."
            ):
                return 130
        # K 宝密码窗口由硬件厂商插件渲染（独立进程），但保险起见仍跳过 page 截图，
        # 避免在登录流转尚未完成时触发 Playwright 协议调用。
        debug_checkpoint("K 宝密码阶段结束", page, skip_page_screenshot=True)

        page = find_active_home_page(context, page)
        assert_current_page_allowed(page, "企业网银主页")
        debug_checkpoint("企业网银主页定位结束", page)
        if stop_after_login:
            log.info("ABC_STOP_AFTER_LOGIN=true，已登录到企业网银主页；不进入付款业务、不打开单笔转账")
            if env_flag("ABC_KEEP_BROWSER_OPEN", True):
                _wait_for_ctrl_c("已登录到企业网银主页，浏览器保持打开；需要结束时按 Ctrl+C...")
            else:
                log.info("ABC_KEEP_BROWSER_OPEN=false，登录验证结束后关闭浏览器")
            return 0
        navigate_to_single_transfer(page)
        debug_checkpoint("单笔转账页面导航结束", page)

        submitted_once = False
        if transfer_batch:
            results = fill_transfer_batch(page, transfer_batch)
            debug_checkpoint("批量表单填写结束", page)
            if env_flag("ABC_ALLOW_SUBMIT_ONCE", False):
                failed = []
                if results:
                    failed = list(results[0].get("failed") or [])
                submitted_once = click_submit_once_after_fill(
                    page,
                    {name: False for name in failed} if failed else {F_ACCOUNT: True, F_NAME: True, F_BANK: True, F_AMOUNT: True, F_PURPOSE: True},
                )
        elif transfer_data:
            statuses = fill_transfer_form(page, transfer_data)
            debug_checkpoint("表单填写结束", page)
            submitted_once = click_submit_once_after_fill(page, statuses)
        else:
            log.info("无可用转账数据，已跳过表单填写")
            debug_checkpoint("无转账数据，表单填写已跳过", page)

        post_submit_confirmed = False
        trade_info_confirmed = False
        transfer_kb_submitted = False
        if submitted_once:
            post_submit_confirmed = click_post_submit_message_confirm_once(page)
            trade_info_confirmed = click_trade_info_confirm_once(page)
            if trade_info_confirmed:
                transfer_kb_submitted = submit_transfer_kb_password_once(page, transfer_kb_password)
                transfer_kb_password = ""

        if submitted_once and transfer_kb_submitted:
            # fail-closed：代码默认 False。生产入口 / M3 生产路由需要物理 OK 时
            # 必须显式 ABC_OK_SERVO_AFTER_TRANSFER_KB_PASSWORD=true。仍只在
            # submitted_once 且 transfer_kb_submitted 之后才可能走到这里。
            should_press_after_transfer_kb = env_flag(
                "ABC_OK_SERVO_AFTER_TRANSFER_KB_PASSWORD", False
            ) or env_flag("ABC_OK_SERVO_AFTER_SUBMIT", False)
            if should_press_after_transfer_kb:
                delay_s = env_float(
                    "ABC_OK_SERVO_AFTER_TRANSFER_KB_PASSWORD_DELAY_S",
                    env_float("ABC_OK_SERVO_AFTER_SUBMIT_DELAY_S", 1.0),
                )
                log.warning("[舵机OK] 交易 K 宝密码提交后等待 %.1fs 再触发物理 OK", delay_s)
                safe_sleep(delay_s)
                trigger_ok_servo("交易K宝密码提交后")
                settle_s = env_float("ABC_OK_SERVO_AFTER_SUBMIT_SETTLE_S", 3.0)
                if settle_s > 0:
                    safe_sleep(settle_s)
                    debug_checkpoint("U盾点击器触发后页面状态", page, force_screenshot=True)
        elif submitted_once and env_flag("ABC_OK_SERVO_AFTER_SUBMIT", False):
            if transfer_kb_submitted:
                # Kept only for defensive completeness; the transfer_kb_submitted
                # path above handles the only permitted automatic servo trigger.
                pass
            elif transfer_kb_password_dialog_visible(page):
                log.warning("[舵机OK] 已出现交易 K 宝密码弹窗但未提交密码，拒绝提前触发物理 OK")
            elif trade_info_confirmed:
                log.warning("[舵机OK] 交易 K 宝密码尚未提交，拒绝在交易信息确认后提前触发物理 OK")
            elif post_submit_confirmed:
                log.warning("[舵机OK] 交易 K 宝密码尚未提交，拒绝在提交网页确认后提前触发物理 OK")
            else:
                log.warning("[舵机OK] 交易 K 宝密码尚未提交，拒绝在页面提交后提前触发物理 OK")

        debug_checkpoint("流程结束前最终状态", page, force_screenshot=True)
        safe_screenshot(page, os.path.join(SCRIPT_DIR, "screenshot.png"), label="兼容最终截图")

        log.info("=================================================")
        if env_flag("ABC_ALLOW_SUBMIT_ONCE", False):
            log.warning("流程结束。已按 ABC_ALLOW_SUBMIT_ONCE 门禁执行单条提交测试。")
            log.warning("除本次显式单条提交测试外，脚本默认仍不会自动提交转账。")
        else:
            log.info("流程结束。请人工核对表单内容并手动点击「下一步/提交」。")
            log.info("脚本不会自动提交转账，也不会自动处理验证码或安全校验。")
        log.info("=================================================")
        if env_flag("ABC_KEEP_BROWSER_OPEN", True):
            _wait_for_ctrl_c("浏览器保持打开，等待人工核对...")
        else:
            log.info("ABC_KEEP_BROWSER_OPEN=false，测试模式下不保留浏览器窗口")
        return 0
    except KeyboardInterrupt:
        log.info("中途被 Ctrl+C 终止")
        debug_checkpoint("用户中断后页面状态", page, force_screenshot=True)
        return 130
    except Exception as e:
        log.exception("发生异常：%s: %s", type(e).__name__, e)
        debug_checkpoint("异常发生后页面状态", page, force_screenshot=True)
        safe_screenshot(page, os.path.join(SCRIPT_DIR, "failure_screenshot.png"), label="兼容失败截图")
        if env_flag("ABC_KEEP_BROWSER_OPEN", True):
            _wait_for_ctrl_c("浏览器保留打开供人工排查")
        else:
            log.info("ABC_KEEP_BROWSER_OPEN=false，异常后不保留浏览器窗口")
        return 1
    finally:
        kb_password = None
        transfer_kb_password = ""
        # 确保资源释放，单独 try 防止互相干扰
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass
        if rt._live_recorder is not None:
            try:
                rt._live_recorder.stop()
            finally:
                rt._live_recorder = None
        log.info("浏览器资源已释放")


if __name__ == "__main__":
    sys.exit(main())
