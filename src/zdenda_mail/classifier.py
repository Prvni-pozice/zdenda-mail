"""Fáze 2 — klasifikační helpery.

Nedělá samotnou klasifikaci — tu provádí Claude Code session, která tento
modul přes CLI volá. Tady jsou jen:

- konstanty s aktuální verzí prompt instrukcí (`CURRENT_PROMPT_TAG`, `PROMPT_V1`)
- `get_or_create_prompt_version()` — idempotentní seed do `prompt_versions`
- `next_batch()`, `save_classification()`, `stats()`, `get_message_full()`

Validace vstupů (kategorie, confidence rozsah) je tady, ne v CLI.
"""
from __future__ import annotations

import sqlite3
from typing import Any

CATEGORIES = ("invoice", "important", "unimportant", "unsure", "spam")
SENDER_TYPES = (
    "customer",
    "supplier",
    "bank",
    "gov",
    "service",
    "marketing",
    "personal",
    "unknown",
)

CURRENT_PROMPT_TAG = "v1-conservative-2026-05-11"

PROMPT_V1 = """\
# Klasifikační instrukce v1 (konzervativní, 2026-05-11)

Tříděný účet: zdenek@prvni-pozice.com (Prvni-pozice s.r.o., majitel).

## Kategorie (právě jedna)

- `invoice` — JAKÝKOLIV doklad za platbu zboží/služby: ostrá daňová faktura,
  zálohová faktura, proforma, účtenka, výzva k úhradě, upomínka. Typicky PDF
  příloha + IČO/DIČ + variabilní symbol + splatnost. Cíl: `_mail.Účetní`.

- `important` — vyžaduje reakci/rozhodnutí majitele:
  - zákazníci (poptávka, dotaz, reklamace, smlouva)
  - dodavatelé (mimo faktury — např. dotaz, změna podmínek)
  - banky, úřady, datové schránky, finanční správa
  - doména/hosting (vypršení, technický problém)
  - osobní/B2B konverzace
  Cíl: `_mail.Review`.

- `unimportant` — automat, marketing, statistika, newsletter, „neotevřeli jste…",
  GitHub digest, social media notifikace, registrace cizích služeb, drobné
  potvrzení o doručení. Cíl: `_mail.Archive`.

- `spam` — zjevný spam/scam: Viagra a podobné léky, „rychlé zbohatnutí",
  podvodné nabídky dědictví („nigerijský princ"), kryptoměnové scamy, fake
  invoice scams, phishing, sexuální nabídky, neexistující výhry. Cíl: `Junk`.

- `unsure` — nejde rozhodnout z dostupných informací. Cíl: `_mail.HITL`.

## Konzervativní bias

- Když váháš mezi `important` a `unimportant` → `unsure`.
- `confidence < 0.7` → vždy `unsure` (i kdyby byl odhad jiný).
- Faktura má prioritu — pokud je v příloze PDF s charakterem dokladu, je to
  `invoice` i kdyby byl předmět neutrální.
- `spam` jen pro JEDNOZNAČNÝ scam/podvod. Marketing legitimní firmy (i otravný)
  je `unimportant`, NE `spam`.

## Sender type (volitelný tag)

`customer | supplier | bank | gov | service | marketing | personal | unknown`

## Whitelist (důvěryhodní odesílatelé → spíš `important`)

Zatím prázdné. Doplní se postupně z `human_labels`.

## Junk složka (na vstupu)

Maily, které UŽ JSOU v `Junk` (server je tam označil jako spam):
- pokud opravdu vypadají jako spam → `spam`
- pokud vypadají jako legit firma/marketing → `unimportant` + `sender_type=marketing`
- zjevná legit faktura nebo zákazník (false positive) → `unsure` s `reason`
  vysvětlujícím proč to mohlo skončit ve Spamu.
"""


def get_or_create_prompt_version(
    conn: sqlite3.Connection,
    *,
    tag: str = CURRENT_PROMPT_TAG,
    instructions: str = PROMPT_V1,
    notes: str | None = None,
) -> int:
    """Vrátí `id` prompt verze. Idempotentní — pokud `tag` existuje, jen ho vrátí.

    Pozor: pokud `tag` existuje, ale `instructions` se liší od uloženého textu,
    NEPŘEPISUJEME — vyhodíme `ValueError`. To je úmyslné: změna instrukcí musí
    znamenat nový `tag` (kvůli auditu, ke které verzi se která klasifikace váže).
    """
    cur = conn.execute(
        "SELECT id, instructions FROM prompt_versions WHERE version_tag = ?", [tag]
    )
    row = cur.fetchone()
    if row is not None:
        if row["instructions"] != instructions:
            raise ValueError(
                f"Prompt tag {tag!r} už existuje, ale text se liší. "
                "Místo přepsání použij nový version_tag."
            )
        return int(row["id"])

    cur = conn.execute(
        "INSERT INTO prompt_versions (version_tag, instructions, notes) "
        "VALUES (?, ?, ?)",
        [tag, instructions, notes],
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def next_batch(
    conn: sqlite3.Connection,
    *,
    limit: int,
    prompt_version_id: int,
    snippet_chars: int = 400,
) -> list[dict[str, Any]]:
    """Nezklasifikované maily (vůči dané prompt verzi), seřazené podle `date_sent` ASC.

    "Nezklasifikovaný" = neexistuje žádná řádka v `classifications` pro tuto
    `(message_id, prompt_version_id)` kombinaci. Switch prompt verze = reklasifikace.

    Snippet je prefix `body_text` (HTML-only se nezpracovává — Claude si to dotáhne přes `show`).
    """
    from . import db as _db

    cur = conn.execute(
        """
        SELECT m.id, m.uid, m.folder, m.from_addr, m.from_name,
               m.subject, m.date_sent, m.body_text, m.has_attachments
          FROM messages m
         WHERE NOT EXISTS (
                SELECT 1 FROM classifications c
                 WHERE c.message_id = m.id
                   AND c.prompt_version_id = ?
            )
         ORDER BY m.date_sent ASC, m.id ASC
         LIMIT ?
        """,
        [prompt_version_id, limit],
    )
    rows = cur.fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        msg_id = int(r["id"])
        attachments: list[dict[str, Any]] = []
        if r["has_attachments"]:
            acur = conn.execute(
                "SELECT filename, mime_type, size_bytes "
                "FROM attachments WHERE message_id = ?",
                [msg_id],
            )
            attachments = [
                {
                    "filename": a["filename"],
                    "mime_type": a["mime_type"],
                    "size_bytes": a["size_bytes"],
                }
                for a in acur.fetchall()
            ]

        body = r["body_text"] or ""
        snippet = body[:snippet_chars]
        if len(body) > snippet_chars:
            snippet += "…"

        out.append(
            {
                "id": msg_id,
                "uid": r["uid"],
                "folder": r["folder"],
                "from_addr": r["from_addr"],
                "from_name": r["from_name"],
                "subject": r["subject"],
                "date_sent": r["date_sent"],
                "snippet": snippet,
                "has_attachments": bool(r["has_attachments"]),
                "attachments": attachments,
            }
        )
    _ = _db  # avoid unused warning if removed later
    return out


def save_classification(
    conn: sqlite3.Connection,
    *,
    message_id: int,
    prompt_version_id: int,
    category: str,
    confidence: float,
    reason: str | None = None,
    sender_type: str | None = None,
    classified_by: str = "claude_code",
) -> int:
    """Vlož klasifikaci. Validuje kategorii, sender_type a rozsah `confidence`.

    Idempotence: pro stejné `(message_id, prompt_version_id)` se NEPŘEPISUJE
    — vznikne nová řádka (kvůli auditu). Pokud chceš overwrite, smaž ručně.
    """
    if category not in CATEGORIES:
        raise ValueError(
            f"Neznámá kategorie {category!r}. Povolené: {', '.join(CATEGORIES)}"
        )
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence musí být v [0.0, 1.0], dostal jsem {confidence}")
    if sender_type is not None and sender_type not in SENDER_TYPES:
        raise ValueError(
            f"Neznámý sender_type {sender_type!r}. Povolené: {', '.join(SENDER_TYPES)}"
        )

    # FK kontrola — pokud message_id neexistuje, INSERT shodí FK ON, takže
    # není potřeba dvojí dotaz, ale chceme čitelnou hlášku:
    cur = conn.execute("SELECT 1 FROM messages WHERE id = ?", [message_id])
    if cur.fetchone() is None:
        raise ValueError(f"message_id={message_id} v DB neexistuje")

    cur = conn.execute(
        "INSERT INTO classifications "
        "(message_id, prompt_version_id, category, confidence, reason, sender_type, classified_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            message_id,
            prompt_version_id,
            category,
            confidence,
            reason,
            sender_type,
            classified_by,
        ],
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def stats(
    conn: sqlite3.Connection, *, prompt_version_id: int
) -> dict[str, Any]:
    """Souhrn: kolik je v DB, kolik klasifikovaných pro danou verzi, per kategorie."""
    total = conn.execute("SELECT count(*) AS n FROM messages").fetchone()["n"]
    classified = conn.execute(
        "SELECT count(DISTINCT message_id) AS n FROM classifications "
        "WHERE prompt_version_id = ?",
        [prompt_version_id],
    ).fetchone()["n"]
    unclassified = total - classified

    per_cat_cur = conn.execute(
        """
        SELECT category, count(*) AS n
          FROM classifications
         WHERE prompt_version_id = ?
         GROUP BY category
         ORDER BY n DESC
        """,
        [prompt_version_id],
    )
    per_category = {row["category"]: row["n"] for row in per_cat_cur.fetchall()}

    per_sender_cur = conn.execute(
        """
        SELECT sender_type, count(*) AS n
          FROM classifications
         WHERE prompt_version_id = ? AND sender_type IS NOT NULL
         GROUP BY sender_type
         ORDER BY n DESC
        """,
        [prompt_version_id],
    )
    per_sender = {row["sender_type"]: row["n"] for row in per_sender_cur.fetchall()}

    return {
        "total_messages": total,
        "classified": classified,
        "unclassified": unclassified,
        "per_category": per_category,
        "per_sender_type": per_sender,
    }


def get_message_full(
    conn: sqlite3.Connection, *, message_id: int
) -> dict[str, Any] | None:
    """Plný detail mailu + přílohy + případné klasifikace (napříč verzemi)."""
    from . import db as _db

    cur = conn.execute("SELECT * FROM messages WHERE id = ?", [message_id])
    row = cur.fetchone()
    if row is None:
        return None

    msg = dict(row)
    msg["thread_refs"] = _db.from_json(msg.get("thread_refs"))
    msg["to_addrs"] = _db.from_json(msg.get("to_addrs"))
    msg["cc_addrs"] = _db.from_json(msg.get("cc_addrs"))
    msg["headers_raw"] = _db.from_json(msg.get("headers_raw"))

    acur = conn.execute(
        "SELECT filename, mime_type, size_bytes, saved_path "
        "FROM attachments WHERE message_id = ?",
        [message_id],
    )
    msg["attachments"] = [dict(a) for a in acur.fetchall()]

    ccur = conn.execute(
        """
        SELECT c.id, c.category, c.confidence, c.reason, c.sender_type,
               c.classified_by, c.created_at, p.version_tag
          FROM classifications c
          JOIN prompt_versions p ON p.id = c.prompt_version_id
         WHERE c.message_id = ?
         ORDER BY c.created_at DESC
        """,
        [message_id],
    )
    msg["classifications"] = [dict(c) for c in ccur.fetchall()]

    return msg
