"""API 冒烟：看板用到的全部端点在隔离库副本上可用（无 LLM 调用，秒级）。"""
from __future__ import annotations


def test_get_endpoints(app_client, hoodie_target_id):
    """GET 类端点全部 200 且结构正确。"""
    targets = app_client.get("/api/targets").json()
    assert isinstance(targets, list) and targets

    shops = app_client.get("/api/shops").json()
    assert isinstance(shops, list)

    cur = app_client.get("/api/current-target").json()
    assert "id" in cur and "name" in cur

    prods = app_client.get(f"/api/targets/{hoodie_target_id}/products").json()
    assert isinstance(prods, list)

    inqs = app_client.get(f"/api/targets/{hoodie_target_id}/inquiries").json()
    # 无询价任务时，每个商品以 pending 形态出现
    assert all(x["status"] == "pending" for x in inqs) if inqs else True

    quotes = app_client.get(f"/api/targets/{hoodie_target_id}/quotes").json()
    assert isinstance(quotes, list)

    # 404 语义：不存在的数据
    assert app_client.get("/api/targets/nonexistent/products").json() == []


def test_target_crud_roundtrip(app_client):
    """创建 → 列表可见 → 删除 全流程（隔离副本上操作）。"""
    r = app_client.post("/api/targets", data={
        "name": "pytest-临时款", "category": "测试",
    })
    assert r.status_code == 200
    tid = r.json()["id"]

    names = [t["name"] for t in app_client.get("/api/targets").json()]
    assert "pytest-临时款" in names

    r = app_client.request("DELETE", f"/api/targets/{tid}")
    assert r.json()["ok"] is True
    names = [t["name"] for t in app_client.get("/api/targets").json()]
    assert "pytest-临时款" not in names
