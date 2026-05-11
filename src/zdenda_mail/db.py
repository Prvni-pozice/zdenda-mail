"""SQLite úložiště — připojení, migrace, JSON helpery."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"


def connect(path: str | Path) -> sqlite3.Connection:
    """Otevři spojení s SQLite databází. WAL + foreign keys vynutíme každé sezení.

    `row_factory` = `sqlite3.Row` — výsledky čteme jako mapping (sloupec → hodnota).
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # WAL a foreign keys jsou per-connection (foreign_keys) i per-DB (journal_mode).
    # Nastavení voláme bezpodmínečně — idempotentní.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db(path: str | Path) -> Path:
    """Vytvoří databázový soubor a aplikuje všechny migrace.

    Idempotentní — pokud již existují tabulky, migrace se neaplikuje znovu
    (rozpozná podle existence `messages` tabulky).
    """
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = connect(db_path)
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        )
        if cur.fetchone() is None:
            for migration_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
                sql = migration_file.read_text(encoding="utf-8")
                conn.executescript(sql)
            conn.commit()
    finally:
        conn.close()

    return db_path


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """`with conn:` ekvivalent s explicitním commit/rollback."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ── JSON helpery ───────────────────────────────────────────────────────────────


def to_json(value: Any) -> str | None:
    """Serializuj Python objekt do JSON stringu. `None` zůstává `None`."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def from_json(value: str | None) -> Any:
    """Deserializuj JSON string zpět; `None` a prázdný string vrací `None`."""
    if not value:
        return None
    return json.loads(value)


# ── Bezpečné batch operace ─────────────────────────────────────────────────────


def insert_message(conn: sqlite3.Connection, row: dict[str, Any]) -> int | None:
    """Vlož jeden mail. Vrátí `lastrowid` nebo `None`, pokud kombinace (uid, folder)
    už existuje (kolize na UNIQUE).
    """
    cols = list(row.keys())
    placeholders = ", ".join(["?"] * len(cols))
    col_list = ", ".join(cols)
    sql = (
        f"INSERT OR IGNORE INTO messages ({col_list}) VALUES ({placeholders})"
    )
    cur = conn.execute(sql, [row[c] for c in cols])
    return cur.lastrowid if cur.rowcount > 0 else None


def insert_attachments(
    conn: sqlite3.Connection,
    message_id: int,
    attachments: Sequence[dict[str, Any]],
) -> None:
    """Vlož všechny přílohy jednoho mailu (jen metadata)."""
    if not attachments:
        return
    rows = [
        (
            message_id,
            att.get("filename"),
            att.get("mime_type"),
            att.get("size_bytes"),
            att.get("saved_path"),
        )
        for att in attachments
    ]
    conn.executemany(
        "INSERT INTO attachments (message_id, filename, mime_type, size_bytes, saved_path) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )


def existing_uids(
    conn: sqlite3.Connection, folder: str, uids: Sequence[int]
) -> set[int]:
    """Vrať podmnožinu z `uids`, která už v DB pro daný folder existuje."""
    if not uids:
        return set()
    placeholders = ",".join(["?"] * len(uids))
    sql = f"SELECT uid FROM messages WHERE folder = ? AND uid IN ({placeholders})"
    cur = conn.execute(sql, [folder, *uids])
    return {row["uid"] for row in cur.fetchall()}


def get_last_uid(conn: sqlite3.Connection, folder: str) -> int | None:
    """Nejvyšší UID, které je v DB pro daný folder."""
    cur = conn.execute(
        "SELECT MAX(uid) AS max_uid FROM messages WHERE folder = ?", [folder]
    )
    row = cur.fetchone()
    return row["max_uid"] if row and row["max_uid"] is not None else None


# ── Runs (audit běhů) ──────────────────────────────────────────────────────────


def start_run(conn: sqlite3.Connection, command: str) -> int:
    cur = conn.execute("INSERT INTO runs (command) VALUES (?)", [command])
    assert cur.lastrowid is not None
    return cur.lastrowid


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    last_uid: int | None,
    messages_processed: int,
    errors_count: int,
    notes: str | None = None,
) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = datetime('now'), last_uid = ?, "
        "messages_processed = ?, errors_count = ?, notes = ? WHERE id = ?",
        [last_uid, messages_processed, errors_count, notes, run_id],
    )
