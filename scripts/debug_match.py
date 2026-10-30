"""调试匹配为何秒返回空。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.matcher.matcher import Matcher
from app.store.db import Store

store = Store(Path(__file__).resolve().parent.parent / "data" / "app.db")

target = store.get_target_style("70fa3b2a20")
print("目标款:", target.name)
print("  图片:", target.image_paths)

products = store.list_products("70fa3b2a20")
print("商品数:", len(products))

with_img = [p for p in products if p.image_paths]
print("有图商品:", len(with_img))
if with_img:
    p = with_img[0]
    print("  示例:", p.title[:40])
    print("  图片:", p.image_paths[0])
    print("  图片存在:", Path(p.image_paths[0]).exists())

print("\n--- 手动跑匹配（只跑前2个，看是否真的调了模型）---")
import time
matcher = Matcher()
t0 = time.time()
for p in with_img[:2]:
    t1 = time.time()
    import app.matcher.vision as v
    try:
        r = v.compare_images(target.image_paths[0], p.image_paths[0])
        print(f"  {p.title[:30]} -> same={r.get('same')} tier={r.get('tier')} ({time.time()-t1:.1f}s)")
    except Exception as e:
        print(f"  {p.title[:30]} -> 错误 {type(e).__name__}: {str(e)[:120]}")
print(f"总耗时 {time.time()-t0:.1f}s")

store.close()