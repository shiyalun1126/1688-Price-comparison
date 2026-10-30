"""LLM 客户端：DeepSeek（OpenAI 兼容协议）可插拔。

无 API key 时降级为规则话术/正则解析，保证离线可跑通链路。
"""
from __future__ import annotations

import os
from typing import Optional

import httpx

import app.config  # noqa: F401  触发 .env 加载
from app.llm.usage import USAGE, extract_usage


class LLMClient:
    """极简 DeepSeek Chat 客户端（token 用量自动汇入全局计数器）。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self.base_url = (base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip("/")
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def chat(self, messages: list[dict], temperature: float = 0.3, max_tokens: int = 512) -> str:
        if not self.available:
            raise RuntimeError("DEEPSEEK_API_KEY 未配置")
        url = f"{self.base_url}/chat/completions"
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        data = resp.json()
        USAGE.add(*extract_usage(data))
        return data["choices"][0]["message"]["content"]


def get_client() -> LLMClient:
    return LLMClient()