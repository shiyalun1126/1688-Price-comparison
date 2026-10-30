"""多模态视觉比对：判断两张商品图是否同款（品类自适应，覆盖服装/数码/百货等）。

设计要点：
- 提示词分两层：品类无关的通用判定核心（同款判定原则、拍摄差异容忍、
  明确矛盾才否决）+ 按目标品类注入的比对维度提示。
- 实测：纯服装专用提示词在数码品类上也能工作（模型会自行推断维度），
  但显式注入品类维度可提升稳定性与理由质量。
"""
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

# 品类 → 比对维度提示（按目标款 category 注入；未命中用通用维度）
_CATEGORY_HINTS: dict[str, str] = {
    "服装": "服装类重点看：花色/格纹/图案、款式版型（廓形/长短）、领型袖型、门襟结构、面料观感（厚薄/织纹）。",
    "服饰": "服装类重点看：花色/格纹/图案、款式版型（廓形/长短）、领型袖型、门襟结构、面料观感（厚薄/织纹）。",
    "数码": "数码类重点看：产品品类与形态（入耳式/头戴式、机型代际）、外观结构与配色、充电盒/配件形态、接口与按键布局、品牌标识。注意区分本体与保护套/配件。",
    "鞋": "鞋类重点看：鞋型（鞋头/鞋帮高低）、配色与图案、鞋底造型、材质观感、鞋带/闭合方式。",
    "包": "箱包类重点看：包型（尺寸/廓形）、配色与图案、五金件、开合方式、材质观感、背带/提手结构。",
    "家电": "家电类重点看：品类与形态、容量/尺寸档位、配色、控制面板与按键布局、品牌标识、配件组成。",
    "美妆": "美妆类重点看：品类（膏体/液体/粉状）、包装容器造型与配色、容量规格、品牌标识、图案设计。",
    "食品": "食品类重点看：食品品类与形态、包装容器造型与配色、净含量规格、品牌标识、口味/系列变体。",
}
_DEFAULT_HINT = "通用维度：商品品类与整体形态、外观结构、颜色配色、图案/印花、品牌标识、规格档位、配件组成。"

_CORE_SYSTEM = """你是货源比对专家。给定两张同平台商品图，判断它们是否是「同一款货」。
注意：同一款货在不同店铺可能用不同拍摄方式（模特/挂拍/平铺/场景）、不同背景、不同水印、不同角度和光线，或一张是供货示意图/多型号合集图。

【重要判定原则】
- 拍摄方式差异会导致部分细节被遮挡、看不清、或显得变形。**"看不到某个细节"不等于"没有这个细节"**，
  绝不能因为"图B看不到某细节"就判定为不同款。
- 只有**明确可见且相互矛盾**的证据才算不同款，例如：品类/形态明显不同（耳机 vs 耳机保护套、
  长款 vs 短款）、颜色明显不同、型号代际明显不同、图案明显不同。
- 若两图在品类、整体外观、结构、配色上基本一致，仅细节可见性/拍摄角度/是否多色展示有差异 → 应判为同款。
- 宁可判"同款/等效"，也不要把同款误判成不同款（漏判的代价远大于误判）。

【判定维度（按重要性）】
1. 品类与形态：是否同一类商品、同一形态（这是硬门槛，品类不同直接 different）
2. 外观结构：造型、版型/机型、组件构成是否一致
3. 颜色配色与图案：底色、主色、印花/格纹是否一致
4. 细节设计：口袋/扣子/按键/接口/刺绣/标签（仅作辅助，不可单独作为否定依据）

只输出 JSON，不要解释，格式：
{"same": true|false, "confidence": 0.0-1.0, "tier": "exact"|"equivalent"|"different",
 "reason": "一句话理由", "key_diff": "若不同，指出关键差异（仅限明确可见的矛盾）"}
tier：exact=确定同款；equivalent=相似但未必同版；different=明确不同款。"""


def build_system_prompt(category: Optional[str] = None) -> str:
    """通用核心 + 品类维度提示。category 为空或用默认维度。"""
    if not category:
        return _CORE_SYSTEM + "\n\n【本品类提示】\n" + _DEFAULT_HINT
    # 支持「蓝牙耳机」「3C数码」等含关键字的写法：取命中的提示
    hint = None
    for key, h in _CATEGORY_HINTS.items():
        if key in category:
            hint = h
            break
    return _CORE_SYSTEM + "\n\n【本品类提示】\n" + (hint or _DEFAULT_HINT)


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
    category: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = 120.0,
    retries: int = 2,
) -> dict:
    """比对两张图，返回 {same, confidence, tier, reason, key_diff}。

    category：目标款品类（如「蓝牙耳机」「POLO衫」），用于注入品类比对维度；
    为空时用通用维度。

    内置重试：视觉模型对大 b64 负载偶发返回空/截断内容。
    """
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")

    payload = {
        "model": model or VISION_MODEL,
        "messages": [
            {"role": "system", "content": build_system_prompt(category)},
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