"""重算历史上「视觉比对失败→误判为已排除/等效」的商品结论。

背景：视觉调用因网关限流失败时，旧逻辑拿属性核验的默认分（0.0/0.3）定了状态，
这些结论被写库并被增量模式永久复用，污染了比价榜单。修完降级语义后需要重算。

用法：
    python scripts/debug/recompute_vision_fallback.py          # 演练（库副本）
    python scripts/debug/recompute_vision_fallback.py --apply  # 写真实库
"""
from __future__ import annotations

import shutil
import sys
from collections import Counter
from pathlib import Path

from app.matcher.matcher import Matcher
from app.store.db import Store

ROOT = Path(__file__).resolve().parents[2]
REAL_DB = ROOT / "data" / "app.db"
MARK = "视觉比对失败"


def _is_polluted(p) -> bool:
    """历史上因视觉失败而拿到默认分的那批商品。"""
    if MARK not in (p.match_reason or ""):
        return False
    # 新的降级语义会写 candidate + 「未完成核验」，不需要重算
    return "未完成核验" not in (p.match_reason or "")


def main(apply: bool) -> None:
    db = REAL_DB if apply else ROOT / "scripts" / "debug" / "_recompute_dry" / "app.db"
    if not apply:
        db.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REAL_DB, db)
        print(f"[演练模式] 使用库副本：{db}")
    else:
        print(f"[写入模式] 直接改真实库：{db}")

    store = Store(db)
    try:
        total_before = Counter()
        total_after = Counter()
        changed = 0
        for t in store.list_target_styles():
            prods = [p for p in store.list_products(t.id) if p.image_paths]
            dirty = [p for p in prods if _is_polluted(p)]
            if not dirty:
                continue
            print(f"\n=== {t.name}（{t.category}）：待重算 {len(dirty)} 条")
            old = {p.id: (p.match_status.value, p.match_score) for p in dirty}
            for v, _ in old.values():
                total_before[v] += 1
            cls = Matcher().run(t, dirty)   # 并发走 LLM_VISION_CONCURRENCY
            outcomes = cls.exact + cls.equivalent + cls.rejected + cls.candidate
            now = __import__("datetime").datetime.now()
            for m in outcomes:
                p = next(x for x in dirty if x.id == m.product_id)
                ob, oc = old[m.product_id]
                total_after[m.status.value] += 1
                if m.status.value != ob:
                    changed += 1
                print(f"  {p.title[:26]:<28} {ob}({oc}) → {m.status.value}({m.confidence:.2f})")
                if apply:
                    p.match_status = m.status
                    p.match_tier = m.tier
                    p.match_score = m.confidence
                    p.match_reason = m.reason
                    p.matched_at = now
                    store.save_product(p)
        print("\n=== 汇总")
        print("  重算前状态:", dict(total_before))
        print("  重算后状态:", dict(total_after))
        print(f"  状态发生变化: {changed} 条")
        if not apply:
            print("  （演练，未写入真实库；加 --apply 生效）")
    finally:
        store.close()


if __name__ == "__main__":
    from app.config import load_dotenv  # noqa: F401  确保读 .env
    main("--apply" in sys.argv)
