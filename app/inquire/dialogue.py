"""谈判大脑：缺口驱动的追问、议价发起、收敛与转人工判定。

这一层是纯逻辑（不碰浏览器、不碰数据库），输入是「当前会话状态 + 已解析报价
+ 卖家最新一句话」，输出是下一步动作。因此可以完全离线回归测试，也便于把
决策过程讲清楚。

谈判被拆成两个阶段，这是与「一问一答机器人」的本质区别：
1. inquiry 阶段：只做信息采集。每轮解析后算缺口，**只问缺的那几项**，不重复
   追问已经答过的东西。
2. bargain 阶段：信息齐了以后，拿全市场最低价做锚，回头要求本店降价。
   让价目标不是拍脑袋，是「市场价」与「心理价（参考价）」里更严格的那个。

证据先行：所有话术都基于已有数据（别家报了多少、我采购多少件），不出
无法兑现的承诺（不承诺下单时间、不承诺长期合作量）。
"""
from __future__ import annotations

import os
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from app.inquire.detail_facts import DetailFacts
from app.inquire.inquiry import (
    build_inquiry_script,
    clean_outgoing,
    clean_product_title,
    parse_model_message,
)
from app.llm.client import LLMClient
from app.models.entities import InquirySession, Product, Quote, TargetStyle
from app.models.enums import InquiryStage, MsgRole, StockStatus, TaxStatus

# 必问信息项 → 中文标签（话术里用中文，缺口清单里用 key）
GAP_LABELS = {
    "price": "单价",
    "moq": "起订量",
    "tax": "是否含税",
    "shipping": "运费怎么算",
    "stock": "有没有现货",
    "lead_time": "多久能发货",
    # 条件项：只在我方采购量明确、且卖家只给了单档价时才问。
    # 1688 上「1 个起批 56.9」和「200 件 47」是两个价，拿前者去比别家 200 件的
    # 报价会直接得出「我们已经最便宜」的假结论——量级不对齐，比价就是错的。
    "tier_price": "拿这个量能做到什么价",
}
# 缺口 key → 直接能说出口的问句（开场用）。「是否含税」这种书面说法
# 放进聊天窗口会像表单，人不会这样跟老板说话。
GAP_ASKS = {
    "price": "什么价",
    "moq": "起订量多少",
    "tax": "含税吗",
    "shipping": "运费怎么算",
    "stock": "有现货吗",
    "lead_time": "几天能发",
    "tier_price": "这个量能给到什么价",
}

# 卖家明确不让价 / 不做的信号
_REFUSE_WORDS = (
    "最低", "不能再少", "不能少", "少不了", "没得少", "不接受议价", "不还价",
    "已经是最低", "就是这个价", "做不了", "做不到", "不议价", "实价",
)
# 引导到站外聊（微信/电话）—— 平台外交易有风险，一律转人工，智能体不跟
# 「可以+个联系方式吗」也算：卖家真实发过，比「加微信」更隐晦，但目的一样
_OFF_CHANNEL_WORDS = ("加微信", "加我微信", "微信聊", "加个微信", "电话聊", "打我电话",
                      "加qq", "加QQ", "个联系方式", "加个v", "加v", "vx号", "wx号",
                      "微信号", "手机号发我")
# 卖家承诺「我去问/稍等」——中间态，不是答案。这时候再催一遍就是骚扰，
# 真实踩过：卖家回「好的 这边问下」，机器人立刻又追问一遍同样的时效和价格。
_PENDING_WORDS = (
    "问下", "问问", "问一下", "稍等", "等一下", "等下", "稍后", "回头",
    "确认下再", "去查", "核实", "请示", "报给", "算一下", "查一下", "看下库存",
    "晚点", "下午给", "明天给", "半小时", "一会儿",
    # 「店长还没回，回了给您留言」——同样是在处理中，等就行
    "未回复", "还没回", "还没有回", "没有回复", "会留言", "给您留言", "暂时没有",
)
# 卖家在问「你说的是哪一款」——这时候该给链接，不是再把开场复述一遍。
# 真实事故（2026-11-06 义乌希因帽业）：卖家连问三次「哪款呢」「链接是哪个」
# 「哪款链接啊？？？」，引擎每次都重发一遍询价，最后被回「机器人那？」。
_ASK_WHICH_WORDS = (
    "哪款", "哪个款", "哪一款", "哪一个款", "哪件", "哪个商品", "哪个产品",
    "哪个链接", "链接是哪个", "链接发", "发个链接", "发下链接", "什么链接",
    "哪个sku", "哪款呢", "哪款啊", "哪款?",
)
# 卖家在喊人：「有人吗」「在吗」「怎么不说话」。
# 真实事故（2026-11-06 义乌希因帽业）：这条被当成「内容重复」跳过发送，
# 卖家对着一个不吭声的窗口，只会更确信对面是脚本。对方喊人必须先应一句。
_CALLING_WORDS = (
    "有人吗", "有人在吗", "在吗", "在不在", "还在吗", "人呢", "怎么不说话",
    "还问吗", "还需要吗",
)
# 卖家怀疑对面是机器人。真实事故里出现在连续三次收到同一句话之后。
# 一旦被这样问，继续自动聊只会加深怀疑——交给人工换个人格说话。
_ROBOT_SUSPECT_WORDS = (
    "机器人", "是不是人", "不是人吧", "自动回复", "脚本", "ai回复", "机器回复",
    "你是ai", "你是AI", "人机",
)


@dataclass
class DialogueConfig:
    """谈判节奏配置（全部可用环境变量覆盖，演示时把节奏调快）。"""

    required: list[str] = field(
        default_factory=lambda: ["price", "moq", "tax", "shipping", "stock", "lead_time"]
    )
    max_rounds: int = 6            # 我方最多发几条（询价 + 追问 + 议价）
    # 6 而不是 4：真实卖家一次只答一两项（「有现货」「1 个起批」「运费看地址」分开说），
    # 4 条额度在信息采集阶段就见底，永远走不到议价那一步——实测沃乐克会话就是这样收摊的。
    max_gaps_per_turn: int = 3     # 一条消息里最多同时问几项，多了像问卷
    # 同一项最多问几次。问到第 2 次对方还不答，就不该再问了——真实事故里
    # 「单价/起订量/含税」被连问四轮，卖家直接判定对面是脚本。
    max_ask_per_round: int = 2
    bargain_min_gap: float = 0.03  # 与市场最低价差 3% 以上才值得开口压价
    expire_hours: float = 24.0     # 超过多久没回就判超时（不会无限等）
    default_quantity: int = 100    # 买家没填数量时的可谈量（卖家要量才报价，不能空手问）

    @classmethod
    def from_env(cls) -> DialogueConfig:
        def _f(key: str, default: float) -> float:
            try:
                return float(os.getenv(key, "") or default)
            except ValueError:
                return default

        def _i(key: str, default: int) -> int:
            try:
                return int(os.getenv(key, "") or default)
            except ValueError:
                return default

        return cls(
            max_rounds=_i("INQUIRY_MAX_ROUNDS", 6),
            max_gaps_per_turn=_i("INQUIRY_MAX_GAPS", 3),
            max_ask_per_round=_i("INQUIRY_MAX_ASK_PER_ITEM", 2),
            bargain_min_gap=_f("INQUIRY_BARGAIN_MIN_GAP", 0.03),
            expire_hours=_f("INQUIRY_EXPIRE_HOURS", 24.0),
            default_quantity=_i("INQUIRY_DEFAULT_QTY", 100),
        )


@dataclass
class Decision:
    """下一步该做什么。action 取值见 decide() 文档。"""

    action: str
    text: str = ""
    reason: str = ""
    gap: list[str] = field(default_factory=list)
    delay_s: int = 0
    stage: InquiryStage = InquiryStage.INQUIRY
    # 这句是「应答对方呼叫」（在的…），允许与上一条正文相同——对方在等回话，
    # 沉默比重复更伤。引擎据此豁免一次去重。
    answer_call: bool = False


# ---------------------------------------------------------------------------
# 报价的增量合并与缺口计算
# ---------------------------------------------------------------------------
def compute_gaps(quote: Optional[Quote], required: list[str],
                 quantity: Optional[int] = None,
                 tier_min_qty: int = 100,
                 sample_moq_max: int = 10) -> list[str]:
    """按「已拿到什么」算还缺什么。没有报价时全部缺。

    `quantity` 给了才会追加 tier_price，且只在「卖家报的是拿样档」时追加：
    1688 上「1 个起批 56.9」「10 个起 52」这类是拿样/代发价，几十上百件起批
    才是批发档。拿拿样价去比别家的批发价，比出来的「我们最便宜」是假的。
    """
    if quote is None:
        return list(required)
    # 注意：报价存在但 price=0 只代表「价格还没拿到」，不代表其他字段也白问了。
    # 旧写法在这里直接 return 全部缺口，导致卖家答了含税/现货也被当成没答，
    # 下一轮又问一遍——正是「同一批问题反复问」的源头之一。
    gaps: list[str] = []
    for key in required:
        if key == "price" and not quote.price:
            gaps.append(key)
        elif key == "moq" and quote.moq is None:
            gaps.append(key)
        elif key == "tax" and quote.tax == TaxStatus.UNKNOWN:
            gaps.append(key)
        elif key == "shipping" and quote.includes_shipping is None:
            gaps.append(key)
        elif key == "stock" and quote.stock_status == StockStatus.UNKNOWN:
            gaps.append(key)
        elif key == "lead_time" and quote.lead_time_hours is None:
            gaps.append(key)
    # 卖家已经按我这个量（或更大的量）报了阶梯价，就不用再问一次
    covered = bool(quantity) and any(
        isinstance(t, dict) and (t.get("qty") or 0) >= (quantity or 0)
        for t in (quote.tiers or []))
    if (quantity and quantity >= tier_min_qty and not covered
            and (quote.moq is None or quote.moq <= sample_moq_max)
            and "tier_price" not in gaps):
        gaps.append("tier_price")
    return gaps


def merge_quote(base: Optional[Quote], patch: Quote) -> Quote:
    """把新一轮解析结果合并进已有报价。

    卖家习惯分几条说：先报价、再补起订量、最后补运费。如果每轮覆盖，
    前面的信息就丢了——所以这里做「非空才覆盖」的增量合并。
    """
    if base is None:
        return patch
    merged = base.model_copy(deep=True)
    if patch.price:
        merged.price = patch.price
    if patch.moq is not None:
        merged.moq = patch.moq
    if patch.tax != TaxStatus.UNKNOWN:
        merged.tax = patch.tax
    if patch.includes_shipping is not None:
        merged.includes_shipping = patch.includes_shipping
    if patch.stock_status != StockStatus.UNKNOWN:
        merged.stock_status = patch.stock_status
    if patch.lead_time_hours is not None:
        merged.lead_time_hours = patch.lead_time_hours
    if patch.tiers:
        seen = {(t.get("qty"), t.get("price")) for t in merged.tiers if isinstance(t, dict)}
        for t in patch.tiers:
            if isinstance(t, dict) and (t.get("qty"), t.get("price")) not in seen:
                merged.tiers.append(t)
    if patch.condition_note:
        merged.condition_note = (merged.condition_note + " / " + patch.condition_note).strip(" /")
    # 原文按时间累积，保留完整对话证据
    if patch.raw_text:
        prev = merged.raw_text or ""
        merged.raw_text = (prev + "\n" + patch.raw_text).strip() if prev else patch.raw_text
    return merged


def pick_market_min(quotes: list[Quote], exclude_product_id: str = "",
                    exclude_shop_id: str = "",
                    exclude_quote_id: str = "") -> Optional[float]:
    """全市场最低有效报价。

    按 **店铺/报价** 排除自己，而不是按商品：同一个 1688 商品链接会被多家店
    挂出来卖（跟卖），按 product_id 排除会把真实的竞店报价一起丢掉，锚点就
    虚高了。
    """
    prices = [
        q.price for q in quotes
        if q.price and q.price > 0
        and (not exclude_product_id or q.product_id != exclude_product_id)
        and (not exclude_shop_id or q.shop_id != exclude_shop_id)
        and (not exclude_quote_id or q.id != exclude_quote_id)
    ]
    return min(prices) if prices else None


# ---------------------------------------------------------------------------
# 卖家意图识别（规则优先，够用且可解释，不烧 token）
# ---------------------------------------------------------------------------
def classify_reply(text: str) -> str:
    """把卖家回复粗分类：off_channel / ask_which / refuse / pending / normal。

    pending 是「我这边问下 / 稍等」这类中间态：信息没给，但对方已经在处理，
    此时该等，不该再问一遍。
    ask_which 是「哪款 / 链接发我」——他要的是商品链接，不是再听一遍询价话术。
    calling 是「有人吗 / 在吗」——对方在等回应，这时候沉默比废话更可疑。
    顺序很重要：先判站外引流（最该停手），再判问款（有明确动作），其余才往下走。
    """
    t = (text or "").lower()
    if any(w.lower() in t for w in _OFF_CHANNEL_WORDS):
        return "off_channel"
    if any(w.lower() in t for w in _ROBOT_SUSPECT_WORDS):
        return "suspect_bot"
    if any(w.lower() in t for w in _ASK_WHICH_WORDS):
        return "ask_which"
    if any(w in t for w in _CALLING_WORDS):
        return "calling"
    if any(w in t for w in _REFUSE_WORDS):
        return "refuse"
    # 先判「去确认」，再判普通：否则「好的 这边问下」会被当成一句普通回复
    if any(w in t for w in _PENDING_WORDS):
        return "pending"
    return "normal"


def should_bargain(quote: Optional[Quote], market_min: Optional[float],
                   floor_price: Optional[float], cfg: DialogueConfig) -> bool:
    """值不值得开口压价：有对比锚点、且价差足够大。"""
    if not quote or not quote.price:
        return False
    # 对标市场最低价
    if market_min and market_min > 0 and market_min < quote.price:
        if (quote.price - market_min) / market_min >= cfg.bargain_min_gap:
            return True
    # 或者对标心理价（参考价）
    if floor_price and floor_price > 0 and quote.price > floor_price * 1.05:
        return True
    return False


def bargain_target(quote: Quote, market_min: Optional[float],
                   floor_price: Optional[float]) -> float:
    """争取的目标价：市场最低价与心理价取更严格者，留 1% 余量避免压死。"""
    candidates = []
    if market_min and market_min > 0:
        candidates.append(market_min)
    if floor_price and floor_price > 0:
        candidates.append(floor_price)
    if not candidates:
        return round(quote.price * 0.95, 2)
    return round(min(candidates) * 0.99, 2)


# ---------------------------------------------------------------------------
# 话术生成
# ---------------------------------------------------------------------------
# 缺口 → 话术里应该出现的词。用来判断模型那句话到底有没有问到点上：
# 真实踩过：卖家只差「时效 + 大货价」，模型回了「200件能做到什么价」——
# 语法没毛病、长度也够，但漏问了一半，等于白聊一轮。
_GAP_KEYWORDS = {
    "price": ("价", "多少钱"),
    "moq": ("起订", "起批", "最少", "多少件起"),
    "tax": ("税", "票"),
    "shipping": ("运费", "邮费", "包邮"),
    "stock": ("现货", "库存", "备货"),
    "lead_time": ("发货", "多久", "时效", "几天", "什么时候"),
    "tier_price": ("价", "多少钱"),
}


def _covers_gaps(text: str, gaps: list[str], need: float = 0.6) -> bool:
    """这句话有没有把该问的都问到（按关键词覆盖率，宽松阈值）。"""
    if not gaps:
        return True
    hit = sum(1 for g in gaps
              if any(k in text for k in _GAP_KEYWORDS.get(g, ())))
    return hit / len(gaps) >= need


def build_followup(
    gaps: list[str],
    target: TargetStyle,
    product: Product,
    history: list[str] | None = None,
    llm: Optional[LLMClient] = None,
    cfg: Optional[DialogueConfig] = None,
    quantity: Optional[int] = None,
    facts: Optional[DetailFacts] = None,
) -> str:
    """针对缺口生成追问（只问缺的，不重复已答的）。

    带上我方的采购量：卖家说「要看数量才能报」时，不给数量就永远问不出价；
    给数量本身就是谈判筹码，也是把「报价」变成「大货价」的唯一入口。

    `facts`（详情页公示）给了就在话里带出页面单价：卖家一眼看出我们看过页面，
    而不是照着清单播报——「页面 15.8，我要 500 件，能给到多少」比「什么价」有效得多。
    """
    cfg = cfg or DialogueConfig.from_env()
    ask = gaps[: cfg.max_gaps_per_turn]
    labels = [GAP_LABELS.get(k, k) for k in ask]
    if facts and facts.price and quantity:
        qty_line = f"我们这边按 {quantity} 件拿（页面单价 ¥{facts.price:g}），"
    elif facts and facts.price:
        qty_line = f"页面单价 ¥{facts.price:g}，"
    else:
        qty_line = f"我们这边按 {quantity} 件拿，" if quantity else ""

    if len(labels) == 1:
        fallback = f"好的，{qty_line}再确认一下{labels[0]}？"
    else:
        fallback = (f"好的，{qty_line}再确认下："
                    f"{'、'.join(labels[:-1])}，还有{labels[-1]}？")
    # 同时问「这个量的价」和运费/时效时，模板已经够用；但一句话里塞四件事不合适，
    # 优先保住价格相关项（gap 顺序里 tier_price 在最后，这里固定随行不占位）

    if llm and llm.available:
        try:
            hist = "\n".join((history or [])[-6:])
            qty_hint = (f"我方采购量：{quantity} 件（直接把这个数量说出来，让对方按这个量报）"
                        if quantity else "我方采购量：未定")
            raw = llm.chat([
                {"role": "system", "content": (
                    "你是采购员，正在 1688 旺旺上跟卖家聊。直接输出要发出去的那句话，"
                    "不要输出思考过程或解释。上一轮已经问过一些问题，"
                    "现在只补问还缺的项，绝对不要重复已经问过的内容。"
                    "卖家如果说「要看数量/地址才能报」，就把我们能提供的数量直接给出来，"
                    "再请他给准数。"
                    "要求：一句话，口语，礼貌但不啰嗦，不要编号，不要客套超过 6 个字，"
                    "句子必须说完整（不能停在「再确认」这种半句上），"
                    "不要复述商品名或重复开场那句，不要用省略号，"
                    "20~60 字，把要问的几项都问进去，别只问一项就收尾。\n"
                    "输出格式：把要发的那句话写在 <msg></msg> 之间，标记外不要写任何内容。"
                )},
                {"role": "user", "content": (
                    f"目标款：{target.name}\n候选商品：{clean_product_title(product.title)}\n"
                    f"{qty_hint}\n"
                    f"本次要补问：{'、'.join(labels)}\n"
                    + (f"商品页公示（别再问这些）：{facts.summary(quantity)}\n"
                       if facts and facts.has_any() else "")
                    + f"已有对话：\n{hist or '（刚发了开场询价）'}"
                )},
            ], temperature=0.5, max_tokens=300)
            msg = parse_model_message(raw, fallback)
            # 追问阶段的两条硬约束：
            # ① 不许复述开场（「老板，看到你家…想拿100件」已经说过了，再说一遍就是复读）；
            # ② 不许把商品名缩成「...」（2026-11-06 真实外发事故，卖家当场问「机器人那？」）。
            if ("看到" in msg and "想拿" in msg) or "..." in msg or "…" in msg:
                return fallback
            # 问到点上才算数：漏项的短句退回模板（模板一定把缺口列全）
            return msg if _covers_gaps(msg, ask) else fallback
        except Exception:
            pass

    return fallback


def build_link_reply(url: str, quantity: Optional[int] = None) -> str:
    """卖家问「哪款 / 链接发我」时，把商品链接给过去。

    真实事故（2026-11-06 义乌希因帽业）：卖家问「哪款呢？」，引擎走了通用追问，
    又发了一遍开场询价；卖家再问「链接是哪个？」「哪款链接啊？？？」，
    第三遍仍是同样的问价句 —— 对方直接判定「机器人那？」。
    人不会在对方问「哪款」时继续报需求，人会把链接甩过去。
    """
    qty = f"按 {quantity} 件" if quantity else "按这个量"
    return f"就是这款，麻烦{qty}报个价，含税和运费一起算：{url}"


def build_bargain(
    target: TargetStyle,
    product: Product,
    quote: Quote,
    market_min: Optional[float],
    quantity: Optional[int],
    llm: Optional[LLMClient] = None,
    floor_price: Optional[float] = None,
) -> str:
    """议价话术：锚点（别家价/采购量）→ 具体诉求 → 给台阶。"""
    aim = bargain_target(quote, market_min, floor_price)
    qty = quantity or 100

    if market_min and market_min < quote.price:
        head = f"老板，同款我这边另一家给到 {market_min:.2f} 元"
    else:
        head = f"老板，我这个量是 {qty} 件"
    fallback = (f"{head}，你这边 {aim:.2f} 元能做吗？"
                f"可以的话我就按这个价报上去，不行也没关系，我再看下。")

    if llm and llm.available:
        try:
            anchor = (f"同款另一家报 {market_min:.2f} 元" if market_min
                      else f"我预期价位在 {aim:.2f} 元左右")
            raw = llm.chat([
                {"role": "system", "content": (
                    "你是采购员，正在跟 1688 卖家谈价。直接输出要发出去的那句话，"
                    "不要输出思考过程或解释。结构：①一句锚点（同款别家报价或我的采购量）"
                    "②明确诉求（能做到多少）③给台阶（能做到就下单/做不到也理解）。"
                    "铁律：不虚构别家报价、不承诺无法兑现的事、不贬低对方产品；"
                    "总共不超过 3 句，口语。不要承诺下单/付款/签约时间。\n"
                    "输出格式：把要发的那句话写在 <msg></msg> 之间，标记外不要写任何内容。"
                )},
                {"role": "user", "content": (
                    f"商品：{clean_product_title(product.title)}\n对方报价：{quote.price} 元"
                    f"（{'含税' if quote.tax == TaxStatus.INCLUDED else '税未确认'}，"
                    f"起订 {quote.moq or '未知'}）\n"
                    f"{anchor}\n采购量：{qty} 件\n我的目标价：约 {aim} 元"
                )},
            ], temperature=0.5, max_tokens=400)
            msg = parse_model_message(raw, fallback)
            # 议价话术必须是一句「开口问对方能不能做」的话：
            # 没有问句标记、或者分句太多（超过 3 个句号/分号）的，都是模型在
            # 写分析而不是在说话，一律退回模板。
            if not re.search(r"[？?]|吗|能不能|可不可以|行不行", msg):
                return fallback
            if len(re.findall(r"[。；;]", msg)) > 3:
                return fallback
            # 承诺下单/付款/签约是采购员不能说出口的话：说出口就成了报价里的条款，
            # 卖家会拿它当筹码。真实跑出来过「能做就下单」。
            if re.search(r"(就|马上|立刻|立即)(下单|订|采|签)|签合同|先付款|打定金", msg):
                return fallback
            return msg
        except Exception:
            pass

    return fallback


# ---------------------------------------------------------------------------
# 主决策
# ---------------------------------------------------------------------------
def backoff_seconds(round_no: int, expire_hours: float = 24.0) -> int:
    """等回复的轮询间隔：越等越稀，带抖动避免整点齐发。

    所有档位都按 INQUIRY_MIN_DELAY（默认 60 秒）等比缩放，而不是只调第一档——
    否则演示时第一轮 5 秒、第二轮又跳回 180 秒，链路根本跑不到议价那一步。
    """
    try:
        first = max(int(os.getenv("INQUIRY_MIN_DELAY", "") or 60), 3)
    except ValueError:
        first = 60
    ladder = [first, first * 3, first * 10, first * 30, first * 120]
    base = ladder[min(max(round_no - 1, 0), len(ladder) - 1)]
    # 上限不超过过期时间的一半，避免一次等待跨过超时点
    cap = int(expire_hours * 3600 / 2)
    return min(base, cap) + random.randint(0, max(base // 4, 1))


def decide(
    *,
    session: InquirySession,
    quote: Optional[Quote],
    seller_text: str = "",
    market_min: Optional[float] = None,
    target: TargetStyle,
    product: Product,
    cfg: Optional[DialogueConfig] = None,
    llm: Optional[LLMClient] = None,
    history: Optional[list[str]] = None,
    asked_counts: Optional[dict[str, int]] = None,
    clarify_sent: int = 0,
    facts: Optional[DetailFacts] = None,
    now: Optional[datetime] = None,
) -> Decision:
    """决定下一步动作。

    返回值 action 取值：
    - send_inquiry：还没开过口，发开场询价
    - send_followup：信息不全，补问缺口
    - send_clarify：卖家问「哪款/要链接」，把商品链接给过去
    - send_bargain：信息齐了且值得压价，发议价
    - wait：等卖家回（delay_s 为下次检查间隔）
    - close：收工（报价已存/卖家拒绝/轮次到顶）
    - escalate：转人工（引导站外、异常、无法判断）
    """
    cfg = cfg or DialogueConfig.from_env()
    now = now or datetime.now()
    # 买家没填量也要有个可谈量：卖家说「看数量才能报」时，不给量就永远问不出价
    qty = session.buyer_quantity or cfg.default_quantity
    gaps = compute_gaps(quote, cfg.required, quantity=qty)
    # 详情页已经写明的项不算缺口：问「起订量多少」而页面就写着「1 顶起批」，
    # 卖家只会觉得我们没看页面。价格仍留着——页面价是公示价，要卖家按我们的量确认。
    if facts and facts.has_any():
        known = facts.known_gaps(qty)
        gaps = [g for g in gaps if g not in known]

    # 1) 从未开口
    if session.round == 0:
        ask_labels = [GAP_ASKS.get(g, GAP_LABELS.get(g, g)) for g in gaps
                      if g not in ("price", "tier_price")]
        return Decision(
            action="send_inquiry",
            text=build_inquiry_script(target, product, qty, llm,
                                      facts=facts, asks=ask_labels),
            reason=("开场询价：页面已知的不再问，只确认这个量的价/税/时效"
                    if facts and facts.has_any() else
                    "开场询价：一次性问清价格/起订量/税费/运费/现货/时效"),
            gap=gaps,
            delay_s=backoff_seconds(1, cfg.expire_hours),
            stage=InquiryStage.INQUIRY,
        )

    # 2) 等待的条件：最后一条是我方说的，卖家还没回
    if session.last_speaker != MsgRole.SELLER:
        if session.expires_at and now >= session.expires_at:
            return Decision(
                action="close",
                reason=f"超过 {cfg.expire_hours:.0f} 小时未回复，判定超时",
            )
        return Decision(
            action="wait",
            reason="已发出，等卖家回复",
            gap=gaps,
            delay_s=backoff_seconds(session.round, cfg.expire_hours),
        )

    # 卖家最后说话，但这一轮没读到新内容：说明只是到了检查时间，不是他回了句空的。
    # 真事故：把检查点提前后，引擎对着同一句「好的 这边问下」又追问了一遍一模一样的话。
    if not (seller_text or "").strip():
        return Decision(
            action="wait",
            reason="暂无新回复，继续等（不重复追问）",
            gap=gaps,
            delay_s=backoff_seconds(session.round, cfg.expire_hours),
        )

    intent = classify_reply(seller_text)
    # 「客观还缺什么」和「能问什么」是两件事：同一项问到第二次对方还不答，
    # 就说明他不打算答，再问只会把对话聊死（真实事故里单价被连问四轮）。
    counts = asked_counts or {}
    fresh = [g for g in gaps if counts.get(g, 0) < cfg.max_ask_per_round]
    if intent == "off_channel":
        return Decision(
            action="escalate",
            reason="卖家引导站外沟通（微信/电话），平台外交易无保障，转人工",
            gap=gaps,
        )

    if intent == "suspect_bot":
        return Decision(
            action="escalate",
            reason="卖家怀疑是机器人（「机器人那？」这类），再自动聊只会加深怀疑，转人工接话",
            gap=gaps,
        )

    # 卖家在问「哪款 / 链接是哪个」：他要的是商品链接。
    # 这里必须给链接，不能走通用追问——通用追问会把需求再报一遍，
    # 对方看到的就是「一句话连发三次」，等同于自证是机器人。
    if intent == "ask_which":
        link = (product.url or "").strip()
        if link and not clarify_sent:
            return Decision(
                action="send_clarify",
                text=build_link_reply(link, qty),
                reason="卖家在问是哪一款，直接给商品链接（不再重复询价话术）",
                gap=gaps,
                delay_s=backoff_seconds(session.round + 1, cfg.expire_hours),
                stage=session.stage,
            )
        if link:
            # 链接已经发过一次，对方还在问：多半是没送到/没看到/被折叠成卡片了。
            # 这时候再发同一句毫无意义，必须人工去看一眼窗口。
            return Decision(
                action="escalate",
                reason="链接已发过，卖家仍在问哪款，可能是链接没送达或被折叠，转人工确认",
                gap=gaps,
            )

    # 卖家在喊人：先应一句，再把缺口补上。
    # 真实事故（2026-11-06）：卖家发「有人吗」，这句被判成「与上一条重复」跳过发送，
    # 窗口里我方一声不吭——对方本来就怀疑是机器人，沉默等于默认。
    if intent == "calling" and gaps:
        body = (build_followup(fresh, target, product, history, llm, cfg, quantity=qty)
                if fresh
                else "老板，我刚问的那几项方便回一下吗？我这边今天要拿去比价。")
        body = re.sub(r"^好的[，,]\s*", "", body)
        return Decision(
            action="send_followup",
            text="在的，" + body,
            reason="卖家在喊人/催问，先应答再补问缺口",
            gap=gaps,
            delay_s=backoff_seconds(session.round + 1, cfg.expire_hours),
            stage=session.stage,
            answer_call=True,
        )

    # 卖家说「我去问下 / 稍等」：他在处理，我们等，不催。
    # 催一次很便宜，代价是这个账号在卖家眼里变成一台催问机器。
    if intent == "pending" and gaps:
        return Decision(
            action="wait",
            reason="卖家表示要去确认，等他回，不催",
            gap=gaps,
            delay_s=max(backoff_seconds(session.round, cfg.expire_hours), 180),
        )

    # 3) 信息齐了 → 该谈价了
    if not gaps:
        if session.stage == InquiryStage.INQUIRY and should_bargain(
                quote, market_min, session.floor_price, cfg):
            return Decision(
                action="send_bargain",
                text=build_bargain(target, product, quote, market_min,
                                   qty, llm, session.floor_price),
                reason=(
                    f"报价完整，且本店 {quote.price:.2f} 高于"
                    f"{'市场最低 ' + format(market_min, '.2f') if market_min else '心理价'}"
                    f"，开口议价"
                ),
                gap=[],
                delay_s=backoff_seconds(session.round + 1, cfg.expire_hours),
                stage=InquiryStage.BARGAIN,
            )
        return Decision(
            action="close",
            reason=("议价已完成，收敛到当前报价" if session.stage == InquiryStage.BARGAIN
                    else "报价信息完整且价格无可谈空间"),
            gap=[],
        )

    # 4) 轮次到顶：不硬聊，拿现有信息收工
    if session.round >= cfg.max_rounds:
        return Decision(
            action="close",
            reason=f"已达 {cfg.max_rounds} 轮上限，按现有信息收工（缺："
                   f"{'、'.join(GAP_LABELS.get(g, g) for g in gaps)}）",
            gap=gaps,
        )

    # 5) 卖家已明确不让价，且已拿到单价 → 到此为止
    if intent == "refuse" and quote and quote.price:
        return Decision(
            action="close",
            reason="卖家明确表示不再让价，按当前报价收工",
            gap=gaps,
        )

    # 6) 只问「还没问过两遍」的项。缺口是「客观还缺什么」，
    # 能问什么是另一回事：真实事故（2026-11-06 义乌希因帽业）里，单价/起订量/含税
    # 被连着问了四轮，每轮措辞略变、内容一模一样，卖家看到的是一台复读机。
    # 同一项问到第二次对方还不答，就该等他自己回，或按现有信息收工。
    if not fresh:
        return Decision(
            action="wait",
            reason=(
                "该问的都问过两遍了，卖家没答，等他自己回（不再重复追问）："
                + "、".join(GAP_LABELS.get(g, g) for g in gaps)
            ),
            gap=gaps,
            delay_s=max(backoff_seconds(session.round, cfg.expire_hours), 600),
        )

    return Decision(
        action="send_followup",
        text=build_followup(fresh, target, product, history, llm, cfg,
                            quantity=qty),
        reason=f"还缺：{'、'.join(GAP_LABELS.get(g, g) for g in fresh)}",
        gap=gaps,
        delay_s=backoff_seconds(session.round + 1, cfg.expire_hours),
        stage=session.stage,
    )


def next_expiry(cfg: DialogueConfig, now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now()
    return now + timedelta(hours=cfg.expire_hours)


def strip_think(text: str) -> str:
    """去掉模型偶尔带出的思维链/引号包裹，保证发出去的是干净一句话。"""
    return clean_outgoing(text)
