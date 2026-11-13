"""API 层端到端：在库副本上走 POST /match（非增量），确认路由→Matcher→视觉全链路正常。"""
from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

import httpx

import app.main as main_module

ROOT = Path(__file__).resolve().parents[2]
TMP = Path(__file__).resolve().parent / "_api_e2e"
TMP.mkdir(parents=True, exist_ok=True)
shutil.copy2(ROOT / "data" / "app.db", TMP / "app.db")

orig = main_module.DATA_DIR
main_module.DATA_DIR = TMP
try:
    application = main_module.create_app()

    async def go():
        tr = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=tr, base_url="http://test", timeout=900) as c:
            targets = (await c.get("/api/targets")).json()
            tg = next(t for t in targets if t["name"] == "airpods")
            u0 = (await c.get("/api/usage")).json()
            t0 = time.time()
            r = await c.post(f"/api/targets/{tg['id']}/match?incremental=false")
            d = r.json()
            print(f"HTTP {r.status_code}  {time.time()-t0:.1f}s  stats={d['stats']}")
            u1 = (await c.get("/api/usage")).json()
            print(f"LLM 调用 {u1['requests']-u0['requests']} 次，"
                  f"prompt {u1['prompt_tokens']-u0['prompt_tokens']}，"
                  f"completion {u1['completion_tokens']-u0['completion_tokens']}")
            s = json.dumps(d, ensure_ascii=False)
            print(f"视觉失败回退次数: {s.count('视觉比对失败')}")
            print("分层: exact", len(d["exact"]), "equiv", len(d["equivalent"]),
                  "rejected", len(d["rejected"]), "candidate", len(d["candidate"]))
            for b in ("exact", "equivalent"):
                for x in d[b][:2]:
                    print(f"  {b} {x['confidence']} :: {x['reason'][:80]}")

    asyncio.run(go())
finally:
    main_module.DATA_DIR = orig
    application.state.store.close()
    shutil.rmtree(TMP, ignore_errors=True)
