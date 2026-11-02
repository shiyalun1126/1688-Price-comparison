"""检查某目标款的比价结果。"""
from __future__ import annotations

import httpx

B = "http://127.0.0.1:8010"


def main() -> None:
    ts = httpx.get(f"{B}/api/targets", timeout=10).json()
    for t in ts:
        prods = httpx.get(f"{B}/api/targets/{t['id']}/products", timeout=60).json()
        n_price = sum(1 for p in prods if p.get("listed_price") is not None)
        if n_price == 0:
            continue
        print(f"\n=== {t['name']} （{len(prods)} 商品，{n_price} 有价）===")
        r = httpx.post(f"{B}/api/targets/{t['id']}/comparison", timeout=180).json()
        print("综述:", r.get("summary"))
        print("建议:", r.get("recommendation"))
        rows = r.get("rows") or []
        print(f"表格 {len(rows)} 行，前5行:")
        for x in rows[:5]:
            print(f"  {x.get('shop_name','')[:20]:20s} | {x.get('price')} "
                  f"| {x.get('price_source')} | {x.get('tier')} | {x.get('title','')[:20]}")


if __name__ == "__main__":
    main()