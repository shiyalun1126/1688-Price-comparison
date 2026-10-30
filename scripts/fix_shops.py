"""一次性数据修复：从商品链接的 uid 参数还原真实卖家，重建店铺归属。

背景：书签脚本曾把登录账号的公司名（如「海南智泰昌」）当成所有商品的卖家，
导致 19 个商品挂在同一家错误的店铺下。旺旺聊天链接里的 uid 参数才是真实卖家名。

处理：
1. 有 uid 的商品 → 按解码后的 uid 名 find-or-create 店铺并重新归属（连带询价/报价）。
2. 标题即卖家名（如「阿里虎」）的坏导入 → 删除。
3. 剩余无 uid 商品所在的错误店铺 → 改名为「1688找相似（卖家未记录）」。
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from urllib.parse import parse_qs, unquote, urlparse

from app.store.db import Store


def main() -> None:
    s = Store("data/app.db")
    conn = s._conn

    shops = {x.name: x.id for x in s.list_shops()}
    moves: dict[str, list[str]] = defaultdict(list)
    junk: list[str] = []

    for p in s.list_products_all() if hasattr(s, "list_products_all") else []:
        pass  # 占位：Store 无全量接口，下面直接用 SQL

    rows = conn.execute(
        "SELECT id, title, url, shop_id FROM product"
    ).fetchall()
    for r in rows:
        uid = (parse_qs(urlparse(r["url"] or "").query).get("uid") or [""])[0]
        name = unquote(uid).strip() if uid else ""
        if name and name != r["title"]:
            if name not in shops:
                sid = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO shop (id, name, tags) VALUES (?,?,'[]')", (sid, name)
                )
                shops[name] = sid
            if shops[name] != r["shop_id"]:
                moves[name].append(r["id"])
        elif name and name == r["title"]:
            junk.append(r["id"])  # 标题就是卖家名 → 抓取失败的坏数据

    # 1) 重新归属（连带询价/报价的 shop_id）
    for name, pids in moves.items():
        sid = shops[name]
        for pid in pids:
            conn.execute("UPDATE product SET shop_id=? WHERE id=?", (sid, pid))
            conn.execute("UPDATE inquiry SET shop_id=? WHERE product_id=?", (sid, pid))
            conn.execute("UPDATE quote SET shop_id=? WHERE product_id=?", (sid, pid))
        print(f"  归属 → {name}: {len(pids)} 个商品")

    # 2) 删除坏导入
    for pid in junk:
        conn.execute("DELETE FROM quote WHERE product_id=?", (pid,))
        conn.execute("DELETE FROM inquiry WHERE product_id=?", (pid,))
        conn.execute("DELETE FROM product WHERE id=?", (pid,))
        print(f"  删除坏导入（标题=卖家名）: {pid[:8]}")

    # 3) 剩余挂在错误店铺下的 → 改名；孤儿店铺删除
    for old_shop_id in {r["shop_id"] for r in rows}:
        n = conn.execute(
            "SELECT COUNT(*) FROM product WHERE shop_id=?", (old_shop_id,)
        ).fetchone()[0]
        if n == 0:
            conn.execute("DELETE FROM shop WHERE id=?", (old_shop_id,))
            conn.execute("DELETE FROM shop WHERE id=? AND name='海南智泰昌国际科技发展有限公司'", (old_shop_id,))
        else:
            row = conn.execute("SELECT name FROM shop WHERE id=?", (old_shop_id,)).fetchone()
            if row and row["name"] == "海南智泰昌国际科技发展有限公司":
                conn.execute(
                    "UPDATE shop SET name='1688找相似（卖家未记录）' WHERE id=?",
                    (old_shop_id,),
                )
                print(f"  改名 → 1688找相似（卖家未记录）: 仍挂 {n} 个无 uid 商品")

    conn.commit()

    print("\n=== 修复后店铺 ===")
    for r in conn.execute(
        "SELECT s.name, COUNT(p.id) c FROM shop s LEFT JOIN product p ON p.shop_id=s.id GROUP BY s.id"
    ):
        print(f"  {r['name']}: {r['c']} 个商品")
    s.close()


if __name__ == "__main__":
    main()
