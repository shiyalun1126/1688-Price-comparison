# -*- coding: utf-8 -*-
"""截在谈名单本体 + 行内细节。"""
import asyncio
import sys
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8000/"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 1000})
        await page.goto(URL, wait_until="networkidle")
        await page.wait_for_timeout(1200)
        await page.click("#btn-switch")
        await page.wait_for_timeout(300)
        await page.locator("#switcher-options > *").first.click()
        await page.wait_for_timeout(2000)
        await page.locator("#zone-inquiry").scroll_into_view_if_needed()
        await page.wait_for_timeout(400)
        await page.click("#auto-toggle")
        await page.wait_for_timeout(900)
        await page.locator("#auto-bar").screenshot(path="docs/screenshots/auto-bar-only.png")
        await page.locator("#auto-pop").screenshot(path="docs/screenshots/auto-pop-only.png")
        await browser.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
