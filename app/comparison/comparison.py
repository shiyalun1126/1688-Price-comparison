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

# 归一基准量（件）：阶梯价 / MOQ 折算到该采购量档做横向可比
NORM_REF_QTY = 100


def _normalize(row: dict, ref_qty: int = NORM_REF_QTY) -> None:
    """把一行价格折算到统一采购量档的单件可比价（MOQ 归一）。

    动机：「500 件起 8.5 元」和「10 件起 10 元」的标价不可直接硬比。
    规则：
    - 有阶梯价：取 qty <= ref_qty 的最大档价格；所有档位都高于 ref_qty 时
      取最低档价并标注「需达 N 件」。
    - 无阶梯价：MOQ <= ref_qty 时原价直接可比；MOQ > ref_qty 保留原价
      并标注起订门槛（提示该价需更大采购量支撑）。
    归一结果写入 normalized_price / norm_note，不改动原始 price / tiers。
    """
    price = row.get("price")
    if price is None or price <= 0:
        row["normalized_price"] = None
        row["norm_note"] = ""
        return
    tiers = [t for t in (row.get("tiers") or [])
             if isinstance(t, dict) and t.get("qty") and t.get("price")]
    tiers.sort(key=lambda t: t["qty"])
    if tiers:
        applicable = [t for t in tiers if t["qty"] <= ref_qty]
        if applicable:
            t = applicable[-1]
            row["normalized_price"] = float(t["price"])
            row["norm_note"] = f"按{t['qty']}件档" if float(t["price"]) != float(price) else ""
        else:
            t = tiers[0]
            row["normalized_price"] = float(t["price"])
            row["norm_note"] = f"需达{t['qty']}件"
        return
    moq = row.get("moq")
    if moq and moq > ref_qty:
        row["normalized_price"] = float(price)
        row["norm_note"] = f"MOQ {moq}件起"
        return
    row["normalized_price"] = float(price)
    row["norm_note"] = ""


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
        # 标价 <=0（抓取失败占位）与 ¥0 报价同样不可比，防止假最低价霸榜
        "comparable": tier == MatchTier.EXACT.value and p.listed_price is not None and p.listed_price > 0,
        "url": p.url,
        "quoted_at": None,
        "price_at": p.created_at.isoformat() if p.created_at else None,  # 标价抓取时间
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
        # 无有效价格（解析失败=0）的报价不可比，防止 ¥0 顶掉真实价格
        "comparable": tier == MatchTier.EXACT.value and q.price is not None and q.price > 0,
        "url": p.url if p else None,
        "quoted_at": q.quoted_at.isoformat(),
        "price_at": q.quoted_at.isoformat(),  # 报价时间（数据时效用）
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
    # 让最新一条最终生效（否则 list_quotes 的价格排序会让旧价覆盖新价）。
    # 无有效价格（<=0，通常是寒暄回复解析失败）的报价不覆盖标价、不参与比价。
    for q in sorted(quotes, key=lambda x: x.quoted_at):
        if q.price is None or q.price <= 0:
            continue
        p = pmap.get(q.product_id)
        qr = _quote_row(q, p, shops.get(q.shop_id))
        # 保留标价作为参考
        if q.product_id in rows:
            qr["listed_price"] = rows[q.product_id].get("price")
        rows[q.product_id] = qr

    out = list(rows.values())
    # MOQ 归一：折算到统一基准量后再排序，避免「大 MOQ 低价」霸榜误导
    for r in out:
        _normalize(r)
    # 排序：可比的在前，按归一价升序（无归一价时退回原始价）
    def _sort_key(r: dict):
        p = r.get("normalized_price")
        if p is None:
            p = r["price"] if r["price"] is not None else 1e9
        return (not r["comparable"], p)
    out.sort(key=_sort_key)
    return out


def _price_text(row: dict) -> str:
    """价格文案：归一价与原始价不同时，同时给出折算依据。"""
    txt = f"{row['price']:.2f} 元"
    if row.get("norm_note") and row.get("normalized_price"):
        txt += (f"（{row['norm_note']}，按 {NORM_REF_QTY} 件基准量折算 "
                f"{row['normalized_price']:.2f} 元）")
    return txt


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
    equiv_rows = [r for r in rows if r["tier"] == "equivalent" and r["price"] is not None and r["price"] > 0]
    ref = target.reference_price

    # ---- 综述 ----
    n_listed = sum(1 for r in rows if r["price_source"] == "listed")
    n_quoted = sum(1 for r in rows if r["price_source"] == "quoted")
    parts = [f"共 {len(rows)} 家可比候选（标价 {n_listed} 条、询价报价 {n_quoted} 条）"]
    parts.append(f"其中完全同款 {len(exact_rows)} 家、等效替代 {len(equiv_rows)} 家")
    if any(r.get("norm_note") for r in rows):
        parts.append(f"价格已按 {NORM_REF_QTY} 件基准量归一折算（阶梯价/MOQ 门槛见行内标注）")
    if exact_rows:
        parts.append(f"同款最低 {_price_text(exact_rows[0])}（{exact_rows[0]['shop_name']}）")
    result.summary = "；".join(parts) + "。"

    # ---- 建议 ----
    if exact_rows:
        cheapest = exact_rows[0]
        src = "报价" if cheapest["price_source"] == "quoted" else "标价"
        cheap_txt = _price_text(cheapest)
        if ref and cheapest["price"] <= ref:
            rec = (f"建议优先考虑「{cheapest['shop_name']}」，{src} {cheap_txt}，"
                   f"低于参考价 {ref:.2f} 元。")
        else:
            rec = (f"完全同款中价格最低的是「{cheapest['shop_name']}」，{src} "
                   f"{cheap_txt}。注意核对 MOQ / 含税 / 运费后再定。")
        # 页面标价可能是引流饵价（非真实成交价），最低价若来自标价须明确提示
        if cheapest["price_source"] == "listed":
            quoted_rows = [r for r in exact_rows if r["price_source"] == "quoted"]
            if quoted_rows:
                rec += (f" ⚠️ 该最低价为页面标价（可能为引流价，未必能按此价成交），"
                        f"已核实报价最低为「{quoted_rows[0]['shop_name']}」"
                        f"{quoted_rows[0]['price']:.2f} 元，以下单报价为准。")
            else:
                rec += " ⚠️ 该价格来自页面标价，可能为引流价；建议发起询价核实真实成交价后再决策。"
        if len(exact_rows) > 1:
            gap = exact_rows[-1]["price"] - cheapest["price"]
            if gap > 0:
                rec += f" 同款价差 {gap:.2f} 元，有议价空间。"
    elif equiv_rows:
        cheapest = equiv_rows[0]
        rec = (f"暂无完全同款，最接近的等效替代来自「{cheapest['shop_name']}」"
               f"{_price_text(cheapest)}。建议先核验是否为同版货。")
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
    cache_only: bool = False,
) -> Optional[ComparisonResult]:
    """从 store 拉商品标价 + 询价报价 + 店铺，生成比价结论并落库。

    带数据指纹缓存：输入数据未变化时直接复用上次结论（省 LLM 调用与等待），
    force_refresh=True 时跳过缓存强制重算。
    cache_only=True 时只读取与当前指纹匹配的缓存，未命中返回 None（不触发 LLM）。
    """
    quotes = store.list_quotes(target.id)
    shops = {s.id: s for s in store.list_shops()}
    products = {p.id: p for p in store.list_products(target.id)}

    fp = _fingerprint(target, products, quotes, shops)
    if not force_refresh:
        cached = store.get_comparison(target.id)
        if cached and cached.fingerprint == fp:
            return cached
        if cache_only:
            return None

    result = build_comparison(target, quotes, shops, products, llm)
    result.fingerprint = fp
    store.save_comparison(result)
    return result