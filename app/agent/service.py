"""Agent 服务：组装工具 + 系统提示，对外提供 run。"""
from __future__ import annotations

from app.agent.react import AgentRun, ReActAgent
from app.agent.tools import build_tools
from app.store.db import Store

SYSTEM_PROMPT = """你是「1688 比价智能体」，帮助采购方在 1688 上找同款货源并比价。

你的工作流程（自主判断，不要机械执行）：
1. 先了解现状：用 list_targets 看有哪些目标款。如果用户提到的目标款能对上，用 inspect_target 看详情。
2. 判断数据是否充足：如果商品数为 0，直接告诉用户需要先导入候选商品（停止，不要编造）。
3. 如果商品还没匹配过（状态多为 candidate），先调用 match_products 做视觉匹配。
4. 匹配完成后，调用 compare_prices 拿比价结论。
5. 如果有特别需要确认报价的商品，可以调用 inquiry_script 生成询价话术。
6. 汇总成结论：告诉用户哪家最便宜、省多少、有什么风险（条件差异），以及下一步建议。

原则：
- 每步先想清楚为什么调用这个工具，再调用。
- 工具报错时，向用户说明并给出补救建议，不要假装成功。
- 结论要具体（带店铺名、价格数字），不要空泛。
- 全程用中文回答。
"""


def run_agent(store: Store, goal: str, target_id: str | None = None) -> dict:
    """执行一次 Agent 运行，返回结果 + 推理轨迹。"""
    tools = build_tools(store)
    agent = ReActAgent(tools=tools, system_prompt=SYSTEM_PROMPT, max_rounds=8)

    context = None
    if target_id:
        t = store.get_target_style(target_id)
        if t:
            context = f"当前用户已选中目标款：{t.name}（ID: {t.id}）。优先从这个目标款开始分析。"

    run: AgentRun = agent.run(goal, context=context)

    return {
        "goal": run.goal,
        "finished": run.finished,
        "final_answer": run.final_answer,
        "steps": [
            {
                "round": s.round,
                "thought": s.thought,
                "tool": s.tool,
                "args": s.args,
                "observation": s.observation,
            }
            for s in run.steps
        ],
    }