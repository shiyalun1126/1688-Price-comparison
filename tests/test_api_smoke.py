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


def test_auto_inquiry_only_for_verified_products(app_client):
    """自动询价只对「完全同款 / 等效替代」发起。

    仅图相似召回、还没核验的商品，连是不是这款都没确认；拿它去问价，
    卖家报的数字跟目标款没关系，白花一条配额还像乱发的号。
    """
    cand = None
    for t in app_client.get("/api/targets").json():
        for p in app_client.get(f"/api/targets/{t['id']}/products").json():
            if p.get("match_status") == "candidate":
                cand = p
                break
        if cand:
            break
    if not cand:
        import pytest
        pytest.skip("库里没有未核验候选")

    inq = app_client.post("/api/inquiries", json={
        "target_style_id": cand["target_style_id"],
        "product_id": cand["id"],
    }).json()
    r = app_client.post(f"/api/inquiries/{inq['id']}/auto", json={}).json()
    assert "核验" in (r.get("error") or ""), r
    assert "session_id" not in r


def test_batch_auto_skips_unverified_and_rejected(app_client):
    """批量入口的候选池只含已核验两类；被排除、未核验都不在池子里。"""
    for t in app_client.get("/api/targets").json():
        prods = app_client.get(f"/api/targets/{t['id']}/products").json()
        if not prods:
            continue
        r = app_client.post(f"/api/targets/{t['id']}/inquiries/auto",
                            json={"create_missing": False, "limit": 8}).json()
        pool = r.get("候选池")
        assert pool is not None, r
        want = {p["id"] for p in prods
                if p.get("match_tier") in ("exact", "equivalent")
                and (p.get("url") or "").startswith("http")}
        # 候选池不会超过「已核验且有链接」的商品数
        assert pool <= len(want), (t["name"], pool, len(want))
