"""MOQ 归一化 + 数据时效的回归测试（无网络、秒级）。

覆盖：阶梯价/MOQ 折算单件可比价、归一价排序、价格数据时间字段（price_at）。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.comparison.comparison import NORM_REF_QTY, _merge_rows, _normalize, _price_text
from app.models.entities import Product, Quote, Shop
from app.models.enums import MatchStatus, MatchTier, QuoteSource


def _product(pid: str, price: float | None, moq: int | None = None) -> Product:
    return Product(
        id=pid, target_style_id="t1", shop_id=f"s-{pid}", title=f"商品{pid}",
        listed_price=price, moq=moq,
        match_status=MatchStatus.VERIFIED_SAME, match_tier=MatchTier.EXACT,
    )


def _quote(qid: str, pid: str, price: float, moq: int | None = None,
           tiers: list[dict] | None = None, hours_ago: int = 2) -> Quote:
    return Quote(
        id=qid, target_style_id="t1", product_id=pid, shop_id=f"s-{pid}",
        source=QuoteSource.INQUIRY, price=price, moq=moq, tiers=tiers or [],
        quoted_at=datetime.now() - timedelta(hours=hours_ago),
    )


class TestNormalize:
    """阶梯价 / MOQ 折算到统一基准量。"""

    def test_no_price(self):
        row = {"price": None, "moq": None, "tiers": []}
        _normalize(row)
        assert row["normalized_price"] is None
        assert row["norm_note"] == ""

    def test_plain_price_no_note(self):
        row = {"price": 9.9, "moq": 10, "tiers": []}
        _normalize(row)
        assert row["normalized_price"] == 9.9
        assert row["norm_note"] == ""

    def test_tier_at_ref_qty(self):
        # 100 件档恰好等于基准量 → 取该档价
        row = {"price": 6.0, "moq": 10, "tiers": [{"qty": 10, "price": 6.0},
                                                  {"qty": 100, "price": 4.0}]}
        _normalize(row)
        assert row["normalized_price"] == 4.0
        assert "100件档" in row["norm_note"]

    def test_all_tiers_above_ref(self):
        # 所有档位都高于基准量 → 取最低档价并标注门槛
        row = {"price": 8.5, "moq": 500, "tiers": [{"qty": 500, "price": 8.5}]}
        _normalize(row)
        assert row["normalized_price"] == 8.5
        assert "需达500件" in row["norm_note"]

    def test_moq_above_ref_no_tiers(self):
        # 无阶梯价但 MOQ 高于基准量 → 保留原价 + 门槛标注
        row = {"price": 8.5, "moq": 500, "tiers": []}
        _normalize(row)
        assert row["normalized_price"] == 8.5
        assert "MOQ 500件起" in row["norm_note"]

    def test_zero_tiers_ignored(self):
        # 脏数据（qty/price 为 0 或缺失）不应崩溃
        row = {"price": 5.0, "moq": None, "tiers": [{"qty": 0, "price": 1.0},
                                                    {"price": 2.0}, "bad"]}
        _normalize(row)
        assert row["normalized_price"] == 5.0

    def test_ref_qty_constant(self):
        assert NORM_REF_QTY == 100


class TestMergeSortByNormalized:
    """归一价参与排序：大 MOQ 低价不得凭原始标价霸榜。"""

    def test_tier_discount_sorts_first(self):
        # A：标价 5.0 无阶梯；B：报价 6.0 但 100 件档 4.0 → 归一后 B(4.0) 排前
        pa = _product("pa", 5.0)
        pb = _product("pb", None)   # 无标价，仅报价参与
        shops = {"s-pa": Shop(id="s-pa", name="店A"), "s-pb": Shop(id="s-pb", name="店B")}
        rows = _merge_rows([pa, pb], [_quote("q1", "pb", 6.0, tiers=[{"qty": 100, "price": 4.0}])], shops)
        assert len(rows) == 2
        assert rows[0]["shop_id"] == "s-pb"
        assert rows[0]["normalized_price"] == 4.0

    def test_high_moq_keeps_original_price_display(self):
        # 排序按归一价，但原始 price 不被改写
        pa = _product("pa", 5.0)
        shops = {"s-pa": Shop(id="s-pa", name="店A"), "s-pb": Shop(id="s-pb", name="店B")}
        rows = _merge_rows([pa], [_quote("q1", "pb", 4.0, moq=500)], shops)
        pb_row = next(r for r in rows if r["shop_id"] == "s-pb")
        assert pb_row["price"] == 4.0
        assert pb_row["normalized_price"] == 4.0
        assert "MOQ 500件起" in pb_row["norm_note"]


class TestPriceAt:
    """比价行携带价格数据时间（数据时效前端展示依赖此字段）。"""

    def test_zero_listed_price_not_comparable(self):
        # 标价 0 元（抓取失败占位）与 ¥0 报价同样不可比，不得成为"最低同款"
        rows = _merge_rows([_product("p0", 0.0), _product("p1", 12.0)], [], {})
        p0 = next(r for r in rows if r["product_id"] == "p0")
        p1 = next(r for r in rows if r["product_id"] == "p1")
        assert p0["comparable"] is False
        assert p1["comparable"] is True
        assert rows[0]["product_id"] == "p1"  # 真实最低价排前

    def test_listed_row_price_at(self):
        p = _product("p1", 10.0)
        rows = _merge_rows([p], [], {})
        assert rows[0]["price_at"] == p.created_at.isoformat()

    def test_quoted_row_price_at(self):
        q = _quote("q1", "p1", 10.0, hours_ago=48)
        rows = _merge_rows([], [q], {})
        assert rows[0]["price_at"] == q.quoted_at.isoformat()


class TestPriceText:
    """归一价文案：有门槛时同时给出折算依据。"""

    def test_plain(self):
        assert _price_text({"price": 9.9, "norm_note": "", "normalized_price": 9.9}) == "9.90 元"

    def test_with_note(self):
        txt = _price_text({"price": 8.5, "norm_note": "MOQ 500件起", "normalized_price": 8.5})
        assert "8.50 元" in txt and "MOQ 500件起" in txt and "100 件基准量" in txt
