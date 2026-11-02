# -*- coding: utf-8 -*-
"""问渠 Springhead 一键启动器。

用法（默认 start）：
    python scripts/start_all.py            # 启动/复用 服务 + 抓取浏览器，并打开看板
    python scripts/start_all.py status     # 查看服务与浏览器状态
    python scripts/start_all.py stop       # 停掉本脚本启动的看板服务（浏览器不关）

职责：
1. 看板服务（uvicorn 8000）：已在运行则复用（8000 常被用户服务占用，不重复起）；
   否则后台拉起，日志写 data/uvicorn.log，PID 记 data/uvicorn.pid。
2. 抓取浏览器（CDP 9222）：复用 scripts/start_browser.py 的探测与启动逻辑。
3. 自动打开看板页面。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = ROOT / "data" / "uvicorn.log"
PID_FILE = ROOT / "data" / "uvicorn.pid"
APP_PORT = 8000
CDP_PORT = 9222
APP_URL = f"http://127.0.0.1:{APP_PORT}/"


def _port_open(port: int, timeout: float = 1.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    """直连探测（绕过系统代理，避免 127.0.0.1 被 HTTP_PROXY 劫走返回 502）。"""
    try:
        import urllib.request
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as r:
            return 200 <= r.status < 500
    except Exception:
        return False


def app_healthy() -> bool:
    return _http_ok(f"{APP_URL}api/targets")


def start_app() -> str:
    """返回 'running'（已在跑）或 'started'（本次拉起）。"""
    if _port_open(APP_PORT):
        if app_healthy():
            print(f"[1/3] 看板服务已在运行（端口 {APP_PORT}），直接复用。")
            return "running"
        print(f"[1/3] 端口 {APP_PORT} 被其他程序占用且不是本看板，跳过启动（请自行处理端口）。")
        return "foreign"

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log_fp = open(LOG_FILE, "ab")
    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        # 新控制台 + 最小化：独立进程组最抗杀（不随启动器退出被连带回收），
        # 任务栏留一个最小化窗口，关掉它即停服务，行为直观。
        creationflags = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 1   # SW_SHOWNORMAL=1, SW_SHOWMINNOACTIVE=7
        startupinfo.wShowWindow = 7   # 最小化且不抢焦点
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(APP_PORT)],
        cwd=str(ROOT), stdout=log_fp, stderr=log_fp,
        creationflags=creationflags, startupinfo=startupinfo,
    )
    PID_FILE.write_text(str(proc.pid), encoding="ascii")
    for _ in range(40):          # 最多等 20 秒
        if app_healthy():
            print(f"[1/3] 看板服务已启动（PID {proc.pid}，端口 {APP_PORT}，日志 data/uvicorn.log）。")
            return "started"
        time.sleep(0.5)
    print(f"[1/3] 服务启动超时，请查看日志：{LOG_FILE}")
    return "timeout"


def start_browser() -> str:
    if _port_open(CDP_PORT):
        print(f"[2/3] 抓取浏览器已在运行（CDP 端口 {CDP_PORT}），直接复用。")
        return "running"
    print("[2/3] 正在启动抓取浏览器（Edge/Chrome + 独立登录目录）…")
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "start_browser.py")],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    out = (r.stdout or "").strip().splitlines()
    for line in out:
        print("      " + line)
    if _port_open(CDP_PORT):
        return "started"
    print("      浏览器未就绪（智能体自动导入功能暂不可用，看板其他功能不受影响）。")
    return "failed"


def open_dashboard() -> None:
    print(f"[3/3] 打开看板：{APP_URL}")
    webbrowser.open(APP_URL)


def do_status() -> None:
    app = "运行中" if app_healthy() else ("端口被占" if _port_open(APP_PORT) else "未启动")
    cdp = "运行中" if _port_open(CDP_PORT) else "未启动"
    pid = PID_FILE.read_text(encoding="ascii").strip() if PID_FILE.exists() else "—"
    print(f"看板服务({APP_PORT})：{app}   抓取浏览器(CDP {CDP_PORT})：{cdp}   记录的PID：{pid}")


def do_stop() -> None:
    if not PID_FILE.exists():
        print("没有由 start_all 启动的服务记录（手动起的服务请自行关闭端口 8000 进程）。")
        return
    pid = PID_FILE.read_text(encoding="ascii").strip()
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
        else:
            subprocess.run(["kill", pid], capture_output=True)
        print(f"已停止看板服务（PID {pid}）。")
    except Exception as e:
        print(f"停止失败：{e}")
    PID_FILE.unlink(missing_ok=True)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "start"
    if mode == "status":
        do_status()
    elif mode == "stop":
        do_stop()
    else:
        start_app()
        start_browser()
        open_dashboard()
        print("完成。双击 start.bat 即可随时再启。")


if __name__ == "__main__":
    main()
