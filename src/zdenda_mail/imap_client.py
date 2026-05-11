"""IMAP wrapper postavený nad `imap_tools`. Read-only ve Fázi 1.

Bezpečnostní invariant: žádná destruktivní operace v této vrstvě. Heslo
prochází funkčním argumentem, NIKDY se neukládá do atributu instance déle,
než je nutné pro `MailBox.login()`.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from imap_tools import AND, MailBox, MailBoxUnencrypted

from .config import ImapConfig
from .models import AttachmentMeta, MailMessage

if TYPE_CHECKING:
    from imap_tools.message import MailMessage as RawMessage


@contextmanager
def open_mailbox(
    cfg: ImapConfig, user: str, password: str
) -> Iterator[MailBox | MailBoxUnencrypted]:
    """Otevřená IMAP relace jako context manager. `password` se po loginu
    nepředává dál — `imap_tools` ho drží internímu socketu, my ne.
    """
    if cfg.use_ssl:
        box: MailBox | MailBoxUnencrypted = MailBox(cfg.host, port=cfg.port)
    else:
        box = MailBoxUnencrypted(cfg.host, port=cfg.port)
    box.login(user, password, initial_folder=cfg.inbox)
    try:
        yield box
    finally:
        try:
            box.logout()
        except Exception:
            pass


def _to_attachment_meta(att) -> AttachmentMeta:  # type: ignore[no-untyped-def]
    return AttachmentMeta(
        filename=getattr(att, "filename", None),
        mime_type=getattr(att, "content_type", None),
        size_bytes=getattr(att, "size", None),
    )


def _to_mail_message(raw: "RawMessage", folder: str) -> MailMessage:
    """Konvertuj `imap_tools.MailMessage` na náš pydantic model."""
    has_atts = bool(raw.attachments)
    return MailMessage(
        uid=int(raw.uid) if raw.uid else 0,
        folder=folder,
        message_id=raw.headers.get("message-id", [None])[0] if raw.headers else None,
        in_reply_to=raw.headers.get("in-reply-to", [None])[0] if raw.headers else None,
        thread_refs=_parse_references(raw.headers.get("references", [None])[0] if raw.headers else None),
        from_addr=raw.from_,
        from_name=raw.from_values.name if raw.from_values else None,
        to_addrs=list(raw.to or []),
        cc_addrs=list(raw.cc or []),
        subject=raw.subject,
        date_sent=raw.date,
        date_received=raw.date,
        body_text=raw.text or None,
        body_html=raw.html or None,
        headers_raw={k: list(v) for k, v in (raw.headers or {}).items()},
        has_attachments=has_atts,
        size_bytes=raw.size_rfc822 if hasattr(raw, "size_rfc822") else None,
        attachments=[_to_attachment_meta(a) for a in raw.attachments],
    )


def _parse_references(header_value: str | None) -> list[str]:
    """`References:` je whitespace-separated seznam `<message-id>` tokenů."""
    if not header_value:
        return []
    return [tok for tok in header_value.split() if tok]


def fetch_unseen(
    box: MailBox | MailBoxUnencrypted,
    *,
    folder: str,
    limit: int,
    skip_uids: set[int] | None = None,
    oldest_first: bool = True,
) -> list[MailMessage]:
    """Stáhne nepřečtené zprávy z `folder` v daném počtu.

    - Přepne aktivní složku přes `box.folder.set(folder)`.
    - `mark_seen=False` — server NEZMĚNÍ `\\Seen` flag.
    - `oldest_first=True` — řadí podle `date` ASC v Pythonu (IMAP `SEARCH` to
      negarantuje napříč servery).
    - `skip_uids` — UID už uložené v DB, přeskočí (idempotence).
    """
    skip = skip_uids or set()

    box.folder.set(folder)

    raw_msgs: list = []
    for msg in box.fetch(AND(seen=False), mark_seen=False, bulk=True):
        if msg.uid is None:
            continue
        try:
            uid = int(msg.uid)
        except ValueError:
            continue
        if uid in skip:
            continue
        raw_msgs.append(msg)

    raw_msgs.sort(key=lambda m: (m.date is None, m.date), reverse=not oldest_first)
    raw_msgs = raw_msgs[:limit]

    return [_to_mail_message(m, folder) for m in raw_msgs]


def ensure_folders(
    box: MailBox | MailBoxUnencrypted, folders: list[str]
) -> tuple[list[str], list[str]]:
    """Vytvoří složky, které na serveru chybí.

    Vrací `(created, already_existed)`. Pojmenování složek na IMAP serveru
    používá hierarchický delimiter (typicky `/` nebo `.`) — používáme to,
    jak je v konfiguraci (typicky `INBOX/_mail/Účetní`). `imap_tools.folder.create`
    si delimiter přizpůsobí.
    """
    existing = {f.name for f in box.folder.list()}
    created: list[str] = []
    skipped: list[str] = []
    for f in folders:
        if f in existing:
            skipped.append(f)
            continue
        box.folder.create(f)
        created.append(f)
    return created, skipped
