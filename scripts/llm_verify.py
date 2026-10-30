"""第①阶段：用真实 DeepSeek 验证 话术生成 / 报价解析 / 议价话术。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.llm.client import get_client
from app.inquire.inquiry import build_inquiry_script, parse_quote
from app.comparison.comparison import build_comparison
from app.models.entities import Product, Quote, Shop, TargetStyle
from app.models.enums import QuoteSource, TaxStatus, MatchTier

client = get_client()
print("=== 1) 连通性 + 询价话术 ===")
target = TargetStyle(
    id="t1", name="280g 纯棉落肩卫衣-藏青",
    description="纯棉 280g 落肩宽松版型，藏青色，S-XXL",
    attributes={"面料": "纯棉", "克重": "280g", "版型": "落肩宽松"},
    reference_price=45.0,
)
prod = Product(id="p1", target_style_id="t1", shop_id="s1",
               title="重磅 280克 落肩卫衣 藏蓝")
script = build_inquiry_script(target, prod, buyer_quantity=200, llm=client)
print("话术:\n", script, "\n")

print("=== 2) 报价解析 ===")
reply = "亲，这款42.5元一件，起订100件，不含税不包邮，500件可以做到41。现货充足。"
q = parse_quote(reply, target, prod, llm=client)
print("price:", q.price, "| moq:", q.moq, "| tax:", q.tax.value)
print("tiers:", q.tiers, "| condition:", q.condition_note, "\n")

print("=== 3) 议价话术 ===")
shops = {"s1": Shop(id="s1", name="义乌A服饰"), "s2": Shop(id="s2", name="广州B制衣")}
quotes = [
    Quote(id="q1", target_style_id="t1", product_id="p1", shop_id="s1",
          source=QuoteSource.MANUAL, price=42.5, moq=100, tax=TaxStatus.EXCLUDED),
    Quote(id="q2", target_style_id="t1", product_id="p2", shop_id="s2",
          source=QuoteSource.MANUAL, price=39.0, moq=50, tax=TaxStatus.INCLUDED),
]
# 标记 p1/p2 为完全同款，让比价生效
prod.match_tier = MatchTier.EXACT
comp = build_comparison(target, quotes, shops, products={"p1": prod}, llm=client)
print("综述:", comp.summary)
print("建议:", comp.recommendation)
print("议价话术:\n", comp.negotiation_script)
print("\nDONE")