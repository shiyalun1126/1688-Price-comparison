"""离线验证「详情页预填」链路：抓页面 → 存商品 → 决策 → 假装发一条。

不碰真实聊天，也不改 data/app.db：
- 全程跑在数据库副本上（跑完删除），不会污染看板；
- 聊天通道换成假的，只打印「本来会发出去的那句话」，不给任何卖家发消息。

用法：
    python scripts/verify_detail_prefill.py --product <商品id> [--qty 500] [--refetch]
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.browser.detail import fetch_offer_facts          # noqa: E402
from app.inquire import detail_facts                     # noqa: E402
from app.inquire import engine as engine_mod             # noqa: E402
from app.inquire.dialogue import compute_gaps            # noqa: E402
from app.models.entities import InquirySession, InquiryTask  # noqa: E402
from app.models.enums import InquiryStatus, MsgRole      # noqa: E402
from app.store.db import Store                           # noqa: E402


class FakeChannel:
    """只记录要发的内容，绝不真的发。"""

    def __init__(self):
        self.sent: list[str] = []
        self.touid = ""

    def open(self, url: str) -> None:
        pass

    def read(self):
        return []

    def send(self, text: str) -> None:
        self.sent.append(text)

    def screenshot(self, tag: str = ""):
        return None

    def close(self) -> None:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True, help="商品 id（product 表）")
    ap.add_argument("--qty", type=int, default=500)
    ap.add_argument("--refetch", action="store_true", help="忽略缓存，重新抓详情页")
    args = ap.parse_args()

    src = ROOT / "data" / "app.db"
    if not src.exists():
        print(f"找不到数据库：{src}")
        return 2
    tmpdir = Path(tempfile.mkdtemp(prefix="prefill_"))
    tmp_db = tmpdir / "app.db"
    # 库里开了 WAL，直接复制 .db 会丢掉还在 -wal 里的新写入（预填信息刚好在那儿）。
    # 用 SQLite 的在线备份 API 复制，读到的就是一致快照。
    import sqlite3
    with sqlite3.connect(str(src)) as s_conn, sqlite3.connect(str(tmp_db)) as d_conn:
        s_conn.backup(d_conn)
    store = Store(str(tmp_db))

    product = store.get_product(args.product)
    if not product:
        print(f"商品不存在：{args.product}")
        return 2
    target = store.get_target_style(product.target_style_id)
    if not target:
        print("目标款不存在")
        return 2

    print(f"商品：{product.title[:60]}")
    print(f"目标款：{target.name}｜采购量 {args.qty}")
    print(f"详情页缓存：{detail_facts.load(product)}")

    facts = detail_facts.load(product)
    if args.refetch or facts is None:
        print("\n[抓取] 正在打开详情页（只读）…")
        facts, why = fetch_offer_facts(product.url or "")
        if not facts:
            print(f"抓取失败/跳过：{why}")
            print("→ 引擎会退回原来的「逐项问清」口径，不影响主流程")
            return 0
        detail_facts.attach(product, facts)
        store.save_product(product)
    else:
        print("\n[缓存] 使用商品上已存的详情页信息（--refetch 可重抓）")

    facts = detail_facts.load(store.get_product(product.id))
    print(f"[页面已知] {facts.summary(args.qty)}")
    print(f"[可跳过] {sorted(facts.known_gaps(args.qty))}")

    # 临时询价任务 + 会话（只存在于副本库里）
    inq = InquiryTask(id=f"tmp-{uuid.uuid4().hex[:8]}", target_style_id=target.id,
                      product_id=product.id, shop_id=product.shop_id,
                      buyer_quantity=args.qty)
    store.save_inquiry(inq)
    session = InquirySession(
        id=f"s-tmp{uuid.uuid4().hex[:6]}", inquiry_id=inq.id,
        target_style_id=target.id, product_id=product.id, shop_id=product.shop_id,
        offer_url=product.url or "", buyer_quantity=args.qty, round=0,
        status=InquiryStatus.SENDING, gap=compute_gaps(None, ["price", "moq"]),
    )
    store.save_session(session)

    fake = FakeChannel()
    engine_mod.get_channel = lambda *a, **kw: fake      # 只在本进程内替换
    eng = engine_mod.InquiryEngine(store)
    result = eng._process(session)

    print("\n=== 决策 ===")
    print("动作：", result.get("action"))
    print("理由：", result.get("reason"))
    print("待确认缺口：", result.get("gap"))
    print("\n=== 本来会发出去的话 ===")
    for t in fake.sent:
        print(t)

    quote = store.get_quote(f"q-{session.id}")
    if quote:
        print("\n=== 落库报价 ===")
        print(f"单价 {quote.price}｜起订 {quote.moq}｜含税 {quote.tax}"
              f"｜含运费 {quote.includes_shipping}｜库存 {quote.stock_status}")
        print("备注：", quote.condition_note)

    print("\n[提示] 本次运行使用临时库副本，未改动 data/app.db，也未向任何卖家发消息。")
    return 0


if __name__ == "__main__":
    code = 0
    tmp = None
    try:
        code = main()
    finally:
        for d in Path(tempfile.gettempdir()).glob("prefill_*"):
            shutil.rmtree(d, ignore_errors=True)
    sys.exit(code)
