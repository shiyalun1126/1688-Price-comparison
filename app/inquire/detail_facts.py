"""商品详情页的公开信息：抓下来当「已知条件」，别再拿它当缺口去问卖家。

动机（2026-11-09）：详情页上明明写着「1 顶起批 ¥15.8、100 顶以上 ¥14.8、
运费 ¥3.3 起、库存 418 顶」，智能体照样一句一句问「什么价」「起订量多少」
「运费怎么算」——卖家回一句「页面上都有」，问价就成了笑话，也白烧一轮。

这里只做两件事：
1. `parse_detail_text()`：从详情页 innerText 里抽出价格/阶梯价/起订/运费/库存/时效。
   纯函数，不碰浏览器，可以拿真实页面文本离线回归。
2. `load()/attach()`：把结果挂在 `product.attributes["_detail"]` 上（JSON 字符串），
   不新增表、不改 schema，旧库直接兼容。

注意边界：这是**页面公示**，不是卖家确认。所以只用来
- 少问几项（页面上写明的就别再问）；
- 开场时把已知信息说出来，让卖家只需要回答差异部分（我这个量什么价、含不含税）；
- 报价里补空字段时必须留痕（`condition_note` 注明「取自商品页」）。
价格本身仍以卖家回复为准，绝不拿页面价冒充询价结果。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.models.entities import Product

# 详情页价格是分行渲染的：「¥\n15\n.\n80」，先压平空白再解析
_UNIT = r"(?:顶|个|件|只|条|套|包|台|双|副|盒|箱|卷|对|支|把|张|片|袋|瓶|罐|米|码|pcs)"
_PRICE = re.compile(r"[¥￥]\s*(\d{1,6}(?:\s*[.．]\s*\d{1,2})?)")
_MOQ = re.compile(r"(\d{1,5})\s*(" + _UNIT + r")\s*起批")
_TIER_GE = re.compile(r"[≥>=]+\s*(\d{1,6})\s*(" + _UNIT + r")")
_TIER_RANGE = re.compile(r"(\d{1,6})\s*[-~—]\s*(\d{1,6})\s*(" + _UNIT + r")")
_BASE_QTY = re.compile(r"(\d{1,5})\s*" + _UNIT + r"\s*价格")
_SHIP_FEE = re.compile(r"运费\s*[¥￥]\s*(\d{1,5}(?:\s*\.\s*\d{1,2})?)\s*起?")
_STOCK = re.compile(r"库存\s*(\d{1,7})")
_LEAD_HOURS = re.compile(r"(\d{1,3})\s*(?:小时|h|H)\s*内?\s*发货")
_LEAD_DAYS = re.compile(r"(\d{1,2})\s*天\s*内?\s*发货")

# 属性块是详情页最有用的补充：卖家在标题里写的词经常和属性表不一致
_ATTR_BLOCK = re.compile(r"商品属性(.{0,600})", re.S)


def _to_float(raw: str) -> Optional[float]:
    try:
        return float(re.sub(r"\s+", "", raw).replace("．", "."))
    except (TypeError, ValueError):
        return None


@dataclass
class DetailFacts:
    """详情页公示信息（未经卖家确认）。"""

    price: Optional[float] = None          # 页面标价
    moq: Optional[int] = None              # 起批量
    unit: str = "件"                       # 计量单位（顶/个/件…），话术里要说对
    tiers: list[dict] = field(default_factory=list)   # [{qty, price}]
    shipping_fee: Optional[float] = None   # 运费起步价（元）
    free_shipping: bool = False            # 页面写明包邮
    stock: Optional[int] = None            # 库存件数（有数字才算）
    lead_time_hours: Optional[int] = None  # 只在页面明写「N 小时/天发货」时才有
    lead_hint: str = ""                    # 「48h揽收率100%」这类弱信号，只做参考
    tax: str = "unknown"                   # included / excluded / unknown
    url: str = ""
    fetched_at: str = ""

    # ---- 派生信息 ---------------------------------------------------------
    def bulk_price(self, quantity: Optional[int]) -> Optional[float]:
        """按我方采购量取适用的页面阶梯价（达不到任何档位就是标价）。"""
        if not quantity:
            return self.price
        usable = [t for t in self.tiers
                  if isinstance(t, dict) and t.get("qty") and t.get("price")
                  and int(t["qty"]) <= int(quantity)]
        if usable:
            return float(max(usable, key=lambda t: int(t["qty"]))["price"])
        return self.price

    def known_gaps(self, quantity: Optional[int] = None) -> set[str]:
        """页面上已经写清楚、不必再问卖家的缺口项。"""
        known: set[str] = set()
        if self.moq is not None:
            known.add("moq")
        if self.shipping_fee is not None or self.free_shipping:
            known.add("shipping")
        if self.stock is not None:
            known.add("stock")
        if self.tax in ("included", "excluded"):
            known.add("tax")
        if self.lead_time_hours is not None:
            known.add("lead_time")
        # 页面阶梯价已经覆盖我的量，就不必再问「这个量什么价」
        if quantity and any(isinstance(t, dict) and (t.get("qty") or 0) <= quantity
                            for t in self.tiers):
            known.add("tier_price")
        return known

    def has_any(self) -> bool:
        return bool(self.price or self.tiers or self.moq or self.stock
                    or self.shipping_fee is not None or self.free_shipping)

    def summary(self, quantity: Optional[int] = None) -> str:
        """一句话说清页面写着什么（话术里直接引用，不超过 60 字）。"""
        bits: list[str] = []
        if self.price:
            moq = f"{self.moq}{self.unit}起批" if self.moq else ""
            bits.append(f"标价 ¥{self.price:g}" + (f"（{moq}）" if moq else ""))
        tiers = sorted([t for t in self.tiers if t.get("price")],
                       key=lambda t: t.get("qty") or 0)[:2]
        for t in tiers:
            bits.append(f"{t['qty']}{self.unit}以上 ¥{float(t['price']):g}")
        if self.shipping_fee is not None:
            bits.append(f"运费 ¥{self.shipping_fee:g} 起")
        elif self.free_shipping:
            bits.append("包邮")
        if self.stock is not None:
            bits.append(f"库存 {self.stock}")
        if self.lead_time_hours:
            bits.append(f"{self.lead_time_hours} 小时发货")
        return "，".join(bits)

    # ---- 序列化 -----------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "price": self.price, "moq": self.moq, "unit": self.unit,
            "tiers": self.tiers,
            "shipping_fee": self.shipping_fee, "free_shipping": self.free_shipping,
            "stock": self.stock, "lead_time_hours": self.lead_time_hours,
            "lead_hint": self.lead_hint, "tax": self.tax, "url": self.url,
            "fetched_at": self.fetched_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> DetailFacts:
        return cls(
            price=d.get("price"), moq=d.get("moq"), unit=d.get("unit") or "件",
            tiers=[t for t in (d.get("tiers") or []) if isinstance(t, dict)],
            shipping_fee=d.get("shipping_fee"),
            free_shipping=bool(d.get("free_shipping")),
            stock=d.get("stock"), lead_time_hours=d.get("lead_time_hours"),
            lead_hint=d.get("lead_hint") or "", tax=d.get("tax") or "unknown",
            url=d.get("url") or "", fetched_at=d.get("fetched_at") or "",
        )


def parse_detail_text(text: str, url: str = "") -> DetailFacts:
    """从详情页 innerText 抽公开信息。解析不出来就返回空 facts（不猜）。"""
    facts = DetailFacts(url=url, fetched_at=datetime.now().isoformat(timespec="seconds"))
    if not (text or "").strip():
        return facts
    flat = re.sub(r"[ \t\u3000]+", " ", text)
    flat = re.sub(r"\s*\n\s*", " ", flat)

    # ---- 价格与阶梯价：按 ¥ 切片，「¥ 后的尾巴」决定这是哪一档 ----------
    hits = list(_PRICE.finditer(flat))
    seen: set[tuple[int, float]] = set()
    for i, m in enumerate(hits):
        price = _to_float(m.group(1))
        if price is None or price <= 0 or price > 1_000_000:
            continue
        end = hits[i + 1].start() if i + 1 < len(hits) else m.end() + 30
        tail = flat[m.end(): min(end, m.end() + 30)]
        moq_m = _MOQ.search(tail)
        if moq_m:
            if facts.price is None:
                facts.price = price
                facts.moq = int(moq_m.group(1))
            facts.unit = moq_m.group(2) or facts.unit
            continue
        ge = _TIER_GE.search(tail)
        rng = _TIER_RANGE.search(tail)
        if ge:
            qty, unit = int(ge.group(1)), ge.group(2)
        elif rng:
            qty, unit = int(rng.group(1)), rng.group(3)
        elif _BASE_QTY.search(tail):
            if facts.price is None:
                facts.price = price
            continue
        else:
            continue
        if unit:
            facts.unit = unit
        if (qty, price) in seen:
            continue
        seen.add((qty, price))
        facts.tiers.append({"qty": qty, "price": price})

    # 没有「N 起批」但页面有价：拿第一个价当标价（1688 详情页首个价格就是当前档）
    if facts.price is None and hits:
        first = _to_float(hits[0].group(1))
        if first and first > 0:
            facts.price = first
    if facts.price is None and facts.tiers:
        facts.price = min(t["price"] for t in facts.tiers)

    facts.tiers.sort(key=lambda t: t["qty"])
    # 阶梯价低于起订门槛的那档如果和标价同价，纯属重复
    facts.tiers = [t for t in facts.tiers if t["price"] and t["price"] > 0]
    if facts.moq is None and facts.tiers:
        facts.moq = min(t["qty"] for t in facts.tiers) if facts.tiers else None

    # ---- 运费 / 库存 / 时效 / 税 ----------------------------------------
    ship = _SHIP_FEE.search(flat)
    if ship:
        facts.shipping_fee = _to_float(ship.group(1))
    if not facts.shipping_fee and re.search(r"(?<!退货)包邮", flat):
        facts.free_shipping = True

    stock = _STOCK.search(flat)
    if stock:
        facts.stock = int(stock.group(1))

    lead = _LEAD_HOURS.search(flat)
    if lead:
        facts.lead_time_hours = int(lead.group(1))
    else:
        days = _LEAD_DAYS.search(flat)
        if days:
            facts.lead_time_hours = int(days.group(1)) * 24
    hint = re.search(r"(\d{1,3})\s*h\s*揽收率\s*(\d{1,3}(?:\.\d+)?%)", flat, re.I)
    if hint:
        facts.lead_hint = f"{hint.group(1)}h揽收率{hint.group(2)}"
    elif re.search(r"(\d{1,2})天(?:内)?送达|后天送达", flat):
        m2 = re.search(r"后天送达", flat)
        facts.lead_hint = facts.lead_hint or ("后天送达" if m2 else "")

    if "不含税" in flat or "不含税价" in flat:
        facts.tax = "excluded"
    elif "含税" in flat:
        facts.tax = "included"
    return facts


# ---------------------------------------------------------------------------
# 挂在商品上（不新增表）
# ---------------------------------------------------------------------------
_ATTR_KEY = "_detail"


def load(product: Product) -> Optional[DetailFacts]:
    raw = (product.attributes or {}).get(_ATTR_KEY)
    if not raw:
        return None
    try:
        return DetailFacts.from_dict(json.loads(raw))
    except (ValueError, TypeError):
        return None


def attach(product: Product, facts: DetailFacts) -> None:
    """把结果写回商品（调用方负责 store.save_product）。顺带补标价/起订量。"""
    product.attributes = dict(product.attributes or {})
    product.attributes[_ATTR_KEY] = json.dumps(facts.to_dict(), ensure_ascii=False)
    if facts.price and not product.listed_price:
        product.listed_price = facts.price
    if facts.moq and not product.moq:
        product.moq = facts.moq


def fill_quote_gaps(quote, facts: Optional[DetailFacts]):
    """把页面公示的起订量/运费口径/库存补进报价的空字段（留痕，不覆盖卖家原话）。"""
    if quote is None or facts is None:
        return quote
    changed: list[str] = []
    if quote.moq is None and facts.moq is not None:
        quote.moq = facts.moq
        changed.append("起订量")
    if quote.includes_shipping is None:
        if facts.shipping_fee is not None:
            quote.includes_shipping = False
            changed.append(f"运费另计（¥{facts.shipping_fee:g} 起）")
        elif facts.free_shipping:
            quote.includes_shipping = True
            changed.append("包邮")
    if quote.stock_status is None or getattr(quote.stock_status, "value", "") == "unknown":
        if facts.stock is not None:
            from app.models.enums import StockStatus
            quote.stock_status = StockStatus.IN_STOCK
            changed.append(f"库存 {facts.stock}")
    if changed:
        note = "；".join(changed) + "取自商品页"
        quote.condition_note = (quote.condition_note + "；" + note).strip("；")
    return quote


# ---------------------------------------------------------------------------
# 「卖家认了页面价」→ 落成一条真报价
# ---------------------------------------------------------------------------
# 场景：开场直接问「页面 100 顶以上 14.8，我拿 500 顶能按这个走吗？含税吗」，
# 卖家回「可以，不含税」。这句话里没有任何数字，纯靠正则解析就是一条空报价，
# 引擎会认为「还没报价」，回头再问一遍价——等于把预填白做了。
# 所以这里把「明确肯定 + 页面价」合成一条报价，并原文留痕。
_AFFIRM = ("可以", "行", "好的", "没问题", "对的", "是的", "能用", "这个价", "就这个",
           "ok", "OK", "嗯", "成")
_NEGATE = ("不可以", "不行", "不能", "做不了", "做不到", "少不了", "低不了", "没有这个价")


def is_affirmative(text: str) -> bool:
    t = (text or "").strip()
    if not t or any(n in t for n in _NEGATE):
        return False
    return any(w in t for w in _AFFIRM)


def quote_from_facts(facts: Optional[DetailFacts], target, product, quantity, raw_text: str):
    """卖家认可页面价时，用页面价 + 我方采购量落一条可溯源报价。"""
    if facts is None or not facts.price:
        return None
    from app.models.entities import Quote
    from app.models.enums import QuoteSource, StockStatus

    price = facts.bulk_price(quantity) or facts.price
    q = Quote(
        id="", target_style_id=target.id, product_id=product.id,
        shop_id=product.shop_id, source=QuoteSource.INQUIRY,
        price=float(price), moq=facts.moq,
        tiers=list(facts.tiers),
        includes_shipping=(True if facts.free_shipping else
                           (False if facts.shipping_fee is not None else None)),
        stock_status=(StockStatus.IN_STOCK if facts.stock is not None
                      else StockStatus.UNKNOWN),
        raw_text=raw_text or "",
        condition_note=(f"卖家确认按商品页价（{quantity} 件档 ¥{price:g}）"
                        + (f"，运费 ¥{facts.shipping_fee:g} 起" if facts.shipping_fee else "")),
    )
    return q
