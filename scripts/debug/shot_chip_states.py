"""抓取浏览器状态胶囊的三态截图（就绪 / 未启动 / 未知）。

用来肉眼校验胶囊跟输入框的对齐、文字是否折行。三态里只有「就绪」是真实状态
（9222 正开着），另外两态靠路由拦截伪造，不碰外面的服务。
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[2] / "scripts" / "debug" / "shots"
URL = "http://127.0.0.1:8000/"


def shot(page, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    row = page.query_selector("#crawl-status")
    if row:
        row.scroll_into_view_if_needed()
    page.wait_for_timeout(400)
    box = page.query_selector("#zone-candidates")
    (box or page).screenshot(path=str(OUT / f"{name}.png"))
    print("saved", OUT / f"{name}.png")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        page.goto(URL, wait_until="networkidle")
        page.wait_for_timeout(1200)
        shot(page, "chip-ready")

        # 未启动：让探针明确回 false
        page.route("**/api/browser/status", lambda r: r.fulfill(
            status=200, content_type="application/json",
            body='{"ok":true,"ready":false,"start_cmd":"python scripts/start_browser.py --hold","hint":"x"}'))
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(900)
        shot(page, "chip-down")
        page.unroute("**/api/browser/status")

        # 未知：后端不通（请求直接失败）
        page.route("**/api/browser/status", lambda r: r.abort())
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(900)
        shot(page, "chip-unknown")
        page.unroute("**/api/browser/status")

        page.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
