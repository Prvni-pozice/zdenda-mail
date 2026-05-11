# CLAUDE.md — zdenda-mail

Tento dokument je **vstup pro každou Claude Code session** v tomto projektu. Drž se fází, nepředbíhej.

## Cíl projektu

Roztřídit ~5 000 nepřečtených mailů na IMAP serveru `1pmail.cz` (účet `zdenek@prvni-pozice.com`) pomocí LLM klasifikace. Všechna data ukládat do SQLite pro pozdější fine-tuning / supervised learning. Postupovat **od nejstarších nepřečtených směrem k novějším**.

## Důležité design rozhodnutí: žádná Anthropic API integrace

Klasifikaci dělá samotný Claude Code v rámci běžící session — ne přes externí API call. Není potřeba `anthropic` SDK ani API klíč. Workflow (Fáze 2+):

1. Skript `zdenda-mail next-batch --limit 20` vypíše nezklasifikované maily z DB do stdout (JSON).
2. Claude (v Claude Code session) si je přečte, pro každý rozhodne kategorii.
3. Claude zavolá `zdenda-mail save-classification --message-id X --category Y --confidence Z --reason "..."` (nebo zapíše dávkově přes JSON).
4. Opakuje, dokud zbývají nezklasifikované, nebo dokud nedojde kontext / nerozhodne pauzu.

Tím využíváme Claude Code subscription, ne pay-per-token API.

## Tech stack

- Python 3.11+, `uv` pro správu závislostí
- `imap_tools` (IMAP klient)
- `sqlite3` ze stdlib, režim WAL
- `pydantic` v2 (validace dat)
- `rich` (CLI tabulky, progress bar)
- `python-dotenv` (config)
- `typer` (CLI s subcommands)

Žádné: `anthropic` SDK, žádný API klíč.

## Architektura ve fázích

**KAŽDÁ FÁZE = SAMOSTATNÝ COMMIT.** Neimplementuj následující fázi, dokud aktuální není funkční a otestovaná.

### Fáze 1 — Fetch + cache  ✓ implementováno

1. Připoj se k IMAP, autentizuj heslem z `getpass.getpass()` (zadané runtime, nikam neuložené).
2. Seřaď nepřečtené maily ve `INBOX` od nejstarších (`imap_tools` `fetch(criteria=AND(seen=False))`, sortuj v Pythonu podle `date` ASC).
3. Stáhni batch (default 50, configurable přes `--batch`), parsuj headers + body + attachments metadata (název, MIME type, velikost — NE obsah příloh).
4. Ulož do SQLite (idempotentně podle `uid` + `folder`).
5. Checkpoint: poslední zpracované UID v tabulce `runs`.
6. NIC NEKLASIFIKUJ. NIC NEPŘESOUVEJ. NIC NEOZNAČUJ JAKO PŘEČTENÉ (`mark_seen=False`).
7. CLI: `zdenda-mail fetch --batch 50 [--dry-run]`

### Fáze 2 — Klasifikační CLI (TBD)

Žádný "classifier" v kódu — jen helpery, které Claude Code session používá:

- `zdenda-mail next-batch --limit 20 [--format json]` — vrátí nezklasifikované maily (id, from, subject, date, snippet body).
- `zdenda-mail save-classification --message-id X --category Y --confidence 0.92 --reason "..." [--sender-type ...]` — uloží do `classifications`. Podporuje i batch přes stdin JSON.
- `zdenda-mail stats` — kolik je klasifikovaných / nezklasifikovaných / podle kategorií.
- `zdenda-mail show --message-id X` — full text mailu pro deep-dive, když je nejasné.

V CLAUDE.md projektu (sekce níže) bude klasifikační instrukce — definice kategorií, whitelist odesílatelů, konzervativní bias atd. Verzování přes git.

### Fáze 3 — Review CLI (TBD)

- `zdenda-mail review --filter "confidence<0.85"` — tabulka v terminálu (rich), interaktivní schválení / oprava klasifikace.
- Tvoje korekce se ukládají do `human_labels` — trénovací data.

### Fáze 4 — Apply (TBD)

- Aplikace rozhodnutí na IMAP: `COPY` do cílové složky, pak `\Seen` flag, pak `MOVE` (až s `--confirm` flagem).
- Defaultně `--dry-run`. Bez `--apply` neudělá nic destruktivního.
- Faktury: stáhne PDF přílohy do `./invoices/YYYY-MM/`, NEFORWARDUJE automaticky.

### Fáze 5 — Export pro učení (TBD)

- Export z `messages` + `classifications` + `human_labels` do JSONL.
- CLI: `zdenda-mail export-training --output training.jsonl`

## Klasifikační instrukce

Aktuální verze: **`v1-conservative-2026-05-11`** (text je v `src/zdenda_mail/classifier.py` jako `PROMPT_V1`).

Zrcadleno tady pro lidské čtení — kanonický zdroj je kód (z něj se seeduje do `prompt_versions`).

### Kategorie

- `invoice` → `_mail.Účetní` — JAKÝKOLIV doklad za platbu (i zálohová/proforma, výzva, upomínka).
- `important` → `_mail.Review` — vyžaduje reakci majitele (zákazník, dodavatel, banka, úřad, doména/hosting, osobní).
- `unimportant` → `_mail.Archive` — marketing legitimní firmy, automaty, notifikace, statistiky.
- `spam` → `Junk` — JEDNOZNAČNÝ scam/podvod (Viagra, „rychlé zbohatnutí", nigerijský princ, krypto scamy, phishing).
- `unsure` → `_mail.HITL` — nelze rozhodnout / `confidence < 0.7`.

### Bias

- Konzervativní: nejistota mezi `important`/`unimportant` → `unsure`.
- `confidence < 0.7` → `unsure` (override).
- Faktura má prioritu nad subjectem.
- `spam` pouze pro evidentní scam, ne pro otravný legit marketing.

### Sender types

`customer | supplier | bank | gov | service | marketing | personal | unknown`

### Whitelist

Zatím prázdný — doplní se z `human_labels` po Fázi 3.

### Změna instrukcí

Změna textu = **nový `version_tag`**. Stávající `prompt_versions` se nikdy nepřepisuje (audit klasifikací).

## Bezpečnostní constraints (NEPORUŠOVAT)

1. **Heslo k IMAPu se NIKDY nikam neukládá** — ani do logu, ani do env, ani do DB. Jen v paměti běžícího procesu.
2. **Žádný destruktivní IMAP příkaz bez `--apply` flagu.** Default je dry-run.
3. **Žádný auto-forward emailů.** Faktury se jen stahují lokálně.
4. **Logging:** maskovat hesla. Tělo mailu NElogovat — je v DB.
5. **SQLite soubor je v `.gitignore`** (`*.db`, `*.db-wal`, `*.db-shm`).
6. **Před `MOVE` operacemi** udělej IMAP zálohu (export do `.mbox`) — `zdenda-mail backup` subcommand (Fáze 4).
7. **SQLite zálohuj:** `sqlite3 zdenda_mail.db ".backup zdenda_mail.bak"` (online backup).

## Acceptance criteria pro Fázi 1

- `zdenda-mail init-db` vytvoří soubor `zdenda_mail.db` a všechny tabulky, zapne WAL + foreign keys.  ✓
- `zdenda-mail fetch --batch 10` stáhne 10 nejstarších nepřečtených, uloží do SQLite, neoznačí je jako přečtené na serveru.  ✓
- `zdenda-mail fetch --batch 10` znovu spuštěn pokračuje od posledního UID (přeskočí již uložené).  ✓
- `sqlite3 zdenda_mail.db "SELECT count(*), min(date_sent), max(date_sent) FROM messages"` ukáže rozumná data.  ✓
- Test s nevalidním heslem → čitelná chybová hláška, žádný traceback.  ✓
- `--dry-run` flag nic nezapisuje do SQLite.  ✓

## Pravidla práce s SQLite v Pythonu

- `sqlite3.connect(path)`, `conn.row_factory = sqlite3.Row`. ✓
- JSON sloupce serializuj přes `json.dumps()` na vstupu, `json.loads()` na výstupu (helpers v `db.py`). ✓
- `with conn:` (resp. `db.transaction(conn)`) pro automatický commit/rollback. ✓
- Batch insert přes `executemany()`. ✓

## Konfigurace

- `config.toml` — IMAP host, cílové složky, batch default, cesta k DB. **V gitu.**
- `.env` — `IMAP_USER`. **V `.gitignore`** (commit jen `.env.example`).
- Heslo — runtime, `getpass.getpass()`. Nikde.

## Struktura repozitáře

```
zdenda-mail/
├── pyproject.toml
├── README.md
├── CLAUDE.md
├── config.toml
├── .env.example
├── .gitignore
├── migrations/001_init.sql
├── src/zdenda_mail/
│   ├── __init__.py
│   ├── cli.py              # typer entry
│   ├── config.py           # pydantic settings + toml loader
│   ├── imap_client.py      # imap_tools wrapper
│   ├── db.py               # sqlite3 + helpery
│   ├── fetcher.py          # Fáze 1
│   └── models.py           # pydantic schemas
└── tests/test_fetcher.py
```

## Přístupy

```
IMAP_HOST: imap.1pmail.cz
IMAP_PORT: 993
IMAP_USER: zdenek@prvni-pozice.com
IMAP_PASS: __zadává se runtime přes getpass, neukládat__

DB_PATH: ./zdenda_mail.db (vytvoří se automaticky)
```
