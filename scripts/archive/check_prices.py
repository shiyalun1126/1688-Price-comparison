"""检查商品的标价抓取情况。"""
from __future__ import annotations

import httpx

B = "http://127.0.0.1:8010"


def main() -> None:
    ts = httpx.get(f"{B}/api/targets", timeout=10).json()
    for t in ts:
        prods = httpx.get(f"{B}/api/targets/{t['id']}/products", timeout=60).json()
        if not prods:
            continue
        n_price = sum(1 for p in prods if p.get("listed_price") is not None)
        print(f"目标款《{t['name']}》商品 {len(prods)} 个，有标价 {n_price} 个")
        for p in prods[:5]:
            print(f"   标价={p.get('listed_price')!r:>8} | {p.get('title','')[:35]}")
        print()


if __name__ == "__main__":
    main()