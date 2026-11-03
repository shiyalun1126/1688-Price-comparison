# -*- coding: utf-8 -*-
"""截图验证：比价结论的时效徽章 + 归一价 + 重算按钮。"""
import asyncio
import sys
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8000/"
OUT = "docs/screenshots/freshness-norm.png"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1600, "height": 1000})
        await page.goto(URL, wait_until="networkidle")
        await page.wait_for_timeout(1200)
        # 打开右上角切款下拉，选第一个目标款
        await page.click("#btn-switch")
        await page.wait_for_timeout(400)
        await page.locator("#switcher-options > *").first.click()
        await page.wait_for_timeout(2500)  # 等缓存比价回显
        # 滚动到比价结论区
        result = page.locator("#result")
        await result.scroll_into_view_if_needed()
        await page.wait_for_timeout(500)
        await page.screenshot(path=OUT, full_page=False)
        print("saved:", OUT)
        await browser.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
