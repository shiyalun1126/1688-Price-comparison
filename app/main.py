"""FastAPI 主应用 + 路由。"""
from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.inquire.engine import get_engine
from app.store.db import Store

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def create_app() -> FastAPI:
    # APP_DB 让「演示实例」能用一份独立的库：给别人点着试的时候，
    # 不能把真实采购价和店铺名单一起端过去，测试数据也不该写进生产库。
    db_path = Path(os.getenv("APP_DB") or (DATA_DIR / "app.db"))
    store = Store(db_path)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # 自动谈判引擎：后台线程推进会话状态机（无待办时不做事，开销可忽略）
        engine = get_engine(store)
        engine.start()
        try:
            yield
        finally:
            engine.stop()
            store.close()

    app = FastAPI(title="1688 比价智能体", version="0.1.0", lifespan=lifespan)

    # 书签脚本从 1688 商品页 POST 到本地服务，只放行 1688 域名 + 本机看板；
    # 不再使用 "*"：任意网页的 JS 都能读写本地采购数据、烧 LLM 额度
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        ],
        allow_origin_regex=r"^https?://([a-z0-9-]+\.)*1688\.com$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.store = store

    app.include_router(router)

    web_dir = Path(__file__).resolve().parent / "web"
    upload_dir = DATA_DIR / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=str(upload_dir)), name="uploads")
    app.mount("/css", StaticFiles(directory=str(web_dir / "css")), name="css")
    app.mount("/js", StaticFiles(directory=str(web_dir / "js")), name="js")
    if (web_dir / "static").exists():
        app.mount("/static", StaticFiles(directory=str(web_dir / "static")), name="static")

    # 静态资源没有指纹，改了 js/css 后浏览器还在用旧缓存，页面看着像没生效。
    # 按文件修改时间打上 ?v=，改一次换一次 URL，改完刷新立刻可见。
    _asset_ref = re.compile(r'((?:src|href)=")((?:css|js)/[^"?]+)(")')

    def _stamp_assets(html: str) -> str:
        def repl(m: re.Match[str]) -> str:
            f = web_dir / m.group(2)
            try:
                # 用纳秒：同一秒内连续改两次也能换 URL，不至于还取到旧文件
                ver = f.stat().st_mtime_ns
            except OSError:
                ver = 0
            return f"{m.group(1)}{m.group(2)}?v={ver}{m.group(3)}"
        return _asset_ref.sub(repl, html)

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        html = _stamp_assets((web_dir / "index.html").read_text(encoding="utf-8"))
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/bookmarklet", response_class=HTMLResponse)
    def bookmarklet_page() -> str:
        return (web_dir / "bookmarklet.html").read_text(encoding="utf-8")

    @app.get("/probe", response_class=HTMLResponse)
    def probe_page() -> str:
        return (web_dir / "probe.html").read_text(encoding="utf-8")

    @app.get("/probe-price", response_class=HTMLResponse)
    def probe_price_page() -> str:
        return (web_dir / "probe-price.html").read_text(encoding="utf-8")

    @app.get("/smart-bm", response_class=HTMLResponse)
    def smart_bm_page() -> str:
        return (web_dir / "smart-bm.html").read_text(encoding="utf-8")

    return app


app = create_app()
