"""端到端：真实图片走 目标款→候选→视觉匹配 全链路。"""
from __future__ import annotations

import uuid
from pathlib import Path

from app.matcher.matcher import Matcher
from app.models.entities import Product, Shop, TargetStyle
from app.store.db import Store

SRC = Path(__file__).resolve().parent.parent / "素材"
DATA = Path(__file__).resolve().parent.parent / "data"

imgs = {k: str(SRC / v) for k, v in {
    "A": "1001bd0b244611075a0ad3e28def4f29~tplv-be4g95zd3a-448x448.jpeg",
    "B": "419fcf1e8087c63340668cb4c77b7e54~tplv-be4g95zd3a-448x448.jpeg",
    "C": "jpeg_m_51340b671d15c51817f1063dd8331bca_sx_149442_www1200-1200~tplv-be4g95zd3a-448x448.jpeg",
    "D": "jpeg_m_72907ffe91411a20e04beab7226252c5_sx_1008443_www800-800~tplv-be4g95zd3a-448x448.jpeg",
    "E": "jpeg_m_9ff592da080cf12ad7aefa05773494bb_sx_120420_www800-800~tplv-be4g95zd3a-448x448.jpeg",
    "F": "XIudEOhG_m_58c308838291322c1f614a54fe6c5104_sx_313931_www1200-1200~tplv-be4g95zd3a-448x448.jpeg",
}.items()}

store = Store(DATA / "app.db")
tid = "real-style-" + uuid.uuid4().hex[:6]

target = TargetStyle(
    id=tid, name="黑白细格纹衬衫（目标款A）", category="衬衫",
    image_paths=[imgs["A"]],
    attributes={"品类": "格纹衬衫", "颜色": "黑白"},
)
store.save_target_style(target)

names = {"B": "绿黑格衬衫", "C": "黑灰大格衬衫", "D": "白底窗格衬衫",
         "E": "红黑格衬衫", "F": "黑白大格衬衫"}
prods = []
for k, title in names.items():
    shop = Shop(id=f"{tid}-shop-{k}", name=f"店铺{k}")
    store.save_shop(shop)
    p = Product(id=f"{tid}-p-{k}", target_style_id=tid, shop_id=shop.id,
                title=title, image_paths=[imgs[k]])
    store.save_product(p)
    prods.append(p)

print(f"目标款：{target.name}（图A）")
print("候选：B/C/D/E/F\n正在调用视觉模型逐一比对……\n")

cls = Matcher().run(target, prods)

print(f"✅ 完全同款 ({len(cls.exact)}):", [m.product_id.split('-')[-1] for m in cls.exact])
print(f"⚠️ 等效替代 ({len(cls.equivalent)}):", [m.product_id.split('-')[-1] for m in cls.equivalent])
print(f"❌ 已排除 ({len(cls.rejected)}):", [m.product_id.split('-')[-1] for m in cls.rejected])
print("\n判定理由：")
for m in cls.exact + cls.equivalent + cls.rejected + cls.candidate:
    k = m.product_id.split('-')[-1]
    print(f"  [{k}] {m.status.value:20s} {m.reason}")

store.close()