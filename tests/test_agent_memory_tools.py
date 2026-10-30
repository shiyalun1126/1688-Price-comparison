"""智能体记忆/导入工具/用量统计的单测（无网络、秒级）。"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.agent.service import _history_recap
from app.llm.usage import UsageCounter, cost_cny
from app.models.entities import AgentRunRecord, TargetStyle


# ---- Token 用量统计 ----
class TestUsageCounter:
    def test_add_snapshot_diff(self):
        c = UsageCounter()
        before = c.snapshot()
        c.add(100, 50)
        c.add(10, 5)
        after = c.snapshot()
        assert after["prompt_tokens"] == 110
        assert after["completion_tokens"] == 55
        assert after["requests"] == 2
        d = UsageCounter.diff(before, after)
        assert d == {"prompt_tokens": 110, "completion_tokens": 55, "requests": 2}

    def test_negative_ignored(self):
        c = UsageCounter()
        c.add(-5, -5)
        snap = c.snapshot()
        assert snap["prompt_tokens"] == 0      # 负值不入账
        assert snap["completion_tokens"] == 0

    def test_cost_cny_configurable(self, monkeypatch):
        u = {"prompt_tokens": 1_000_000, "completion_tokens": 500_000}
        monkeypatch.setenv("LLM_INPUT_PRICE_PER_M", "2")
        monkeypatch.setenv("LLM_OUTPUT_PRICE_PER_M", "8")
        assert cost_cny(u) == 6.0              # 2*1 + 8*0.5
        monkeypatch.delenv("LLM_INPUT_PRICE_PER_M")
        monkeypatch.delenv("LLM_OUTPUT_PRICE_PER_M")
        assert cost_cny(u) is None             # 未配单价不折算


# ---- 多轮记忆的历史压缩 ----
class TestHistoryRecap:
    def test_recap_old_to_new_and_truncated(self):
        # list_agent_runs 返回新→旧；recap 应压成旧→新
        runs = [
            AgentRunRecord(id="b", goal="问题二", final_answer="答案二",
                           created_at=datetime(2026, 1, 2)),
            AgentRunRecord(id="a", goal="问题一", final_answer="答" * 500,
                           created_at=datetime(2026, 1, 1)),
        ]
        recap = _history_recap(runs)
        lines = recap.splitlines()
        assert lines[0].startswith("用户：问题一")          # 旧的在前
        assert "问题二" in recap
        # 长回答被截断（500 个"答"截到 400 + 标签里 2 个）
        assert recap.count("答") < 500

    def test_empty_history(self):
        assert _history_recap([]) == ""


# ---- Agent 导入工具 ----
@pytest.fixture
def tool_store(tmp_path):
    from app.store.db import Store

    s = Store(tmp_path / "tools_test.db")
    t = TargetStyle(id="t1", name="测试POLO衫", category="POLO衫")
    s.save_target_style(t)
    yield s
    s.close()


def _build(store):
    from app.agent.tools import build_tools

    tools = {t.name: t.func for t in build_tools(store, current_target_id="t1")}
    return tools


class TestImportProducts:
    def test_degrades_gracefully_when_blocked(self, tool_store, monkeypatch):
        """反爬拦截（请求异常）→ 仍入库为待核验候选，不报错。"""
        import httpx

        monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocked")))
        tools = _build(tool_store)
        r = tools["import_products"](
            ["https://detail.1688.com/offer/123456.html"]
        )
        assert r["导入数"] == 1
        assert len(r["待书签补全"]) == 1
        p = tool_store.get_product("imp-123456")
        assert p is not None
        assert p.match_status.value == "candidate"
        assert p.target_style_id == "t1"

    def test_dedup_by_url_and_offer_id(self, tool_store, monkeypatch):
        import httpx

        monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("blocked")))
        tools = _build(tool_store)
        url = "https://detail.1688.com/offer/999.html"
        tools["import_products"]([url])
        r2 = tools["import_products"]([url, url])
        assert r2["导入数"] == 0
        assert r2["跳过(重复)"] == 2

    def test_accepts_dict_with_title_price(self, tool_store):
        """用户直接给 {url,title,price} 时数据齐全，不需要抓取。"""
        tools = _build(tool_store)
        r = tools["import_products"]([
            {"url": "https://detail.1688.com/offer/777.html",
             "title": "纯棉POLO衫", "price": 39.9},
        ])
        assert r["导入数"] == 1
        assert r["待书签补全"] == []
        p = tool_store.get_product("imp-777")
        assert p.title == "纯棉POLO衫"
        assert p.listed_price == 39.9


class TestSearchLink:
    def test_link_contains_keywords(self, tool_store):
        tools = _build(tool_store)
        r = tools["make_search_link"]()
        assert "s.1688.com/selloffer" in r["搜索链接"]
        assert "关键词" in r
