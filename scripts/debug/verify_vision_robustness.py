"""修复效果验证：在同一目标款上跑高并发，确认不再出现「视觉失败→假结论」。

对比三个维度：
1. 默认并发（LLM_VISION_CONCURRENCY，默认 4）下是否 0 回退
2. 是否还有 candidate 被误当结论（降级语义）
3. 显式拉到 8 并发时，靠 Retry-After 重试能否顶住（不再整体失败）
"""
from __future__ import annotations

import time
from pathlib import Path

from app.matcher import matcher as matcher_module
from app.matcher.matcher import Matcher
from app.store.db import Store

store = Store(Path(__file__).resolve().parents[2] / "data" / "app.db")
try:
    t = [x for x in store.list_target_styles() if x.name == "airpods"][0]
    prods = [p for p in store.list_products(t.id) if p.image_paths]
    print(f"目标款 {t.name}（{t.category}）商品 {len(prods)}")
    print(f"默认并发 = {matcher_module.vision_concurrency()}")

    for workers in (matcher_module.vision_concurrency(), 8):
        t0 = time.time()
        cls = Matcher().run(t, prods, max_workers=workers)
        el = time.time() - t0
        allm = cls.exact + cls.equivalent + cls.rejected + cls.candidate
        failed = [m for m in allm if "视觉比对失败" in m.reason]
        real = [m for m in allm if m.reason.startswith("[视觉]")]
        print(f"\n-- workers={workers}: {el:.1f}s")
        print(f"   有效视觉结论 {len(real)}/{len(allm)}，失败回退 {len(failed)}")
        print(f"   exact={len(cls.exact)} equiv={len(cls.equivalent)} "
              f"rejected={len(cls.rejected)} candidate={len(cls.candidate)}")
        for m in failed:
            print(f"   ! {m.status.value} {m.confidence:.2f} {m.reason[:80]}")
finally:
    store.close()
