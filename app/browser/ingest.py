"""把 1688 搜索结果落库成候选商品。

智能体工具（agent/tools.py:auto_import）和 HTTP 接口（api/routes.py:crawl_target）
共用这一条路，避免入库口径两处漂移——店铺去重、图片落盘、id 规则都必须一致，
否则同一家店会被建两次、同一件商品会被重复计入候选。
"""
from __future__ import annotations

import uuid
from pathlib import Path

from app.browser import cdp
from app.models.entities import Product, Shop
from app.models.enums import MatchStatus

UPLOAD_DIR = Path(__file__).resolve().parents[2] / "data" / "uploads"


def crawl_into_target(store, target, keywords: str, max_items: int = 20) -> dict:
    """搜索关键词并把结果写成 target 下的候选商品。

    返回统一结构：ok / imported / skipped / images / shops / error。
    失败一律走 ok=False + error，调用方不用猜；error 里写清楚下一步该做什么
    （浏览器没起、没登录、页面改版），因为这三种的处理方式完全不同。
    """
    keywords = (keywords or "").strip()
    if not keywords:
        return {"ok": False, "error": "关键词不能为空"}
    if target is None:
        return {"ok": False, "error": "目标款不存在"}

    max_items = max(1, min(int(max_items or 20), 40))
    if not cdp.is_available():
        return {
            "ok": False,
            "error": "抓取浏览器未运行。先在项目里执行 scripts/start_browser.py，"
                     "首次需在新窗口扫码登录 1688，之后登录态会记住。",
            "hint": "browser_down",
        }

    try:
        raw = cdp.search_offers(keywords, max_items=max_items)
    except Exception as e:
        return {"ok": False, "error": f"浏览器抓取失败：{type(e).__name__}: {e}"}

    if raw.get("login_required"):
        return {
            "ok": False,
            "error": "抓取浏览器未登录 1688。请在抓取浏览器窗口扫码登录一次，然后重试。",
            "hint": "login_required",
        }

    items = cdp.normalize_items(raw.get("items"), max_items)
    if not items:
        return {
            "ok": False,
            "error": "搜索页没提取到商品卡片（关键词太偏或页面改版）。换个关键词试试。",
            "hint": "no_items",
        }

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    shops_by_name = {s.name: s.id for s in store.list_shops()}
    imported, skipped, img_ok, shops_touched = 0, 0, 0, set()

    for it in items:
        pid = f"auto-{it['offer_id']}"
        if store.get_product(pid):
            skipped += 1
            continue
        shop_name = it["shop"] or "1688搜索（卖家未记录）"
        if shop_name in shops_by_name:
            sid = shops_by_name[shop_name]
        else:
            sid = uuid.uuid4().hex
            store.save_shop(Shop(id=sid, name=shop_name))
            shops_by_name[shop_name] = sid
        image_paths: list[str] = []
        local = cdp.download_image(it["image"], UPLOAD_DIR) if it["image"] else None
        if local:
            # 存绝对路径，与书签导入口径一致（matcher 只认本地文件路径）
            image_paths = [str(UPLOAD_DIR / Path(local).name)]
            img_ok += 1
        store.save_product(Product(
            id=pid, target_style_id=target.id, shop_id=sid,
            title=it["title"], url=it["url"], listed_price=it["price"],
            image_paths=image_paths,
            match_status=MatchStatus.CANDIDATE,
            match_reason="浏览器搜索导入（真实店铺）",
        ))
        shops_touched.add(shop_name)
        imported += 1

    return {
        "ok": True,
        "imported": imported,
        "skipped": skipped,
        "images": img_ok,
        "shops": len(shops_touched),
        "keywords": keywords,
        "error": None,
    }
