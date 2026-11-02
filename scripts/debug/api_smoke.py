"""用 httpx + ASGI transport 测 API（不需起真实端口）。"""
from __future__ import annotations

import httpx
from app.main import app

c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
import asyncio

async def main():
    r = await c.get("/api/targets")
    print("GET /api/targets:", r.status_code)
    targets = r.json()
    print("  target count:", len(targets))
    tid = targets[0]["id"]

    r = await c.post(f"/api/targets/{tid}/match")
    print("POST match:", r.status_code)
    m = r.json()
    print("  exact:", len(m["exact"]), "equivalent:", len(m["equivalent"]), "rejected:", len(m["rejected"]))

    r = await c.post(f"/api/targets/{tid}/inquiry-scripts")
    print("POST inquiry-scripts:", r.status_code, "count:", len(r.json()))

    r = await c.post(f"/api/targets/{tid}/comparison")
    print("POST comparison:", r.status_code)
    comp = r.json()
    print("  summary:", comp["summary"])

    await c.aclose()

asyncio.run(main())
print("API smoke OK")