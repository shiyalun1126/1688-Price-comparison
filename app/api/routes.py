"""REST 路由。"""
from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, Request, Response, UploadFile
from pydantic import BaseModel, Field

from app.comparison.comparison import run_comparison
from app.inquire import detail_facts
from app.inquire.dialogue import GAP_ASKS, GAP_LABELS
from app.inquire.engine import get_engine
from app.inquire.inquiry import build_inquiry_script, clean_product_title, parse_quote
from app.matcher.matcher import Matcher
from app.models.entities import InquiryTask, Product, Shop, TargetStyle
from app.models.enums import MatchStatus, MatchTier
from app.uploads import UPLOAD_DIR as _UPLOAD_DIR_IMPORT
from app.uploads import ensure_upload_dir, save_upload, to_web_path, to_web_paths

router = APIRouter(prefix="/api")


# 上传目录
UPLOAD_DIR = ensure_upload_dir(_UPLOAD_DIR_IMPORT)


def _dump_target(ts: TargetStyle) -> dict:
    """模型 → 前端视图：图片路径转成网页 URL。"""
    d = ts.model_dump()
    d["image_paths"] = to_web_paths(ts.image_paths)
    return d


def _dump_product(p: Product) -> dict:
    d = p.model_dump()
    d["image_paths"] = to_web_paths(p.image_paths)
    return d


def _download_image(url: str, upload_dir: Path) -> str | None:
    """下载远程图片到本地上传目录，返回本地绝对路径。失败返回 None。"""
    if not url:
        return None
    try:
        import httpx

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Referer": "https://detail.1688.com/",
        }
        resp = httpx.get(url, headers=headers, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "image" not in ctype and not url.lower().split("?")[0].endswith(
            (".jpg", ".jpeg", ".png", ".webp")
        ):
            return None
        suffix = ".jpg"
        for ext in (".png", ".webp", ".gif", ".jpeg"):
            if ext in ctype or url.lower().split("?")[0].endswith(ext):
                suffix = ext
                break
        name = f"{uuid.uuid4().hex}{suffix}"
        path = upload_dir / name
        path.write_bytes(resp.content)
        return str(path)
    except Exception:
        return None


def _store(request: Request):
    return request.app.state.store


# ---- 目标款 ---------------------------------------------------------------
class TargetCreate(BaseModel):
    name: str
    description: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    reference_price: float | None = None
    category: str | None = None


@router.get("/targets")
def list_targets(request: Request):
    return [_dump_target(t) for t in _store(request).list_target_styles()]


# 「当前选中的目标款」持久化在 app_setting 表（重启不丢、多 worker 安全），
# 书签脚本动态查询用，避免把 ID 写死进书签
_CURRENT_TARGET_KEY = "current_target_id"


@router.get("/current-target")
def get_current_target(request: Request):
    """返回当前应导入到的目标款。书签脚本每次点击时查询。"""
    store = _store(request)
    ts = store.list_target_styles()
    saved = store.get_setting(_CURRENT_TARGET_KEY)
    # 若记录的当前款已不存在，退回第一个
    if saved:
        for t in ts:
            if t.id == saved:
                return {"id": t.id, "name": t.name}
    if ts:
        store.set_setting(_CURRENT_TARGET_KEY, ts[0].id)
        return {"id": ts[0].id, "name": ts[0].name}
    return {"id": None, "name": None}


class SetCurrentTarget(BaseModel):
    target_id: str


@router.post("/current-target")
def set_current_target(request: Request, body: SetCurrentTarget):
    """设置当前目标款（页面选中时调用）。"""
    store = _store(request)
    if not store.get_target_style(body.target_id):
        return {"ok": False, "error": "目标款不存在"}
    store.set_setting(_CURRENT_TARGET_KEY, body.target_id)
    return {"ok": True, "target_id": body.target_id}


@router.delete("/targets/{target_id}")
def delete_target(request: Request, target_id: str):
    """删除目标款及其全部关联数据（不可恢复）。"""
    store = _store(request)
    if not store.get_target_style(target_id):
        return {"ok": False, "error": "目标款不存在"}
    counts = store.delete_target_style(target_id)
    # 若删的正是「当前目标款」，清掉指针，下次查询自动回落到第一个
    if store.get_setting(_CURRENT_TARGET_KEY) == target_id:
        store.set_setting(_CURRENT_TARGET_KEY, "")
    return {"ok": True, "deleted": counts}


@router.post("/targets")
def create_target(
    request: Request,
    name: str = Form(...),
    image: UploadFile | None = File(None),
    description: str | None = Form(None),
    attributes_json: str | None = Form("{}"),
    reference_price: str | None = Form(None),
    category: str | None = Form(None),
):
    attrs = {}
    if attributes_json:
        try:
            attrs = json.loads(attributes_json)
        except json.JSONDecodeError:
            return {"error": "attributes_json 格式错误"}

    image_paths: list[str] = []
    if image and image.filename:
        image_paths.append(save_upload(image, UPLOAD_DIR))

    ts = TargetStyle(
        id=uuid.uuid4().hex,
        name=name,
        description=description,
        attributes=attrs,
        reference_price=float(reference_price) if reference_price else None,
        category=category,
        image_paths=image_paths,
    )
    _store(request).save_target_style(ts)
    return _dump_target(ts)


class CreateTargetFromUrl(BaseModel):
    """从 URL 创建目标款（书签脚本用）。"""
    title: str
    image_url: str
    price: float | None = None
    category: str | None = None
    description: str | None = None


@router.post("/targets/from-url")
def create_target_from_url(request: Request, body: CreateTargetFromUrl):
    """接收书签脚本传来的图片 URL，下载后创建目标款。"""
    store = _store(request)
    img_path = _download_image(body.image_url, UPLOAD_DIR)
    image_paths = [img_path] if img_path else []

    ts = TargetStyle(
        id=uuid.uuid4().hex,
        name=body.title,
        description=body.description or "",
        attributes={"品类": body.category} if body.category else {},
        reference_price=body.price,
        category=body.category,
        image_paths=image_paths,
    )
    store.save_target_style(ts)
    result = _dump_target(ts)
    result["ok"] = True
    return result


@router.post("/targets/{target_id}/upload-image")
def upload_target_image(request: Request, target_id: str, image: UploadFile = File(...)):
    store = _store(request)
    ts = store.get_target_style(target_id)
    if not ts:
        return {"error": "目标款不存在"}
    path = save_upload(image, UPLOAD_DIR)
    ts.image_paths.append(path)
    ts.updated_at = datetime.now()
    store.save_target_style(ts)
    return {"image_path": path, "target_id": target_id}


# ---- 门店 -----------------------------------------------------------------
class ShopCreate(BaseModel):
    name: str
    aliww: str | None = None
    url: str | None = None
    location: str | None = None


@router.get("/shops")
def list_shops(request: Request):
    return [s.model_dump() for s in _store(request).list_shops()]


@router.post("/shops")
def create_shop(request: Request, body: ShopCreate):
    s = Shop(id=uuid.uuid4().hex, **body.model_dump())
    _store(request).save_shop(s)
    return s.model_dump()


# ---- 候选商品 --------------------------------------------------------------
class ProductCreate(BaseModel):
    target_style_id: str
    shop_id: str
    title: str
    url: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    listed_price: float | None = None
    moq: int | None = None


@router.post("/products")
def create_product(
    request: Request,
    target_style_id: str = Form(...),
    shop_id: str = Form(...),
    title: str = Form(...),
    image: UploadFile | None = File(None),
    url: str | None = Form(None),
    attributes_json: str | None = Form("{}"),
    listed_price: str | None = Form(None),
    moq: str | None = Form(None),
):
    attrs = {}
    if attributes_json:
        try:
            attrs = json.loads(attributes_json)
        except json.JSONDecodeError:
            return {"error": "attributes_json 格式错误"}

    image_paths: list[str] = []
    if image and image.filename:
        image_paths.append(save_upload(image, UPLOAD_DIR))

    p = Product(
        id=uuid.uuid4().hex,
        target_style_id=target_style_id,
        shop_id=shop_id,
        title=title,
        image_paths=image_paths,
        url=url,
        attributes=attrs,
        listed_price=float(listed_price) if listed_price else None,
        moq=int(moq) if moq else None,
    )
    _store(request).save_product(p)
    return _dump_product(p)


@router.get("/targets/{target_id}/products")
def list_products(request: Request, target_id: str):
    return [_dump_product(p) for p in _store(request).list_products(target_id)]


@router.post("/products/{product_id}/detail-facts")
def refresh_detail_facts(request: Request, product_id: str, force: bool = False):
    """抓一次商品详情页，看看「页面已知」到底读到了什么（也供前端刷新用）。

    询价开始前会自动抓一次；这个接口是给人核对解析结果、或在页面改动后重抓。
    """
    store = _store(request)
    product = store.get_product(product_id)
    if not product:
        return {"error": "商品不存在"}
    if not (product.url or "").startswith("http"):
        return {"error": "该商品没有 1688 链接"}
    cached = detail_facts.load(product)
    if cached and not force:
        return {"cached": True, "facts": cached.to_dict(),
                "summary": cached.summary(product.moq or 100)}
    from app.browser.detail import fetch_offer_facts
    facts, why = fetch_offer_facts(product.url)
    if not facts:
        return {"error": why or "抓取失败", "cached": bool(cached)}
    detail_facts.attach(product, facts)
    store.save_product(product)
    return {"cached": False, "facts": facts.to_dict(),
            "summary": facts.summary(product.moq or 100)}


# ---- 浏览器书签导入 --------------------------------------------------------
class ImportProduct(BaseModel):
    target_style_id: str
    shop_id: str | None = None
    title: str
    image_url: str | None = None
    price: float | None = None
    url: str | None = None
    shop_name: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    attributes_json: str | None = None


@router.post("/import-product")
def import_product(request: Request, body: ImportProduct):
    """接收书签脚本从 1688 商品页抓取的标题/主图/价格/属性，下载图片并入库。"""
    store = _store(request)
    target = store.get_target_style(body.target_style_id)
    if not target:
        return {"ok": False, "error": "目标款不存在"}

    # 门店：优先用传入 shop_id；店铺名优先取链接里的 uid 参数（旺旺链接中的真实卖家），
    # 防止书签在聊天/搜索页把登录账号的公司名误当卖家
    from urllib.parse import parse_qs, unquote, urlparse

    shop_id = body.shop_id
    if not shop_id:
        uid = (parse_qs(urlparse(body.url or "").query).get("uid") or [""])[0]
        name = (unquote(uid).strip() if uid else "") or (body.shop_name or "1688店铺").strip()
        found = next((s for s in store.list_shops() if s.name == name), None)
        if found:
            shop_id = found.id
        else:
            from app.models.entities import Shop

            s = Shop(id=uuid.uuid4().hex, name=name)
            store.save_shop(s)
            shop_id = s.id

    # 属性：兼容 dict 与 json 字符串两种传法
    attrs = dict(body.attributes or {})
    if body.attributes_json:
        try:
            extra = json.loads(body.attributes_json)
            if isinstance(extra, dict):
                attrs.update(extra)
        except json.JSONDecodeError:
            pass

    image_paths: list[str] = []
    if body.image_url:
        local = _download_image(body.image_url, UPLOAD_DIR)
        if local:
            image_paths.append(local)

    p = Product(
        id=uuid.uuid4().hex,
        target_style_id=body.target_style_id,
        shop_id=shop_id,
        title=body.title,
        image_paths=image_paths,
        url=body.url,
        listed_price=body.price,
        attributes=attrs,
    )
    store.save_product(p)
    return {"ok": True, "product": _dump_product(p), "shop_id": shop_id}


class CrawlBody(BaseModel):
    keywords: str
    max_items: int = 20


@router.get("/browser/status")
def browser_status(response: Response):
    """抓取浏览器（CDP 9222）是否就绪。

    只查端口，不打开页面、不碰 1688 —— 页面上那个小圆点每几秒问一次，
    必须是纯本地判断。
    """
    from app.browser import cdp

    ready = cdp.is_available()
    # 启动命令只在这里拼一处，前端照着显示；用解释器自身路径，换 conda/venv 都不用改前端。
    start_cmd = f'"{sys.executable}" scripts/start_browser.py --hold'
    # 探针结果不缓存：浏览器关了还显示「就绪」，比不知道更糟
    response.headers["Cache-Control"] = "no-store"
    return {
        "ok": True,
        "ready": ready,
        "start_cmd": start_cmd,
        "hint": "" if ready else f"先在项目目录执行：{start_cmd}（首次需扫码登录 1688）",
    }


@router.post("/targets/{target_id}/crawl")
def crawl_target(request: Request, target_id: str, body: CrawlBody):
    """按关键词在抓取浏览器里搜 1688，把结果落成当前目标款的候选商品。

    只读抓取，不发消息、不下单。这是页面上「找货源」的主入口：书签导入退成兜底，
    因为书签要装、换浏览器就丢、手机上根本用不了。
    """
    store = _store(request)
    target = store.get_target_style(target_id)
    if not target:
        return {"ok": False, "error": "目标款不存在"}

    from app.browser.ingest import crawl_into_target

    return crawl_into_target(store, target, body.keywords, body.max_items)


# ---- 匹配 -----------------------------------------------------------------
@router.post("/targets/{target_id}/match")
def run_match(request: Request, target_id: str, incremental: bool = True):
    """执行视觉匹配。

    增量模式（默认开启）：已判定过的商品直接复用结果，只匹配新商品，
    大幅减少重复调用。传 incremental=false 可强制全部重算。
    """
    store = _store(request)
    target = store.get_target_style(target_id)
    if not target:
        return {"ok": False, "error": "目标款不存在"}
    products = store.list_products(target_id)
    shops = {s.id: s for s in store.list_shops()}

    # 增量：复用已判定且图片/属性未变的商品
    reused = 0
    to_match: list[Product] = []
    pre_outcomes: list = []
    from app.matcher.matcher import MatchOutcome
    from app.models.enums import MatchStatus, MatchTier

    for p in products:
        same_image = bool(p.image_paths) and p.match_status != MatchStatus.CANDIDATE
        if incremental and same_image and p.matched_at is not None:
            tier = p.match_tier
            pre_outcomes.append(MatchOutcome(p.id, p.match_status, tier,
                                             p.match_score or 0.0, p.match_reason))
            reused += 1
        else:
            to_match.append(p)

    t0 = __import__("time").time()
    matcher = Matcher()
    cls = matcher.run(target, to_match)   # 并发数走 LLM_VISION_CONCURRENCY（默认 4）
    elapsed = __import__("time").time() - t0

    # 合并增量结果
    from app.matcher.matcher import classify as _classify
    merged = pre_outcomes + (cls.exact + cls.equivalent + cls.rejected + cls.candidate)
    cls = _classify(merged)

    product_map = {p.id: p for p in products}

    def dump(m):
        p = product_map.get(m.product_id)
        shop = shops.get(p.shop_id) if p else None
        return {
            "product_id": m.product_id,
            "status": m.status.value,
            "tier": m.tier.value if m.tier else None,
            "confidence": m.confidence,
            "reason": m.reason,
            "product": _dump_product(p) if p else None,
            "shop_name": shop.name if shop else None,
        }

    # 回写匹配状态（只回写本次新算的）
    now = datetime.now()
    for m in (cls.exact + cls.equivalent + cls.rejected + cls.candidate):
        p = product_map.get(m.product_id)
        if p and p.id in {x.id for x in to_match}:
            p.match_status = m.status
            p.match_tier = m.tier
            p.match_score = m.confidence
            p.match_reason = m.reason
            p.matched_at = now
            store.save_product(p)

    return {
        "exact": [dump(m) for m in cls.exact],
        "equivalent": [dump(m) for m in cls.equivalent],
        "rejected": [dump(m) for m in cls.rejected],
        "candidate": [dump(m) for m in cls.candidate],
        "stats": {
            "total": len(products),
            "matched_now": len(to_match),
            "reused": reused,
            "elapsed_sec": round(elapsed, 1),
        },
    }


# ---- 询价工作台 -----------------------------------------------------------
class InquiryCreate(BaseModel):
    target_style_id: str
    product_id: str
    buyer_quantity: int | None = None


class InquiryStatusUpdate(BaseModel):
    status: str  # draft/sent/replied/quoted/closed


class InquiryReply(BaseModel):
    reply: str


def _inquiry_view(i, product, shop, session=None):
    """把 InquiryTask 组合成前端好用的视图。

    session 存在时附带自动谈判的运行时状态（轮次、缺口、阶段、暂停），
    前端据此显示「自动谈判中 / 待追问：运费」这类信息。
    """
    v = {
        "id": i.id,
        "product_id": i.product_id,
        "shop_id": i.shop_id,
        "shop_name": shop.name if shop else i.shop_id,
        "title": clean_product_title(product.title, 60) if product else "",
        "script": i.script,
        "status": i.status.value,
        "buyer_quantity": i.buyer_quantity,
        "created_at": i.created_at.isoformat(),
        "auto": False,
    }
    if session is not None:
        v.update({
            "auto": True,
            "session_id": session.id,
            "round": session.round,
            "gap": session.gap,
            "gap_labels": [GAP_LABELS.get(g, g) for g in session.gap],
            "stage": session.stage.value,
            "paused": session.paused,
            "channel": session.channel.value,
            "error": session.error,
            "last_msg_at": session.last_msg_at.isoformat() if session.last_msg_at else None,
        })
    return v


_TIER_RANK = {"exact": 0, "equivalent": 1, "unverified": 2, "rejected": 3}


def _inquiry_sort_key(v: dict):
    """与比价结论一致的排序：核验分层优先，同层价格升序（无价格排最后）。"""
    price = v.get("price")
    return (
        _TIER_RANK.get(v.get("tier") or "unverified", 2),
        1 if price is None else 0,
        price if price is not None else 0,
    )


@router.get("/targets/{target_id}/inquiries")
def list_inquiries(request: Request, target_id: str):
    """列出某目标款的所有询价任务（含未创建询价的候选商品）。

    排序与智能体控制台/比价结论一致：完全同款 → 等效替代 → 未核验 → 已排除，
    同层内按有效价格（有报价用报价，否则用标价）升序。
    """
    store = _store(request)
    target = store.get_target_style(target_id)
    if not target:
        return {"error": "目标款不存在"}
    inquiries = store.list_inquiries(target_id)
    inq_by_product = {i.product_id: i for i in inquiries}
    shops = {s.id: s for s in store.list_shops()}
    products = store.list_products(target_id)
    prod_by_id = {p.id: p for p in products}
    sessions = {s.inquiry_id: s for s in store.list_sessions(target_id)}

    # 每个商品最新一条有效报价（>0；寒暄回复解析出的 ¥0 不算，比标价更真实，优先采用）
    quote_by_product: dict[str, tuple] = {}
    for q in sorted(store.list_quotes(target_id), key=lambda x: x.quoted_at):
        if q.price is None or q.price <= 0:
            continue
        quote_by_product[q.product_id] = (q.price, "quoted")

    view = []
    # 已有询价任务的
    for i in inquiries:
        v = _inquiry_view(i, prod_by_id.get(i.product_id), shops.get(i.shop_id),
                          sessions.get(i.id))
        view.append(_attach_match_info(v, prod_by_id.get(i.product_id), quote_by_product))
    # 还没询价的商品
    for p in products:
        if p.id not in inq_by_product:
            v = {
                "id": None, "product_id": p.id, "shop_id": p.shop_id,
                "shop_name": shops.get(p.shop_id).name if shops.get(p.shop_id) else p.shop_id,
                "title": clean_product_title(p.title, 60), "script": "", "status": "pending",
                "buyer_quantity": None, "created_at": None,
            }
            view.append(_attach_match_info(v, p, quote_by_product))
    view.sort(key=_inquiry_sort_key)
    return view


def _attach_match_info(v: dict, p, quote_by_product: dict) -> dict:
    """给询价视图补上核验分层 + 有效价格 + 缩略图（供排序与前端展示）。"""
    tier = None
    if p is not None:
        tier = p.match_tier.value if p.match_tier else None
        if p.match_status == MatchStatus.REJECTED:
            tier = "rejected"
        v["image"] = to_web_path(p.image_paths[0]) if p.image_paths else None
    v["tier"] = tier
    v["price"], v["price_source"] = quote_by_product.get(
        v["product_id"],
        (p.listed_price if p is not None else None, "listed" if (p and p.listed_price is not None) else None),
    )
    return v


@router.post("/inquiries")
def create_inquiry(request: Request, body: InquiryCreate):
    """创建询价任务并生成话术（若有同商品旧任务则复用并更新话术）。"""
    store = _store(request)
    target = store.get_target_style(body.target_style_id)
    if not target:
        return {"error": "目标款不存在"}
    products = store.list_products(body.target_style_id)
    product = next((p for p in products if p.id == body.product_id), None)
    if not product:
        return {"error": "商品不存在"}

    facts = detail_facts.load(product)
    # 页面写明的项不再问，预览出来的话术与实际发送口径保持一致
    asks = None
    if facts:
        known = facts.known_gaps(body.buyer_quantity)
        asks = [GAP_ASKS[k] for k in ("tax", "shipping", "stock", "lead_time")
                if k not in known]

    existing = store.get_inquiry_by_product(body.target_style_id, body.product_id)
    if existing:
        i = existing
        i.buyer_quantity = body.buyer_quantity
        i.script = build_inquiry_script(target, product, body.buyer_quantity,
                                        facts=facts, asks=asks)
        i.updated_at = datetime.now()
    else:
        i = InquiryTask(
            id=uuid.uuid4().hex, target_style_id=body.target_style_id,
            product_id=body.product_id, shop_id=product.shop_id,
            script=build_inquiry_script(target, product, body.buyer_quantity,
                                        facts=facts, asks=asks),
            buyer_quantity=body.buyer_quantity,
        )
    store.save_inquiry(i)
    return _inquiry_view(i, product, store.get_shop(i.shop_id))


@router.post("/inquiries/{inquiry_id}/status")
def update_inquiry_status(request: Request, inquiry_id: str, body: InquiryStatusUpdate):
    """切换询价状态。"""
    store = _store(request)
    i = store.get_inquiry(inquiry_id)
    if not i:
        return {"error": "询价任务不存在"}
    from app.models.enums import InquiryStatus
    if body.status not in InquiryStatus._value2member_map_:
        return {"error": "非法状态"}
    i.status = InquiryStatus(body.status)
    i.updated_at = datetime.now()
    store.save_inquiry(i)
    return {"id": i.id, "status": i.status.value}


def _auto_verify_quoted_product(store, target_id: str, product_id: str) -> None:
    """后台任务：卖家已给出真实报价的商品，若尚未过视觉核验，自动补跑单商品匹配。

    无图商品保持 candidate（视觉无法判定，靠报价本身核验），有图则升级判定。
    已判定过的不重跑（避免视觉抖动覆盖结论）。任何异常只打日志，不影响主流程。
    """
    try:
        target = store.get_target_style(target_id)
        product = next((p for p in store.list_products(target_id) if p.id == product_id), None)
        if not target or not product:
            return
        if product.match_status in (MatchStatus.VERIFIED_SAME, MatchStatus.VERIFIED_EQUIVALENT,
                                    MatchStatus.REJECTED):
            return
        cls = Matcher().run(target, [product], max_workers=1)
        outcomes = cls.exact + cls.equivalent + cls.rejected + cls.candidate
        for m in outcomes:
            product.match_status = m.status
            product.match_tier = m.tier
            product.match_score = m.confidence
            product.match_reason = m.reason
            product.matched_at = datetime.now()
            store.save_product(product)
            print(f"[auto-verify] {product_id[:8]} → {m.status.value} ({m.confidence:.2f})")
    except Exception as e:
        print(f"[auto-verify] 失败 product={product_id[:8]}: {e}")


@router.post("/inquiries/{inquiry_id}/parse-reply")
def parse_inquiry_reply(request: Request, inquiry_id: str, body: InquiryReply,
                        background: BackgroundTasks):
    """贴入卖家回复 → 解析报价 → 落库 → 任务置为已报价 → 后台自动核验该商品。"""
    store = _store(request)
    i = store.get_inquiry(inquiry_id)
    if not i:
        return {"error": "询价任务不存在"}
    target = store.get_target_style(i.target_style_id)
    products = store.list_products(i.target_style_id)
    product = next((p for p in products if p.id == i.product_id), None)
    if not target or not product:
        return {"error": "目标款或商品不存在"}

    quote = parse_quote(body.reply, target, product)
    quote.id = uuid.uuid4().hex
    quote.inquiry_id = i.id
    store.save_quote(quote)

    from app.models.enums import InquiryStatus
    # 只有解析出有效价格（>0）才算「已报价」；寒暄/无价格回复保持「已回复」，
    # 防止 ¥0.00 伪报价顶掉页面标价参与比价排序
    has_price = quote.price is not None and quote.price > 0
    i.status = InquiryStatus.QUOTED if has_price else InquiryStatus.REPLIED
    i.updated_at = datetime.now()
    store.save_inquiry(i)

    background.add_task(_auto_verify_quoted_product, store, i.target_style_id, i.product_id)
    return {"quote": quote.model_dump(), "inquiry_id": i.id,
            "status": "quoted" if has_price else "replied_no_price",
            "auto_verify": "scheduled"}


# ---- 比价 -----------------------------------------------------------------
@router.get("/targets/{target_id}/quotes")
def list_quotes(request: Request, target_id: str):
    return [q.model_dump() for q in _store(request).list_quotes(target_id)]


@router.post("/targets/{target_id}/comparison")
def comparison(request: Request, target_id: str, refresh: bool = False):
    store = _store(request)
    target = store.get_target_style(target_id)
    if not target:
        return {"error": "目标款不存在"}
    # 数据未变化时直接复用上次结论；?refresh=true 强制重算（含 LLM 话术）
    result = run_comparison(store, target, force_refresh=refresh)
    return result.model_dump()


@router.get("/targets/{target_id}/comparison")
def comparison_cached(request: Request, target_id: str):
    """只读当前指纹匹配的缓存结论（零 LLM），未命中返回 {cached:false}。

    供前端切款时自动回显：命中则直接渲染，未命中不打扰用户。
    """
    store = _store(request)
    target = store.get_target_style(target_id)
    if not target:
        return {"error": "目标款不存在"}
    result = run_comparison(store, target, cache_only=True)
    if result is None:
        return {"cached": False}
    data = result.model_dump()
    data["cached"] = True
    return data


_TIER_CN = {"exact": "完全同款", "equivalent": "等效替代", "unverified": "未核验", "rejected": "已排除"}
_STATUS_CN = {"pending": "未询价", "draft": "话术已备", "sent": "已发送",
              "replied": "已回复", "quoted": "已报价", "closed": "已关闭"}


def _fmt_lead(hours) -> str:
    if not hours:
        return "—"
    return f"{hours}h" if hours < 48 else f"{hours / 24:.0f}天"


@router.get("/targets/{target_id}/report")
def build_report(request: Request, target_id: str):
    """一键生成 Markdown 比价报告（复用带指纹缓存的比价结论，不触发 LLM）。"""
    store = _store(request)
    target = store.get_target_style(target_id)
    if not target:
        return {"error": "目标款不存在"}

    result = run_comparison(store, target)   # 命中缓存，演示路径零等待
    shops = {s.id: s for s in store.list_shops()}
    products = {p.id: p for p in store.list_products(target_id)}
    inquiries = store.list_inquiries(target_id)
    inq_by_product = {i.product_id: i for i in inquiries}
    quotes = store.list_quotes(target_id)
    quote_by_product: dict[str, object] = {}
    for q in sorted(quotes, key=lambda x: x.quoted_at):
        quote_by_product[q.product_id] = q

    L: list[str] = []
    L.append(f"# 比价报告：{target.name}")
    meta = [target.category or "未分类"]
    if target.reference_price:
        meta.append(f"参考价 ¥{target.reference_price:.2f}")
    meta.append(f"生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    # 数据时效：取标价抓取时间与报价时间中最近的一条作为「数据截至」
    price_times = [p.created_at for p in products.values() if p.created_at]
    price_times += [q.quoted_at for q in quotes if q.quoted_at]
    if price_times:
        meta.append(f"数据截至 {max(price_times).strftime('%Y-%m-%d %H:%M')}")
    L.append(f"> {' · '.join(meta)}")
    L.append("")
    L.append("> 注：所有价格均为抓取/报价时点的快照，下单前请与卖家重新确认。")

    L.append("## 一、比价结论")
    L.append(f"**综述**：{result.summary}")
    L.append(f"**建议**：{result.recommendation}")
    if result.negotiation_script:
        L.append("**议价话术**：")
        L.append("```")
        L.append(result.negotiation_script)
        L.append("```")
    L.append("")

    L.append("## 二、价格明细")
    if result.rows:
        L.append("| # | 店铺 | 商品 | 价格 | 折算价(100件) | 来源 | 核验 | MOQ | 含税 | 现货 | 时效 |")
        L.append("|---|------|------|------|--------------|------|------|-----|------|------|------|")
        for idx, r in enumerate(result.rows, 1):
            price = f"¥{r['price']:.2f}" if r.get("price") is not None else "—"
            norm = "—"
            if r.get("normalized_price") is not None:
                norm = f"¥{r['normalized_price']:.2f}" + (f"（{r['norm_note']}）" if r.get("norm_note") else "")
            src = "报价" if r.get("price_source") == "quoted" else "标价"
            stock = r.get("stock_status", "")
            stock_cn = {"in_stock": "现货", "pre_sale": "预售", "custom": "定制"}.get(stock, "—" if stock else "—")
            tax_cn = {"included": "含税", "excluded": "不含税"}.get(r.get("tax"), "—")
            L.append(f"| {idx} | {r['shop_name']} | {(r['title'] or '')[:24]} | {price} | {norm} | {src} "
                     f"| {_TIER_CN.get(r['tier'], '未核验')} | {r.get('moq') or '—'} | {tax_cn} | {stock_cn} | {_fmt_lead(r.get('lead_time_hours'))} |")
    else:
        L.append("（暂无可比数据）")
    L.append("")

    L.append("## 三、询价进度")
    if inquiries:
        L.append("| 店铺 | 商品 | 状态 |")
        L.append("|------|------|------|")
        for i in inquiries:
            p = products.get(i.product_id)
            L.append(f"| {shops.get(i.shop_id).name if shops.get(i.shop_id) else i.shop_id} "
                     f"| {(p.title if p else '')[:24]} | {_STATUS_CN.get(i.status.value, i.status.value)} |")
        n_quoted = sum(1 for i in inquiries if i.status.value == "quoted")
        L.append("")
        L.append(f"共 {len(inquiries)} 家询价，已拿到报价 {n_quoted} 家。")
    else:
        L.append("（尚未发起询价）")
    L.append("")

    real_quotes = [q for q in quote_by_product.values()]
    if real_quotes:
        L.append("## 四、报价汇总（卖家回复原文解析）")
        L.append("| 店铺 | 报价 | MOQ | 含税 | 现货 | 时效 | 说明 |")
        L.append("|------|------|-----|------|------|------|------|")
        pmap = products
        for q in real_quotes:
            p = pmap.get(q.product_id)
            shop = shops.get(q.shop_id)
            tax_cn = {"included": "含税", "excluded": "不含税", "unknown": "未知"}.get(q.tax.value, "—")
            stock_cn = {"in_stock": "现货", "pre_sale": "预售", "custom": "定制", "unknown": "未知"}.get(q.stock_status.value, "—")
            price_cn = f"¥{q.price:.2f}" if q.price is not None else "—"
            L.append(f"| {shop.name if shop else q.shop_id} | {price_cn} | {q.moq or '—'} | {tax_cn} "
                     f"| {stock_cn} | {_fmt_lead(q.lead_time_hours)} | {q.condition_note or '—'} |")
        L.append("")

    return {"target": target.name, "markdown": "\n".join(L)}


# ---- 智能体（ReAct Agent）--------------------------------------------------
class AgentRequest(BaseModel):
    goal: str
    target_id: str | None = None


@router.post("/agent/run")
def agent_run(request: Request, body: AgentRequest):
    """让智能体自主完成一次比价任务，返回结论 + 推理轨迹 + token 用量。"""
    from app.agent.service import run_agent

    store = _store(request)
    if not body.goal.strip():
        return {"error": "goal 不能为空"}
    return run_agent(store, body.goal, body.target_id)


@router.get("/agent/plan")
def agent_plan(request: Request, target_id: str):
    """当前目标款进行中的采购计划（长任务规划）。"""
    store = _store(request)
    plan = store.get_active_plan(target_id)
    return plan.model_dump(mode="json") if plan else {"active": False}


@router.get("/agent/history")
def agent_history(request: Request, target_id: str | None = None, limit: int = 20):
    """智能体运行历史（前端刷新后据此恢复轨迹）。"""
    store = _store(request)
    runs = store.list_agent_runs(target_id, limit=min(limit, 50))
    return [r.model_dump(mode="json") for r in runs]


@router.delete("/agent/history")
def agent_history_clear(request: Request, target_id: str | None = None):
    """清空智能体运行历史（可按目标款）。"""
    store = _store(request)
    return {"cleared": store.clear_agent_runs(target_id)}


@router.get("/usage")
def usage_stats(request: Request):
    """进程级 token 用量累计 + 按配置单价的成本折算。"""
    from app.llm.usage import USAGE, cost_cny

    snap = USAGE.snapshot()
    return {
        **snap,
        "cost_cny": cost_cny(snap),
        "price_configured": bool(
            os.getenv("LLM_INPUT_PRICE_PER_M") or os.getenv("LLM_OUTPUT_PRICE_PER_M")
        ),
    }

# ---- 自动谈判（会话引擎）---------------------------------------------------
class InjectText(BaseModel):
    text: str


class PauseBody(BaseModel):
    paused: bool = True


class AutoBatch(BaseModel):
    create_missing: bool = True   # 还没建询价任务的候选商品，顺手建一个再入队
    limit: int = 8                # 单次最多发起几家（自动谈判不该一次轰几十家）
    channel: str = ""             # mock=剧本演示（不碰真实账号）/ cdp=真发 / 空=跟随环境变量


def _session_view(s) -> dict:
    """会话运行时状态 → 前端视图。"""
    return {
        "id": s.id,
        "inquiry_id": s.inquiry_id,
        "product_id": s.product_id,
        "shop_id": s.shop_id,
        "offer_url": s.offer_url,
        "channel": s.channel.value,
        "stage": s.stage.value,
        "status": s.status.value,
        "round": s.round,
        "gap": s.gap,
        "gap_labels": [GAP_LABELS.get(g, g) for g in s.gap],
        "last_speaker": s.last_speaker.value if s.last_speaker else None,
        "last_msg_at": s.last_msg_at.isoformat() if s.last_msg_at else None,
        "next_action_at": s.next_action_at.isoformat() if s.next_action_at else None,
        "expires_at": s.expires_at.isoformat() if s.expires_at else None,
        "buyer_quantity": s.buyer_quantity,
        "floor_price": s.floor_price,
        "market_min": s.market_min,
        "paused": s.paused,
        "error": s.error,
    }


def _message_view(m) -> dict:
    return {
        "id": m.id,
        "role": m.role.value,
        "kind": m.kind,
        "text": m.text,
        "extracted": m.extracted,
        "screenshot": m.screenshot,
        "ts": m.ts.isoformat(),
    }


@router.post("/inquiries/{inquiry_id}/auto")
def auto_inquiry(request: Request, inquiry_id: str, channel: str = ""):
    """把一条询价任务交给智能体自动谈判（发送→追问→议价→收敛）。

    ?channel=mock 走剧本卖家（离线演示，不碰真实账号）；留空跟随 INQUIRY_CHANNEL。
    """
    return get_engine(_store(request)).enqueue(
        inquiry_id, require_verified=True, channel=channel)


@router.post("/targets/{target_id}/inquiries/auto")
def auto_inquiry_batch(request: Request, target_id: str,
                       body: AutoBatch | None = None):
    """批量发起自动询价：默认给还没建询价任务的候选商品也建上。

    排除「已排除」的商品——只对可能同款的店发消息，这既是省钱也是风控：
    发得越少越像正常买家。
    """
    body = body or AutoBatch()
    store = _store(request)
    target = store.get_target_style(target_id)
    if not target:
        return {"error": "目标款不存在"}

    # 只对「已核验：完全同款 / 等效替代」的店开口。
    # 被排除的不打扰；仅图相似召回、还没核验的更不能问——连是不是这款都没确认，
    # 卖家报回来的价跟目标款没关系，白花一条配额，还容易被当成乱发的号。
    wanted = {MatchTier.EXACT, MatchTier.EQUIVALENT}
    all_products = store.list_products(target_id)
    products = [p for p in all_products
                if p.match_tier in wanted and (p.url or "").startswith("http")]
    unverified = sum(1 for p in all_products
                     if p.match_status == MatchStatus.CANDIDATE)

    def _rank(p):
        # 同款先谈，等效替代补位；同层按标价从低到高
        return (0 if p.match_tier == MatchTier.EXACT else 1,
                p.listed_price if p.listed_price is not None else 9e9)

    # 一家店只聊一次：同店铺的多个商品取「核验最好、标价最低」的那个去谈，
    # 否则同一个老板会被同一件事问三遍，既冒犯也容易被判营销。
    by_shop: dict[str, object] = {}
    for p in sorted(products, key=_rank):
        by_shop.setdefault(p.shop_id, p)

    # 这家店为这个款已经聊过（无论聊到哪一步）就不再重复打扰：
    # 「这款多少钱」问过一次就该有答案，换个链接再问一遍只会让老板反感。
    # 只有之前被手动关闭（closed）的才允许重新发起。
    busy = {s.shop_id for s in store.list_sessions(target_id)
            if s.status.value != "closed"}
    limit = max(1, min(int(body.limit or 8), 20))
    picked = [p for p in by_shop.values() if p.shop_id not in busy][:limit]

    from datetime import datetime as _dt
    from app.models.entities import InquiryTask
    from app.models.enums import InquiryStatus
    created = 0
    queued, skipped, errors = [], [], []
    for p in picked:
        inq = store.get_inquiry_by_product(target_id, p.id)
        if inq is None:
            if not body.create_missing:
                continue
            inq = InquiryTask(
                id=f"inq-{uuid.uuid4().hex[:12]}", target_style_id=target_id,
                product_id=p.id, shop_id=p.shop_id,
                script=build_inquiry_script(target, p, None),
                status=InquiryStatus.DRAFT, buyer_quantity=None,
                created_at=_dt.now(), updated_at=_dt.now(),
            )
            store.save_inquiry(inq)
            created += 1
        if inq.status == InquiryStatus.QUOTED:
            skipped.append(inq.id)
            continue
        r = get_engine(store).enqueue(inq.id, require_verified=True,
                                      channel=body.channel)
        if r.get("error"):
            errors.append({"inquiry_id": inq.id, "error": r["error"]})
        elif r.get("already_running"):
            skipped.append(inq.id)
        else:
            queued.append(r["session_id"])

    return {
        "已入队": len(queued),
        "新建询价任务": created,
        "跳过": len(skipped),
        "失败": errors,
        "session_ids": queued,
        "候选池": len(products),
        "未核验跳过": unverified,
        "本次上限": limit,
        "今日剩余配额": get_engine(store).quota_left(),
    }


@router.get("/inquiry-engine")
def inquiry_engine_status(request: Request):
    """引擎状态：是否在跑、今日配额、各状态会话数、是否被风控阻断。"""
    return get_engine(_store(request)).status()


@router.post("/inquiry-engine/run")
def inquiry_engine_run(request: Request, limit: int = 0):
    """立即推进一轮（演示/调试用，不用等定时 tick）。"""
    return {"results": get_engine(_store(request)).run_once(limit=limit)}


@router.post("/inquiry-engine/resume")
def inquiry_engine_resume(request: Request):
    """人工处理完验证码/登录后，一键恢复被暂停的会话。"""
    return get_engine(_store(request)).resume_engine()


@router.get("/sessions")
def list_active_sessions(request: Request, limit: int = 40):
    """在谈会话名单：点引擎状态条时看「到底在跟哪几家聊、聊到哪一步」。

    「未收工」= 状态还没到已报价/已结束/已超时/需人工；其中被人工暂停的
    单独标出来，避免把「暂停」误读成「引擎正在跑」。
    """
    store = _store(request)
    out = []
    for s in store.list_active_sessions()[:limit]:
        shop = store.get_shop(s.shop_id)
        product = store.get_product(s.product_id)
        v = _session_view(s)
        v["shop_name"] = shop.name if shop else "(店铺已删除)"
        v["title"] = clean_product_title(product.title, 48) if product else ""
        # 名单要的是「对方/我们最后说了什么」，不是引擎的内部状态行。
        # 只取买卖双方的消息，一条都没有时才回退到最近一条系统事件。
        msgs = store.list_messages(s.id)
        last = next((m for m in reversed(msgs) if m.role.value in ("seller", "buyer")), None)
        if last is None and msgs:
            last = msgs[-1]
        v["last_text"] = (last.text or "")[:70] if last else ""
        v["last_role"] = last.role.value if last else None
        v["last_kind"] = last.kind if last else None
        out.append(v)
    return {"sessions": out, "total": len(out), "paused": sum(1 for v in out if v["paused"])}


@router.get("/sessions/{session_id}")
def get_session_detail(request: Request, session_id: str):
    """会话详情：状态 + 完整消息流（前端对话视图）。"""
    store = _store(request)
    s = store.get_session(session_id)
    if not s:
        return {"error": "会话不存在"}
    product = store.get_product(s.product_id)
    shop = store.get_shop(s.shop_id)
    view = _session_view(s)
    # 抽屉标题要人话：店铺名 + 商品名，而不是一串 session id
    view["shop_name"] = shop.name if shop else ""
    view["title"] = clean_product_title(product.title, 80) if product else ""
    msgs = store.list_messages(session_id)
    return {"session": view, "messages": [_message_view(m) for m in msgs]}


@router.post("/sessions/{session_id}/pause")
def pause_session(request: Request, session_id: str, body: PauseBody | None = None):
    """暂停/恢复单个会话（人工要自己接管时先暂停）。"""
    body = body or PauseBody()
    return get_engine(_store(request)).pause(session_id, body.paused)


@router.post("/sessions/{session_id}/inject")
def inject_message(request: Request, session_id: str, body: InjectText):
    """人工插话：走同一通道发出去，并自动暂停引擎，避免两边打架。"""
    if not body.text.strip():
        return {"error": "消息不能为空"}
    return get_engine(_store(request)).inject(session_id, body.text.strip())
