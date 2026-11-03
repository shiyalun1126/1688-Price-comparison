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
from app.llm.usage import USAGE, extract_usage

# 视觉模型可通过环境变量覆盖：LLM_VISION_MODEL > LLM_MODEL > DEEPSEEK_VISION_MODEL；
# 默认 deepseek-flash：官方推理型多模态模型，content 可能为空、答案在 reasoning_content
VISION_MODEL = (os.getenv("LLM_VISION_MODEL", "")
                or os.getenv("DEEPSEEK_VISION_MODEL", "")
                or os.getenv("LLM_MODEL", "")
                or "deepseek-flash")

# 品类 → 比对维度提示（按目标款 category 注入；未命中用通用维度）。
# 用「关键词组」匹配而非全等：category 常写成「蓝牙耳机」「POLO衫」等自然说法。
# 顺序即优先级：家电/数码在前，避免「洗衣机」被服装的「衣」误抢。
_CATEGORY_HINTS: list[tuple[tuple[str, ...], str]] = [
    (("家电", "电器", "洗衣", "冰箱", "空调"),
     "家电类重点看：品类与形态、容量/尺寸档位、配色、控制面板与按键布局、品牌标识、配件组成。"),
    (("数码", "电子", "耳机", "3C", "音响", "手表", "手机", "相机"),
     "数码类重点看：产品品类与形态（入耳式/头戴式、机型代际）、外观结构与配色、充电盒/配件形态、"
     "接口与按键布局、品牌标识。注意区分本体与保护套/配件。"),
    (("服装", "服饰", "衫", "衣", "裙", "裤", "外套", "卫衣"),
     "服装类重点看：花色/格纹/图案、款式版型（廓形/长短）、领型袖型、门襟结构、面料观感（厚薄/织纹）。"),
    (("鞋", "靴"),
     "鞋类重点看：鞋型（鞋头/鞋帮高低）、配色与图案、鞋底造型、材质观感、鞋带/闭合方式。"),
    (("包", "箱包"),
     "箱包类重点看：包型（尺寸/廓形）、配色与图案、五金件、开合方式、材质观感、背带/提手结构。"),
    (("美妆", "护肤", "口红", "面膜"),
     "美妆类重点看：品类（膏体/液体/粉状）、包装容器造型与配色、容量规格、品牌标识、图案设计。"),
    (("食品", "零食", "茶叶", "生鲜"),
     "食品类重点看：食品品类与形态、包装容器造型与配色、净含量规格、品牌标识、口味/系列变体。"),
]
_DEFAULT_HINT = "通用维度：商品品类与整体形态、外观结构、颜色配色、图案/印花、品牌标识、规格档位、配件组成。"

# 二次复核指令：换独立视角重新判定，避免受「已有一个结论」的锚定影响
_RECHECK_INSTRUCTION = (
    "【复核判定】这是对同款判定的独立复核。请抛开任何先前结论，"
    "逐项核对：①是否同一品类同一形态；②外观结构是否一致；③颜色配色是否一致；"
    "④有无明确可见的矛盾证据。核对完成后再给出判定，不要猜测倾向。"
)

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
    """通用核心 + 品类维度提示。category 为空或未命中关键词组时用通用维度。"""
    hint = None
    if category:
        for keys, h in _CATEGORY_HINTS:
            if any(k in category for k in keys):
                hint = h
                break
    return _CORE_SYSTEM + "\n\n【本品类提示】\n" + (hint or _DEFAULT_HINT)


# 传给视觉模型前的最长边与 JPEG 质量（1688 原图常为 800~1500px、1MB+）
_MAX_SIDE = 768
_JPEG_QUALITY = 85

# 低置信度灰区：判定落在此区间时做二次复核（temperature=0.1 下边界商品仍有抖动，
# 首判置信度低说明模型自己也不确定，换视角重判一次可显著收敛）
RECHECK_LOW = 0.45
RECHECK_HIGH = 0.75


def merge_recheck(first: dict, second: dict) -> dict:
    """合并首判与复核结果（纯函数，便于单测）。

    tier 一致 → 取置信度高者的理由，置信度取两者较大值，并标注已复核；
    tier 不一致 → 取置信度高者，理由中显式标注分歧（下游可据此人工复核）。
    """
    c1 = float(first.get("confidence", 0.5) or 0)
    c2 = float(second.get("confidence", 0.5) or 0)
    out = dict(first if c1 >= c2 else second)
    if first.get("tier") == second.get("tier"):
        out["confidence"] = max(c1, c2)
        out["reason"] = f"{out.get('reason', '')}（已二次复核）"
    else:
        out["reason"] = (
            f"{out.get('reason', '')}"
            f"（二次复核结论不一致：{first.get('tier')}/{second.get('tier')}，"
            f"取置信度高者，建议人工复核）"
        )
    return out


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


_VISION_KEYS = ["same", "confidence", "tier", "reason", "key_diff"]

# 输出格式强约束：部分推理型模型会无视 system 里的"只输出 JSON"，把结论写成散文，
# 故在用户消息末尾再压一次，并在解析失败时用一次纯文本调用做格式修复。
_JSON_ONLY_HINT = (
    "【判定口径，务必遵守】先找「明确可见且相互矛盾」的证据，只有下面这些才算不同款：\n"
    "品类/形态不同、配色明显不同、图案或格纹本身明显不同、结构或组件明显不同。\n"
    "拍摄方式（模特/挂拍/平铺/场景）、角度、光线、背景、水印、是否多色合集展示、局部被遮挡看不清"
    "——这些都**不算**差异。\n"
    "只要上述硬指标基本一致，就必须判 same=true、tier=exact 或 equivalent；"
    "拿不准时倾向同款，不要因为细节看不清就判 different。\n"
    "直接输出 JSON 对象，第一个字符必须是 {，不要 Markdown、不要标题、不要解释。"
    '字段：{"same":true|false,"confidence":0.0-1.0,'
    '"tier":"exact"|"equivalent"|"different","reason":"一句话理由","key_diff":"关键差异"}'
)


def _repair_json(text: str, api_key: str, base_url: str, model: str, timeout: float) -> dict:
    """把模型输出的散文/列表改写成严格 JSON（纯文本二次调用，不重复传图）。"""
    resp = httpx.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": "你只做格式转换：把给定的商品比对分析改写成严格 JSON，"
                                              "保留原结论（same/tier/confidence 不得改变语义）。"},
                {"role": "user", "content": f"输出字段：{_JSON_ONLY_HINT}\n\n待转换的分析：\n{text[:4000]}"},
            ],
            "temperature": 0.0,
            "max_tokens": 800,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    USAGE.add(*extract_usage(data))
    msg = data["choices"][0].get("message", {})
    out = (msg.get("content") or "").strip() or (msg.get("reasoning_content") or "").strip()
    return _parse_json(out, _VISION_KEYS)


def compare_images(
    img_a: str,
    img_b: str,
    category: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = 120.0,
    retries: int = 2,
    recheck: bool = False,
) -> dict:
    """比对两张图，返回 {same, confidence, tier, reason, key_diff}。

    category：目标款品类（如「蓝牙耳机」「POLO衫」），用于注入品类比对维度；
    为空时用通用维度。
    recheck：复核模式，换一套独立视角的问法再判定一次（用于低置信度二次复核，
    与首次判定交叉验证，收敛边界案例的抖动）。

    内置重试：视觉模型对大 b64 负载偶发返回空/截断内容。
    """
    api_key = os.getenv("LLM_API_KEY", "") or os.getenv("DEEPSEEK_API_KEY", "")
    base_url = (os.getenv("LLM_BASE_URL", "")
                or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip("/")
    if not api_key:
        raise RuntimeError("LLM_API_KEY（或 DEEPSEEK_API_KEY）未配置")

    user_blocks = [
        {"type": "text", "text": "图A（目标款）："},
        {"type": "image_url", "image_url": {"url": _data_url(img_a)}},
        {"type": "text", "text": "图B（候选款）："},
        {"type": "image_url", "image_url": {"url": _data_url(img_b)}},
    ]
    if recheck:
        user_blocks.insert(0, {"type": "text", "text": _RECHECK_INSTRUCTION})
    user_blocks.append({"type": "text", "text": _JSON_ONLY_HINT})

    payload = {
        "model": model or VISION_MODEL,
        "messages": [
            {"role": "system", "content": build_system_prompt(category)},
            {"role": "user", "content": user_blocks},
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
            try:
                parsed = _parse_json(text, _VISION_KEYS)
            except ValueError:
                # 模型无视 JSON 约束、输出了散文 → 追加一次格式修复调用
                parsed = _repair_json(text, api_key, base_url, payload["model"], timeout)
            parsed.setdefault("tier", "different")
            parsed.setdefault("same", parsed.get("tier") != "different")
            parsed.setdefault("confidence", 0.5)
            parsed.setdefault("reason", "")
            USAGE.add(*extract_usage(data))
            return parsed
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"视觉比对失败（已重试 {retries} 次）: {last_err}") from last_err
    raise RuntimeError(f"视觉比对失败: {last_err}")