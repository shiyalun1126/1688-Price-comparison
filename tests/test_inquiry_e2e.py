"""询价闭环 E2E（LLM 调用，标记 slow）：

创建询价 → 生成话术 → 标记已发送 → 贴卖家回复 → 解析报价
→ 状态置 quoted → 比价纳入 → 断言。跑在隔离库副本上，原始数据零改动。
"""
from __future__ import annotations

import pytest

SELLER_REPLY = "亲，42.5元一件，起订100件，不含税不包邮，500件以上41。"

pytestmark = pytest.mark.slow


def test_inquiry_closed_loop(app_client, hoodie_target_id):
    prods = app_client.get(f"/api/targets/{hoodie_target_id}/products").json()
    prod = next((p for p in prods if p.get("match_status") == "verified_same"), prods[0])

    # 初始应为 pending
    inv = app_client.get(f"/api/targets/{hoodie_target_id}/inquiries").json()
    mine = [x for x in inv if x["product_id"] == prod["id"]]
    assert mine and mine[0]["status"] == "pending"

    # 创建询价 → 话术
    r = app_client.post("/api/inquiries", json={
        "target_style_id": hoodie_target_id, "product_id": prod["id"], "buyer_quantity": 200,
    }).json()
    assert r["id"] and len(r["script"]) > 20
    inq_id = r["id"]

    # 状态流转
    r = app_client.post(f"/api/inquiries/{inq_id}/status", json={"status": "sent"}).json()
    assert r["status"] == "sent"

    # 解析报价
    r = app_client.post(f"/api/inquiries/{inq_id}/parse-reply",
                        json={"reply": SELLER_REPLY}).json()
    q = r["quote"]
    assert abs(q["price"] - 42.5) < 0.01
    assert r["status"] == "quoted"

    # 比价纳入该报价，且完全同款行可比
    comp = app_client.post(f"/api/targets/{hoodie_target_id}/comparison").json()
    row = next((x for x in comp["rows"] if x["product_id"] == prod["id"]), None)
    assert row is not None
    assert row["price_source"] == "quoted"
    assert abs(row["price"] - 42.5) < 0.01
    if prod["match_status"] == "verified_same":
        assert row["comparable"] is True

    # 询价最终状态
    inv = app_client.get(f"/api/targets/{hoodie_target_id}/inquiries").json()
    mine = [x for x in inv if x["id"] == inq_id]
    assert mine and mine[0]["status"] == "quoted"
