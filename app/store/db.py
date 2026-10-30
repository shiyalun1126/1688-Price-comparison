"""SQLite 数据存取层（标准库 sqlite3，无额外依赖）。"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from app.models.entities import (
    ComparisonResult,
    InquiryTask,
    Product,
    Quote,
    Shop,
    TargetStyle,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS target_style (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    image_paths TEXT NOT NULL DEFAULT '[]',
    attributes TEXT NOT NULL DEFAULT '{}',
    reference_price REAL,
    category TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shop (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    aliww TEXT,
    url TEXT,
    location TEXT,
    tags TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS product (
    id TEXT PRIMARY KEY,
    target_style_id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    title TEXT NOT NULL,
    image_paths TEXT NOT NULL DEFAULT '[]',
    url TEXT,
    attributes TEXT NOT NULL DEFAULT '{}',
    listed_price REAL,
    moq INTEGER,
    match_status TEXT NOT NULL,
    match_tier TEXT,
    match_score REAL,
    match_reason TEXT NOT NULL DEFAULT '',
    matched_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS inquiry (
    id TEXT PRIMARY KEY,
    target_style_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    script TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    buyer_quantity INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quote (
    id TEXT PRIMARY KEY,
    target_style_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    shop_id TEXT NOT NULL,
    inquiry_id TEXT,
    source TEXT NOT NULL,
    price REAL NOT NULL,
    moq INTEGER,
    tax TEXT NOT NULL,
    includes_shipping INTEGER,
    tiers TEXT NOT NULL DEFAULT '[]',
    condition_note TEXT NOT NULL DEFAULT '',
    raw_text TEXT NOT NULL DEFAULT '',
    quoted_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS comparison (
    target_style_id TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    rows TEXT NOT NULL DEFAULT '[]',
    recommendation TEXT NOT NULL DEFAULT '',
    negotiation_script TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL DEFAULT ''
);

-- 高频查询路径索引（幂等，旧库初始化时同样生效）
CREATE INDEX IF NOT EXISTS idx_product_target  ON product(target_style_id);
CREATE INDEX IF NOT EXISTS idx_product_shop    ON product(shop_id);
CREATE INDEX IF NOT EXISTS idx_inquiry_target  ON inquiry(target_style_id);
CREATE INDEX IF NOT EXISTS idx_inquiry_product ON inquiry(target_style_id, product_id);
CREATE INDEX IF NOT EXISTS idx_quote_target    ON quote(target_style_id);
CREATE INDEX IF NOT EXISTS idx_quote_product   ON quote(product_id);
"""


def _iso(dt) -> str:
    return dt.isoformat()


def _loads(s: str, default):
    try:
        return json.loads(s) if s else default
    except json.JSONDecodeError:
        return default


class Store:
    """按类型简单封装的 SQLite 存储。"""

    def __init__(self, db_path: str | Path):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()   # 保护共享连接（多线程匹配时使用）
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """补齐旧库缺少的列（幂等）。"""
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(product)")}
        if "match_reason" not in cols:
            self._conn.execute("ALTER TABLE product ADD COLUMN match_reason TEXT NOT NULL DEFAULT ''")
        if "matched_at" not in cols:
            self._conn.execute("ALTER TABLE product ADD COLUMN matched_at TEXT")
        ccols = {r["name"] for r in self._conn.execute("PRAGMA table_info(comparison)")}
        if "fingerprint" not in ccols:
            self._conn.execute("ALTER TABLE comparison ADD COLUMN fingerprint TEXT NOT NULL DEFAULT ''")

    def close(self) -> None:
        self._conn.close()

    # ---- target_style -----------------------------------------------------
    def save_target_style(self, ts: TargetStyle) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO target_style
               (id, name, description, image_paths, attributes, reference_price,
                category, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                ts.id, ts.name, ts.description, json.dumps(ts.image_paths),
                json.dumps(ts.attributes), ts.reference_price, ts.category,
                _iso(ts.created_at), _iso(ts.updated_at),
            ),
        )
        self._conn.commit()

    def get_target_style(self, id: str) -> TargetStyle | None:
        row = self._conn.execute(
            "SELECT * FROM target_style WHERE id=?", (id,)
        ).fetchone()
        return self._row_to_target_style(row) if row else None

    def list_target_styles(self) -> list[TargetStyle]:
        rows = self._conn.execute("SELECT * FROM target_style ORDER BY updated_at DESC").fetchall()
        return [self._row_to_target_style(r) for r in rows]

    def delete_target_style(self, id: str) -> dict:
        """删除目标款及其所有关联数据（商品/询价/报价/比价）。"""
        counts: dict[str, int] = {}
        for table in ("product", "inquiry", "quote", "comparison"):
            cur = self._conn.execute(
                f"DELETE FROM {table} WHERE target_style_id=?", (id,)
            )
            counts[table] = cur.rowcount
        cur = self._conn.execute("DELETE FROM target_style WHERE id=?", (id,))
        counts["target_style"] = cur.rowcount
        # 清理因此产生的孤儿门店
        cur = self._conn.execute(
            "DELETE FROM shop WHERE id NOT IN (SELECT DISTINCT shop_id FROM product)"
        )
        counts["orphan_shop"] = cur.rowcount
        self._conn.commit()
        return counts

    @staticmethod
    def _row_to_target_style(row) -> TargetStyle:
        from datetime import datetime
        return TargetStyle(
            id=row["id"], name=row["name"], description=row["description"],
            image_paths=_loads(row["image_paths"], []),
            attributes=_loads(row["attributes"], {}),
            reference_price=row["reference_price"], category=row["category"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # ---- shop -------------------------------------------------------------
    def save_shop(self, s: Shop) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO shop (id, name, aliww, url, location, tags) VALUES (?,?,?,?,?,?)",
            (s.id, s.name, s.aliww, s.url, s.location, json.dumps(s.tags)),
        )
        self._conn.commit()

    def get_shop(self, id: str) -> Shop | None:
        row = self._conn.execute("SELECT * FROM shop WHERE id=?", (id,)).fetchone()
        return self._row_to_shop(row) if row else None

    def list_shops(self) -> list[Shop]:
        rows = self._conn.execute("SELECT * FROM shop ORDER BY name").fetchall()
        return [self._row_to_shop(r) for r in rows]

    @staticmethod
    def _row_to_shop(row) -> Shop:
        return Shop(
            id=row["id"], name=row["name"], aliww=row["aliww"], url=row["url"],
            location=row["location"], tags=_loads(row["tags"], []),
        )

    # ---- product ----------------------------------------------------------
    def save_product(self, p: Product) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO product
                   (id, target_style_id, shop_id, title, image_paths, url, attributes,
                    listed_price, moq, match_status, match_tier, match_score,
                    match_reason, matched_at, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    p.id, p.target_style_id, p.shop_id, p.title,
                    json.dumps(p.image_paths), p.url, json.dumps(p.attributes),
                    p.listed_price, p.moq, p.match_status.value,
                    p.match_tier.value if p.match_tier else None,
                    p.match_score, p.match_reason,
                    _iso(p.matched_at) if p.matched_at else None,
                    _iso(p.created_at),
                ),
            )
            self._conn.commit()

    def list_products(self, target_style_id: str) -> list[Product]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM product WHERE target_style_id=? ORDER BY match_score DESC",
                (target_style_id,),
            ).fetchall()
        return [self._row_to_product(r) for r in rows]

    @staticmethod
    def _row_to_product(row) -> Product:
        from datetime import datetime
        from app.models.enums import MatchStatus, MatchTier
        keys = row.keys()
        return Product(
            id=row["id"], target_style_id=row["target_style_id"],
            shop_id=row["shop_id"], title=row["title"],
            image_paths=_loads(row["image_paths"], []), url=row["url"],
            attributes=_loads(row["attributes"], {}),
            listed_price=row["listed_price"], moq=row["moq"],
            match_status=MatchStatus(row["match_status"]),
            match_tier=MatchTier(row["match_tier"]) if row["match_tier"] else None,
            match_score=row["match_score"],
            match_reason=(row["match_reason"] or "") if "match_reason" in keys else "",
            matched_at=(datetime.fromisoformat(row["matched_at"])
                        if "matched_at" in keys and row["matched_at"] else None),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    # ---- inquiry ----------------------------------------------------------
    def save_inquiry(self, i: InquiryTask) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO inquiry
               (id, target_style_id, product_id, shop_id, script, status,
                buyer_quantity, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (i.id, i.target_style_id, i.product_id, i.shop_id, i.script,
             i.status.value, i.buyer_quantity, _iso(i.created_at), _iso(i.updated_at)),
        )
        self._conn.commit()

    def list_inquiries(self, target_style_id: str) -> list[InquiryTask]:
        rows = self._conn.execute(
            "SELECT * FROM inquiry WHERE target_style_id=? ORDER BY created_at DESC",
            (target_style_id,),
        ).fetchall()
        return [self._row_to_inquiry(r) for r in rows]

    def get_inquiry(self, id: str) -> InquiryTask | None:
        row = self._conn.execute("SELECT * FROM inquiry WHERE id=?", (id,)).fetchone()
        return self._row_to_inquiry(row) if row else None

    def get_inquiry_by_product(self, target_style_id: str, product_id: str) -> InquiryTask | None:
        row = self._conn.execute(
            "SELECT * FROM inquiry WHERE target_style_id=? AND product_id=? ORDER BY created_at DESC LIMIT 1",
            (target_style_id, product_id),
        ).fetchone()
        return self._row_to_inquiry(row) if row else None

    @staticmethod
    def _row_to_inquiry(row) -> InquiryTask:
        from datetime import datetime
        from app.models.enums import InquiryStatus
        return InquiryTask(
            id=row["id"], target_style_id=row["target_style_id"],
            product_id=row["product_id"], shop_id=row["shop_id"],
            script=row["script"], status=InquiryStatus(row["status"]),
            buyer_quantity=row["buyer_quantity"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # ---- quote ------------------------------------------------------------
    def save_quote(self, q: Quote) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO quote
               (id, target_style_id, product_id, shop_id, inquiry_id, source, price,
                moq, tax, includes_shipping, tiers, condition_note, raw_text, quoted_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                q.id, q.target_style_id, q.product_id, q.shop_id, q.inquiry_id,
                q.source.value, q.price, q.moq, q.tax.value,
                q.includes_shipping, json.dumps(q.tiers), q.condition_note,
                q.raw_text, _iso(q.quoted_at),
            ),
        )
        self._conn.commit()

    def list_quotes(self, target_style_id: str) -> list[Quote]:
        rows = self._conn.execute(
            "SELECT * FROM quote WHERE target_style_id=? ORDER BY price ASC",
            (target_style_id,),
        ).fetchall()
        return [self._row_to_quote(r) for r in rows]

    @staticmethod
    def _row_to_quote(row) -> Quote:
        from datetime import datetime
        from app.models.enums import QuoteSource, TaxStatus
        return Quote(
            id=row["id"], target_style_id=row["target_style_id"],
            product_id=row["product_id"], shop_id=row["shop_id"],
            inquiry_id=row["inquiry_id"], source=QuoteSource(row["source"]),
            price=row["price"], moq=row["moq"], tax=TaxStatus(row["tax"]),
            includes_shipping=row["includes_shipping"],
            tiers=_loads(row["tiers"], []), condition_note=row["condition_note"],
            raw_text=row["raw_text"], quoted_at=datetime.fromisoformat(row["quoted_at"]),
        )

    # ---- comparison -------------------------------------------------------
    def save_comparison(self, c: ComparisonResult) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO comparison
               (target_style_id, generated_at, rows, recommendation,
                negotiation_script, summary, fingerprint)
               VALUES (?,?,?,?,?,?,?)""",
            (c.target_style_id, _iso(c.generated_at), json.dumps(c.rows),
             c.recommendation, c.negotiation_script, c.summary, c.fingerprint),
        )
        self._conn.commit()

    def get_comparison(self, target_style_id: str) -> ComparisonResult | None:
        row = self._conn.execute(
            "SELECT * FROM comparison WHERE target_style_id=?", (target_style_id,)
        ).fetchone()
        if not row:
            return None
        from datetime import datetime
        return ComparisonResult(
            target_style_id=row["target_style_id"],
            generated_at=datetime.fromisoformat(row["generated_at"]),
            rows=_loads(row["rows"], []), recommendation=row["recommendation"],
            negotiation_script=row["negotiation_script"], summary=row["summary"],
            fingerprint=(row["fingerprint"] or "") if "fingerprint" in row.keys() else "",
        )