"""比价：商品标价 + 询价报价 → 归一 → 分层 → 建议 + 议价话术。

数据来源两类：
1. 标价（product.listed_price）：书签从 1688 页面抓到的公开价，覆盖全部候选商品。
2. 报价（quote.price）：逐家询价后解析的真实价，覆盖范围小但更真实。

比价规则：
- 完全同款(exact) 的标价/报价 → 可硬比价（comparable=True）
- 等效替代(equivalent) → 仅参考，标注 tier
- 已排除/未核验 → 不参与硬比价
- 同一商品若两者都有，以「报价」为准（更真实），标价保留供参考
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional

from app.llm.client import LLMClient
from app.models.entities import ComparisonResult, Product, Quote, Shop, TargetStyle
from app.models.enums import MatchStatus, MatchTier
from app.store.db import Store

TIER_LABEL = {
    "exact": "完全同款",
    "equivalent": "等效替代",
    "unverified": "未核验",
    "rejected": "已排除",
}


def _product_row(p: Product, shop: Optional[Shop]) -> dict:
    """把商品转成比价行（标价来源）。"""
    tier = p.match_tier.value if p.match_tier else "unverified"
    if p.match_status == MatchStatus.REJECTED:
        tier = "rejected"
    return {
        "product_id": p.id,
        "shop_id": p.shop_id,
        "shop_name": shop.name if shop else p.shop_id,
        "title": p.title,
        "price": p.listed_price,
        "price_source": "listed",      # 标价
        "moq": p.moq,
        "tax": "unknown",
        "includes_shipping": None,
        "tiers": [],                   # 标价无阶梯价信息
        "condition_note": "页面标价",
        "tier": tier,
        "comparable": tier == MatchTier.EXACT.value and p.listed_price is not None,
        "url": p.url,
        "quoted_at": None,
    }


def _quote_row(q: Quote, p: Optional[Product], shop: Optional[Shop]) -> dict:
    """把报价转成比价行（询价来源）。"""
    tier = (p.match_tier.value if (p and p.match_tier) else "unverified")
    if p and p.match_status == MatchStatus.REJECTED:
        tier = "rejected"
    return {
        "product_id": q.product_id,
        "shop_id": q.shop_id,
        "shop_name": shop.name if shop else q.shop_id,
        "title": p.title if p else "",
        "price": q.price,
        "price_source": "quoted",      # 询价报价
        "moq": q.moq,
        "tax": q.tax.value,
        "includes_shipping": q.includes_shipping,
        "stock_status": q.stock_status.value,    # in_stock/pre_sale/custom/unknown
        "lead_time_hours": q.lead_time_hours,    # 发货时效（小时）
        "tiers": q.tiers,              # 阶梯价 [{qty, price}]
        "condition_note": q.condition_note,
        "tier": tier,
        "comparable": tier == MatchTier.EXACT.value,
        "url": p.url if p else None,
        "quoted_at": q.quoted_at.isoformat(),
    }


def _merge_rows(products: list[Product], quotes: list[Quote],
                shops: dict[str, Shop]) -> list[dict]:
    """合并标价与报价。同一商品优先用报价（更真实），标价另存字段供参考。"""
    pmap = {p.id: p for p in products}
    rows: dict[str, dict] = {}

    # 先铺标价（覆盖所有有标价的商品）
    for p in products:
        if p.listed_price is None:
            continue
        rows[p.id] = _product_row(p, shops.get(p.shop_id))

    # 再用报价覆盖同商品（报价更可信）
    # 同一商品可能有多条报价（多次询价/多轮谈判），按报价时间升序处理，
    # 让最新一条最终生效（否则 list_quotes 的价格排序会让旧价覆盖新价）
    for q in sorted(quotes, key=lambda x: x.quoted_at):
        p = pmap.get(q.product_id)
        qr = _quote_row(q, p, shops.get(q.shop_id))
        # 保留标价作为参考
        if q.product_id in rows:
            qr["listed_price"] = rows[q.product_id].get("price")
        rows[q.product_id] = qr

    out = list(rows.values())
    # 排序：可比的在前，按价格升序
    out.sort(key=lambda r: (not r["comparable"], r["price"] if r["price"] is not None else 1e9))
    return out


def build_comparison(
    target: TargetStyle,
    quotes: list[Quote],
    shops: dict[str, Shop],
    products: dict[str, Product] | None = None,
    llm: Optional[LLMClient] = None,
) -> ComparisonResult:
    llm = llm or LLMClient()
    products = products or {}
    rows = _merge_rows(list(products.values()), quotes, shops)

    result = ComparisonResult(target_style_id=target.id, rows=rows)

    if not rows:
        result.summary = "暂无可比数据：候选商品没有标价，也没有询价报价。"
        result.recommendation = "请先通过书签导入带价格的商品，或对候选店铺发起询价。"
        result.negotiation_script = ""
        return result

    exact_rows = [r for r in rows if r["comparable"]]
    equiv_rows = [r for r in rows if r["tier"] == "equivalent" and r["price"] is not None]
    ref = target.reference_price

    # ---- 综述 ----
    n_listed = sum(1 for r in rows if r["price_source"] == "listed")
    n_quoted = sum(1 for r in rows if r["price_source"] == "quoted")
    parts = [f"共 {len(rows)} 家可比候选（标价 {n_listed} 条、询价报价 {n_quoted} 条）"]
    parts.append(f"其中完全同款 {len(exact_rows)} 家、等效替代 {len(equiv_rows)} 家")
    if exact_rows:
        parts.append(f"同款最低 {exact_rows[0]['price']:.2f} 元（{exact_rows[0]['shop_name']}）")
    result.summary = "；".join(parts) + "。"

    # ---- 建议 ----
    if exact_rows:
        cheapest = exact_rows[0]
        src = "报价" if cheapest["price_source"] == "quoted" else "标价"
        if ref and cheapest["price"] <= ref:
            rec = (f"建议优先考虑「{cheapest['shop_name']}」，{src} {cheapest['price']:.2f} 元，"
                   f"低于参考价 {ref:.2f} 元。")
        else:
            rec = (f"完全同款中价格最低的是「{cheapest['shop_name']}」，{src} "
                   f"{cheapest['price']:.2f} 元。注意核对 MOQ / 含税 / 运费后再定。")
        if len(exact_rows) > 1:
            gap = exact_rows[-1]["price"] - cheapest["price"]
            if gap > 0:
                rec += f" 同款价差 {gap:.2f} 元，有议价空间。"
    elif equiv_rows:
        cheapest = equiv_rows[0]
        rec = (f"暂无完全同款，最接近的等效替代来自「{cheapest['shop_name']}」"
               f"{cheapest['price']:.2f} 元。建议先核验是否为同版货。")
    else:
        rec = "没有可用价格数据，请先导入带价格的商品或完成询价。"
    result.recommendation = rec

    # ---- 议价话术 ----
    anchor = exact_rows[0] if exact_rows else (equiv_rows[0] if equiv_rows else None)
    if anchor is None:
        result.negotiation_script = ""
        return result

    if llm.available:
        try:
            lines = [
                f"{r['shop_name']}: {r['price']:.2f} 元"
                f"（{'标价' if r['price_source']=='listed' else '报价'}, "
                f"MOQ {r['moq'] or '未知'}, {TIER_LABEL.get(r['tier'], r['tier'])}）"
                for r in (exact_rows[:8] or equiv_rows[:8])
            ]
            user_content = (
                f"目标款：{target.name}\n"
                f"各店价格：\n" + "\n".join(lines) + "\n"
                f"最低价：{anchor['shop_name']} {anchor['price']:.2f} 元。\n"
                "请帮我写一段向价格较高卖家议价的话术，自然、有据、不卑不亢。"
            )
            result.negotiation_script = llm.chat([
                {"role": "system", "content": "你是议价助手，写一段给卖家的砍价话术。"},
                {"role": "user", "content": user_content},
            ])
        except Exception:
            pass

    if not result.negotiation_script:
        others = [r for r in exact_rows if r["shop_id"] != anchor["shop_id"]]
        if others:
            ref_row = others[0]
            result.negotiation_script = (
                f"老板，这款别家「{ref_row['shop_name']}」能做到 {ref_row['price']:.2f} 元，"
                f"我这边量也不少，你这边能不能再给个更实在的价？"
            )
        else:
            result.negotiation_script = (
                f"老板，这款你标 {anchor['price']:.2f} 元，"
                f"如果我能多拿一些，价格上能不能再优惠点？"
            )
    return result


def _fingerprint(
    target: TargetStyle,
    products: dict[str, Product],
    quotes: list[Quote],
    shops: dict[str, Shop],
) -> str:
    """比价输入数据指纹：影响比价结论的任一字段变化则指纹变化。

    覆盖：目标款参考价、商品（标价/MOQ/匹配状态）、报价（全字段）、店铺名。
    不含无关字段（图片路径、URL、时间戳等），避免无意义的缓存失效。
    """
    payload = {
        "ref": target.reference_price,
        "products": sorted(
            (
                p.id, p.listed_price, p.moq,
                p.match_status.value, p.match_tier.value if p.match_tier else None,
            )
            for p in products.values()
        ),
        "quotes": sorted(
            (
                q.id, q.price, q.moq, q.tax.value, q.includes_shipping,
                q.stock_status.value, q.lead_time_hours,
                json.dumps(q.tiers, ensure_ascii=False, sort_keys=True),
                q.condition_note, q.quoted_at.isoformat(),
            )
            for q in quotes
        ),
        "shops": sorted((s.id, s.name) for s in shops.values()),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def run_comparison(
    store: Store,
    target: TargetStyle,
    llm: Optional[LLMClient] = None,
    force_refresh: bool = False,
) -> ComparisonResult:
    """从 store 拉商品标价 + 询价报价 + 店铺，生成比价结论并落库。

    带数据指纹缓存：输入数据未变化时直接复用上次结论（省 LLM 调用与等待），
    force_refresh=True 时跳过缓存强制重算。
    """
    quotes = store.list_quotes(target.id)
    shops = {s.id: s for s in store.list_shops()}
    products = {p.id: p for p in store.list_products(target.id)}

    fp = _fingerprint(target, products, quotes, shops)
    if not force_refresh:
        cached = store.get_comparison(target.id)
        if cached and cached.fingerprint == fp:
            return cached

    result = build_comparison(target, quotes, shops, products, llm)
    result.fingerprint = fp
    store.save_comparison(result)
    return result