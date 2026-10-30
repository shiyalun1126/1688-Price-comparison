"""智能体 E2E（LLM 调用，标记 slow）：自主完成 了解现状→比价→汇总 闭环。"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.slow


def test_agent_autonomous_run(app_client, hoodie_target_id):
    r = app_client.post("/api/agent/run", json={
        "goal": "帮我分析一下这个款哪家最便宜，给出采购建议",
        "target_id": hoodie_target_id,
    }, timeout=300).json()

    assert r["finished"] is True
    steps = r["steps"]
    tools_called = [s["tool"] for s in steps if s.get("tool")]
    assert len(tools_called) >= 2, f"工具调用过少: {tools_called}"
    # 有工具调用的轮次都必须带观察结果
    assert all(s.get("observation") for s in steps if s.get("tool"))

    answer = r["final_answer"]
    assert answer.strip(), "结论为空"
    # 结论要具体：含店铺名或价格数字
    assert any(n in answer for n in ("义乌", "广州", "杭州", "店", "司")) or \
           any(ch.isdigit() for ch in answer)
