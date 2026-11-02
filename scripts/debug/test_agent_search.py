"""测试：目标款无商品时，Agent 会不会主动规划搜索关键词。"""
from __future__ import annotations

import json

import httpx

B = "http://127.0.0.1:8010"


def main() -> None:
    ts = httpx.get(f"{B}/api/targets", timeout=10).json()

    # 找一个商品数为 0 的目标款
    empty = None
    for t in ts:
        prods = httpx.get(f"{B}/api/targets/{t['id']}/products", timeout=60).json()
        if len(prods) == 0:
            empty = t
            break

    if not empty:
        print("没有空目标款，先创建一个…")
        r = httpx.post(f"{B}/api/targets/from-url", json={
            "title": "测试-藏青格纹衬衫",
            "image_url": (B + ts[0]["image_paths"][0]) if ts[0].get("image_paths") else "",
            "category": "衬衫", "price": 50.0,
        }, timeout=60).json()
        empty = {"id": r["id"], "name": r.get("name")}
        print("已创建:", empty["name"])

    print(f"使用空目标款: {empty['name']}")
    print("=" * 70)

    r = httpx.post(f"{B}/api/agent/run", json={
        "goal": "这个款还没有候选商品，帮我找货源",
        "target_id": empty["id"],
    }, timeout=600)

    d = r.json()
    print("HTTP", r.status_code, "| finished:", d.get("finished"))
    print("=" * 70)

    for s in d.get("steps", []):
        print(f"\n【第 {s['round']} 轮】")
        if s.get("thought"):
            print(f"  💭 {s['thought'][:180]}")
        if s.get("tool"):
            print(f"  🔧 {s['tool']}")
            obs = s.get("observation") or ""
            print(f"  👁 {obs[:600]}{'…' if len(obs) > 600 else ''}")

    print("\n" + "=" * 70)
    print("结论:")
    print(d.get("final_answer", "")[:1500])


if __name__ == "__main__":
    main()