"""1688 页面结构探测：帮助确认书签脚本的选择器。

说明：服务端抓不到 1688 页面（有风控），
真正的选择器需要从"你抓到的那条商品"反推。

本脚本用途：
1. 列出数据库里通过书签导入的商品，检查抓取质量
2. 给出改进建议
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "app.db"


def main() -> None:
    c = sqlite3.connect(str(DB))
    c.row_factory = sqlite3.Row

    print("=== 书签导入的商品抓取质量检查 ===\n")
    rows = c.execute("""
        SELECT p.id, p.title, p.url, p.listed_price, p.image_paths, s.name AS shop_name
        FROM product p LEFT JOIN shop s ON p.shop_id = s.id
        ORDER BY p.rowid DESC
    """).fetchall()

    for r in rows:
        imgs = json.loads(r["image_paths"] or "[]")
        print(f"标题: {r['title']}")
        print(f"  店铺: {r['shop_name']}")
        print(f"  价格: {r['listed_price']}")
        print(f"  图片: {len(imgs)} 张")
        print(f"  URL : {r['url']}")
        # 质量诊断
        warnings = []
        if r["shop_name"] and len(r["shop_name"]) > 20:
            warnings.append("店铺名过长，疑似抓到了网页标题")
        if not imgs:
            warnings.append("没有图片，无法视觉匹配")
        if not r["listed_price"]:
            warnings.append("没抓到价格")
        if not r["url"] or "1688" not in (r["url"] or ""):
            warnings.append("URL 不像 1688 商品链接")
        if warnings:
            print(f"  ⚠️ {'；'.join(warnings)}")
        print()

    c.close()


if __name__ == "__main__":
    main()