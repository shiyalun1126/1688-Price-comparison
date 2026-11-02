"""清理测试商品并查看各目标款商品数。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "app.db"


def main() -> None:
    c = sqlite3.connect(str(DB))
    cur = c.execute("DELETE FROM product WHERE title LIKE ?", ("%动态验证%",))
    print("清理测试商品:", cur.rowcount, "条")
    c.commit()

    print("\n各目标款商品数:")
    rows = c.execute(
        """SELECT t.name, COUNT(p.id)
           FROM target_style t LEFT JOIN product p ON p.target_style_id = t.id
           GROUP BY t.id ORDER BY COUNT(p.id) DESC"""
    ).fetchall()
    for name, cnt in rows:
        print(f"  {name}: {cnt}")
    c.close()


if __name__ == "__main__":
    main()