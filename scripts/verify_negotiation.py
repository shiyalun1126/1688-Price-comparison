"""离线验证「会谈判」全链路：询价 → 追问 → 议价 → 收敛。

不需要 1688 账号、不需要浏览器：INQUIRY_CHANNEL=mock 时用按剧本回复的
假卖家，剧本刻意模拟真实节奏（先只报价格、再补齐税费运费、最后让一口价），
用来验证三件事：

1. 追问只在有缺口时发生，且不重复问已经答过的项；
2. 信息补齐后是否真的会开口议价；
3. 议价成功 / 卖家拒绝时能否正确收敛，不无限聊下去。

运行：D:/miniconda3/python.exe scripts/verify_negotiation.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ["INQUIRY_CHANNEL"] = "mock"
os.environ["INQUIRY_MIN_DELAY"] = "1"        # 演示节奏：不用等真实退避
os.environ["INQUIRY_SEND_INTERVAL"] = "0"
os.environ["INQUIRY_TICK_S"] = "2"

from app.inquire.engine import InquiryEngine          # noqa: E402
from app.models.entities import (                      # noqa: E402
    InquiryTask,
    Product,
    Shop,
    TargetStyle,
)
from app.models.enums import MatchStatus, MatchTier, InquiryStatus  # noqa: E402
from app.store.db import Store                         # noqa: E402

ROLE_CN = {"buyer": "采购(智能体)", "seller": "卖家", "system": "系统"}


def build_fixture(store: Store) -> str:
    now = datetime.now()
    store.save_shop(Shop(id="shop1", name="普赛服饰实力工厂"))
    store.save_target_style(TargetStyle(
        id="t1", name="重磅纯棉落肩短袖T恤",
        description="260g 双纱纯棉，落肩宽版，圆领",
        attributes={"面料": "纯棉", "克重": "260g"},
        reference_price=50.0,          # 心理价，用于判断是否值得压价
        category="服装",
        created_at=now, updated_at=now,
    ))
    store.save_product(Product(
        id="p1", target_style_id="t1", shop_id="shop1",
        title="260g重磅纯棉短袖T恤 落肩宽松 男女同款 批发",
        url="https://detail.1688.com/offer/123456789.html",
        listed_price=58.0,
        match_status=MatchStatus.VERIFIED_SAME,
        match_tier=MatchTier.EXACT,
        match_reason="视觉与版型一致",
        created_at=now,
    ))
    inq = InquiryTask(
        id="inq1", target_style_id="t1", product_id="p1", shop_id="shop1",
        script="", status=InquiryStatus.DRAFT, buyer_quantity=500,
        created_at=now, updated_at=now,
    )
    store.save_inquiry(inq)
    return inq.id


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="wb-verify-"))
    try:
        store = Store(tmp / "app.db")
        inquiry_id = build_fixture(store)
        engine = InquiryEngine(store, tick_s=2, batch=5,
                               shot_dir=tmp / "shots")

        queued = engine.enqueue(inquiry_id)
        print("入队:", queued)
        session_id = queued["session_id"]

        for step in range(6):
            # 手动把下次动作时间提前，避免真的等退避间隔
            s = store.get_session(session_id)
            if s is None:
                break
            s.next_action_at = datetime.now() - timedelta(seconds=1)
            store.save_session(s)
            results = engine.run_once(limit=1)
            if not results:
                print("（无到期会话，结束）")
                break
            r = results[0]
            print(f"\n--- 第 {step + 1} 次推进 ---")
            print(f"动作: {r.get('action')} | 理由: {r.get('reason')}")
            if r.get("sent"):
                print(f"发出: {r['sent']}")
            if r.get("gap"):
                from app.inquire.dialogue import GAP_LABELS
                print(f"缺口: {'、'.join(GAP_LABELS.get(g, g) for g in r['gap'])}")
            done = store.get_session(session_id)
            if done and done.status in (InquiryStatus.QUOTED, InquiryStatus.CLOSED,
                                        InquiryStatus.ESCALATED):
                break

        print("\n================ 完整对话 ================")
        for m in store.list_messages(session_id):
            stamp = m.ts.strftime("%H:%M:%S")
            print(f"[{stamp}] {ROLE_CN.get(m.role.value, m.role.value)}: {m.text}")

        final = store.get_session(session_id)
        quote = store.get_quote(f"q-{session_id}")
        print("\n================ 结果 ================")
        print(f"会话状态: {final.status.value}（{final.round} 轮）")
        print(f"阶段: {final.stage.value} | 缺口: {final.gap or '无'}")
        if quote:
            print(f"成交报价: {quote.price} 元 | MOQ {quote.moq} | 含税 {quote.tax.value}"
                  f" | 含运 {quote.includes_shipping} | 现货 {quote.stock_status.value}"
                  f" | 发货 {quote.lead_time_hours}h")
            print(f"原标价 58.00 → 谈后 {quote.price:.2f}，"
                  f"降幅 {(58 - quote.price) / 58 * 100:.1f}%")
        ok = (final.status == InquiryStatus.QUOTED
              and quote is not None and quote.price > 0)
        print("\n✅ 全链路收敛" if ok else "\n❌ 未收敛，需检查决策链")
        store.close()
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
