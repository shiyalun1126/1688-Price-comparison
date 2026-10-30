"""询价：话术生成 + 报价解析（DeepSeek 优先，规则兜底）。"""
from __future__ import annotations

import re
from typing import Optional

from app.llm.client import LLMClient
from app.models.entities import Product, Quote, TargetStyle
from app.models.enums import QuoteSource, TaxStatus


def build_inquiry_script(
    target: TargetStyle,
    product: Product,
    buyer_quantity: Optional[int] = None,
    llm: Optional[LLMClient] = None,
) -> str:
    """生成询价话术。带属性核验问题，兼顾问价与辨货。"""
    llm = llm or LLMClient()
    attr_line = f"属性：{target.attributes}" if target.attributes else ""
    qty_line = f"数量：{buyer_quantity} 件" if buyer_quantity else "数量：请告知起订量"

    if llm.available:
        try:
            return llm.chat([
                {"role": "system", "content": (
                    "你是采购询价助手。生成一条发给 1688 卖家的询价消息："
                    "口语、自然、像真人，问价格、起订量、含税/运费，"
                    "并顺带核验目标款的关键属性是否与候选商品一致（属性随品类而变，"
                    "如服装问面料/克重/版型，数码问型号/代际/规格，按给定属性提问）。"
                    "只输出消息正文，不要多余说明。"
                )},
                {"role": "user", "content": (
                    f"目标款：{target.name}\n{target.description or ''}\n"
                    f"{attr_line}\n{qty_line}\n"
                    f"候选商品标题：{product.title}"
                )},
            ])
        except Exception:
            pass  # 降级到规则模板

    # 规则兜底模板
    parts = [f"你好，看到你家「{product.title}」，想询个价。"]
    if buyer_quantity:
        parts.append(f"我这边大概要 {buyer_quantity} 件。")
    parts.append("麻烦问下：单价多少？起订量多少？含税吗？包运费吗？")
    if target.attributes:
        parts.append(f"另外确认下规格：{', '.join(f'{k}是不是{v}' for k, v in target.attributes.items())}？")
    return "".join(parts)


def _parse_rules(reply: str) -> dict:
    """规则兜底：正则抽取价格/起订量/含税。"""
    result: dict = {"price": None, "moq": None, "tax": TaxStatus.UNKNOWN, "tiers": []}
    # 价格：形如「5.5 元」「5元」「¥5」「5.5/件」
    m = re.search(r"[¥￥]\s*(\d+(?:\.\d+)?)", reply)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块)/?(?:件|个|只|条|套|包)?", reply)
    if m:
        result["price"] = float(m.group(1))
    if "不含税" in reply or "不含票" in reply:
        result["tax"] = TaxStatus.EXCLUDED
    elif "含税" in reply or "含票" in reply:
        result["tax"] = TaxStatus.INCLUDED
    # 起订量
    mq = re.search(r"(?:起订|起批量|最小|MOQ)[^\d]*(\d+)", reply, re.IGNORECASE)
    if mq:
        result["moq"] = int(mq.group(1))
    return result


def parse_quote(
    reply: str,
    target: TargetStyle,
    product: Product,
    llm: Optional[LLMClient] = None,
) -> Quote:
    """从卖家回复抽取结构化报价。LLM 优先，正则兜底。"""
    llm = llm or LLMClient()
    q = Quote(
        id="", target_style_id=target.id, product_id=product.id,
        shop_id=product.shop_id, source=QuoteSource.INQUIRY,
        price=0.0, raw_text=reply,
    )

    if llm.available:
        try:
            text = llm.chat([
                {"role": "system", "content": (
                    "从卖家回复中抽取报价，输出 JSON，字段：price(数字,单位元)、"
                    "moq(整数,可为null)、tax(字符串: included/excluded/unknown)、"
                    "includes_shipping(布尔,可为null)、condition_note(条件备注原文)、"
                    "tiers(阶梯价数组[{qty,price}],可为空)。"
                    "只输出 JSON，不要解释。"
                )},
                {"role": "user", "content": reply},
            ])
            import json
            data = json.loads(text.strip().strip("`"))
            q.price = float(data["price"])
            if data.get("moq") is not None:
                q.moq = int(data["moq"])
            tax = data.get("tax", "unknown")
            q.tax = TaxStatus(tax) if tax in TaxStatus._value2member_map_ else TaxStatus.UNKNOWN
            q.includes_shipping = data.get("includes_shipping")
            q.tiers = data.get("tiers", [])
            q.condition_note = data.get("condition_note", "")
            return q
        except Exception:
            pass  # 降级

    r = _parse_rules(reply)
    if r["price"] is not None:
        q.price = r["price"]
    q.moq = r["moq"]
    q.tax = r["tax"]
    q.condition_note = reply
    return q