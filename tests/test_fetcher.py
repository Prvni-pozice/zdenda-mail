"""Testy DB vrstvy a parsování modelů. IMAP část je integrační — testuje se
ručně proti reálnému serveru přes `zdenda-mail fetch --dry-run --batch 5`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from zdenda_mail import db
from zdenda_mail.models import AttachmentMeta, MailMessage


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Čerstvá inicializovaná DB v tmp adresáři."""
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    return db_path


def _sample_message(uid: int = 100, folder: str = "INBOX") -> MailMessage:
    return MailMessage(
        uid=uid,
        folder=folder,
        message_id=f"<test-{uid}@example.com>",
        from_addr="alice@example.com",
        from_name="Alice",
        to_addrs=["zdenek@prvni-pozice.com"],
        subject=f"Test message {uid}",
        date_sent=datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc),
        body_text="Hello, world.",
        has_attachments=True,
        attachments=[
            AttachmentMeta(filename="invoice.pdf", mime_type="application/pdf", size_bytes=12345)
        ],
        thread_refs=["<thread-a@example.com>"],
    )


# ── init_db ────────────────────────────────────────────────────────────────────


def test_init_db_creates_tables(tmp_db: Path) -> None:
    conn = db.connect(tmp_db)
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row["name"] for row in cur.fetchall()}
    finally:
        conn.close()

    expected = {
        "messages",
        "attachments",
        "prompt_versions",
        "classifications",
        "human_labels",
        "actions",
        "runs",
    }
    assert expected.issubset(tables)


def test_init_db_is_idempotent(tmp_db: Path) -> None:
    """Druhé spuštění nesmí spadnout, ani duplikovat schéma."""
    db.init_db(tmp_db)  # already initialized — nesmí spadnout
    db.init_db(tmp_db)


def test_wal_and_foreign_keys_enabled(tmp_db: Path) -> None:
    conn = db.connect(tmp_db)
    try:
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    finally:
        conn.close()
    assert journal.lower() == "wal"
    assert fk == 1


# ── JSON helpery ───────────────────────────────────────────────────────────────


def test_to_json_handles_none() -> None:
    assert db.to_json(None) is None


def test_to_json_roundtrip() -> None:
    payload = {"refs": ["<a@b>", "<c@d>"], "count": 2}
    assert db.from_json(db.to_json(payload)) == payload


def test_from_json_handles_empty() -> None:
    assert db.from_json(None) is None
    assert db.from_json("") is None


# ── Insert / idempotence ───────────────────────────────────────────────────────


def test_insert_message_returns_id(tmp_db: Path) -> None:
    msg = _sample_message()
    conn = db.connect(tmp_db)
    try:
        with db.transaction(conn):
            msg_id = db.insert_message(conn, msg.to_db_row())
        assert msg_id is not None
        assert isinstance(msg_id, int)
    finally:
        conn.close()


def test_insert_message_idempotent_by_uid_folder(tmp_db: Path) -> None:
    """Druhý insert se stejným (uid, folder) vrátí None (INSERT OR IGNORE)."""
    msg = _sample_message(uid=42)
    conn = db.connect(tmp_db)
    try:
        with db.transaction(conn):
            first = db.insert_message(conn, msg.to_db_row())
            second = db.insert_message(conn, msg.to_db_row())
        assert first is not None
        assert second is None
    finally:
        conn.close()


def test_attachments_insert_and_cascade(tmp_db: Path) -> None:
    """Smazání zprávy smaže i přílohy (FK ON DELETE CASCADE)."""
    msg = _sample_message()
    conn = db.connect(tmp_db)
    try:
        with db.transaction(conn):
            mid = db.insert_message(conn, msg.to_db_row())
            assert mid is not None
            db.insert_attachments(conn, mid, [a.model_dump() for a in msg.attachments])

        n = conn.execute(
            "SELECT count(*) FROM attachments WHERE message_id = ?", [mid]
        ).fetchone()[0]
        assert n == 1

        with db.transaction(conn):
            conn.execute("DELETE FROM messages WHERE id = ?", [mid])

        n = conn.execute(
            "SELECT count(*) FROM attachments WHERE message_id = ?", [mid]
        ).fetchone()[0]
        assert n == 0
    finally:
        conn.close()


# ── existing_uids / last_uid ───────────────────────────────────────────────────


def test_existing_uids_and_last_uid(tmp_db: Path) -> None:
    conn = db.connect(tmp_db)
    try:
        with db.transaction(conn):
            for uid in (5, 7, 9):
                db.insert_message(conn, _sample_message(uid=uid).to_db_row())

        assert db.existing_uids(conn, "INBOX", [3, 5, 9, 11]) == {5, 9}
        assert db.existing_uids(conn, "INBOX", []) == set()
        assert db.get_last_uid(conn, "INBOX") == 9
        assert db.get_last_uid(conn, "INBOX/Other") is None
    finally:
        conn.close()


# ── Runs ───────────────────────────────────────────────────────────────────────


def test_run_lifecycle(tmp_db: Path) -> None:
    conn = db.connect(tmp_db)
    try:
        with db.transaction(conn):
            run_id = db.start_run(conn, "fetch --batch 10")
        with db.transaction(conn):
            db.finish_run(
                conn,
                run_id,
                last_uid=42,
                messages_processed=10,
                errors_count=0,
                notes="ok",
            )

        row = conn.execute("SELECT * FROM runs WHERE id = ?", [run_id]).fetchone()
        assert row["command"] == "fetch --batch 10"
        assert row["last_uid"] == 42
        assert row["messages_processed"] == 10
        assert row["finished_at"] is not None
    finally:
        conn.close()


# ── Model conversion ──────────────────────────────────────────────────────────


def test_message_to_db_row_serializes_json() -> None:
    msg = _sample_message()
    row = msg.to_db_row()

    assert row["from_addr"] == "alice@example.com"
    assert row["has_attachments"] == 1
    # JSON sloupce
    assert db.from_json(row["to_addrs"]) == ["zdenek@prvni-pozice.com"]
    assert db.from_json(row["thread_refs"]) == ["<thread-a@example.com>"]
    # ISO datum
    assert row["date_sent"] is not None and "2026-05-11" in row["date_sent"]


def test_message_to_db_row_handles_empty_lists() -> None:
    msg = MailMessage(uid=1, from_addr="x@y", to_addrs=[], thread_refs=[])
    row = msg.to_db_row()
    assert row["to_addrs"] is None
    assert row["thread_refs"] is None
