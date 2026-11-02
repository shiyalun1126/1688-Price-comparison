"""测试：通过 API 上传图片并执行视觉匹配。"""
from __future__ import annotations

from pathlib import Path

import httpx
from app.main import app

SRC = Path(__file__).resolve().parent.parent / "素材"


async def main():
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    # 创建目标款（带图）
    target_img = SRC / "O1CN01ByFwJQ1F5jnveHF0s_!!2208152340436-0-cib.jpg"
    with open(target_img, "rb") as f:
        r = await c.post(
            "/api/targets",
            data={"name": "蓝白格衬衫-目标款", "attributes_json": '{"面料":"棉","颜色":"蓝白格"}', "category": "衬衫"},
            files={"image": ("target.jpg", f, "image/jpeg")},
        )
    t = r.json()
    print("target:", t["id"], "img:", t.get("image_paths"))
    tid = t["id"]

    # 创建门店
    s = (await c.post("/api/shops", json={"name": "测试店铺"})).json()
    sid = s["id"]

    # 创建候选商品（带图）—— 同款另一张
    prod_img = SRC / "O1CN01pCQwQK1MysorjhjGe_!!2908521504-0-cib.jpg"
    with open(prod_img, "rb") as f:
        p = (await c.post(
            "/api/products",
            data={"target_style_id": tid, "shop_id": sid, "title": "同款衬衫"},
            files={"image": ("prod.jpg", f, "image/jpeg")},
        )).json()
    print("product:", p["id"], "img:", p.get("image_paths"))

    # 执行视觉匹配
    m = (await c.post(f"/api/targets/{tid}/match")).json()
    print("\n匹配结果:")
    print("  exact:", len(m["exact"]))
    print("  equivalent:", len(m["equivalent"]))
    print("  rejected:", len(m["rejected"]))
    for row in m["exact"] + m["equivalent"] + m["rejected"]:
        print(f"  - {row['status']} | {row['shop_name']} | {row['reason']}")

    await c.aclose()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())