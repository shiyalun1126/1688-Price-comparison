# -*- coding: utf-8 -*-
"""把本地 main 对齐到 GitHub 远程 main（当前走 Git Data API 推送，commit sha 与本地不同）。

背景：仓库主站在本机网络被阻断，只能经 api.github.com 的 Git Data API 推送。
API 重建 commit 时用的是 UTC 日期、且 message 不带尾部换行，因此远程 commit
对象与本地那份字节不同 → sha 不同 → 本地 git 认定与远程「分叉」，以后
普通 git push 会被拒（non-fast-forward）。

这个脚本把远程那条链的 commit 对象在本地重建出来（tree/blob 本地都有，
只需补 commit 封装），然后把 refs/heads/main 指过去，本地即与远程完全一致。
只动本地 refs，不改远程；重建后先校验 tree 与工作区一致再更新引用。
"""
import json
import subprocess
import sys
import time
from calendar import timegm
from datetime import datetime

import httpx

REPO = "shiyalun1126/1688-Price-comparison"
API = f"https://api.github.com/repos/{REPO}"

# gh api 在本机网络下会 404（它的 HTTP 栈走的代理与 httpx 不同），
# 直接用 httpx 打 api.github.com，token 从 gh 本地取。
_TOKEN = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
_CLIENT = httpx.Client(
    timeout=httpx.Timeout(60.0),
    headers={"Authorization": f"Bearer {_TOKEN}", "Accept": "application/vnd.github+json"},
)


def gh(path):
    # 代理偶尔在 TLS 握手时掐断（SSL EOF），重试几次即可
    last = None
    for attempt in range(5):
        try:
            r = _CLIENT.get(f"{API}{path}")
        except Exception as e:          # 连接层抖动
            last = e
            time.sleep(1 + attempt)
            continue
        if r.status_code >= 300:
            raise RuntimeError(f"GET {path} -> {r.status_code}: {r.text[:200]}")
        return r.json()
    raise RuntimeError(f"GET {path} 反复失败: {last}")


def git(*args, inp=None):
    # 必须按 bytes 送 stdin：Windows 的文本模式会把 \n 写成 \r\n，
    # 而 commit 对象的字节必须和 Git 生成的完全一致，否则 sha 对不上。
    data = inp.encode("utf-8") if isinstance(inp, str) else inp
    r = subprocess.run(["git", *args], input=data, capture_output=True)
    r.stdout = r.stdout.decode("utf-8", "replace")
    r.stderr = r.stderr.decode("utf-8", "replace")
    return r


def has_object(sha):
    r = git("cat-file", "-t", sha)
    return r.returncode == 0


def epoch_of(iso):
    """GitHub 的 ISO 时间 → (epoch, '+0000')。"""
    dt = datetime.strptime(iso.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
    return timegm(dt.utctimetuple()), "+0000"


def build_commit(c, tz):
    lines = [f"tree {c['tree']['sha']}"]
    for p in c["parents"]:
        lines.append(f"parent {p['sha']}")
    a_epoch, _ = epoch_of(c["author"]["date"])
    c_epoch, _ = epoch_of(c["committer"]["date"])
    lines.append(f"author {c['author']['name']} <{c['author']['email']}> {a_epoch} {tz}")
    lines.append(f"committer {c['committer']['name']} <{c['committer']['email']}> {c_epoch} {tz}")
    body = "\n".join(lines) + "\n\n" + c["message"]
    return body


def main():
    ref = gh(f"/git/ref/heads/main")
    head = ref["object"]["sha"]
    print("远程 main:", head)

    # 从远端往回收集整条链
    chain, cur = [], head
    while cur:
        c = gh(f"/git/commits/{cur}")
        chain.append(c)
        cur = c["parents"][0]["sha"] if c["parents"] else None
    chain.reverse()          # 由老到新
    print(f"远程链共 {len(chain)} 个 commit")

    local_head_tree = git("rev-parse", "HEAD^{tree}").stdout.strip()

    for c in chain:
        # 早期用普通 git push 推上去的 commit，本地本来就有同一份对象，直接复用
        if has_object(c["sha"]):
            continue
        tree = c["tree"]["sha"]
        if not has_object(tree):
            # 极端情况：本地缺 tree，用 API 拉回来重建
            t = gh(f"/git/trees/{tree}")
            entries = [
                f"{e['mode']} {'tree' if e['type'] == 'tree' else 'blob'} {e['sha']}\t{e['path']}"
                for e in t["tree"]
            ]
            r = git("mktree", inp="\n".join(entries) + "\n")
            if r.returncode != 0:
                print("mktree 失败:", r.stderr[:200])
                sys.exit(1)
            print("  重建 tree:", tree[:7])
        for p in c["parents"]:
            if not has_object(p["sha"]):
                print(f"  父 commit 缺失 {p['sha'][:7]}，先重建它")
                sys.exit(1)
        # GitHub 的 API 只回 UTC 时刻，不回原始提交用的时区偏移；而 commit 对象
        # 里存的是 "epoch +0800" 这种带偏移的字符串，偏移不同 → sha 不同。
        # 本仓库的提交都在 +0800，所以两个候选都试一遍。
        got = None
        for tz in ("+0000", "+0800"):
            body = build_commit(c, tz)
            for variant in (body, body + "\n"):
                r = git("hash-object", "-t", "commit", "-w", "--stdin", inp=variant)
                if r.stdout.strip() == c["sha"]:
                    got = r.stdout.strip()
                    break
            if got:
                break
        if got != c["sha"]:
            print(f"  重建 sha 不符: 本地 {r.stdout.strip()[:7]} != 远程 {c['sha'][:7]}")
            sys.exit(1)

    # 校验：远程 main 的 tree 必须等于本地 HEAD 的 tree（内容一致，工作区不受影响）
    remote_head = chain[-1]
    if remote_head["tree"]["sha"] != local_head_tree:
        print("树不一致，拒绝更新引用：", remote_head["tree"]["sha"], local_head_tree)
        sys.exit(1)

    old = git("rev-parse", "HEAD").stdout.strip()
    git("update-ref", "refs/heads/main", head, old)
    print(f"=== 本地 main 已对齐远程：{old[:7]} -> {head[:7]} ===")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
