# -*- coding: utf-8 -*-
"""刷新 README 用到的 5 张主图，保证与当前界面一致。

选目标款时挑「有候选商品」的那一个，否则询价工作台是空的，截出来没法看。
"""
import asyncio
import sys
from playwright.async_api import async_playwright

URL = "http://127.0.0.1:8000/"
OUT = "docs/screenshots"


async def pick_target_with_products(page):
    await page.click("#btn-switch")
    await page.wait_for_timeout(400)
    opts = page.locator("#switcher-options > *")
    n = await opts.count()
    for i in range(n):
        await opts.nth(i).click()
        await page.wait_for_timeout(2000)
        cnt = await page.evaluate("document.getElementById('stat-products')?.textContent || '0'")
        if cnt and cnt.isdigit() and int(cnt) > 0:
            print("picked target with products:", cnt)
            return True
        await page.click("#btn-switch")
        await page.wait_for_timeout(300)
    print("no target with products")
    return False


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1600, "height": 1000})
        await page.goto(URL, wait_until="networkidle")
        await page.wait_for_timeout(1500)
        await pick_target_with_products(page)

        # 1) 工作台总览（把智能体控制台展开，首图才看得出有智能体在）
        await page.evaluate("window.scrollTo(0,0)")
        await page.wait_for_timeout(600)
        try:
            await page.click(".console-spine")
            await page.wait_for_timeout(800)
        except Exception as e:
            print("console expand skipped:", e)
        await page.screenshot(path=f"{OUT}/dashboard-polo.png")
        try:
            await page.click(".console-spine")
            await page.wait_for_timeout(600)
        except Exception:
            pass

        # 2) 询价工作台
        await page.locator("#zone-inquiry").scroll_into_view_if_needed()
        await page.wait_for_timeout(800)
        await page.screenshot(path=f"{OUT}/inquiry-workbench.png")

        # 3) 在谈名单
        try:
            await page.click("#auto-toggle")
            await page.wait_for_timeout(1000)
            await page.locator("#auto-pop").screenshot(path=f"{OUT}/auto-negotiation-list.png")
        except Exception as e:
            print("list shot skipped:", e)

        # 4) 对话抽屉
        try:
            await page.locator(".apl-row").first.click()
            await page.wait_for_timeout(1800)
            await page.screenshot(path=f"{OUT}/auto-negotiation-chat.png")
            await page.click(".drawer-close, #session-drawer .close, #drawer-close")
            await page.wait_for_timeout(400)
        except Exception as e:
            print("chat shot skipped:", e)

        # 5) 比价结论
        try:
            await page.locator("#zone-result").scroll_into_view_if_needed()
            await page.wait_for_timeout(600)
            await page.screenshot(path=f"{OUT}/comparison-result.png")
        except Exception as e:
            print("comparison shot skipped:", e)
        await browser.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
