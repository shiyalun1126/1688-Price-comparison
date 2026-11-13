"""自动谈判编排器：一个后台线程推进所有会话的状态机。

为什么必须是后台线程而不是请求里同步跑：卖家可能几小时后才回。请求超时会
把「等待」变成「失败」，而对话本身还在继续。所以拆成「持久化状态 + 定时
推进」：每次 tick 取出 next_action_at 已到的会话，读一次会话、解析一次、
决策一次、必要时发一句，然后重新排下一次检查时间（指数退避）。

风控是硬约束，写在调度层而不是话术层：单次发送之后必须静默一段随机间隔，
每日新增会话有配额，命中验证码时整个引擎暂停等人工，绝不做「多重试几次
也许就过了」这种事。
"""
from __future__ import annotations

import os
import random
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from app.browser.chat import ChatChannelBase, ChatError, get_channel
from app.browser.cdp import is_available
from app.browser.detail import fetch_offer_facts
from app.inquire import detail_facts
from app.inquire.dialogue import (
    GAP_LABELS,
    DialogueConfig,
    classify_reply,
    compute_gaps,
    decide,
    merge_quote,
    next_expiry,
    pick_market_min,
    strip_think,
)
from app.inquire.inquiry import parse_quote
from app.llm.client import LLMClient
from app.models.entities import InquiryMessage, InquirySession
from app.models.enums import (
    ChatChannel,
    InquiryStage,
    InquiryStatus,
    MatchTier,
    MsgRole,
    QuoteSource,
    StockStatus,
    TaxStatus,
)
from app.store.db import Store

# 需要人工先处理的通道级故障：继续跑只会不断撞墙
_BLOCKING_KINDS = ("need_verify", "not_logged_in", "no_browser")

_TERMINAL = (InquiryStatus.QUOTED, InquiryStatus.CLOSED,
             InquiryStatus.ESCALATED, InquiryStatus.EXPIRED)


def _int_env(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, "") or default)
    except ValueError:
        return default


def _float_env(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, "") or default)
    except ValueError:
        return default


class InquiryEngine:
    """会话编排器（进程内单例，由 main 启动）。"""

    def __init__(self, store: Store, tick_s: Optional[int] = None,
                 batch: Optional[int] = None, shot_dir: Optional[Path] = None):
        self.store = store
        self.tick_s = tick_s or _int_env("INQUIRY_TICK_S", 20)
        self.batch = batch or _int_env("INQUIRY_BATCH", 3)
        self.shot_dir = shot_dir or (Path(__file__).resolve().parents[2]
                                     / "data" / "uploads" / "chat")
        self.cfg = DialogueConfig.from_env()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._run_lock = threading.Lock()      # 防止 tick 与手动推进并发
        self._last_send = 0.0
        self.last_tick: Optional[datetime] = None
        self.last_error = ""
        self.blocked_reason = ""               # 全局阻断（验证码/未登录）
        self.sent_total = 0
        self.reply_total = 0
        # 每个会话上一次的决策动作。tick 每 20 秒一轮，连续 wait 全记进会话流
        # 会把对话刷成噪声，所以只有动作变化时才留一条决策轨迹。
        self._last_action: dict[str, str] = {}

    # ---- 生命周期 --------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="inquiry-engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
                self.last_error = ""
            except Exception as e:      # 任何异常都不能让引擎线程死掉
                self.last_error = f"{type(e).__name__}: {e}"
            self._stop.wait(self.tick_s)

    # ---- 入队 ------------------------------------------------------------
    def quota_left(self) -> int:
        """今日还能新建几个会话（风控配额）。"""
        limit = _int_env("INQUIRY_DAILY_LIMIT", 25)
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return max(0, limit - self.store.count_sessions_since(today))

    def enqueue(self, inquiry_id: str, *, force: bool = False,
                require_verified: bool = False,
                channel: str = "") -> dict:
        """把询价任务变成自动会话（已存在则重置重跑）。

        require_verified：只接受已核验为「完全同款 / 等效替代」的商品。
        对外的自动询价（接口、智能体工具）都打开这个开关——连是不是这款都没
        确认就去问价，卖家报回来的数字跟目标款没关系，白花配额还像乱发的号。
        离线脚本与演示需要绕过时显式传 False。

        channel：按会话指定通道。留空则用 INQUIRY_CHANNEL 环境变量。
        有了这个参数，「演示通道（剧本秒回）」和「真实通道（真发到 1688）」
        才能在同一份代码里共存——让对方自己点着试，而不是只信一段录屏。
        """
        inquiry = self.store.get_inquiry(inquiry_id)
        if not inquiry:
            return {"error": "询价任务不存在"}
        target = self.store.get_target_style(inquiry.target_style_id)
        product = self.store.get_product(inquiry.product_id)
        if not target or not product:
            return {"error": "目标款或商品不存在"}
        if require_verified and product.match_tier not in (MatchTier.EXACT,
                                                           MatchTier.EQUIVALENT):
            return {"error": "该商品还没核验为「完全同款 / 等效替代」，"
                             "先跑视觉匹配，或手动改档后再发起自动询价"}
        if not (product.url or "").startswith("http"):
            return {"error": "该商品没有 1688 链接，无法打开会话（请用书签重新导入）"}

        existing = self.store.get_session_by_inquiry(inquiry_id)
        if existing and existing.status not in _TERMINAL and not force:
            return {"session_id": existing.id, "already_running": True,
                    "status": existing.status.value,
                    "提示": "该询价已在自动谈判中，无需重复发起"}
        # 同一家店只保留一个会话：换商品重开会让老板收到两套一模一样的问句
        for other in self.store.list_active_sessions():
            if (other.target_style_id == target.id and other.shop_id == product.shop_id
                    and other.inquiry_id != inquiry_id):
                return {"error": f"该店铺已有进行中的会话（{other.id}），一家店只聊一次"}
        if not existing and self.quota_left() <= 0:
            return {"error": "今日自动询价配额已用完（风控保护），明天再试或调大 "
                             "INQUIRY_DAILY_LIMIT"}

        now = datetime.now()
        mode = (channel or os.getenv("INQUIRY_CHANNEL", "cdp")).lower()
        # 演示实例（给别人点着试的那种）默认禁止真实通道：截图里那句话说得很清楚，
        # 「需要智能体去跟商家对话」是要证明能力，不是让评审拿着我的号去骚扰老板。
        # 关掉之后就只剩剧本卖家，点坏了也不会发出任何一条真实消息。
        if mode == "cdp" and os.getenv("INQUIRY_ALLOW_REAL", "1").lower() in (
                "0", "false", "no"):
            return {"error": "当前是演示实例，真实通道已关闭（INQUIRY_ALLOW_REAL=0）："
                             "请点「▷ 演示一遍」用剧本卖家体验完整谈判过程"}
        session = InquirySession(
            id=existing.id if existing else f"s-{uuid.uuid4().hex[:12]}",
            inquiry_id=inquiry_id,
            target_style_id=target.id,
            product_id=product.id,
            shop_id=product.shop_id,
            offer_url=product.url or "",
            channel=ChatChannel.MOCK if mode == "mock" else ChatChannel.CDP,
            stage=InquiryStage.INQUIRY,
            status=InquiryStatus.SENDING,
            round=0,
            gap=compute_gaps(None, self.cfg.required),
            buyer_quantity=inquiry.buyer_quantity,
            floor_price=target.reference_price,
            next_action_at=now,
            expires_at=next_expiry(self.cfg, now),
            created_at=now,
            updated_at=now,
        )
        self.store.save_session(session)
        inquiry.status = InquiryStatus.SENDING
        inquiry.updated_at = now
        self.store.save_inquiry(inquiry)
        self._log(session.id, MsgRole.SYSTEM,
                  f"会话已创建，通道={session.channel.value}，"
                  f"拟采购 {session.buyer_quantity or '未填'} 件")
        return {"session_id": session.id, "status": "queued"}

    def enqueue_many(self, target_style_id: str) -> dict:
        """批量发起：只对已有的询价任务入队（不会凭空给未询价的商品发消息）。"""
        queued, skipped, errors = [], [], []
        for inquiry in self.store.list_inquiries(target_style_id):
            if inquiry.status in (InquiryStatus.QUOTED,):
                skipped.append(inquiry.id)
                continue
            r = self.enqueue(inquiry.id)
            if r.get("error"):
                errors.append({"inquiry_id": inquiry.id, "error": r["error"]})
            elif r.get("already_running"):
                skipped.append(inquiry.id)
            else:
                queued.append(r["session_id"])
        return {"已入队": len(queued), "跳过": len(skipped),
                "失败": errors, "session_ids": queued,
                "今日剩余配额": self.quota_left()}

    # ---- 推进 ------------------------------------------------------------
    def run_once(self, limit: int = 0) -> list[dict]:
        """推进一轮到期的会话；返回每个会话的处理结果（可手动调用做演示）。"""
        with self._run_lock:
            now = datetime.now()
            self.last_tick = now
            due = self.store.list_due_sessions(now, limit=limit or self.batch)
            results = []
            for session in due:
                if self.blocked_reason:
                    break
                if session.paused:
                    continue
                try:
                    results.append(self._process(session))
                except Exception as e:
                    session.error = f"{type(e).__name__}: {e}"
                    session.status = InquiryStatus.ESCALATED
                    session.updated_at = datetime.now()
                    self.store.save_session(session)
                    self._log(session.id, MsgRole.SYSTEM, f"推进失败转人工：{session.error}")
                    results.append({"session_id": session.id, "action": "escalate",
                                    "reason": session.error})
            return results

    def _quote_is_live(self, quote) -> bool:
        """这条报价对应的会话是否还在。

        模拟通道跑完的会话被清掉后，报价却留在库里，仍参与「市场最低价」计算——
        拿一条真实报价去跟剧本里的假报价比，比出来的「我们最便宜」是假的。
        手工录入的报价（source=manual）本来就没有会话，要保留。
        """
        if getattr(quote.source, "value", quote.source) != QuoteSource.INQUIRY.value:
            return True
        sid = quote.id[2:] if (quote.id or "").startswith("q-") else ""
        return bool(sid) and self.store.get_session(sid) is not None

    def _prefill_detail(self, session, product):
        """开聊前抓一次商品详情页，把页面公示信息存到商品上。

        只在第一轮、且商品还没有缓存时抓；失败（验证码/未登录/改版）只记一条
        系统提示，然后按原流程继续问——预填是加分项，不能成为阻塞项。
        """
        if os.getenv("INQUIRY_DETAIL_PREFILL", "1") in ("0", "false", "False"):
            return None
        if not is_available():
            return None
        facts, why = fetch_offer_facts(product.url or "")
        if facts:
            detail_facts.attach(product, facts)
            self.store.save_product(product)
            self._log(session.id, MsgRole.SYSTEM,
                      f"详情页预填：{facts.summary(session.buyer_quantity)}")
            return facts
        if why:
            self._log(session.id, MsgRole.SYSTEM, f"详情页预填跳过：{why}")
        return None

    def _process(self, session: InquirySession) -> dict:
        target = self.store.get_target_style(session.target_style_id)
        product = self.store.get_product(session.product_id)
        inquiry = self.store.get_inquiry(session.inquiry_id)
        if not target or not product or not inquiry:
            session.status = InquiryStatus.ESCALATED
            session.error = "关联数据缺失（目标款/商品/询价任一不存在）"
            self.store.save_session(session)
            return {"session_id": session.id, "action": "escalate", "reason": session.error}

        llm = LLMClient()
        quote_id = f"q-{session.id}"
        base = self.store.get_quote(quote_id)
        prev = [m for m in self.store.list_messages(session.id) if m.role != MsgRole.SYSTEM]
        # 详情页公示信息（价格/阶梯价/起订/运费/库存）：有就不问，没有就第一次抓一次。
        facts = detail_facts.load(product)
        if facts is None and session.round == 0:
            facts = self._prefill_detail(session, product)

        shop = self.store.get_shop(session.shop_id)
        channel: ChatChannelBase = get_channel(
            session.id, mode=session.channel.value, shot_dir=self.shot_dir,
            touid=(shop.aliww if shop else None))
        sent_text = ""
        try:
            channel.open(session.offer_url)
            self._remember_touid(shop, channel)

            # 1) 读会话 → 增量对齐 → 落库
            bubbles = channel.read()
            fresh = _align(prev, bubbles)
            seller_texts = []
            for b in fresh:
                role = MsgRole.BUYER if b.get("self") else MsgRole.SELLER
                self.store.add_message(InquiryMessage(
                    id=uuid.uuid4().hex, session_id=session.id, role=role,
                    kind=b.get("kind") or "text", text=b["text"],
                ))
                if role == MsgRole.SELLER:
                    seller_texts.append(b["text"])
            if fresh:
                session.last_speaker = (MsgRole.BUYER if fresh[-1].get("self")
                                        else MsgRole.SELLER)
                session.last_msg_at = datetime.now()
                if seller_texts:
                    self.reply_total += 1

            # 1.5) 接管既有会话：真实店铺常常「之前已经聊过」（人工问过、别的工具问过、
            # 甚至我们自己试跑过）。这时 round=0 会照着第一次的顺序再问一遍开场，
            # 卖家看到的是同一句话第二次出现——最像机器人的动作。
            # 以页面上的历史为准校准轮次，让决策从「现在聊到哪」继续。
            if session.round == 0 and bubbles:
                own = sum(1 for b in bubbles if b.get("self"))
                if own:
                    session.round = own
                    self._log(session.id, MsgRole.SYSTEM,
                              f"接管既有会话：页面上已有 {len(bubbles)} 条消息"
                              f"（我方 {own} 条），沿用历史继续，不重发开场")

            # 2) 增量解析报价并合并（卖家常分几条说完，不能覆盖）
            for text in seller_texts:
                patch = parse_quote(text, target, product)
                if not _has_signal(patch):
                    continue        # 纯闲聊，不产生报价
                base = merge_quote(base, patch)
            # 2.5) 卖家只回「可以，不含税」时，话里一个数字都没有，上面解析不出价。
            # 此时用页面价落一条可溯源报价——否则会回头再问一遍「什么价」，
            # 预填就白做了。带「问下/稍等」的中间态不算确认。
            if (not base or not base.price) and facts and seller_texts:
                last_seller = seller_texts[-1]
                if (classify_reply(last_seller) not in ("pending", "refuse")
                        and detail_facts.is_affirmative(last_seller)):
                    patch = detail_facts.quote_from_facts(
                        facts, target, product,
                        session.buyer_quantity or self.cfg.default_quantity,
                        last_seller)
                    if patch:
                        base = merge_quote(base, patch)
            # 2.6) 页面公示的起订量/运费口径/库存补空字段（留痕，不覆盖卖家原话）
            if base and facts:
                base = detail_facts.fill_quote_gaps(base, facts)
            if base and _has_signal(base):
                base.id = quote_id
                base.inquiry_id = session.inquiry_id
                base.quoted_at = datetime.now()
                self.store.save_quote(base)

            # 3) 决策
            quotes = [q for q in self.store.list_quotes(target.id)
                      if self._quote_is_live(q)]
            market_min = pick_market_min(quotes, exclude_shop_id=session.shop_id,
                                         exclude_quote_id=quote_id)
            history = [m.text for m in prev][-8:]
            # 站外引流扫本轮全部消息：卖家常先说「加个联系方式」再补发商品卡片，
            # 只看最后一条就会漏掉那句最该停下来的话。
            seller_line = seller_texts[-1] if seller_texts else ""
            risky = next((t for t in seller_texts
                          if classify_reply(t) == "off_channel"), "")
            if risky:
                seller_line = risky
            plan = decide(
                session=session, quote=base,
                seller_text=seller_line,
                market_min=market_min, target=target, product=product,
                cfg=self.cfg, llm=llm, history=history,
                asked_counts=_asked_counts(prev),
                clarify_sent=sum(1 for m in prev if m.role == MsgRole.BUYER
                                 and "http" in (m.text or "").lower()),
                facts=facts,
            )

            # 4) 执行
            result = {"session_id": session.id, "action": plan.action,
                      "reason": plan.reason, "gap": plan.gap}
            now = datetime.now()

            # 决策轨迹先落库再执行：发送失败也留得下「当时为什么决定这么说」
            self._trace_decision(session.id, plan)

            if plan.action.startswith("send_"):
                text = strip_think(plan.text)
                # 本轮卖家说过话 → 这是新互动，换句话追问同一批缺口不算复读；
                # prev 是在读会话之前取的，看不到本轮新消息，所以单独把
                # seller_texts 传进去；否则卖家回「可先拍下改价」（等于没报价）
                # 时，追问会被判成重复而跳过，会话就卡住直到过期。
                dup = _same_as_last_buyer(prev, text,
                                          seller_spoke=bool(seller_texts))
                if plan.answer_call:
                    # 对方在喊人，必须应一句。但刚应过一次就不要再应，
                    # 否则变成「在的在的在的」——同样是机器行为。
                    last_buyer = next((m.text for m in reversed(prev)
                                       if m.role == MsgRole.BUYER), "")
                    dup = last_buyer.startswith("在的")
                if text and not dup:
                    self._throttle()
                    channel.send(text)
                    self.store.add_message(InquiryMessage(
                        id=uuid.uuid4().hex, session_id=session.id,
                        role=MsgRole.BUYER, kind="text", text=text,
                    ))
                    shot = channel.screenshot(tag=plan.action.replace("send_", ""))
                    if shot:
                        self._log(session.id, MsgRole.SYSTEM, f"已发送并截图 {shot}")
                    sent_text = text
                    session.round += 1
                    self.sent_total += 1
                    result["sent"] = text
                    session.last_speaker = MsgRole.BUYER
                    session.stage = plan.stage
                    session.gap = plan.gap
                    session.status = (InquiryStatus.NEGOTIATING
                                      if plan.stage == InquiryStage.BARGAIN
                                      else InquiryStatus.FOLLOWING_UP)
                    session.next_action_at = now + timedelta(seconds=max(plan.delay_s, 1))
                else:
                    # 没真正发出去，就绝不能把状态改成「已发」——否则引擎会去等
                    # 一条根本不存在的回复，一直等到超时。原地重排，缺口保留。
                    why = ("内容未通过外发校验" if not text else "与上一条重复")
                    skips = sum(1 for m in prev if m.role == MsgRole.SYSTEM
                                and m.text.startswith("跳过发送"))
                    self._log(session.id, MsgRole.SYSTEM,
                              f"跳过发送（{why}）：{(plan.text or '')[:60]}")
                    if skips >= 2:
                        session.status = InquiryStatus.ESCALATED
                        session.error = f"连续 {skips + 1} 次生成内容不合格，转人工"
                        session.next_action_at = None
                    else:
                        session.next_action_at = now + timedelta(
                            seconds=max(plan.delay_s, 30))
                    result["skipped"] = why

            elif plan.action == "wait":
                session.next_action_at = now + timedelta(seconds=max(plan.delay_s, 1))
                session.status = (InquiryStatus.NEGOTIATING
                                  if session.stage == InquiryStage.BARGAIN
                                  else InquiryStatus.SENT)

            elif plan.action == "close":
                has_price = bool(base and base.price)
                session.status = InquiryStatus.QUOTED if has_price else InquiryStatus.CLOSED
                session.next_action_at = None
                session.gap = plan.gap
                # 收工理由已由 _trace_decision 记进决策轨迹，这里不再重复一遍

            elif plan.action == "escalate":
                session.status = InquiryStatus.ESCALATED
                session.next_action_at = None
                session.error = plan.reason
                # 同上：转人工的理由在决策轨迹里，避免一行话在会话流里出现两次

            session.market_min = market_min
            session.updated_at = now
            self.store.save_session(session)
            self._sync_inquiry(session, inquiry)
            return result

        except ChatError as e:
            return self._handle_chat_error(session, e)
        except Exception as e:
            session.error = f"{type(e).__name__}: {e}"
            session.status = InquiryStatus.ESCALATED
            session.updated_at = datetime.now()
            self.store.save_session(session)
            self._log(session.id, MsgRole.SYSTEM, f"异常转人工：{session.error}")
            return {"session_id": session.id, "action": "escalate", "reason": session.error}
        finally:
            try:
                channel.close()
            except Exception:
                pass

    def _handle_chat_error(self, session: InquirySession, e: ChatError) -> dict:
        """通道级错误：能自愈的重排，要人工的立刻停手。"""
        now = datetime.now()
        session.error = str(e)
        if e.kind in _BLOCKING_KINDS:
            # 全局暂停：这类问题对每个会话都一样，继续跑只会反复撞墙
            self.blocked_reason = str(e)
            session.paused = True
            self._log(session.id, MsgRole.SYSTEM,
                      f"引擎暂停（{e.kind}）：{e}；处理完成后可一键恢复")
        elif e.kind in ("send_failed", "read_failed", "no_input", "no_entry", "not_open"):
            # 页面偶发问题：重排一次，连续失败两次才转人工
            session.next_action_at = now + timedelta(seconds=120 + random.randint(0, 60))
            self._log(session.id, MsgRole.SYSTEM, f"页面异常，稍后重试：{e}")
        else:
            session.status = InquiryStatus.ESCALATED
            session.next_action_at = None
            self._log(session.id, MsgRole.SYSTEM, f"转人工：{e}")
        session.updated_at = now
        self.store.save_session(session)
        return {"session_id": session.id, "action": "error", "reason": str(e),
                "blocked": bool(self.blocked_reason)}

    # ---- 人工干预 --------------------------------------------------------
    def pause(self, session_id: str, paused: bool = True) -> dict:
        s = self.store.get_session(session_id)
        if not s:
            return {"error": "会话不存在"}
        s.paused = paused
        # 恢复时立刻推进一轮，不用干等下个退避周期
        s.next_action_at = datetime.now() if not paused else None
        s.updated_at = datetime.now()
        self.store.save_session(s)
        return {"session_id": s.id, "paused": s.paused}

    def resume_engine(self) -> dict:
        """清掉全局阻断，把被暂停的会话放回队列。"""
        self.blocked_reason = ""
        resumed = 0
        for s in self.store.list_active_sessions():
            if s.paused:
                s.paused = False
                s.next_action_at = datetime.now()
                s.updated_at = datetime.now()
                self.store.save_session(s)
                resumed += 1
        return {"resumed": resumed, "blocked_reason": ""}

    def inject(self, session_id: str, text: str) -> dict:
        """人工接管时插一句话（走同一通道，保持会话连续）。"""
        s = self.store.get_session(session_id)
        if not s:
            return {"error": "会话不存在"}
        shop = self.store.get_shop(s.shop_id)
        channel = get_channel(session_id, mode=s.channel.value,
                              shot_dir=self.shot_dir,
                              touid=(shop.aliww if shop else None))
        try:
            channel.open(s.offer_url)
            self._remember_touid(shop, channel)
            channel.send(text)
            self.store.add_message(InquiryMessage(
                id=uuid.uuid4().hex, session_id=s.id, role=MsgRole.BUYER,
                kind="text", text=text))
            s.round += 1
            s.last_speaker = MsgRole.BUYER
            s.paused = True
            s.updated_at = datetime.now()
            self.store.save_session(s)
            return {"ok": True, "sent": text}
        except ChatError as e:
            return {"error": str(e)}
        finally:
            try:
                channel.close()
            except Exception:
                pass

    # ---- 工具 ------------------------------------------------------------
    def _throttle(self) -> None:
        """两次发送之间静默一段随机间隔（拟人 + 防高频判定）。"""
        gap = _float_env("INQUIRY_SEND_INTERVAL", 8.0)
        wait = gap + random.uniform(0, max(gap * 0.5, 0.1)) - (time.time() - self._last_send)
        if wait > 0:
            time.sleep(min(wait, 120))
        self._last_send = time.time()

    def _remember_touid(self, shop, channel) -> None:
        """把首次发现的旺旺号写回店铺。

        有了它后续就能直连 IM 地址，不用再走详情页——详情页有验证码墙，
        IM 页没有。这个字段省下的是每次会话的失败概率，不只是几秒钟。
        """
        touid = getattr(channel, "touid", "") or ""
        if not shop or not touid or shop.aliww == touid:
            return
        shop.aliww = touid
        self.store.save_shop(shop)

    def _log(self, session_id: str, role: MsgRole, text: str,
             kind: str = "system") -> None:
        self.store.add_message(InquiryMessage(
            id=uuid.uuid4().hex, session_id=session_id, role=role,
            kind=kind, text=text))

    def _trace_decision(self, session_id: str, plan) -> None:
        """把「为什么这么说」也留在会话流里。

        消息只能证明说了什么，理由才证明这句是它自己想出来的。审阅的人要判断
        「是智能体在谈，还是人照着剧本发」，靠的就是这一行一行的决策轨迹。
        连续 wait 不记：tick 每 20 秒一轮，全记会把真话淹成噪声。
        """
        prev = self._last_action.get(session_id, "")
        self._last_action[session_id] = plan.action
        if plan.action == "wait" and prev == "wait":
            return
        self._log(session_id, MsgRole.SYSTEM, _decision_text(plan), kind="decision")

    def _sync_inquiry(self, session: InquirySession, inquiry) -> None:
        """会话状态回写询价任务（前端列表读的是 inquiry）。"""
        if inquiry.status == session.status:
            return
        inquiry.status = session.status
        inquiry.updated_at = datetime.now()
        self.store.save_inquiry(inquiry)

    def status(self) -> dict:
        sessions = self.store.list_active_sessions()
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        by_status: dict[str, int] = {}
        for s in sessions:
            by_status[s.status.value] = by_status.get(s.status.value, 0) + 1
        # 未收工的会话里有多少是人工暂停的。状态条不能把「暂停」说成「进行中」，
        # 否则用户以为是引擎在跑、其实一条也不会发出去。
        paused = sum(1 for s in sessions if s.paused)
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "channel": os.getenv("INQUIRY_CHANNEL", "cdp"),
            "tick_s": self.tick_s,
            "batch": self.batch,
            "active": len(sessions),
            "paused": paused,
            "by_status": by_status,
            "sent_today": self.store.count_sessions_since(today),
            "quota_left": self.quota_left(),
            "sent_total": self.sent_total,
            "reply_total": self.reply_total,
            "blocked_reason": self.blocked_reason,
            "last_error": self.last_error,
            "last_tick": self.last_tick.isoformat() if self.last_tick else None,
        }


def _has_signal(q) -> bool:
    """这条解析结果有没有携带新信息。

    不能只看价格：卖家经常先回「现货、1 个起批」「含税、运费看地址」，
    把税/运费/现货丢掉的话，下一轮会拿同一件事再问一遍——对方会觉得
    我们根本没在听他说话。
    """
    if q is None:
        return False
    return bool(
        q.price or q.moq is not None or q.tiers
        or q.lead_time_hours is not None
        or q.tax != TaxStatus.UNKNOWN
        or q.includes_shipping is not None
        or q.stock_status != StockStatus.UNKNOWN
    )


def _align(prev: list[InquiryMessage], bubbles: list[dict]) -> list[dict]:
    """找出页面里「还没入库」的尾部消息。

    DOM 会重渲染、可能有系统提示混入，所以不做逐条 diff，而是拿已入库的
    最后一条**聊天**消息去页面里做最后一次定位，其后的都算新消息。

    这里的「聊天消息」必须排除 SYSTEM：系统提示（已发送并截图、跳过发送）
    只存在于我们自己的库里，页面上根本没有，拿它去定位永远匹配不到，
    于是每次都回退成「最后 3 条」——真实事故：同一条我方消息被重复入库两遍。
    """
    if not bubbles:
        return []
    chat = [m for m in prev if m.role != MsgRole.SYSTEM]
    if not chat:
        return bubbles
    last_text = chat[-1].text.strip()
    idx = -1
    for i in range(len(bubbles) - 1, -1, -1):
        if (bubbles[i].get("text") or "").strip() == last_text:
            idx = i
            break
    return bubbles[idx + 1:] if idx >= 0 else bubbles[-3:]


def _norm_outgoing(text: str) -> str:
    """去掉标点、空白与链接后再比对：换了个标点/商品名还是同一句话。"""
    t = re.sub(r"https?://\S+", "", text or "")
    t = re.sub(r"[\s，。、？！,.?!：:；;「」“”\"'（）()~…·]", "", t)
    return t


# 「这句话在问哪几项」——比字符串相似度更能反映对外观感：
# 措辞变了但问的还是同样六件事，卖家看到的就是复读。
_GAP_PROBES = {
    "price": ("价", "多少钱"),
    "moq": ("起订", "起批", "多少件起", "最少"),
    "tax": ("税", "票"),
    "shipping": ("运费", "邮费", "包邮"),
    "stock": ("现货", "库存"),
    "lead_time": ("发货", "多久", "时效", "几天"),
    # 只有它才区分「按我这个量报的价」和「单件价」，不能和 price 混在一起
    "tier_price": ("这个量", "大货", "批量价", "量价"),
}


def _asked_items(text: str) -> set[str]:
    return {k for k, ws in _GAP_PROBES.items() if any(w in text for w in ws)}


# 决策动作的中文名。引擎内部用英文枚举，会话流里给人看，不留英文黑话。
ACTION_LABELS = {
    "send_inquiry": "开场询价",
    "send_clarify": "澄清款式",
    "send_followup": "补问缺口",
    "send_bargain": "开口议价",
    "close": "收工",
    "wait": "静观等待",
    "escalate": "转人工",
}


def _decision_text(plan) -> str:
    """一行说清这次决策：做什么、为什么、还缺什么。"""
    label = ACTION_LABELS.get(plan.action, plan.action)
    reason = (plan.reason or "").strip()
    if not reason:
        text = label
    elif reason.startswith(label):
        text = reason                      # reason 已自报动作，别叠一遍
    else:
        text = f"{label} · {reason}"
    # 开场询价本来就是一次把所有缺口问全，reason 里已经写明了，不必再列一遍
    if plan.action == "send_inquiry":
        return text
    gap = "、".join(GAP_LABELS.get(g, g) for g in (plan.gap or []))
    if gap and "还缺" not in text:
        text += f" ｜ 还缺 {gap}"
    return text


def _asked_counts(prev: list[InquiryMessage]) -> dict[str, int]:
    """每一项「我们已经问过几次」。

    用来止住「同一批问题每轮重问一遍」——这是最像机器人的行为之一。
    带链接的消息不计入：那是回答「哪一款」，不是提问，不该把价/税/运费
    的提问次数顶上去。
    """
    counts: dict[str, int] = {}
    for m in prev:
        if m.role != MsgRole.BUYER or "http" in (m.text or "").lower():
            continue
        for k in _asked_items(_norm_outgoing(m.text)):
            counts[k] = counts.get(k, 0) + 1
    return counts


def _same_as_last_buyer(prev: list[InquiryMessage], text: str,
                        seller_spoke: bool = False) -> bool:
    """幂等：最近一条我方消息就是同一句，别重发（防重复轰炸）。

    不能只看「最后一条消息」：卖家在中间说了句「店长还没回」，最后一条就不是
    我方的了，于是同一句追问又发一遍。真实事故：同一句「多久能发货」连发两次。

    判定：
    ① 字面（去标点后）相同 → 复读，任何情况下都拦；
    ② 问的还是同样那几项 → 复读。真实事故（2026-11-06 义乌希因帽业）：
       「…看到你家「渔夫帽…」想拿100件…」之后又发「…看到...想拿100件…」，
       只换了个商品名，卖家看到的就是同一句话连发两次，直接回「机器人那？」；
    ③ 带链接的消息不参与判重——它回答的是「哪一款」，是新信息，不是重复。

    seller_spoke：本轮卖家说了话（刚读到他的新消息）。此时只拦 ①——
    措辞变了但问项相同，是「他没答，我换个说法再问」，属于追问。
    真实卡死案例（2026-11-12 泉州沙足鞋业）：卖家回「可先拍下改价」，
    等于没报价格，智能体想追问却被判成重复直接跳过，会话停在那儿直到过期。
    """
    norm = _norm_outgoing(text)
    compared_asked = False
    for m in reversed(prev):
        if m.role != MsgRole.BUYER:
            continue        # 系统提示、决策记录、卖家的话都不算我方说过的
        a = _norm_outgoing(m.text)
        if a == norm:
            return True
        if compared_asked:
            continue        # 问项只跟最近一条我方消息比一次
        compared_asked = True
        if seller_spoke:
            continue        # 卖家刚回过话：允许换句话追问同一批缺口
        if "http" in (m.text or "").lower() or "http" in (text or "").lower():
            return False
        asked_a, asked_b = _asked_items(a), _asked_items(norm)
        return bool(asked_a) and asked_a == asked_b
    return False


_ENGINES: dict[int, InquiryEngine] = {}
_ENGINE_LOCK = threading.Lock()


def get_engine(store: Store) -> InquiryEngine:
    """按 store 实例缓存引擎（每个进程一个）。"""
    with _ENGINE_LOCK:
        key = id(store)
        engine = _ENGINES.get(key)
        if engine is None:
            engine = InquiryEngine(store)
            _ENGINES[key] = engine
        return engine
