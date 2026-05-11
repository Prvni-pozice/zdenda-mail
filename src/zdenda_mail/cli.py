"""CLI entrypoint — `zdenda-mail` přes Typer.

Fáze 1: subcommands `init-db` a `fetch`. Ostatní fáze (next-batch,
save-classification, review, apply, export-training) přibydou později.
"""
from __future__ import annotations

import getpass
import logging
import sys
from pathlib import Path

import typer
from imap_tools.errors import MailboxLoginError
from rich.console import Console

from . import db, fetcher
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
        f"\n[bold]Hotovo[/bold] — vytvořeno {len(created)}, existovalo {len(existed)}."
    )


def main() -> None:
    """Entry point pro `python -m zdenda_mail` nebo `uv run zdenda-mail`."""
    app()


if __name__ == "__main__":
    main()
