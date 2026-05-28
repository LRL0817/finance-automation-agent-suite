# -*- coding: utf-8 -*-
"""顶层 about:blank 白屏检测的纯函数 / monkeypatch 小测试。

覆盖：
  1. is_about_blank_url：about:blank / about:blank#... 判 True，正常 OA/report URL 判 False。
  2. looks_like_top_level_blank：顶层 about:blank + 空正文 True；OA/report 页 False。
  3. wait_detail_ready：新开详情页停在 about:blank 时抛 M3WhiteScreenError（用假 detail）。

不访问网络、不启动浏览器、不打印/修改 .env、不触碰任何银行/USBHub/生产入口。
注意：import step4_extract 会间接 import step1_open，而既有 step1_open 在导入时
可能对同目录 .env 做 setdefault 读取（不打印、不启动浏览器），本测试不依赖也不改动该行为。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import step4_extract as s4


class FakePage:
    """最小 Playwright Page/Frame 替身：只实现被测代码用到的 url / evaluate / wait_for_timeout。"""

    def __init__(self, url, body_text="", *, locator_hits=False):
        self._url = url
        self._body = body_text
        self._locator_hits = locator_hits
        self.waited_ms = 0

    @property
    def url(self):
        return self._url

    def evaluate(self, _script):
        return self._body

    def wait_for_timeout(self, ms):
        self.waited_ms += ms

    def wait_for_load_state(self, *_args, **_kwargs):
        return None

    @property
    def frames(self):
        return []

    def locator(self, _selector):
        page = self

        class _Loc:
            @property
            def first(self):
                return self

            def count(self):
                return 1 if page._locator_hits else 0

        return _Loc()


class FakeNavPage:
    """最小 Page 替身：goto 真实 URL 时抛 transient 导航错误，记录导航/等待/stop 次数。

    用于验证导航重试不会在退避期间 goto about:blank 后长时间等待。
    """

    def __init__(self, error_text):
        self._error_text = error_text
        self._url = "about:blank"
        self.waited_ms = 0
        self.goto_urls = []
        self.stop_calls = 0

    @property
    def url(self):
        return self._url

    def goto(self, url, **_kwargs):
        self.goto_urls.append(url)
        raise s4.PlaywrightError(self._error_text)

    def evaluate(self, script):
        if "window.stop" in script:
            self.stop_calls += 1
            return None
        return ""  # frame_body_text -> 空正文，配合 about:blank 判为顶层白屏

    def wait_for_timeout(self, ms):
        self.waited_ms += ms


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}: {label}")
    if not cond:
        raise SystemExit(1)


def main():
    # 1. is_about_blank_url
    check("about:blank -> True", s4.is_about_blank_url("about:blank") is True)
    check("about:blank#x -> True", s4.is_about_blank_url("about:blank#blocked") is True)
    check("空 url -> True", s4.is_about_blank_url("") is True)
    check(
        "OA report URL -> False",
        s4.is_about_blank_url("https://oa.example.com/seeyon/rest/cap4/report/report4Result.do") is False,
    )
    check("普通 OA 首页 -> False", s4.is_about_blank_url("https://oa.example.com/seeyon/main.do") is False)

    # 2. looks_like_top_level_blank
    blank_page = FakePage("about:blank", "")
    check("顶层 about:blank + 空正文 -> True", s4.looks_like_top_level_blank(blank_page) is True)

    report_page = FakePage(
        "https://oa.example.com/seeyon/rest/cap4/report/report4Result.do",
        "流水号 申请日期 申请金额 付款方式",
    )
    check("OA report 页 -> False", s4.looks_like_top_level_blank(report_page) is False)

    blank_but_loaded = FakePage("about:blank", "x" * 50)
    check("about:blank 但正文非空 -> False", s4.looks_like_top_level_blank(blank_but_loaded) is False)

    # 3. wait_detail_ready：详情页停在 about:blank 应抛 M3WhiteScreenError
    #    把 grace 调到 0，让等待循环里第一/第二次即可命中连续阈值。
    saved_grace = s4.TOP_BLANK_GRACE_MS
    s4.TOP_BLANK_GRACE_MS = 0
    try:
        blank_detail = FakePage("about:blank#blocked", "", locator_hits=False)
        raised = False
        try:
            s4.wait_detail_ready(blank_detail)
        except s4.M3WhiteScreenError as exc:
            raised = True
            check("详情页 about:blank 错误信息含 url", "url=" in str(exc))
        check("详情页 about:blank -> 抛 M3WhiteScreenError", raised)
        check("详情页快速失败（远早于 25s 上限）", blank_detail.waited_ms <= 5000)
    finally:
        s4.TOP_BLANK_GRACE_MS = saved_grace

    # 4. 导航失败重试：transient 错误 + 顶层停在 about:blank 时，应快速抛 M3WhiteScreenError，
    #    且退避期间不再 goto("about:blank") 长时间停留，也不会叠加 20/45 秒长退避。
    saved_after = s4.NAV_BLANK_RESTART_AFTER_ATTEMPTS
    s4.NAV_BLANK_RESTART_AFTER_ATTEMPTS = 2
    try:
        nav = FakeNavPage("net::ERR_CONNECTION_CLOSED at https://oa.example.com/report")
        raised = False
        try:
            s4.goto_page(nav, "https://oa.example.com/seeyon/report4Result.do", "测试直达")
        except s4.M3WhiteScreenError:
            raised = True
        check("导航连续失败+顶层空白 -> 抛 M3WhiteScreenError", raised)
        check("重试期间未 goto about:blank", all(not s4.is_about_blank_url(u) for u in nav.goto_urls))
        check("重试期间调用过 window.stop", nav.stop_calls >= 1)
        check("只在阈值次尝试后即重启（goto 次数==阈值）", len(nav.goto_urls) == 2)
        # 退避只发生一次（第 1 次失败后的最短退避），未叠加第二/第三段长退避(20/45s)。
        check("退避未叠加长等待（<20s）", nav.waited_ms < 20000)
    finally:
        s4.NAV_BLANK_RESTART_AFTER_ATTEMPTS = saved_after

    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
