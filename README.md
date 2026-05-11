# zdenda-mail

IMAP mail fetcher + klasifikace přes Claude Code, SQLite úložiště pro fine-tuning data.

**Účet:** `zdenek@prvni-pozice.com` na `imap.1pmail.cz`
**Cíl:** Roztřídit ~5 000 nepřečtených e-mailů od nejstarších, ukládat data pro pozdější supervised learning.

## Aktuální stav

- **Fáze 1 — Fetch + cache** ✓ implementováno
- Fáze 2 — Klasifikační CLI (next-batch / save-classification / stats / show) — TBD
- Fáze 3 — Review CLI — TBD
- Fáze 4 — Apply na IMAP — TBD
- Fáze 5 — Export pro učení — TBD

## Tech stack

Python 3.11+ s `uv` pro správu závislostí. SQLite (WAL) ze stdlib. `imap_tools`, `pydantic v2`, `typer`, `rich`, `python-dotenv`.

**Žádné Anthropic API.** Klasifikaci provádí Claude Code v interaktivní session — viz `CLAUDE.md`.

## Setup

```bash
cd /data/bot/zdenda-mail
uv sync                                 # nainstaluje závislosti
cp .env.example .env                    # IMAP_USER (heslo NIKDY do .env)
uv run zdenda-mail init-db              # vytvoří zdenda_mail.db
```

## CLI (Fáze 1)

```bash
# Stažení 50 nejstarších nepřečtených (default batch)
uv run zdenda-mail fetch

# Manuální velikost batch + dry-run pro test
uv run zdenda-mail fetch --batch 10 --dry-run

# Help
uv run zdenda-mail --help
```

Heslo se zadává interaktivně přes `getpass.getpass()` — nikam se neukládá.

## Sanity check

```bash
sqlite3 zdenda_mail.db "SELECT count(*), min(date_sent), max(date_sent) FROM messages"
```

## Bezpečnost

1. **Heslo k IMAPu se NIKDE neukládá** — ani v .env, ani v DB, ani v logu.
2. **`mark_seen=False`** — fetch nikdy nemění `\Seen` flag na serveru.
3. **Žádný destruktivní příkaz bez `--apply`** (relevantní až ve Fázi 4).
4. **DB soubor je v `.gitignore`** — `*.db`, `*.db-wal`, `*.db-shm`.

## Testy

```bash
uv run pytest -q
```

13 unit testů pokrývá DB vrstvu, JSON adapter, idempotenci insertu, FK kaskádu a model serializaci.

## Struktura

```
zdenda-mail/
├── pyproject.toml
├── config.toml             # IMAP host, target složky, batch limit
├── .env.example
├── migrations/001_init.sql # kompletní DDL
├── src/zdenda_mail/
│   ├── cli.py              # typer entry
│   ├── config.py           # pydantic settings + tomllib
│   ├── db.py               # SQLite + JSON helpery
│   ├── imap_client.py      # imap_tools wrapper
│   ├── fetcher.py          # Fáze 1 orchestrátor
│   └── models.py           # pydantic schemas
└── tests/test_fetcher.py
```
