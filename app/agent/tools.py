"""把比价业务能力包装成 Agent 可调用的工具集。"""
from __future__ import annotations

import re
from urllib.parse import quote

import httpx

from app.agent.react import ToolSpec
from app.comparison.comparison import run_comparison
from app.inquire.inquiry import build_inquiry_script
from app.matcher.matcher import Matcher
from app.models.entities import Product, Shop, TargetStyle
from app.models.enums import MatchStatus
from app.store.db import Store

# 1688 详情页 offer id 提取（detail.1688.com/offer/<id>.html）
_OFFER_ID_RE = re.compile(r"offer/(\d+)")
_FETCH_TIMEOUT = 10.0
_FETCH_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def build_tools(store: Store, current_target_id: str | None = None) -> list[ToolSpec]:
    """构建工具集，绑定到具体的数据存储。current_target_id 为会话选中的目标款。"""

    def _current_target() -> TargetStyle | None:
        if not current_target_id:
            return None
        return store.get_target_style(current_target_id)

    def list_targets(limit: int = 10) -> dict:
        """列出已有的目标款及商品数。"""
        out = []
        for t in store.list_target_styles()[:limit]:
            prods = store.list_products(t.id)
            with_img = sum(1 for p in prods if p.image_paths)
            with_price = sum(1 for p in prods if p.listed_price is not None)
            out.append({
                "target_id": t.id,
                "name": t.name,
                "category": t.category,
                "商品数": len(prods),
                "有图商品": with_img,
                "有价商品": with_price,
                "参考价": t.reference_price,
            })
        return {"targets": out, "count": len(out)}

    def inspect_target(target_id: str) -> dict:
        """查看某个目标款的详细信息（图片、属性、商品概况）。"""
        t = store.get_target_style(target_id)
        if not t:
            return {"error": "目标款不存在"}
        prods = store.list_products(target_id)
        status_count: dict[str, int] = {}
        for p in prods:
            k = p.match_status.value
            status_count[k] = status_count.get(k, 0) + 1
        return {
            "target_id": t.id,
            "name": t.name,
            "category": t.category,
            "description": t.description,
            "attributes": t.attributes,
            "参考价": t.reference_price,
            "商品总数": len(prods),
            "匹配状态分布": status_count,
            "样例商品": [
                {
                    "id": p.id,
                    "title": p.title[:40],
                    "标价": p.listed_price,
                    "有图": bool(p.image_paths),
                }
                for p in prods[:5]
            ],
        }

    def match_products(target_id: str) -> dict:
        """对目标款的候选商品执行视觉匹配（增量，已判定的会复用）。"""
        t = store.get_target_style(target_id)
        if not t:
            return {"error": "目标款不存在"}
        prods = store.list_products(target_id)
        if not prods:
            return {"error": "该目标款下没有候选商品，请先导入"}
        matcher = Matcher()
        cls = matcher.run(t, prods, max_workers=8)
        # 回写
        from datetime import datetime
        pmap = {p.id: p for p in prods}
        now = datetime.now()
        for m in cls.exact + cls.equivalent + cls.rejected + cls.candidate:
            p = pmap.get(m.product_id)
            if p:
                p.match_status = m.status
                p.match_tier = m.tier
                p.match_score = m.confidence
                p.match_reason = m.reason
                p.matched_at = now
                store.save_product(p)
        return {
            "完全同款": len(cls.exact),
            "等效替代": len(cls.equivalent),
            "已排除": len(cls.rejected),
            "未核验": len(cls.candidate),
            "同款样例": [
                {"店铺": m.reason[:60], "状态": m.status.value} for m in cls.exact[:3]
            ],
        }

    def compare_prices(target_id: str) -> dict:
        """生成比价结论：价格排序、建议、议价话术。"""
        t = store.get_target_style(target_id)
        if not t:
            return {"error": "目标款不存在"}
        r = run_comparison(store, t)
        rows = r.rows or []
        return {
            "综述": r.summary,
            "建议": r.recommendation,
            "报价条数": len(rows),
            "价格前5": [
                {
                    "店铺": x.get("shop_name"),
                    "价格": x.get("price"),
                    "来源": x.get("price_source"),
                    "匹配": x.get("tier"),
                    "商品": (x.get("title") or "")[:30],
                }
                for x in rows[:5]
            ],
            "议价话术": r.negotiation_script[:300] if r.negotiation_script else "",
        }

    def inquiry_script(target_id: str, product_id: str, quantity: int = 100) -> dict:
        """为某商品生成询价话术。"""
        t = store.get_target_style(target_id)
        if not t:
            return {"error": "目标款不存在"}
        prods = store.list_products(target_id)
        p = next((x for x in prods if x.id == product_id), None)
        if not p:
            return {"error": "商品不存在"}
        script = build_inquiry_script(t, p, quantity)
        return {"话术": script, "商品": p.title[:40], "单价": p.listed_price}

    def make_search_link(extra_keywords: str = "") -> dict:
        """生成 1688 搜索链接，指引用户去搜索并回填候选商品（副驾模式）。"""
        t = _current_target()
        if not t:
            return {"error": "缺少当前目标款"}
        kws = " ".join(x for x in [t.name, extra_keywords or ""] if x.strip())
        # 把关键属性值也拼进去，提高召回精度（取前 3 个非空值）
        attr_vals = [v for v in (t.attributes or {}).values() if v and len(v) <= 12][:3]
        full_kws = " ".join([kws] + attr_vals).strip()
        url = f"https://s.1688.com/selloffer/offer_search.htm?keywords={quote(full_kws)}"
        return {
            "搜索链接": url,
            "关键词": full_kws,
            "使用方法": (
                "1. 把链接发给用户在浏览器打开；2. 用户挑中候选商品后，"
                "用书签脚本一键导入（看板「导入」标签页有书签），"
                "或把商品详情页链接贴回来给你；3. 收到链接后调用 import_products 入库。"
            ),
        }

    def _fetch_offer(url: str) -> dict:
        """轻量抓取 1688 详情页标题/价格；被反爬或解析失败返回空结果。"""
        out = {"title": None, "price": None, "fetched": False}
        try:
            resp = httpx.get(
                url, headers={"User-Agent": _FETCH_UA},
                timeout=_FETCH_TIMEOUT, follow_redirects=True,
            )
            html = resp.text
        except Exception:
            return out
        m = re.search(r"<title>([^<]+)</title>", html, re.I)
        if not m:
            return out
        title = re.sub(r"\s*[-_—]\s*(阿里巴巴|1688|1688\.com).*$", "", m.group(1)).strip()
        # 反爬页特征：验证/滑块/登录
        if not title or any(k in title for k in ("验证", "滑块", "登录", "访问异常")):
            return out
        out["title"], out["fetched"] = title[:80], True
        # 价格：尝试页面内常见的几种价格字面量
        for pat in (r'"price"\s*:\s*"?([\d.]+)', r"[¥￥]\s*([\d.]+)", r'"unitPrice[^"]*"\s*:\s*"?([\d.]+)'):
            pm = re.search(pat, html)
            if pm:
                try:
                    v = float(pm.group(1))
                    if 0.1 <= v <= 100000:
                        out["price"] = v
                        break
                except ValueError:
                    continue
        return out

    def import_products(items: list, target_id: str = "") -> dict:
        """把用户贴来的 1688 商品链接/信息入库为候选商品。

        items 元素可以是 URL 字符串，或 {url, title?, price?} 字典。
        服务端先尝试轻量抓取标题/价格；被反爬拦截时降级为「只存 URL 的
        待核验候选」，图片与准确价格等用户用书签脚本补全。
        """
        t = _current_target() if not target_id else store.get_target_style(target_id)
        if not t:
            return {"error": "缺少目标款（请先选定目标款或在参数里给 target_id）"}
        items = (items or [])[:10]   # 单次上限 10 条，防失控
        existing = {p.url for p in store.list_products(t.id) if p.url}
        # 导入商品统一挂在「链接导入」店铺下
        if not store.get_shop("imported"):
            store.save_shop(Shop(id="imported", name="链接导入"))
        imported, skipped, degraded = 0, 0, []
        for it in items:
            url = it.get("url", "").strip() if isinstance(it, dict) else str(it).strip()
            if not url:
                continue
            if url in existing:
                skipped += 1
                continue
            m = _OFFER_ID_RE.search(url)
            offer_id = m.group(1) if m else str(abs(hash(url)) % 10**10)
            title = (it.get("title") if isinstance(it, dict) else None) or ""
            price = (it.get("price") if isinstance(it, dict) else None) or None
            fetched = False
            if not title or not price:
                fr = _fetch_offer(url)
                fetched = fr["fetched"]
                title = title or fr["title"] or ""
                price = price or fr["price"]
            if not fetched and not title:
                degraded.append(url[:60])
                title = f"1688商品{offer_id}（待书签补全）"
            pid = f"imp-{offer_id}"
            if store.get_product(pid):
                skipped += 1
                continue
            store.save_product(Product(
                id=pid, target_style_id=t.id, shop_id="imported",
                title=title, url=url, listed_price=price,
                match_status=MatchStatus.CANDIDATE,
            ))
            existing.add(url)
            imported += 1
        return {
            "导入数": imported,
            "跳过(重复)": skipped,
            "自动抓到标题": imported - len(degraded),
            "待书签补全": degraded,
            "下一步": "导入完成。有图的商品可直接 match_products 做视觉匹配；"
                      "无图的（链接导入）需要用户用书签脚本重新导入以补全图片，否则只能按属性/价格粗筛。",
        }

    tools = [
        ToolSpec(
            name="make_search_link",
            description=(
                "按当前目标款生成 1688 搜索链接，让用户去搜索候选商品。"
                "当目标款下的候选商品太少或没有时调用：把链接发给用户，"
                "用户挑好后用书签脚本导入或把详情页链接贴回来。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "extra_keywords": {"type": "string", "description": "追加搜索词（可选），如品牌、风格词"},
                },
                "required": [],
            },
            func=make_search_link,
        ),
        ToolSpec(
            name="import_products",
            description=(
                "把用户贴来的 1688 商品详情页链接（或 {url,title,price} 数据）"
                "入库为候选商品。系统会尝试自动抓取标题和价格，抓不到时降级存为"
                "待核验候选。导入后提醒：无图商品需要书签脚本补全图片才能视觉匹配。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "商品链接字符串数组，或 {url, title?, price?} 对象数组",
                        "items": {},
                    },
                    "target_id": {"type": "string", "description": "目标款 ID（缺省用当前选中的目标款）"},
                },
                "required": ["items"],
            },
            func=import_products,
        ),
        ToolSpec(
            name="list_targets",
            description="列出系统中已有的目标款（要比价的款），包括每个目标款下的商品数和价格覆盖情况。用于了解当前有哪些款可以比价。",
            parameters={"type": "object", "properties": {}, "required": []},
            func=list_targets,
        ),
        ToolSpec(
            name="inspect_target",
            description="查看某个目标款的详情：属性、参考价、商品总数、匹配状态分布。用于判断该款的数据是否足够做比价。",
            parameters={
                "type": "object",
                "properties": {"target_id": {"type": "string", "description": "目标款 ID"}},
                "required": ["target_id"],
            },
            func=inspect_target,
        ),
        ToolSpec(
            name="match_products",
            description="对目标款的候选商品执行视觉匹配，判断哪些是完全同款、等效替代、需要排除。必须在比价前做这一步，否则商品都是未核验状态。",
            parameters={
                "type": "object",
                "properties": {"target_id": {"type": "string", "description": "目标款 ID"}},
                "required": ["target_id"],
            },
            func=match_products,
        ),
        ToolSpec(
            name="compare_prices",
            description="生成比价结论：按价格排序、给出采购建议和议价话术。需要先完成视觉匹配效果最好。",
            parameters={
                "type": "object",
                "properties": {"target_id": {"type": "string", "description": "目标款 ID"}},
                "required": ["target_id"],
            },
            func=compare_prices,
        ),
        ToolSpec(
            name="inquiry_script",
            description="为某个候选商品生成向卖家询价的话术，包含价格、起订量、含税等问题。用于需要进一步确认报价的场景。",
            parameters={
                "type": "object",
                "properties": {
                    "target_id": {"type": "string", "description": "目标款 ID"},
                    "product_id": {"type": "string", "description": "候选商品 ID"},
                    "quantity": {"type": "integer", "description": "拟采购数量", "default": 100},
                },
                "required": ["target_id", "product_id"],
            },
            func=inquiry_script,
        ),
    ]
    return tools