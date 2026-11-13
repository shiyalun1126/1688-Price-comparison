"""谈判决策层单测：全部是纯函数，不落库、不联网、不碰浏览器。

覆盖「会谈判」这个说法的可验证部分：
- 缺口怎么算、多轮回复怎么合并（卖家常分几条说完）
- 什么情况下追问、什么情况下开口议价、什么情况下收手
- 议价目标价怎么定（不能凭感觉，要有锚点）
- 模型输出跑偏时能不能兜住（防止把思考过程发到旺旺上）
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.inquire.dialogue import (
    DialogueConfig,
    backoff_seconds,
    bargain_target,
    classify_reply,
    compute_gaps,
    decide,
    merge_quote,
    pick_market_min,
    should_bargain,
)
from app.inquire.inquiry import (
    _parse_rules,
    clean_outgoing,
    clean_product_title,
    parse_model_message,
    parse_quote,
)
from app.models.entities import InquirySession, Product, Quote, TargetStyle
from app.models.enums import InquiryStage, InquiryStatus, MsgRole, StockStatus, TaxStatus


class _NoLLM:
    """假装 LLM 不可用：强制走规则模板，测试不依赖网络与额度。"""

    available = False


@pytest.fixture
def cfg() -> DialogueConfig:
    return DialogueConfig(max_rounds=4, max_gaps_per_turn=3, bargain_min_gap=0.03,
                          expire_hours=24.0)


@pytest.fixture
def target() -> TargetStyle:
    return TargetStyle(
        id="t1", name="260g纯棉落肩T恤", description="260g 双纱纯棉",
        attributes={"面料": "纯棉"}, reference_price=50.0, category="服装",
    )


@pytest.fixture
def product() -> Product:
    return Product(
        id="p1", target_style_id="t1", shop_id="sh1",
        title="260g重磅纯棉短袖T恤 落肩宽松 批发 ¥58 运费4元 全网50+件",
        url="https://detail.1688.com/offer/1.html", listed_price=58.0,
    )


def _session(**kw) -> InquirySession:
    base = dict(id="s1", inquiry_id="i1", target_style_id="t1",
                product_id="p1", shop_id="sh1", buyer_quantity=500)
    base.update(kw)
    return InquirySession(**base)


def _quote(**kw) -> Quote:
    base = dict(id="q1", target_style_id="t1", product_id="p1", shop_id="sh1", price=56.0)
    base.update(kw)
    return Quote(**base)


# ---------------------------------------------------------------------------
# 缺口与合并
# ---------------------------------------------------------------------------
def test_gaps_all_missing_without_quote(cfg):
    assert compute_gaps(None, cfg.required) == cfg.required


def test_gaps_shrink_as_fields_fill(cfg):
    q = _quote(price=56.0, moq=200, tax=TaxStatus.INCLUDED)
    gaps = compute_gaps(q, cfg.required)
    assert "price" not in gaps and "moq" not in gaps and "tax" not in gaps
    assert set(gaps) == {"shipping", "stock", "lead_time"}


def test_gaps_empty_when_complete(cfg):
    q = _quote(price=56.0, moq=200, tax=TaxStatus.INCLUDED, includes_shipping=False,
               stock_status=StockStatus.IN_STOCK, lead_time_hours=48)
    assert compute_gaps(q, cfg.required) == []


def test_merge_quote_keeps_earlier_fields():
    """卖家先报价、再补 MOQ/运费：后一轮不能把前一轮的信息冲掉。"""
    first = _quote(price=56.0, moq=200)
    second = Quote(id="q2", target_style_id="t1", product_id="p1", shop_id="sh1",
                   price=0.0, tax=TaxStatus.INCLUDED, includes_shipping=False,
                   raw_text="含税的，不含运费")
    merged = merge_quote(first, second)
    assert merged.price == 56.0          # 第二句没报价，保留原价
    assert merged.moq == 200
    assert merged.tax == TaxStatus.INCLUDED
    assert merged.includes_shipping is False


def test_merge_quote_updates_price_on_bargain():
    first = _quote(price=56.0)
    second = _quote(price=53.8)
    assert merge_quote(first, second).price == 53.8


def test_merge_quote_dedupes_tiers():
    a = _quote(tiers=[{"qty": 100, "price": 55.0}])
    b = _quote(price=0.0, tiers=[{"qty": 100, "price": 55.0}, {"qty": 500, "price": 52.0}])
    merged = merge_quote(a, b)
    assert len(merged.tiers) == 2


def test_market_min_excludes_self():
    quotes = [_quote(id="a", product_id="p1", price=56.0),
              _quote(id="b", product_id="p2", price=52.0)]
    assert pick_market_min(quotes, exclude_product_id="p1") == 52.0
    assert pick_market_min([_quote(price=56.0)], exclude_product_id="p1") is None


# ---------------------------------------------------------------------------
# 意图识别与议价判定
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,expect", [
    ("这是最低价了，不能再少", "refuse"),
    ("加个微信详聊吧", "off_channel"),
    ("含税的，48 小时内发货", "normal"),
])
def test_classify_reply(text, expect):
    assert classify_reply(text) == expect


def test_should_bargain_when_above_market(cfg):
    q = _quote(price=58.0)
    assert should_bargain(q, market_min=52.0, floor_price=None, cfg=cfg) is True


def test_no_bargain_when_already_cheapest(cfg):
    q = _quote(price=52.0)
    assert should_bargain(q, market_min=52.0, floor_price=None, cfg=cfg) is False


def test_bargain_target_uses_stricter_anchor():
    """目标价取「市场最低价」与「心理价」里更严格的那个，再留 1% 余量。"""
    q = _quote(price=60.0)
    assert bargain_target(q, market_min=52.0, floor_price=50.0) == round(50.0 * 0.99, 2)
    assert bargain_target(q, market_min=48.0, floor_price=50.0) == round(48.0 * 0.99, 2)


# ---------------------------------------------------------------------------
# 决策分支
# ---------------------------------------------------------------------------
def test_first_turn_sends_inquiry(cfg, target, product):
    d = decide(session=_session(), quote=None, target=target, product=product,
               cfg=cfg, llm=_NoLLM())
    assert d.action == "send_inquiry"
    assert "价" in d.text and "起订" in d.text


def test_wait_when_seller_silent(cfg, target, product):
    s = _session(round=1, last_speaker=MsgRole.BUYER,
                 expires_at=datetime.now() + timedelta(hours=5))
    d = decide(session=s, quote=None, target=target, product=product, cfg=cfg, llm=_NoLLM())
    assert d.action == "wait"
    assert d.delay_s > 0


def test_timeout_closes(cfg, target, product):
    s = _session(round=1, last_speaker=MsgRole.BUYER,
                 expires_at=datetime.now() - timedelta(minutes=1))
    d = decide(session=s, quote=None, target=target, product=product, cfg=cfg, llm=_NoLLM())
    assert d.action == "close"
    assert "超时" in d.reason


def test_followup_only_asks_missing(cfg, target, product):
    s = _session(round=1, last_speaker=MsgRole.SELLER)
    q = _quote(price=56.0, moq=200, stock_status=StockStatus.IN_STOCK)
    d = decide(session=s, quote=q, seller_text="56元一件，200件起批，有现货",
               target=target, product=product, cfg=cfg, llm=_NoLLM())
    assert d.action == "send_followup"
    assert set(d.gap) == {"tax", "shipping", "lead_time"}


def test_bargain_when_quote_complete_and_expensive(cfg, target, product):
    s = _session(round=2, last_speaker=MsgRole.SELLER)
    q = _quote(price=58.0, moq=200, tax=TaxStatus.INCLUDED, includes_shipping=False,
               stock_status=StockStatus.IN_STOCK, lead_time_hours=48)
    d = decide(session=s, quote=q, seller_text="含税不含运，48小时发货",
               market_min=52.0, target=target, product=product, cfg=cfg, llm=_NoLLM())
    assert d.action == "send_bargain"
    assert d.stage == InquiryStage.BARGAIN
    assert "52.00" in d.text          # 锚点必须是真实市场价，不能编


def test_close_when_cheapest(cfg, target, product):
    s = _session(round=2, last_speaker=MsgRole.SELLER)
    q = _quote(price=52.0, moq=200, tax=TaxStatus.INCLUDED, includes_shipping=False,
               stock_status=StockStatus.IN_STOCK, lead_time_hours=48)
    d = decide(session=s, quote=q, seller_text="52含税，48小时发货",
               market_min=52.0, target=target, product=product, cfg=cfg, llm=_NoLLM())
    assert d.action == "close"


def test_close_after_bargain_converges(cfg, target, product):
    """议价后卖家回了个价，就该收手，不能没完没了地磨。"""
    s = _session(round=3, last_speaker=MsgRole.SELLER, stage=InquiryStage.BARGAIN)
    q = _quote(price=55.7, moq=200, tax=TaxStatus.INCLUDED, includes_shipping=False,
               stock_status=StockStatus.IN_STOCK, lead_time_hours=48)
    d = decide(session=s, quote=q, seller_text="55.7可以给你",
               market_min=52.0, target=target, product=product, cfg=cfg, llm=_NoLLM())
    assert d.action == "close"
    assert d.gap == []


def test_escalate_on_off_channel(cfg, target, product):
    s = _session(round=1, last_speaker=MsgRole.SELLER)
    d = decide(session=s, quote=None, seller_text="加我微信聊吧",
               target=target, product=product, cfg=cfg, llm=_NoLLM())
    assert d.action == "escalate"
    assert "站外" in d.reason


def test_round_limit_stops_chatting(cfg, target, product):
    s = _session(round=cfg.max_rounds, last_speaker=MsgRole.SELLER)
    q = _quote(price=56.0)          # 只有价格，其他都缺
    d = decide(session=s, quote=q, seller_text="就这个价", target=target,
               product=product, cfg=cfg, llm=_NoLLM())
    assert d.action == "close"
    assert "上限" in d.reason


def test_backoff_grows_and_respects_min(monkeypatch):
    monkeypatch.setenv("INQUIRY_MIN_DELAY", "10")
    a = backoff_seconds(1, 24)
    b = backoff_seconds(3, 24)
    assert 10 <= a <= 13
    assert b > a


# ---------------------------------------------------------------------------
# 模型输出净化：绝不能把思考过程发到旺旺上
# ---------------------------------------------------------------------------
def test_clean_outgoing_extracts_quoted_answer():
    raw = 'Let me think. The requirement says 60 chars. 所以："老板，这款什么价？"'
    assert clean_outgoing(raw) == "老板，这款什么价？"


def test_parse_model_message_prefers_msg_tag():
    raw = "先想一下怎么说。<msg>老板，这款多少钱？</msg>"
    assert parse_model_message(raw, "兜底") == "老板，这款多少钱？"


@pytest.mark.parametrize("raw", [
    "消息",
    "好的",
    "需要一句话，最好直接输出 msg。",
    "变几天内发出。这算补问细化，不算重复？要求绝对不要重复已经问过的内容。",
])
def test_parse_model_message_rejects_garbage(raw):
    """残句和元指令一律退回模板——发出去半句话比不发更糟。"""
    assert parse_model_message(raw, "老板，这款什么价？") == "老板，这款什么价？"


def test_clean_product_title_strips_card_noise():
    t = clean_product_title("260g纯棉短袖T恤落肩 ¥58 运费4元 全网50+件 退货包运费 3天达")
    assert "¥" not in t and "运费" not in t and "全网" not in t
    assert t.startswith("260g纯棉短袖T恤")


# ---------------------------------------------------------------------------
# 规则解析：补上运费口径与「200 件起批」这类说法
# ---------------------------------------------------------------------------
def test_parse_rules_shipping_negative():
    r = _parse_rules("这个价不含运费，运费看地区")
    assert r["includes_shipping"] is False


def test_parse_rules_shipping_positive():
    assert _parse_rules("这款包邮的")["includes_shipping"] is True


def test_parse_rules_moq_without_keyword():
    assert _parse_rules("200 件起批，量大可以谈")["moq"] == 200


def test_parse_quote_end_to_end():
    q = parse_quote("含税的，这个价不含运费，运费看地区，一般省内 30 左右。"
                    "今天下单 48 小时内发货。", TargetStyle(id="t1", name="T恤"),
                    Product(id="p1", target_style_id="t1", shop_id="s", title="T恤"))
    assert q.tax == TaxStatus.INCLUDED
    assert q.includes_shipping is False
    assert q.lead_time_hours == 48


# ---------------------------------------------------------------------------
# 外发文本必须像人话（真实事故回归）
# 实测踩过：模型把一段 Markdown 分析整段返回，而旺旺输入框里换行=回车=发送，
# 结果一段话被拆成 5 条碎片轰给卖家。这两个防线都不能退。
# ---------------------------------------------------------------------------
def test_markdown_block_is_rejected():
    from app.inquire.inquiry import looks_like_chat_message
    bad = ("无法从候选标题判断具体起订量，需向供应商确认。参考范围：\n"
           "- **现货拿样/小批**：约 **1–10 件**起\n"
           "- **常规批发**：约 **100–500 件**起\n"
           "建议直接问供应商这几点：")
    assert looks_like_chat_message(bad) is False
    assert parse_model_message(bad, "老板，这款什么价？") == "老板，这款什么价？"


def test_analysis_tone_is_rejected():
    from app.inquire.inquiry import looks_like_chat_message
    assert looks_like_chat_message("无法从候选标题判断起订量") is False
    assert looks_like_chat_message("综合来看这款性价比不错，建议先问清楚。") is False


def test_normal_message_passes_untouched():
    msg = "老板，这款700ML杯子要500个，批发价能给到多少？含税吗，运费怎么算？"
    assert parse_model_message(msg, "兜底") == msg


def test_msg_tag_is_extracted_before_validation():
    raw = "<msg>老板，这款杯子要500个，什么价？含税吗，运费怎么算？</msg>"
    out = parse_model_message(raw, "兜底")
    assert out.startswith("老板") and "<msg>" not in out


def test_typing_text_is_forced_to_one_line():
    """换行必须被压平：旺旺里换行会被当成回车直接发出去。"""
    from app.browser.chat import _one_line, _split_for_typing
    assert _one_line("第一行\n第二行\n\n第三行") == "第一行 第二行 第三行"
    assert all("\n" not in c for c in _split_for_typing("你好\n老板，什么价？"))


# ---------------------------------------------------------------------------
# 2026-11-06 真实会话事故回归（沃乐克，真实 1688 账号）
# ---------------------------------------------------------------------------
def test_dangling_half_sentence_is_rejected():
    """真事故：模型输出被截成「老板，麻烦再确认」，长度和中文比例都过了校验，
    结果半句话直接发到卖家旺旺上。必须靠「悬空尾巴」拦下来。"""
    from app.inquire.inquiry import looks_like_chat_message
    for bad in ("老板，麻烦再确认", "好的，我再问下", "麻烦你帮我",
                "另外还有", "关于这个"):
        assert looks_like_chat_message(bad) is False, bad
    assert parse_model_message("老板，麻烦再确认", "好的，再确认下运费和发货时间？") \
        == "好的，再确认下运费和发货时间？"


def test_complete_short_sentence_still_passes():
    """残句规则不能误伤正常短句。"""
    from app.inquire.inquiry import looks_like_chat_message
    for ok in ("好的，运费和发货时间能给个准数吗？", "这个价能再低一点吗？",
               "老板，200 件什么价？"):
        assert looks_like_chat_message(ok) is True, ok


def test_shipping_quoted_by_address_means_excluded():
    """卖家说「需要看您的地址运费」= 不包邮，不能当成「没提到运费」再问一遍。"""
    r = _parse_rules("需要看您的地址运费 商品是含税的价格 如果需要开专票还要+10个点")
    assert r["tax"] == TaxStatus.INCLUDED
    assert r["includes_shipping"] is False


def test_tax_only_reply_is_still_a_signal():
    """卖家只回「含税」没回价：这条信息必须入库，否则下一轮又问一遍。"""
    from app.inquire.engine import _has_signal
    r = _parse_rules("商品是含税的价格")
    q = Quote(id="x", target_style_id="t1", product_id="p1", shop_id="s",
              price=0.0, tax=r["tax"])
    assert _has_signal(q) is True


def test_sample_moq_triggers_bulk_price_question():
    """1 个起批的价不是批发价：我方要 200 件，必须问「这个量什么价」。"""
    q = _quote(price=56.9, moq=1, tax=TaxStatus.INCLUDED, includes_shipping=False,
               stock_status=StockStatus.IN_STOCK, lead_time_hours=48)
    gaps = compute_gaps(q, DialogueConfig().required, quantity=200)
    assert gaps == ["tier_price"]


def test_bulk_moq_does_not_ask_again():
    """卖家已经按 200 件起批报的价，就是批发价，不必再问一次。"""
    q = _quote(price=56.0, moq=200, tax=TaxStatus.INCLUDED, includes_shipping=False,
               stock_status=StockStatus.IN_STOCK, lead_time_hours=48)
    assert compute_gaps(q, DialogueConfig().required, quantity=500) == []


def test_market_min_keeps_rival_shop_same_product():
    """同一商品链接会被多家店跟卖：按 product_id 排除会把竞店报价一起丢掉。"""
    quotes = [_quote(id="q-own", product_id="p1", shop_id="sh1", price=56.9),
              _quote(id="q-rival", product_id="p1", shop_id="sh2", price=52.0)]
    assert pick_market_min(quotes, exclude_shop_id="sh1", exclude_quote_id="q-own") == 52.0


def test_pending_reply_waits_instead_of_asking_again(cfg, target, product):
    """真事故：卖家回「好的 这边问下」，机器人立刻又追问一遍同样的问题。
    卖家承诺去确认时，该等——催一次很便宜，账号变成催问机器很贵。"""
    assert classify_reply("好的 这边问下") == "pending"
    assert classify_reply("我问问老板，稍等") == "pending"
    s = _session(round=2, last_speaker=MsgRole.SELLER)
    q = _quote(price=56.9, moq=1, tax=TaxStatus.INCLUDED, includes_shipping=False,
               stock_status=StockStatus.IN_STOCK)
    d = decide(session=s, quote=q, seller_text="好的 这边问下",
               target=target, product=product, cfg=cfg, llm=_NoLLM())
    assert d.action == "wait"
    assert d.delay_s >= 180


def test_no_new_message_does_not_reask(cfg, target, product):
    """真事故：检查点提前到卖家还没回时，引擎对着同一句话又追问了一遍。
    没读到新内容就该等——提前检查不等于对方回了话。"""
    s = _session(round=3, last_speaker=MsgRole.SELLER)
    q = _quote(price=56.9, moq=1, tax=TaxStatus.INCLUDED, includes_shipping=False,
               stock_status=StockStatus.IN_STOCK)
    d = decide(session=s, quote=q, seller_text="", target=target,
               product=product, cfg=cfg, llm=_NoLLM())
    assert d.action == "wait"
    assert "暂无新回复" in d.reason


def test_waiting_for_manager_is_pending():
    """卖家说「店长暂时还未回复 回复后会给您留言」= 仍在处理中，等就行。"""
    assert classify_reply("店长暂时还未回复 回复后会给您留言") == "pending"
    assert classify_reply("我们店长还没回，晚点回复您") == "pending"


def test_same_question_not_repeated_after_seller_chatter():
    """真事故：卖家在中间插了句「还没回复」，幂等检查失效，同一句追问又发一遍。"""
    from app.models.entities import InquiryMessage

    from app.inquire.engine import _same_as_last_buyer
    q = "好的，我们这边按 200 件拿，再确认下：多久能发货，还有拿这个量能做到什么价？"
    prev = [
        InquiryMessage(id="1", session_id="s", role=MsgRole.SELLER, text="有现货"),
        InquiryMessage(id="2", session_id="s", role=MsgRole.BUYER, text=q),
        InquiryMessage(id="3", session_id="s", role=MsgRole.SYSTEM, text="已发送并截图 x.png"),
        InquiryMessage(id="4", session_id="s", role=MsgRole.SELLER, text="店长暂时还未回复"),
    ]
    assert _same_as_last_buyer(prev, q) is True
    assert _same_as_last_buyer(prev, "老板，200 件什么价？") is False


def test_followup_allowed_when_seller_replied_without_answering():
    """真卡死：卖家回「可先拍下改价」等于没报价格，换句话追问却被判成重复。

    2026-11-12 泉州沙足鞋业：开场问「按哪档价走、含税吗、几天能发」，卖家回
    「量大可先拍下 改价后如合适再付款」，缺口一个没补；智能体生成的追问问的还是
    这三项，`_same_as_last_buyer` 判重 → 跳过发送 → 会话停在 following_up 直到过期。

    规则：卖家本轮说过话时只拦「字面复读」，问项相同不算重复。
    """
    from datetime import datetime

    from app.models.entities import InquiryMessage

    from app.inquire.engine import _same_as_last_buyer

    def msg(i, role, text):
        return InquiryMessage(id=str(i), session_id="s", role=role, kind="text",
                              text=text, ts=datetime.now())

    opener = ("老板，这款德训鞋我看了标价58一双，想要100双，"
              "这个量按哪档价走，含税吗，几天能发？")
    follow = "老板，按100件这个量给报个准单价吧，顺便确认含税情况和几天能发货。"
    prev = [msg(1, MsgRole.BUYER, opener),
            msg(2, MsgRole.SELLER, "亲，如量大可先拍下 改价后如合适再付款")]

    # 卖家回过话：换措辞追问同一批缺口要放行
    assert _same_as_last_buyer(prev, follow, seller_spoke=True) is False
    # 但原句复读仍然拦住，别在卖家插话后把同一句再发一遍
    assert _same_as_last_buyer(prev, opener, seller_spoke=True) is True
    # 卖家没说话时，老规则照旧（防刷屏）
    assert _same_as_last_buyer(prev[:1], follow) is True


def test_align_ignores_system_notes():
    """系统提示不在页面上，拿它去对齐会匹配失败 → 把整段尾部重复入库。"""
    from app.models.entities import InquiryMessage

    from app.inquire.engine import _align
    prev = [
        InquiryMessage(id="1", session_id="s", role=MsgRole.SELLER, text="现货"),
        InquiryMessage(id="2", session_id="s", role=MsgRole.BUYER, text="200件什么价"),
        InquiryMessage(id="3", session_id="s", role=MsgRole.SYSTEM, text="已发送并截图 x.png"),
    ]
    bubbles = [{"text": "现货"}, {"text": "200件什么价"}, {"text": "48一个", "self": False}]
    fresh = _align(prev, bubbles)
    assert [b["text"] for b in fresh] == ["48一个"]


def test_asking_for_contact_is_off_channel():
    """卖家真实发过：「可以+个联系方式吗」——比「加微信」隐晦，同样要转人工。"""
    assert classify_reply("您好老板，可以+个联系方式吗？我把报价发您") == "off_channel"
    assert classify_reply("加个微信详聊吧") == "off_channel"
    assert classify_reply("含税的，48 小时内发货") == "normal"


def test_bargain_text_cannot_promise_ordering():
    """议价话术里不能说「能做就下单」——说出口就是卖家手里的条款。"""
    from app.inquire.dialogue import build_bargain
    t = build_bargain(target_fixture(), product_fixture(), _quote(price=62.0),
                      market_min=50.0, quantity=500, llm=None)
    assert "下单" not in t
    assert "50.00" in t or "49.5" in t


def target_fixture() -> TargetStyle:
    return TargetStyle(id="t1", name="260g纯棉短袖T恤", description="落肩宽松",
                       reference_price=None)


def product_fixture() -> Product:
    return Product(id="p1", target_style_id="t1", shop_id="sh1",
                   title="260g重磅纯棉短袖T恤 落肩宽松 批发")


def test_thinking_chain_about_how_to_write_is_rejected():
    """最严重的一次：模型把自己「该怎么写这条话术」的推敲整段发了出去。"""
    bad = ('用户语言中文。需要"respond in user\'s language"。应该给可直接复制的话术。'
           '也可提醒先不要暴露底价? 但他们目标价49.5。可以建议首轮报48.8-49.0，'
           '目标49.5；若对方坚持57，要求解释成本。给两个版本：直接谈判版，稳健版。'
           '最终回复结构：先判断：500')
    assert parse_model_message(bad, "老板，49.5 能做吗？") == "老板，49.5 能做吗？"


def test_bargain_must_actually_ask():
    """议价话术没有问句、或分句过多，都说明它不是在跟人说话。"""
    from app.inquire.dialogue import build_bargain
    t = build_bargain(target_fixture(), product_fixture(), _quote(price=62.0),
                      market_min=50.0, quantity=500, llm=None)
    assert "？" in t or "吗" in t


def test_asking_which_product_gets_link_not_repeat():
    """卖家问「哪款呢」时，要发商品链接，不能把询价话术再报一遍。

    真实事故（2026-11-06 义乌希因帽业）：卖家连问「哪款呢」「链接是哪个」
    「哪款链接啊？？？」，引擎三次都重发同样的问价句，对方回「机器人那？」。
    """
    assert classify_reply("哪款呢？") == "ask_which"
    assert classify_reply("链接是哪个？") == "ask_which"
    assert classify_reply("哪款链接啊？？？") == "ask_which"

    s = _session(round=1, last_speaker=MsgRole.SELLER)
    p = product_fixture()
    p.url = "https://detail.1688.com/offer/899553107153.html"
    d = decide(session=s, quote=None, seller_text="哪款呢？", target=target_fixture(),
               product=p, cfg=DialogueConfig(), llm=_NoLLM())
    assert d.action == "send_clarify"
    assert "899553107153" in d.text
    assert "看到" not in d.text          # 不再复述开场


def test_dirty_card_title_is_cleaned_before_sending():
    """卡片标题带着价格/运费/属性标签，原样发过去卖家认不出是哪款。"""
    dirty = ("夏季特惠！渔夫帽经典百搭款夏季休闲风素颜纯色防晒速干水桶帽子"
             " 防晒 ｜ 腈纶 ｜ 缎档 ¥ 15 .8 运费3.3元 100+件 退货包运费 3天达 先采后付")
    clean = clean_product_title(dirty)
    assert "｜" not in clean and "¥" not in clean and "运费" not in clean
    assert clean.startswith("渔夫帽")


def test_ellipsis_placeholder_never_goes_out():
    """模型把长标题缩成「看到...想拿100件」，发出去是残话，必须退回模板。"""
    fallback = "老板，看到你家「渔夫帽」想拿 100 件。现在什么价？"
    raw = "<msg>老板，看到...想拿100件。现在什么价？起订量多少，含税和运费怎么算？</msg>"
    assert parse_model_message(raw, fallback) == fallback


def test_repeat_with_only_product_name_changed_is_blocked():
    """只换个商品名再发一遍，对外仍是复读（真实事故里就是这样被识破的）。"""
    from app.inquire.engine import _same_as_last_buyer
    from app.models.entities import InquiryMessage
    prev = [InquiryMessage(
        id="1", session_id="s", role=MsgRole.BUYER,
        text="老板，看到你家「夏季特惠！渔夫帽经典百搭款…水桶帽子」，想拿 100 件。"
             "现在什么价？起订量多少，含税和运费怎么算？有现货的话多久能发？")]
    same = ("老板，看到...想拿100件。现在什么价？起订量多少，"
            "含税和运费怎么算？有现货的话多久能发？")
    assert _same_as_last_buyer(prev, same) is True
    assert _same_as_last_buyer(prev, "好的，按 100 件，可否给个含税价？运费怎么算？") is False


def test_seller_calling_must_get_an_answer():
    """卖家喊「有人吗」时必须应一句——沉默比重复更像机器人。

    真实事故（2026-11-06 义乌希因帽业）：这句被当成「与上一条重复」跳过发送，
    窗口里我方一声不吭，卖家此前已经在问「机器人那？」。
    """
    assert classify_reply("有人吗") == "calling"
    assert classify_reply("在吗？") == "calling"
    s = _session(round=3, last_speaker=MsgRole.SELLER)
    d = decide(session=s, quote=None, seller_text="有人吗", target=target_fixture(),
               product=product_fixture(), cfg=DialogueConfig(), llm=_NoLLM())
    assert d.action == "send_followup"
    assert d.answer_call is True
    assert d.text.startswith("在的")


def test_asked_counts_ignores_link_replies():
    """给链接是在回答「哪一款」，不能把价/税/运费的提问次数顶上去。"""
    from app.models.entities import InquiryMessage

    from app.inquire.engine import _asked_counts
    prev = [
        InquiryMessage(id="1", session_id="s", role=MsgRole.BUYER,
                       text="老板，看到你家「渔夫帽」想拿 100 件。现在什么价？含税和运费怎么算？"),
        InquiryMessage(id="2", session_id="s", role=MsgRole.BUYER,
                       text="就是这款，麻烦按 100 件报个价，含税和运费一起算："
                            "https://detail.1688.com/offer/899553107153.html"),
    ]
    assert _asked_counts(prev) == {"price": 1, "tax": 1, "shipping": 1}


def test_gap_asked_twice_is_not_asked_again():
    """同一项问到第二次对方还不答，就不要再问了——真实事故里连问了四轮。"""
    s = _session(round=3, last_speaker=MsgRole.SELLER)
    d = decide(session=s, quote=None, seller_text="好的", target=target_fixture(),
               product=product_fixture(), cfg=DialogueConfig(), llm=_NoLLM(),
               asked_counts={"price": 2, "moq": 2, "tax": 2, "shipping": 2,
                             "stock": 2, "lead_time": 1})
    assert d.action == "send_followup"
    assert "起订" not in d.text and "税" not in d.text        # 已问过两遍的不再问
    assert "发货" in d.text or "多久" in d.text               # 只问还没问够的


def test_all_gaps_asked_twice_waits_instead_of_repeating():
    """六项全问过两遍、卖家一项没答：这时再发一遍等于自证是脚本，只能等。"""
    s = _session(round=4, last_speaker=MsgRole.SELLER)
    all_twice = {k: 2 for k in ("price", "moq", "tax", "shipping", "stock", "lead_time")}
    d = decide(session=s, quote=None, seller_text="好的", target=target_fixture(),
               product=product_fixture(), cfg=DialogueConfig(), llm=_NoLLM(),
               asked_counts=all_twice)
    assert d.action == "wait"
    assert d.delay_s >= 600


def test_second_request_for_link_escalates():
    """链接发过一次对方还在问，多半是没送达/被折叠，再发同一句没用，转人工。"""
    s = _session(round=2, last_speaker=MsgRole.SELLER)
    p = product_fixture()
    p.url = "https://detail.1688.com/offer/899553107153.html"
    d = decide(session=s, quote=None, seller_text="链接是哪个？", target=target_fixture(),
               product=p, cfg=DialogueConfig(), llm=_NoLLM(), clarify_sent=1)
    assert d.action == "escalate"
    assert "链接" in d.reason


def test_suspected_bot_hands_over_to_human():
    """卖家问「机器人那？」——继续自动聊只会加深怀疑，必须换成人工接话。"""
    assert classify_reply("机器人那？") == "suspect_bot"
    assert classify_reply("你是ai吗") == "suspect_bot"
    s = _session(round=3, last_speaker=MsgRole.SELLER)
    d = decide(session=s, quote=None, seller_text="机器人那？", target=target_fixture(),
               product=product_fixture(), cfg=DialogueConfig(), llm=_NoLLM())
    assert d.action == "escalate"
