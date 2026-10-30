"""视觉匹配复测（品类自适应版）：在内存中重跑匹配（不写库），验证判定稳定性 + 跨品类负对照。

- 对所有「有图且有商品」的目标款重跑匹配（覆盖服装/数码等多品类），与库中已存结果对比
- 负对照：取两个不同品类的目标款图互比，必须判 rejected
- 翻转分类：危险翻转 = 已验证(same/equivalent) → rejected（漏判同款）；
  equivalent↔same 互为边界波动，允许
"""
from __future__ import annotations

import sys
from pathlib import Path

from app.matcher.matcher import Matcher
from app.matcher.vision import compare_images
from app.store.db import Store

ROOT = Path(__file__).resolve().parent.parent


def retest_target(store: Store, target) -> tuple[int, int, int, list[str]]:
    """重跑单个目标款，返回 (一致数, 翻转数, 危险翻转数, 翻转明细)。"""
    prods = store.list_products(target.id)
    with_img = [p for p in prods if p.image_paths]
    if not with_img:
        return 0, 0, 0, []

    cls = Matcher().run(target, prods, max_workers=8)
    new_results = {m.product_id: m for m in cls.exact + cls.equivalent + cls.rejected + cls.candidate}

    agree = flip = dangerous = 0
    flips: list[str] = []
    for p in prods:
        old = p.match_status.value
        if not p.image_paths:
            continue
        new = new_results.get(p.id)
        if not new:
            continue
        if new.status.value == old:
            agree += 1
        else:
            flip += 1
            if old in ("verified_same", "verified_equivalent") and new.status.value == "rejected":
                dangerous += 1
            flips.append(f"    {p.title[:30]}：{old} → {new.status.value}（{new.reason[:50]}）")
    return agree, flip, dangerous, flips


def main() -> bool:
    store = Store(ROOT / "data" / "app.db")
    targets = store.list_target_styles()
    ok = True
    tested = 0

    for t in targets:
        prods = store.list_products(t.id)
        with_img = sum(1 for p in prods if p.image_paths)
        if not with_img or not t.image_paths:
            print(f"「{t.name}」（{t.category or '未分类'}）：跳过（目标款无图或无带图商品）")
            continue
        tested += 1
        print(f"\n「{t.name}」（{t.category or '未分类'}）：重跑 {len(prods)} 个商品…")
        agree, flip, dangerous, flips = retest_target(store, t)
        print(f"  一致 {agree}，翻转 {flip}（含危险翻转 {dangerous}）")
        for f in flips:
            print(f)
        if dangerous > 0:
            ok = False

    # ---- 负对照：取两个不同品类目标款互比，必须判 different ----
    print("\n跨品类负对照：")
    with_target_img = [t for t in targets if t.image_paths]
    negative_ok = True
    for i in range(len(with_target_img)):
        for j in range(i + 1, len(with_target_img)):
            a, b = with_target_img[i], with_target_img[j]
            if a.category and b.category and a.category == b.category:
                continue
            r = compare_images(a.image_paths[0], b.image_paths[0], category=a.category)
            pair = f"{a.name}({a.category}) vs {b.name}({b.category})"
            print(f"  {pair} → tier={r.get('tier')} conf={r.get('confidence')}"
                  f" | {r.get('reason', '')[:60]}")
            if r.get("tier") != "different":
                negative_ok = False

    print(f"\n===== 视觉复测（品类自适应）：{'通过' if ok and negative_ok and tested else '存在问题'}"
          f"（测试 {tested} 个目标款，无危险翻转={ok}，跨品类负对照={'通过' if negative_ok else '失败'}）=====")
    store.close()
    return ok and negative_ok and tested > 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
