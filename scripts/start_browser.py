"""启动带 CDP 调试端口的浏览器（Edge/Chrome 自动探测），供智能体接管抓取。

用法：python scripts/start_browser.py
- 独立 user-data-dir（data/browser_profile），不影响日常浏览器，也不要求先关掉它
- 9222 端口已被占用时视为已在运行，直接提示
- 首次启动需要在打开的窗口里扫码登录一次 1688（登录态会保存在独立目录里）
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / "data" / "browser_profile"
CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"

CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def _port_open(port: int, timeout: float = 1.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_browser() -> str:
    for p in CANDIDATES:
        if Path(p).exists():
            return p
    print("未找到 Chrome/Edge，请把浏览器路径填进 scripts/start_browser.py 的 CANDIDATES")
    sys.exit(1)


def main() -> None:
    hold = "--hold" in sys.argv   # 由 run_in_background 调用时挂住进程树，防止浏览器被回收
    try:
        import httpx

        r = httpx.get(f"{CDP_URL}/json/version", timeout=2, trust_env=False)
        if r.status_code == 200:
            print(f"抓取浏览器已在运行（CDP {CDP_URL}），无需重复启动。")
            return
    except Exception:
        pass

    exe = find_browser()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([
        exe,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
        "https://www.1688.com",
    ])
    # 等端口就绪
    for _ in range(30):
        if _port_open(CDP_PORT):
            break
        time.sleep(0.5)
    if _port_open(CDP_PORT):
        print(f"浏览器已启动，CDP 端口 {CDP_PORT} 就绪。")
        print("首次使用请在这个新窗口里扫码登录一次 1688（登录态会记住）。")
        print("之后就可以对智能体说「帮我找这款的候选货源」了。")
        if hold:
            try:
                while True:
                    time.sleep(60)   # 挂住父进程，浏览器随后台任务存活
            except KeyboardInterrupt:
                pass
    else:
        print("浏览器已启动，但 CDP 端口未就绪，请重试或检查浏览器路径。")


if __name__ == "__main__":
    main()
