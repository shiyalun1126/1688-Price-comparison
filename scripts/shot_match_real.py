"""用库里真实的匹配结果重绘「匹配与比价」区并截图。

不重新跑视觉模型（省钱省配额），只把已入库的 match_status / match_reason
拼成 runMatch 的返回结构，调 renderMatchResult 画出来。

    python scripts/shot_match_real.py "渔夫帽"
    python scripts/shot_match_real.py "渔夫帽" --open   # 展开折叠区一起截
"""
from __future__ import annotations

import argparse
import asyncio
import json
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
OUT = Path("docs/screenshots")

STATUS_CN = {
    "verified_same": "完全同款",
    "verified_equivalent": "等效替代",
    "rejected": "已排除",
    "candidate": "未核验",
}


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def pick_target(keyword: str) -> dict:
    for t in get("/api/targets"):
        if keyword in t["name"]:
            return t
    raise SystemExit(f"没找到目标款：{keyword}")


def build_groups(target_id: str) -> dict:
    shops = {s["id"]: s.get("name", "") for s in get("/api/shops")}
    g = {"exact": [], "equivalent": [], "rejected": [], "candidate": []}
    for p in get(f"/api/targets/{target_id}/products"):
        st = p.get("match_status")
        key = {"verified_same": "exact", "verified_equivalent": "equivalent",
               "rejected": "rejected"}.get(st, "candidate")
        g[key].append({
            "product_id": p["id"],
            "product": p,
            "shop_name": shops.get(p.get("shop_id"), "") or (p.get("title") or "")[:18],
            "status": STATUS_CN.get(st, st or "未核验"),
            "reason": p.get("match_reason") or "",
        })
    return g


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("keyword")
    ap.add_argument("--open", action="store_true", help="展开折叠区")
    args = ap.parse_args()

    target = pick_target(args.keyword)
    groups = build_groups(target["id"])
    counts = {k: len(v) for k, v in groups.items()}
    print("分组:", counts)

    from playwright.async_api import async_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    suffix = "open" if args.open else "collapsed"

    async with async_playwright() as p:
        b = await p.chromium.launch()
        page = await b.new_page(viewport={"width": 1440, "height": 1000})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        await page.goto(BASE, wait_until="networkidle")
        await page.wait_for_timeout(900)

        # 选中目标款并注入真实结果，走前端自己的渲染函数
        await page.evaluate(
            """(tid) => {
                if (typeof selectTarget === 'function') selectTarget(tid);
                else currentTargetId = tid;
            }""",
            target["id"],
        )
        await page.wait_for_timeout(1200)

        # 页面加载/切款时会自动拉一次比价结论，和匹配结果共用 #result，晚到的会盖掉先画的
        for _ in range(3):
            await page.evaluate("(groups) => renderMatchResult(groups)", groups)
            await page.wait_for_timeout(600)
            if await page.evaluate("() => !!document.querySelector('#result .match-col')"):
                break
        await page.wait_for_timeout(300)
        print("has_match_col:", await page.evaluate(
            "() => !!document.querySelector('#result .match-col')"),
            "| details:", await page.evaluate(
            "() => document.querySelectorAll('#result details').length"))

        zone = page.locator("#result")
        await zone.scroll_into_view_if_needed()
        await page.wait_for_timeout(200)

        if args.open:
            await page.evaluate(
                "() => document.querySelectorAll('#result details').forEach(d => d.open = true)")
            await page.wait_for_timeout(400)

        await zone.screenshot(path=str(OUT / f"match-real-{suffix}.png"))
        await page.screenshot(path=str(OUT / f"match-real-{suffix}-page.png"))
        print("errors:", errs[:3])
        print("saved:", OUT / f"match-real-{suffix}.png")
        await b.close()


if __name__ == "__main__":
    asyncio.run(main())
