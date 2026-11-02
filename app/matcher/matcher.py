"""匹配引擎：双通道（图相似召回 + 属性核验）→ 分层判定。

设计要点（全品类通用，源自服装场景约束并已泛化）：
- 图像相似仅是召回信号，同图 ≠ 同款。
- 属性核验是判定「同版货」的关键（品类相关属性：面料/克重/版型、型号/代际等）。
- 分层：完全同款（verified_same）进 exact 列表；
  等效替代（verified_equivalent）进 equivalent 列表，并显式声明。
- 未核验的候选保持 candidate，不进入任何「同款」列表。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional

from app.models.entities import Product, TargetStyle
from app.models.enums import MatchStatus, MatchTier
from app.matcher.image import image_similarity
from app.matcher.vision import RECHECK_HIGH, RECHECK_LOW, compare_images, merge_recheck

# 属性核验器签名：返回 (是否同版货, 置信度 0~1, 核验依据文案)
AttributeVerifier = Callable[[TargetStyle, Product], tuple[bool, float, str]]

# 图相似召回默认阈值（pHash 汉明距离换算）；真实照片场景需实测校准
IMAGE_RECALL_THRESHOLD = 0.75


@dataclass
class MatchOutcome:
    """一次匹配核验的结果。"""

    product_id: str
    status: MatchStatus
    tier: Optional[MatchTier]
    confidence: float
    reason: str


@dataclass
class Classification:
    """按分层拆好的匹配结果。"""

    exact: list[MatchOutcome] = field(default_factory=list)
    equivalent: list[MatchOutcome] = field(default_factory=list)
    rejected: list[MatchOutcome] = field(default_factory=list)
    candidate: list[MatchOutcome] = field(default_factory=list)


def classify(matches: list[MatchOutcome]) -> Classification:
    """把核验结果按分层归桶。"""
    c = Classification()
    for m in matches:
        if m.status == MatchStatus.VERIFIED_SAME:
            m.tier = MatchTier.EXACT
            c.exact.append(m)
        elif m.status == MatchStatus.VERIFIED_EQUIVALENT:
            m.tier = MatchTier.EQUIVALENT
            c.equivalent.append(m)
        elif m.status == MatchStatus.REJECTED:
            c.rejected.append(m)
        else:
            c.candidate.append(m)
    return c


class Matcher:
    """基于可插拔核验器做双层匹配。

    主判定通道为多模态视觉比对（use_vision=True，需双方带图），
    属性核验作为兜底。pHash 已证实对服装场景区分度不足，仅保留召回/参考用。
    """

    def __init__(
        self,
        verifier: Optional[AttributeVerifier] = None,
        use_vision: bool = True,
        recheck_gray: bool = True,
    ):
        self._verifier = verifier or self._rules_verifier
        self._use_vision = use_vision
        self._recheck_gray = recheck_gray   # 低置信度时二次复核（多一次 LLM 调用，仅灰区商品触发）

    @staticmethod
    def _rules_verifier(target: TargetStyle, product: Product) -> tuple[bool, float, str]:
        """默认规则核验器：属性完全命中才判同版货（保守，适合无 LLM 阶段）。

        属性维度随品类而变（服装：面料/克重/版型；数码：型号/代际/规格），
        由目标款 attributes 驱动，核验器本身品类无关。
        更智能的核验应替换为 LLM（对比品类关键属性的语义）。
        """
        if not target.attributes:
            # 无结构化属性时，无法凭规则核验，标记为等效替代候选
            return False, 0.3, "目标款无结构化属性，暂无法核验同版货，视为等效替代候选"
        tkeys = {k: v.strip().lower() for k, v in target.attributes.items() if v}
        pkeys = {k: v.strip().lower() for k, v in product.attributes.items() if v}
        common = set(tkeys) & set(pkeys)
        if not common:
            return False, 0.0, "无共同属性维度，无法判定"
        agree = sum(1 for k in common if tkeys[k] == pkeys[k])
        ratio = agree / len(tkeys)
        if ratio >= 0.8:
            return True, ratio, f"属性一致度 {ratio:.0%}（{agree}/{len(tkeys)} 项命中）"
        return False, ratio, f"属性一致度不足 {ratio:.0%}，判为等效替代"

    @staticmethod
    def _resolve_img(p):
        """把图片路径解析为本地文件路径。

        兼容三种来源：绝对路径（UI/书签导入）、"/uploads/xxx"（auto_import 服务端落盘）。
        """
        from pathlib import Path

        s = str(p)
        if Path(s).is_absolute() or (len(s) > 1 and s[1] == ":"):
            return Path(s)
        root = Path(__file__).resolve().parents[2]   # 项目根
        return root / "data" / s.lstrip("/")

    def verify(self, target: TargetStyle, product: Product) -> MatchOutcome:
        """判定单个候选。

        通道优先级：视觉比对（多模态 LLM，最准） > 属性核验（规则兜底）。
        视觉判定 different 直接排除；否则结合置信度分层。
        """
        # 主通道：视觉比对（需双方都有图且文件存在）
        t_img = self._resolve_img(target.image_paths[0]) if target.image_paths else None
        p_img = self._resolve_img(product.image_paths[0]) if product.image_paths else None
        if self._use_vision and t_img and p_img:
            from pathlib import Path

            if not t_img.exists() or not p_img.exists():
                return MatchOutcome(
                    product.id, MatchStatus.CANDIDATE, None, 0.0,
                    "图片文件缺失，无法视觉比对（请重新上传图片）",
                )
            try:
                r = compare_images(str(t_img), str(p_img), category=target.category)
                conf = float(r.get("confidence", 0.5) or 0)
                # 低置信度灰区 → 换独立视角二次复核，收敛边界案例抖动；
                # 复核本身失败不影响首判结果
                if self._recheck_gray and RECHECK_LOW <= conf <= RECHECK_HIGH:
                    try:
                        r2 = compare_images(str(t_img), str(p_img), category=target.category, recheck=True)
                        r = merge_recheck(r, r2)
                    except Exception:
                        pass
                tier = r.get("tier", "different")
                conf = float(r.get("confidence", 0.5))
                reason = r.get("reason", "")
                if r.get("key_diff"):
                    reason += f"（差异：{r['key_diff']}）"
                if tier == "different" or not r.get("same"):
                    return MatchOutcome(product.id, MatchStatus.REJECTED, None, conf,
                                        f"[视觉] {reason}")
                status = (MatchStatus.VERIFIED_SAME if tier == "exact"
                          else MatchStatus.VERIFIED_EQUIVALENT)
                return MatchOutcome(product.id, status, None, conf, f"[视觉] {reason}")
            except Exception as e:  # 视觉调用失败 → 退回属性核验
                fallback_note = f"视觉比对失败({type(e).__name__})，改用属性核验"
                is_same, conf, reason = self._verifier(target, product)
                status = (MatchStatus.VERIFIED_SAME if is_same else
                          MatchStatus.REJECTED if conf <= 0 else MatchStatus.VERIFIED_EQUIVALENT)
                return MatchOutcome(product.id, status, None, conf,
                                    f"{fallback_note}：{reason}")

        # 兜底通道：属性核验
        if not t_img or not p_img:
            # 缺图时无法视觉判定；若无属性也无法判定，标记为待核验而非排除
            if not target.attributes or not product.attributes:
                return MatchOutcome(
                    product.id, MatchStatus.CANDIDATE, None, 0.0,
                    "缺少图片或属性，无法判定同款（请补充图片或属性）",
                )
        is_same, conf, reason = self._verifier(target, product)
        if is_same:
            status = MatchStatus.VERIFIED_SAME
        elif conf <= 0.0:
            status = MatchStatus.REJECTED
        else:
            status = MatchStatus.VERIFIED_EQUIVALENT
        return MatchOutcome(
            product_id=product.id, status=status,
            tier=None, confidence=conf, reason=reason,
        )

    def image_scores(self, target: TargetStyle, products: list[Product]) -> dict[str, float]:
        """计算各商品与目标款的图相似度（用于召回与参考信号）。

        目标款取第一张图；候选商品取第一张图。无图时返回 0.0。
        """
        t_img = target.image_paths[0] if target.image_paths else None
        scores: dict[str, float] = {}
        for p in products:
            p_img = p.image_paths[0] if p.image_paths else None
            if t_img and p_img:
                scores[p.id] = image_similarity(t_img, p_img)
            else:
                scores[p.id] = 0.0
        return scores

    def recall(
        self,
        target: TargetStyle,
        products: list[Product],
        threshold: float = IMAGE_RECALL_THRESHOLD,
    ) -> list[Product]:
        """图相似召回：筛出图相似度 >= 阈值的候选。

        若目标款或候选无图，返回 None，表示「无法用图召回」，由调用方决定是否跳过。
        """
        if not target.image_paths:
            return products  # 无目标图，不筛，全部保留
        return [p for p in products if self.image_scores(target, [p]).get(p.id, 0.0) >= threshold]

    def run(
        self,
        target: TargetStyle,
        products: list[Product],
        use_image_recall: bool = False,
        image_threshold: float = IMAGE_RECALL_THRESHOLD,
        max_workers: int = 8,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> Classification:
        """执行匹配。

        默认仅属性核验；可选开启图相似召回（以图找款）。
        视觉比对为网络 IO，采用线程池并发（默认 8 并发），
        相比串行可提速数倍；progress_cb(已完成数, 总数) 用于上报进度。
        """
        pool = self.recall(target, products, image_threshold) if use_image_recall else products
        total = len(pool)
        if total == 0:
            return classify([])

        outcomes: list[MatchOutcome] = []
        done = 0

        # 图片齐全且启用视觉时才值得并发；否则逐个走规则核验即可（纯本地，很快）
        need_vision = self._use_vision and any(
            target.image_paths and p.image_paths for p in pool
        )

        if need_vision and max_workers > 1 and total > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = {ex.submit(self.verify, target, p): p for p in pool}
                for fut in as_completed(futures):
                    p = futures[fut]
                    try:
                        outcomes.append(fut.result())
                    except Exception as e:  # 单个失败不影响整体
                        outcomes.append(MatchOutcome(
                            p.id, MatchStatus.CANDIDATE, None, 0.0,
                            f"匹配异常：{type(e).__name__}",
                        ))
                    done += 1
                    if progress_cb:
                        progress_cb(done, total)
        else:
            for p in pool:
                outcomes.append(self.verify(target, p))
                done += 1
                if progress_cb:
                    progress_cb(done, total)

        return classify(outcomes)