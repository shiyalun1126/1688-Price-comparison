"""详情页预填：把「页面上写着的信息」从缺口里拿掉。

真实背景（2026-11-09）：详情页明明印着「1 顶起批 ¥15.8、100 顶以上 ¥14.8、
运费 ¥3.3 起、库存 418 顶」，智能体还在一条条问「什么价」「起订量多少」
「运费怎么算」。卖家会觉得我们根本没看页面，问出来的价也未必按我们的量报。

这些用例全部离线：解析用真实页面文本切片，话术不调模型（llm 不可用）。
"""
from __future__ import annotations

from app.inquire import detail_facts
from app.inquire.detail_facts import DetailFacts, parse_detail_text
from app.inquire.dialogue import DialogueConfig, compute_gaps, decide
from app.models.entities import Product, Quote, TargetStyle, InquirySession
from app.models.enums import MsgRole, TaxStatus

# 2026-11-09 从真实详情页抓下来的文本片段（保留换行与空格原样）
DETAIL_TEXT = """夏季特惠！渔夫帽经典百搭款夏季休闲风素颜纯色防晒速干水桶帽子
100+人好评
商品复购率36.36%
¥
15
.
80

1顶起批
60天老客价

¥
15
.
30

10-99顶

¥
14
.
80

≥100顶

已售100+顶
满100减5券
浙江金华
送至
河南洛阳
16:00前付款，预计后天送达
运费¥3.3起

退货包运费
7天无理由退货
晚发必赔
颜色
黑色
库存418顶
"""


class _NoLLM:
    available = False


def _target() -> TargetStyle:
    return TargetStyle(id="t", name="复古机车极简风防风渔夫帽")


def _product() -> Product:
    return Product(
        id="p", target_style_id="t", shop_id="sh",
        title="夏季特惠！渔夫帽经典百搭款夏季休闲风素颜纯色防晒速干水桶帽子 防晒 ｜ 腈纶 ｜ 缎档",
        url="https://detail.1688.com/offer/899553107153.html",
    )


def _session(qty: int = 500, round: int = 0) -> InquirySession:
    return InquirySession(
        id="s", inquiry_id="i", target_style_id="t", product_id="p",
        shop_id="sh", buyer_quantity=qty, round=round,
        last_speaker=MsgRole.SELLER,
    )


def test_parse_real_detail_page():
    """真实页面：价格、阶梯价、起订量、运费、库存都要读出来。"""
    f = parse_detail_text(DETAIL_TEXT, "https://detail.1688.com/offer/899553107153.html")
    assert f.price == 15.8
    assert f.moq == 1 and f.unit == "顶"
    assert f.tiers == [{"qty": 10, "price": 15.3}, {"qty": 100, "price": 14.8}]
    assert f.shipping_fee == 3.3
    assert f.stock == 418
    assert f.has_any()


def test_bulk_price_follows_quantity():
    """阶梯价要按我方采购量取档，别拿 1 件的价当大货价。"""
    f = parse_detail_text(DETAIL_TEXT)
    assert f.bulk_price(500) == 14.8
    assert f.bulk_price(50) == 15.3
    assert f.bulk_price(2) == 15.8


def test_known_gaps_drops_page_visible_items():
    """页面上写着的起订/运费/库存/阶梯价，不算缺口。含税页面没写，仍要问。"""
    f = parse_detail_text(DETAIL_TEXT)
    known = f.known_gaps(500)
    assert {"moq", "shipping", "stock", "tier_price"} <= known
    assert "tax" not in known


def test_opening_message_does_not_ask_what_page_says():
    """开场话术：把页面数字说出来，只问差异化的问题。"""
    f = parse_detail_text(DETAIL_TEXT, "https://detail.1688.com/offer/899553107153.html")
    d = decide(session=_session(500), quote=None, seller_text="", target=_target(),
               product=_product(), cfg=DialogueConfig(), llm=_NoLLM(), facts=f)
    assert d.action == "send_inquiry"
    text = d.text
    assert "¥15.8" in text and "14.8" in text          # 引用了页面价，说明看过页面
    assert "500" in text
    assert "起订" not in text and "运费" not in text and "现货" not in text
    assert "含税" in text and "几天能发" in text
    assert d.gap == ["price", "tax", "lead_time"]


def test_without_facts_the_old_questionnaire_stays():
    """没有预填能力时保持原样，不能因为新逻辑把必问项弄丢。"""
    d = decide(session=_session(500), quote=None, seller_text="", target=_target(),
               product=_product(), cfg=DialogueConfig(), llm=_NoLLM())
    assert "起订量" in d.text and "运费" in d.text and "现货" in d.text


def test_quote_gaps_keep_answered_fields_when_price_missing():
    """价格还没拿到 ≠ 其他项没答过。

    旧实现里 `not quote.price` 直接返回全部缺口，卖家明明说了「不含税」，
    下一轮仍会被再问一遍「含税吗」——同一批问题反复问的底层原因之一。
    """
    q = Quote(id="q", target_style_id="t", product_id="p", shop_id="sh",
              price=0.0, tax=TaxStatus.EXCLUDED)
    gaps = compute_gaps(q, ["price", "moq", "tax", "shipping", "stock", "lead_time"])
    assert "tax" not in gaps
    assert "price" in gaps


def test_affirmative_confirmation_turns_page_price_into_quote():
    """卖家只回「可以，不含税」时，用页面价落一条报价，别回头再问一遍价。"""
    f = parse_detail_text(DETAIL_TEXT)
    assert detail_facts.is_affirmative("可以，不含税")
    assert detail_facts.is_affirmative("这个价能做")
    assert not detail_facts.is_affirmative("不可以")
    assert not detail_facts.is_affirmative("做不了这个价")

    q = detail_facts.quote_from_facts(f, _target(), _product(), 500, "可以，不含税")
    assert q is not None
    assert q.price == 14.8                 # 500 件走 ≥100 顶那一档
    assert q.moq == 1
    assert q.includes_shipping is False    # 页面写着运费 3.3 起
    assert "商品页" in q.condition_note and "可以，不含税" in q.raw_text


def test_fill_quote_gaps_never_overrides_seller():
    """卖家说的优先：卖家报了起订 500，就不能被页面上的「1 顶起批」覆盖。"""
    f = parse_detail_text(DETAIL_TEXT)
    q = Quote(id="q", target_style_id="t", product_id="p", shop_id="sh",
              price=14.5, moq=500, includes_shipping=True)
    out = detail_facts.fill_quote_gaps(q, f)
    assert out.moq == 500
    assert out.includes_shipping is True


def test_facts_roundtrip_on_product_attributes():
    """结果挂在商品上，不需要新表；读回来要一模一样。"""
    f = parse_detail_text(DETAIL_TEXT)
    p = _product()
    detail_facts.attach(p, f)
    back = detail_facts.load(p)
    assert back is not None
    assert back.price == 15.8 and back.tiers == f.tiers
    assert p.listed_price == 15.8 and p.moq == 1


def test_empty_or_blocked_page_yields_nothing():
    """抓到验证码页/空白页时宁可没有预填，也不许猜一个价格出来。"""
    assert not DetailFacts().has_any()
    assert parse_detail_text("").has_any() is False
    assert parse_detail_text("请拖动滑块完成验证").has_any() is False
