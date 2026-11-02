"""REST 路由。"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, Request, UploadFile
from pydantic import BaseModel, Field

from app.comparison.comparison import run_comparison
from app.inquire.inquiry import build_inquiry_script, parse_quote
from app.matcher.matcher import Matcher
from app.models.entities import InquiryTask, Product, Shop, TargetStyle
from app.models.enums import MatchStatus
from app.uploads import UPLOAD_DIR as _UPLOAD_DIR_IMPORT
from app.uploads import ensure_upload_dir, save_upload, to_web_paths

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


# 当前选中的目标款（书签脚本动态查询用，避免把 ID 写死进书签）
_CURRENT_TARGET_ID: str | None = None


@router.get("/current-target")
def get_current_target(request: Request):
    """返回当前应导入到的目标款。书签脚本每次点击时查询。"""
    global _CURRENT_TARGET_ID
    store = _store(request)
    ts = store.list_target_styles()
    # 若记录的当前款已不存在，退回第一个
    if _CURRENT_TARGET_ID:
        for t in ts:
            if t.id == _CURRENT_TARGET_ID:
                return {"id": t.id, "name": t.name}
    if ts:
        _CURRENT_TARGET_ID = ts[0].id
        return {"id": ts[0].id, "name": ts[0].name}
    return {"id": None, "name": None}


class SetCurrentTarget(BaseModel):
    target_id: str


@router.post("/current-target")
def set_current_target(request: Request, body: SetCurrentTarget):
    """设置当前目标款（页面选中时调用）。"""
    global _CURRENT_TARGET_ID
    store = _store(request)
    if not store.get_target_style(body.target_id):
        return {"ok": False, "error": "目标款不存在"}
    _CURRENT_TARGET_ID = body.target_id
    return {"ok": True, "target_id": body.target_id}


@router.delete("/targets/{target_id}")
def delete_target(request: Request, target_id: str):
    """删除目标款及其全部关联数据（不可恢复）。"""
    store = _store(request)
    if not store.get_target_style(target_id):
        return {"ok": False, "error": "目标款不存在"}
    counts = store.delete_target_style(target_id)
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
    cls = matcher.run(target, to_match, max_workers=8)
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


def _inquiry_view(i, product, shop):
    """把 InquiryTask 组合成前端好用的视图。"""
    return {
        "id": i.id,
        "product_id": i.product_id,
        "shop_id": i.shop_id,
        "shop_name": shop.name if shop else i.shop_id,
        "title": product.title if product else "",
        "script": i.script,
        "status": i.status.value,
        "buyer_quantity": i.buyer_quantity,
        "created_at": i.created_at.isoformat(),
    }


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

    # 每个商品最新一条报价（比标价更真实，优先采用）
    quote_by_product: dict[str, tuple] = {}
    for q in sorted(store.list_quotes(target_id), key=lambda x: x.quoted_at):
        quote_by_product[q.product_id] = (q.price, "quoted")

    view = []
    # 已有询价任务的
    for i in inquiries:
        v = _inquiry_view(i, prod_by_id.get(i.product_id), shops.get(i.shop_id))
        view.append(_attach_match_info(v, prod_by_id.get(i.product_id), quote_by_product))
    # 还没询价的商品
    for p in products:
        if p.id not in inq_by_product:
            v = {
                "id": None, "product_id": p.id, "shop_id": p.shop_id,
                "shop_name": shops.get(p.shop_id).name if shops.get(p.shop_id) else p.shop_id,
                "title": p.title, "script": "", "status": "pending",
                "buyer_quantity": None, "created_at": None,
            }
            view.append(_attach_match_info(v, p, quote_by_product))
    view.sort(key=_inquiry_sort_key)
    return view


def _attach_match_info(v: dict, p, quote_by_product: dict) -> dict:
    """给询价视图补上核验分层 + 有效价格（供排序与前端展示）。"""
    tier = None
    if p is not None:
        tier = p.match_tier.value if p.match_tier else None
        if p.match_status == MatchStatus.REJECTED:
            tier = "rejected"
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

    existing = store.get_inquiry_by_product(body.target_style_id, body.product_id)
    if existing:
        i = existing
        i.buyer_quantity = body.buyer_quantity
        i.script = build_inquiry_script(target, product, body.buyer_quantity)
        i.updated_at = datetime.now()
    else:
        i = InquiryTask(
            id=uuid.uuid4().hex, target_style_id=body.target_style_id,
            product_id=body.product_id, shop_id=product.shop_id,
            script=build_inquiry_script(target, product, body.buyer_quantity),
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
    i.status = InquiryStatus.QUOTED
    i.updated_at = datetime.now()
    store.save_inquiry(i)

    background.add_task(_auto_verify_quoted_product, store, i.target_style_id, i.product_id)
    return {"quote": quote.model_dump(), "inquiry_id": i.id, "status": "quoted",
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
    L.append(f"> {' · '.join(meta)}")
    L.append("")

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
        L.append("| # | 店铺 | 商品 | 价格 | 来源 | 核验 | MOQ | 含税 | 现货 | 时效 |")
        L.append("|---|------|------|------|------|------|-----|------|------|------|")
        for idx, r in enumerate(result.rows, 1):
            price = f"¥{r['price']:.2f}" if r.get("price") is not None else "—"
            src = "报价" if r.get("price_source") == "quoted" else "标价"
            stock = r.get("stock_status", "")
            stock_cn = {"in_stock": "现货", "pre_sale": "预售", "custom": "定制"}.get(stock, "—" if stock else "—")
            tax_cn = {"included": "含税", "excluded": "不含税"}.get(r.get("tax"), "—")
            L.append(f"| {idx} | {r['shop_name']} | {(r['title'] or '')[:24]} | {price} | {src} "
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