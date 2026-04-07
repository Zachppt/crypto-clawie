"""
db.py — SQLite 持久化预警去重状态
替代 scheduler.py 中的内存 _alerted 字典，重启后状态不丢失。
"""

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH  = BASE_DIR / "data" / "clawie.db"


def init_db():
    """创建表（如不存在）。"""
    DB_PATH.parent.mkdir(exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_log (
                key        TEXT PRIMARY KEY,
                sent_at    TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)
        conn.commit()


def is_alerted(key: str) -> bool:
    """检查 key 是否已推送且未过期。"""
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT expires_at FROM alert_log WHERE key = ?", (key,)
        ).fetchone()
    if not row:
        return False
    return row[0] > now


def mark_alerted(key: str, ttl_hours: int = 8):
    """记录已推送。Upsert，重置过期时间。"""
    now     = datetime.now(timezone.utc)
    expires = (now + timedelta(hours=ttl_hours)).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO alert_log (key, sent_at, expires_at) VALUES (?, ?, ?)",
            (key, now.isoformat(), expires),
        )
        conn.commit()


def clear_expired() -> int:
    """删除已过期的记录，返回删除行数。"""
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        deleted = conn.execute(
            "DELETE FROM alert_log WHERE expires_at < ?", (now,)
        ).rowcount
        conn.commit()
    return deleted
