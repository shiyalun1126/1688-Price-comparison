"""pytest 共享配置：隔离的 app fixture + 路径常量。

核心思路：测试跑在「真实库的临时副本」上（create_app 前把 DATA_DIR 指向
tmp_path 并复制 app.db），原始数据零风险，也避免 sqlite 连接跨测试串味。

httpx 的 ASGITransport 只支持 async，这里用 asyncio.run 包一个同步客户端，
测试代码保持同步写法，无需 pytest-asyncio 依赖。
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
REAL_DB = ROOT / "data" / "app.db"


class SyncASGIClient:
    """把 ASGI 应用包成同步 HTTP 客户端（每次请求独立 AsyncClient，测试够用）。"""

    def __init__(self, application: Any, base_url: str = "http://test") -> None:
        self._app = application
        self._base_url = base_url

    def _run(self, coro):
        return asyncio.run(coro)

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        async def go():
            transport = httpx.ASGITransport(app=self._app)
            async with httpx.AsyncClient(transport=transport, base_url=self._base_url) as c:
                return await c.request(method, url, **kwargs)
        return self._run(go())

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def delete(self, url: str, **kwargs) -> httpx.Response:
        return self.request("DELETE", url, **kwargs)


@pytest.fixture
def app_client(tmp_path):
    """返回指向「真实库副本」的同步测试客户端；原始库不被触碰。"""
    import app.main as main_module

    tmp_db = tmp_path / "app.db"
    if REAL_DB.exists():
        shutil.copy2(REAL_DB, tmp_db)

    orig_data_dir = main_module.DATA_DIR
    main_module.DATA_DIR = tmp_path
    try:
        application = main_module.create_app()
        yield SyncASGIClient(application)
        application.state.store.close()
    finally:
        main_module.DATA_DIR = orig_data_dir


@pytest.fixture
def hoodie_target_id(app_client):
    """数据最有料的目标款（带报价/参考价/分层）：优先「卫衣」，退化到第一个有商品的款。"""
    targets = app_client.get("/api/targets").json()
    for t in targets:
        if "卫衣" in t["name"]:
            return t["id"]
    for t in targets:
        if app_client.get(f"/api/targets/{t['id']}/products").json():
            return t["id"]
    pytest.skip("库中没有带商品的目标款")
