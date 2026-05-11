"""Pydantic schémata pro IMAP zprávy a přílohy."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AttachmentMeta(BaseModel):
    """Metadata přílohy (NE obsah)."""

    model_config = ConfigDict(extra="ignore")

    filename: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None


class MailMessage(BaseModel):
    """Parsovaná IMAP zpráva, připravená k uložení do `messages`."""

    model_config = ConfigDict(extra="ignore")

    uid: int
    folder: str = "INBOX"
    message_id: str | None = None
    in_reply_to: str | None = None
    thread_refs: list[str] = Field(default_factory=list)
    from_addr: str
    from_name: str | None = None
    to_addrs: list[str] = Field(default_factory=list)
    cc_addrs: list[str] = Field(default_factory=list)
    subject: str | None = None
    date_sent: datetime | None = None
    date_received: datetime | None = None
    body_text: str | None = None
    body_html: str | None = None
    headers_raw: dict[str, list[str]] = Field(default_factory=dict)
    has_attachments: bool = False
    size_bytes: int | None = None
    attachments: list[AttachmentMeta] = Field(default_factory=list)

    @staticmethod
    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None

    def to_db_row(self) -> dict[str, object | None]:
        """Vrať dict ve tvaru pro `db.insert_message` (JSON sloupce serializované)."""
        from .db import to_json

        return {
            "uid": self.uid,
            "folder": self.folder,
            "message_id": self.message_id,
            "in_reply_to": self.in_reply_to,
            "thread_refs": to_json(self.thread_refs) if self.thread_refs else None,
            "from_addr": self.from_addr,
            "from_name": self.from_name,
            "to_addrs": to_json(self.to_addrs) if self.to_addrs else None,
            "cc_addrs": to_json(self.cc_addrs) if self.cc_addrs else None,
            "subject": self.subject,
            "date_sent": self._iso(self.date_sent),
            "date_received": self._iso(self.date_received),
            "body_text": self.body_text,
            "body_html": self.body_html,
            "headers_raw": to_json(self.headers_raw) if self.headers_raw else None,
            "has_attachments": 1 if self.has_attachments else 0,
            "size_bytes": self.size_bytes,
        }
