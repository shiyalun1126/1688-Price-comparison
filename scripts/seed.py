"""种子数据脚本：写入示例目标款/门店/商品，便于本地跑通链路。

用法：python -m scripts.seed
"""
from __future__ import annotations

import uuid
from pathlib import Path

from app.models.entities import Product, Shop, TargetStyle
from app.store.db import Store

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def main() -> None:
    store = Store(DATA_DIR / "app.db")

    ts = TargetStyle(
        id="seed-style-1",
        name="280g 纯棉落肩卫衣-藏青",
        description="纯棉 280g 落肩宽松版型，藏青色，S-XXL",
        attributes={"面料": "纯棉", "克重": "280g", "版型": "落肩宽松"},
        reference_price=45.0,
        category="卫衣",
    )
    store.save_target_style(ts)

    shops = [
        Shop(id="seed-shop-1", name="义乌A服饰", location="浙江义乌"),
        Shop(id="seed-shop-2", name="广州B制衣", location="广东广州"),
        Shop(id="seed-shop-3", name="杭州C档口", location="浙江杭州"),
    ]
    for s in shops:
        store.save_shop(s)

    products = [
        Product(id="seed-prod-1", target_style_id=ts.id, shop_id="seed-shop-1",
                title="280g 纯棉落肩卫衣 藏青 宽松", listed_price=48.0, moq=50,
                attributes={"面料": "纯棉", "克重": "280g", "版型": "落肩宽松"}),
        Product(id="seed-prod-2", target_style_id=ts.id, shop_id="seed-shop-2",
                title="重磅 280克 落肩卫衣 藏蓝", listed_price=43.0, moq=100,
                attributes={"面料": "纯棉", "克重": "280g", "版型": "落肩"}),
        Product(id="seed-prod-3", target_style_id=ts.id, shop_id="seed-shop-3",
                title="卫衣 宽松 藏青 连帽", listed_price=39.0, moq=20,
                attributes={"面料": "涤棉", "克重": "240g"}),
    ]
    for p in products:
        store.save_product(p)

    print("种子数据已写入:", DATA_DIR / "app.db")
    print("目标款 id:", ts.id)
    store.close()


if __name__ == "__main__":
    main()