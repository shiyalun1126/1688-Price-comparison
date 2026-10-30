"""Agent 核心：ReAct 循环（Think → Act → Observe）。

设计要点：
- 用 DeepSeek function calling 做工具选择，不硬编码流程。
- 每轮记录推理轨迹（trace），供前端展示"智能体在想什么"。
- 有最大轮数上限，防止死循环。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx

import app.config  # noqa: F401  触发 .env 加载
from app.llm.usage import USAGE, extract_usage


@dataclass
class ToolSpec:
    """一个可被 Agent 调用的工具。"""

    name: str
    description: str
    parameters: dict[str, Any]
    func: Callable[..., Any]


@dataclass
class AgentStep:
    """Agent 的一轮动作记录。"""

    round: int
    thought: str = ""
    tool: Optional[str] = None
    args: dict = field(default_factory=dict)
    observation: str = ""


@dataclass
class AgentRun:
    """一次 Agent 运行的完整结果。"""

    goal: str
    steps: list[AgentStep] = field(default_factory=list)
    final_answer: str = ""
    finished: bool = False


class ReActAgent:
    """ReAct 智能体：自主规划 → 调工具 → 观察 → 继续。"""

    def __init__(
        self,
        tools: list[ToolSpec],
        system_prompt: str,
        max_rounds: int = 6,
        model: Optional[str] = None,
    ):
        self.tools = {t.name: t for t in tools}
        self.tool_specs = tools
        self.system_prompt = system_prompt
        self.max_rounds = max_rounds
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self.base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _tool_schemas(self) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self.tool_specs
        ]

    def _call_llm(self, messages: list[dict]) -> dict:
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": messages,
                "tools": self._tool_schemas(),
                "tool_choice": "auto",
                "temperature": 0.2,
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        data = resp.json()
        USAGE.add(*extract_usage(data))
        return data["choices"][0]["message"]

    def run(self, goal: str, context: Optional[str] = None) -> AgentRun:
        """执行 Agent 循环。"""
        run = AgentRun(goal=goal)

        if not self.available:
            run.final_answer = "未配置 DEEPSEEK_API_KEY，智能体无法运行。"
            return run

        messages: list[dict] = [{"role": "system", "content": self.system_prompt}]
        user_msg = goal if not context else f"{context}\n\n用户需求：{goal}"
        messages.append({"role": "user", "content": user_msg})

        for rnd in range(1, self.max_rounds + 1):
            try:
                msg = self._call_llm(messages)
            except Exception as e:
                run.final_answer = f"LLM 调用失败：{type(e).__name__}: {e}"
                return run

            tool_calls = msg.get("tool_calls") or []
            content = (msg.get("content") or "").strip()

            # 没有工具调用 → Agent 认为可以收尾
            if not tool_calls:
                step = AgentStep(round=rnd, thought=content[:200])
                run.steps.append(step)
                run.final_answer = content
                run.finished = True
                return run

            # 有工具调用 → 逐个执行
            messages.append({
                "role": "assistant",
                "content": content or None,
                "tool_calls": tool_calls,
            })

            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name")
                raw_args = fn.get("args") or fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    args = {}

                step = AgentStep(round=rnd, thought=content[:200], tool=name, args=args)

                tool = self.tools.get(name)
                if not tool:
                    obs = f"错误：不存在工具 {name}"
                else:
                    try:
                        result = tool.func(**args)
                        obs = json.dumps(result, ensure_ascii=False, default=str)
                    except Exception as e:
                        obs = f"工具执行失败：{type(e).__name__}: {e}"
                    # 观察结果过长会撑爆上下文，截断
                    if len(obs) > 3000:
                        obs = obs[:3000] + "…(已截断)"

                step.observation = obs
                run.steps.append(step)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": obs,
                })

        # 达到轮数上限
        run.final_answer = f"已达到最大轮数 {self.max_rounds}，提前结束。请参考上面各步观察结果。"
        return run