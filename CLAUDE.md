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

### Server-side audit + reapply (v4)

- `zdenda-mail audit-server [--output FILE]` — **read-only** porovnání server-side rozmístění vs. aktuální DB klasifikace. Skenuje všechny `_mail.*` + `INBOX` + `Junk`, vrací breakdown mismatchů (current → predicted). Volitelně uloží detail do JSON.
- `zdenda-mail reapply [--apply] [--plan-output FILE]` — smíření server-side stavu s aktuální klasifikací. Default DRY-RUN. Pravidla:
  - **Respektuje user manual moves**: pokud poslední `actions.target ≠ current_folder`, mail je tam, kam ho user dal → SKIP.
  - **Žádný downgrade**: nepřesouvá ze známé `_mail.*` složky do `_mail.HITL` (unsure).
  - Aplikuje: INBOX → predicted (standard apply), nebo state drift po reclassify (`last_action.target == current ≠ predicted`).
  - `action_type='reapply'` v `actions` tabulce.

### Fáze 5 — Export pro učení (TBD)

- Export z `messages` + `classifications` + `human_labels` do JSONL.
- CLI: `zdenda-mail export-training --output training.jsonl`

## Klasifikační instrukce

Aktuální verze: **`v5-newsletter-cleanup-2026-05-14`** (kanonický zdroj: `src/zdenda_mail/rules.py`, funkce `classify(item)`).

Historie verzí:
- `v1-conservative-2026-05-11` (text v `classifier.py` jako `PROMPT_V1`)
- `v2-subcategories-2026-05-12` — 7 podsložek unimportant
- `v3-clients-2026-05-13` — kategorie `client` + `_mail.klienti`
- `v4-rental-firma-2026-05-14` — kategorie `rental` (`_mail.Najmy`) a `firma_budova` (`_mail.Firma-budova`); přesun `hkpe.cz`/`hkjihlava.cz` z CLIENT_DOMAINS/IMPORTANT_DOMAINS → KOMORA_DOMAINS; cleanup duplicit (`vnd.cz` byl v obou CLIENT i IMPORTANT); řada sender corrections (DJI, Loxone, businessprofile-google, families-noreply-google, support@ppl.cz, airbnb discover přesunuty mezi kategoriemi).
- `v5-newsletter-cleanup-2026-05-14` — vytřízdění 1019 nepřečtených HITL/Review. Nový "Exact-match overrides" blok v `classify()` (běží před IMPORTANT_DOMAINS suffix-matchem) — fix kolizí `prvni-pozice.com`, `collabim.cz`, `edc-cr.cz`. Rozšířené patterny: `egd.cz`/`dis.edc-cr.cz` (energie), `fio.cz` (banks), `airbnb`/`rcobchod`/`e-flypgs`/`cdkeysales` (eshops), `8020ai.co`/`mindvalley`/`deepstash`/`onedrive`/`capterra`/`complianz`/`claude.com`/`promotime`/`nascar`/`trademedia` (sw), `ppl.cz`/`balikovna.cz` (doprava). Nové spam patterny: `firezink.de`, `hymedimachinery.com`, `nextgroup.ge`. Aplikace: 587 přesunů, 0 chyb (1m 46s IMAP, 1.2s reclassify pro 8917 zpráv).
- `v6-domeny-interni-retriage-2026-05-17` — nové kategorie `domeny` (`_mail.domeny`, notifikace registrátora `nic.cz`) a `interni` (`_mail.interni`, interní pošta `prvni-pozice.com`/`michalmartinek.cz`); re-triage složky Review (1176→419, 757 přesunů); `ortex.cz`/`karlova-pekarna.cz` přesunuty z IMPORTANT_DOMAINS do CLIENT_DOMAINS. Migrace `003`: `messages.current_folder/current_uid` (kde mail teď reálně leží) + tabulka `message_analysis` (obsahová vrstva — relationship, intent, summary, needs_reply, suggested_folder). Návrhy dalších kroků v `navrhy.md`.

### Kategorie + subkategorie

- `invoice` → `_mail.Účetní` — JAKÝKOLIV doklad za platbu (i zálohová/proforma, výzva, upomínka).
- `rental` → `_mail.Najmy` — pošta od nájemníků a správce nemovitosti (`bytservis-ji.cz`, `peta9870@email.cz`). Kontroluje se PO invoice a PŘED client. (v4)
- `firma_budova` → `_mail.Firma-budova` — cenové nabídky pro firmu/budovu (`fabrego.cz`, `flexibox.cz`, `zasklej.to`). Kontroluje se po rental, před client. (v4)
- `domeny` → `_mail.domeny` — doménové notifikace registrátora (`nic.cz`): prodloužení, zrušení, změny, autorizační info. Automatické, ne osobní korespondence. (v6)
- `interni` → `_mail.interni` — interní firemní pošta (`prvni-pozice.com`, `michalmartinek.cz`) — komunikace týmu. (v6)
- `client` → `_mail.klienti` — mail od některé z ~532 klientských domén v `CLIENT_DOMAINS` (suffix-match). Kontroluje se PO `invoice/rental/firma_budova` a PŘED `important` — faktura od klienta jde do Účetní (reconciliation), ostatní pošta od klienta přeskakuje Review queue a jde rovnou do klientů.
- `important` → `_mail.Review` — vyžaduje reakci majitele (zákazník, dodavatel, banka, úřad, doména/hosting, osobní).
- `unimportant` → `_mail.unimportant` (s 7 podsložkami):
  - `banks` → `_mail.unimportant.banks` — banky a spořitelny (ČS, ČSOB, mBank, Revolut, XTB, KB, ...)
  - `energie` → `_mail.unimportant.energie` — energie a dobíjení (EON, ČEZ, EnelX, ChargePoint, PlugSurfing, Ionity, ...)
  - `eshops` → `_mail.unimportant.eshops` — eshopy (Alza, Decathlon, IKEA, Datart, Notino, Netflix, Booking, Airbnb, ...)
  - `develop` → `_mail.unimportant.develop` — dev nástroje (GitHub, GitLab, Docker, Vercel, Cloudflare, ...)
  - `sw` → `_mail.unimportant.sw` — ostatní SaaS a marketing aplikace (Spotify, Adobe, PayPal, MailerLite, ...)
  - `doprava` → `_mail.unimportant.doprava` — dopravci (DPD, PPL, FedEx, DHL)
  - `komora` → `_mail.unimportant.komora` — hospodářská komora
- `spam` → `Junk` — JEDNOZNAČNÝ scam/podvod + cold-B2B/výprodejové TLDs (.shop, .buzz, .za.com, .in.rs, .com.tr, czech-fake .eu domény).
- `unsure` → `_mail.HITL` — nelze rozhodnout / `confidence < 0.7` / soft-spam TLDs (.pl/.ru/.ua/.cn/.tw/.de) bez automated patternu.

### Bias

- Konzervativní: nejistota mezi `important`/`unimportant` → `unsure`.
- `confidence < 0.7` → `unsure` (override).
- Faktura má prioritu nad subjectem.
- `spam` pouze pro evidentní scam, ne pro otravný legit marketing.

### Sender types

`customer | supplier | bank | gov | service | marketing | personal | unknown`

### Whitelist a rulebook

Kanonický zdroj pravidel: `src/zdenda_mail/rules.py`. Obsahuje:
- `INVOICE_FROM_EXACT`, `INVOICE_FROM_CONTAINS` — známí dodavatelé fakturující
- `RENTAL_FROM_EXACT`, `RENTAL_DOMAINS` — nájemníci + správa nemovitosti (v4)
- `FIRMA_BUDOVA_FROM_EXACT`, `FIRMA_BUDOVA_DOMAINS` — cenové nabídky pro firmu/budovu (v4)
- `CLIENT_DOMAINS` — ~532 klientských domén (v3-clients-2026-05-13), suffix-match
- `IMPORTANT_DOMAINS`, `IMPORTANT_FROM_EXACT` — interní, govt, zákazníci (mimo CLIENT_DOMAINS), dodavatelé, osobní
- 7 párů `{SUB}_FROM_EXACT` + `{SUB}_DOMAINS` pro podsložky unimportant
- `COLD_SPAM_DOMAINS_RE`, `CZECH_FAKE_EU_DOMAINS`, `SPAM_TLDS_HARD`, `SPAM_TLDS_SOFT`
- `SCAM_SUBJECTS`, `GIBBERISH_LOCAL_RE`, `FIRSTNAME_LASTNAME_RE`

Když poznáš nový spam pattern nebo nového legit odesílatele, **uprav patterny v `rules.py`**, bump `PROMPT_VERSION` (např. `v2.1-...-2026-06-15`), a spusť `zdenda-mail reclassify --overwrite` pro reaplikaci.

### Měsíční audit z Junku

`zdenda-mail learn-from-junk --since-days 30` — vypíše top odesílatele z `Junk`, kteří nejsou pokrytí žádným patternem v `rules.py`. Workflow:
1. Projdi top položky.
2. Pokud opravdu spam → přidej do `COLD_SPAM_DOMAINS_RE` nebo `CZECH_FAKE_EU_DOMAINS`.
3. Pokud false-positive → přidej do `IMPORTANT_DOMAINS` nebo do odpovídající unimportant podsložky.
4. Bump `PROMPT_VERSION` a reclassify.

### Změna instrukcí

Změna textu/pravidel = **nový `version_tag`**. Stávající `prompt_versions` se nikdy nepřepisuje (audit klasifikací).

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
├── migrations/002_subcategory.sql
├── src/zdenda_mail/
│   ├── __init__.py
│   ├── cli.py              # typer entry
│   ├── config.py           # pydantic settings + toml loader
│   ├── imap_client.py      # imap_tools wrapper
│   ├── db.py               # sqlite3 + helpery
│   ├── fetcher.py          # Fáze 1
│   ├── classifier.py       # prompt v1 + DB helpery
│   ├── rules.py            # KANONICKÝ rulebook v2 + classify()
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
