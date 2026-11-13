# -*- coding: utf-8 -*-
"""截图检查：视觉匹配结果里「已排除 / 未核验」折叠后的版面。

不跑视觉模型（省配额），而是用库里真实候选商品拼出与 renderMatchResult
相同的 DOM 结构，专门看折叠条与卡片的样式、层级关系。
"""
import asyncio
import json
import sys

import httpx
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8000"
TARGET = "15c927722afc4625a1503174698d9e07"


async def main() -> int:
    async with httpx.AsyncClient(base_url=BASE, timeout=20) as c:
        prods = (await c.get(f"/api/targets/{TARGET}/products")).json()
        shops = {s["id"]: s["name"] for s in (await c.get("/api/shops")).json()}

    exact = [p for p in prods if p.get("match_tier") == "exact"]
    equiv = [p for p in prods if p.get("match_tier") == "equivalent"]
    rejected = [p for p in prods if p.get("match_status") == "rejected"]

    def card(p: dict, cls: str, status: str) -> str:
        img = p["image_paths"][0] if p.get("image_paths") else ""
        price = (f'<span class="price">¥{p["listed_price"]:.2f}</span>'
                 if p.get("listed_price") else '<span class="muted">未抓到价</span>')
        return (f'<div class="match-card">'
                f'{f"<img src={chr(34)}{img}{chr(34)} alt={chr(34)}{chr(34)}>" if img else ""}'
                f'<div class="info"><div class="shop">{shops.get(p["shop_id"], "")}{price}</div>'
                f'<div class="tag {cls}">{status}</div>'
                f'<div class="reason">属性与目标款一致，可直接比价</div></div></div>')

    html = '<div class="match-col"><h3>完全同款 <span class="n">%d</span></h3>%s</div>' % (
        len(exact), "".join(card(p, "exact", "完全同款") for p in exact))
    html += '<div class="match-col"><h3>等效替代 <span class="n">%d</span></h3>%s</div>' % (
        len(equiv), "".join(card(p, "equiv", "等效替代") for p in equiv))
    html += ('<details class="match-col fold"><summary><h3>已排除 <span class="n">%d</span>'
             '<span class="fold-hint">· 都已判为不是这款，点开可复核</span></h3></summary>'
             '<div class="cards">%s</div></details>') % (
        len(rejected), "".join(card(p, "other", "已排除") for p in rejected))
    html += ('<details class="match-col fold"><summary><h3>未核验候选 <span class="n">%d</span>'
             '<span class="fold-hint">· 还没判定，点开查看</span></h3></summary>'
             '<div class="cards">%s</div></details>') % (
        6, "".join(card(p, "other", "未核验") for p in rejected[:6]))

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.goto(BASE + "/", wait_until="networkidle")
        await page.wait_for_timeout(800)
        await page.evaluate("h => { document.getElementById('result').innerHTML = h; }", html)
        await page.wait_for_timeout(400)
        el = page.locator("#zone-result")
        await el.screenshot(path="docs/screenshots/match-fold-collapsed.png")

        await page.evaluate(
            "() => document.querySelectorAll('#result details').forEach(d => d.open = true)")
        await page.wait_for_timeout(300)
        first = page.locator("#result details").first
        await first.scroll_into_view_if_needed()
        await page.wait_for_timeout(200)
        await first.screenshot(path="docs/screenshots/match-fold-open.png")
        await page.screenshot(path="docs/screenshots/match-fold-open-page.png", full_page=False)
        print(json.dumps({"exact": len(exact), "equivalent": len(equiv),
                          "rejected": len(rejected)}, ensure_ascii=False))
        await browser.close()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main()))
