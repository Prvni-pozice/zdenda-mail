"""Konfigurace projektu — `config.toml` (statický) + `.env` (uživatel/server)."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field


class ImapConfig(BaseModel):
    host: str
    port: int = 993
    use_ssl: bool = True
    inbox: str = "INBOX"
    # Složky, ze kterých se čte při `fetch` (defaultně jen `inbox`).
    # Junk se přidává proto, aby spam fungoval jako trénovací data.
    fetch_folders: list[str] = Field(default_factory=lambda: ["INBOX"])


class TargetsConfig(BaseModel):
    invoices: str
    unimportant: str
    important_review: str
    unsure: str


class BatchConfig(BaseModel):
    default_size: int = 50
    oldest_first: bool = True


class DbConfig(BaseModel):
    path: str = "./zdenda_mail.db"


class Config(BaseModel):
    imap: ImapConfig
    targets: TargetsConfig
    batch: BatchConfig
    db: DbConfig

    # Runtime — z .env (NIKDY heslo)
    imap_user: str = Field(default="")


def _project_root() -> Path:
    """Adresář, ze kterého spouštíme CLI (kde má být config.toml)."""
    return Path.cwd()


def load_config(toml_path: str | Path | None = None) -> Config:
    """Načti `config.toml` z working dir (nebo zadané cesty) + `.env`.

    `.env` načítáme do `os.environ` — NIKDY ho nevracíme jako součást
    `Config`, kromě nesensitivních polí (`IMAP_USER`). Heslo se získává
    runtime přes `getpass.getpass()` v CLI a do tohoto modulu se nedostane.
    """
    if toml_path is None:
        toml_path = _project_root() / "config.toml"
    toml_path = Path(toml_path)

    if not toml_path.is_file():
        raise FileNotFoundError(f"Konfigurační soubor nenalezen: {toml_path}")

    with toml_path.open("rb") as f:
        data = tomllib.load(f)

    load_dotenv(_project_root() / ".env", override=False)
    imap_user = os.getenv("IMAP_USER", "")

    return Config(
        imap=ImapConfig(**data["imap"]),
        targets=TargetsConfig(**data["targets"]),
        batch=BatchConfig(**data["batch"]),
        db=DbConfig(**data["db"]),
        imap_user=imap_user,
    )
