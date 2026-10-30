"""Token 用量统计：进程级累计 + 按需快照。

三个调用点都汇入全局计数器：
- LLMClient.chat（询价话术 / 比价议价话术 / 报价解析）
- ReActAgent._call_llm（智能体每轮推理）
- vision.compare_images（多模态视觉比对，也是钱）

用法：
    from app.llm.usage import USAGE
    before = USAGE.snapshot(); ...; after = USAGE.snapshot()
    delta = USAGE.diff(before, after)
"""
from __future__ import annotations

import os
import threading

import app.config  # noqa: F401  触发 .env 加载（读单价配置）


class UsageCounter:
    """线程安全的 token 计数器。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._prompt = 0
        self._completion = 0
        self._requests = 0

    def add(self, prompt_tokens: int, completion_tokens: int) -> None:
        with self._lock:
            self._prompt += max(0, prompt_tokens)
            self._completion += max(0, completion_tokens)
            self._requests += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "prompt_tokens": self._prompt,
                "completion_tokens": self._completion,
                "requests": self._requests,
            }

    def reset(self) -> None:
        with self._lock:
            self._prompt = self._completion = self._requests = 0

    @staticmethod
    def diff(before: dict, after: dict) -> dict:
        """两次快照之差（一次运行/一段代码的用量）。"""
        return {
            "prompt_tokens": after["prompt_tokens"] - before["prompt_tokens"],
            "completion_tokens": after["completion_tokens"] - before["completion_tokens"],
            "requests": after["requests"] - before["requests"],
        }


def cost_cny(usage: dict) -> float | None:
    """按配置单价折算人民币（元）。单价未配置（<=0）时返回 None。

    单价为「元 / 百万 tokens」，从环境变量读取（LLM_INPUT_PRICE_PER_M /
    LLM_OUTPUT_PRICE_PER_M）。DeepSeek 牌价会调整，由使用者自行更新。
    """
    in_price = float(os.getenv("LLM_INPUT_PRICE_PER_M", "0") or 0)
    out_price = float(os.getenv("LLM_OUTPUT_PRICE_PER_M", "0") or 0)
    if in_price <= 0 and out_price <= 0:
        return None
    cost = (
        usage.get("prompt_tokens", 0) / 1_000_000 * in_price
        + usage.get("completion_tokens", 0) / 1_000_000 * out_price
    )
    return round(cost, 4)


def extract_usage(data: dict) -> tuple[int, int]:
    """从 OpenAI 兼容响应体里取 usage，取不到返回 (0, 0)。"""
    u = data.get("usage") or {}
    try:
        return int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
    except (TypeError, ValueError):
        return 0, 0


# 进程级全局计数器
USAGE = UsageCounter()
