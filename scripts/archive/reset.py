"""清空数据库并重新灌入种子数据（回到干净的可演示状态）。

用法：python -m scripts.reset
"""
from __future__ import annotations

from pathlib import Path

import sqlite3

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB = DATA_DIR / "app.db"


def main() -> None:
    if DB.exists():
        DB.unlink()
        print("已删除旧库:", DB)
    import scripts.seed
    scripts.seed.main()


if __name__ == "__main__":
    main()