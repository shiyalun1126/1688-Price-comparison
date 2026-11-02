"""实测 Agent 是否真能自主决策（ASGI 直连，无需手工起服务；备份库→跑→恢复）。

验证点：
- Agent 能自主完成 了解现状 → 匹配 → 比价 → 汇总 的闭环
- 推理轨迹里有工具调用与观察
- 最终结论包含具体店铺名和价格数字（不空泛）
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import httpx

from app.main import app

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "app.db"

GOAL = "帮我分析一下这个款哪家最便宜，给出采购建议"


def show(result: dict) -> None:
    print("=" * 70)
    print("目标:", result.get("goal"))
    print("完成:", result.get("finished"))
    print("=" * 70)
    for s in result.get("steps") or []:
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


def comp_generated_at() -> str | None:
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute("SELECT MAX(generated_at) FROM comparison").fetchone()
    conn.close()
    return row[0] if row else None


async def main() -> bool:
    backup = Path(tempfile.mkdtemp(prefix="agent_e2e_")) / "app.db.bak"
    shutil.copy2(DB_PATH, backup)
    print(f"已备份数据库 → {backup}")
    comp_before = comp_generated_at()

    ok = False
    try:
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

        # 选一个数据最有料的目标款：优先带 exact/报价/参考价的「卫衣」
        targets = (await c.get("/api/targets")).json()
        target = next((t for t in targets if "卫衣" in t["name"]), None)
        if not target:
            target = next((t for t in targets
                           if (await c.get(f"/api/targets/{t['id']}/products")).json()), None)
        if not target:
            print("没有可用目标款")
            return False
        print(f"选中目标款: {target['name']}")

        print("正在运行智能体…\n")
        r = await c.post("/api/agent/run",
                         json={"goal": GOAL, "target_id": target["id"]}, timeout=600)
        result = r.json()
        show(result)

        steps = result.get("steps") or []
        tools_called = [s.get("tool") for s in steps if s.get("tool")]
        answer = result.get("final_answer") or ""

        checks = [
            ("Agent 正常收尾", result.get("finished") is True, str(result.get("finished"))),
            ("调用了业务工具", len(tools_called) >= 2, f"调用序列: {tools_called}"),
            ("轨迹含观察结果", all(s.get("observation") for s in steps if s.get("tool")), ""),
            ("结论含店铺名", any(n in answer for n in ("义乌", "广州", "杭州", "店", "司")),
             answer[:60]),
            ("结论含价格数字", any(ch.isdigit() for ch in answer), answer[:60]),
        ]
        print("\n----- 断言 -----")
        for name, passed, detail in checks:
            print(f"  [{'PASS' if passed else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        ok = all(p for _, p, _ in checks)

        # 智能体跑过比价后，comparison 表应有新生成记录
        comp_after = comp_generated_at()
        print(f"  [{'PASS' if comp_after and comp_after != comp_before else 'FAIL'}] "
              f"比价结论已刷新（{comp_before} → {comp_after}）")
        ok = ok and bool(comp_after and comp_after != comp_before)

        await c.aclose()
    finally:
        shutil.copy2(backup, DB_PATH)
        backup.unlink()
        print("\n已恢复数据库（智能体跑出的比价结论为临时数据，已清除）")

    print(f"\n===== 智能体 E2E：{'通过' if ok else '未通过'} =====")
    return ok


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
