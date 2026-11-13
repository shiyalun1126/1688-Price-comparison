"""校准工具：真实 1688 上，旺旺会话到底长什么样。

自动谈判的发送/读取全依赖页面结构。1688 改版比翻书还勤，与其猜选择器，
不如把真实链路跑一遍并打印每一步的判定依据。

链路（实测）：
    详情页 ──点「客服」──> 新标签页 air.1688.com/.../def_cbu_web_im/
                            └─ iframe def_cbu_web_im_core（真正的聊天面板）
拿到 touid（卖家旺旺号）后可以直连 IM 地址，不必再走详情页——
详情页有验证码墙，IM 页没有。

用法：
    D:/miniconda3/python.exe scripts/probe_chat.py https://detail.1688.com/offer/xxx.html
    D:/miniconda3/python.exe scripts/probe_chat.py --touid cnalichn某某公司 --offer 123456789
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.browser.chat import (  # noqa: E402
    _ANTI_BOT,
    SELECTORS,
    CDPChatChannel,
    ChatError,
    _CORE_MARK,
    _im_url,
    _offer_id,
)

OUT = Path(__file__).resolve().parents[1] / "data" / "probe"


def _captcha(page) -> bool:
    try:
        title = page.title() or ""
        body = page.inner_text("body")[:800]
        return ("_____tmd_____" in page.url or "/punish" in page.url
                or any(k in title for k in _ANTI_BOT)
                or any(k in body for k in _ANTI_BOT))
    except Exception:
        return False


def _dump_frames(page) -> None:
    print("\n[frame 列表]")
    for i, f in enumerate(page.frames):
        try:
            ce = f.locator(SELECTORS["input"][0]).count()
        except Exception:
            ce = -1
        mark = " ← 聊天面板" if _CORE_MARK in (f.url or "") else ""
        print(f"  [{i}] 输入框={ce}  {f.url[:100]}{mark}")


def probe(url: str, touid: str = "") -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ch = CDPChatChannel(shot_dir=OUT, touid=touid)
    try:
        if touid:
            print(f"直连 IM（touid={touid}）")
            _probe_im(ch, url)
            return 0

        page = ch._ensure_page()
        print(f"打开详情页：{url}")
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        time.sleep(3)
        print(f"标题：{page.title()}")
        page.screenshot(path=str(OUT / "01-detail.png"))
        if _captcha(page):
            print("\n命中人机验证。先跑 scripts/live_trial.py 过滑块，"
                  "或直接在有旺旺号时用 --touid 直连 IM。")
            return 2

        print("\n[联系入口候选]")
        for sel in SELECTORS["contact"]:
            try:
                loc = page.locator(sel)
                n = loc.count()
                vis = sum(1 for i in range(min(n, 5)) if loc.nth(i).is_visible())
                print(f"  {'OK ' if n and vis else '   '}{sel:34} count={n} visible={vis}")
            except Exception as e:
                print(f"  ERR {sel:34} {type(e).__name__}")

        print("\n点击联系入口，捕获是否会新开 IM 标签页…")
        ch._open_via_detail(page, url)
        print(f"当前会话页：{ch._page.url[:100]}")
        print(f"取到旺旺号：{ch.touid or '（未取到，可能不是新标签页模式）'}")
        _dump_frames(ch._page)
        _probe_im(ch, url, already_open=True)
        return 0
    except ChatError as e:
        print(f"\n通道错误（{e.kind}）：{e}")
        return 3
    finally:
        try:
            ch.close()
        except Exception:
            pass


def _probe_im(ch: CDPChatChannel, url: str, already_open: bool = False) -> None:
    if not already_open:
        ch.open(url)
    frame = ch._im_frame()
    print(f"\n聊天面板 frame：{frame.url[:100]}")
    print(f"输入框：{SELECTORS['input'][0]} → 命中")

    msgs = ch.read()
    print(f"\n[会话消息 {len(msgs)} 条]")
    for m in msgs[-10:]:
        who = "我方" if m.get("self") else "卖家"
        print(f"  [{who}/{m.get('kind')}] {m['text'][:80]}")
    if not msgs:
        print("  （空会话，正常——还没人说过话）")
    ch.screenshot(tag="probe-im")
    print(f"\n截图：{OUT}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", default="")
    ap.add_argument("--touid", default="", help="卖家旺旺号，已知时直连 IM 跳过详情页")
    ap.add_argument("--offer", default="", help="配合 --touid 使用的商品 id")
    args = ap.parse_args()

    if args.touid and not args.url:
        print(f"IM 地址：{_im_url(args.touid, args.offer)}")
        return probe(_im_url(args.touid, args.offer), touid=args.touid)
    if not args.url:
        print(__doc__)
        return 1
    return probe(args.url, touid=args.touid)


if __name__ == "__main__":
    raise SystemExit(main())
