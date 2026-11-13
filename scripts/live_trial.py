"""真实环境试跑：只对单家店铺跑一遍「打开会话 → 读消息 →（可选）发一条」。

与 api/engine 的区别：这里显式逐步骤执行并打印每一步的判定依据，
适合第一次接真实账号时校验，也适合排查某一家店为什么发不出去。

设计上把「人机验证」当成流程的一个状态而不是异常：
命中滑块时不会自动绕过，而是提示人工在浏览器窗口里过一次。
过一次之后 x5sec cookie 会留在 profile 里，后续详情页就能正常打开；
更进一步，拿到 touid 后可以直连 IM 页面，连详情页都不必再碰。

用法：
    # 干跑（不发消息，只读会话）
    D:/miniconda3/python.exe scripts/live_trial.py --url https://detail.1688.com/offer/xxx.html
    D:/miniconda3/python.exe scripts/live_trial.py --product <product_id>

    # 已知道旺旺号时直连（最快，且避开验证码）
    D:/miniconda3/python.exe scripts/live_trial.py --touid cnalichn某某公司 --offer 123456789

    # 真发一条（第一次务必只发这一条，确认无误再放开批量）
    D:/miniconda3/python.exe scripts/live_trial.py --product <id> --send "老板，这款什么价？"
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.browser.chat import (  # noqa: E402
    _ANTI_BOT,
    CDPChatChannel,
    ChatError,
    _CORE_MARK,
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


def _wait_human_verify(page, seconds: int) -> bool:
    """命中验证时停在这里等人过，过完返回 True。绝不自动破解。"""
    print("\n" + "=" * 62)
    print("  命中人机验证，需要人工处理")
    print(f"  请切到已打开的浏览器窗口，按提示完成滑块（最多等 {seconds} 秒）")
    print("  这一步故意不自动化：绕过验证码既不可靠，也会加重账号风险")
    print("=" * 62)
    deadline = time.time() + seconds
    last = 0
    while time.time() < deadline:
        time.sleep(2)
        if not _captcha(page):
            print("\n  验证已通过，继续。")
            time.sleep(3)
            return True
        if time.time() - last > 30:
            last = time.time()
            print(f"  仍在等待…（剩 {int(deadline - time.time())} 秒）")
    print("\n  等待超时：验证未通过，本次试跑终止。")
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="")
    ap.add_argument("--product", default="", help="库里商品 id，自动取其 1688 链接")
    ap.add_argument("--touid", default="", help="卖家旺旺号；给了就直连 IM，跳过详情页")
    ap.add_argument("--offer", default="", help="配合 --touid 的商品 id")
    ap.add_argument("--send", default="", help="要发出去的那句话；留空则只读不写")
    ap.add_argument("--wait-verify", type=int, default=600)
    args = ap.parse_args()

    url = args.url
    if not url and args.product:
        from app.store.db import Store
        st = Store(Path(__file__).resolve().parents[1] / "data" / "app.db")
        p = st.get_product(args.product)
        st.close()
        if not p:
            print(f"商品不存在：{args.product}")
            return 1
        url = p.url
        print(f"商品：{p.title[:50]}")
    if not (url or "").startswith("http"):
        print(__doc__)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    ch = CDPChatChannel(shot_dir=OUT, touid=args.touid)
    try:
        try:
            ch.open(url)
        except ChatError as e:
            if e.kind != "need_verify":
                raise
            if not _wait_human_verify(ch._page, args.wait_verify):
                return 3
            ch.open(url)          # 过完验证重试，此时 cookie 已生效

        print(f"\n会话页：{ch._page.url[:90]}")
        print(f"旺旺号：{ch.touid or '（未取到）'}")
        fr = ch._im_frame()
        print(f"聊天面板：{'已就绪' if _CORE_MARK in fr.url else fr.url[:80]}")

        msgs = ch.read()
        print(f"\n[会话已有 {len(msgs)} 条]")
        for m in msgs[-10:]:
            who = "我方" if m.get("self") else "卖家"
            print(f"  [{who}/{m.get('kind')}] {m['text'][:78]}")
        if not msgs:
            print("  （空会话，还没人说过话）")

        if not args.send:
            ch.screenshot(tag="trial-read")
            print("\n干跑结束（未发送任何消息）。加 --send \"…\" 才会真正发出去。")
            print(f"截图：{OUT}")
            return 0

        print(f"\n发送：{args.send}")
        ch.send(args.send)
        shot = ch.screenshot(tag="trial-sent")
        after = ch.read()
        print(f"\n[发送后 {len(after)} 条，新增 {len(after) - len(msgs)}]")
        for m in after[-4:]:
            who = "我方" if m.get("self") else "卖家"
            print(f"  [{who}] {m['text'][:78]}")
        print(f"\n✅ 已在会话里读到刚发的消息。截图：{shot or OUT}")
        return 0
    except ChatError as e:
        print(f"\n通道错误（{e.kind}）：{e}")
        if ch._page is not None:
            ch.screenshot(tag=f"trial-error-{e.kind}")
        return 2
    finally:
        try:
            ch.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
