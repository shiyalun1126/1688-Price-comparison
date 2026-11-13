"""验证「后台有新消息 → 页面自动更新」，全程不刷新页面。

做法：拦截列表接口，在返回体里把某个会话的轮次改大、last_msg_at 改成新时间，
模拟卖家回话后引擎推进的状态。然后什么都不做，看 5 秒一轮的轮询有没有把它取回来：
行的进度文字变了、行闪了一下、右下角弹出提示。

不写数据库、不真发消息，只验证前端这条链路。

    python scripts/verify_live_poll.py
"""
from __future__ import annotations

import asyncio
import json
import urllib.request

BASE = "http://127.0.0.1:8000"
TARGET_NAME = "冰裂纹喷雾水杯"


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def pick_target() -> dict:
    for t in get("/api/targets"):
        if TARGET_NAME in t["name"]:
            return t
    raise SystemExit(f"没找到目标款：{TARGET_NAME}")


async def main() -> None:
    from playwright.async_api import async_playwright

    target = pick_target()
    rows = get(f"/api/targets/{target['id']}/inquiries")
    auto = [r for r in rows if r.get("session_id")]
    if not auto:
        raise SystemExit("这个目标款没有自动会话，换个目标款再验")
    victim = auto[0]
    sid = victim["session_id"]
    print(f"目标款：{target['name']}")
    print(f"观察对象：{victim['shop_name']} · 第 {victim.get('round')} 轮 · {victim['status']}")

    state = {"patched": False, "n": 1}

    async with async_playwright() as p:
        b = await p.chromium.launch()
        page = await b.new_page(viewport={"width": 1440, "height": 900})
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))

        async def inflate(route):
            try:
                resp = await route.fetch()
            except Exception:
                await route.abort()      # 关页面时还有请求在飞，直接放掉
                return
            body = await resp.json()
            if state["patched"] and isinstance(body, list):
                for q in body:
                    if q.get("session_id") == sid:
                        q["round"] = (q.get("round") or 0) + state["n"]
                        q["last_msg_at"] = f"2026-11-06T15:59:0{state['n'] % 9}"
                        q["status"] = "negotiating"     # 卖家回话后引擎进入的态
                state["n"] += 1        # 每次请求都往前推一步，模拟连续的对话
            await route.fulfill(
                response=resp,
                body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers={**resp.headers, "content-type": "application/json"},
            )

        await page.route(f"**/api/targets/{target['id']}/inquiries", inflate)
        await page.goto(BASE, wait_until="networkidle")
        await page.evaluate("(tid) => selectTarget(tid)", target["id"])
        await page.wait_for_timeout(2500)

        def row_text() -> str:
            return page.evaluate(
                """(prod) => {
                    const tr = document.querySelector(`tr[data-inq="${prod}"]`);
                    return tr ? tr.innerText.replace(/\\s+/g, ' ') : '(没有这一行)';
                }""",
                victim["product_id"],
            )

        before = await row_text()
        print("改前：", before[:110])

        state["patched"] = True          # 之后每次轮询拿到的都是「有新消息」的版本
        await page.wait_for_timeout(7000)  # 5 秒一轮，留足一轮

        after = await row_text()
        print("改后：", after[:110])
        flashed = await page.evaluate(
            """(prod) => {
                const tr = document.querySelector(`tr[data-inq="${prod}"]`);
                return !!tr && tr.classList.contains('row-flash');
            }""",
            victim["product_id"],
        )
        toasts = await page.evaluate(
            "() => [...document.querySelectorAll('.inq-toast-item')].map(e => e.innerText)")
        timer_on = await page.evaluate("() => !!liveTimer")

        await page.screenshot(path="docs/screenshots/live-poll.png")

        # 再验一次「切到后台标签页」：不该弹窗，但标题上要有未读
        await page.evaluate(
            """() => {
                Object.defineProperty(document, 'hidden', {get: () => true, configurable: true});
                document.dispatchEvent(new Event('visibilitychange'));
            }""")
        await page.wait_for_timeout(12000)     # 后台降频到 10 秒一轮
        title = await page.title()
        unread = await page.evaluate("() => unreadCount")
        await page.evaluate(
            """() => {
                Object.defineProperty(document, 'hidden', {get: () => false, configurable: true});
                document.dispatchEvent(new Event('visibilitychange'));
            }""")

        print("轮询在跑：", timer_on)
        print("行有闪烁标记：", flashed)
        print("前台提示：", toasts)
        print(f"后台未读：{unread} 条 · 标题「{title}」")
        print("控制台报错：", errs[:3])
        changed = before != after
        ok = timer_on and changed and flashed and toasts and unread > 0
        print("\n结论：", "通过——页面自己更新了，切到后台也照跟，不需要刷新"
              if ok else "未通过，需要排查")
        await b.close()


if __name__ == "__main__":
    asyncio.run(main())
