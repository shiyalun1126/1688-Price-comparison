"""验证删除目标款：级联删除商品/询价/报价，并清理孤儿门店。"""
from __future__ import annotations

import httpx

BASE = "http://127.0.0.1:8010"


def main() -> None:
    # 1) 建一个临时目标款（复用已有图片）
    targets = httpx.get(f"{BASE}/api/targets", timeout=10).json()
    with_img = next((t for t in targets if t["image_paths"]), None)
    if not with_img:
        print("没有带图目标款，无法测试")
        return
    img_url = BASE + with_img["image_paths"][0]

    r = httpx.post(
        f"{BASE}/api/targets/from-url",
        json={"title": "【待删除测试】目标款", "image_url": img_url, "category": "测试", "price": 99.0},
        timeout=60,
    ).json()
    tid = r["id"]
    print("创建测试目标款:", r.get("name"), tid[:10])

    # 2) 给它塞一个商品
    p = httpx.post(
        f"{BASE}/api/import-product",
        json={"target_style_id": tid, "title": "测试商品", "image_url": img_url,
              "price": 10.0, "url": "https://detail.1688.com/offer/1.html", "shop_name": "测试店"},
        timeout=60,
    ).json()
    print("导入测试商品:", p.get("ok"), p.get("product", {}).get("title"))

    before = httpx.get(f"{BASE}/api/targets/{tid}/products", timeout=10).json()
    print("删除前商品数:", len(before))

    # 3) 删除
    d = httpx.delete(f"{BASE}/api/targets/{tid}", timeout=30).json()
    print("删除结果:", d)

    # 4) 确认已消失
    after_targets = httpx.get(f"{BASE}/api/targets", timeout=10).json()
    still = any(t["id"] == tid for t in after_targets)
    print("目标款是否仍存在:", still)
    print("剩余目标款数:", len(after_targets))

    # 5) 再删一次，应报不存在
    d2 = httpx.delete(f"{BASE}/api/targets/{tid}", timeout=30).json()
    print("重复删除返回:", d2)


if __name__ == "__main__":
    main()