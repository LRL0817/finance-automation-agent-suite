# -*- coding: utf-8 -*-
"""
辅助脚本：使用系统默认浏览器打开中国农业银行官网首页。

注意：
- 这只是一个便捷入口，不做任何登录或自动化操作。
- 单笔转账完整流程的主入口是同目录下的 `open_browser.py`。
"""
from __future__ import annotations

import sys
import webbrowser

ABC_HOME_URL = "https://www.abchina.com.cn/cn/"


def open_abc_home(url: str = ABC_HOME_URL) -> bool:
    """在系统默认浏览器中打开 ABC 官网首页。返回是否成功调起。"""
    print(f"正在打开: {url}")
    return webbrowser.open(url)


def main() -> int:
    ok = open_abc_home()
    if not ok:
        print("无法调起系统默认浏览器，请手动访问上面的链接。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
