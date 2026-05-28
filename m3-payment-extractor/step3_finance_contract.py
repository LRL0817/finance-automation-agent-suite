"""获取数据 - 步骤3: 进入报表分析页 → 财务报表 → 合同付款（凭证）

报表分析在新窗口打开。这里直接用已知的报表中心 URL 打开，避免依赖 step2 的导航链。
持久化用户目录里的 cookie 已携带登录态。
"""
import os
from playwright.sync_api import sync_playwright

from step1_open import USER_DATA_DIR, OA_URL, chromium_launch_args, mark_browser_profile_clean, try_login

REPORT_URL = os.getenv(
    "M3_REPORT_URL",
    "https://newoa.guorui.net/seeyon/vreport/vReport.do"
    "?method=vReportView&portalId=1130463384810964158&_resourceCode=F08_report_view",
)
DEFAULT_CONTRACT_REPORT_NAME = "合同付款（凭证）"
CONTRACT_REPORT_NAME = os.getenv("M3_CONTRACT_REPORT_NAME", DEFAULT_CONTRACT_REPORT_NAME).strip() or DEFAULT_CONTRACT_REPORT_NAME


def contract_report_variants(report_name: str) -> list[str]:
    """Return exact report titles/aliases to try, preserving the requested title first."""
    target = (report_name or DEFAULT_CONTRACT_REPORT_NAME).strip() or DEFAULT_CONTRACT_REPORT_NAME
    aliases = {
        "合同付款凭证": "合同付款（凭证）",
        "合同付款云链": "合同付款（云链）",
    }
    canonical = aliases.get(target, target)
    variants = [canonical]
    if canonical == "合同付款（凭证）":
        variants.append("合同付款凭证")
    elif canonical == "合同付款（云链）":
        variants.append("合同付款云链")
    return list(dict.fromkeys(variants))


def click_finance_report(page) -> None:
    """点击左侧『财务报表』。"""
    page.wait_for_selector('text=报表中心', timeout=10000)
    candidates = page.locator('xpath=//*[normalize-space(text())="财务报表"]')
    n = candidates.count()
    clicked = False
    for i in range(n):
        el = candidates.nth(i)
        try:
            if not el.is_visible():
                continue
            el.scroll_into_view_if_needed()
            el.click()
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        page.locator('text=财务报表').first.click(force=True)
    page.wait_for_function(
        "() => /财务报表\\s*\\(/.test(document.body.innerText)",
        timeout=8000,
    )
    page.wait_for_timeout(500)


def click_contract_payment(page, report_name: str = CONTRACT_REPORT_NAME) -> str:
    """点击指定合同付款报表卡片，默认固定为『合同付款（凭证）』。"""
    last_error = ""
    for name in contract_report_variants(report_name):
        locators = [
            page.locator(f'xpath=//*[normalize-space(text())="{name}"]'),
            page.get_by_text(name, exact=True),
            page.locator(f"text={name}"),
        ]
        for locator in locators:
            try:
                item = locator.first
                item.wait_for(state="visible", timeout=3000)
                item.scroll_into_view_if_needed(timeout=2000)
                item.click(timeout=5000)
                page.wait_for_load_state("domcontentloaded")
                return name
            except Exception as exc:
                last_error = str(exc)
    raise RuntimeError(f"未找到『{report_name}』报表卡片。最后错误: {last_error}")


def run():
    os.makedirs(USER_DATA_DIR, exist_ok=True)
    mark_browser_profile_clean()
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            USER_DATA_DIR,
            headless=False,
            viewport={"width": 1366, "height": 800},
            args=chromium_launch_args(),
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(REPORT_URL, wait_until="domcontentloaded")

        # 若被重定向到登录页则登录后重新进入
        if "main.do" in page.url or page.locator('input[type="password"]').count() > 0:
            page.goto(OA_URL, wait_until="domcontentloaded")
            try_login(page)
            page.wait_for_timeout(1500)
            page.goto(REPORT_URL, wait_until="domcontentloaded")

        print("已打开报表分析:", page.url)

        click_finance_report(page)
        print("已点击『财务报表』。")

        selected_report = click_contract_payment(page)
        print(f"已点击『{selected_report}』，当前页:", page.url)

        print("关闭浏览器窗口即结束。")
        page.wait_for_event("close", timeout=0)


if __name__ == "__main__":
    run()
