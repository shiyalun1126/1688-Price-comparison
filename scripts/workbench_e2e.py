"""询价工作台端到端测试：创建询价→发状态→贴回复→解析报价 全流转。"""
from __future__ import annotations

import asyncio
import httpx
from app.main import app


async def main():
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    # 取目标款
    targets = (await c.get("/api/targets")).json()
    tid = targets[0]["id"]
    print("目标款 id:", tid)

    # 取候选商品
    prods = (await c.get(f"/api/targets/{tid}/products")).json()
    print("候选商品数:", len(prods))
    prod = prods[0]

    # 列出询价（应为 pending）
    inv = (await c.get(f"/api/targets/{tid}/inquiries")).json()
    print("初始询价状态:", [(x["product_id"][:6], x["status"]) for x in inv])

    # 创建询价（生成话术）
    r = (await c.post("/api/inquiries", json={
        "target_style_id": tid, "product_id": prod["id"], "buyer_quantity": 200,
    })).json()
    print("创建询价:", r["id"], "| status:", r["status"], "| 话术长度:", len(r["script"]))
    inq_id = r["id"]

    # 标记已发送
    r = (await c.post(f"/api/inquiries/{inq_id}/status", json={"status": "sent"})).json()
    print("标记已发送:", r)

    # 贴回复并解析
    r = (await c.post(f"/api/inquiries/{inq_id}/parse-reply", json={
        "reply": "亲，42.5元一件，起订100件，不含税不包邮，500件以上41。"
    })).json()
    q = r.get("quote", {})
    print("解析报价:", q.get("price"), "| MOQ:", q.get("moq"), "| tax:", q.get("tax"), "| status:", r.get("status"))

    # 再次列出询价，应显示 quoted
    inv = (await c.get(f"/api/targets/{tid}/inquiries")).json()
    mine = [x for x in inv if x["id"] == inq_id]
    print("最终状态:", mine[0]["status"] if mine else "?")

    # 比价是否纳入这条报价
    comp = (await c.post(f"/api/targets/{tid}/comparison")).json()
    print("比价综述:", comp.get("summary"))

    await c.aclose()


asyncio.run(main())
print("工作台 E2E OK")