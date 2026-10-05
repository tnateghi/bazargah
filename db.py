from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "queue.db"


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sort_order INTEGER NOT NULL DEFAULT 0,
                tafsili_code TEXT,
                shenase TEXT NOT NULL UNIQUE,
                name TEXT,
                light_count INTEGER NOT NULL DEFAULT 0,
                heavy_count INTEGER NOT NULL DEFAULT 0,
                activity_type TEXT NOT NULL,
                old_light INTEGER,
                old_heavy INTEGER,
                changed INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cols = {r[1] for r in conn.execute("PRAGMA table_info(records)").fetchall()}
        if "sort_order" not in cols:
            conn.execute("ALTER TABLE records ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
        if "changed" not in cols:
            conn.execute("ALTER TABLE records ADD COLUMN changed INTEGER NOT NULL DEFAULT 1")
        if "tafsili_code" not in cols:
            conn.execute("ALTER TABLE records ADD COLUMN tafsili_code TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        conn.commit()


def replace_queue(rows: list[dict]) -> dict:
    """Full rebuild from Excel — previous progress is discarded."""
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM records")
        for row in rows:
            status = row["status"]
            if row.get("changed") == 0:
                status = "nochange"
            elif status not in {"pending", "nochange"}:
                status = "pending"
            conn.execute(
                """
                INSERT INTO records (
                    sort_order, tafsili_code, shenase, name, light_count, heavy_count, activity_type,
                    old_light, old_heavy, changed, status, error
                ) VALUES (
                    :sort_order, :tafsili_code, :shenase, :name, :light_count, :heavy_count, :activity_type,
                    :old_light, :old_heavy, :changed, :status, NULL
                )
                """,
                {**row, "status": status, "error": None},
            )
        conn.commit()
        return stats()


def stats() -> dict:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS c FROM records GROUP BY status"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        changed = conn.execute(
            "SELECT COUNT(*) FROM records WHERE changed=1"
        ).fetchone()[0]
        unchanged = conn.execute(
            "SELECT COUNT(*) FROM records WHERE changed=0"
        ).fetchone()[0]
        pending_changed = conn.execute(
            "SELECT COUNT(*) FROM records WHERE status='pending' AND changed=1"
        ).fetchone()[0]
    counts = {r["status"]: r["c"] for r in rows}
    return {
        "total": total,
        "pending": counts.get("pending", 0),
        "done": counts.get("done", 0),
        "nochange": counts.get("nochange", 0),
        "failed": counts.get("failed", 0),
        "running": counts.get("running", 0),
        "changed": changed,
        "unchanged": unchanged,
        "pending_changed": pending_changed,
    }


def _filter_sql(
    status: str | None = None,
    q: str | None = None,
    changed: str | None = None,
) -> tuple[str, list]:
    sql = "FROM records WHERE 1=1"
    params: list = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if changed == "1":
        sql += " AND changed = 1"
    elif changed == "0":
        sql += " AND changed = 0"
    if q:
        sql += " AND (shenase LIKE ? OR name LIKE ? OR IFNULL(tafsili_code,'') LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    return sql, params


def count_records(
    status: str | None = None,
    q: str | None = None,
    changed: str | None = None,
) -> int:
    init_db()
    where, params = _filter_sql(status, q, changed)
    with connect() as conn:
        return conn.execute(f"SELECT COUNT(*) {where}", params).fetchone()[0]


def list_records(
    status: str | None = None,
    q: str | None = None,
    changed: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    init_db()
    where, params = _filter_sql(status, q, changed)
    sql = f"SELECT * {where} ORDER BY sort_order ASC, id ASC LIMIT ? OFFSET ?"
    params = [*params, limit, offset]
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_records_by_shenase(shenases: list[str]) -> list[dict]:
    if not shenases:
        return []
    init_db()
    placeholders = ",".join("?" for _ in shenases)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM records WHERE shenase IN ({placeholders}) ORDER BY sort_order ASC",
            shenases,
        ).fetchall()
    by = {r["shenase"]: dict(r) for r in rows}
    return [by[s] for s in shenases if s in by]


def set_status(shenase: str, status: str, error: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE records
            SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE shenase = ?
            """,
            (status, error, shenase),
        )
        conn.commit()


def mark_until(sort_order: int, status: str = "done") -> int:
    """Mark all rows up to sort_order (inclusive) as done/skipped."""
    with connect() as conn:
        cur = conn.execute(
            """
            UPDATE records
            SET status = ?, error = 'marked until here', updated_at = CURRENT_TIMESTAMP
            WHERE sort_order <= ? AND status != 'done'
            """,
            (status, sort_order),
        )
        conn.commit()
        return cur.rowcount


def mark_many(shenases: list[str], status: str, error: str | None = None) -> int:
    if not shenases:
        return 0
    with connect() as conn:
        cur = conn.executemany(
            """
            UPDATE records
            SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE shenase = ?
            """,
            [(status, error, s) for s in shenases],
        )
        conn.commit()
        return cur.rowcount


def reset_failed() -> int:
    with connect() as conn:
        cur = conn.execute(
            """
            UPDATE records
            SET status='pending', error=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE status='failed'
            """
        )
        conn.commit()
        return cur.rowcount
