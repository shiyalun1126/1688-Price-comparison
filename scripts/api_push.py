# -*- coding: utf-8 -*-
"""经 GitHub Git Data API 推送（api.github.com 走沙箱代理稳定可通，github.com 主站被阻断时的替代通道）。

逐 commit 重建 blob -> tree -> commit -> ref，作者/日期/提交信息完整保留。
用法: D:/miniconda3/python.exe scripts/api_push.py <since_local_sha...>   # 按从旧到新的顺序
"""
import base64
import json
import subprocess
import sys

import httpx

REPO = "shiyalun1126/1688-Price-comparison"
API = f"https://api.github.com/repos/{REPO}"

token = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
client = httpx.AsyncClient(
    timeout=httpx.Timeout(120.0),
    headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
)


def run(*args):
    return subprocess.check_output(args, text=True, encoding="utf-8").strip()


async def api(method, path, js=None):
    r = await client.request(method, f"{API}{path}", json=js)
    if r.status_code >= 300:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
    return r.json() if r.text else {}


async def push_commit(remote_parent_sha, commit_sha, diff_parent_sha):
    meta_raw = run("git", "log", "-1", "--format=%an|%ae|%aI|%cn|%ce|%cI|%B", commit_sha)
    an, ae, ad, cn, ce, cd, msg = meta_raw.split("|", 6)
    msg = msg.rstrip("\n")

    # 变更文件清单（相对本地父提交；远程父仅用于挂链，sha 可能不同）
    diff = run("git", "diff-tree", "-r", "--no-renames", "--name-status",
               diff_parent_sha, commit_sha)
    entries, blobs = [], 0
    for line in diff.splitlines():
        status, path = line.split("\t", 1)
        if status == "D":
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            continue
        info = run("git", "ls-tree", commit_sha, "--", path).split("\t")[0]
        mode, _, blob_sha = info.split(" ")
        # blob 已存在于远程吗（如未变更过的二进制）——直接引用本地 git 的 sha 即可，
        # 若远程没有则上传
        try:
            await api("GET", f"/git/blobs/{blob_sha}")
        except RuntimeError:
            raw = subprocess.check_output(["git", "cat-file", "blob", blob_sha])
            created = await api("POST", "/git/blobs", {
                "content": base64.b64encode(raw).decode(), "encoding": "base64"})
            assert created["sha"] == blob_sha, f"blob sha 不符: {path}"
        entries.append({"path": path, "mode": mode, "type": "blob", "sha": blob_sha})
        blobs += 1

    parent = await api("GET", f"/git/commits/{remote_parent_sha}")
    tree = await api("POST", "/git/trees", {"base_tree": parent["tree"]["sha"], "tree": entries})
    commit = await api("POST", "/git/commits", {
        "message": msg, "tree": tree["sha"], "parents": [remote_parent_sha],
        "author": {"name": an, "email": ae, "date": ad},
        "committer": {"name": cn, "email": ce, "date": cd},
    })
    print(f"  {commit_sha[:7]} -> 远程新 commit {commit['sha'][:7]}（{blobs} 个 blob 上传/引用）")
    return commit["sha"]


async def main():
    commits = sys.argv[1:]
    parent_override = None
    if commits and commits[0].startswith("PARENT="):
        parent_override = commits.pop(0)[len("PARENT="):]
    # 起点：默认取远程当前 main；指定 PARENT=<sha> 时强制以该 sha 为链头（用于修复挂错的链）
    if parent_override:
        remote_sha = parent_override
    else:
        ref = await api("GET", "/git/ref/heads/main")
        remote_sha = ref["object"]["sha"]
    print("起点 parent:", remote_sha[:7])

    parent = remote_sha
    for c in commits:
        local_diff_parent = run("git", "rev-parse", f"{c}^")
        parent = await push_commit(parent, c, local_diff_parent)

    force = parent_override is not None
    await api("PATCH", "/git/refs/heads/main", {"sha": parent, "force": force})
    print(f"=== 推送完成，远程 main = {parent[:7]}" + ("（force）" if force else "") + " ===")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
