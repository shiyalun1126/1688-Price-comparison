"""询价闭环端到端验证（备份真实库 → 跑全链路 → 恢复，不污染数据）。

覆盖：创建询价任务 → 生成话术 → 标记已发送 → 贴卖家回复 → 解析结构化报价
      → 询价状态置 quoted → 比价结论纳入该报价 → 验证后恢复数据库。

用法：python -m scripts.inquiry_e2e   （或 python scripts/inquiry_e2e.py）
"""
from __future__ import annotations

import asyncio
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import httpx

from app.main import app

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "app.db"

# 一条典型的多条件卖家回复：单价、起订量、含税、运费、阶梯价全都有
SELLER_REPLY = "亲，42.5元一件，起订100件，不含税不包邮，500件以上41。"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def count_rows(table: str) -> int:
    conn = sqlite3.connect(str(DB_PATH))
    n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return n


async def main() -> bool:
    if not DB_PATH.exists():
        print(f"数据库不存在：{DB_PATH}")
        return False

    # ---- 备份 ----
    backup = Path(tempfile.mkdtemp(prefix="inquiry_e2e_")) / "app.db.bak"
    shutil.copy2(DB_PATH, backup)
    print(f"已备份数据库 → {backup}")

    inq_before, quote_before = count_rows("inquiry"), count_rows("quote")

    try:
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

        # ---- 选目标款：优先「卫衣」（有 exact/equivalent/rejected 分层） ----
        targets = (await c.get("/api/targets")).json()
        print("\n目标款：", [(t["name"], t["id"][:6]) for t in targets])
        target = next((t for t in targets if "卫衣" in t["name"]), targets[0] if targets else None)
        if not target:
            print("没有任何目标款，无法测试")
            return False
        tid = target["id"]
        print(f"选用目标款：{target['name']}")

        prods = (await c.get(f"/api/targets/{tid}/products")).json()
        # 优先选「完全同款」商品，比价行才会 comparable
        prod = next(
            (p for p in prods if p.get("match_status") == "verified_same"),
            next((p for p in prods if p.get("listed_price") is not None), None),
        )
        if not prod:
            print("该目标款下没有可用商品")
            return False
        print(f"选用商品：{prod['title'][:40]}（{prod.get('match_status')}，标价 {prod.get('listed_price')}）")

        # ---- 1. 列出询价（应为 pending） ----
        inv = (await c.get(f"/api/targets/{tid}/inquiries")).json()
        mine = [x for x in inv if x["product_id"] == prod["id"]]
        check("初始询价状态为 pending", mine and mine[0]["status"] == "pending",
              mine[0]["status"] if mine else "未找到")

        # ---- 2. 创建询价 → 生成话术 ----
        r = (await c.post("/api/inquiries", json={
            "target_style_id": tid, "product_id": prod["id"], "buyer_quantity": 200,
        })).json()
        check("创建询价任务", bool(r.get("id")), f"id={r.get('id', '?')[:8]}")
        check("话术已生成", len(r.get("script", "")) > 20,
              f"{len(r.get('script', ''))} 字：{r.get('script', '')[:60]}…")
        inq_id = r["id"]

        # ---- 3. 标记已发送 ----
        r = (await c.post(f"/api/inquiries/{inq_id}/status", json={"status": "sent"})).json()
        check("状态流转 draft→sent", r.get("status") == "sent", str(r))

        # ---- 4. 贴卖家回复 → 解析报价 ----
        r = (await c.post(f"/api/inquiries/{inq_id}/parse-reply",
                          json={"reply": SELLER_REPLY})).json()
        q = r.get("quote", {})
        print(f"  解析结果：price={q.get('price')} moq={q.get('moq')} tax={q.get('tax')} "
              f"shipping={q.get('includes_shipping')} tiers={q.get('tiers')} note={q.get('condition_note')!r}")
        check("报价解析出价格 42.5", abs((q.get("price") or 0) - 42.5) < 0.01, str(q.get("price")))
        check("询价状态置为 quoted", r.get("status") == "quoted", str(r.get("status")))
        check("报价落库（+1）", count_rows("quote") == quote_before + 1,
              f"{quote_before} → {count_rows('quote')}")

        # ---- 5. 比价纳入该报价 ----
        comp = (await c.post(f"/api/targets/{tid}/comparison")).json()
        rows = comp.get("rows", [])
        quoted = [x for x in rows if x.get("product_id") == prod["id"]]
        print(f"  比价综述：{comp.get('summary')}")
        print(f"  采购建议：{comp.get('recommendation')}")
        check("比价行包含该报价", bool(quoted), f"共 {len(rows)} 行")
        if quoted:
            row = quoted[0]
            check("报价行来源为 quoted", row.get("price_source") == "quoted", str(row.get("price_source")))
            check("报价行价格为 42.5", abs((row.get("price") or 0) - 42.5) < 0.01, str(row.get("price")))
            check("完全同款行可比（comparable）", row.get("comparable") is True,
                  f"tier={row.get('tier')}")

        # ---- 6. 询价任务最终状态 ----
        inv = (await c.get(f"/api/targets/{tid}/inquiries")).json()
        mine = [x for x in inv if x["id"] == inq_id]
        check("询价最终状态 quoted", mine and mine[0]["status"] == "quoted",
              mine[0]["status"] if mine else "未找到")

        await c.aclose()
    finally:
        # ---- 恢复数据库 ----
        shutil.copy2(backup, DB_PATH)
        backup.unlink()
        print(f"\n已恢复数据库（询价 {inq_before} 条 / 报价 {quote_before} 条，测试数据已清除）")

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n===== 询价闭环 E2E：{len(results) - len(failed)}/{len(results)} 通过 =====")
    if failed:
        print("未通过项：", failed)
    return not failed


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
