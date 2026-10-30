"""多模态视觉比对：调用 deepseek-flash 判断两张服装图是否同款。"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image

import app.config  # noqa: F401  触发 .env 加载

VISION_MODEL = "deepseek-flash"

_SYSTEM = """你是服装货源比对专家。给定两张 1688 商品图，判断它们是否是「同一款货」。
注意服装行业特点：同一款货在不同店铺可能用不同拍摄方式（模特/挂拍/平铺）、不同背景、不同水印、不同角度和光线。

【重要判定原则】
- 拍摄方式差异会导致部分细节被遮挡、看不清、或显得变形。**"看不到某个细节"不等于"没有这个细节"**，
  绝不能因为"图B看不到口袋/标签"就判定为不同款。
- 只有**明确可见且相互矛盾**的证据才算不同款，例如：颜色明显不同、格纹类型明显不同（细格 vs 大格 vs 苏格兰格）、
  版型明显不同（长款 vs 短款、连帽 vs 立领）、明显不同的印花图案。
- 若两图花色、格纹基调、版型、领型基本一致，仅细节可见性/拍摄角度有差异 → 应判为同款。
- 宁可判"同款/等效"，也不要把同款误判成不同款（漏判的代价远大于误判）。

【判定维度（按重要性）】
1. 花色/格纹/图案：底色、主色、格型（细密格/大格/苏格兰格/窗格）、线条组合是否一致
2. 款式/版型：廓形、领型、袖型、门襟
3. 细节设计：口袋、扣子、刺绣、标签（仅作辅助，不可单独作为否定依据）

只输出 JSON，不要解释，格式：
{"same": true|false, "confidence": 0.0-1.0, "tier": "exact"|"equivalent"|"different",
 "reason": "一句话理由", "key_diff": "若不同，指出关键差异（仅限明确可见的矛盾）"}
tier：exact=确定同款；equivalent=相似但未必同版；different=明确不同款。
"""


# 传给视觉模型前的最长边与 JPEG 质量（1688 原图常为 800~1500px、1MB+）
_MAX_SIDE = 768
_JPEG_QUALITY = 85


def _prepare_b64(path: str) -> str:
    """把图片缩放到合理尺寸并压成 JPEG，再转 base64。

    大图直接 base64 会导致负载过大、模型返回空内容。
    """
    img = Image.open(path).convert("RGB")
    w, h = img.size
    longest = max(w, h)
    if longest > _MAX_SIDE:
        ratio = _MAX_SIDE / longest
        img = img.resize((max(1, int(w * ratio)), max(1, int(h * ratio))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def _data_url(path: str) -> str:
    return f"data:image/jpeg;base64,{_prepare_b64(path)}"


def _parse_json(text: str, expect_keys: list[str]) -> dict:
    """健壮解析：去代码围栏、修复常见截断、兜底正则提取。"""
    raw = text.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 2:
            raw = parts[1]
        if raw.lstrip().startswith("json"):
            raw = raw.lstrip()[4:]
        raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # 截断修复：补齐缺失的右括号
    for suffix in ("}", '"}', '"} '):
        try:
            return json.loads(raw + suffix)
        except json.JSONDecodeError:
            continue
    # 正则兜底：逐字段抓
    out: dict = {}
    for k in expect_keys:
        m = re.search(rf'"{k}"\s*:\s*(true|false|"[^"]*"|[0-9.]+)', raw)
        if not m:
            continue
        val = m.group(1)
        if val in ("true", "false"):
            out[k] = val == "true"
        elif val.startswith('"'):
            out[k] = val.strip('"')
        else:
            out[k] = float(val) if "." in val else int(val)
    if not out:
        raise ValueError(f"无法解析视觉模型返回: {text[:200]}")
    return out


def compare_images(
    img_a: str,
    img_b: str,
    model: Optional[str] = None,
    timeout: float = 120.0,
    retries: int = 2,
) -> dict:
    """比对两张图，返回 {same, confidence, tier, reason, key_diff}。

    内置重试：视觉模型对大 b64 负载偶发返回空/截断内容。
    """
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")

    payload = {
        "model": model or VISION_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": "图A（目标款）："},
                {"type": "image_url", "image_url": {"url": _data_url(img_a)}},
                {"type": "text", "text": "图B（候选款）："},
                {"type": "image_url", "image_url": {"url": _data_url(img_b)}},
            ]},
        ],
        "temperature": 0.1,
        "max_tokens": 3000,  # deepseek-flash 会先推理，需留足预算
    }

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            choice = data["choices"][0]
            msg = choice.get("message", {})
            text = (msg.get("content") or "").strip()
            # 思考型模型：content 为空时，答案可能落在 reasoning_content 末尾
            if not text:
                text = (msg.get("reasoning_content") or "").strip()
            parsed = _parse_json(text, ["same", "confidence", "tier", "reason", "key_diff"])
            parsed.setdefault("tier", "different")
            parsed.setdefault("same", parsed.get("tier") != "different")
            parsed.setdefault("confidence", 0.5)
            parsed.setdefault("reason", "")
            return parsed
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"视觉比对失败（已重试 {retries} 次）: {last_err}") from last_err
    raise RuntimeError(f"视觉比对失败: {last_err}")