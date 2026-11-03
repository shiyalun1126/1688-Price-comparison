"""FastAPI 主应用 + 路由。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.store.db import Store

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def create_app() -> FastAPI:
    store = Store(DATA_DIR / "app.db")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
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

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (web_dir / "index.html").read_text(encoding="utf-8")

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
