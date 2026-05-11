"""Testy klasifikačních helperů (Fáze 2)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zdenda_mail import classifier, db
from zdenda_mail.models import AttachmentMeta, MailMessage


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    db.init_db(db_path)
    return db_path


def _insert_msg(conn, uid: int, *, folder: str = "INBOX", offset_days: int = 0,
                attachments: list[AttachmentMeta] | None = None) -> int:
    msg = MailMessage(
        uid=uid,
        folder=folder,
        message_id=f"<msg-{uid}@x>",
        from_addr=f"a{uid}@example.com",
        subject=f"Subj {uid}",
        date_sent=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=offset_days),
        body_text=f"Body of message {uid}. " + "x" * 500,
        has_attachments=bool(attachments),
        attachments=attachments or [],
    )
    with db.transaction(conn):
        mid = db.insert_message(conn, msg.to_db_row())
        if attachments:
            db.insert_attachments(conn, mid, [a.model_dump() for a in attachments])
    assert mid is not None
    return mid


# ── prompt_versions ────────────────────────────────────────────────────────────


def test_get_or_create_prompt_version_creates(tmp_db: Path) -> None:
    conn = db.connect(tmp_db)
    try:
        with db.transaction(conn):
            pv = classifier.get_or_create_prompt_version(conn)
        assert isinstance(pv, int)

        row = conn.execute(
            "SELECT version_tag FROM prompt_versions WHERE id = ?", [pv]
        ).fetchone()
        assert row["version_tag"] == classifier.CURRENT_PROMPT_TAG
    finally:
        conn.close()


def test_get_or_create_prompt_version_idempotent(tmp_db: Path) -> None:
    conn = db.connect(tmp_db)
    try:
        with db.transaction(conn):
            a = classifier.get_or_create_prompt_version(conn)
        with db.transaction(conn):
            b = classifier.get_or_create_prompt_version(conn)
        assert a == b
        n = conn.execute("SELECT count(*) FROM prompt_versions").fetchone()[0]
        assert n == 1
    finally:
        conn.close()


def test_get_or_create_prompt_version_rejects_text_drift(tmp_db: Path) -> None:
    """Stejný tag + jiný text musí explicitně shodit (nový tag = nová verze)."""
    conn = db.connect(tmp_db)
    try:
        with db.transaction(conn):
            classifier.get_or_create_prompt_version(conn)
        with pytest.raises(ValueError, match="už existuje"):
            with db.transaction(conn):
                classifier.get_or_create_prompt_version(
                    conn, instructions="UPLNĚ JINÝ TEXT"
                )
    finally:
        conn.close()


# ── next_batch ─────────────────────────────────────────────────────────────────


def test_next_batch_oldest_first_and_limit(tmp_db: Path) -> None:
    conn = db.connect(tmp_db)
    try:
        # Vlož 5 zpráv v opačném pořadí (uid neodpovídá date pořadí)
        _insert_msg(conn, uid=10, offset_days=5)
        _insert_msg(conn, uid=20, offset_days=1)
        _insert_msg(conn, uid=30, offset_days=3)
        _insert_msg(conn, uid=40, offset_days=2)
        _insert_msg(conn, uid=50, offset_days=4)

        with db.transaction(conn):
            pv = classifier.get_or_create_prompt_version(conn)

        items = classifier.next_batch(conn, limit=3, prompt_version_id=pv)
        assert len(items) == 3
        # Seřazeno podle date_sent ASC — nejstarší (offset=1, uid=20) první
        assert [it["uid"] for it in items] == [20, 40, 30]
    finally:
        conn.close()


def test_next_batch_excludes_classified(tmp_db: Path) -> None:
    conn = db.connect(tmp_db)
    try:
        m1 = _insert_msg(conn, uid=1, offset_days=1)
        m2 = _insert_msg(conn, uid=2, offset_days=2)

        with db.transaction(conn):
            pv = classifier.get_or_create_prompt_version(conn)
            classifier.save_classification(
                conn, message_id=m1, prompt_version_id=pv,
                category="unimportant", confidence=0.9,
            )

        items = classifier.next_batch(conn, limit=10, prompt_version_id=pv)
        assert [it["id"] for it in items] == [m2]
    finally:
        conn.close()


def test_next_batch_snippet_truncates(tmp_db: Path) -> None:
    conn = db.connect(tmp_db)
    try:
        _insert_msg(conn, uid=1)
        with db.transaction(conn):
            pv = classifier.get_or_create_prompt_version(conn)
        items = classifier.next_batch(conn, limit=1, prompt_version_id=pv, snippet_chars=50)
        assert len(items[0]["snippet"]) <= 51  # 50 + ellipsis
        assert items[0]["snippet"].endswith("…")
    finally:
        conn.close()


def test_next_batch_includes_attachments(tmp_db: Path) -> None:
    conn = db.connect(tmp_db)
    try:
        att = AttachmentMeta(filename="faktura.pdf", mime_type="application/pdf", size_bytes=999)
        _insert_msg(conn, uid=1, attachments=[att])
        with db.transaction(conn):
            pv = classifier.get_or_create_prompt_version(conn)
        items = classifier.next_batch(conn, limit=1, prompt_version_id=pv)
        assert items[0]["has_attachments"] is True
        assert items[0]["attachments"] == [
            {"filename": "faktura.pdf", "mime_type": "application/pdf", "size_bytes": 999}
        ]
    finally:
        conn.close()


# ── save_classification ───────────────────────────────────────────────────────


def test_save_classification_basic(tmp_db: Path) -> None:
    conn = db.connect(tmp_db)
    try:
        mid = _insert_msg(conn, uid=1)
        with db.transaction(conn):
            pv = classifier.get_or_create_prompt_version(conn)
            cid = classifier.save_classification(
                conn, message_id=mid, prompt_version_id=pv,
                category="invoice", confidence=0.95, reason="PDF faktura",
                sender_type="supplier",
            )
        assert cid > 0

        row = conn.execute(
            "SELECT category, confidence, sender_type, classified_by "
            "FROM classifications WHERE id = ?", [cid]
        ).fetchone()
        assert row["category"] == "invoice"
        assert row["confidence"] == 0.95
        assert row["sender_type"] == "supplier"
        assert row["classified_by"] == "claude_code"
    finally:
        conn.close()


def test_save_classification_rejects_unknown_category(tmp_db: Path) -> None:
    conn = db.connect(tmp_db)
    try:
        mid = _insert_msg(conn, uid=1)
        with db.transaction(conn):
            pv = classifier.get_or_create_prompt_version(conn)
        with pytest.raises(ValueError, match="kategorie"):
            classifier.save_classification(
                conn, message_id=mid, prompt_version_id=pv,
                category="nonsense", confidence=0.5,
            )
    finally:
        conn.close()


def test_save_classification_rejects_bad_confidence(tmp_db: Path) -> None:
    conn = db.connect(tmp_db)
    try:
        mid = _insert_msg(conn, uid=1)
        with db.transaction(conn):
            pv = classifier.get_or_create_prompt_version(conn)
        with pytest.raises(ValueError, match="confidence"):
            classifier.save_classification(
                conn, message_id=mid, prompt_version_id=pv,
                category="invoice", confidence=1.5,
            )
    finally:
        conn.close()


def test_save_classification_rejects_unknown_sender(tmp_db: Path) -> None:
    conn = db.connect(tmp_db)
    try:
        mid = _insert_msg(conn, uid=1)
        with db.transaction(conn):
            pv = classifier.get_or_create_prompt_version(conn)
        with pytest.raises(ValueError, match="sender_type"):
            classifier.save_classification(
                conn, message_id=mid, prompt_version_id=pv,
                category="invoice", confidence=0.9, sender_type="alien",
            )
    finally:
        conn.close()


def test_save_classification_rejects_missing_message(tmp_db: Path) -> None:
    conn = db.connect(tmp_db)
    try:
        with db.transaction(conn):
            pv = classifier.get_or_create_prompt_version(conn)
        with pytest.raises(ValueError, match="neexistuje"):
            classifier.save_classification(
                conn, message_id=999, prompt_version_id=pv,
                category="invoice", confidence=0.9,
            )
    finally:
        conn.close()


def test_save_classification_accepts_spam_category(tmp_db: Path) -> None:
    """Regrese: `spam` jako platná kategorie."""
    conn = db.connect(tmp_db)
    try:
        mid = _insert_msg(conn, uid=1)
        with db.transaction(conn):
            pv = classifier.get_or_create_prompt_version(conn)
            classifier.save_classification(
                conn, message_id=mid, prompt_version_id=pv,
                category="spam", confidence=0.99, reason="Viagra scam",
            )
        cat = conn.execute(
            "SELECT category FROM classifications WHERE message_id = ?", [mid]
        ).fetchone()["category"]
        assert cat == "spam"
    finally:
        conn.close()


# ── stats ──────────────────────────────────────────────────────────────────────


def test_stats_counts(tmp_db: Path) -> None:
    conn = db.connect(tmp_db)
    try:
        m1 = _insert_msg(conn, uid=1, offset_days=1)
        m2 = _insert_msg(conn, uid=2, offset_days=2)
        _insert_msg(conn, uid=3, offset_days=3)  # zůstane nezklasifikovaný

        with db.transaction(conn):
            pv = classifier.get_or_create_prompt_version(conn)
            classifier.save_classification(
                conn, message_id=m1, prompt_version_id=pv,
                category="invoice", confidence=0.9, sender_type="supplier",
            )
            classifier.save_classification(
                conn, message_id=m2, prompt_version_id=pv,
                category="spam", confidence=0.99,
            )

        s = classifier.stats(conn, prompt_version_id=pv)
        assert s["total_messages"] == 3
        assert s["classified"] == 2
        assert s["unclassified"] == 1
        assert s["per_category"] == {"invoice": 1, "spam": 1}
        assert s["per_sender_type"] == {"supplier": 1}
    finally:
        conn.close()


# ── get_message_full ───────────────────────────────────────────────────────────


def test_get_message_full_returns_none_for_missing(tmp_db: Path) -> None:
    conn = db.connect(tmp_db)
    try:
        assert classifier.get_message_full(conn, message_id=999) is None
    finally:
        conn.close()


def test_get_message_full_includes_history(tmp_db: Path) -> None:
    conn = db.connect(tmp_db)
    try:
        att = AttachmentMeta(filename="x.pdf", mime_type="application/pdf", size_bytes=10)
        mid = _insert_msg(conn, uid=1, attachments=[att])

        with db.transaction(conn):
            pv = classifier.get_or_create_prompt_version(conn)
            classifier.save_classification(
                conn, message_id=mid, prompt_version_id=pv,
                category="invoice", confidence=0.92, reason="testing",
            )

        full = classifier.get_message_full(conn, message_id=mid)
        assert full is not None
        assert full["uid"] == 1
        assert len(full["attachments"]) == 1
        assert full["attachments"][0]["filename"] == "x.pdf"
        assert len(full["classifications"]) == 1
        assert full["classifications"][0]["category"] == "invoice"
        assert full["classifications"][0]["version_tag"] == classifier.CURRENT_PROMPT_TAG
    finally:
        conn.close()


# ── CATEGORIES / SENDER_TYPES sanity ───────────────────────────────────────────


def test_categories_includes_spam() -> None:
    assert "spam" in classifier.CATEGORIES


def test_sender_types_complete() -> None:
    for st in ("customer", "supplier", "bank", "marketing", "unknown"):
        assert st in classifier.SENDER_TYPES
