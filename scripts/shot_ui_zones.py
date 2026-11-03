# -*- coding: utf-8 -*-
"""分区截图：目标款/候选商品区、比价表、询价工作台。"""
import asyncio
import sys
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8000/"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1600, "height": 1050})
        await page.goto(URL, wait_until="networkidle")
        await page.wait_for_timeout(1200)
        await page.click("#btn-switch")
        await page.wait_for_timeout(400)
        await page.locator("#switcher-options > *").first.click()
        await page.wait_for_timeout(2500)

        for zone, out in [("zone-target", "ui-zone-target.png"),
                          ("zone-candidates", "ui-zone-candidates.png"),
                          ("zone-result", "ui-zone-result.png"),
                          ("zone-inquiry", "ui-zone-inquiry.png")]:
            el = page.locator(f"#{zone}")
            try:
                await el.scroll_into_view_if_needed()
                await page.wait_for_timeout(400)
                await page.screenshot(path=f"docs/screenshots/{out}")
                print("saved", out)
            except Exception as e:
                print("skip", zone, e)
        await browser.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
