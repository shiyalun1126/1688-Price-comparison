"""视觉匹配复测（视觉 API 调用较多，标记 slow）：判定稳定性 + 跨品类负对照。

直接在真实库上只读跑（匹配结果不写库），与已存结果逐条对比。

注意：视觉模型（temperature=0.1）对个别边界商品存在判定抖动（如多色合集展示的
耳机在 equivalent/rejected 间摇摆），属固有噪声。因此断言允许至多 1 个危险翻转、
一致率不低于 70%——用于拦截系统性回归（如提示词改坏导致大面积误判），
而不是要求逐位一致。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.matcher.matcher import Matcher
from app.matcher.vision import compare_images
from app.store.db import Store

pytestmark = pytest.mark.slow

REAL_DB = Path(__file__).resolve().parent.parent / "data" / "app.db"

MAX_DANGEROUS_FLIPS = 1   # 容忍的边界抖动的「同款→不同」上限
MIN_AGREEMENT = 0.70      # 一致率下限


def _retest(store, target) -> tuple[int, int, int, list[str]]:
    """返回 (一致数, 翻转数, 危险翻转数, 翻转明细)。"""
    prods = store.list_products(target.id)
    if not any(p.image_paths for p in prods):
        return 0, 0, 0, []
    cls = Matcher().run(target, prods, max_workers=8)
    new = {m.product_id: m for m in cls.exact + cls.equivalent + cls.rejected + cls.candidate}
    agree = flip = dangerous = 0
    details: list[str] = []
    for p in prods:
        if not p.image_paths:
            continue
        m = new.get(p.id)
        if not m:
            continue
        if m.status.value == p.match_status.value:
            agree += 1
        else:
            flip += 1
            mark = ""
            if p.match_status.value in ("verified_same", "verified_equivalent") \
                    and m.status.value == "rejected":
                dangerous += 1
                mark = "⚠️危险 "
            details.append(f"{mark}{p.title[:30]}：{p.match_status.value} → {m.status.value}")
    return agree, flip, dangerous, details


def test_vision_stability_and_cross_category():
    store = Store(REAL_DB)
    try:
        targets = [t for t in store.list_target_styles()
                   if t.image_paths and any(p.image_paths for p in store.list_products(t.id))]
        assert targets, "没有可测的目标款"
        for t in targets:
            agree, flip, dangerous, details = _retest(store, t)
            total = agree + flip
            rate = agree / total if total else 1.0
            print(f"\n「{t.name}」（{t.category}）：一致 {agree}/{total}"
                  f"（{rate:.0%}），危险翻转 {dangerous}")
            for d in details:
                print(f"  {d}")
            assert dangerous <= MAX_DANGEROUS_FLIPS, \
                f"「{t.name}」危险翻转 {dangerous} 个（>{MAX_DANGEROUS_FLIPS}）：{details}"
            assert rate >= MIN_AGREEMENT, \
                f"「{t.name}」一致率 {rate:.0%} 低于 {MIN_AGREEMENT:.0%}：{details}"

        # 跨品类负对照：不同品类目标款互比必须判 different
        with_img = [t for t in store.list_target_styles() if t.image_paths]
        pairs = [(a, b) for i, a in enumerate(with_img) for b in with_img[i + 1:]
                 if not (a.category and a.category == b.category)]
        assert pairs, "没有跨品类目标款对"
        for a, b in pairs:
            r = compare_images(a.image_paths[0], b.image_paths[0], category=a.category)
            assert r.get("tier") == "different", \
                f"跨品类负对照失败: {a.name} vs {b.name} → {r.get('tier')}"
    finally:
        store.close()
