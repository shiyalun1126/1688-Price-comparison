"""截取某个自动谈判会话的抽屉视图（多轮对话 + 缺口 + 阶段），用于复盘/汇报。

用法：
    D:/miniconda3/python.exe scripts/shot_session.py s-78b260b3fcb0
    SHOT_URL=http://127.0.0.1:8010/ ...            # 截演示实例
    SHOT_OUT=docs/screenshots/demo-xxx.png ...     # 指定输出
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = os.getenv("SHOT_URL") or "http://127.0.0.1:8000/"


def main() -> int:
    sid = sys.argv[1] if len(sys.argv) > 1 else ""
    out = Path(os.getenv("SHOT_OUT") or
               (ROOT / "docs" / "screenshots" / f"session-{sid or 'latest'}.png"))
    out.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1600, "height": 1000})
        page.goto(URL, wait_until="networkidle")
        page.wait_for_timeout(1200)
        # 切到第一个目标款（会话挂在目标款下）
        try:
            page.click("#btn-switch")
            page.wait_for_timeout(400)
            page.locator("#switcher-options > *").first.click()
            page.wait_for_timeout(2500)
        except Exception:
            pass
        page.evaluate(f"openSession('{sid}')")
        page.wait_for_timeout(2500)
        drawer = page.locator("#session-drawer")
        if drawer.count() and drawer.is_visible():
            drawer.screenshot(path=str(out))
        else:
            page.screenshot(path=str(out), full_page=True)
        # 同时在列表里留一张，说明这条会话在界面上的状态
        page.evaluate("closeSession()")
        page.wait_for_timeout(800)
        page.screenshot(path=str(ROOT / "docs" / "screenshots" / "multiround-list.png"),
                        full_page=True)
        b.close()
    print(f"saved: {out}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
