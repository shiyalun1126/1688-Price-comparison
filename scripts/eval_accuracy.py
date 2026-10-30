"""视觉匹配准确率评估：跑标注集 → 混淆矩阵 + 各类精确率/召回率 + 准确率数字。

用法：
    py -3.12 -m scripts.eval_accuracy            # 全量标注集
    py -3.12 -m scripts.eval_accuracy --reviewed # 只跑 reviewed=true 的样本

诚实说明：
- reviewed=false 的样本由库内判定自动预填，对它们的"准确率"是回归基线
  （模型没变就该 100%），不是真实准确率。
- reviewed=true 的样本经人工核对，其准确率才是可信数字。
- 评估会真实调用视觉 API（约 1~2 分钟、消耗 token），结束时报告本次用量。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.llm.usage import USAGE, UsageCounter, cost_cny  # noqa: E402
from app.matcher.matcher import Matcher  # noqa: E402
from app.store.db import Store  # noqa: E402

LABELS_PATH = ROOT / "eval" / "labels.json"
LABEL_TO_TIER = {"exact": "exact", "equivalent": "equivalent", "different": "different"}
STATUS_TO_TIER = {
    "verified_same": "exact",
    "verified_equivalent": "equivalent",
    "rejected": "different",
}
TIERS = ["exact", "equivalent", "different", "no_image"]
TIER_CN = {"exact": "完全同款", "equivalent": "等效替代",
           "different": "不同款", "no_image": "无法判定(无图)"}


def predicted_tier(outcome) -> str:
    if outcome is None:
        return "no_image"
    return STATUS_TO_TIER.get(outcome.status.value, "no_image")


def prf(confusion: dict, cls: str) -> tuple[int, int, int]:
    tp = confusion[cls][cls]
    fp = sum(confusion[other][cls] for other in TIERS if other != cls)
    fn = sum(confusion[cls][other] for other in TIERS if other != cls)
    return tp, fp, fn


def _fmt_rate(num: int, den: int) -> str:
    return f"{num / den:.1%}" if den else "-"


def main() -> None:
    only_reviewed = "--reviewed" in sys.argv
    data = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
    labels = [r for r in data["labels"] if (r.get("reviewed") or not only_reviewed)]
    if not labels:
        print("没有符合条件的标注样本。")
        return

    store = Store(ROOT / "data" / "app.db")
    by_target: dict[str, list[dict]] = {}
    for row in labels:
        by_target.setdefault(row["target_id"], []).append(row)

    before = USAGE.snapshot()
    matcher = Matcher()
    judged: list[tuple[dict, object, str]] = []
    for tid, rows in by_target.items():
        t = store.get_target_style(tid)
        if not t:
            print(f"!! 目标款不存在，跳过 {tid}")
            continue
        prods = [store.get_product(r["product_id"]) for r in rows]
        prods = [p for p in prods if p is not None]
        cls = matcher.run(t, prods, max_workers=8)
        pred = {m.product_id: m for m in cls.exact + cls.equivalent + cls.rejected + cls.candidate}
        for r in rows:
            m = pred.get(r["product_id"])
            judged.append((r, m, predicted_tier(m)))
    usage = UsageCounter.diff(before, USAGE.snapshot())
    store.close()

    # ---- 混淆矩阵（行=人工标注，列=模型判定）----
    confusion = {a: {b: 0 for b in TIERS} for a in TIERS}
    for r, _, pt in judged:
        confusion[r["label"]][pt] += 1

    n = len(judged)
    agree = sum(confusion[c][c] for c in ("exact", "equivalent", "different"))
    reviewed = [j for j in judged if j[0].get("reviewed")]
    reviewed_agree = sum(1 for r, _, pt in reviewed if pt == r["label"])

    print("=" * 62)
    print(f"视觉匹配准确率评估（{'仅已复核样本' if only_reviewed else '全量标注集'}）")
    print("=" * 62)
    print(f"样本数：{n}（其中人工复核 {len(reviewed)} 条）")
    print()
    print(f"总体准确率：{agree}/{n} = {_fmt_rate(agree, n)}")
    if reviewed:
        print(f"已复核样本准确率（可信数字）：{reviewed_agree}/{len(reviewed)}"
              f" = {_fmt_rate(reviewed_agree, len(reviewed))}")
    print()
    print("混淆矩阵（行=人工标注，列=模型判定）：")
    header = "标注\\判定   " + "".join(f"{TIER_CN[t]:>10}" for t in TIERS)
    print(header)
    for a in TIERS:
        row_total = sum(confusion[a].values())
        if not row_total:
            continue
        cells = "".join(f"{confusion[a][b]:>12}" for b in TIERS)
        print(f"{TIER_CN[a]:<10}{cells}   合计{row_total}")
    print()
    print("各类指标：")
    for cls in ("exact", "equivalent", "different"):
        tp, fp, fn = prf(confusion, cls)
        precision = _fmt_rate(tp, tp + fp)
        recall = _fmt_rate(tp, tp + fn)
        print(f"  {TIER_CN[cls]}：精确率 {precision}（{tp}/{tp + fp}），"
              f"召回率 {recall}（{tp}/{tp + fn}）")
    print()
    cost = cost_cny(usage)
    total_tokens = usage["prompt_tokens"] + usage["completion_tokens"]
    cost_str = f"，约 ¥{cost:.3f}" if cost is not None else ""
    print(f"本次评估消耗：{total_tokens} tokens（{usage['requests']} 次调用）{cost_str}")

    # ---- 分歧明细（供修正标注或定位问题）----
    disagreements = [(r, pt, m) for r, m, pt in judged if pt != r["label"]]
    if disagreements:
        print(f"\n分歧明细（{len(disagreements)} 条，标注≠判定；"
              f"先怀疑标注错，再看判定理由）：")
        for r, pt, m in disagreements:
            reason = (m.reason if m else "")[:80]
            print(f"  [{r['target_name']}] {r['title'][:36]}")
            print(f"    标注 {r['label']} → 判定 {pt} | {reason}")
            print(f"    库内原判定理由：{r.get('note', '')[:80]}")
    print()
    if not reviewed:
        print("提示：当前标注全部是自动预填，数字只是回归基线。"
              "请在 eval/labels.json 里人工核对（reviewed 改 true）后重跑，"
              "用 --reviewed 拿真实准确率。")


if __name__ == "__main__":
    main()
