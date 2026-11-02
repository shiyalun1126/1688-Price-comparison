"""auto_import（浏览器自动导入）工具逻辑的单测：mock CDP 与图片下载，无网络秒级。"""
from __future__ import annotations

import pytest

from app.models.entities import TargetStyle


@pytest.fixture
def tool_store(tmp_path):
    from app.store.db import Store

    s = Store(tmp_path / "auto_test.db")
    s.save_target_style(TargetStyle(id="t1", name="316不锈钢保温杯", category="保温杯"))
    yield s
    s.close()


def _build(store):
    from app.agent.tools import build_tools

    tools = {t.name: t.func for t in build_tools(store, current_target_id="t1")}
    return tools


RAW_ITEMS = [
    {"offer_id": "111", "title": "316不锈钢保温杯500ml 哑光白", "price": 12.8,
     "shop": "永康市某某工贸有限公司", "image": "https://cbu01.alicdn.com/img/a.jpg"},
    {"offer_id": "222", "title": "智能保温杯男女水杯", "price": 15.5,
     "shop": "义乌市某某电子商务商行", "image": ""},
    {"offer_id": "333", "title": "弹跳盖汽车杯", "price": None,
     "shop": "", "image": ""},   # 无店名 → 落「卖家未记录」
]


@pytest.fixture
def mock_cdp(monkeypatch):
    """mock 掉 CDP 抓取与图片下载，隔离网络。"""
    from app.browser import cdp

    monkeypatch.setattr(cdp, "is_available", lambda: True)
    monkeypatch.setattr(cdp, "search_offers",
                        lambda kw, max_items=24: {"items": RAW_ITEMS, "login_required": False})
    monkeypatch.setattr(cdp, "download_image",
                        lambda url, d: "/uploads/mock.jpg" if url else None)
    return cdp


class TestAutoImport:
    def test_import_with_real_shops(self, tool_store, mock_cdp):
        tools = _build(tool_store)
        r = tools["auto_import"]("316不锈钢保温杯")
        assert r["导入数"] == 3
        assert r["图片落盘"] == 1
        assert r["跳过(已存在)"] == 0
        # 真实店铺名落库
        names = {s.name for s in tool_store.list_shops()}
        assert "永康市某某工贸有限公司" in names
        assert "义乌市某某电子商务商行" in names
        assert "1688搜索（卖家未记录）" in names
        # 商品带正确归属与图片
        p = tool_store.get_product("auto-111")
        assert p.listed_price == 12.8
        # 存绝对路径（与书签导入口径一致），指向 data/uploads/mock.jpg
        assert str(p.image_paths[0]).replace("\\", "/").endswith("data/uploads/mock.jpg")
        assert p.match_status.value == "candidate"

    def test_dedup_on_rerun(self, tool_store, mock_cdp):
        tools = _build(tool_store)
        tools["auto_import"]("保温杯")
        r2 = tools["auto_import"]("保温杯")
        assert r2["导入数"] == 0
        assert r2["跳过(已存在)"] == 3

    def test_max_items_clamped(self, tool_store, mock_cdp, monkeypatch):
        from app.browser import cdp

        seen = {}
        real = cdp.search_offers

        def spy(kw, max_items=24):
            seen["max"] = max_items
            return real(kw, max_items=max_items)

        monkeypatch.setattr(cdp, "search_offers", spy)
        tools = _build(tool_store)
        tools["auto_import"]("保温杯", max_items=999)
        assert seen["max"] == 40          # 上限钳制

    def test_browser_not_running(self, tool_store, monkeypatch):
        from app.browser import cdp

        monkeypatch.setattr(cdp, "is_available", lambda: False)
        tools = _build(tool_store)
        r = tools["auto_import"]("保温杯")
        assert "error" in r
        assert "start_browser" in r["解决办法"]

    def test_login_required(self, tool_store, monkeypatch):
        from app.browser import cdp

        monkeypatch.setattr(cdp, "is_available", lambda: True)
        monkeypatch.setattr(cdp, "search_offers",
                            lambda kw, max_items=24: {"items": [], "login_required": True})
        tools = _build(tool_store)
        r = tools["auto_import"]("保温杯")
        assert "登录" in r["error"]

    def test_empty_keywords(self, tool_store, mock_cdp):
        tools = _build(tool_store)
        assert "error" in tools["auto_import"]("  ")

    def test_no_target(self, tmp_path, mock_cdp):
        from app.store.db import Store
        from app.agent.tools import build_tools

        s = Store(tmp_path / "notarget.db")
        tools = {t.name: t.func for t in build_tools(s, current_target_id=None)}
        r = tools["auto_import"]("保温杯")
        assert "目标款" in r["error"]
        s.close()


class TestNormalizeItems:
    def test_normalize_filters_and_caps(self):
        from app.browser.cdp import normalize_items

        raw = [{"offer_id": 123, "title": "x", "price": "1.5", "shop": 0, "image": 1},
               {"offer_id": None},
               {"offer_id": "456", "title": "y", "price": 2.0, "shop": "店", "image": ""}] * 30
        out = normalize_items(raw, max_items=2)
        assert len(out) == 2
        assert out[0]["offer_id"] == "123"
        assert out[0]["price"] is None        # 字符串价格不算数
        assert out[0]["url"].endswith("/offer/123.html")
