"""把比价业务能力包装成 Agent 可调用的工具集。"""
from __future__ import annotations

from typing import Any

from app.agent.react import ToolSpec
from app.comparison.comparison import run_comparison
from app.inquire.inquiry import build_inquiry_script
from app.matcher.matcher import Matcher
from app.models.entities import Product, TargetStyle
from app.store.db import Store


def build_tools(store: Store) -> list[ToolSpec]:
    """构建工具集，绑定到具体的数据存储。"""

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

    tools = [
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