# -*- coding: utf-8 -*-
"""验证「在谈名单」：点状态条展开、行内打开对话。"""
import asyncio
import sys
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8000/"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(URL, wait_until="networkidle")
        await page.wait_for_timeout(1500)
        await page.click("#btn-switch")
        await page.wait_for_timeout(400)
        await page.locator("#switcher-options > *").first.click()
        await page.wait_for_timeout(2500)

        zone = page.locator("#zone-inquiry")
        await zone.scroll_into_view_if_needed()
        await page.wait_for_timeout(500)
        await page.screenshot(path="docs/screenshots/auto-bar-closed.png")

        await page.click("#auto-toggle")
        await page.wait_for_timeout(900)
        await page.screenshot(path="docs/screenshots/auto-bar-open.png")

        rows = await page.locator(".apl-row").count()
        print("rows:", rows)

        # 点第一行进抽屉
        await page.locator(".apl-row").first.click()
        await page.wait_for_timeout(1500)
        await page.screenshot(path="docs/screenshots/auto-bar-drawer.png")
        print("drawer on:", await page.locator("#session-drawer.on").count())
        print("pageerrors:", errors[:3])
        await browser.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
