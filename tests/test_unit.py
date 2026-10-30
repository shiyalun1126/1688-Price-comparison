"""纯逻辑单测（无网络、秒级）：比价合并 / 匹配分层 / 报价正则 / 视觉提示词。"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.comparison.comparison import _merge_rows, _product_row, _quote_row
from app.inquire.inquiry import _parse_rules
from app.matcher.matcher import MatchOutcome, classify
from app.matcher.vision import build_system_prompt
from app.models.entities import Product, Quote, Shop, TargetStyle
from app.models.enums import (
    InquiryStatus,
    MatchStatus,
    MatchTier,
    QuoteSource,
    TaxStatus,
)


def _target() -> TargetStyle:
    return TargetStyle(id="t1", name="测试款", category="POLO衫")


def _product(pid: str, price: float | None, status=MatchStatus.VERIFIED_SAME,
             tier=MatchTier.EXACT) -> Product:
    return Product(
        id=pid, target_style_id="t1", shop_id=f"s-{pid}", title=f"商品{pid}",
        listed_price=price, match_status=status, match_tier=tier,
    )


def _quote(qid: str, pid: str, price: float, minutes_ago: int) -> Quote:
    return Quote(
        id=qid, target_style_id="t1", product_id=pid, shop_id=f"s-{pid}",
        source=QuoteSource.INQUIRY, price=price,
        quoted_at=datetime.now() - timedelta(minutes=minutes_ago),
    )


# ---- 比价合并：同一商品多条报价，最新的一条生效（回归 bug 修复）----
class TestMergeRows:
    def test_latest_quote_wins(self):
        """旧报价不得覆盖新报价（曾因按价格排序覆盖导致 48 盖掉 42.5）。"""
        p = _product("p1", 48.0)
        shops = {"s-p1": Shop(id="s-p1", name="店铺A")}
        quotes = [
            _quote("q-old", "p1", 48.0, minutes_ago=120),   # 早
            _quote("q-new", "p1", 42.5, minutes_ago=5),     # 晚
        ]
        rows = _merge_rows([p], quotes, shops)
        assert len(rows) == 1
        assert rows[0]["price"] == 42.5
        assert rows[0]["price_source"] == "quoted"

    def test_quote_overrides_listed_and_keeps_listed_as_reference(self):
        p = _product("p1", 48.0)
        shops = {"s-p1": Shop(id="s-p1", name="店铺A")}
        rows = _merge_rows([p], [_quote("q1", "p1", 42.5, 10)], shops)
        assert rows[0]["price"] == 42.5
        assert rows[0]["listed_price"] == 48.0

    def test_listed_only_rows_kept(self):
        p = _product("p1", 39.0)
        rows = _merge_rows([p], [], {"s-p1": Shop(id="s-p1", name="店铺A")})
        assert rows[0]["price"] == 39.0
        assert rows[0]["price_source"] == "listed"

    def test_comparable_only_for_exact_with_price(self):
        exact = _product("p1", 10.0, MatchStatus.VERIFIED_SAME, MatchTier.EXACT)
        equiv = _product("p2", 10.0, MatchStatus.VERIFIED_EQUIVALENT, MatchTier.EQUIVALENT)
        rejected = _product("p3", 10.0, MatchStatus.REJECTED, None)
        shops = {f"s-{p.id}": Shop(id=f"s-{p.id}", name=p.id) for p in (exact, equiv, rejected)}
        rows = {r["product_id"]: r for r in _merge_rows([exact, equiv, rejected], [], shops)}
        assert rows["p1"]["comparable"] is True
        assert rows["p2"]["comparable"] is False
        assert rows["p3"]["comparable"] is False
        assert rows["p3"]["tier"] == "rejected"


# ---- 匹配分层 ----
class TestClassify:
    def test_bucketing(self):
        outcomes = [
            MatchOutcome("a", MatchStatus.VERIFIED_SAME, None, 0.9, ""),
            MatchOutcome("b", MatchStatus.VERIFIED_EQUIVALENT, None, 0.7, ""),
            MatchOutcome("c", MatchStatus.REJECTED, None, 0.8, ""),
            MatchOutcome("d", MatchStatus.CANDIDATE, None, 0.0, ""),
        ]
        c = classify(outcomes)
        assert [m.product_id for m in c.exact] == ["a"]
        assert [m.product_id for m in c.equivalent] == ["b"]
        assert [m.product_id for m in c.rejected] == ["c"]
        assert [m.product_id for m in c.candidate] == ["d"]
        # classify 会给 verified_* 补 tier
        assert c.exact[0].tier == MatchTier.EXACT
        assert c.equivalent[0].tier == MatchTier.EQUIVALENT


# ---- 报价正则兜底 ----
class TestParseRules:
    def test_price_moq_tax(self):
        r = _parse_rules("亲，42.5元一件，起订100件，不含税不包邮，500件以上41。")
        assert r["price"] == 42.5
        assert r["moq"] == 100
        assert r["tax"] == TaxStatus.EXCLUDED

    def test_price_with_currency_symbol(self):
        r = _parse_rules("这个¥39一件")
        assert r["price"] == 39.0

    def test_tax_included(self):
        r = _parse_rules("含税价 55 元")
        assert r["tax"] == TaxStatus.INCLUDED

    def test_no_price(self):
        r = _parse_rules("私聊详谈")
        assert r["price"] is None


# ---- 视觉提示词：品类自适应 ----
class TestVisionPrompt:
    def test_clothing_hint_injected(self):
        prompt = build_system_prompt("POLO衫")
        assert "服装" in prompt and "领型" in prompt

    def test_digital_hint_injected(self):
        prompt = build_system_prompt("蓝牙耳机")
        assert "数码" in prompt and ("型号" in prompt or "代际" in prompt)

    def test_default_hint_for_unknown_category(self):
        prompt = build_system_prompt("陶瓷花盆")
        assert "通用维度" in prompt

    def test_core_principles_always_present(self):
        for cat in ("POLO衫", "蓝牙耳机", None):
            prompt = build_system_prompt(cat)
            assert "明确可见且相互矛盾" in prompt
            assert "宁可判" in prompt
