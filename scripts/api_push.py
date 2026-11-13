# -*- coding: utf-8 -*-
"""经 GitHub Git Data API 推送（api.github.com 走沙箱代理稳定可通，github.com 主站被阻断时的替代通道）。

逐 commit 重建 blob -> tree -> commit -> ref，作者/日期/提交信息完整保留。
用法: D:/miniconda3/python.exe scripts/api_push.py <since_local_sha...>   # 按从旧到新的顺序
"""
import base64
import json
import subprocess
import sys

REPO = "shiyalun1126/1688-Price-comparison"
API = f"https://api.github.com/repos/{REPO}"

token = subprocess.check_output(["gh", "auth", "token"], text=True).strip()


def run(*args):
    return subprocess.check_output(args, text=True, encoding="utf-8").strip()


def api(method, path, js=None):
    """走 curl 而不是 httpx：本机代理对 httpx 的 TLS 握手不稳，间歇性 SSL EOF。"""
    cmd = [
        "curl", "-sS", "--max-time", "120",
        "--retry", "4", "--retry-delay", "2", "--retry-all-errors",
        "-X", method,
        "-H", f"Authorization: Bearer {token}",
        "-H", "Accept: application/vnd.github+json",
    ]
    if js is not None:
        cmd += ["-H", "Content-Type: application/json", "--data-binary", json.dumps(js)]
    cmd.append(f"{API}{path}")
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"{method} {path} curl 失败: {r.stderr.decode('utf-8','replace')[:200]}")
    out = r.stdout.decode("utf-8", "replace").strip()
    data = json.loads(out) if out else {}
    if isinstance(data, dict) and "message" in data and "sha" not in data and "ref" not in data:
        raise RuntimeError(f"{method} {path} -> {data.get('message')}")
    return data


async def push_commit(remote_parent_sha, commit_sha, diff_parent_sha):
    meta_raw = run("git", "log", "-1", "--format=%an|%ae|%aI|%cn|%ce|%cI|%B", commit_sha)
    an, ae, ad, cn, ce, cd, msg = meta_raw.split("|", 6)
    msg = msg.rstrip("\n")

    # 变更文件清单（相对本地父提交；远程父仅用于挂链，sha 可能不同）
    # core.quotepath=false：否则中文路径会被转义成 "docs/\346\234\254..." 再加引号，
    # 后面 ls-tree / blob 上传全都找不到这个文件。
    diff = run("git", "-c", "core.quotepath=false", "diff-tree", "-r", "--no-renames",
               "--name-status", diff_parent_sha, commit_sha)
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
            api("GET", f"/git/blobs/{blob_sha}")
        except RuntimeError:
            raw = subprocess.check_output(["git", "cat-file", "blob", blob_sha])
            created = api("POST", "/git/blobs", {
                "content": base64.b64encode(raw).decode(), "encoding": "base64"})
            assert created["sha"] == blob_sha, f"blob sha 不符: {path}"
        entries.append({"path": path, "mode": mode, "type": "blob", "sha": blob_sha})
        blobs += 1

    parent = api("GET", f"/git/commits/{remote_parent_sha}")
    tree = api("POST", "/git/trees", {"base_tree": parent["tree"]["sha"], "tree": entries})
    commit = api("POST", "/git/commits", {
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
        ref = api("GET", "/git/ref/heads/main")
        remote_sha = ref["object"]["sha"]
    print("起点 parent:", remote_sha[:7])

    parent = remote_sha
    for c in commits:
        local_diff_parent = run("git", "rev-parse", f"{c}^")
        parent = await push_commit(parent, c, local_diff_parent)

    force = parent_override is not None
    api("PATCH", "/git/refs/heads/main", {"sha": parent, "force": force})
    print(f"=== 推送完成，远程 main = {parent[:7]}" + ("（force）" if force else "") + " ===")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
