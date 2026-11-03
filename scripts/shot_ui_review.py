# -*- coding: utf-8 -*-
"""全页截图：用于 UI 评估（目标款区 / 候选商品区 / 匹配与比价 / 询价工作台）。"""
import asyncio
import sys
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8000/"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1600, "height": 1000})
        await page.goto(URL, wait_until="networkidle")
        await page.wait_for_timeout(1200)
        # 选第一个目标款
        await page.click("#btn-switch")
        await page.wait_for_timeout(400)
        await page.locator("#switcher-options > *").first.click()
        await page.wait_for_timeout(2500)
        await page.screenshot(path="docs/screenshots/ui-full.png", full_page=True)
        print("saved full page")
        await browser.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
