"""清空指定目标款商品（按名称匹配），保留目标款本身。

用法：python -m scripts.clear_named_targets airpods POLO衫
不传参数则清空除种子数据外的所有目标款商品。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "app.db"
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"


def main(names: list[str]) -> None:
    c = sqlite3.connect(str(DB))
    c.row_factory = sqlite3.Row

    targets = c.execute("SELECT id, name FROM target_style").fetchall()
    todo = [t for t in targets if any(n in t["name"] for n in names)]
    if not todo:
        print("没有匹配的目标款")
        return

    for t in todo:
        tid, name = t["id"], t["name"]
        prods = c.execute(
            "SELECT image_paths FROM product WHERE target_style_id=?", (tid,)
        ).fetchall()
        imgs: set[str] = set()
        for p in prods:
            for ip in json.loads(p["image_paths"] or "[]"):
                if ip:
                    imgs.add(ip)

        for table in ("quote", "inquiry", "product"):
            cur = c.execute(f"DELETE FROM {table} WHERE target_style_id=?", (tid,))
            if cur.rowcount:
                print(f"  {name} / {table}: 删除 {cur.rowcount} 行")

        # 删图片文件（只删 uploads 下且不再被引用的）
        still_used: set[str] = set()
        for r in c.execute("SELECT image_paths FROM product"):
            still_used.update(json.loads(r["image_paths"] or "[]"))
        for r in c.execute("SELECT image_paths FROM target_style"):
            still_used.update(json.loads(r["image_paths"] or "[]"))
        removed = 0
        for ip in imgs:
            if ip in still_used:
                continue
            p = Path(ip)
            try:
                if p.exists() and p.resolve().parent == UPLOAD_DIR.resolve():
                    p.unlink()
                    removed += 1
            except OSError:
                pass
        print(f"  {name}: 删除图片 {removed} 张")

    # 清理孤儿门店
    cur = c.execute("DELETE FROM shop WHERE id NOT IN (SELECT DISTINCT shop_id FROM product)")
    print(f"  孤儿门店: 清理 {cur.rowcount} 个")
    c.commit()

    print("\n=== 清理后 ===")
    stats = c.execute(
        """SELECT t.name, COUNT(p.id) cnt,
                  SUM(CASE WHEN p.listed_price IS NOT NULL THEN 1 ELSE 0 END) priced
           FROM target_style t LEFT JOIN product p ON p.target_style_id = t.id
           GROUP BY t.id ORDER BY cnt DESC"""
    ).fetchall()
    for s in stats:
        print(f"  {s['name']}: {s['cnt']} 商品（{s['priced'] or 0} 有标价）")
    c.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print("请指定目标款名称关键字，例如：python -m scripts.clear_named_targets airpods")
        sys.exit(1)
    main(args)