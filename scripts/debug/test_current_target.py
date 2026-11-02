"""验证动态目标款：切换 current-target 后，导入是否落到正确的目标款。"""
from __future__ import annotations

import httpx

B = "http://127.0.0.1:8010"


def main() -> None:
    ts = httpx.get(f"{B}/api/targets", timeout=10).json()
    print("目标款清单:")
    for t in ts:
        print(f"  {t['id'][:12]}  {t['name']}")

    # 1) 查当前目标款
    cur = httpx.get(f"{B}/api/current-target", timeout=10).json()
    print("\n当前目标款:", cur)

    # 2) 切换到 POLO衫
    polo = next((t for t in ts if "POLO" in t["name"]), None)
    if not polo:
        print("没有 POLO衫，跳过")
        return
    r = httpx.post(f"{B}/api/current-target", json={"target_id": polo["id"]}, timeout=10).json()
    print("切换后:", r)

    cur2 = httpx.get(f"{B}/api/current-target", timeout=10).json()
    print("再次查询:", cur2)

    # 3) 模拟书签导入（不指定 target_style_id 之外的逻辑）
    #    书签会先 GET current-target 拿到 id，再 POST import-product
    img = polo["image_paths"][0]
    img_url = B + img
    before = len(httpx.get(f"{B}/api/targets/{polo['id']}/products", timeout=30).json())
    imp = httpx.post(f"{B}/api/import-product", json={
        "target_style_id": cur2["id"],
        "title": "【动态验证】POLO测试商品",
        "image_url": img_url,
        "price": 88.0,
        "url": "https://detail.1688.com/offer/999.html",
        "shop_name": "动态验证店铺",
    }, timeout=60).json()
    print("导入结果:", imp.get("ok"))
    after = len(httpx.get(f"{B}/api/targets/{polo['id']}/products", timeout=30).json())
    print(f"POLO衫商品数: {before} -> {after}")

    # 4) 清理这条测试数据
    if imp.get("ok"):
        pid = imp["product"]["id"]
        print("（测试商品 id:", pid[:10], "，可手动清理）")


if __name__ == "__main__":
    main()