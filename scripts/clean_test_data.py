"""清理测试污染数据：删除空名称/测试产生的目标款及其关联数据。

用法：python -m scripts.clean_test_data
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "app.db"

# 要保留的目标款（按 id 前缀判断）
KEEP_PREFIXES = ("seed-style", "70fa3b2a")


def main() -> None:
    c = sqlite3.connect(str(DB))
    c.row_factory = sqlite3.Row

    rows = c.execute("SELECT id, name FROM target_style").fetchall()
    print("清理前目标款：")
    for r in rows:
        keep = any(r["id"].startswith(p) for p in KEEP_PREFIXES)
        print(f"  {'保留' if keep else '删除'}  {r['id'][:10]}  {r['name']!r}")

    to_delete = [r["id"] for r in rows if not any(r["id"].startswith(p) for p in KEEP_PREFIXES)]
    if not to_delete:
        print("\n无需清理。")
        c.close()
        return

    ph = ",".join("?" * len(to_delete))
    for table in ("product", "inquiry", "quote", "comparison"):
        cur = c.execute(f"DELETE FROM {table} WHERE target_style_id IN ({ph})", to_delete)
        print(f"  {table}: 删除 {cur.rowcount} 行")
    cur = c.execute(f"DELETE FROM target_style WHERE id IN ({ph})", to_delete)
    print(f"  target_style: 删除 {cur.rowcount} 行")

    # 清掉没有商品的孤儿门店
    cur = c.execute(
        "DELETE FROM shop WHERE id NOT IN (SELECT DISTINCT shop_id FROM product)"
    )
    print(f"  shop: 删除孤儿门店 {cur.rowcount} 行")

    c.commit()
    print("\n=== 清理后 ===")
    for r in c.execute("SELECT id, name FROM target_style"):
        print(f"  目标款: {r['id'][:10]}  {r['name']}")
    print("商品数:", c.execute("SELECT COUNT(*) FROM product").fetchone()[0])
    print("门店数:", c.execute("SELECT COUNT(*) FROM shop").fetchone()[0])
    c.close()


if __name__ == "__main__":
    main()