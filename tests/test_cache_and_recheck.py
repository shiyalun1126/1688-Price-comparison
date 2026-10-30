"""比价指纹缓存 + 视觉二次复核的单测（无网络、秒级）。"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.comparison.comparison import _fingerprint, run_comparison
from app.matcher.vision import RECHECK_HIGH, RECHECK_LOW, merge_recheck
from app.models.entities import Product, Quote, Shop, TargetStyle
from app.models.enums import MatchStatus, MatchTier, QuoteSource, TaxStatus


class SpyLLM:
    """记录 chat 调用次数的假 LLM（available=True 让代码走 LLM 分支）。"""

    def __init__(self):
        self.calls = 0

    @property
    def available(self) -> bool:
        return True

    def chat(self, messages, **kwargs) -> str:
        self.calls += 1
        return "测试话术"


@pytest.fixture
def store(tmp_path):
    from app.store.db import Store

    s = Store(tmp_path / "cache_test.db")
    yield s
    s.close()


def _seed(store, price: float = 42.5):
    """铺一套最小可比数据：目标款 + 店铺 + exact 商品 + 报价。"""
    t = TargetStyle(id="t1", name="测试款", reference_price=45.0, category="POLO衫")
    store.save_target_style(t)
    store.save_shop(Shop(id="s1", name="店铺A"))
    store.save_product(Product(
        id="p1", target_style_id="t1", shop_id="s1", title="同款POLO",
        listed_price=48.0, match_status=MatchStatus.VERIFIED_SAME,
        match_tier=MatchTier.EXACT,
    ))
    store.save_quote(Quote(
        id="q1", target_style_id="t1", product_id="p1", shop_id="s1",
        source=QuoteSource.INQUIRY, price=price, tax=TaxStatus.EXCLUDED,
        quoted_at=datetime.now() - timedelta(minutes=5),
    ))
    return t


# ---- 指纹缓存 ----
class TestFingerprintCache:
    def test_cache_hit_skips_llm(self, store):
        """数据不变时第二次 run_comparison 不再调用 LLM（复用缓存结论）。"""
        t = _seed(store)
        llm = SpyLLM()
        r1 = run_comparison(store, t, llm=llm)
        assert llm.calls == 1                      # 首次生成议价话术
        r2 = run_comparison(store, t, llm=llm)
        assert llm.calls == 1                      # 缓存命中，未再调用
        assert r2.fingerprint == r1.fingerprint != ""
        assert r2.summary == r1.summary

    def test_quote_change_invalidates(self, store):
        """报价变化 → 指纹变化 → 重新计算。"""
        t = _seed(store, price=42.5)
        llm = SpyLLM()
        run_comparison(store, t, llm=llm)
        assert llm.calls == 1
        # 卖家砍到了新价
        q = store.list_quotes("t1")[0]
        q.price = 39.0
        store.save_quote(q)
        r2 = run_comparison(store, t, llm=llm)
        assert llm.calls == 2                      # 指纹失效，重算
        assert any(r["price"] == 39.0 for r in r2.rows)

    def test_force_refresh_bypasses_cache(self, store):
        t = _seed(store)
        llm = SpyLLM()
        run_comparison(store, t, llm=llm)
        r2 = run_comparison(store, t, llm=llm, force_refresh=True)
        assert llm.calls == 2
        assert r2.negotiation_script == "测试话术"

    def test_irrelevant_change_keeps_cache(self, store):
        """不影响比价结论的字段（标题/URL）变化不应失效缓存。"""
        t = _seed(store)
        llm = SpyLLM()
        run_comparison(store, t, llm=llm)
        p = store.list_products("t1")[0]
        p.title = "改了个标题而已"
        store.save_product(p)
        run_comparison(store, t, llm=llm)
        assert llm.calls == 1

    def test_fingerprint_distinguishes_inputs(self):
        t = TargetStyle(id="t1", name="x", reference_price=45.0)
        s = Shop(id="s1", name="店铺A")
        p = Product(id="p1", target_style_id="t1", shop_id="s1", title="x",
                    listed_price=48.0, match_status=MatchStatus.VERIFIED_SAME,
                    match_tier=MatchTier.EXACT)
        q = lambda price: Quote(  # noqa: E731
            id="q1", target_style_id="t1", product_id="p1", shop_id="s1",
            source=QuoteSource.INQUIRY, price=price, tax=TaxStatus.EXCLUDED,
            quoted_at=datetime(2026, 1, 1),
        )
        fp_a = _fingerprint(t, {"p1": p}, [q(42.5)], {"s1": s})
        fp_b = _fingerprint(t, {"p1": p}, [q(39.0)], {"s1": s})
        fp_c = _fingerprint(t, {"p1": p}, [q(42.5)], {"s1": Shop(id="s1", name="改名店")})
        assert fp_a != fp_b
        assert fp_a != fp_c


# ---- 二次复核合并 ----
class TestMergeRecheck:
    def test_same_tier_takes_higher_confidence(self):
        first = {"tier": "equivalent", "confidence": 0.6, "reason": "首次理由"}
        second = {"tier": "equivalent", "confidence": 0.8, "reason": "复核理由"}
        out = merge_recheck(first, second)
        assert out["confidence"] == 0.8
        assert "复核理由" in out["reason"]
        assert "已二次复核" in out["reason"]

    def test_conflict_takes_higher_confidence(self):
        first = {"tier": "exact", "confidence": 0.55, "reason": "判同"}
        second = {"tier": "different", "confidence": 0.9, "reason": "判异"}
        out = merge_recheck(first, second)
        assert out["tier"] == "different"
        assert out["confidence"] == 0.9
        assert "不一致" in out["reason"] and "人工复核" in out["reason"]

    def test_conflict_lower_confidence_discarded(self):
        first = {"tier": "different", "confidence": 0.9, "reason": "判异"}
        second = {"tier": "exact", "confidence": 0.5, "reason": "判同"}
        out = merge_recheck(first, second)
        assert out["tier"] == "different"

    def test_none_confidence_tolerated(self):
        first = {"tier": "equivalent", "confidence": None, "reason": ""}
        second = {"tier": "equivalent", "confidence": 0.7, "reason": ""}
        out = merge_recheck(first, second)
        assert out["confidence"] == 0.7

    def test_recheck_thresholds_sane(self):
        assert 0 < RECHECK_LOW < RECHECK_HIGH < 1
