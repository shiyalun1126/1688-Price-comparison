"""长任务规划（agent_plan）与 match_products 增量语义的单测（无网络、秒级）。"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.models.entities import Product, Shop, TargetStyle
from app.models.enums import MatchStatus


@pytest.fixture
def tool_store(tmp_path):
    from app.store.db import Store

    s = Store(tmp_path / "plan_test.db")
    s.save_target_style(TargetStyle(id="t1", name="测试POLO衫", category="POLO衫"))
    yield s
    s.close()


def _build(store):
    from app.agent.tools import build_tools

    tools = {t.name: t.func for t in build_tools(store, current_target_id="t1")}
    return tools


# ---- Store 层：计划的持久化与唯一性 ----
class TestPlanStore:
    def test_save_and_get_active(self, tool_store):
        from app.models.entities import AgentPlanRecord

        rec = AgentPlanRecord(
            id="p1", target_style_id="t1", goal="低价采购50件",
            steps=[{"index": 0, "action": "核验", "status": "pending"}],
        )
        tool_store.save_agent_plan(rec)
        got = tool_store.get_active_plan("t1")
        assert got is not None
        assert got.goal == "低价采购50件"
        assert got.steps[0]["action"] == "核验"

    def test_new_plan_supersedes_old_active(self, tool_store):
        from app.models.entities import AgentPlanRecord

        tool_store.save_agent_plan(AgentPlanRecord(id="p1", target_style_id="t1", goal="旧计划"))
        tool_store.save_agent_plan(AgentPlanRecord(id="p2", target_style_id="t1", goal="新计划"))
        got = tool_store.get_active_plan("t1")
        assert got.id == "p2"

    def test_no_active_returns_none(self, tool_store):
        assert tool_store.get_active_plan("t1") is None


# ---- 工具层：create / get / update ----
class TestPlanTools:
    def test_create_plan_normalizes_steps(self, tool_store):
        tools = _build(tool_store)
        r = tools["create_plan"](
            "用最低价采购50件", ["核验候选", {"action": "询价", "detail": "对最便宜3家"}],
        )
        assert r["步骤数"] == 2
        steps = r["计划"]
        assert steps[0]["status"] == "pending"
        assert steps[1]["detail"] == "对最便宜3家"
        # 落库可查
        assert tool_store.get_active_plan("t1") is not None

    def test_create_plan_rejects_empty(self, tool_store):
        tools = _build(tool_store)
        assert "error" in tools["create_plan"]("目标", [])

    def test_update_step_progress_and_close(self, tool_store):
        tools = _build(tool_store)
        tools["create_plan"]("目标", ["步骤A", "步骤B"])
        r1 = tools["update_plan_step"](0, status="done", note="完成A")
        assert r1["总进度"] == "1/2"
        assert r1["计划状态"] == "active"
        r2 = tools["update_plan_step"](1, status="done")
        assert r2["总进度"] == "2/2"
        assert r2["计划状态"] == "done"          # 全部完成 → 计划关闭
        assert tool_store.get_active_plan("t1") is None

    def test_update_invalid_index(self, tool_store):
        tools = _build(tool_store)
        tools["create_plan"]("目标", ["唯一步骤"])
        r = tools["update_plan_step"](5)
        assert "error" in r

    def test_update_invalid_status_falls_back(self, tool_store):
        tools = _build(tool_store)
        tools["create_plan"]("目标", ["步骤A"])
        r = tools["update_plan_step"](0, status="乱写的")
        assert r["已更新"]["status"] == "done"   # 非法状态回落 done

    def test_get_plan_empty(self, tool_store):
        tools = _build(tool_store)
        assert tools["get_plan"]().get("无进行中的计划") is True


# ---- match_products 增量语义 ----
class TestMatchIncremental:
    def _add_products(self, store):
        from datetime import datetime

        store.save_shop(Shop(id="s1", name="测试店"))
        # 已核验（有图+已判定+有匹配时间）→ 增量模式应跳过
        store.save_product(Product(
            id="p_done", target_style_id="t1", shop_id="s1", title="已核验商品",
            image_paths=["x.jpg"], match_status=MatchStatus.REJECTED,
            matched_at=datetime(2026, 1, 1),
        ))
        # 未核验候选 → 应参与匹配
        store.save_product(Product(
            id="p_new", target_style_id="t1", shop_id="s1", title="新候选商品",
            match_status=MatchStatus.CANDIDATE,
        ))

    def test_incremental_skips_verified(self, tool_store):
        self._add_products(tool_store)
        tools = _build(tool_store)
        r = tools["match_products"]("t1")
        assert r["本次新匹配"] == 1          # 只有 p_new
        assert r["已复用"] == 1              # p_done 被复用

    def test_force_remaches_all(self, tool_store):
        self._add_products(tool_store)
        tools = _build(tool_store)
        r = tools["match_products"]("t1", force=True)
        assert r["本次新匹配"] == 2
        assert r["已复用"] == 0

    def test_all_verified_short_circuits(self, tool_store):
        from datetime import datetime

        tool_store.save_shop(Shop(id="s2", name="店2"))
        tool_store.save_product(Product(
            id="p_only", target_style_id="t1", shop_id="s2", title="唯一已核验",
            image_paths=["y.jpg"], match_status=MatchStatus.VERIFIED_SAME,
            matched_at=datetime(2026, 1, 1),
        ))
        tools = _build(tool_store)
        r = tools["match_products"]("t1")
        assert r["本次新匹配"] == 0
        assert r["已复用"] == 1
        assert "提示" in r


class TestImagePathResolution:
    """matcher._resolve_img：兼容绝对路径与 /uploads 相对路径。"""

    def test_relative_uploads_resolves_to_project_data_dir(self):
        from pathlib import Path as _P

        from app.matcher.matcher import Matcher

        root = _P(__file__).resolve().parents[1]
        got = Matcher._resolve_img("/uploads/abc.webp")
        assert got == root / "data" / "uploads" / "abc.webp"

    def test_absolute_path_passthrough(self):
        from app.matcher.matcher import Matcher
        s = r"C:\some\dir\img.jpg"
        assert str(Matcher._resolve_img(s)) == s

    def test_windows_drive_forward_slash_passthrough(self):
        from app.matcher.matcher import Matcher
        s = "D:/some/dir/img.jpg"
        # Windows Path 会把正斜杠规范化为反斜杠，只要求指向同一文件
        assert Matcher._resolve_img(s).drive == "D:"
        assert Matcher._resolve_img(s).name == "img.jpg"
