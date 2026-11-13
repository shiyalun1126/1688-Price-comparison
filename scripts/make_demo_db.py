"""造一个「演示库」：只有样例数据，没有真实店铺、真实报价、真实账号痕迹。

用途：把这一份库挂到独立实例上，给别人点着试。
生产库里有真实采购价和店铺名单，端给别人看既没必要也不专业；演示库
只有三家编出来的店，点坏了也伤不到任何东西。

用法：
    D:/miniconda3/python.exe scripts/make_demo_db.py            # 写到 data/_demo/app.db
    D:/miniconda3/python.exe scripts/make_demo_db.py --fresh     # 先删旧库再重建
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models.entities import Product, Shop, TargetStyle  # noqa: E402
from app.models.enums import MatchStatus, MatchTier  # noqa: E402
from app.store.db import Store  # noqa: E402

DEMO_DB = ROOT / "data" / "_demo" / "app.db"

# 编出来的三家店。名字刻意不指向任何真实店铺。
SHOPS = [
    ("demo-shop-a", "义乌 · 纯棉针织A厂", "浙江义乌"),
    ("demo-shop-b", "广州 · 落肩卫衣B制衣", "广东广州"),
    ("demo-shop-c", "杭州 · 四季青C档口", "浙江杭州"),
]

# 同一个款，三家报出不同的价与起订量——比价才有意义
PRODUCTS = [
    ("demo-prod-a", "demo-shop-a", "280g 纯棉落肩卫衣 藏青 宽松 毛圈底",
     46.0, 50, MatchTier.EXACT, 0.93, "版型、克重、毛圈底与目标款一致"),
    ("demo-prod-b", "demo-shop-b", "280g 纯棉落肩卫衣 藏青色 宽松套头",
     43.5, 100, MatchTier.EQUIVALENT, 0.78, "面料克重一致，下摆罗纹略窄，属等效替代"),
    ("demo-prod-c", "demo-shop-c", "280g 纯棉卫衣 宽松落肩 多色可选",
     49.0, 30, MatchTier.EXACT, 0.91, "同版货，现货色齐"),
]


def build(fresh: bool) -> Path:
    if fresh and DEMO_DB.exists():
        DEMO_DB.unlink()               # 只删本脚本自己造的这一份
    DEMO_DB.parent.mkdir(parents=True, exist_ok=True)
    store = Store(DEMO_DB)

    store.save_target_style(TargetStyle(
        id="demo-style",
        name="280g 纯棉落肩卫衣 · 藏青",
        description="280g 毛圈底纯棉，落肩宽松版型，藏青色，S-XXL。目标是把到手价压到 45 以内。",
        attributes={"面料": "纯棉", "克重": "280g", "版型": "落肩宽松", "颜色": "藏青"},
        reference_price=45.0,
        category="卫衣",
    ))
    for sid, name, loc in SHOPS:
        store.save_shop(Shop(id=sid, name=name, location=loc))

    now = datetime.now()
    for pid, shop_id, title, price, moq, tier, score, reason in PRODUCTS:
        store.save_product(Product(
            id=pid, target_style_id="demo-style", shop_id=shop_id, title=title,
            # 剧本通道不会真的打开这个链接，但要过 enqueue 的「必须有 1688 链接」校验
            url=f"https://detail.1688.com/offer/10000{abs(hash(pid)) % 1000000}.html",
            listed_price=price, moq=moq,
            attributes={"面料": "纯棉", "克重": "280g", "版型": "落肩宽松"},
            match_status=(MatchStatus.VERIFIED_SAME if tier == MatchTier.EXACT
                          else MatchStatus.VERIFIED_EQUIVALENT),
            match_tier=tier, match_score=score, match_reason=reason, matched_at=now,
        ))
    store.close()
    return DEMO_DB


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true", help="先删掉旧演示库再重建")
    a = ap.parse_args()
    p = build(a.fresh)
    print(f"演示库已生成：{p}")
    print("  1 个目标款（卫衣，参考价 45）+ 3 家样例店 + 3 个已核验候选")
    print("  起实例：INQUIRY_CHANNEL=mock INQUIRY_ALLOW_REAL=0 APP_DB=%s "
          "uvicorn app.main:app --port 8010" % p)
