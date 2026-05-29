# Návrhy — pokročilé analýzy a delegace/hlídání

Sepsáno 2026-05-17 po dokončení re-triage Review (fáze A–C). Cíl: maily pod kontrolou,
rychlá reakce, delegace s ověřením, že je úkol vyřešen.

## Stav po fázi A–C

- DB rozšířena: `messages.current_folder/current_uid` (kde mail teď leží),
  tabulka `message_analysis` (obsahová vrstva — vztah, záměr, shrnutí, „vyžaduje odpověď").
- Review: 1176 → **685** mailů (491 přesunuto: 268 doménových notifikací do nové
  `_mail.domeny`, 94 klientských do `_mail.klienti`, 104 SaaS/marketing do unimportant.sw, …).
- `rules.py` v6: nová kategorie `domeny`, klientské domény `ortex.cz`/`karlova-pekarna.cz`
  přesunuty z IMPORTANT do CLIENT.

---

## A) Pokročilé analýzy (návrhy)

1. **Skutečná per-mail shrnutí (LLM).** Teď je `summary` odvozené z předmětu. Příští krok:
   Claude přečte tělo a vytvoří 1–2větné shrnutí + extrahuje konkrétní *požadavek*
   („klient chce X do data Y"). Dávkově přes `next-batch`-styl workflow.

2. **Rekonstrukce vláken + „otevřené smyčky".** Spárovat příchozí ↔ odeslané přes
   `In-Reply-To`/`References` + `thread_key`. Detekovat vlákna, kde **klient něco žádal
   a Zdeněk dosud neodpověděl** — to je nejcennější seznam „co dlužím".

3. **Prioritní skóre.** Kombinace `needs_reply` × důležitost odesílatele × stáří mailu
   × hodnota zakázky. Výstup: denní „co řešit dnes" top 10.

4. **Profil kontaktu.** Per e-mail/firma: historie komunikace, typická témata, poslední
   interakce, otevřené body, tón. Slouží přípravě odpovědí (návaznost na `persona.md`).

5. **RAG nad celým korpusem.** Embeddings (SQLite + `sqlite-vec` nebo FTS5) — při psaní
   odpovědi dotáhnout, jak se s daným člověkem/tématem komunikovalo dříve.

6. **Auto-koncepty rutinních odpovědí.** Pro opakující se typy (potvrzení schůzky,
   přeposlání faktury účetní, „díky, vyřízeno") generovat koncept automaticky dle
   `persona.md` + pipeline z `assets/signature/`.

7. **Detekce faktur a plateb z obsahu.** Z těla mailu tahat částky, VS, splatnosti →
   přehled závazků/pohledávek, párování s `_mail.Účetní`.

8. **Měsíční drift report.** Co přibývá v Junku/HITL bez patternu, noví odesílatelé,
   nové klientské domény → údržba `rules.py`.

---

## B) Delegace a hlídání vyřešení

Problém: u mailu, který je potřeba vyřešit, vědět **kdo to má**, **dokdy** a **že je hotovo**.

### Varianta 1 — Stavová vrstva v naší DB (nejrychlejší, doporučeno jako základ)
Nová tabulka `message_tasks`: `message_id`, `assigned_to`, `status`
(new/delegated/in_progress/waiting/done), `due_date`, `resolution_note`, `resolved_at`,
`resolved_msg_id` (odkaz na odeslanou odpověď). CLI `zdenda-mail delegate --message-id X
--to tonda --due 2026-05-20`. Hlídání: denní přehled „po termínu / čeká na reakci".
+ Žádná integrace, hned použitelné.  − Žije mimo firemní IS.

### Varianta 2 — Zápis úkolu do firemního IS („N")
Když mail vyžaduje akci, založit v IS úkol/zakázku s odkazem na mail (Message-ID).
Stav se pak čte zpět z IS. Potřebné: zjistit, zda IS má API / DB přístup / import.
+ Jeden zdroj pravdy, vidí celý tým.  − Závisí na možnostech IS — nutno prověřit.
**Doporučení:** Varianta 1 jako okamžitý základ, a most do IS (V2) přidat, až bude jasné,
co IS umí (API/DB). DB tabulka pak slouží jako fronta na synchronizaci do IS.

### Varianta 3 — IMAP keywords / složky jako stav
Stav přímo na mailu přes IMAP keywords (`$Delegated-Tonda`, `$Waiting`) nebo podsložky.
Vidět v každém klientovi i na mobilu, bez vlastní appky.
+ Nula navíc infrastruktury.  − Omezené metadata (dokdy, poznámka), hůř reportovat.

### Varianta 4 — Delegační mail + sledování odpovědi
`zdenda-mail delegate` rovnou připraví koncept přeposlání kolegovi (pipeline konceptů)
a založí task. „Vyřešeno" se detekuje automaticky: když na vlákno přijde/odejde odpověď
od pověřené osoby → status `done`.

### Hlídání „je vyřešeno"
Napříč variantami: vlákno je **vyřešené**, když po posledním příchozím mailu existuje
odeslaná odpověď (Sent) ve stejném vláknu, nebo je task `done`. Nevyřešená vlákna starší
než N dní → eskalace do denního přehledu.

---

## C) Automatizace běhu (issue z 2026-05-29)

Cíl: hodinový sort nově příchozích + týdenní persona refresh, bez ručního spouštění.

**Hodinový sort** (proveditelné dnes): cron → `zdenda-mail fetch` + apply (rule-based, žádný LLM). Heslo už v `.env`, neinteraktivní běh funguje. Pokryje rutinu (newslettery, doménové notifikace, klienty na whitelistu). Skutečně ambiguous maily půjdou do HITL/Review jako dnes.

**Týdenní persona refresh** (potřebuje LLM): `fetch-sent` + obsahová analýza odeslaných za posledních N dní → update `persona.md` + memory `email-writing-style`. Buď naplánovaná Claude Code session (skill `/schedule`), nebo akceptace pay-per-token API (proti původnímu designu).

**Plný obsahový triage** příchozích (LLM místo jen pravidel) — má smysl spustit ve stejném týdenním passu, ne hodinově (cena/čas).

## Doporučené pořadí

1. `message_tasks` tabulka + `delegate` a denní přehled „co dlužím / po termínu".
2. Otevřené smyčky (analýza #2) — napárovat příchozí ↔ Sent, vytvořit první seznam dluhů.
3. Per-mail LLM shrnutí (#1) pro Review backlog.
4. Prověřit API/DB firemního IS → most DB ↔ IS.
5. RAG (#5) + auto-koncepty (#6) pro rychlé reakce.
