"""把比价业务能力包装成 Agent 可调用的工具集。"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx

from app.agent.react import ToolSpec
from app.comparison.comparison import run_comparison
from app.inquire.inquiry import build_inquiry_script
from app.llm.client import LLMClient
from app.matcher.matcher import Matcher
from app.models.entities import Product, Shop, TargetStyle
from app.models.enums import MatchStatus
from app.store.db import Store

# 1688 详情页 offer id 提取（detail.1688.com/offer/<id>.html）
_OFFER_ID_RE = re.compile(r"offer/(\d+)")
_FETCH_TIMEOUT = 10.0
_FETCH_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def build_tools(store: Store, current_target_id: str | None = None) -> list[ToolSpec]:
    """构建工具集，绑定到具体的数据存储。current_target_id 为会话选中的目标款。"""

    def _current_target() -> TargetStyle | None:
        if not current_target_id:
            return None
        return store.get_target_style(current_target_id)

    def list_targets(limit: int = 10) -> dict:
        """列出已有的目标款及商品数。"""
        out = []
        for t in store.list_target_styles()[:limit]:
            prods = store.list_products(t.id)
            with_img = sum(1 for p in prods if p.image_paths)
            with_price = sum(1 for p in prods if p.listed_price is not None)
            out.append({
                "target_id": t.id,
                "name": t.name,
                "category": t.category,
                "商品数": len(prods),
                "有图商品": with_img,
                "有价商品": with_price,
                "参考价": t.reference_price,
            })
        return {"targets": out, "count": len(out)}

    def inspect_target(target_id: str) -> dict:
        """查看某个目标款的详细信息（图片、属性、商品概况）。"""
        t = store.get_target_style(target_id)
        if not t:
            return {"error": "目标款不存在"}
        prods = store.list_products(target_id)
        status_count: dict[str, int] = {}
        for p in prods:
            k = p.match_status.value
            status_count[k] = status_count.get(k, 0) + 1
        return {
            "target_id": t.id,
            "name": t.name,
            "category": t.category,
            "description": t.description,
            "attributes": t.attributes,
            "参考价": t.reference_price,
            "商品总数": len(prods),
            "匹配状态分布": status_count,
            "样例商品": [
                {
                    "id": p.id,
                    "title": p.title[:40],
                    "标价": p.listed_price,
                    "有图": bool(p.image_paths),
                }
                for p in prods[:5]
            ],
        }

    def match_products(target_id: str, force: bool = False) -> dict:
        """对目标款的候选商品执行视觉匹配。

        默认增量：已判定且图片未变的商品直接复用结果，不重复烧视觉调用；
        force=True 时全量重跑。无图商品每次都会走规则核验（本地、很快）。
        """
        t = store.get_target_style(target_id)
        if not t:
            return {"error": "目标款不存在"}
        prods = store.list_products(target_id)
        if not prods:
            return {"error": "该目标款下没有候选商品，请先导入"}
        if force:
            to_match = list(prods)
        else:
            # 与 API 路由的增量口径一致：有图 + 已判定 + 匹配时间存在 → 复用
            to_match = [
                p for p in prods
                if not (p.image_paths and p.match_status != MatchStatus.CANDIDATE
                        and p.matched_at is not None)
            ]
        if not to_match:
            return {
                "本次新匹配": 0,
                "已复用": len(prods),
                "提示": "所有候选均已核验过（增量模式），可直接 compare_prices 出比价结论。",
            }
        matcher = Matcher()
        cls = matcher.run(t, to_match, max_workers=8)
        # 回写
        from datetime import datetime
        pmap = {p.id: p for p in to_match}
        now = datetime.now()
        for m in cls.exact + cls.equivalent + cls.rejected + cls.candidate:
            p = pmap.get(m.product_id)
            if p:
                p.match_status = m.status
                p.match_tier = m.tier
                p.match_score = m.confidence
                p.match_reason = m.reason
                p.matched_at = now
                store.save_product(p)
        return {
            "本次新匹配": len(to_match),
            "已复用": len(prods) - len(to_match),
            "完全同款": len(cls.exact),
            "等效替代": len(cls.equivalent),
            "已排除": len(cls.rejected),
            "仍未核验": len(cls.candidate),
            "下一步": "匹配完成，可调用 compare_prices 生成比价结论。",
        }

    def compare_prices(target_id: str) -> dict:
        """生成比价结论：价格排序、建议、议价话术。"""
        t = store.get_target_style(target_id)
        if not t:
            return {"error": "目标款不存在"}
        r = run_comparison(store, t)
        rows = r.rows or []
        return {
            "综述": r.summary,
            "建议": r.recommendation,
            "报价条数": len(rows),
            "价格前5": [
                {
                    "店铺": x.get("shop_name"),
                    "价格": x.get("price"),
                    "来源": x.get("price_source"),
                    "匹配": x.get("tier"),
                    "商品": (x.get("title") or "")[:30],
                }
                for x in rows[:5]
            ],
            "议价话术": r.negotiation_script[:300] if r.negotiation_script else "",
        }

    def inquiry_script(target_id: str, product_id: str, quantity: int = 100) -> dict:
        """为某商品生成询价话术。"""
        t = store.get_target_style(target_id)
        if not t:
            return {"error": "目标款不存在"}
        prods = store.list_products(target_id)
        p = next((x for x in prods if x.id == product_id), None)
        if not p:
            return {"error": "商品不存在"}
        script = build_inquiry_script(t, p, quantity)
        return {"话术": script, "商品": p.title[:40], "单价": p.listed_price}

    def make_search_link(extra_keywords: str = "") -> dict:
        """生成 1688 搜索链接，指引用户去搜索并回填候选商品（副驾模式）。"""
        t = _current_target()
        if not t:
            return {"error": "缺少当前目标款"}
        kws = " ".join(x for x in [t.name, extra_keywords or ""] if x.strip())
        # 把关键属性值也拼进去，提高召回精度（取前 3 个非空值）
        attr_vals = [v for v in (t.attributes or {}).values() if v and len(v) <= 12][:3]
        full_kws = " ".join([kws] + attr_vals).strip()
        url = f"https://s.1688.com/selloffer/offer_search.htm?keywords={quote(full_kws.encode('gbk'))}"
        return {
            "搜索链接": url,
            "关键词": full_kws,
            "使用方法": (
                "1. 把链接发给用户在浏览器打开；2. 用户挑中候选商品后，"
                "用书签脚本一键导入（看板「导入」标签页有书签），"
                "或把商品详情页链接贴回来给你；3. 收到链接后调用 import_products 入库。"
            ),
        }

    def _fetch_offer(url: str) -> dict:
        """轻量抓取 1688 详情页标题/价格；被反爬或解析失败返回空结果。"""
        out = {"title": None, "price": None, "fetched": False}
        try:
            resp = httpx.get(
                url, headers={"User-Agent": _FETCH_UA},
                timeout=_FETCH_TIMEOUT, follow_redirects=True,
            )
            html = resp.text
        except Exception:
            return out
        m = re.search(r"<title>([^<]+)</title>", html, re.I)
        if not m:
            return out
        title = re.sub(r"\s*[-_—]\s*(阿里巴巴|1688|1688\.com).*$", "", m.group(1)).strip()
        # 反爬页特征：验证/滑块/登录
        if not title or any(k in title for k in ("验证", "滑块", "登录", "访问异常")):
            return out
        out["title"], out["fetched"] = title[:80], True
        # 价格：尝试页面内常见的几种价格字面量
        for pat in (r'"price"\s*:\s*"?([\d.]+)', r"[¥￥]\s*([\d.]+)", r'"unitPrice[^"]*"\s*:\s*"?([\d.]+)'):
            pm = re.search(pat, html)
            if pm:
                try:
                    v = float(pm.group(1))
                    if 0.1 <= v <= 100000:
                        out["price"] = v
                        break
                except ValueError:
                    continue
        return out

    def import_products(items: list, target_id: str = "") -> dict:
        """把用户贴来的 1688 商品链接/信息入库为候选商品。

        items 元素可以是 URL 字符串，或 {url, title?, price?} 字典。
        服务端先尝试轻量抓取标题/价格；被反爬拦截时降级为「只存 URL 的
        待核验候选」，图片与准确价格等用户用书签脚本补全。
        """
        t = _current_target() if not target_id else store.get_target_style(target_id)
        if not t:
            return {"error": "缺少目标款（请先选定目标款或在参数里给 target_id）"}
        items = (items or [])[:10]   # 单次上限 10 条，防失控
        existing = {p.url for p in store.list_products(t.id) if p.url}
        # 导入商品统一挂在「链接导入」店铺下
        if not store.get_shop("imported"):
            store.save_shop(Shop(id="imported", name="链接导入"))
        imported, skipped, degraded = 0, 0, []
        for it in items:
            url = it.get("url", "").strip() if isinstance(it, dict) else str(it).strip()
            if not url:
                continue
            if url in existing:
                skipped += 1
                continue
            m = _OFFER_ID_RE.search(url)
            offer_id = m.group(1) if m else str(abs(hash(url)) % 10**10)
            title = (it.get("title") if isinstance(it, dict) else None) or ""
            price = (it.get("price") if isinstance(it, dict) else None) or None
            fetched = False
            if not title or not price:
                fr = _fetch_offer(url)
                fetched = fr["fetched"]
                title = title or fr["title"] or ""
                price = price or fr["price"]
            if not fetched and not title:
                degraded.append(url[:60])
                title = f"1688商品{offer_id}（待书签补全）"
            pid = f"imp-{offer_id}"
            if store.get_product(pid):
                skipped += 1
                continue
            store.save_product(Product(
                id=pid, target_style_id=t.id, shop_id="imported",
                title=title, url=url, listed_price=price,
                match_status=MatchStatus.CANDIDATE,
            ))
            existing.add(url)
            imported += 1
        return {
            "导入数": imported,
            "跳过(重复)": skipped,
            "自动抓到标题": imported - len(degraded),
            "待书签补全": degraded,
            "下一步": "导入完成。有图的商品可直接 match_products 做视觉匹配；"
                      "无图的（链接导入）需要用户用书签脚本重新导入以补全图片，否则只能按属性/价格粗筛。",
        }

    def plan_search(target_id: str = "", focus: str = "") -> dict:
        """用 LLM 分析目标款，生成多条高质量搜索关键词策略。

        1688 反爬严格，服务端无法直接搜索；本工具负责「智能决策搜什么」，
        返回可直接点击的搜索链接，由用户在浏览器执行（人在环）。
        """
        t = _current_target() if not target_id else store.get_target_style(target_id)
        if not t:
            return {"error": "缺少目标款（请先选定目标款）"}

        attr_desc = "、".join(f"{k}:{v}" for k, v in (t.attributes or {}).items() if v) or "无"
        prompt = (
            f"目标款：{t.name}\n"
            f"品类：{t.category or '未知'}\n"
            f"描述：{t.description or '无'}\n"
            f"属性：{attr_desc}\n"
            f"参考价：{t.reference_price or '无'}\n"
        )
        if focus:
            prompt += f"用户额外关注：{focus}\n"

        queries: list[dict] = []
        llm = LLMClient()
        if llm.available:
            try:
                raw = llm.chat([
                    {"role": "system", "content": (
                        "你是 1688 采购搜索专家。根据商品信息，生成 3-4 条用于在 1688 搜索"
                        "「同款/同款货源」的搜索关键词。要求：\n"
                        "- 每条 4-12 个字，贴近 1688 卖家常用标题写法\n"
                        "- 覆盖不同角度：精准款式、品类通用、材质工艺、批发场景\n"
                        "- 不要包含品牌名（除非用户明确要求）\n"
                        "输出 JSON 数组，每项 {\"kw\":\"关键词\",\"why\":\"一句话理由\"}，只输出 JSON。"
                    )},
                    {"role": "user", "content": prompt},
                ], temperature=0.4, max_tokens=600)
                txt = raw.strip()
                if txt.startswith("```"):
                    txt = txt.split("```")[1]
                    if txt.lstrip().startswith("json"):
                        txt = txt.lstrip()[4:]
                parsed = json.loads(txt.strip())
                if isinstance(parsed, list):
                    for item in parsed[:4]:
                        if isinstance(item, dict) and item.get("kw"):
                            queries.append({
                                "kw": str(item["kw"]).strip()[:20],
                                "why": str(item.get("why", "")).strip()[:60],
                            })
            except Exception:
                pass

        # LLM 不可用或失败 → 规则兜底：名称 + 属性组合
        if not queries:
            base = re.sub(r"[\s_\-—]+", " ", t.name).strip()
            queries.append({"kw": base[:20], "why": "目标款原名，最直接的搜索词"})
            vals = [v for v in (t.attributes or {}).values() if v and len(v) <= 10][:2]
            if vals:
                queries.append({"kw": f"{vals[0]} {vals[-1]}".strip()[:20],
                                "why": "用材质/工艺属性搜索，可发现更多同款货源"})
            if t.category:
                queries.append({"kw": f"{t.category} 批发", "why": "品类通用词，覆盖批发场景"})

        for q in queries:
            q["url"] = ("https://s.1688.com/selloffer/offer_search.htm?keywords="
                        + quote(q["kw"].encode("gbk")))

        return {
            "目标款": t.name,
            "搜索策略": queries,
            "执行方式": (
                "这些链接需要用户在浏览器中逐个打开搜索，然后在 1688 页面点"
                "「📥 导入1688商品」书签批量导入候选商品，导入后回来继续分析。"
            ),
            "注意": "1688 有严格反爬，服务端无法自动检索，因此采用人在环模式。",
        }

    # ---- 长任务规划：跨轮次的采购计划 ---------------------------------------

    _PLAN_STATUS = {"pending", "in_progress", "done", "blocked"}

    def create_plan(goal: str, steps: list) -> dict:
        """把多阶段采购目标分解为持久化计划（同目标款只保留一个进行中的计划）。"""
        t = _current_target()
        if not steps:
            return {"error": "steps 不能为空，请给出有序步骤"}
        norm = []
        for i, s in enumerate(steps[:10]):   # 上限 10 步，防失控
            if isinstance(s, str):
                norm.append({"index": i, "action": s[:60], "detail": "", "status": "pending", "note": ""})
            elif isinstance(s, dict) and s.get("action"):
                norm.append({
                    "index": i,
                    "action": str(s["action"])[:60],
                    "detail": str(s.get("detail", ""))[:120],
                    "status": "pending",
                    "note": "",
                })
        if not norm:
            return {"error": "steps 里没有有效步骤（每步需要 action 字段）"}
        from datetime import datetime as _dt
        from app.models.entities import AgentPlanRecord
        rec = AgentPlanRecord(
            id=f"plan-{uuid.uuid4().hex[:12]}",
            target_style_id=t.id if t else None,
            goal=goal[:200],
            steps=norm,
            created_at=_dt.now(), updated_at=_dt.now(),
        )
        store.save_agent_plan(rec)
        return {
            "plan_id": rec.id,
            "目标": rec.goal,
            "步骤数": len(norm),
            "计划": norm,
            "说明": ("计划已持久化，之后每次运行会自动带上进度。每完成一个阶段"
                     "（如核验完成、拿到某家报价）就调用 update_plan_step 更新状态；"
                     "全部完成后计划自动关闭。"),
        }

    def get_plan() -> dict:
        """查看当前目标款进行中的采购计划与步骤进度。"""
        t = _current_target()
        plan = store.get_active_plan(t.id) if t else None
        if not plan:
            return {"无进行中的计划": True, "提示": "如目标需要多轮完成，可用 create_plan 创建。"}
        done = sum(1 for s in plan.steps if s.get("status") == "done")
        return {
            "plan_id": plan.id,
            "目标": plan.goal,
            "进度": f"{done}/{len(plan.steps)}",
            "步骤": plan.steps,
        }

    def update_plan_step(step_index: int, status: str = "done", note: str = "") -> dict:
        """更新计划某一步的状态（pending/in_progress/done/blocked），全部完成则计划关闭。"""
        t = _current_target()
        plan = store.get_active_plan(t.id) if t else None
        if not plan:
            return {"error": "当前目标款没有进行中的计划"}
        status = status if status in _PLAN_STATUS else "done"
        target_step = next((s for s in plan.steps if s.get("index") == step_index), None)
        if target_step is None:
            valid = [s["index"] for s in plan.steps]
            return {"error": f"步骤 {step_index} 不存在，有效索引：{valid}"}
        target_step["status"] = status
        if note:
            target_step["note"] = str(note)[:120]
        done = sum(1 for s in plan.steps if s["status"] == "done")
        if done == len(plan.steps):
            plan.status = "done"
        from datetime import datetime as _dt
        plan.updated_at = _dt.now()
        store.save_agent_plan(plan)
        return {
            "plan_id": plan.id,
            "已更新": {"index": step_index, "status": status, "note": target_step["note"]},
            "总进度": f"{done}/{len(plan.steps)}",
            "计划状态": plan.status,
        }

    # ---- 浏览器自动导入：CDP 接管真实浏览器抓搜索结果 -----------------------

    def auto_import(keywords: str, max_items: int = 20) -> dict:
        """在用户已登录的抓取浏览器里自动搜索 1688 并导入候选商品（含真实店铺名与主图）。

        前提：用户先用 scripts/start_browser.py 启动抓取浏览器并登录过 1688。
        只读抓取（搜索结果页），不自动发消息、不下单，单次限量防风控。
        """
        from app.browser import cdp

        if not keywords or not keywords.strip():
            return {"error": "keywords 不能为空（可先想好搜索词，或参考 plan_search 的策略）"}
        max_items = max(1, min(int(max_items or 20), 40))
        if not cdp.is_available():
            return {
                "error": "抓取浏览器未运行。",
                "解决办法": "让用户运行 scripts/start_browser.py 启动抓取浏览器"
                          "（首次需在新窗口扫码登录一次 1688），然后重新调用本工具。",
                "降级方案": "暂用 plan_search 生成搜索链接走人工书签导入。",
            }
        try:
            result = cdp.search_offers(keywords, max_items=max_items)
        except Exception as e:
            return {"error": f"浏览器抓取失败：{type(e).__name__}: {e}",
                    "降级方案": "暂用 plan_search 生成搜索链接走人工书签导入。"}
        if result.get("login_required"):
            return {
                "error": "抓取浏览器未登录 1688。",
                "解决办法": "请在抓取浏览器窗口里扫码登录一次 1688（登录态会记住），"
                          "然后重新调用本工具。",
            }
        items = cdp.normalize_items(result.get("items"), max_items)
        if not items:
            return {"error": "搜索页没有提取到商品卡片（可能是关键词太偏或页面改版）",
                    "关键词": keywords, "降级方案": "换关键词重试，或走 plan_search 人工路。"}

        t = _current_target()
        if not t:
            return {"error": "缺少当前目标款（请先选定目标款）"}

        # 店铺 get_or_create（真实店名），商品 id=auto-{offer_id}，图片落盘
        shops_by_name = {s.name: s.id for s in store.list_shops()}
        upload_dir = Path(__file__).resolve().parents[2] / "data" / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        imported, skipped, img_ok, shops_touched = 0, 0, 0, set()
        for it in items:
            pid = f"auto-{it['offer_id']}"
            if store.get_product(pid):
                skipped += 1
                continue
            shop_name = it["shop"] or "1688搜索（卖家未记录）"
            if shop_name in shops_by_name:
                sid = shops_by_name[shop_name]
            else:
                sid = uuid.uuid4().hex
                store.save_shop(Shop(id=sid, name=shop_name))
                shops_by_name[shop_name] = sid
            image_paths = []
            local = cdp.download_image(it["image"], upload_dir) if it["image"] else None
            if local:
                # 存绝对路径，与书签导入口径一致（matcher 只认本地文件路径）
                image_paths = [str(upload_dir / Path(local).name)]
                img_ok += 1
            store.save_product(Product(
                id=pid, target_style_id=t.id, shop_id=sid,
                title=it["title"], url=it["url"], listed_price=it["price"],
                image_paths=image_paths,
                match_status=MatchStatus.CANDIDATE,
                match_reason="智能体浏览器搜索导入（真实店铺）",
            ))
            shops_touched.add(shop_name)
            imported += 1
        return {
            "导入数": imported,
            "跳过(已存在)": skipped,
            "图片落盘": img_ok,
            "涉及店铺": len(shops_touched),
            "关键词": keywords,
            "下一步": ("候选已入库且多数带图。建议直接调用 match_products 做视觉核验，"
                      "然后 compare_prices 出比价结论。"),
        }

    tools = [
        ToolSpec(
            name="auto_import",
            description=(
                "自动搜索并导入候选商品：在用户已登录的抓取浏览器里打开 1688 搜索页，"
                "提取商品（真实店铺名、标价、主图）直接入库。当目标款候选太少、或用户说"
                "「帮我找货源/找候选」时优先用这个工具（比 plan_search 人工路快得多）；"
                "浏览器未运行或未登录时按返回的解决办法引导用户，再降级 plan_search。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "keywords": {"type": "string", "description": "1688 搜索关键词（参考目标款名称与属性，4-20 字）"},
                    "max_items": {"type": "integer", "description": "单次导入上限（默认20，最大40）"},
                },
                "required": ["keywords"],
            },
            func=auto_import,
        ),
        ToolSpec(
            name="create_plan",
            description=(
                "把一个需要多轮才能完成的长期采购目标（如「帮我用最低价采购50件XX」，"
                "涉及搜索→导入→核验→比价→询价→等卖家回复→复比价）分解为有序步骤并持久化。"
                "之后每次运行都会自动带上计划进度，跨轮次接续推进；同目标款新建计划会替代旧计划。"
                "一次性能完成的简单问题不要用这个工具。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "长期目标的一句话描述"},
                    "steps": {
                        "type": "array",
                        "description": "有序步骤数组，每项为字符串或 {action, detail?} 对象",
                        "items": {},
                    },
                },
                "required": ["goal", "steps"],
            },
            func=create_plan,
        ),
        ToolSpec(
            name="get_plan",
            description="查看当前目标款进行中的采购计划与各步骤进度（上下文里通常已注入，用于主动确认最新状态）。",
            parameters={"type": "object", "properties": {}, "required": []},
            func=get_plan,
        ),
        ToolSpec(
            name="update_plan_step",
            description=(
                "更新采购计划某一步的状态：pending(待办)/in_progress(进行中)/done(完成)/blocked(受阻)。"
                "每完成一个阶段必须调用一次，防止跨轮次丢进度；全部步骤完成时计划自动关闭。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "step_index": {"type": "integer", "description": "步骤索引（从0开始）"},
                    "status": {"type": "string", "description": "新状态", "enum": ["pending", "in_progress", "done", "blocked"]},
                    "note": {"type": "string", "description": "一句话进展备注（可选），如「玮枫13元已核验」"},
                },
                "required": ["step_index"],
            },
            func=update_plan_step,
        ),
        ToolSpec(
            name="plan_search",
            description=(
                "当目标款下候选商品太少（少于5个）或为空时调用。"
                "智能分析目标款，生成多条 1688 搜索关键词和可直接点击的搜索链接。"
                "调用后把链接和建议展示给用户，请用户去 1688 搜索并用书签导入候选商品。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target_id": {"type": "string", "description": "目标款 ID（缺省用当前选中的）"},
                    "focus": {"type": "string", "description": "用户额外关注点（可选），如某个材质/风格"},
                },
                "required": [],
            },
            func=plan_search,
        ),
        ToolSpec(
            name="make_search_link",
            description=(
                "按当前目标款生成 1688 搜索链接，让用户去搜索候选商品。"
                "当目标款下的候选商品太少或没有时调用：把链接发给用户，"
                "用户挑好后用书签脚本导入或把详情页链接贴回来。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "extra_keywords": {"type": "string", "description": "追加搜索词（可选），如品牌、风格词"},
                },
                "required": [],
            },
            func=make_search_link,
        ),
        ToolSpec(
            name="import_products",
            description=(
                "把用户贴来的 1688 商品详情页链接（或 {url,title,price} 数据）"
                "入库为候选商品。系统会尝试自动抓取标题和价格，抓不到时降级存为"
                "待核验候选。导入后提醒：无图商品需要书签脚本补全图片才能视觉匹配。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "商品链接字符串数组，或 {url, title?, price?} 对象数组",
                        "items": {},
                    },
                    "target_id": {"type": "string", "description": "目标款 ID（缺省用当前选中的目标款）"},
                },
                "required": ["items"],
            },
            func=import_products,
        ),
        ToolSpec(
            name="list_targets",
            description="列出系统中已有的目标款（要比价的款），包括每个目标款下的商品数和价格覆盖情况。用于了解当前有哪些款可以比价。",
            parameters={"type": "object", "properties": {}, "required": []},
            func=list_targets,
        ),
        ToolSpec(
            name="inspect_target",
            description="查看某个目标款的详情：属性、参考价、商品总数、匹配状态分布。用于判断该款的数据是否足够做比价。",
            parameters={
                "type": "object",
                "properties": {"target_id": {"type": "string", "description": "目标款 ID"}},
                "required": ["target_id"],
            },
            func=inspect_target,
        ),
        ToolSpec(
            name="match_products",
            description=(
                "对目标款的候选商品执行视觉匹配，判断哪些是完全同款、等效替代、需要排除。"
                "必须在比价前做这一步，否则商品都是未核验状态。增量模式：已核验且图片未变"
                "的商品自动复用结果，重复调用没有额外开销；force=true 可强制全量重跑。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target_id": {"type": "string", "description": "目标款 ID"},
                    "force": {"type": "boolean", "description": "是否强制全量重跑（默认 false 增量）"},
                },
                "required": ["target_id"],
            },
            func=match_products,
        ),
        ToolSpec(
            name="compare_prices",
            description="生成比价结论：按价格排序、给出采购建议和议价话术。需要先完成视觉匹配效果最好。",
            parameters={
                "type": "object",
                "properties": {"target_id": {"type": "string", "description": "目标款 ID"}},
                "required": ["target_id"],
            },
            func=compare_prices,
        ),
        ToolSpec(
            name="inquiry_script",
            description="为某个候选商品生成向卖家询价的话术，包含价格、起订量、含税等问题。用于需要进一步确认报价的场景。",
            parameters={
                "type": "object",
                "properties": {
                    "target_id": {"type": "string", "description": "目标款 ID"},
                    "product_id": {"type": "string", "description": "候选商品 ID"},
                    "quantity": {"type": "integer", "description": "拟采购数量", "default": 100},
                },
                "required": ["target_id", "product_id"],
            },
            func=inquiry_script,
        ),
    ]
    return tools