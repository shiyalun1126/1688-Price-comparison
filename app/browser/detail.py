"""打开 1688 商品详情页，把「页面上已经写明的东西」抓成结构化事实。

为什么单独一层：详情页是买家自己就能看到的信息，智能体却拿它当缺口去问卖家
（「什么价」「起订量多少」「运费怎么算」），卖家一句「页面上都有」就把对话聊死，
还白烧一轮询价额度。抓一次，能少问三四个问题。

规则：
- 只读，不点「立即下单」、不点客服；
- 命中滑块验证/登录墙 → 返回失败原因，由调用方退回「没有预填」的正常流程，
  绝不硬闯；
- 页面文本拿不到就返回 None，宁可少预填，也不许猜价格。
"""
from __future__ import annotations

from typing import Optional

from app.inquire.detail_facts import DetailFacts, parse_detail_text

CDP_URL = "http://127.0.0.1:9222"

_CAPTCHA_HINTS = ("拖动滑块", "请完成验证", "滑动验证", "验证码", "安全验证")
_LOGIN_HINTS = ("扫码登录", "请登录后", "密码登录")


def _looks_blocked(text: str) -> str:
    head = (text or "")[:800]
    for w in _CAPTCHA_HINTS:
        if w in head:
            return f"详情页出现验证（{w}），已跳过预填，交人工"
    for w in _LOGIN_HINTS:
        if w in head:
            return f"详情页要求登录（{w}），已跳过预填"
    return ""


def fetch_offer_facts(url: str, *, wait_ms: int = 4500) -> tuple[Optional[DetailFacts], str]:
    """抓取详情页公开信息。返回 (facts, 失败原因)；成功时原因串为空。"""
    if not (url or "").startswith("http"):
        return None, "没有商品链接"
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:                     # 没装 playwright 不影响主流程
        return None, f"playwright 不可用：{e}"

    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            try:
                ctx = browser.contexts[0] if browser.contexts else browser.new_context()
                page = ctx.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    # 价格/阶梯价是异步渲染的，等到出现 ¥ 或超时
                    try:
                        page.wait_for_function(
                            "() => /[¥￥]/.test(document.body.innerText || '')",
                            timeout=wait_ms)
                    except Exception:
                        page.wait_for_timeout(wait_ms)
                    # 往下滚一点，触发规格/运费/库存区域
                    try:
                        page.mouse.wheel(0, 900)
                        page.wait_for_timeout(1200)
                    except Exception:
                        pass
                    text = page.inner_text("body")
                finally:
                    page.close()
            finally:
                browser.close()
    except Exception as e:
        return None, f"打开详情页失败：{type(e).__name__}: {e}"

    blocked = _looks_blocked(text)
    if blocked:
        return None, blocked
    facts = parse_detail_text(text, url)
    if not facts.has_any():
        return None, "详情页没读到价格/起订等信息（可能改版或未渲染完）"
    return facts, ""
