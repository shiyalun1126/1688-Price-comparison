"""端到端验证：网页 API 返回的图片路径、匹配结果结构。"""
from __future__ import annotations

import httpx

BASE = "http://127.0.0.1:8010"


def main() -> None:
    targets = httpx.get(f"{BASE}/api/targets", timeout=10).json()
    print("=== 目标款 ===")
    for t in targets:
        print(f"  {t['name']} | cat={t['category']} | 图片={t['image_paths']}")

    with_img = [t for t in targets if t["image_paths"]]
    if not with_img:
        print("\n没有带图目标款，跳过匹配测试")
        return

    t = with_img[0]
    print(f"\n=== 目标款《{t['name']}》的商品 ===")
    prods = httpx.get(f"{BASE}/api/targets/{t['id']}/products", timeout=10).json()
    for p in prods:
        print(f"  {p['title']} | 图={p['image_paths']}")

    print("\n=== 执行视觉匹配 ===")
    m = httpx.post(f"{BASE}/api/targets/{t['id']}/match", timeout=300).json()
    for key, label in [("exact", "完全同款"), ("equivalent", "等效替代"),
                       ("rejected", "已排除"), ("candidate", "未核验")]:
        lst = m.get(key, [])
        print(f"  {label}: {len(lst)}")
        for row in lst:
            img = ""
            if row.get("product") and row["product"].get("image_paths"):
                img = f" [图: {row['product']['image_paths'][0]}]"
            print(f"    - {row.get('shop_name')}{img}")
            print(f"      理由: {row.get('reason')}")


if __name__ == "__main__":
    main()