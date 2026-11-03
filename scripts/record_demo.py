"""录制两支演示视频：手动书签导入版 + 智能体版（Playwright 真实录屏）"""
import asyncio
import shutil
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8000"
OUT = Path("app/web/static/videos")
TMP = Path("data/_demo_raw")
VIEW = {"width": 1440, "height": 900}
REC = {"width": 1280, "height": 720}


async def new_ctx(browser, name):
    return await browser.new_context(
        viewport=VIEW, record_video_dir=str(TMP), record_video_size=REC
    ), name


async def wait_match_done(page, timeout_s=420):
    """等待视觉匹配完成（进度文本出现「完成」或进度条隐藏）"""
    for _ in range(timeout_s * 2):
        txt = await page.evaluate(
            "() => document.getElementById('progress-text')?.textContent || ''"
        )
        if "完成" in txt:
            return txt
        await page.wait_for_timeout(500)
    return "TIMEOUT"


async def wait_agent_done(page, timeout_s=600):
    """等待智能体运行结束：分析按钮从禁用恢复"""
    for _ in range(timeout_s * 2):
        done = await page.evaluate(
            "() => !document.getElementById('btn-agent').disabled"
        )
        if done:
            return True
        await page.wait_for_timeout(500)
    return False


async def record_manual(browser):
    """视频一：手动书签导入全流程（Playwright 路由劫持模拟 s.1688.com，书签脚本原样真实运行）"""
    ctx, _ = await new_ctx(browser, "manual")
    page = await ctx.new_page()
    dialogs = []

    def on_dialog(d):
        dialogs.append(d.message)
        asyncio.ensure_future(d.accept())

    page.on("dialog", on_dialog)

    mock_html = Path("app/web/static/demo1688.html").read_text(encoding="utf-8")
    # mock 页内相对资源改指到本地真实服务
    mock_html = mock_html.replace('src="/static/demo/', 'src="http://127.0.0.1:8000/static/demo/')

    # mock 页上的一切请求都由路由层应答（不进浏览器网络栈，绕开 Chrome 对
    # 公共源→本机地址的私有网络访问限制），API 经 httpx 转发到真实服务
    client = httpx.AsyncClient(base_url=BASE, timeout=httpx.Timeout(600.0))

    async def proxy_api(route):
        req = route.request
        path = "/" + req.url.split("//", 1)[1].split("/", 1)[1]
        if req.method == "OPTIONS":
            await route.fulfill(status=204, headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "*",
                "Access-Control-Allow-Headers": "*",
            })
            return
        try:
            r = await client.request(
                req.method, path, content=req.post_data,
                headers={"content-type": req.headers.get("content-type", "application/json")})
            await route.fulfill(status=r.status_code, body=r.content, headers={
                "content-type": r.headers.get("content-type", "application/json"),
                "Access-Control-Allow-Origin": "*",
            })
        except Exception as e:  # noqa: BLE001
            await route.fulfill(status=502, body=str(e))

    async def fake_1688(route):
        url = route.request.url
        req = route.request
        # 纯按请求 URL 判断（导航瞬间 page.url 尚未更新，不能依赖它）
        if "/api/" in url and ("s.1688.com" in url or "127.0.0.1:8000" in url):
            await proxy_api(route)
        elif "/static/demo/" in url and "127.0.0.1:8000" in url:
            f = Path("app/web/static/demo") / url.rsplit("/", 1)[1]
            if f.exists():
                await route.fulfill(body=f.read_bytes(), content_type="image/jpeg")
            else:
                await route.fulfill(status=404, body="not found")
        elif "s.1688.com" in url and req.resource_type == "document":
            # 注意：s.1688.com 有 HSTS 预加载，Chromium 会把 http 强制升级为 https
            await route.fulfill(body=mock_html, content_type="text/html; charset=utf-8")
        else:
            await route.continue_()

    await ctx.route("**/*", fake_1688)

    # 1. 工作台全貌
    await page.goto(BASE + "/", wait_until="networkidle")
    await page.wait_for_timeout(3500)

    # 2. 导入书签页：拿到书签代码
    await page.evaluate("showTab('import')")
    await page.wait_for_timeout(2500)
    bm = await page.evaluate("() => document.getElementById('bm-btn').href")
    print("书签代码长度:", len(bm))

    # 3. 访问"1688 搜索页"（被劫持为 mock 内容，hostname 真实）
    await page.goto("http://s.1688.com/s?kw=%E8%93%9D%E7%99%BD%E6%A0%BC%E8%A1%AC%E8%A1%AB",
                    wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)

    # 4. 运行书签（与真实书签行为一致的 javascript: 执行）
    await page.evaluate("(bm) => { window.location.href = bm; }", bm)
    for _ in range(30):  # 等书签脚本完成导入（最多 15 秒）
        if dialogs:
            break
        await page.wait_for_timeout(500)
    print("书签弹窗:", dialogs)
    await page.wait_for_timeout(3000)

    # 5. 回工作台执行视觉匹配
    await page.goto(BASE + "/", wait_until="networkidle")
    await page.wait_for_timeout(2500)
    await page.click("#btn-match")
    result = await wait_match_done(page, timeout_s=600)
    print("匹配完成:", result)
    await page.wait_for_timeout(4000)  # 让分层结果停留展示

    # 6. 生成比价结论（强制重算以纳入新导入商品）
    await page.evaluate("runComparison(true)")
    for _ in range(240):
        has = await page.evaluate(
            "() => !!document.querySelector('#result table tbody tr')"
        )
        if has:
            break
        await page.wait_for_timeout(500)
    await page.evaluate("document.getElementById('zone-result')?.scrollIntoView()")
    await page.wait_for_timeout(4500)

    video = await page.video.path()
    await ctx.close()
    return video


async def record_agent(browser):
    """视频二：智能体自动分析全流程"""
    ctx, _ = await new_ctx(browser, "agent")
    page = await ctx.new_page()

    await page.goto(BASE + "/", wait_until="networkidle")
    await page.wait_for_timeout(3000)

    await page.fill(
        "#agent-goal",
        "查看当前目标款的情况，执行视觉匹配，然后生成比价结论，告诉我最低价同款和采购建议",
    )
    await page.wait_for_timeout(1200)
    await page.click("#btn-agent")

    done = await wait_agent_done(page)
    print("智能体运行结束:", done)
    await page.wait_for_timeout(3000)

    # 展示比价结论
    await page.evaluate("document.getElementById('zone-result')?.scrollIntoView()")
    await page.wait_for_timeout(5000)
    await page.evaluate("window.scrollTo({top:0, behavior:'smooth'})")
    await page.wait_for_timeout(3500)

    video = await page.video.path()
    await ctx.close()
    return video


async def main():
    TMP.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        v1 = await record_manual(browser)
        v2 = await record_agent(browser)
        await browser.close()

    for src, name in [(v1, "demo-manual"), (v2, "demo-agent")]:
        dst = OUT / f"{name}.webm"
        shutil.copy(src, dst)
        print("已保存:", dst)


asyncio.run(main())
