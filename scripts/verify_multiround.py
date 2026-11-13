"""离线重放一段真实卖家对话，验证多轮决策链（不联网、不发消息、不落库）。

用途：把真实会话里发生过的回复按顺序喂给 decide()，看每一轮的缺口判断、
话术产出和收敛动作是否合理。改完谈判逻辑先跑这个，比直接上真实账号安全。

用法：
    D:/miniconda3/python.exe scripts/verify_multiround.py
    D:/miniconda3/python.exe scripts/verify_multiround.py --session s-xxxxxxxx
    D:/miniconda3/python.exe scripts/verify_multiround.py --no-llm     # 只看规则模板

--session 会从库里取该会话的卖家原话按序重放；不传则用内置的沃乐克案例。
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.inquire.dialogue import (  # noqa: E402
    DialogueConfig,
    compute_gaps,
    decide,
    merge_quote,
    pick_market_min,
)
from app.inquire.inquiry import looks_like_chat_message, parse_quote  # noqa: E402
from app.llm.client import LLMClient  # noqa: E402
from app.models.entities import InquirySession, Quote  # noqa: E402
from app.models.enums import MsgRole, StockStatus, TaxStatus  # noqa: E402
from app.store.db import Store  # noqa: E402

# 2026-11-06 沃乐克真实对话（原话，未改写）
CASE = [
    "我在看这个商品 700ML纹喷雾水杯高颜值冰裂大容量运动吸管运动陶瓷不锈钢杯单环 ￥56.90",
    "这款是有现货在的 支持1个起批",
    "需要看您的地址运费 商品是含税的价格 如果需要开专票还要+10个点",
    "有现货需要包装 具体是需要看您需要的数量才能确认多久发货时间",
    "200件的话48一个 现货3天内可以发出 专票要加10个点",
    "最低47.5 再低真做不了",
]


class _NoLLM(LLMClient):
    available = False


def _load(session_id: str):
    st = Store(Path(__file__).resolve().parents[1] / "data" / "app.db")
    s = st.get_session(session_id)
    if not s:
        raise SystemExit(f"会话不存在：{session_id}")
    target = st.get_target_style(s.target_style_id)
    product = st.get_product(s.product_id)
    texts = [m.text for m in st.list_messages(s.id)
             if m.role == MsgRole.SELLER]
    others = [q for q in st.list_quotes(s.target_style_id)]
    market = pick_market_min(others, exclude_shop_id=s.shop_id,
                             exclude_quote_id=f"q-{s.id}")
    st.close()
    return s, target, product, texts, market, others


def _fallback_case():
    """不连库时的最小上下文：用真实报价里的市场价做锚。"""
    from app.models.entities import Product, TargetStyle
    target = TargetStyle(id="t1", name="700ML喷雾运动水杯", reference_price=None)
    product = Product(id="p1", target_style_id="t1", shop_id="sh1",
                      title="700ML纹喷雾水杯 高颜值冰裂 运动吸管杯")
    s = InquirySession(id="s-demo", inquiry_id="i1", target_style_id="t1",
                       product_id="p1", shop_id="sh1", buyer_quantity=200)
    return s, target, product, CASE, 57.6, []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--qty", type=int, default=0, help="覆盖采购量")
    args = ap.parse_args()

    if args.session:
        s, target, product, texts, market, _ = _load(args.session)
    else:
        s, target, product, texts, market, _ = _fallback_case()
    if args.qty:
        s.buyer_quantity = args.qty

    cfg = DialogueConfig.from_env()
    llm = _NoLLM() if args.no_llm else LLMClient()
    print(f"目标款：{target.name}")
    print(f"采购量：{s.buyer_quantity or '未填'}　市场最低（排除本店）：{market}")
    print(f"轮次上限：{cfg.max_rounds}　单轮最多问：{cfg.max_gaps_per_turn} 项")
    print("=" * 72)

    s.round = 1
    s.last_speaker = MsgRole.SELLER
    s.expires_at = datetime.now() + timedelta(hours=cfg.expire_hours)
    base: Quote | None = None
    history: list[str] = []

    for i, seller_text in enumerate(texts, 1):
        # 每轮都是「卖家刚说完」的状态，否则会被判定成等待回复
        s.last_speaker = MsgRole.SELLER
        patch = parse_quote(seller_text, target, product, llm=_NoLLM())
        has = any([patch.price, patch.moq is not None, patch.tiers,
                   patch.tax != TaxStatus.UNKNOWN,
                   patch.includes_shipping is not None,
                   patch.stock_status != StockStatus.UNKNOWN,
                   patch.lead_time_hours is not None])
        if has:
            base = merge_quote(base, patch)
        print(f"\n[第 {i} 条卖家消息] {seller_text}")
        if has:
            print(f"  解析 → 价 {base.price or '—'} / 起订 {base.moq} / "
                  f"{'含税' if base.tax == TaxStatus.INCLUDED else '税未知'} / "
                  f"运费{'已含' if base.includes_shipping else '另算' if base.includes_shipping is False else '未知'} / "
                  f"现货{'是' if base.stock_status == StockStatus.IN_STOCK else '未知'} / "
                  f"时效 {base.lead_time_hours or '—'}h / 阶梯 {base.tiers or '—'}")
        else:
            print("  解析 → 无有效信息（视为闲聊）")

        gaps = compute_gaps(base, cfg.required, quantity=s.buyer_quantity)
        print(f"  缺口 → {'、'.join(gaps) or '无'}")

        d = decide(session=s, quote=base, seller_text=seller_text,
                   market_min=market, target=target, product=product,
                   cfg=cfg, llm=llm, history=history)
        print(f"  决策 → {d.action}　（{d.reason}）")
        if d.text:
            ok = looks_like_chat_message(d.text)
            print(f"  话术 → {d.text}")
            print(f"  校验 → {'可外发' if ok else '!! 不可外发，会被发送层拦下'}")

        if d.action == "close":
            print("\n收工：" + d.reason)
            break
        if d.action.startswith("send_"):
            history.append(d.text)
            s.round += 1
            s.stage = d.stage
            s.gap = d.gap
            s.last_speaker = MsgRole.BUYER
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
