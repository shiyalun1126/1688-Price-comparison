"""测试导入接口：模拟书签脚本提交 1688 商品数据（含图片 URL 下载）。"""
from __future__ import annotations

import httpx

BASE = "http://127.0.0.1:8010"


def main() -> None:
    targets = httpx.get(f"{BASE}/api/targets", timeout=10).json()
    with_img = [t for t in targets if t["image_paths"]]
    if not with_img:
        print("没有带图目标款，无法测试")
        return
    t = with_img[0]
    print("目标款:", t["name"], t["id"][:10])

    # 用素材里图片的本地访问 URL 模拟远程图片
    # 先看已有商品的第一张图 URL
    prods = httpx.get(f"{BASE}/api/targets/{t['id']}/products", timeout=10).json()
    if not prods or not prods[0]["image_paths"]:
        print("没有可用的图片 URL 做测试")
        return
    img_url = BASE + prods[0]["image_paths"][0]
    print("测试用图片 URL:", img_url)

    payload = {
        "target_style_id": t["id"],
        "title": "【导入测试】1688商品-蓝格衬衫",
        "image_url": img_url,
        "price": 45.5,
        "url": "https://detail.1688.com/offer/123456789.html",
        "shop_name": "导入测试店铺",
    }
    r = httpx.post(f"{BASE}/api/import-product", json=payload, timeout=60)
    print("HTTP", r.status_code)
    d = r.json()
    print("ok:", d.get("ok"))
    if d.get("ok"):
        p = d["product"]
        print("  商品:", p["title"])
        print("  图片:", p["image_paths"])
        print("  shop_id:", d["shop_id"])
    else:
        print("  error:", d.get("error"))


if __name__ == "__main__":
    main()