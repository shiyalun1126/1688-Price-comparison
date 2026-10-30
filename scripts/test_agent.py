"""实测 Agent 是否真能自主决策。"""
from __future__ import annotations

import json

import httpx

B = "http://127.0.0.1:8010"


def show(result: dict) -> None:
    print("=" * 70)
    print("目标:", result.get("goal"))
    print("完成:", result.get("finished"))
    print("=" * 70)

    steps = result.get("steps") or []
    for s in steps:
        print(f"\n【第 {s['round']} 轮】")
        if s.get("thought"):
            print(f"  💭 思考: {s['thought'][:200]}")
        if s.get("tool"):
            print(f"  🔧 调用: {s['tool']}")
            print(f"     参数: {json.dumps(s.get('args'), ensure_ascii=False)}")
            obs = s.get("observation") or ""
            print(f"  👁 观察: {obs[:300]}{'…' if len(obs) > 300 else ''}")

    print("\n" + "=" * 70)
    print("最终结论:")
    print(result.get("final_answer"))
    print("=" * 70)


def main() -> None:
    # 找一个有商品的目标款
    ts = httpx.get(f"{B}/api/targets", timeout=10).json()
    target = None
    for t in ts:
        prods = httpx.get(f"{B}/api/targets/{t['id']}/products", timeout=60).json()
        if prods:
            target = t
            print(f"选中目标款: {t['name']}（{len(prods)} 个商品）\n")
            break

    payload = {"goal": "帮我分析一下这个款哪家最便宜，给出采购建议"}
    if target:
        payload["target_id"] = target["id"]

    print("正在运行智能体…\n")
    r = httpx.post(f"{B}/api/agent/run", json=payload, timeout=600)
    print("HTTP", r.status_code)
    show(r.json())


if __name__ == "__main__":
    main()