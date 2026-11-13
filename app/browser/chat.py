"""旺旺会话执行器：把话发出去、把回复读回来。

两条通道共用同一套接口，worker 不关心背后是真浏览器还是模拟：

- CDPChatChannel：接管用户已登录的真实浏览器，打开商品详情页唤起旺旺浮层，
  拟人化输入并发送；读消息时从聊天面板抓全量气泡，由上层做增量对齐。
- MockChatChannel：按剧本回复的假卖家。没有 1688 账号也能跑通「询价→追问→
  议价→收敛」全链路，用于演示、回归测试和面试现场兜底。

选择器集中在 SELECTORS 里，1688 改版时只改这一处；scripts/probe_chat.py
会打印每个候选选择器的命中情况，用来校准。
"""
from __future__ import annotations

import json
import os
import random
import re
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import quote, unquote

# 网页版旺旺（air.1688.com）的会话地址。touid 即卖家旺旺号，
# 拿到之后可以直接打开指定店铺的会话，不必再走详情页点击——
# 详情页有验证码墙，而 IM 页没有，这条捷径是实测出来的。
IM_BASE = ("https://air.1688.com/app/ocms-fusion-components-1688/"
           "def_cbu_web_im/index.html")
_CORE_MARK = "def_cbu_web_im_core"      # 真正的聊天面板在这个 iframe 里

# 全部候选选择器：按优先级排列，命中即用（改版时在此补充，不必改逻辑）
SELECTORS = {
    # 详情页唤起旺旺的入口（实测：右侧栏 a.action-item「客服」，顶上 a.action-link）
    "contact": [
        "a.action-item:has-text('客服')",
        "a.action-link.customer-service",
        "a:has-text('在线洽谈')",
        "a:has-text('旺旺')",
        "button:has-text('联系供应商')",
    ],
    # 聊天输入框：必须限定在 .editBox 内且 contenteditable=true。
    # 已发送的我方消息本身是 <pre class="edit" contenteditable="false">，
    # 只写 .edit 会命中历史消息，于是「输入框还留着刚发的字」的误判会引发重复发送。
    "input": ['.editBox .edit[contenteditable="true"]'],
    # 发送键：内容为空时带 send-btn--empty（置灰，点了不生效）
    "send": [".send-btn:not(.send-btn--empty)", ".send-btn"],
    # 消息条目：方向靠 .message-item 上的 self 类判定（实测确认）
    "message": ".message-item",
}

_ANTI_BOT = ("验证码", "滑块", "拖动滑块", "请按照说明进行验证", "安全验证",
             "访问异常", "请完成验证")

# 抓取会话里的全部消息，返回 [{text, self, kind, nick}]。
# 只取 .content 内的文本：外层还混着昵称、时间、「未读」标记，一起取会把噪声带进解析。
_READ_JS = r"""
() => {
  const out = [];
  document.querySelectorAll('.message-item').forEach(it => {
    const contentEl = it.querySelector('.content');
    const raw = contentEl ? (contentEl.innerText || '') : '';
    const text = raw.replace(/[ \t]+\n/g, '\n').trim();
    const hasImg = !!(contentEl && contentEl.querySelector('img'));
    if (!text && !hasImg) return;
    const nickEl = it.querySelector('.nick');
    out.push({
      text: text || '[图片]',
      self: it.classList.contains('self'),
      kind: (!text && hasImg) ? 'image' : 'text',
      nick: nickEl ? (nickEl.innerText || '').trim() : '',
    });
  });
  return out;
}
"""


class ChatError(Exception):
    """通道级异常。need_verify / not_logged_in 表示需要人工介入。"""

    def __init__(self, msg: str, kind: str = "error"):
        super().__init__(msg)
        self.kind = kind


class ChatChannelBase:
    """通道接口。open 之后才能 send / read。"""

    name = "base"

    def open(self, offer_url: str) -> None:
        raise NotImplementedError

    def send(self, text: str) -> None:
        raise NotImplementedError

    def read(self) -> list[dict]:
        """返回聊天面板里的全量气泡 [{text, self, kind}]。"""
        raise NotImplementedError

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# 真实通道：CDP 接管浏览器
# ---------------------------------------------------------------------------
def _offer_id(url: str) -> str:
    m = re.search(r"offer/(\d+)", url or "")
    return m.group(1) if m else ""


def _im_url(touid: str, offer_id: str = "") -> str:
    q = f"touid={quote(touid)}&siteid=cnalichn"
    if offer_id:
        q += f"&offerId={offer_id}"
    return f"{IM_BASE}?{q}#/"


def _touid_from_url(url: str) -> str:
    m = re.search(r"[?&]touid=([^&#]+)", url or "")
    return unquote(m.group(1)) if m else ""


class CDPChatChannel(ChatChannelBase):
    """一个会话对应一个标签页；用完关闭，避免长跑把浏览器堆满 tab。

    两条打开路径：
    - 已知旺旺号（Shop.aliww）→ 直接打开 IM 地址。实测无验证码，最快最稳。
    - 未知 → 打开详情页点「客服」，捕获新开的 IM 标签页，从 URL 里取出 touid。
      详情页有验证码墙，命中时抛 need_verify 交给人工，不尝试绕过。
    """

    name = "cdp"

    def __init__(self, cdp_url: Optional[str] = None, headless: bool = False,
                 shot_dir: Optional[Path] = None, touid: Optional[str] = None):
        from app.browser.cdp import CDP_URL
        self.cdp_url = cdp_url or os.getenv("CDP_URL", CDP_URL)
        self.headless = headless
        self.shot_dir = shot_dir
        self.touid = (touid or "").strip()
        self.offer_url = ""
        self._pw = None
        self._browser = None
        self._page = None
        self._frame = None

    # -- 生命周期 ---------------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _ensure_page(self):
        if self._page is not None:
            return self._page
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
        except Exception as e:
            self._pw.stop()
            self._pw = None
            raise ChatError(f"连接抓取浏览器失败（先运行 scripts/start_browser.py）：{e}",
                            kind="no_browser") from e
        ctx = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        self._page = ctx.new_page()
        self._page.set_default_timeout(15000)
        return self._page

    def open(self, offer_url: str) -> None:
        """打开该商品对应的旺旺会话；已知道旺旺号时跳过详情页。"""
        self.offer_url = offer_url or ""
        page = self._ensure_page()
        if self.touid:
            page.goto(_im_url(self.touid, _offer_id(offer_url)),
                      wait_until="domcontentloaded", timeout=45000)
            time.sleep(random.uniform(1.5, 2.5))
            if not self._core_frame(page, timeout=25):
                # 带上现场：光说「找不到面板」无法判断是旺旺号错、登录失效还是改版
                raise ChatError(
                    f"IM 页面没加载出聊天面板（touid={self.touid}，"
                    f"标题={page.title()!r}，frame={len(page.frames)}，"
                    f"url={page.url[:80]}）",
                    kind="no_input")
            return
        self._open_via_detail(page, offer_url)

    def _guard(self, page) -> None:
        """登录/验证码检测。命中就抛需人工处理的错误，绝不尝试自动过验证。"""
        title = page.title() or ""
        try:
            body = page.inner_text("body")[:2000]
        except Exception:
            body = ""
        if ("_____tmd_____" in page.url or "/punish" in page.url
                or any(k in title for k in _ANTI_BOT) or any(k in body for k in _ANTI_BOT)):
            raise ChatError("详情页命中人机验证，需要人工处理一次"
                            "（scripts/live_trial.py 会停下来等你过滑块）",
                            kind="need_verify")
        if "扫码登录" in body or ("登录" in title and "阿里巴巴" not in title):
            raise ChatError("浏览器未登录 1688", kind="not_logged_in")

    def _open_via_detail(self, page, offer_url: str) -> None:
        page.goto(offer_url, wait_until="domcontentloaded", timeout=45000)
        time.sleep(random.uniform(1.5, 2.5))
        self._guard(page)

        ctx = page.context
        for sel in SELECTORS["contact"]:
            try:
                loc = page.locator(sel).first
                if loc.count() == 0 or not loc.is_visible():
                    continue
            except Exception:
                continue
            newp = None
            try:
                with ctx.expect_page(timeout=9000) as pi:
                    loc.click(timeout=6000)
                newp = pi.value
            except Exception:
                newp = None            # 有些页面是内嵌浮层，不开新标签
            time.sleep(2.0)

            if newp is not None:
                touid = _touid_from_url(newp.url)
                if touid and self._core_frame(newp, timeout=18):
                    self.touid = touid
                    self._page, self._frame = newp, None
                    try:
                        page.close()      # 详情页用完就关，少一个 tab
                    except Exception:
                        pass
                    return
                try:
                    newp.close()
                except Exception:
                    pass
            if self._core_frame(page, timeout=6):
                return
        raise ChatError("没找到旺旺会话入口（页面可能改版，跑 probe_chat.py 校准）",
                        kind="no_entry")

    def _core_frame(self, page, timeout: float = 15):
        """等聊天面板 iframe 就绪并露出输入框。"""
        deadline = time.time() + timeout
        dbg = bool(os.getenv("CHAT_DEBUG"))
        while time.time() < deadline:
            for f in page.frames:
                if _CORE_MARK not in (f.url or ""):
                    continue
                try:
                    n = f.locator(SELECTORS["input"][0]).count()
                    if dbg:
                        print(f"[core_frame] count={n} {f.url[:60]}", flush=True)
                    if n > 0:
                        self._frame = f
                        return f
                except Exception as e:
                    if dbg:
                        print(f"[core_frame] 异常 {type(e).__name__}: {str(e)[:100]}",
                              flush=True)
                    continue
            # 必须用 wait_for_timeout 而不是 time.sleep：同步 API 只有在执行命令时
            # 才会处理协议事件，纯 sleep 期间新出现的 iframe 不会被感知，
            # frames 列表会一直停在旧快照上（实测卡满 25 秒仍是 2 个 frame）。
            page.wait_for_timeout(500)
        if dbg:
            print(f"[core_frame] 超时，frames={len(page.frames)}", flush=True)
        return None

    def _im_frame(self):
        if self._frame is not None:
            return self._frame
        fr = self._core_frame(self._page, timeout=10)
        if fr is None:
            raise ChatError("聊天面板里找不到输入框", kind="no_input")
        return fr

    # -- 发送 -----------------------------------------------------------
    def _self_count(self, frame) -> int:
        return frame.evaluate(
            "() => document.querySelectorAll('.message-item.self').length")

    def send(self, text: str) -> None:
        """拟人输入并发送；发完必须能读到，读不到就算失败。"""
        if self._page is None:
            raise ChatError("会话尚未打开", kind="not_open")
        text = _one_line(text)
        if not text:
            raise ChatError("空消息不发送", kind="empty")
        frame = self._im_frame()

        try:
            box = frame.locator(SELECTORS["input"][0]).first
            box.click(timeout=6000)
            # 清掉可能残留的草稿：不清会拼在新消息前面发出去。
            # 键盘事件挂在 page 上（Frame 没有 keyboard 属性），
            # 焦点在 iframe 内时 page.keyboard 依然作用于该元素。
            self._page.keyboard.press("Control+A")
            self._page.keyboard.press("Delete")
            time.sleep(0.2)

            delay = random.randint(26, 65)
            for chunk in _split_for_typing(text):
                box.type(chunk, delay=delay)
                time.sleep(random.uniform(0.05, 0.18))
            time.sleep(random.uniform(0.4, 0.9))

            typed = (box.inner_text() or "").strip()
            if typed[:6] != text[:6]:
                raise ChatError(f"输入框内容与预期不符（实际：{typed[:20]}）",
                                kind="send_failed")

            before = self._self_count(frame)
            self._submit(frame, text)
            if not self._wait_delivered(frame, text, before):
                raise ChatError("消息发出后没在会话里读到，可能未真正送达",
                                kind="send_failed")
        except ChatError:
            raise
        except Exception as e:
            raise ChatError(f"发送失败：{type(e).__name__}: {e}", kind="send_failed") from e

    def _submit(self, frame, text: str) -> None:
        """优先点发送键；置灰（页面没识别到输入）时退回回车。"""
        for sel in SELECTORS["send"]:
            try:
                btn = frame.locator(sel).first
                if btn.count() > 0 and btn.is_visible():
                    btn.click(timeout=4000)
                    return
            except Exception:
                continue
        self._page.keyboard.press("Enter")

    def _wait_delivered(self, frame, text: str, before: int,
                        timeout: float = 12.0) -> bool:
        """以「会话里真的出现这条」为准，而不是以「没报错」为准。"""
        deadline = time.time() + timeout
        head = text[:8]
        while time.time() < deadline:
            try:
                if self._self_count(frame) > before:
                    last = frame.evaluate(
                        "() => { const n=[...document.querySelectorAll('.message-item.self')];"
                        " const e=n[n.length-1]; const c=e&&e.querySelector('.content');"
                        " return c ? (c.innerText||'').trim() : ''; }")
                    if head in (last or ""):
                        return True
            except Exception:
                pass
            time.sleep(0.8)
        return False

    # -- 读取 -----------------------------------------------------------
    def read(self) -> list[dict]:
        if self._page is None:
            raise ChatError("会话尚未打开", kind="not_open")
        frame = self._im_frame()
        try:
            raw = frame.evaluate(_READ_JS)
        except Exception as e:
            raise ChatError(f"读取会话失败：{type(e).__name__}", kind="read_failed") from e
        out = []
        for m in raw or []:
            text = (m.get("text") or "").strip()
            if text:
                out.append({"text": text, "self": bool(m.get("self")),
                            "kind": m.get("kind") or "text"})
        return out

    # -- 留证 -----------------------------------------------------------
    def screenshot(self, tag: str = "") -> str:
        """截图存档（失败复盘 + 演示素材），返回相对路径。"""
        if self._page is None or not self.shot_dir:
            return ""
        try:
            self.shot_dir.mkdir(parents=True, exist_ok=True)
            name = f"{datetime_stamp()}-{tag or uuid.uuid4().hex[:6]}.png"
            path = self.shot_dir / name
            self._page.screenshot(path=str(path), full_page=False)
            return f"/uploads/chat/{name}"
        except Exception:
            return ""

    def close(self) -> None:
        try:
            if self._page is not None:
                self._page.close()
        except Exception:
            pass
        self._page = None
        self._frame = None
        try:
            if self._pw is not None:
                self._pw.stop()   # 只停自己的 driver，不动用户的浏览器
        except Exception:
            pass
        self._pw = None
        self._browser = None


def datetime_stamp() -> str:
    from datetime import datetime
    return datetime.now().strftime("%H%M%S")


def _one_line(text: str) -> str:
    """强制单行。

    旺旺的输入框里换行等于回车=立即发送，一段多行文本会被拆成好几条消息
    分别轰出去。实测踩过：模型输出的一段 Markdown 分析被拆成 5 条发给卖家。
    所以外发前必须压平，任何 \\n 都不能留。
    """
    t = re.sub(r"\s*\n+\s*", " ", text or "")
    return re.sub(r"[ \t]{2,}", " ", t).strip()


def _split_for_typing(text: str) -> list[str]:
    """按标点切段，模拟真人边想边打。"""
    parts = re.split(r"(?<=[，。！？、；,])", _one_line(text))
    return [p for p in parts if p] or [_one_line(text)]


# ---------------------------------------------------------------------------
# 模拟通道：按剧本回复的假卖家
# ---------------------------------------------------------------------------
_DEFAULT_SCRIPT = {
    # 轮次（我方第几次发言后卖家回什么）。缺失则用兜底话术。
    # 刻意模拟真实卖家的节奏：先只回价格和起订量（信息不全 → 触发追问），
    # 第二轮把税/运费/时效补齐（缺口清零 → 触发议价），第三轮让一口价（收敛）。
    1: "老板在的，这款有现货，{price} 元一件，200 件起批，量大可以谈。",
    2: "含税的，这个价不含运费，运费看地区，一般省内 30 左右。今天下单 48 小时内发货。",
    3: "你这个量的话 {cut} 元可以给你，再低真做不了，我们也是实价了。",
}


# 模拟通道的对话状态：每次推进都会新建通道对象，所以会话内容必须放在进程级
# 缓存里，否则「上一轮卖家说了什么」在下一轮就丢了（真实通道靠页面 DOM 保留）。
# 同时落盘一份，服务重启后仍能续上——否则 mock 会话会永久卡在「等回复」。
_MOCK_STATE: dict[str, list[dict]] = {}


def _turns_path(root: Path, session_id: str) -> Path:
    return root / f"{session_id}.turns.json"


def _load_turns(root: Path, session_id: str) -> list[dict]:
    cached = _MOCK_STATE.get(session_id)
    if cached:
        return cached
    path = _turns_path(root, session_id)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def _save_turns(root: Path, session_id: str, turns: list[dict]) -> None:
    try:
        root.mkdir(parents=True, exist_ok=True)
        _turns_path(root, session_id).write_text(
            json.dumps(turns, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


class MockChatChannel(ChatChannelBase):
    """离线假卖家：按我方已发轮次返回预设回复。

    剧本文件：data/mock_chats/<session_id>.json
        {"price": 56, "cut": 54, "replies": {"1": "...", "2": "..."}}
    没剧本时用内置默认剧本，模拟「先只回价格 → 补全信息 → 让一口价」的
    真实卖家节奏，用来验证追问与议价是否真的被触发。
    """

    name = "mock"

    def __init__(self, session_id: str, root: Optional[Path] = None):
        self.session_id = session_id
        self.root = root or (Path(__file__).resolve().parents[2] / "data" / "mock_chats")
        self.turns: list[dict] = _load_turns(self.root, session_id)
        _MOCK_STATE[session_id] = self.turns
        self.buyer_count = sum(1 for t in self.turns if t.get("self"))

    def _script(self) -> dict:
        path = self.root / f"{self.session_id}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        # 无剧本时按 session_id 派生一个稳定报价：各家不同价，才能看出
        # 「拿市场最低价去压高价那家」是真的在发生，而不是所有店报同一个数
        import zlib
        crc = zlib.crc32(self.session_id.encode("utf-8"))
        price = 50 + crc % 14          # 50~63 元
        return {"price": price, "cut": round(price * 0.96, 1)}

    def open(self, offer_url: str) -> None:
        # 不重置历史：模拟「同一个会话窗口还开着」。
        # 从磁盘恢复，保证服务重启后 mock 会话不会永久卡在「等回复」。
        self.turns = _load_turns(self.root, self.session_id)
        _MOCK_STATE[self.session_id] = self.turns
        self.buyer_count = sum(1 for t in self.turns if t.get("self"))

    def send(self, text: str) -> None:
        self.buyer_count += 1
        self.turns.append({"text": text, "self": True, "kind": "text"})
        script = self._script()
        replies = script.get("replies") or _DEFAULT_SCRIPT
        # 剧本文本里的 key 可能是 int 也可能是 str，两种都认
        reply = replies.get(str(self.buyer_count)) or replies.get(self.buyer_count)
        if reply is not None:
            price = script.get("price", 56)
            cut = script.get("cut", round(float(price) * 0.96, 1))
            self.turns.append({
                "text": str(reply).format(price=price, cut=cut),
                "self": False, "kind": "text",
            })
        _save_turns(self.root, self.session_id, self.turns)

    def read(self) -> list[dict]:
        # 未回的消息不重复返回：调用方按「新尾部」对齐，这里每次返回全量即可
        return list(self.turns)

    def screenshot(self, tag: str = "") -> str:
        return ""


def get_channel(session_id: str, mode: Optional[str] = None,
                shot_dir: Optional[Path] = None,
                touid: Optional[str] = None) -> ChatChannelBase:
    """按配置返回通道。INQUIRY_CHANNEL=mock 时走离线剧本。

    touid 是店铺旺旺号：已知就直连 IM 地址（无验证码），未知则由通道去详情页发现。
    """
    mode = (mode or os.getenv("INQUIRY_CHANNEL", "") or "").lower()
    if mode == "mock":
        return MockChatChannel(session_id)
    return CDPChatChannel(shot_dir=shot_dir, touid=touid)
