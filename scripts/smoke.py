"""离线冒烟测试：跑通 匹配→询价话术→报价解析→比价 全链路（无 API key）。"""
from __future__ import annotations

from pathlib import Path

from app.comparison.comparison import run_comparison
from app.inquire.inquiry import build_inquiry_script, parse_quote
from app.matcher.matcher import Matcher
from app.store.db import Store

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
store = Store(DATA_DIR / "app.db")

target = store.get_target_style("seed-style-1")
products = store.list_products(target.id)

print("=== 匹配 ===")
matcher = Matcher()
cls = matcher.run(target, products)
print("完全同款:", [(m.product_id, round(m.confidence, 2)) for m in cls.exact])
print("等效替代:", [(m.product_id, round(m.confidence, 2)) for m in cls.equivalent])
print("已排除:", [m.product_id for m in cls.rejected])
print("候选:", [m.product_id for m in cls.candidate])

print("\n=== 询价话术（规则兜底，无 API key）===")
for p in products:
    s = build_inquiry_script(target, p, buyer_quantity=200)
    print(f"[{p.id}] {s}")

print("\n=== 报价解析（规则兜底）===")
resp = "这款 42 元一件，起订 100 件，不含税，运费自理。量大可以再谈。"
q = parse_quote(resp, target, products[1])
print(f"价格={q.price} MOQ={q.moq} 含税={q.tax.value}")

print("\n=== 比价 ===")
# 先把匹配状态回写到商品（这样比价才知道哪些是可比的同款）
from app.models.enums import MatchStatus, MatchTier
for p in products:
    cls_map = {}
    for m in cls.exact:
        cls_map[m.product_id] = (MatchStatus.VERIFIED_SAME, MatchTier.EXACT)
    for m in cls.equivalent:
        cls_map[m.product_id] = (MatchStatus.VERIFIED_EQUIVALENT, MatchTier.EQUIVALENT)
    for m in cls.rejected:
        cls_map[m.product_id] = (MatchStatus.REJECTED, None)
    if p.id in cls_map:
        p.match_status, p.match_tier = cls_map[p.id]
        store.save_product(p)

# 手工补报价再比价（包括被排除的 seed-prod-3，验证它不会进入同款比价）
from app.models.entities import Quote
from app.models.enums import QuoteSource, TaxStatus
for p, price, moq in [(products[0], 48.0, 50), (products[1], 42.0, 100), (products[2], 39.0, 20)]:
    store.save_quote(Quote(id=f"q-{p.id}", target_style_id=target.id, product_id=p.id,
                           shop_id=p.shop_id, source=QuoteSource.MANUAL, price=price, moq=moq,
                           tax=TaxStatus.UNKNOWN))
result = run_comparison(store, target)
print(result.summary)
print(result.recommendation)
print("议价话术:", result.negotiation_script)

store.close()
print("\nOK")