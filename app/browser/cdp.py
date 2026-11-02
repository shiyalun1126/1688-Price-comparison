"""CDP 接管真实浏览器，抓取 1688 搜索结果。

设计要点：
- 通过 connect_over_cdp 接管用户已登录的真实浏览器（scripts/start_browser.py 启动），
  真实指纹 + 真实登录态，反爬几乎无感；只做只读抓取，不自动发消息不下单。
- 提取逻辑做多层兜底：优先已知卡片结构，退化为「/offer/ 锚点向上找含价格容器」的
  通用策略，1688 改版时仍大概率可用。
- 主图来自 alicdn 公开 CDN，服务端可直接下载，导入即有图、可直接视觉核验。
"""
from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx

CDP_URL = "http://127.0.0.1:9222"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# 环境有系统级 HTTP 代理，会把 127.0.0.1 的 CDP 请求也劫走（502）：
# 本地回环一律直连（httpx 用 trust_env=False，playwright 子进程用 NO_PROXY）
_NO_PROXY = os.environ.get("NO_PROXY", "")
if "127.0.0.1" not in _NO_PROXY:
    _joined = (_NO_PROXY + "," if _NO_PROXY else "") + "127.0.0.1,localhost"
    os.environ["NO_PROXY"] = _joined
    os.environ["no_proxy"] = _joined

# 提取搜索结果卡片的 JS（在浏览器页面里执行，多层兜底）
_EXTRACT_JS = r"""
() => {
  const seen = new Map();
  const priceOf = (t) => {
    const m = (t || '').match(/[¥￥]\s*([\d.]+)/);
    return m ? parseFloat(m[1]) : null;
  };
  document.querySelectorAll('a[href*="/offer/"]').forEach(a => {
    const m = (a.href || '').match(/offer\/(\d+)/);
    if (!m) return;
    const id = m[1];
    if (seen.has(id)) return;
    // 向上找卡片容器：第一个文本量足够且含价格符号的祖先
    let node = a, box = null;
    for (let i = 0; i < 7 && node; i++) {
      const t = node.innerText || '';
      if (t.length > 25 && /[¥￥]/.test(t)) { box = node; break; }
      node = node.parentElement;
    }
    const scope = box || a;
    const img = scope.querySelector('img');
    const title = (a.getAttribute('title') || (img && img.alt) ||
                   a.innerText || '').trim().replace(/\s+/g, ' ');
    if (!title || title.length < 4) return;
    // 店铺名：常见公司/店铺元素，兜底用文本行启发式
    const shopEl = scope.querySelector(
      '[class*="company"],[class*="shop-name"],[class*="seller"],[class*="companyName"]');
    let shop = shopEl ? (shopEl.innerText || '').trim().split('\n')[0] : '';
    if (!shop) {
      const lines = (scope.innerText || '').split('\n').map(s => s.trim())
        .filter(s => s.length >= 4 && s.length <= 30 &&
                     /(公司|厂|贸易|商行|经营部|工作室|百货|电商|制品|服饰)/.test(s));
      shop = lines[0] || '';
    }
    seen.set(id, {
      offer_id: id,
      title: title.slice(0, 80),
      price: priceOf(scope.innerText),
      shop: shop.slice(0, 40),
      image: img ? (img.src || '') : '',
      url: 'https://detail.1688.com/offer/' + id + '.html',
    });
  });
  return [...seen.values()];
}
"""


def is_available() -> bool:
    """CDP 浏览器是否在运行。"""
    try:
        r = httpx.get(f"{CDP_URL}/json/version", timeout=2, trust_env=False)
        return r.status_code == 200
    except Exception:
        return False


def search_offers(keywords: str, max_items: int = 24) -> dict:
    """在接管的浏览器里打开 1688 搜索页，提取候选商品卡片。

    返回 {items: [...], login_required: bool, page_title: str}。
    """
    from playwright.sync_api import sync_playwright

    # 1688 老搜索接口按 GBK 解码 keywords，UTF-8 会被搜成乱码词（结果全噪声）
    url = ("https://s.1688.com/selloffer/offer_search.htm?keywords="
           + quote(keywords.strip()[:40].encode("gbk")))
    items: list[dict] = []
    login_required = False
    page_title = ""

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        try:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(3.0)   # 等卡片异步渲染
                page_title = page.title()
                # 登录墙检测
                cur = page.url
                if "login" in cur or "signin" in cur:
                    login_required = True
                else:
                    body = page.inner_text("body")[:3000]
                    if ("扫码登录" in body or "请登录" in body[:200]) and len(items) == 0:
                        login_required = True
                if not login_required:
                    # 滚动一屏触发懒加载图片
                    try:
                        page.mouse.wheel(0, 2400)
                        time.sleep(1.5)
                    except Exception:
                        pass
                    raw = page.evaluate(_EXTRACT_JS)
                    items = raw[:max_items]
            finally:
                page.close()
        finally:
            browser.close()

    return {"items": items, "login_required": login_required, "page_title": page_title}


def download_image(img_url: str, upload_dir: Path) -> str | None:
    """下载 alicdn 主图到 uploads 目录，返回 /uploads/xxx 相对路径；失败返回 None。"""
    if not img_url or not img_url.startswith("http"):
        return None
    # //cbu01.alicdn.com/... 协议相对地址
    if img_url.startswith("//"):
        img_url = "https:" + img_url
    try:
        r = httpx.get(img_url, headers={"User-Agent": _UA, "Referer": "https://www.1688.com/"},
                      timeout=12.0, follow_redirects=True, trust_env=False)
        if r.status_code != 200 or len(r.content) < 2000:
            return None
        ctype = (r.headers.get("content-type") or "").lower()
        ext = ".jpg"
        if "png" in ctype:
            ext = ".png"
        elif "webp" in ctype:
            ext = ".webp"
        name = uuid.uuid4().hex + ext
        (upload_dir / name).write_bytes(r.content)
        return f"/uploads/{name}"
    except Exception:
        return None


_OFFER_RE = re.compile(r"offer/(\d+)")


def normalize_items(raw: list[dict], max_items: int = 24) -> list[dict]:
    """清洗提取结果：去空、规范字段、限量（过滤后再截断，避免有效项被截掉）。"""
    out = []
    for it in (raw or []):
        if len(out) >= max_items:
            break
        if not isinstance(it, dict) or not it.get("offer_id"):
            continue
        out.append({
            "offer_id": str(it["offer_id"]),
            "title": str(it.get("title") or "").strip()[:80],
            "price": it.get("price") if isinstance(it.get("price"), (int, float)) else None,
            "shop": str(it.get("shop") or "").strip()[:40],
            "image": str(it.get("image") or ""),
            "url": f"https://detail.1688.com/offer/{it['offer_id']}.html",
        })
    return out
