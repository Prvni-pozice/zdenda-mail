"""Fáze 1 — stažení batch nepřečtených mailů a uložení do SQLite.

Idempotentní: opakované volání pokračuje od posledního UID v DB (resp.
přeskočí UIDy, které už jsou uložené).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from . import db
from .config import Config
from .imap_client import fetch_unseen, open_mailbox
from .models import MailMessage

logger = logging.getLogger(__name__)
console = Console()


@dataclass
class FetchStats:
    """Výsledek běhu (agregát napříč všemi složkami)."""

    fetched: int = 0
    inserted: int = 0
    skipped_existing: int = 0
    errors: int = 0
    last_uid: int | None = None
    per_folder: dict[str, dict[str, int]] | None = None


def run_fetch(
    cfg: Config,
    password: str,
    *,
    batch: int,
    dry_run: bool = False,
    folders: list[str] | None = None,
) -> FetchStats:
    """Stáhni `batch` nepřečtených z každé složky v `folders`, ulož do DB.

    - `folders` defaultně z `cfg.imap.fetch_folders` (typicky `INBOX` + `Junk`).
    - `batch` se aplikuje **per složka** (ne celkově).
    - Heslo dostane jen jednou, dál se k němu nevracíme.
    """
    folders = folders or cfg.imap.fetch_folders or [cfg.imap.inbox]
    stats = FetchStats(per_folder={})

    # Connect k DB (i v dry-run režimu, abychom věděli, co skipnout)
    conn = db.connect(cfg.db.path)
    run_id: int | None = None

    if not dry_run:
        with db.transaction(conn):
            run_id = db.start_run(
                conn, command=f"fetch --batch {batch} folders={','.join(folders)}"
            )

    try:
        with open_mailbox(cfg.imap, cfg.imap_user, password) as box:
            all_messages: list[MailMessage] = []
            for folder in folders:
                last_uid_in_db = db.get_last_uid(conn, folder)
                try:
                    folder_messages = fetch_unseen(
                        box,
                        folder=folder,
                        limit=batch,
                        skip_uids=_known_uids(conn, folder),
                        oldest_first=cfg.batch.oldest_first,
                    )
                except Exception as e:
                    stats.errors += 1
                    logger.exception("Chyba při fetch ze složky %r: %s", folder, e)
                    console.print(f"[red]Chyba u složky {folder}: {e}[/red]")
                    continue

                stats.per_folder[folder] = {
                    "fetched": len(folder_messages),
                    "last_uid_in_db": last_uid_in_db or 0,
                }
                all_messages.extend(folder_messages)

            stats.fetched = len(all_messages)

        if not all_messages:
            console.print(
                f"[yellow]Žádné nové nepřečtené maily v žádné z {folders}[/yellow]"
            )
            return stats

        if dry_run:
            _print_dry_run_preview(all_messages)
            return stats

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Ukládám do DB", total=len(all_messages))

            with db.transaction(conn):
                for msg in all_messages:
                    try:
                        msg_id = db.insert_message(conn, msg.to_db_row())
                        if msg_id is None:
                            stats.skipped_existing += 1
                        else:
                            stats.inserted += 1
                            if msg.attachments:
                                db.insert_attachments(
                                    conn,
                                    msg_id,
                                    [a.model_dump() for a in msg.attachments],
                                )
                            if stats.last_uid is None or msg.uid > stats.last_uid:
                                stats.last_uid = msg.uid
                    except Exception as e:
                        stats.errors += 1
                        logger.exception("Chyba u UID=%s: %s", msg.uid, e)
                    progress.advance(task)

        return stats

    finally:
        if run_id is not None and not dry_run:
            with db.transaction(conn):
                db.finish_run(
                    conn,
                    run_id,
                    last_uid=stats.last_uid,
                    messages_processed=stats.inserted,
                    errors_count=stats.errors,
                )
        conn.close()


def _known_uids(conn, folder: str) -> set[int]:  # type: ignore[no-untyped-def]
    """Načti všechny UIDy daného folderu — pro malé DB (5000) v pohodě."""
    cur = conn.execute("SELECT uid FROM messages WHERE folder = ?", [folder])
    return {row["uid"] for row in cur.fetchall()}


def _print_dry_run_preview(messages: list[MailMessage]) -> None:
    """Vypiš tabulku, co bychom uložili — bez zápisu do DB."""
    from rich.table import Table

    table = Table(title="[DRY-RUN] Maily, které by se uložily", show_lines=False)
    table.add_column("Folder", style="magenta")
    table.add_column("UID", justify="right", style="cyan")
    table.add_column("Date", style="dim")
    table.add_column("From", overflow="fold")
    table.add_column("Subject", overflow="fold")
    table.add_column("Att", justify="center")

    for m in messages:
        table.add_row(
            m.folder,
            str(m.uid),
            (m.date_sent.isoformat() if m.date_sent else "—")[:19],
            (m.from_addr or "")[:60],
            (m.subject or "")[:80],
            "✓" if m.has_attachments else "",
        )

    console.print(table)
    console.print(
        f"[yellow]DRY-RUN[/yellow] — nic se neuložilo. Připravených k uložení: {len(messages)}"
    )
