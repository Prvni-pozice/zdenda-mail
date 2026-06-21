#!/usr/bin/env python3
"""Záloha mailů z reapply plánu do .mbox (před MOVE operacemi).

Pro každou zdrojovou složku stáhne přesně UID z plánu a appendne raw zprávu
do jednoho .mbox souboru. MOVE na IMAPu je sice nedestruktivní (mail se nemaže),
ale .mbox je pojistka dle CLAUDE.md bod 6.
"""
import json
import sys
from collections import defaultdict
from imap_tools import MailBox, AND
from zdenda_mail.config import load_config

PLAN = "reapply_plan.json"
OUT = "backup-reapply-2026-06-21.mbox"


def main():
    cfg = load_config("config.toml")
    plan = json.load(open(PLAN))
    by_folder = defaultdict(list)
    for q in plan:
        by_folder[q["current"]].append(int(q["uid"]))

    import os
    pw = os.environ.get("IMAP_PASS") or cfg.imap_pass
    total = 0
    with open(OUT, "wb") as out:
        with MailBox(cfg.imap.host, cfg.imap.port).login(cfg.imap_user, pw) as box:
            for folder, uids in by_folder.items():
                box.folder.set(folder)
                got = 0
                for i in range(0, len(uids), 200):
                    chunk = uids[i:i + 200]
                    uidset = ",".join(str(u) for u in chunk)
                    for msg in box.fetch(AND(uid=uidset), mark_seen=False, bulk=True):
                        raw = msg.obj.as_bytes()
                        out.write(b"From reapply-backup@zdenda-mail\n")
                        out.write(raw)
                        if not raw.endswith(b"\n"):
                            out.write(b"\n")
                        out.write(b"\n")
                        got += 1
                        total += 1
                print(f"  {folder}: zálohováno {got}/{len(uids)}")
    print(f"Celkem zálohováno: {total} → {OUT}")


if __name__ == "__main__":
    main()
