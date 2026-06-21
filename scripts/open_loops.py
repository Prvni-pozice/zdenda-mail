#!/usr/bin/env python3
"""Otevřené smyčky — vlákna, kde přišel mail a Zdeněk neodpověděl.

Párování:
  1) Thread-level: (korespondent, normalizovaný subject). Vlákno je OTEVŘENÉ,
     když poslední příchozí v něm je novější než poslední odeslaný (nebo žádný
     odeslaný neexistuje).
  2) Override přes message_id: pokud message_id posledního příchozího je
     referencováno v některém Sent (in_reply_to / References), vlákno je
     uzavřené i kdyby subject-match selhal.

Výstup: open_loops.md (segmentováno podle kategorie = člověk vs automat).
"""
import sqlite3
import json
import re
from collections import defaultdict
from datetime import datetime, timezone

DB = "zdenda_mail.db"
ME = "zdenek@prvni-pozice.com"
OUT = "open_loops.md"

# Kategorie, které reálně mohou být "dluh" (lidská korespondence).
HUMAN_CATS = {"important", "client", "rental", "firma_budova", "invoice", "interni", "unsure"}
# Zbytek (unimportant*, domeny, spam) = automat/bulk — jen napočítat.

RE_PREFIX = re.compile(r"^\s*(re|fwd|fw|odp|aw|vs|sv)\s*:\s*", re.IGNORECASE)
RE_MSGID = re.compile(r"<[^>]+>")

# Lokální části adres, které znamenají "neodpovídá se" (automat/notifikace).
NOREPLY_LOCAL = re.compile(
    r"(no[._-]?reply|noreply|do[._-]?not[._-]?reply|robot|mailer|mailer-daemon|"
    r"notifikace|notification|notifications|alert|alerts|bounce|postmaster|"
    r"automat|auto[._-]?confirm|system|nepovidat|neodpovidejte)",
    re.IGNORECASE,
)


# Domény, které jsou důležité i přes noreply odesílatele (akce nutná v systému).
NOREPLY_EXEMPT = ("mssf.cz",)


def is_noreply(corr: str) -> bool:
    dom = corr.split("@", 1)[1] if "@" in corr else ""
    if any(dom == d or dom.endswith("." + d) for d in NOREPLY_EXEMPT):
        return False
    local = corr.split("@", 1)[0] if "@" in corr else corr
    return bool(NOREPLY_LOCAL.search(local))


def norm_subject(s: str) -> str:
    s = s or ""
    prev = None
    while prev != s:
        prev = s
        s = RE_PREFIX.sub("", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def parse_dt(s: str):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def emails_from_json(s: str):
    if not s:
        return []
    try:
        arr = json.loads(s)
    except Exception:
        return [s]
    out = []
    for x in arr:
        m = re.search(r"[\w.+-]+@[\w.-]+", str(x))
        if m:
            out.append(m.group(0).lower())
    return out


def email_of(addr: str) -> str:
    if not addr:
        return ""
    m = re.search(r"[\w.+-]+@[\w.-]+", addr)
    return m.group(0).lower() if m else addr.strip().lower()


def main():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row

    # --- nejnovější klasifikace per message ---
    cat = {}
    for r in c.execute("""
        WITH latest AS (
          SELECT message_id, category, subcategory,
            ROW_NUMBER() OVER (PARTITION BY message_id ORDER BY created_at DESC, id DESC) rn
          FROM classifications)
        SELECT message_id, category, subcategory FROM latest WHERE rn=1
    """):
        cat[r["message_id"]] = (r["category"], r["subcategory"])

    # --- Sent: referenced ids + per-correspondent thread dates ---
    referenced = set()          # message_ids, na které Zdeněk odpověděl
    sent_thread = defaultdict(lambda: None)   # (corr, nsubj) -> max sent date
    sent_to_any = defaultdict(lambda: None)   # corr -> max sent date (jakákoliv pošta)
    for r in c.execute("SELECT to_addrs, cc_addrs, subject, in_reply_to, thread_refs, date_sent FROM messages WHERE folder='Sent'"):
        d = parse_dt(r["date_sent"])
        if r["in_reply_to"]:
            for m in RE_MSGID.findall(r["in_reply_to"]):
                referenced.add(m)
            if "<" not in r["in_reply_to"]:
                referenced.add(r["in_reply_to"].strip())
        if r["thread_refs"]:
            for m in RE_MSGID.findall(r["thread_refs"]):
                referenced.add(m)
        nsubj = norm_subject(r["subject"])
        recips = emails_from_json(r["to_addrs"]) + emails_from_json(r["cc_addrs"])
        for corr in recips:
            if corr == ME:
                continue
            key = (corr, nsubj)
            if d and (sent_thread[key] is None or d > sent_thread[key]):
                sent_thread[key] = d
            if d and (sent_to_any[corr] is None or d > sent_to_any[corr]):
                sent_to_any[corr] = d

    # --- Incoming: vše mimo Sent a Junk, ne od Zdeňka ---
    threads = {}  # (corr, nsubj) -> dict
    for r in c.execute("""
        SELECT id, folder, current_folder, from_addr, from_name, subject,
               message_id, date_sent
        FROM messages
        WHERE folder NOT IN ('Sent','Junk')
    """):
        corr = email_of(r["from_addr"])
        if not corr or corr == ME:
            continue
        d = parse_dt(r["date_sent"])
        nsubj = norm_subject(r["subject"])
        key = (corr, nsubj)
        t = threads.get(key)
        category = cat.get(r["id"], ("?", None))[0]
        if t is None:
            t = {
                "corr": corr, "from_name": r["from_name"] or "",
                "subject": r["subject"] or "(bez předmětu)",
                "n": 0, "last_date": None, "last_msgid": None,
                "last_folder": None, "cats": set(),
            }
            threads[key] = t
        t["n"] += 1
        t["cats"].add(category)
        if d and (t["last_date"] is None or d > t["last_date"]):
            t["last_date"] = d
            t["last_msgid"] = r["message_id"]
            t["last_folder"] = r["current_folder"] or r["folder"]

    # --- určit otevřené ---
    open_threads = []
    for key, t in threads.items():
        corr, nsubj = key
        last_in = t["last_date"]
        # odpovězeno přes message_id?
        if t["last_msgid"] and t["last_msgid"] in referenced:
            continue
        # odpovězeno přes thread subject?
        sd = sent_thread.get(key)
        if sd and last_in and sd >= last_in:
            continue
        # odpovězeno jakoukoliv pozdější poštou témuž korespondentovi se shodným subj? (už pokryto sent_thread)
        # -> otevřené
        t["last_in"] = last_in
        # primární kategorie vlákna = "nejlidštější"
        cats = t["cats"]
        prim = next((x for x in ["important", "client", "invoice", "rental", "firma_budova", "interni", "unsure", "domeny"] if x in cats), None)
        if prim is None:
            prim = next(iter(cats)) if cats else "?"
        t["prim"] = prim
        open_threads.append(t)

    return c, open_threads


def write_report(c, open_threads):
    human = [t for t in open_threads if t["prim"] in HUMAN_CATS and not is_noreply(t["corr"])]
    auto = [t for t in open_threads if not (t["prim"] in HUMAN_CATS and not is_noreply(t["corr"]))]

    # human seřadit: prioritní kategorie, pak nejnovější první
    catrank = {"important": 0, "client": 1, "invoice": 2, "rental": 3, "firma_budova": 4, "interni": 5, "unsure": 6}
    human.sort(key=lambda t: (catrank.get(t["prim"], 9), t["last_in"] or datetime.min.replace(tzinfo=timezone.utc)), reverse=False)
    human.sort(key=lambda t: (catrank.get(t["prim"], 9), -(t["last_in"].timestamp() if t["last_in"] else 0)))

    by_cat = defaultdict(int)
    for t in open_threads:
        by_cat[t["prim"]] += 1

    lines = []
    lines.append("# Otevřené smyčky — „co dlužím\"")
    lines.append("")
    lines.append(f"Vygenerováno {datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M')}. ")
    lines.append("Vlákno = (odesílatel, normalizovaný předmět). Otevřené = poslední zpráva je příchozí "
                 "a neexistuje pozdější odeslaná odpověď ve stejném vláknu (ani reference na message_id).")
    lines.append("")
    lines.append(f"**Otevřených vláken celkem: {len(open_threads)}**  "
                 f"(lidská korespondence: {len(human)}, automat/bulk: {len(auto)})")
    lines.append("")
    lines.append("## Přehled podle kategorie")
    lines.append("")
    lines.append("| kategorie | otevřených vláken |")
    lines.append("|---|---|")
    for cat_, n in sorted(by_cat.items(), key=lambda x: -x[1]):
        lines.append(f"| {cat_} | {n} |")
    lines.append("")

    lines.append("## Reálné dluhy (lidská korespondence)")
    lines.append("")
    lines.append("Seřazeno: priorita kategorie → nejnovější první.")
    lines.append("")
    cur = None
    for t in human:
        if t["prim"] != cur:
            cur = t["prim"]
            lines.append("")
            lines.append(f"### {cur}")
            lines.append("")
            lines.append("| poslední příchozí | odesílatel | předmět | # | složka |")
            lines.append("|---|---|---|---|---|")
        ds = t["last_in"].strftime("%Y-%m-%d") if t["last_in"] else "?"
        name = (t["from_name"] or "").replace("|", "/")[:30]
        who = f"{name} <{t['corr']}>" if name else t["corr"]
        subj = t["subject"].replace("|", "/").replace("\n", " ")[:70]
        lines.append(f"| {ds} | {who} | {subj} | {t['n']} | {t['last_folder'] or '?'} |")
    lines.append("")

    lines.append("## Automat / bulk bez odpovědi (appendix — jen souhrn)")
    lines.append("")
    auto_by_cat = defaultdict(int)
    for t in auto:
        auto_by_cat[t["prim"]] += 1
    lines.append("| kategorie | otevřených vláken |")
    lines.append("|---|---|")
    for cat_, n in sorted(auto_by_cat.items(), key=lambda x: -x[1]):
        lines.append(f"| {cat_} | {n} |")
    lines.append("")
    lines.append("_Automatické/bulk kategorie (newslettery, doménové notifikace, eshopy…) "
                 "nepředstavují dluh na odpověď — neenumerováno._")

    with open(OUT, "w") as f:
        f.write("\n".join(lines))
    print(f"Zapsáno: {OUT}")
    print(f"Otevřených vláken: {len(open_threads)} (lidská: {len(human)}, automat: {len(auto)})")
    print("Top kategorie:", dict(sorted(by_cat.items(), key=lambda x: -x[1])[:8]))


if __name__ == "__main__":
    c, open_threads = main()
    write_report(c, open_threads)
