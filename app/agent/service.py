"""Agent 服务：组装工具 + 系统提示 + 对话记忆，对外提供 run。"""
from __future__ import annotations

import uuid
from datetime import datetime

from app.agent.react import AgentRun, ReActAgent
from app.agent.tools import build_tools
from app.llm.usage import USAGE, UsageCounter, cost_cny
from app.store.db import Store

# 多轮记忆窗口：取同目标款最近 N 轮历史拼入上下文
MEMORY_ROUNDS = 3
# 历史里单条文本的截断长度（防撑爆上下文）
_HIST_GOAL_MAX = 200
_HIST_ANS_MAX = 400

SYSTEM_PROMPT = """你是「1688 比价智能体」，帮助采购方在 1688 上找同款货源并比价。

你的工作流程（自主判断，不要机械执行）：
1. 先了解现状：用 list_targets 看有哪些目标款。如果用户提到的目标款能对上，用 inspect_target 看详情。
2. 判断数据是否充足：如果商品数为 0 或少于 5 个，**优先调用 auto_import**（在用户已登录的
   抓取浏览器里自动搜索并导入候选，快且带真实店铺名和图片）。
   浏览器未运行/未登录时按返回的解决办法引导用户；用户不便操作时降级 plan_search 生成
   搜索链接走人工书签导入。
   （1688 反爬严格，服务端裸爬不可行；抓取浏览器是「接管真实浏览器」的设计，不是裸爬。）
3. 用户贴来商品链接后，调用 import_products 入库，然后按需 match_products。
4. 如果商品还没匹配过（状态多为 candidate），自主调用 match_products 做视觉匹配（增量模式，重复调用无额外开销）。
5. 匹配完成后，调用 compare_prices 拿比价结论。
6. 如果有特别需要确认报价的商品，可以调用 inquiry_script 生成询价话术。
7. 汇总成结论：告诉用户哪家最便宜、省多少、有什么风险（条件差异），以及下一步建议。

长任务规划：
- 用户目标需要多轮才能完成时（如「帮我用最低价采购N件」，涉及等卖家回复、分批核验），
  第一次遇到就用 create_plan 分解为有序步骤并持久化。
- 上下文若已注入【进行中的采购计划】，接续推进未完成步骤，不要重复已完成的工作；
  每完成一个阶段就调用 update_plan_step 更新进度（带一句 note 说明进展）。
- 本次运行做不完的步骤，在结论里明确告诉用户「下一步做什么、等什么」。

原则：
- 有对话历史时注意衔接：用户的问题往往基于上次的结论（比如"第二家呢"），不要当全新问题处理。
- 每步先想清楚为什么调用这个工具，再调用。
- 工具报错时，向用户说明并给出补救建议，不要假装成功。
- 结论要具体（带店铺名、价格数字），不要空泛。
- 全程用中文回答。
"""


def _history_recap(runs) -> str:
    """把最近几轮运行压成一段衔接上下文（旧→新）。"""
    lines = []
    for r in reversed(runs):
        lines.append(f"用户：{r.goal[:_HIST_GOAL_MAX]}")
        lines.append(f"你（上次回答）：{r.final_answer[:_HIST_ANS_MAX]}")
    return "\n".join(lines)


_PLAN_MARK = {"done": "✅", "in_progress": "▶️", "blocked": "⚠️", "pending": "⬜"}


def _plan_recap(plan) -> str:
    """把进行中的采购计划压成一段接续上下文。"""
    done = sum(1 for s in plan.steps if s.get("status") == "done")
    lines = [
        f"【进行中的采购计划】{plan.goal}（进度 {done}/{len(plan.steps)}，plan_id={plan.id}）"
    ]
    for s in plan.steps:
        mark = _PLAN_MARK.get(s.get("status", "pending"), "⬜")
        detail = f" - {s['detail'][:60]}" if s.get("detail") else ""
        note = f"（备注：{s['note'][:60]}）" if s.get("note") else ""
        lines.append(f"{mark} 步骤{s['index']}：{s['action']}{detail}{note}")
    lines.append("（接续推进未完成步骤，不要重复已完成的工作；每完成一步调用 update_plan_step 更新。）")
    return "\n".join(lines)


def run_agent(store: Store, goal: str, target_id: str | None = None) -> dict:
    """执行一次 Agent 运行：带记忆、持久化轨迹、统计 token 用量。"""
    tools = build_tools(store, current_target_id=target_id)
    agent = ReActAgent(tools=tools, system_prompt=SYSTEM_PROMPT, max_rounds=8)

    context = None
    if target_id:
        t = store.get_target_style(target_id)
        if t:
            context = f"当前用户已选中目标款：{t.name}（ID: {t.id}）。优先从这个目标款开始分析。"
        # 多轮记忆：同目标款最近 N 轮
        history = store.list_agent_runs(target_id, limit=MEMORY_ROUNDS)
        if history:
            recap = _history_recap(history)
            context = f"{context}\n\n此前对话记录（供衔接上下文，已完成的结论不要重复输出）：\n{recap}"
        # 长任务规划：进行中的计划注入上下文，让 agent 跨轮次接续推进
        plan = store.get_active_plan(target_id)
        if plan:
            context = f"{context or ''}\n\n{_plan_recap(plan)}"

    before = USAGE.snapshot()
    run: AgentRun = agent.run(goal, context=context)
    usage = UsageCounter.diff(before, USAGE.snapshot())
    cost = cost_cny(usage)

    from app.models.entities import AgentRunRecord

    rec = AgentRunRecord(
        id=f"run-{uuid.uuid4().hex[:12]}",
        target_style_id=target_id,
        goal=goal,
        steps=[
            {
                "round": s.round,
                "thought": s.thought,
                "tool": s.tool,
                "args": s.args,
                "observation": s.observation,
            }
            for s in run.steps
        ],
        final_answer=run.final_answer,
        finished=run.finished,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        created_at=datetime.now(),
    )
    store.save_agent_run(rec)

    # 运行后计划状态（agent 可能刚 create/update 过）
    plan_after = store.get_active_plan(target_id) if target_id else None

    return {
        "run_id": rec.id,
        "goal": run.goal,
        "finished": run.finished,
        "final_answer": run.final_answer,
        "steps": rec.steps,
        "usage": {**usage, "cost_cny": cost},
        "plan": (
            {
                "id": plan_after.id, "goal": plan_after.goal,
                "status": plan_after.status, "steps": plan_after.steps,
            }
            if plan_after else None
        ),
    }
