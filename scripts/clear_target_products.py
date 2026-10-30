"""清空指定目标款下的所有商品（保留目标款本身）。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "app.db"
UPLOAD_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"
KEEP_CLASSES = {"动漫类"}


def main() -> None:
    c = sqlite3.connect(str(DB))
    c.row_factory = sqlite3.Row

    # 找到蓝白格衬衫目标款
    rows = c.execute("SELECT id, name FROM target_style").fetchall()
    target = None
    for r in rows:
        if "蓝白格" in r["name"]:
            target = r
            break
    if not target:
        print("未找到蓝白格目标款")
        return
    tid = target["id"]
    print(f"目标款: {target['name']}  ({tid[:12]})")

    # 统计要删的商品，并收集图片路径
    prods = c.execute(
        "SELECT id, title, image_paths FROM product WHERE target_style_id=?", (tid,)
    ).fetchall()
    print(f"待删除商品: {len(prods)} 个")

    img_paths: set[str] = set()
    for p in prods:
        for ip in json.loads(p["image_paths"] or "[]"):
            if ip:
                img_paths.add(ip)

    # 删除关联数据
    for table in ("quote", "inquiry", "product"):
        cur = c.execute(f"DELETE FROM {table} WHERE target_style_id=?", (tid,))
        print(f"  {table}: 删除 {cur.rowcount} 行")

    # 清理孤儿门店
    cur = c.execute(
        "DELETE FROM shop WHERE id NOT IN (SELECT DISTINCT shop_id FROM product)"
    )
    print(f"  孤儿门店: 清理 {cur.rowcount} 个")

    c.commit()

    # 删除图片文件（只删 uploads 目录下的，且不再被任何商品/目标款引用）
    deleted_files = 0
    if img_paths:
        still_used: set[str] = set()
        for r in c.execute("SELECT image_paths FROM product"):
            still_used.update(json.loads(r["image_paths"] or "[]"))
        for r in c.execute("SELECT image_paths FROM target_style"):
            still_used.update(json.loads(r["image_paths"] or "[]"))

        for ip in img_paths:
            if ip in still_used:
                continue
            p = Path(ip)
            try:
                if p.exists() and p.resolve().parent == UPLOAD_DIR.resolve():
                    p.unlink()
                    deleted_files += 1
            except OSError:
                pass
    print(f"  删除图片文件: {deleted_files} 张")

    # 打印最终统计
    print("\n=== 清理后各目标款商品数 ===")
    stats = c.execute(
        """SELECT t.name, COUNT(p.id) AS cnt
           FROM target_style t LEFT JOIN product p ON p.target_style_id = t.id
           GROUP BY t.id ORDER BY cnt DESC"""
    ).fetchall()
    for s in stats:
        print(f"  {s['name']}: {s['cnt']}")

    total = c.execute("SELECT COUNT(*) FROM product").fetchone()[0]
    shops = c.execute("SELECT COUNT(*) FROM shop").fetchone()[0]
    print(f"\n全库商品总数: {total}")
    print(f"全库门店总数: {shops}")

    c.close()


if __name__ == "__main__":
    main()