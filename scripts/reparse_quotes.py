"""用当前的解析规则，把会话里已有的卖家消息重放一遍，重建这条会话的报价。

为什么需要它：解析规则是会长大的。卖家早就说过「商品是含税的价格」「运费要看
你的地址」，但如果当时的规则认不出来，这条信息就永久丢了——后面每一轮都会再问
一遍已经答过的问题，对方只会觉得你不会听人说话。

改了 _parse_rules 之后跑一次，历史对话就能按新规则重新算出报价。

用法：
    D:/miniconda3/python.exe scripts/reparse_quotes.py --session s-xxxx
    D:/miniconda3/python.exe scripts/reparse_quotes.py --all
    D:/miniconda3/python.exe scripts/reparse_quotes.py --all --dry   # 只看结果不写库
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.inquire.dialogue import merge_quote  # noqa: E402
from app.inquire.engine import _has_signal  # noqa: E402
from app.inquire.inquiry import parse_quote  # noqa: E402
from app.models.entities import Quote  # noqa: E402
from app.models.enums import InquiryStatus, MsgRole  # noqa: E402
from app.store.db import Store  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _fmt(q: Quote | None) -> str:
    if not q:
        return "（无报价）"
    ship = ("含运费" if q.includes_shipping else
            "运费另算" if q.includes_shipping is False else "运费未知")
    return (f"价 {q.price or '—'} / 起订 {q.moq} / 税 {q.tax.value} / {ship} / "
            f"现货 {q.stock_status.value} / 时效 {q.lead_time_hours or '—'}h / "
            f"阶梯 {q.tiers or '—'}")


def rebuild(store: Store, session_id: str, llm=None, dry: bool = False) -> dict:
    s = store.get_session(session_id)
    if not s:
        return {"session_id": session_id, "error": "会话不存在"}
    target = store.get_target_style(s.target_style_id)
    product = store.get_product(s.product_id)
    if not target or not product:
        return {"session_id": session_id, "error": "目标款/商品缺失"}

    old = store.get_quote(f"q-{s.id}")
    merged: Quote | None = None
    used = 0
    for m in store.list_messages(session_id):
        if m.role != MsgRole.SELLER or not (m.text or "").strip():
            continue
        patch = parse_quote(m.text, target, product, llm=llm)
        if not _has_signal(patch):
            continue
        merged = merge_quote(merged, patch)
        used += 1

    if merged is None:
        return {"session_id": session_id, "before": _fmt(old), "after": "（无可解析内容）"}

    merged.id = f"q-{s.id}"
    merged.inquiry_id = s.inquiry_id
    merged.quoted_at = datetime.now()
    if not dry:
        store.save_quote(merged)
    return {"session_id": session_id, "before": _fmt(old), "after": _fmt(merged),
            "rebuilt_from": f"{used} 条卖家消息"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()

    store = Store(ROOT / "data" / "app.db")
    llm = None
    if args.no_llm:
        from app.llm.client import LLMClient

        class _No(LLMClient):
            available = False
        llm = _No()

    if args.session:
        ids = [args.session]
    elif args.all:
        ids = [s.id for s in store.list_active_sessions()]
    else:
        print(__doc__)
        return 1

    for sid in ids:
        r = rebuild(store, sid, llm=llm, dry=args.dry)
        print(f"\n[{sid}]")
        for k, v in r.items():
            if k != "session_id":
                print(f"  {k}: {v}")
    store.close()
    print("\n完成" + ("（dry run，未写库）" if args.dry else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
