"""抓取浏览器探针 + 在谈名单的接口契约。

这两个端点是页面自己高频轮询的轻接口（小圆点 15 秒一次、状态条点开一次），
浏览器没开、库里没会话都必须正常返回，否则前端会一直卡在「检查中」。
"""
from __future__ import annotations

from app.browser import cdp


def test_browser_status_shape(app_client):
    """结构稳定：ready 一定是 bool，启动命令由后端给（前端不再自己拼一份）。"""
    r = app_client.get("/api/browser/status")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["ready"], bool)
    assert "scripts/start_browser.py --hold" in body["start_cmd"]


def test_browser_status_no_store(app_client):
    """探针不能缓存：浏览器关了还显示「就绪」，比不知道更糟。"""
    r = app_client.get("/api/browser/status")
    assert "no-store" in r.headers.get("cache-control", "").lower()


def test_browser_status_ready_branch(app_client, monkeypatch):
    monkeypatch.setattr(cdp, "is_available", lambda: True)
    body = app_client.get("/api/browser/status").json()
    assert body["ready"] is True
    assert body["hint"] == ""


def test_browser_status_down_branch(app_client, monkeypatch):
    monkeypatch.setattr(cdp, "is_available", lambda: False)
    body = app_client.get("/api/browser/status").json()
    assert body["ready"] is False
    assert "start_browser.py" in body["hint"]


def test_sessions_list_shape(app_client):
    """在谈名单：空库也得给出三个键，前端直接 .sessions.map 不炸。"""
    r = app_client.get("/api/sessions")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["sessions"], list)
    assert body["total"] == len(body["sessions"])
    assert body["paused"] <= body["total"]
