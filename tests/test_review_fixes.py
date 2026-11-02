"""外部评审问题修复的回归测试（无网络、秒级）。

覆盖：¥0 伪报价不参与比价 / 阶梯价正则 / 确定性兜底 id / 删除目标款级联清理 /
当前目标款持久化。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.comparison.comparison import _merge_rows
from app.inquire.inquiry import _parse_rules
from app.models.entities import Product, Quote, Shop, TargetStyle
from app.models.enums import MatchStatus, MatchTier, QuoteSource


def _target() -> TargetStyle:
    return TargetStyle(id="t1", name="测试款", category="POLO衫")


def _product(pid: str, price: float | None, status=MatchStatus.VERIFIED_SAME,
             tier=MatchTier.EXACT) -> Product:
    return Product(
        id=pid, target_style_id="t1", shop_id=f"s-{pid}", title=f"商品{pid}",
        listed_price=price, match_status=status, match_tier=tier,
    )


def _quote(qid: str, pid: str, price: float, minutes_ago: int = 5) -> Quote:
    return Quote(
        id=qid, target_style_id="t1", product_id=pid, shop_id=f"s-{pid}",
        source=QuoteSource.INQUIRY, price=price,
        quoted_at=datetime.now() - timedelta(minutes=minutes_ago),
    )


class TestZeroPriceQuote:
    """寒暄回复解析出 price=0 时不得顶掉页面标价（评审 P0-1）。"""

    def test_zero_quote_does_not_override_listed(self):
        p = _product("p1", 48.0)
        shops = {"s-p1": Shop(id="s-p1", name="店铺A")}
        rows = _merge_rows([p], [_quote("q0", "p1", 0.0)], shops)
        assert len(rows) == 1
        assert rows[0]["price"] == 48.0
        assert rows[0]["price_source"] == "listed"

    def test_zero_quote_row_not_comparable(self):
        from app.comparison.comparison import _quote_row
        p = _product("p1", None)
        row = _quote_row(_quote("q0", "p1", 0.0), p, Shop(id="s-p1", name="A"))
        assert row["comparable"] is False

    def test_valid_quote_still_wins(self):
        p = _product("p1", 48.0)
        shops = {"s-p1": Shop(id="s-p1", name="店铺A")}
        rows = _merge_rows([p], [_quote("q1", "p1", 42.5)], shops)
        assert rows[0]["price"] == 42.5
        assert rows[0]["price_source"] == "quoted"


class TestTierPriceRules:
    """正则兜底解析阶梯价（评审长文 #5）。"""

    def test_ladder_price_parsed(self):
        r = _parse_rules("满100件4元，500件以上3.5元")
        assert r["tiers"] == [{"qty": 100, "price": 4.0}, {"qty": 500, "price": 3.5}]

    def test_no_tier_in_plain_reply(self):
        r = _parse_rules("好的，稍等帮您查一下")
        assert r["tiers"] == []
        assert r["price"] is None


class TestDeterministicOfferId:
    """非 offer 链接兜底 id 必须跨进程稳定（评审 P1-6，内建 hash 每次进程随机）。"""

    def test_uuid5_stable_across_calls(self):
        import uuid
        url = "https://m.1688.com/pages?h=658f2"
        a = str(uuid.uuid5(uuid.NAMESPACE_URL, url).int % 10**10)
        b = str(uuid.uuid5(uuid.NAMESPACE_URL, url).int % 10**10)
        assert a == b


class TestDeleteCascadeAndSetting:
    """删除目标款级联清 agent_run/agent_plan；当前目标款持久化（评审 P1-5）。"""

    def test_delete_cascade_and_setting(self, tmp_path):
        from app.store.db import Store
        store = Store(tmp_path / "t.db")
        t = _target()
        store.save_target_style(t)
        store.save_shop(Shop(id="s1", name="店"))
        store.save_product(_product("p1", 10.0))
        store.save_agent_run(type("R", (), {
            "id": "r1", "target_style_id": "t1", "goal": "g", "steps": [],
            "final_answer": "", "finished": True,
            "prompt_tokens": 0, "completion_tokens": 0,
            "created_at": datetime.now(),
        })())
        store.set_setting("current_target_id", "t1")

        counts = store.delete_target_style("t1")
        assert counts["product"] == 1
        assert counts["agent_run"] == 1
        assert counts["agent_plan"] == 0  # 没建过计划，删除不报错即可
        assert store.list_products("t1") == []
        # 当前目标款指针仍在（由路由层负责清空），kv 表本身可用
        assert store.get_setting("current_target_id") == "t1"
        store.close()

    def test_setting_roundtrip(self, tmp_path):
        from app.store.db import Store
        store = Store(tmp_path / "s.db")
        assert store.get_setting("k") is None
        store.set_setting("k", "v1")
        store.set_setting("k", "v2")  # upsert
        assert store.get_setting("k") == "v2"
        store.close()
