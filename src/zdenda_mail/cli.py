"""CLI entrypoint — `zdenda-mail` přes Typer.

Fáze 1: `init-db`, `fetch`, `setup-folders`.
Fáze 2: `next-batch`, `save-classification`, `stats`, `show`.
Další fáze (review, apply, export-training) přibydou později.
"""
from __future__ import annotations

import getpass
import json
import logging
import sys
from pathlib import Path

import typer
from imap_tools.errors import MailboxLoginError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import classifier, db, fetcher
from .config import load_config
from .imap_client import ensure_folders, open_mailbox

app = typer.Typer(
    name="zdenda-mail",
    help="IMAP fetcher + klasifikace přes Claude Code, SQLite úložiště.",
    no_args_is_help=True,
)

console = Console()
err_console = Console(stderr=True, style="red")


def _setup_logging(verbose: bool = False) -> None:
    """Logging bez tělových citlivých dat — formát s level prefix."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


@app.command("init-db")
def init_db_cmd(
    config: Path = typer.Option(
        Path("./config.toml"), "--config", "-c", help="Cesta ke config.toml"
    ),
) -> None:
    """Vytvoří SQLite soubor a aplikuje migrace (idempotentní)."""
    _setup_logging()
    cfg = load_config(config)

    db_path = db.init_db(cfg.db.path)
    console.print(f"[green]✓[/green] DB inicializována: [bold]{db_path}[/bold]")

    # Quick sanity: vypiš nalezené tabulky
    conn = db.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row["name"] for row in cur.fetchall()]
    finally:
        conn.close()
    console.print(f"  Tabulky: {', '.join(tables)}")


@app.command("fetch")
def fetch_cmd(
    config: Path = typer.Option(
        Path("./config.toml"), "--config", "-c", help="Cesta ke config.toml"
    ),
    batch: int = typer.Option(
        50,
        "--batch",
        "-n",
        help="Velikost batch — počet mailů ke stažení PER SLOŽKA z fetch_folders",
    ),
    folder: str | None = typer.Option(
        None,
        "--folder",
        "-f",
        help="Stáhni jen z této jedné složky (override fetch_folders z configu)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Stáhne hlavičky a vypíše tabulku, ALE nic neuloží"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Stáhne `batch` nejstarších nepřečtených mailů z každé fetch složky a uloží do SQLite.

    Heslo se zadává interaktivně přes `getpass.getpass()`. NIKDE se neukládá.
    """
    _setup_logging(verbose=verbose)
    cfg = load_config(config)

    if not cfg.imap_user:
        err_console.print(
            "Chybí IMAP_USER. Vytvoř `.env` ze souboru `.env.example` a doplň "
            "`IMAP_USER=...`."
        )
        raise typer.Exit(code=2)

    if not Path(cfg.db.path).is_file():
        err_console.print(
            f"DB soubor neexistuje: {cfg.db.path}. Spusť nejdříve `zdenda-mail init-db`."
        )
        raise typer.Exit(code=2)

    # Heslo runtime, NIKDY nikde mimo paměť tohoto procesu.
    try:
        password = getpass.getpass(f"IMAP heslo pro {cfg.imap_user}: ")
    except (EOFError, KeyboardInterrupt):
        err_console.print("Zrušeno uživatelem.")
        raise typer.Exit(code=130)

    if not password:
        err_console.print("Prázdné heslo — končím.")
        raise typer.Exit(code=2)

    folders_override = [folder] if folder else None

    try:
        stats = fetcher.run_fetch(
            cfg, password, batch=batch, dry_run=dry_run, folders=folders_override
        )
    except MailboxLoginError:
        err_console.print(
            "IMAP login selhal: neplatný uživatel nebo heslo. Heslo nikam neukládáme — "
            "zkus to znovu."
        )
        raise typer.Exit(code=1)
    except OSError as e:
        err_console.print(f"Síťová chyba při připojení k IMAP serveru: {e}")
        raise typer.Exit(code=1)
    finally:
        # Heslo z paměti smažeme, jak to Python umožní (nahradíme náhodným blobem).
        password = "x" * len(password) if password else ""
        del password

    # Hezký souhrn
    console.rule("Souhrn")
    console.print(f"Stáhnuto z IMAP:     [bold]{stats.fetched}[/bold]")
    console.print(f"Uloženo do DB:       [green]{stats.inserted}[/green]")
    console.print(f"Přeskočeno (existují): {stats.skipped_existing}")
    if stats.errors:
        console.print(f"[red]Chyby: {stats.errors}[/red]")
    if stats.last_uid is not None:
        console.print(f"Poslední UID:        {stats.last_uid}")
    if stats.per_folder:
        console.print("\n[bold]Per složka:[/bold]")
        for f, info in stats.per_folder.items():
            console.print(
                f"  {f:30s}  fetched={info['fetched']}  last_uid_in_db={info['last_uid_in_db']}"
            )


@app.command("setup-folders")
def setup_folders_cmd(
    config: Path = typer.Option(
        Path("./config.toml"), "--config", "-c", help="Cesta ke config.toml"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Vytvoří cílové IMAP složky z `[targets]` v `config.toml`, pokud chybí.

    Idempotentní — existující složky se přeskočí. Spouští se před prvním
    použitím Fáze 4 (MOVE operace). Heslo runtime přes `getpass.getpass()`.
    """
    _setup_logging(verbose=verbose)
    cfg = load_config(config)

    if not cfg.imap_user:
        err_console.print("Chybí IMAP_USER. Vytvoř `.env` z `.env.example`.")
        raise typer.Exit(code=2)

    try:
        password = getpass.getpass(f"IMAP heslo pro {cfg.imap_user}: ")
    except (EOFError, KeyboardInterrupt):
        err_console.print("Zrušeno uživatelem.")
        raise typer.Exit(code=130)

    if not password:
        err_console.print("Prázdné heslo — končím.")
        raise typer.Exit(code=2)

    targets = [
        cfg.targets.invoices,
        cfg.targets.unimportant,
        cfg.targets.important_review,
        cfg.targets.unsure,
        cfg.targets.spam,
    ]

    try:
        with open_mailbox(cfg.imap, cfg.imap_user, password) as box:
            created, existed = ensure_folders(box, targets)
    except MailboxLoginError:
        err_console.print("IMAP login selhal: neplatný uživatel nebo heslo.")
        raise typer.Exit(code=1)
    except OSError as e:
        err_console.print(f"Síťová chyba: {e}")
        raise typer.Exit(code=1)
    finally:
        password = "x" * len(password) if password else ""
        del password

    console.rule("Cílové složky")
    for f in created:
        console.print(f"[green]✓ vytvořeno:[/green] {f}")
    for f in existed:
        console.print(f"[dim]· existovalo:[/dim] {f}")

    console.print(
        f"\n[bold]Hotovo[/bold] — vytvořeno {len(created)}, existovalo {len(existed)}.\n"
        f"[dim]Pokud složky v mailovém klientovi nevidíš, přihlas je ručně "
        f"v jeho dialogu Subscribe folders / Spravovat odběry.[/dim]"
    )


def _connect_db_or_exit(cfg) -> "db.sqlite3.Connection":  # type: ignore[name-defined]
    """DB musí existovat (po `init-db`). Jinak hláška a exit 2."""
    if not Path(cfg.db.path).is_file():
        err_console.print(
            f"DB soubor neexistuje: {cfg.db.path}. Spusť nejdříve `zdenda-mail init-db`."
        )
        raise typer.Exit(code=2)
    return db.connect(cfg.db.path)


def _resolve_prompt_version(conn, tag: str | None) -> int:
    """Seed (idempotentně) aktuální prompt verzi a vrátí její `id`.

    `tag=None` → použij `classifier.CURRENT_PROMPT_TAG` + `PROMPT_V1`.
    Jiný `tag` → musí už v DB existovat (nepřipravuji text v kódu).
    """
    if tag is None or tag == classifier.CURRENT_PROMPT_TAG:
        with db.transaction(conn):
            return classifier.get_or_create_prompt_version(conn)
    cur = conn.execute(
        "SELECT id FROM prompt_versions WHERE version_tag = ?", [tag]
    )
    row = cur.fetchone()
    if row is None:
        err_console.print(
            f"Prompt version {tag!r} v DB neexistuje. "
            f"Aktuální verze v kódu je {classifier.CURRENT_PROMPT_TAG!r}."
        )
        raise typer.Exit(code=2)
    return int(row["id"])


@app.command("next-batch")
def next_batch_cmd(
    config: Path = typer.Option(
        Path("./config.toml"), "--config", "-c", help="Cesta ke config.toml"
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="Max počet mailů"),
    format: str = typer.Option(
        "json", "--format", "-f", help="json | table"
    ),
    prompt_version: str | None = typer.Option(
        None,
        "--prompt-version",
        help="version_tag prompt verze (default: aktuální v kódu)",
    ),
) -> None:
    """Vypíše nezklasifikované maily (vzhledem k dané prompt verzi) jako JSON.

    Default JSON → určeno pro čtení Claude Code session. `--format table`
    pro lidský preview.
    """
    _setup_logging()
    cfg = load_config(config)
    conn = _connect_db_or_exit(cfg)

    try:
        pv_id = _resolve_prompt_version(conn, prompt_version)
        items = classifier.next_batch(conn, limit=limit, prompt_version_id=pv_id)
    finally:
        conn.close()

    if format == "json":
        # Plain stdout (ne přes rich) — Claude Code parsuje JSON.
        sys.stdout.write(
            json.dumps(
                {
                    "prompt_version": prompt_version or classifier.CURRENT_PROMPT_TAG,
                    "prompt_version_id": pv_id,
                    "count": len(items),
                    "items": items,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        sys.stdout.write("\n")
        return

    if format != "table":
        err_console.print(f"Neznámý formát {format!r}. Povolené: json, table.")
        raise typer.Exit(code=2)

    table = Table(
        title=f"Nezklasifikované (prompt {prompt_version or classifier.CURRENT_PROMPT_TAG})",
        show_lines=False,
    )
    table.add_column("ID", justify="right", style="cyan")
    table.add_column("Folder", style="magenta")
    table.add_column("Date", style="dim")
    table.add_column("From", overflow="fold")
    table.add_column("Subject", overflow="fold")
    table.add_column("Att", justify="center")
    for it in items:
        table.add_row(
            str(it["id"]),
            it["folder"],
            (it["date_sent"] or "—")[:19],
            (it["from_addr"] or "")[:50],
            (it["subject"] or "")[:80],
            "✓" if it["has_attachments"] else "",
        )
    console.print(table)
    console.print(f"Vráceno: [bold]{len(items)}[/bold]")


@app.command("save-classification")
def save_classification_cmd(
    config: Path = typer.Option(
        Path("./config.toml"), "--config", "-c", help="Cesta ke config.toml"
    ),
    message_id: int | None = typer.Option(
        None, "--message-id", "-m", help="ID zprávy (z `next-batch`)"
    ),
    category: str | None = typer.Option(
        None, "--category", help=f"Jedna z: {', '.join(classifier.CATEGORIES)}"
    ),
    confidence: float | None = typer.Option(
        None, "--confidence", help="0.0 – 1.0"
    ),
    reason: str | None = typer.Option(
        None, "--reason", help="Krátké odůvodnění (volitelné, doporučené)"
    ),
    sender_type: str | None = typer.Option(
        None, "--sender-type", help=f"Volitelné. Jedna z: {', '.join(classifier.SENDER_TYPES)}"
    ),
    prompt_version: str | None = typer.Option(
        None, "--prompt-version", help="Default: aktuální verze v kódu"
    ),
    from_stdin: bool = typer.Option(
        False,
        "--stdin",
        help="Čti JSON array `[{message_id, category, confidence, reason?, sender_type?}, …]` ze stdin",
    ),
) -> None:
    """Ulož klasifikaci jedné zprávy, nebo batch přes `--stdin`."""
    _setup_logging()
    cfg = load_config(config)
    conn = _connect_db_or_exit(cfg)

    try:
        pv_id = _resolve_prompt_version(conn, prompt_version)

        if from_stdin:
            raw = sys.stdin.read()
            if not raw.strip():
                err_console.print("Stdin je prázdný.")
                raise typer.Exit(code=2)
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as e:
                err_console.print(f"Stdin není validní JSON: {e}")
                raise typer.Exit(code=2)
            if not isinstance(payload, list):
                err_console.print("Stdin musí být JSON array of objects.")
                raise typer.Exit(code=2)

            saved = 0
            errors = 0
            with db.transaction(conn):
                for i, item in enumerate(payload):
                    try:
                        classifier.save_classification(
                            conn,
                            message_id=int(item["message_id"]),
                            prompt_version_id=pv_id,
                            category=str(item["category"]),
                            confidence=float(item["confidence"]),
                            reason=item.get("reason"),
                            sender_type=item.get("sender_type"),
                        )
                        saved += 1
                    except (KeyError, ValueError, TypeError) as e:
                        errors += 1
                        err_console.print(
                            f"  [red]chyba u položky #{i}[/red]: {e}"
                        )
            console.print(
                f"[green]Uloženo:[/green] {saved}, [red]chyb:[/red] {errors}"
            )
            if errors and not saved:
                raise typer.Exit(code=1)
            return

        # Single mode — všechny required povinné
        missing = [
            name
            for name, val in {
                "--message-id": message_id,
                "--category": category,
                "--confidence": confidence,
            }.items()
            if val is None
        ]
        if missing:
            err_console.print(
                f"Chybí povinné: {', '.join(missing)}. Nebo použij `--stdin`."
            )
            raise typer.Exit(code=2)

        try:
            with db.transaction(conn):
                cls_id = classifier.save_classification(
                    conn,
                    message_id=int(message_id),  # type: ignore[arg-type]
                    prompt_version_id=pv_id,
                    category=str(category),
                    confidence=float(confidence),  # type: ignore[arg-type]
                    reason=reason,
                    sender_type=sender_type,
                )
        except ValueError as e:
            err_console.print(f"{e}")
            raise typer.Exit(code=2)

        console.print(
            f"[green]✓[/green] uloženo (classification id={cls_id}): "
            f"msg={message_id} cat={category} conf={confidence}"
        )
    finally:
        conn.close()


@app.command("stats")
def stats_cmd(
    config: Path = typer.Option(
        Path("./config.toml"), "--config", "-c", help="Cesta ke config.toml"
    ),
    prompt_version: str | None = typer.Option(
        None, "--prompt-version", help="Default: aktuální v kódu"
    ),
) -> None:
    """Souhrn — klasifikováno / nezklasifikováno / per kategorie."""
    _setup_logging()
    cfg = load_config(config)
    conn = _connect_db_or_exit(cfg)

    try:
        pv_id = _resolve_prompt_version(conn, prompt_version)
        s = classifier.stats(conn, prompt_version_id=pv_id)
    finally:
        conn.close()

    tag = prompt_version or classifier.CURRENT_PROMPT_TAG
    console.rule(f"Statistiky — prompt {tag}")
    console.print(f"V DB celkem:        [bold]{s['total_messages']}[/bold]")
    console.print(f"Klasifikováno:      [green]{s['classified']}[/green]")
    console.print(f"Nezklasifikováno:   [yellow]{s['unclassified']}[/yellow]")

    if s["per_category"]:
        console.print("\n[bold]Per kategorie:[/bold]")
        for cat, n in s["per_category"].items():
            console.print(f"  {cat:13s}  {n}")
    if s["per_sender_type"]:
        console.print("\n[bold]Per sender_type:[/bold]")
        for st, n in s["per_sender_type"].items():
            console.print(f"  {st:13s}  {n}")


@app.command("show")
def show_cmd(
    config: Path = typer.Option(
        Path("./config.toml"), "--config", "-c", help="Cesta ke config.toml"
    ),
    message_id: int = typer.Option(..., "--message-id", "-m"),
    format: str = typer.Option(
        "panel", "--format", "-f", help="panel | json"
    ),
    full_body: bool = typer.Option(
        False, "--full-body", help="Vypiš celé tělo (default: oříznuto na 4000 znaků)"
    ),
) -> None:
    """Plný detail mailu (pro deep-dive při nejasné klasifikaci)."""
    _setup_logging()
    cfg = load_config(config)
    conn = _connect_db_or_exit(cfg)
    try:
        msg = classifier.get_message_full(conn, message_id=message_id)
    finally:
        conn.close()

    if msg is None:
        err_console.print(f"message_id={message_id} v DB neexistuje.")
        raise typer.Exit(code=2)

    if format == "json":
        sys.stdout.write(json.dumps(msg, ensure_ascii=False, indent=2, default=str))
        sys.stdout.write("\n")
        return

    if format != "panel":
        err_console.print(f"Neznámý formát {format!r}. Povolené: panel, json.")
        raise typer.Exit(code=2)

    header_lines = [
        f"[bold]ID:[/bold] {msg['id']}   "
        f"[bold]UID:[/bold] {msg['uid']}   "
        f"[bold]Folder:[/bold] {msg['folder']}",
        f"[bold]From:[/bold] {msg.get('from_name') or ''} <{msg['from_addr']}>",
        f"[bold]To:[/bold] {', '.join(msg.get('to_addrs') or []) or '—'}",
        f"[bold]Date:[/bold] {msg.get('date_sent') or '—'}",
        f"[bold]Subject:[/bold] {msg.get('subject') or '—'}",
    ]
    console.print(Panel("\n".join(header_lines), title="Hlavička"))

    if msg["attachments"]:
        atable = Table(title="Přílohy", show_header=True)
        atable.add_column("Filename", overflow="fold")
        atable.add_column("MIME")
        atable.add_column("Size", justify="right")
        for a in msg["attachments"]:
            atable.add_row(
                a["filename"] or "—",
                a["mime_type"] or "—",
                str(a["size_bytes"]) if a["size_bytes"] is not None else "—",
            )
        console.print(atable)

    if msg["classifications"]:
        ctable = Table(title="Klasifikace (historie)", show_header=True)
        ctable.add_column("Tag", style="dim")
        ctable.add_column("Cat")
        ctable.add_column("Conf", justify="right")
        ctable.add_column("Sender")
        ctable.add_column("By", style="dim")
        ctable.add_column("When", style="dim")
        ctable.add_column("Reason", overflow="fold")
        for c in msg["classifications"]:
            ctable.add_row(
                c["version_tag"],
                c["category"],
                f"{c['confidence']:.2f}",
                c["sender_type"] or "—",
                c["classified_by"],
                c["created_at"],
                c["reason"] or "",
            )
        console.print(ctable)

    body = msg.get("body_text") or "[dim](mail nemá body_text — jen HTML)[/dim]"
    if not full_body and len(body) > 4000:
        body = body[:4000] + "\n\n[dim]…(oříznuto, použij --full-body)[/dim]"
    console.print(Panel(body, title="Body (text)"))


def main() -> None:
    """Entry point pro `python -m zdenda_mail` nebo `uv run zdenda-mail`."""
    app()


if __name__ == "__main__":
    main()
