"""视觉匹配复测：在内存中重跑匹配（不写库），验证判定稳定性 + 跨品类负对照。

- 取 POLO衫 目标款及其 20 个真实候选商品（均已带历史匹配结果）
- 用当前匹配引擎重跑，与库中已存结果逐条对比
- 负对照：POLO 目标款图 vs airpods 目标款图，必须判 rejected
"""
from __future__ import annotations

import sys
from pathlib import Path

from app.matcher.matcher import Matcher
from app.matcher.vision import compare_images
from app.store.db import Store

ROOT = Path(__file__).resolve().parent.parent


def main() -> bool:
    store = Store(ROOT / "data" / "app.db")
    targets = {t.name: t for t in store.list_target_styles()}
    polo = next((t for n, t in targets.items() if "POLO" in n), None)
    airpods = next((t for n, t in targets.items() if "airpods" in n.lower()), None)
    if not polo:
        print("找不到 POLO衫 目标款")
        return False

    prods = store.list_products(polo.id)
    with_img = [p for p in prods if p.image_paths]
    print(f"目标款「{polo.name}」：{len(prods)} 个商品，{len(with_img)} 个有图")
    if not with_img:
        print("没有带图商品，无法复测")
        return False

    # ---- 重跑匹配（纯内存，不写库） ----
    print("重跑视觉匹配（8 并发）…")
    cls = Matcher().run(polo, prods, max_workers=8)
    new_results = {m.product_id: m for m in cls.exact + cls.equivalent + cls.rejected + cls.candidate}

    agree = flip = skipped = 0
    flips: list[str] = []
    dangerous = 0   # 已验证(same/equivalent) → rejected：漏判同款，危险方向
    for p in prods:
        old = p.match_status.value
        if not p.image_paths:
            skipped += 1
            continue
        new = new_results.get(p.id)
        if not new:
            skipped += 1
            continue
        if new.status.value == old:
            agree += 1
        else:
            flip += 1
            if old in ("verified_same", "verified_equivalent") and new.status.value == "rejected":
                dangerous += 1
            flips.append(f"  {p.title[:30]}：{old} → {new.status.value}（{new.reason[:50]}）")

    print(f"\n重跑 {agree + flip} 条：一致 {agree}，翻转 {flip}，跳过 {skipped}")
    for f in flips:
        print(f)

    # ---- 负对照：跨品类必须判拒 ----
    print("\n负对照：POLO 目标款 vs airpods 目标款")
    if airpods and airpods.image_paths and polo.image_paths:
        r = compare_images(polo.image_paths[0], airpods.image_paths[0])
        print(f"  判定：same={r.get('same')} tier={r.get('tier')} conf={r.get('confidence')}")
        print(f"  理由：{r.get('reason', '')[:100]}")
        negative_ok = r.get("tier") == "different"
    else:
        print("  airpods 目标款缺图，跳过")
        negative_ok = True

    total = agree + flip
    # 通过标准：无「危险翻转」（已判同款/等效被误判为不同）；边界波动（equivalent↔same）允许
    ok = dangerous == 0 and negative_ok
    print(f"\n===== 视觉复测：{'通过' if ok else '存在问题'}"
          f"（一致 {agree}/{total}，翻转 {flip} 中含危险翻转 {dangerous}，"
          f"负对照 {'通过' if negative_ok else '失败'}）=====")
    store.close()
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
