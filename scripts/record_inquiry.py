"""录制第三支演示视频：询价闭环（生成话术 → 标记发送 → 贴入卖家回复 → 自动解析报价 → 比价结论更新）"""
import asyncio
import shutil
import subprocess
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8000"
OUT = Path("app/web/static/videos")
TMP = Path("data/_demo_raw")
VIEW = {"width": 1440, "height": 900}
REC = {"width": 1280, "height": 720}

# prompt() 弹窗按消息内容分发应答
REPLY_TEXT = (
    "在的老板，这款我们有现货的，56元一件含税，200件起批，今天下单48小时内发货，"
    "1000件以上可以做到55元，量大价格再谈。"
)


def prompt_answer(message):
    if "拟采购数量" in message:
        return "500"
    if "卖家回复" in message:
        return REPLY_TEXT
    return ""


async def on_dialog(d):
    if d.type == "prompt":
        asyncio.ensure_future(d.accept(prompt_answer(d.message)))
    else:
        asyncio.ensure_future(d.accept())


async def wb_text(page):
    return await page.evaluate("() => document.getElementById('workbench')?.innerText || ''")


async def wait_wb_contains(page, keyword, timeout_s=20):
    for _ in range(timeout_s * 2):
        if keyword in await wb_text(page):
            return True
        await page.wait_for_timeout(500)
    print("等待超时:", keyword)
    return False


async def record_inquiry(browser):
    ctx = await browser.new_context(viewport=VIEW, record_video_dir=str(TMP),
                                    record_video_size=REC)
    await ctx.grant_permissions(["clipboard-read", "clipboard-write"])
    page = await ctx.new_page()
    page.on("dialog", lambda d: asyncio.ensure_future(on_dialog(d)))

    # 1. 工作台全貌
    await page.goto(BASE + "/", wait_until="networkidle")
    await page.wait_for_timeout(3500)

    # 2. 进入「04 询价工作台」
    await page.evaluate(
        "() => document.getElementById('zone-inquiry')?.scrollIntoView({block:'start'})"
    )
    await page.wait_for_timeout(2500)

    # 3. 先看「已报价」筛选：3 条已有报价（此前询价攒下的）
    await page.evaluate("inqFilter='quoted';renderInqChips();renderWorkbench()")
    await page.wait_for_timeout(3000)

    # 4. 回到全部，搜索目标店铺（话术已生成，从复制开始走完闭环）
    await page.evaluate("inqFilter='all';renderInqChips();renderWorkbench()")
    await page.fill("#inquiry-search", "普赛")
    await page.evaluate("renderWorkbench()")
    await page.wait_for_timeout(2500)

    # 5. 点开话术看一眼 → 复制（alert 自动确认）→ 标记已发送
    await page.evaluate(
        "() => { const d = document.querySelector('#workbench details.script-fold');"
        " if (d) d.open = true; }"
    )
    await page.wait_for_timeout(3500)

    await page.evaluate(
        "() => { const b = [...document.querySelectorAll('#workbench button')]"
        ".find(x => x.textContent.trim() === '复制'); if (b) b.click(); }"
    )
    await page.wait_for_timeout(1800)
    await page.evaluate(
        "() => { const b = [...document.querySelectorAll('#workbench button')]"
        ".find(x => x.textContent.includes('标记已发送')); if (b) b.click(); }"
    )
    await wait_wb_contains(page, "贴入回复")
    await page.wait_for_timeout(2000)

    # 8. 贴入卖家回复 → 真实解析器提取报价（alert 展示解析结果 → 自动确认）
    await page.evaluate(
        "() => { const b = [...document.querySelectorAll('#workbench button')]"
        ".find(x => x.textContent.includes('贴入回复')); if (b) b.click(); }"
    )
    await wait_wb_contains(page, "报价已到", timeout_s=30)
    await page.wait_for_timeout(3500)

    # 9. 清掉搜索，跳到比价结论，强制重算以纳入新报价
    await page.evaluate(
        "() => { const s = document.getElementById('inquiry-search');"
        " if (s) s.value = ''; renderWorkbench(); }"
    )
    await page.wait_for_timeout(1200)
    await page.evaluate(
        "() => document.getElementById('zone-result')?.scrollIntoView({block:'start'})"
    )
    await page.wait_for_timeout(2000)
    await page.evaluate("runComparison(true)")

    # 等综述更新为「询价报价 4 条」（新报价入库后重算完成）
    updated = False
    for _ in range(240):
        body = await page.evaluate(
            "() => document.getElementById('result')?.innerText || ''"
        )
        if "询价报价 4 条" in body:
            updated = True
            break
        await page.wait_for_timeout(500)
    print("比价重算完成:", updated)

    # 10. 展示比价结论：先定格综述（含「询价报价 4 条」），再居中新报价行
    await page.wait_for_timeout(1500)
    await page.evaluate(
        "() => document.querySelector('#result .cmp-summary')?.scrollIntoView({block:'start'})"
    )
    await page.wait_for_timeout(5000)
    await page.evaluate(
        "() => { const tr = [...document.querySelectorAll('#result table tbody tr')]"
        ".find(r => r.innerText.includes('普赛'));"
        " if (tr) tr.scrollIntoView({block:'center'}); }"
    )
    await page.wait_for_timeout(5500)

    video = await page.video.path()
    await ctx.close()
    return video


async def main():
    TMP.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        v = await record_inquiry(browser)
        await browser.close()

    raw = OUT / "demo-inquiry.webm"
    shutil.copy(v, raw)
    print("原始录制:", raw)

    # 转 mp4（与另外两支格式一致）
    dst = OUT / "demo-inquiry.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(raw),
        "-c:v", "libx264", "-preset", "fast", "-crf", "26",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst),
    ], check=True)
    raw.unlink()
    print("已保存:", dst, dst.stat().st_size, "bytes")


asyncio.run(main())
