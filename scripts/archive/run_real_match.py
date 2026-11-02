"""对指定目标款执行真实视觉匹配（用于验证端到端）。"""
from __future__ import annotations

import time

import httpx

B = "http://127.0.0.1:8010"


def main() -> None:
    ts = httpx.get(f"{B}/api/targets", timeout=10).json()
    t = next(x for x in ts if "蓝白格" in x["name"])
    print("目标款:", t["name"])
    print("id:", t["id"])

    prods = httpx.get(f"{B}/api/targets/{t['id']}/products", timeout=60).json()
    print("商品数:", len(prods))
    print("\n开始匹配（每个商品调一次视觉模型，预计较慢）……")

    t0 = time.time()
    r = httpx.post(f"{B}/api/targets/{t['id']}/match", timeout=1800)
    elapsed = time.time() - t0
    print(f"HTTP {r.status_code} | 耗时 {elapsed:.1f}s")
    d = r.json()
    print("exact:", len(d.get("exact", [])),
          "| equiv:", len(d.get("equivalent", [])),
          "| rejected:", len(d.get("rejected", [])),
          "| candidate:", len(d.get("candidate", [])))

    for k in ("exact", "equivalent"):
        rows = d.get(k, [])
        if rows:
            print(f"\n[{k}] 前2条:")
            for row in rows[:2]:
                print("  店铺:", row.get("shop_name"))
                print("  理由:", (row.get("reason") or "")[:100])


if __name__ == "__main__":
    main()