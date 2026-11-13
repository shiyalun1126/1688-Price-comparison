# -*- coding: utf-8 -*-
"""验证侧栏「工作台」下拉：展开态、收起态、点子项跳转。"""
import asyncio
import sys
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8000/"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.goto(URL, wait_until="networkidle")
        await page.wait_for_timeout(1500)
        await page.screenshot(path="docs/screenshots/nav-drop-open.png")

        # 收起
        await page.click("#nav-home")
        await page.wait_for_timeout(400)
        await page.screenshot(path="docs/screenshots/nav-drop-closed.png")
        print("collapsed:", await page.get_attribute("#pipeline", "class"))

        # 再展开，点第 4 项跳转
        await page.click("#nav-home")
        await page.wait_for_timeout(300)
        await page.click('.pipe-step[data-zone="zone-inquiry"]')
        await page.wait_for_timeout(1200)
        await page.screenshot(path="docs/screenshots/nav-drop-jump.png")
        y = await page.evaluate("window.scrollY")
        print("scrollY after jump:", y)

        # 窄屏
        await page.set_viewport_size({"width": 820, "height": 900})
        await page.wait_for_timeout(500)
        await page.evaluate("window.scrollTo(0,0)")
        await page.wait_for_timeout(300)
        await page.screenshot(path="docs/screenshots/nav-drop-narrow.png")
        await browser.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
