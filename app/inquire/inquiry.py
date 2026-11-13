"""询价：话术生成 + 报价解析（DeepSeek 优先，规则兜底）。"""
from __future__ import annotations

import re
from typing import Optional

from app.llm.client import LLMClient
from app.models.entities import Product, Quote, TargetStyle
from app.models.enums import QuoteSource, StockStatus, TaxStatus


_THINK_HINTS = (
    "let's", "let me", "count:", "so choose", "i need to", "hmm",
    "the requirement", "characters", "sentences", "roughly",
    "字数", "数一下", "这句话", "可以稍微", "要求：", "不符合",
    "我们需要", "输出一句话", "只输出", "但本次", "已有对话", "补问还缺",
    "需要补问", "本次要补问", "回答用户",
)

# 搜索卡片抓下来的标题常混着价格/销量/服务标签，直接发给卖家会很怪
_TITLE_NOISE = re.compile(
    r"[¥￥]\s*[\d.]+\s*[\s\S]*$"        # 从价格符号起全是噪声
    r"|运费\s*[\d.]+\s*元[\s\S]*$"
    r"|全网\s*\d+[\s\S]*$"
    r"|回头率[\s\S]*$"
    r"|(首单|包邮|退货|先采后付|当天达|次日达|\d+天达|批发价)[\s\S]*$"
)
# 卡片标题尾部还粘着属性标签块：「……水桶帽子 防晒 ｜ 腈纶 ｜ 缎档」。
# 真实事故（2026-11-06 义乌希因帽业）：这句被原样发给卖家，对方看不懂，
# 连问三次「哪款呢」，最后判定我们是机器人。属性块一律不进口语。
_ATTR_BLOCK = re.compile(r"\s*[｜|][\s\S]*$")
# 开头的促销词：「夏季特惠！」「清仓！」——念给卖家没有信息量
_PROMO_HEAD = re.compile(
    r"^(?:夏季|春季|秋季|冬季|新款|爆款|热卖|特惠|清仓|厂家直销|一件代发)"
    r"[^！!]{0,6}[！!]\s*"
)


def _strip_tail_tags(t: str) -> str:
    """去掉截断属性块后残留在末尾的孤立标签词（≤4 字且前文出现过）。

    例：「……水桶帽子 防晒」→ 删掉与 ｜ 块同源的「防晒」→「……水桶帽子」。
    只在前文重复出现时才删，避免把「渔夫帽」这类真词误砍。
    """
    while True:
        m = re.search(r"\s+([\u4e00-\u9fa5A-Za-z]{1,4})$", t)
        if not m:
            break
        word = m.group(1)
        if word in t[:m.start()]:
            t = t[:m.start()]
        else:
            break
    return t


def clean_product_title(title: str, limit: int = 26) -> str:
    """把候选商品标题洗成「能说给卖家听的款名」。"""
    raw = (title or "").strip()
    t = raw
    if _ATTR_BLOCK.search(t):
        t = _ATTR_BLOCK.sub("", t)
        t = _strip_tail_tags(t)
    t = _TITLE_NOISE.sub("", t)
    t = _PROMO_HEAD.sub("", t)
    t = t.strip(" -|·、,｜")
    t = re.sub(r"\s{2,}", " ", t)
    if len(t) < 4:                      # 洗没了就退回原串截断
        t = raw
    return t[:limit]

# 约定模型把成品包在这个标记里：对推理型模型最可靠——它再怎么推敲，
# 最后总要按格式交付；比猜测「这段是思考还是正文」稳得多。
_MSG_TAG = re.compile(r"<msg>\s*(.*?)\s*</msg>", re.S | re.I)

# 分析口吻/结构化输出的特征。这些东西发给卖家就是一段莫名其妙的话，
# 真实踩过：模型把「无法从候选标题判断起订量…参考范围：- **现货拿样**…」
# 整段发了出去，而且是按行拆成 5 条消息轰过去的。
_META_HINTS = (
    "参考范围", "建议直接问", "建议先问", "需向供应商确认", "无法从", "无法判断",
    "以下是", "综合来看", "总结", "结论", "分析", "候选标题", "需确认", "一般来说",
    # 模型对自己「该怎么写这条消息」的推敲。真实事故：整段「用户语言中文。需要
    # respond in user's language。应该给可直接复制的话术…给两个版本：直接谈判版，
    # 稳健版。最终回复结构：先判断」被当成正文发给了卖家。
    "用户语言", "用户明确", "话术", "两个版本", "回复结构", "直接复制",
    "建议首轮", "先判断", "稳健版", "谈判版", "目标价", "底价", "对方坚持",
)
_MD_HINTS = ("**", "##", "- ", "* ", "1. ", "2. ", "|", "---", "```")

# 半句话的尾巴：以这些词收尾说明句子没说完。
# 真实事故（2026-11-06 沃乐克会话）：模型输出被截成「老板，麻烦再确认」直接发给了卖家。
# 长度、字符比例都过得了当时的校验——所以必须单独拦「悬空动词/连接词结尾」。
_TAIL_DANGLING = (
    "再确认", "确认一下", "确认下", "再问", "问一下", "问下", "再说一下",
    "说一下", "说下", "看一下", "看下", "麻烦", "麻烦你", "帮我", "顺便",
    "还有个", "还有", "以及", "关于", "我这边", "您这边", "老板你",
)


def looks_like_chat_message(t: str) -> bool:
    """这条文本能不能直接发给卖家。

    标准很朴素：单行、口语长度、不是列表/标题/分析段。
    宁可退回规则模板，也不能把推理过程发出去——模板至少信息完整、语句通顺。
    """
    if not t:
        return False
    if "\n" in t or "\r" in t:
        return False
    zh = len(re.findall(r"[\u4e00-\u9fa5]", t))
    if zh < 6 or not (8 <= len(t) <= 220):
        return False
    if any(h in t for h in _META_HINTS):
        return False
    if any(h in t for h in _MD_HINTS):
        return False
    # 省略号式复述：「老板，看到...想拿100件。现在什么价？……」
    # 模型把长商品标题缩成「...」，发出去是一句主语都没有的残话。真实事故
    # （2026-11-06 义乌希因帽业）：卖家先问「哪款呢」，收到这句后直接问「机器人那？」。
    # 采购话术里没有非用省略号不可的场合，命中就退回模板（模板一定写全）。
    if re.search(r"\.{2,}|…", t):
        return False
    # 残句：去掉句末标点后仍停在动词/连接词上，说明话没说完（宁可用模板重发完整句）。
    # 只对短句生效——长句结尾的「我再看下」是正常收尾，不是被截断。
    tail = re.sub(r"[\s，,。.！!？?、；;：:~…·]+$", "", t)
    if len(tail) <= 14 and any(tail.endswith(w) for w in _TAIL_DANGLING):
        return False
    return True


def _to_one_line(t: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"\s*\n+\s*", " ", t or "")).strip()


def clean_outgoing(text: str, fallback: str = "") -> str:
    """把模型输出净化成「能直接发出去的一句话」。

    推理型模型（如 deepseek-v4.1-flash）有时会把思维链、Markdown 列表当正文
    吐出来。这里按四级策略取一句话：单行合格 → 引号内成品 → 任意一行中文短文
    → 全部不合格就回退模板。
    """
    t = (text or "").strip()
    if not t:
        return fallback
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()

    zh = len(re.findall(r"[\u4e00-\u9fa5]", t))
    looks_think = (
        len(t) > 300
        or zh / max(len(t), 1) < 0.45
        or any(h in t.lower() for h in _THINK_HINTS)
    )

    one = _to_one_line(t)
    if not looks_think and looks_like_chat_message(one):
        return one

    # 引号包裹的成品优先（模型常把最终答案写在引号里）
    for cand in reversed(re.findall(r"[「“\"]([^」”\"]{6,300})[」”\"]", t)):
        cand = _to_one_line(cand)
        if looks_like_chat_message(cand):
            return cand

    # 退而求其次：多行里挑一行能发的中文短文
    for line in reversed(t.splitlines()):
        line = _to_one_line(line.strip().strip('"').strip("“”"))
        if looks_like_chat_message(line):
            return line

    return fallback


# 这些词只会出现在「模型对指令的推敲」里，不会出现在真实采购消息里。
# 一旦命中，说明模型把思考写进了正文，宁可退回模板也不发出去。
_META_HINTS = _META_HINTS + ("输出", "补问", "字数", "重复已经", "一句话", "msg",
                             "标签", "绝对不要", "上述", "要求绝对", "算重复")


def parse_model_message(raw: str, fallback: str = "", min_len: int = 8) -> str:
    """从模型输出里取出「真正要发出去的那句话」。

    三级策略：取 <msg> 标记内容 → 启发式净化 → **质量校验**。
    校验这步不能省：推理型模型偶尔会吐出「消息」「好的」这类残句，或者把
    「需要一句话，最好直接输出 msg」这种自我推敲写进正文。发到旺旺上就是
    一句没头没尾甚至莫名其妙的话，比不发还糟，所以宁可回退规则模板
    （模板至少信息完整、语句通顺、必问项齐全）。
    """
    m = _MSG_TAG.search(raw or "")
    cand = clean_outgoing(m.group(1) if m else raw, fallback) or fallback
    if len(cand) < min_len:
        return fallback
    if any(h in cand for h in _META_HINTS):
        return fallback
    if not looks_like_chat_message(cand):
        return fallback
    return cand


def _facts_opening(facts, quantity: Optional[int], asks: list[str]) -> str:
    """把「页面已知」与「还要问的」拼成一句人话。

    目标是把卖家要回答的问题从六项压到一两项：页面写了起订量、阶梯价、运费，
    就别再问一遍，只确认「我这个量按哪档走、含不含税、几天发」。
    """
    bits: list[str] = []
    if facts.price:
        bits.append(f"{facts.moq}{facts.unit}起批 ¥{facts.price:g}"
                    if facts.moq else f"标价 ¥{facts.price:g}")
    tiers = sorted([t for t in facts.tiers if t.get("price")],
                   key=lambda t: t.get("qty") or 0)
    for t in tiers[:2]:
        bits.append(f"{t['qty']}{facts.unit}以上 ¥{float(t['price']):g}")
    known = ("，页面 " + "、".join(bits)) if bits else ""

    qty_part = f"，我要 {quantity} {facts.unit}" if quantity else "，想拿一批"
    bulk = facts.bulk_price(quantity)
    if bulk and facts.price and bulk < facts.price - 1e-9:
        ask_part = f"，按 ¥{bulk:g} 能走吗？"
    elif quantity:
        ask_part = "，这个量能给到什么价？"
    else:
        ask_part = "，现在什么价？"

    tail = "，".join(asks[:2])
    if tail:
        ask_part = ask_part + tail + "？"
    return known + qty_part + ask_part


def build_inquiry_script(
    target: TargetStyle,
    product: Product,
    buyer_quantity: Optional[int] = None,
    llm: Optional[LLMClient] = None,
    facts=None,
    asks: Optional[list[str]] = None,
) -> str:
    """生成询价话术。带属性核验问题，兼顾问价与辨货。

    `facts`（详情页公示信息）给了就改口径：页面写明的价格/起订/运费/库存
    不再问一遍，只问「我这个量按哪档、含不含税、几天发」。
    """
    llm = llm or LLMClient()
    attr_line = f"属性：{target.attributes}" if target.attributes else ""
    qty_line = f"数量：{buyer_quantity} 件" if buyer_quantity else "数量：请告知起订量"

    cand = clean_product_title(product.title)
    use_facts = bool(facts and facts.has_any())
    default_asks = ["是否含税", "多久能发货"]
    # 规则兜底模板（LLM 不可用、输出跑偏或校验不过时用）：
    # 不罗列清单，按真人聊天的口气把必问项串进两三句话里
    if use_facts:
        fallback = f"老板，看到你家「{cand}」" + _facts_opening(
            facts, buyer_quantity, asks or default_asks)
        if target.attributes and len(fallback) < 88:
            fallback += ("另外确认下"
                         + "、".join(f"{k}是{v}吧" for k, v in target.attributes.items()) + "？")
    else:
        head = f"老板，看到你家「{cand}」"
        head += f"，想拿 {buyer_quantity} 件" if buyer_quantity else "，想拿一批"
        head += "。现在什么价？起订量多少，含税和运费怎么算？有现货的话多久能发？"
        if target.attributes:
            head += ("另外确认下"
                     + "、".join(f"{k}是{v}吧" for k, v in target.attributes.items()) + "？")
        fallback = head

    if llm.available:
        try:
            known_block = ""
            if use_facts:
                known_block = (
                    f"\n商品页公示（买家自己能看到，禁止当成问题再问一遍）："
                    f"{facts.summary(buyer_quantity)}\n"
                    "只问页面上看不出来的：这个采购量按哪档价、是否含税、多久能发货。"
                )
                if asks:
                    known_block += f"本轮还要问：{'、'.join(asks[:3])}。"
            # 有页面信息时问项要求必须跟着变：还照旧「把起订量/运费/现货都问进去」，
            # 模型就会把页面上明明写着的项再问一遍，预填等于白做。
            ask_req = ("页面上的数字要顺口说出来（让卖家知道你看过页面），"
                       "然后只问：这个量按哪一档价、是否含税、多久能发货；"
                       "起订量、运费、库存页面上写了的，一个字都不要再问。"
                       if use_facts else
                       "把价格、起订量、含税与运费、有没有现货、"
                       "多久能发货自然揉进句子里；")
            raw = llm.chat([
                {"role": "system", "content": (
                    "你在 1688 旺旺上跟卖家询价。直接输出要发出去的那条消息，"
                    "不要输出任何思考过程、解释或自我推敲。\n"
                    "消息要求：像真人打字，60~120 字，最多 3 句，"
                    "禁止编号、分点、换行列表；用「老板」这类 1688 常见称呼，"
                    "不要客服腔；" + ask_req +
                    "顺带确认目标款的关键属性是否一致。\n"
                    "输出格式：把要发的那句话写在 <msg></msg> 之间，"
                    "标记外不要写任何内容（不要解释、不要数字数）。"
                )},
                {"role": "user", "content": (
                    f"目标款：{target.name}\n{target.description or ''}\n"
                    f"{attr_line}\n{qty_line}\n"
                    f"候选商品标题：{cand}{known_block}"
                )},
            ], temperature=0.6, max_tokens=400)
            msg = parse_model_message(raw, fallback)
            # 询价消息连「价」字都没提到，说明模型跑偏了，直接用模板（信息更全）
            if "价" not in msg and "多少" not in msg:
                return fallback
            # 硬约束：页面写明的项，模型照样追问的一律退回模板。
            # 光在提示词里说「别问」不够，实测模型仍会写「运费怎么算，几天能发」。
            if use_facts:
                known = facts.known_gaps(buyer_quantity)
                asked_again = (
                    ("moq" in known and re.search(r"起订|起批", msg))
                    or ("shipping" in known and re.search(r"运费怎么|运费多少|包邮吗|运费是", msg))
                    or ("stock" in known and re.search(r"有现货吗|现货吗|库存多少", msg))
                    or ("tax" in known and re.search(r"含税吗|含税么|包不包税", msg))
                )
                if asked_again:
                    return fallback
            return msg
        except Exception:
            pass  # 降级到规则模板

    return fallback


def _parse_rules(reply: str) -> dict:
    """规则兜底：正则抽取价格/起订量/含税/现货/发货时效。"""
    result: dict = {
        "price": None, "moq": None, "tax": TaxStatus.UNKNOWN, "tiers": [],
        "stock_status": StockStatus.UNKNOWN, "lead_time_hours": None,
        "includes_shipping": None,
    }
    # 价格：形如「5.5 元」「5元」「¥5」「5.5/件」
    m = re.search(r"[¥￥]\s*(\d+(?:\.\d+)?)", reply)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:元|块)/?(?:件|个|只|条|套|包)?", reply)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(?:件|个|只|条|套|包|pcs|PCS)", reply)
    if not m:
        # 「48一个」「45一件」——1688 上最常用的口语单价，反而最容易被漏
        m = re.search(r"(\d+(?:\.\d+)?)\s*[一每]\s*(?:个|件|只|条|套|包|台)", reply)
    if not m:
        # 「最低47.5」「再低就做到45」——卖家让价时常用，不带单位
        m = re.search(r"(?:最低|底价|最多做到|做到|给到|给你|算你)\s*[¥￥]?\s*(\d+(?:\.\d+)?)",
                      reply)
    if m:
        result["price"] = float(m.group(1))
    if "不含税" in reply or "不含票" in reply:
        result["tax"] = TaxStatus.EXCLUDED
    elif "含税" in reply or "含票" in reply:
        result["tax"] = TaxStatus.INCLUDED
    # 运费口径（比价必须拉平：报价含不含运费，差异常比单价还大）
    # 先判否定再判肯定：「不含运费」里也含「含运费」三个字，顺序错了会全判反
    # 卖家常用「要看你发哪个地址/按地区算」表达不包邮，这类条件式说法也算不含运费
    if re.search(
            r"不含运|运费另|运费自理|运费到付|运费自付|运费自担|不包邮|物流费另"
            r"|运费实收|运费现收|运费单算"
            r"|(?:看|按|视|根据)[^，,。；;]{0,10}(?:地址|地区|省份|地方)[^，,。；;]{0,6}运费"
            r"|运费[^，,。；;]{0,10}(?:看|按|视|根据)[^，,。；;]{0,8}(?:地址|地区|省份|地方|实际|重量|方数)",
            reply):
        result["includes_shipping"] = False
    elif re.search(r"包邮|含运费|免运费|运费我(们)?出|运费包含|运杂费含", reply):
        result["includes_shipping"] = True
    # 起订量：「起订200」「200件起批」「200个起」
    mq = re.search(r"(?:起订|起批量|最小|MOQ)[^\d]*(\d+)", reply, re.IGNORECASE)
    if not mq:
        mq = re.search(r"(\d+)\s*[件个只条套包台]\s*起(?:批|订|售|发)?", reply)
    if mq:
        result["moq"] = int(mq.group(1))
    # 现货状态：注意先判「现货」，再判预售/定制
    if "现货" in reply:
        result["stock_status"] = StockStatus.IN_STOCK
    elif "预售" in reply or "期货" in reply:
        result["stock_status"] = StockStatus.PRE_SALE
    elif "定制" in reply or "订制" in reply or "按单生产" in reply:
        result["stock_status"] = StockStatus.CUSTOM
    # 发货时效：「48小时内发货」「3天发货」「3天内可以发出」（中间常夹副词）
    lt = re.search(r"(\d+)\s*(?:个)?\s*(小时|天)[以之内前后]*[^，。；;\n]{0,6}?"
                   r"(?:发货|发出|寄出|发走|走货)", reply)
    if not lt:
        lt = re.search(r"(?:发货|发出|寄出)[^\d]{0,6}(\d+)\s*(?:个)?\s*(小时|天)", reply)
    if lt:
        n = int(lt.group(1))
        result["lead_time_hours"] = n * 24 if lt.group(2) == "天" else n
    # 阶梯价：「满100件4元」「100件以上4元」「200件的话48一个」（大宗买家核心信息）
    tiers = []
    for tm in re.finditer(
            r"(?:满\s*)?(\d{2,})\s*[件个只条套包]+\s*(?:的话|以上|起)?[^\d¥￥]{0,6}[¥￥]?\s*"
            r"(\d+(?:\.\d+)?)\s*(?:元|一个|一件|一只|一台|/件|/个)",
            reply):
        qty, unit_price = int(tm.group(1)), float(tm.group(2))
        if qty > 0 and unit_price > 0 and (qty, unit_price) not in tiers:
            tiers.append((qty, unit_price))
    result["tiers"] = [{"qty": q, "price": p} for q, p in tiers]
    return result


def _fill_from_rules(q: Quote, reply: str) -> Quote:
    """用规则结果补齐 LLM 没抽到的字段（只填空白，不覆盖模型给出的值）。"""
    r = _parse_rules(reply)
    if not q.price and r["price"] is not None:
        q.price = r["price"]
    if q.moq is None:
        q.moq = r["moq"]
    if q.tax == TaxStatus.UNKNOWN:
        q.tax = r["tax"]
    if q.includes_shipping is None:
        q.includes_shipping = r["includes_shipping"]
    if q.stock_status == StockStatus.UNKNOWN:
        q.stock_status = r["stock_status"]
    if q.lead_time_hours is None:
        q.lead_time_hours = r["lead_time_hours"]
    if not q.tiers:
        q.tiers = r["tiers"]
    return q


def parse_quote(
    reply: str,
    target: TargetStyle,
    product: Product,
    llm: Optional[LLMClient] = None,
) -> Quote:
    """从卖家回复抽取结构化报价。LLM 优先，正则兜底。"""
    llm = llm or LLMClient()
    q = Quote(
        id="", target_style_id=target.id, product_id=product.id,
        shop_id=product.shop_id, source=QuoteSource.INQUIRY,
        price=0.0, raw_text=reply,
    )

    if llm.available:
        try:
            text = llm.chat([
                {"role": "system", "content": (
                    "从卖家回复中抽取报价，输出 JSON，字段：price(数字,单位元)、"
                    "moq(整数,可为null)、tax(字符串: included/excluded/unknown)、"
                    "includes_shipping(布尔,可为null)、"
                    "stock_status(字符串: in_stock=现货/pre_sale=预售或期货/custom=定制/unknown=未提及)、"
                    "lead_time_hours(发货时效,整数小时数,如「48小时内发货」=48、「3天发货」=72,未提及为null)、"
                    "condition_note(条件备注原文)、"
                    "tiers(阶梯价数组[{qty,price}],可为空)。"
                    "只输出 JSON，不要解释。"
                )},
                {"role": "user", "content": reply},
            ])
            import json
            data = json.loads(text.strip().strip("`"))
            q.price = float(data["price"])
            if data.get("moq") is not None:
                q.moq = int(data["moq"])
            tax = data.get("tax", "unknown")
            q.tax = TaxStatus(tax) if tax in TaxStatus._value2member_map_ else TaxStatus.UNKNOWN
            q.includes_shipping = data.get("includes_shipping")
            stock = data.get("stock_status", "unknown")
            q.stock_status = (StockStatus(stock)
                              if stock in StockStatus._value2member_map_ else StockStatus.UNKNOWN)
            if data.get("lead_time_hours") is not None:
                q.lead_time_hours = int(data["lead_time_hours"])
            q.tiers = data.get("tiers", [])
            q.condition_note = data.get("condition_note", "")
            # LLM 漏掉的字段用规则补位，而不是二元选一个：
            # 模型对「48 小时内发货」这类表述更准，规则对「含税」「不含运」这种
            # 关键词更稳。谁解析到就留谁的，都不填才以为没有。
            return _fill_from_rules(q, reply)
        except Exception:
            pass  # 降级

    r = _parse_rules(reply)
    if r["price"] is not None:
        q.price = r["price"]
    q.moq = r["moq"]
    q.tax = r["tax"]
    q.includes_shipping = r["includes_shipping"]
    q.stock_status = r["stock_status"]
    q.lead_time_hours = r["lead_time_hours"]
    q.tiers = r["tiers"]
    q.condition_note = reply
    return q