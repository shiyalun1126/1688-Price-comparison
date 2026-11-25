"""演示数据：把空库一次填成「有候选、有报价、有谈判记录、有比价结论」的状态。

为什么需要它：仓库不跟踪 data/，别人 clone 下来是一张空界面，README 里的
截图无法自证。跑一遍本脚本，不配任何密钥、不登录 1688，就能看到完整工作台。

用法
    python -m scripts.seed_demo              # 写入 demo 数据
    python -m scripts.seed_demo --reset      # 只删除 demo 数据
    python -m scripts.seed_demo --force      # 已有 demo 数据时先清后写
    python -m scripts.seed_demo --db data/demo.db   # 写到别的库，不碰真实库

安全约定
    - 所有 id 以 demo- 开头，--reset 只删这些，不碰真实数据；
    - 目标库已有非 demo 目标款时默认拒绝写入（需 --allow-existing）；
    - 演示会话全部落在终态或 paused，引擎启动后不会真的发消息。
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

from app.comparison.comparison import run_comparison
from app.models.entities import (
    AgentRunRecord,
    InquiryMessage,
    InquirySession,
    InquiryTask,
    Product,
    Quote,
    Shop,
    TargetStyle,
)
from app.models.enums import (
    ChatChannel,
    InquiryStage,
    InquiryStatus,
    MatchStatus,
    MatchTier,
    MsgRole,
    QuoteSource,
    StockStatus,
    TaxStatus,
)
from app.store.db import Store

ROOT = Path(__file__).resolve().parent.parent
UPLOAD_DIR = ROOT / "data" / "uploads"
PREFIX = "demo-"
CURRENT_TARGET_KEY = "current_target_id"


class _NoLLM:
    """比价结论里的议价话术走规则模板。

    演示数据不该依赖密钥：没有 key 的人跑出来要和有 key 的人看到同一份内容，
    否则 README 截图与本地结果对不上。
    """

    available = False

    def chat(self, *args, **kwargs):  # pragma: no cover - 本类只用于禁用
        raise RuntimeError("demo 数据不调用模型")


# ---------------------------------------------------------------------------
# 示意主图（SVG）：形状 + 色块，不依赖网络与第三方库
# ---------------------------------------------------------------------------
def _svg(name: str, body: str, label: str) -> str:
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240" \
width="240" height="240">
  <defs>
    <pattern id="g" width="12" height="12" patternUnits="userSpaceOnUse">
      <path d="M12 0H0v12" fill="none" stroke="#DCDAD4" stroke-width="0.6"/>
    </pattern>
  </defs>
  <rect width="240" height="240" fill="#EFEEEA"/>
  <rect width="240" height="240" fill="url(#g)"/>
  {body}
  <text x="120" y="227" text-anchor="middle" font-family="system-ui,-apple-system,'Segoe UI',sans-serif"
        font-size="9.5" letter-spacing="0.6" fill="#8A8983">{escape(label)}</text>
</svg>
"""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / name
    path.write_text(svg, encoding="utf-8")
    return str(path)


def _hoodie(name: str, color: str, shade: str, *, hooded: bool, label: str) -> str:
    """卫衣平铺剪影。hooded=True 加一个帽子。"""
    cap = (
        f'<path d="M95 46c1-16 11-25 25-25s24 9 25 25c-8-7-16-10-25-10s-17 3-25 10z" '
        f'fill="{shade}" stroke="#26282B" stroke-width="1.2" stroke-linejoin="round"/>'
        if hooded else ""
    )
    strings = (
        '<path d="M112 40v22M128 40v22" stroke="#26282B" stroke-width="1.1" '
        'stroke-linecap="round"/>'
        if hooded else ""
    )
    body = f"""{cap}
  <path d="M120 44c-9 0-17 3-24 7L52 75l17 40 20-9-2 88c18 5 48 5 66 0l-2-88 20 9 17-40-44-24c-7-4-15-7-24-7z"
        fill="{color}" stroke="#26282B" stroke-width="1.2" stroke-linejoin="round"/>
  <path d="M99 44c4 9 11 14 21 14s17-5 21-14" fill="none" stroke="#26282B" stroke-width="1.2"/>
  {strings}
  <path d="M87 194c18 4 48 4 66 0" fill="none" stroke="{shade}" stroke-width="2.4"/>"""
    return _svg(name, body, "示意主图 · DEMO")


def _case(name: str, color: str, shade: str, label: str) -> str:
    """耳机盒剪影。"""
    body = f"""<rect x="70" y="74" width="100" height="76" rx="20" fill="{color}"
        stroke="#26282B" stroke-width="1.2"/>
  <path d="M70 108h100" stroke="{shade}" stroke-width="1.6"/>
  <ellipse cx="101" cy="130" rx="12" ry="14" fill="#FFFFFF" opacity="0.86" stroke="#26282B" stroke-width="0.8"/>
  <ellipse cx="139" cy="130" rx="12" ry="14" fill="#FFFFFF" opacity="0.86" stroke="#26282B" stroke-width="0.8"/>
  <circle cx="120" cy="164" r="2.6" fill="{shade}"/>"""
    return _svg(name, body, "示意主图 · DEMO")


# ---------------------------------------------------------------------------
# 数据定义
# ---------------------------------------------------------------------------
_TARGET_A = dict(
    id=f"{PREFIX}target-hoodie",
    name="280g 纯棉落肩卫衣 · 藏青",
    description="纯棉 280g 双面布，落肩宽松版型，藏青拼白罗纹，S-XXL 码",
    attributes={"面料": "纯棉", "克重": "280g", "版型": "落肩宽松",
                "颜色": "藏青", "尺码": "S-XXL"},
    reference_price=45.0,
    category="卫衣",
)

_TARGET_B = dict(
    id=f"{PREFIX}target-case",
    name="AirPods Pro 2 液态硅胶保护壳",
    description="液态硅胶材质，支持无线充电，含挂扣，适配 AirPods Pro 2 代",
    attributes={"材质": "液态硅胶", "适配型号": "AirPods Pro 2",
                "功能": "支持无线充电", "配件": "含金属挂扣"},
    reference_price=6.0,
    category="数码配件",
)

# (店铺名, 发货地)
_SHOPS_A = [
    ("诸暨市恒织针织厂", "浙江诸暨"),
    ("广州锦承制衣有限公司", "广东广州"),
    ("义乌市帛川服饰厂", "浙江义乌"),
    ("淮安恒美服饰有限公司", "江苏淮安"),
    ("泉州市轻野服装厂", "福建泉州"),
    ("东莞常平锦纶制衣厂", "广东东莞"),
    ("湖州织里禾木制衣厂", "浙江湖州"),
    ("青岛即墨区嘉禾服装厂", "山东青岛"),
    ("汕头潮南区新维针织", "广东汕头"),
    ("苏州吴中区诚衣坊", "江苏苏州"),
    ("嘉兴桐乡市尚织服饰", "浙江嘉兴"),
    ("中山市沙溪镇万隆制衣", "广东中山"),
]

_SHOPS_B = [
    ("深圳市宝安区悦壳数码配件厂", "广东深圳"),
    ("东莞市长安镇优品塑胶制品", "广东东莞"),
    ("义乌市小物数码配件商行", "浙江义乌"),
    ("广州市天河区潮壳贸易", "广东广州"),
]

# (商品序号, 店铺序号, 标题, 标价, MOQ, 分层, 状态, 置信度, 判定理由)
_PRODUCTS_A = [
    (1, 0, "280g纯棉卫衣男落肩宽松美式复古藏青色圆领套头衫厂家直销", 48.0, 50,
     MatchTier.EXACT, MatchStatus.VERIFIED_SAME, 0.93,
     "主图圆领开口、落肩角度与目标款一致；标题克重 280g、面料纯棉与属性核验通过"),
    (2, 1, "重磅280克纯棉卫衣女落肩宽松休闲藏蓝圆领打底衫批发", 43.0, 100,
     MatchTier.EXACT, MatchStatus.VERIFIED_SAME, 0.89,
     "克重 280g、落肩剪裁一致；图片为平铺角度，按文字属性二次核验通过"),
    (3, 2, "纯棉280g落肩卫衣 藏青 宽松慵懒风套头上衣 工厂直供", 46.5, 30,
     MatchTier.EXACT, MatchStatus.VERIFIED_SAME, 0.91,
     "领型、袖口罗纹、下摆走线与目标款一致；色号标注藏青，与目标款吻合"),
    (4, 3, "280克纯棉圆领卫衣 落肩 藏青 男女同款 200件起批", 45.0, 200,
     MatchTier.EXACT, MatchStatus.VERIFIED_SAME, 0.86,
     "克重与版型一致；图片分辨率偏低，细节证据不足，按属性核验判为同款"),
    (5, 4, "240g轻薄纯棉卫衣 藏青 落肩 春秋薄款打底", 39.0, 50,
     MatchTier.EQUIVALENT, MatchStatus.VERIFIED_EQUIVALENT, 0.71,
     "版型与颜色一致，但克重 240g 低于目标款 280g，厚度手感不同，判为等效替代"),
    (6, 5, "涤棉混纺卫衣 藏青 落肩 不起球 通勤款", 36.5, 100,
     MatchTier.EQUIVALENT, MatchStatus.VERIFIED_EQUIVALENT, 0.64,
     "外观接近，面料为涤棉混纺而非纯棉，穿着体感与缩水率不同，判为等效替代"),
    (7, 6, "320g重磅加厚卫衣 藏青 落肩 秋冬保暖", 52.0, 100,
     MatchTier.EQUIVALENT, MatchStatus.VERIFIED_EQUIVALENT, 0.68,
     "版型一致但克重 320g 明显高于目标款，属加厚款，判为等效替代"),
    (8, 7, "连帽卫衣 藏青 宽松 落肩 加绒保暖", 41.0, 50,
     MatchTier.EQUIVALENT, MatchStatus.REJECTED, 0.22,
     "目标款为圆领，该商品带帽，领型不符，排除"),
    (9, 8, "印花卫衣 藏青 落肩 图案款 街头风", 44.0, 30,
     MatchTier.EQUIVALENT, MatchStatus.REJECTED, 0.18,
     "正面有大面积印花，与目标款纯色不一致，排除"),
    (10, 9, "儿童卫衣 藏青 落肩 中大童 春秋款", 28.0, 50,
     MatchTier.EQUIVALENT, MatchStatus.REJECTED, 0.12,
     "尺码段为儿童款，与目标款成人版型不同，排除"),
    (11, 10, "卫衣 藏青 宽松 男女同款 春秋", 42.0, 50,
     None, MatchStatus.CANDIDATE, None,
     "主图缺失，标题未标注克重与面料，证据不足，保留为未核验"),
    (12, 11, "纯棉卫衣 落肩 藏青 春秋款 基础百搭", 47.0, 100,
     None, MatchStatus.CANDIDATE, None,
     "标题未标注克重，图片与目标款相似度中等，需补图后复核"),
]

_PRODUCTS_B = [
    (1, 0, "适用AirPods Pro2代液态硅胶保护壳 无线充电 带挂扣 批发", 3.8, 100,
     MatchTier.EXACT, MatchStatus.VERIFIED_SAME, 0.94,
     "商品图开孔位置、指示灯位与目标款一致；标题明确适配 Pro 2 代"),
    (2, 1, "AirPods Pro 2 硅胶耳机壳 防摔 支持无线充 多色可选", 4.2, 50,
     MatchTier.EXACT, MatchStatus.VERIFIED_SAME, 0.88,
     "材质与适配型号一致；挂扣为选配，主商品形态一致"),
    (3, 2, "AirPods Pro2 透明硬壳保护套 防刮", 2.9, 100,
     MatchTier.EQUIVALENT, MatchStatus.VERIFIED_EQUIVALENT, 0.61,
     "适配型号一致，但材质为 PC 硬壳而非液态硅胶，手感与防摔性不同"),
    (4, 3, "AirPods 3代 硅胶保护壳 无线充", 3.5, 100,
     MatchTier.EQUIVALENT, MatchStatus.REJECTED, 0.09,
     "适配型号为 AirPods 3 代，与 Pro 2 代机身尺寸不同，排除"),
]


def _gap_pairs(*items: str) -> list[str]:
    return list(items)


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------
def _demo_images() -> dict[str, str]:
    """生成全部示意主图（同色系不同形态，滚动候选时能看出是不同商品）。"""
    imgs: dict[str, str] = {}
    spec_a = [
        ("藏青-圆领", "#2E3A50", "#3B4964", False),
        ("藏蓝-平铺", "#31405A", "#3F5070", False),
        ("藏青-直供", "#2B364B", "#37455E", False),
        ("藏青-男女同款", "#33415C", "#415273", False),
        ("薄款-灰蓝", "#5C6B80", "#6E7E96", False),
        ("混纺-炭灰", "#4A4F57", "#5A606A", False),
        ("加厚-深藏", "#243044", "#2F3D55", True),
        ("连帽-藏青", "#2F3C54", "#3C4B66", True),
        ("印花-深蓝", "#3A4668", "#485578", False),
        ("儿童-浅藏", "#45577A", "#556990", False),
        ("无图-灰", "#8B8F96", "#9CA0A6", False),
        ("基础-雾蓝", "#55677F", "#647799", False),
    ]
    for i, (label, c, s, hooded) in enumerate(spec_a, start=1):
        imgs[f"p{i}"] = _hoodie(f"{PREFIX}img-hoodie-{i:02d}.svg", c, s,
                                hooded=hooded, label=f"卫衣-{i:02d}-{label}")
    spec_b = [
        ("硅胶-深灰", "#3A3E45", "#4A4F58"),
        ("硅胶-雾白", "#D8D5CE", "#C4C0B7"),
        ("硬壳-透明", "#C9CFD4", "#B6BDC4"),
        ("三代-浅灰", "#A9AEB4", "#B9BEC4"),
    ]
    for i, (label, c, s) in enumerate(spec_b, start=1):
        imgs[f"c{i}"] = _case(f"{PREFIX}img-case-{i:02d}.svg", c, s,
                              f"耳机壳-{i:02d}-{label}")
    imgs["target_a"] = _hoodie(f"{PREFIX}img-target-hoodie.svg", "#2E3A50", "#3B4964",
                               hooded=False, label="目标款-卫衣-藏青")
    imgs["target_b"] = _case(f"{PREFIX}img-target-case.svg", "#3A3E45", "#4A4F58",
                             "目标款-耳机壳")
    return imgs


def _add_messages(store: Store, session_id: str, base: datetime,
                  items: list[tuple[int, MsgRole, str, str, dict, str]]) -> None:
    """按 (分钟偏移, 角色, 类型, 文本, 解析结果, 决策标记) 写消息。

    kind=decision 的行是智能体自己的判断依据（前端灰底显示），和真实引擎
    落库的结构保持一致，否则抽屉里会少掉最能说明「它在想什么」的那些行。
    """
    for idx, (minutes, role, kind, text, extracted, _tag) in enumerate(items):
        store.add_message(InquiryMessage(
            id=f"{PREFIX}msg-{session_id.replace(PREFIX + 'session-', '')}-{idx:02d}",
            session_id=session_id,
            role=role,
            kind=kind,
            text=text,
            extracted=extracted,
            ts=base + timedelta(minutes=minutes),
        ))


def _seed_target_a(store: Store, imgs: dict[str, str], base: datetime) -> str:
    ts = TargetStyle(image_paths=[imgs["target_a"]], **_TARGET_A)
    store.save_target_style(ts)

    shops: list[Shop] = []
    for i, (name, loc) in enumerate(_SHOPS_A, start=1):
        s = Shop(id=f"{PREFIX}shop-a{i}", name=name, location=loc)
        store.save_shop(s)
        shops.append(s)

    products: dict[int, Product] = {}
    for idx, si, title, price, moq, tier, status, score, reason in _PRODUCTS_A:
        p = Product(
            id=f"{PREFIX}prod-a{idx}",
            target_style_id=ts.id,
            shop_id=shops[si].id,
            title=title,
            image_paths=[imgs[f"p{idx}"]] if idx != 11 else [],
            url=f"https://detail.1688.com/offer/{900000000000 + idx}.html",
            attributes={"面料": "纯棉", "克重": "280g"},
            listed_price=price,
            moq=moq,
            match_status=status,
            match_tier=tier,
            match_score=score,
            match_reason=reason,
            matched_at=base - timedelta(hours=2) if tier else None,
            created_at=base - timedelta(hours=4),
        )
        store.save_product(p)
        products[idx] = p

    # ---- 会话 1：已收工（询价 → 补问 → 议价成功） ----
    s1_id = f"{PREFIX}session-1"
    inq1_id = f"{PREFIX}inq-1"
    script1 = "你好，这款纯棉 280g 卫衣 200 件什么价？含税吗，运费怎么算，多久能发货？"
    store.save_inquiry(InquiryTask(
        id=inq1_id, target_style_id=ts.id, product_id=products[1].id,
        shop_id=shops[0].id, script=script1, status=InquiryStatus.QUOTED,
        buyer_quantity=200,
        created_at=base - timedelta(hours=3), updated_at=base - timedelta(hours=1),
    ))
    store.save_session(InquirySession(
        id=s1_id, inquiry_id=inq1_id, target_style_id=ts.id,
        product_id=products[1].id, shop_id=shops[0].id,
        offer_url=products[1].url or "", channel=ChatChannel.CDP,
        stage=InquiryStage.BARGAIN, status=InquiryStatus.QUOTED, round=3,
        gap=[], last_speaker=MsgRole.SELLER,
        last_msg_at=base - timedelta(hours=1),
        buyer_quantity=200, floor_price=44.5, market_min=43.0,
        created_at=base - timedelta(hours=3), updated_at=base - timedelta(hours=1),
    ))
    _add_messages(store, s1_id, base - timedelta(hours=3), [
        (0, MsgRole.SYSTEM, "decision",
         "开场询价 · 首轮一次问全：单价、含税、运费、时效", {}, "send_inquiry"),
        (1, MsgRole.BUYER, "text", script1, {}, "send_inquiry"),
        (6, MsgRole.SELLER, "text",
         "老板在的，这款有现货，48 元一件，100 件起批，量大可以谈。",
         {"price": 48.0, "moq": 100, "stock": "in_stock"}, ""),
        (7, MsgRole.SYSTEM, "decision",
         "补问缺口 · 卖家只给了价和起订量 ｜ 还缺 是否含税、运费怎么算、多久能发货",
         {}, "send_followup"),
        (8, MsgRole.BUYER, "text",
         "好的，48 这个价含税吗？运费怎么算，几天能发？", {}, "send_followup"),
        (14, MsgRole.SELLER, "text",
         "含税的，这个价不含运费，运费看地区，一般省内 30 左右。今天下单 48 小时内发货。",
         {"tax": "included", "shipping": "excluded", "lead_time": 48}, ""),
        (15, MsgRole.SYSTEM, "decision",
         "开口议价 · 缺口已清零，别家同款报到 43，拿这条压价", {}, "send_bargain"),
        (16, MsgRole.BUYER, "text",
         "老板，同款别家做到 43 了，我这单 200 件，44.5 能不能做？", {}, "send_bargain"),
        (22, MsgRole.SELLER, "text",
         "44.5 可以，再低真做不了，我们也是实价了。",
         {"price": 44.5, "tax": "included", "shipping": "excluded",
          "lead_time": 48, "moq": 100}, ""),
        (23, MsgRole.SYSTEM, "decision",
         "收工 · 报价信息完整（价 / 含税 / 运费 / 时效），价格已到目标价", {}, "close"),
    ])
    store.save_quote(Quote(
        id=f"q-{s1_id}", target_style_id=ts.id, product_id=products[1].id,
        shop_id=shops[0].id, inquiry_id=inq1_id, source=QuoteSource.INQUIRY,
        price=44.5, moq=100, tax=TaxStatus.INCLUDED, includes_shipping=False,
        stock_status=StockStatus.IN_STOCK, lead_time_hours=48,
        tiers=[], condition_note="含税，运费到付（省内约 30 元），48 小时内发货",
        raw_text="44.5 可以，再低真做不了，我们也是实价了。",
        quoted_at=base - timedelta(hours=1),
    ))

    # ---- 会话 2：在谈（人工暂停，等对方回时效） ----
    s2_id = f"{PREFIX}session-2"
    inq2_id = f"{PREFIX}inq-2"
    script2 = "你好，这款 280g 纯棉卫衣 300 件什么价？含税和运费怎么算？"
    store.save_inquiry(InquiryTask(
        id=inq2_id, target_style_id=ts.id, product_id=products[2].id,
        shop_id=shops[1].id, script=script2, status=InquiryStatus.FOLLOWING_UP,
        buyer_quantity=300,
        created_at=base - timedelta(hours=2), updated_at=base - timedelta(minutes=18),
    ))
    store.save_session(InquirySession(
        id=s2_id, inquiry_id=inq2_id, target_style_id=ts.id,
        product_id=products[2].id, shop_id=shops[1].id,
        offer_url=products[2].url or "", channel=ChatChannel.CDP,
        stage=InquiryStage.INQUIRY, status=InquiryStatus.FOLLOWING_UP, round=2,
        gap=_gap_pairs("lead_time", "tier_price"),
        last_speaker=MsgRole.BUYER,
        last_msg_at=base - timedelta(minutes=18),
        # 暂停 + 远期重排：即使有人点了「恢复」，也不会立刻对真卖家发消息
        next_action_at=datetime.now() + timedelta(days=7),
        buyer_quantity=300, floor_price=42.0, market_min=44.5,
        paused=True,
        created_at=base - timedelta(hours=2), updated_at=base - timedelta(minutes=18),
    ))
    _add_messages(store, s2_id, base - timedelta(hours=2), [
        (0, MsgRole.SYSTEM, "decision",
         "开场询价 · 首轮一次问全：单价、含税、运费、时效、这个量的价", {}, "send_inquiry"),
        (1, MsgRole.BUYER, "text", script2, {}, "send_inquiry"),
        (9, MsgRole.SELLER, "text",
         "43 一件，含税的，100 件起，300 件的话算 40，运费另算。",
         {"price": 43.0, "moq": 100, "tax": "included", "shipping": "excluded",
          "tier_price": 40.0}, ""),
        (10, MsgRole.SYSTEM, "decision",
         "补问缺口 · 卖家给了阶梯价但没说时效 ｜ 还缺 多久能发货、拿这个量能做到什么价",
         {}, "send_followup"),
        (11, MsgRole.BUYER, "text",
         "40 是 300 件能做到的价对吧？那多久能发货？", {}, "send_followup"),
        (12, MsgRole.SYSTEM, "decision",
         "静观等待 · 卖家还在线但未回复，按退避 30 分钟后再看", {}, "wait"),
    ])
    store.save_quote(Quote(
        id=f"q-{s2_id}", target_style_id=ts.id, product_id=products[2].id,
        shop_id=shops[1].id, inquiry_id=inq2_id, source=QuoteSource.INQUIRY,
        price=43.0, moq=100, tax=TaxStatus.INCLUDED, includes_shipping=False,
        stock_status=StockStatus.IN_STOCK, lead_time_hours=None,
        tiers=[{"qty": 100, "price": 43.0}, {"qty": 300, "price": 40.0}],
        condition_note="含税，运费另算；300 件 40 元",
        raw_text="43 一件，含税的，100 件起，300 件的话算 40，运费另算。",
        quoted_at=base - timedelta(minutes=100),
    ))

    # ---- 会话 3：转人工（卖家引导站外交易） ----
    s3_id = f"{PREFIX}session-3"
    inq3_id = f"{PREFIX}inq-3"
    script3 = "你好，这款纯棉 280g 卫衣 100 件什么价？"
    store.save_inquiry(InquiryTask(
        id=inq3_id, target_style_id=ts.id, product_id=products[4].id,
        shop_id=shops[3].id, script=script3, status=InquiryStatus.ESCALATED,
        buyer_quantity=100,
        created_at=base - timedelta(hours=5), updated_at=base - timedelta(hours=4),
    ))
    store.save_session(InquirySession(
        id=s3_id, inquiry_id=inq3_id, target_style_id=ts.id,
        product_id=products[4].id, shop_id=shops[3].id,
        offer_url=products[4].url or "", channel=ChatChannel.CDP,
        stage=InquiryStage.INQUIRY, status=InquiryStatus.ESCALATED, round=1,
        gap=_gap_pairs("price", "moq", "tax", "shipping", "stock", "lead_time"),
        last_speaker=MsgRole.SELLER,
        last_msg_at=base - timedelta(hours=4), next_action_at=None,
        buyer_quantity=100, paused=True,
        error="卖家要求加微信走站外交易，按规则停止自动谈判并转人工",
        created_at=base - timedelta(hours=5), updated_at=base - timedelta(hours=4),
    ))
    _add_messages(store, s3_id, base - timedelta(hours=5), [
        (0, MsgRole.SYSTEM, "decision",
         "开场询价 · 首轮一次问全：单价、含税、运费、时效", {}, "send_inquiry"),
        (1, MsgRole.BUYER, "text", script3, {}, "send_inquiry"),
        (7, MsgRole.SELLER, "text",
         "这个你加我微信聊吧，微信号 hz****88，给你更低的价。", {}, ""),
        (8, MsgRole.SYSTEM, "decision",
         "转人工 · 卖家引导站外交易（微信 / 电话），平台外交易无保障，智能体不跟",
         {}, "escalate"),
    ])

    # ---- 会话 4：无回复自动关闭 ----
    s4_id = f"{PREFIX}session-4"
    inq4_id = f"{PREFIX}inq-4"
    script4 = "你好，这款纯棉 280g 落肩卫衣 100 件什么价？含税吗？"
    store.save_inquiry(InquiryTask(
        id=inq4_id, target_style_id=ts.id, product_id=products[3].id,
        shop_id=shops[2].id, script=script4, status=InquiryStatus.CLOSED,
        buyer_quantity=100,
        created_at=base - timedelta(days=3), updated_at=base - timedelta(days=1),
    ))
    store.save_session(InquirySession(
        id=s4_id, inquiry_id=inq4_id, target_style_id=ts.id,
        product_id=products[3].id, shop_id=shops[2].id,
        offer_url=products[3].url or "", channel=ChatChannel.CDP,
        stage=InquiryStage.INQUIRY, status=InquiryStatus.CLOSED, round=1,
        gap=_gap_pairs("price", "moq", "tax", "shipping", "stock", "lead_time"),
        last_speaker=MsgRole.BUYER,
        last_msg_at=base - timedelta(days=3), next_action_at=None,
        buyer_quantity=100,
        created_at=base - timedelta(days=3), updated_at=base - timedelta(days=1),
    ))
    _add_messages(store, s4_id, base - timedelta(days=3), [
        (0, MsgRole.SYSTEM, "decision",
         "开场询价 · 首轮一次问全：单价、含税、运费、时效", {}, "send_inquiry"),
        (1, MsgRole.BUYER, "text", script4, {}, "send_inquiry"),
        (1440, MsgRole.SYSTEM, "text",
         "超过 48 小时未收到回复，会话超时关闭（不重发，避免骚扰）", {}, ""),
    ])
    return ts.id


def _seed_target_b(store: Store, imgs: dict[str, str], base: datetime) -> str:
    ts = TargetStyle(image_paths=[imgs["target_b"]], **_TARGET_B)
    store.save_target_style(ts)

    shops: list[Shop] = []
    for i, (name, loc) in enumerate(_SHOPS_B, start=1):
        s = Shop(id=f"{PREFIX}shop-b{i}", name=name, location=loc)
        store.save_shop(s)
        shops.append(s)

    for idx, si, title, price, moq, tier, status, score, reason in _PRODUCTS_B:
        store.save_product(Product(
            id=f"{PREFIX}prod-b{idx}",
            target_style_id=ts.id,
            shop_id=shops[si].id,
            title=title,
            image_paths=[imgs[f"c{idx}"]],
            url=f"https://detail.1688.com/offer/{920000000000 + idx}.html",
            attributes={"材质": "液态硅胶", "适配型号": "AirPods Pro 2"},
            listed_price=price,
            moq=moq,
            match_status=status,
            match_tier=tier,
            match_score=score,
            match_reason=reason,
            matched_at=base - timedelta(hours=6) if tier else None,
            created_at=base - timedelta(hours=8),
        ))
    return ts.id


def _seed_agent_run(store: Store, target_id: str, base: datetime) -> None:
    store.save_agent_run(AgentRunRecord(
        id=f"{PREFIX}run-1",
        target_style_id=target_id,
        goal="帮我分析这个款哪家最便宜，给出采购建议",
        steps=[],
        final_answer=(
            "完全同款 4 家，已询价 3 家。最低为「广州锦承制衣有限公司」43.00 元/件"
            "（含税、运费另算、100 件起），但时效未确认；「诸暨市恒织针织厂」"
            "议价后 44.50 元、48 小时内发货且支持 200 件小单，综合履约更稳。"
            "参考价 45 元，两家均已低于参考价。建议以 43 元为价格锚点，"
            "对锦承补问发货时效后再定；等效替代的 240g 款（39 元）克重偏低，"
            "不适合作为同款替代。"
        ),
        finished=True,
        prompt_tokens=3184,
        completion_tokens=512,
        created_at=base - timedelta(minutes=50),
    ))


def cleanup(store: Store) -> None:
    """只删 demo- 前缀的数据，真实数据一条不动。"""
    removed = 0
    for t in store.list_target_styles():
        if t.id.startswith(PREFIX):
            store.delete_target_style(t.id)
            removed += 1
    # 会话表可能留下 demo 前缀但目标款已被手动删掉的孤儿
    store._conn.execute(
        "DELETE FROM inquiry_message WHERE session_id LIKE 'demo-%'")
    for table in ("inquiry_session", "inquiry", "quote", "product",
                  "agent_run", "agent_plan"):
        store._conn.execute(f"DELETE FROM {table} WHERE id LIKE 'demo-%'")
    # comparison 的主键是 target_style_id，不是 id
    store._conn.execute(
        "DELETE FROM comparison WHERE target_style_id LIKE 'demo-%'")
    store._conn.execute("DELETE FROM shop WHERE id LIKE 'demo-%'")
    store._conn.commit()
    if (store.get_setting(CURRENT_TARGET_KEY) or "").startswith(PREFIX):
        store.set_setting(CURRENT_TARGET_KEY, "")
    for p in UPLOAD_DIR.glob(f"{PREFIX}img-*.svg"):
        try:
            p.unlink()
        except OSError:
            pass
    print(f"已清理 demo 目标款 {removed} 个及关联数据")


def main() -> int:
    ap = argparse.ArgumentParser(description="写入演示数据（空库开箱可用）")
    ap.add_argument("--db", default=str(Path(os.getenv("APP_DB") or (ROOT / "data" / "app.db"))),
                    help="数据库路径，默认 data/app.db（或环境变量 APP_DB）")
    ap.add_argument("--reset", action="store_true", help="只删除 demo 数据")
    ap.add_argument("--force", action="store_true", help="已有 demo 数据时先清后写")
    ap.add_argument("--allow-existing", action="store_true",
                    help="目标库已有真实目标款时仍写入（会混进你的列表，慎用）")
    args = ap.parse_args()

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(db_path)

    if args.reset:
        cleanup(store)
        store.close()
        return 0

    targets = store.list_target_styles()
    demo_targets = [t for t in targets if t.id.startswith(PREFIX)]
    real_targets = [t for t in targets if not t.id.startswith(PREFIX)]

    if demo_targets and not args.force:
        print(f"库里已有 {len(demo_targets)} 个 demo 目标款。"
              f"用 --force 覆盖，或 --reset 清除。", file=sys.stderr)
        store.close()
        return 2
    if real_targets and not args.allow_existing:
        print(
            "目标库里已有真实数据，默认不写入，避免演示数据混进你的列表。\n"
            f"  当前库：{db_path}\n"
            "  · 想单独看演示：python -m scripts.seed_demo --db data/demo.db\n"
            "    然后 APP_DB=data/demo.db 启动服务\n"
            "  · 确认要混入同一库：加 --allow-existing",
            file=sys.stderr,
        )
        store.close()
        return 2

    if demo_targets:
        cleanup(store)

    base = datetime.now().replace(microsecond=0)
    imgs = _demo_images()

    tid_a = _seed_target_a(store, imgs, base)
    tid_b = _seed_target_b(store, imgs, base)
    _seed_agent_run(store, tid_a, base)

    target_a = store.get_target_style(tid_a)
    target_b = store.get_target_style(tid_b)
    assert target_a and target_b
    run_comparison(store, target_a, llm=_NoLLM(), force_refresh=True)
    run_comparison(store, target_b, llm=_NoLLM(), force_refresh=True)

    store.set_setting(CURRENT_TARGET_KEY, tid_a)

    n_prod = len(store.list_products(tid_a)) + len(store.list_products(tid_b))
    n_shop = len([s for s in store.list_shops() if s.id.startswith(PREFIX)])
    n_session = len(store.list_sessions(tid_a))
    n_quote = len(store.list_quotes(tid_a))
    store.close()

    print(f"演示数据已写入 {db_path}")
    print(f"  目标款 2 个 / 候选商品 {n_prod} 个 / demo 店铺 {n_shop} 家")
    print(f"  谈判会话 {n_session} 条 / 报价 {n_quote} 条 / 比价结论 2 份")
    print("  启动服务后打开 http://127.0.0.1:8000 即可看到完整界面")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
