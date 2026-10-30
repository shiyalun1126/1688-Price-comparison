"""测试并发匹配提速 + 增量匹配。"""
from __future__ import annotations

import time

import httpx

B = "http://127.0.0.1:8010"


def main() -> None:
    ts = httpx.get(f"{B}/api/targets", timeout=10).json()
    # 挑商品数最多的目标款
    best = None
    for t in ts:
        prods = httpx.get(f"{B}/api/targets/{t['id']}/products", timeout=60).json()
        if prods and (best is None or len(prods) > best[1]):
            best = (t, len(prods))

    if not best:
        print("没有带商品的目标款")
        return

    t, n = best
    print(f"目标款: {t['name']} | 商品数: {n}")
    print()

    # 第一次：全量匹配
    print("=== 第一次匹配（全量，强制重算）===")
    t0 = time.time()
    r = httpx.post(f"{B}/api/targets/{t['id']}/match?incremental=false", timeout=1800)
    elapsed = time.time() - t0
    d = r.json()
    stats = d.get("stats", {})
    print(f"HTTP {r.status_code} | 总耗时 {elapsed:.1f}s")
    print(f"  stats: {stats}")
    print(f"  exact={len(d.get('exact',[]))} equiv={len(d.get('equivalent',[]))} "
          f"rejected={len(d.get('rejected',[]))} candidate={len(d.get('candidate',[]))}")

    # 第二次：增量匹配（应立即返回）
    print("\n=== 第二次匹配（增量，应复用结果）===")
    t0 = time.time()
    r2 = httpx.post(f"{B}/api/targets/{t['id']}/match", timeout=1800)
    elapsed2 = time.time() - t0
    d2 = r2.json()
    stats2 = d2.get("stats", {})
    print(f"HTTP {r2.status_code} | 总耗时 {elapsed2:.1f}s")
    print(f"  stats: {stats2}")
    print(f"  exact={len(d2.get('exact',[]))} equiv={len(d2.get('equivalent',[]))} "
          f"rejected={len(d2.get('rejected',[]))} candidate={len(d2.get('candidate',[]))}")

    if n > 0 and elapsed > 0:
        print(f"\n串行预估: {n * 3:.0f}s（按每个3秒估算）")
        print(f"并发实际: {elapsed:.1f}s")
        print(f"提速倍数: 约 {n * 3 / elapsed:.1f}x")


if __name__ == "__main__":
    main()