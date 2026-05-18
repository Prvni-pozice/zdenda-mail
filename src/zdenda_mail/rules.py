"""Pravidla pro klasifikaci mailů + mapování na cílové IMAP složky.

Verze: v4-rental-firma-2026-05-14

Tento modul je **kanonický rulebook**. Když poznáš nový vzor (nový odesílatel
v INBOX, nový spam pattern, jiný typ legit notifikace), uprav patterny tady a
udělej commit. Stejné rule sety se používají pro:

- klasifikaci nově fetchnutých mailů (`classify(item)`)
- reklasifikaci historicky uložených mailů (`zdenda-mail reclassify`)
- learn-from-junk audit (`zdenda-mail learn-from-junk`)

Funkce `classify(item)` vrací `RuleResult` = (category, subcategory, confidence,
sender_type, reason). `subcategory` je relevantní jen pro `category="unimportant"`
a říká, do které z 7 podsložek mail patří.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

PROMPT_VERSION = "v6-domeny-interni-retriage-2026-05-17"


@dataclass
class RuleResult:
    category: str                  # invoice | client | rental | firma_budova | important | unimportant | spam | unsure
    subcategory: str | None        # banks|energie|eshops|develop|sw|doprava|komora|None
    confidence: float
    sender_type: str               # customer|supplier|bank|gov|service|marketing|personal|unknown
    reason: str


# ============================================================================
# INVOICE — vždy faktura / platební doklad
# ============================================================================

INVOICE_FROM_EXACT = {
    # Energie / dobíjení
    "e-bill@eon.cz", "pohledavky@eon.cz", "eonpohledavky@eon.cz",
    "vyuctovani@cez.cz", "upominani_neodpovidat@cez.cz",
    "fakturace@pre.cz",
    # Tisk / dodavatelé
    "info@lukashron.cz",
    "postak@polygonhradec.cz", "lanska@polygonhradec.cz",
    "gartom@seznam.cz",
    "fakturace@pelhrimovska.cz",
    "info@pneuprodejna.cz",
    # SaaS receipts
    "invoice+statements@midjourney.com",
    "telusbilling@info.telus.com",
    "payments@comgate.cz",
    "help@paddle.com",
    "appstore@insideapple.apple.com",
    # PSN
    "reply@txn-email.playstation.com",
    "sony@txn-email03.playstation.com",
    # Bambulab
    "noreply@bambulab.com",
    # Shoptet — TODO podmínečně
    "notification@shoptet.cz",  # daňový doklad
    # v4: Booking payment receipts (potvrzení o platbě) → účetní
    "noreply-payments@booking.com",
}

INVOICE_FROM_CONTAINS = (
    "invoice+statements",   # Stripe / SaaS receipt
    "@stripe.com",
    "@fakturoidmail.cz",
)


# ============================================================================
# CLIENT — explicitní seznam klientských domén (zákazníci 1pa zákazníků)
#
# Pořadí v classify(): kontrola PO INVOICE a PŘED IMPORTANT.
# Důvod: faktura od klienta → _mail.Účetní (účetní reconciliation),
# ostatní pošta od klienta → _mail.klienti (skip Review queue).
#
# Suffix-match: subdoména klientské domény (`shop.X.cz`) také matchne.
# Z user-poskytnutého seznamu byly vyřazeny:
#   - `prvni-pozice.com` (vlastní interní infrastruktura — viz IMPORTANT_DOMAINS)
#   - 11 redundantních subdomén, kde už je rodičovská doména v seznamu
# ============================================================================

CLIENT_DOMAINS = {
    # v6-2026-05-17: přesunuto z IMPORTANT_DOMAINS — jde o klientské domény
    "ortex.cz", "karlova-pekarna.cz",
    "1p-maintenance.com", "1pdesign.com", "2dweb.cz", "2kk-restaurant.cz", "2safe4u.cz", "a-gastro.eu",
    "abaj.cz", "acre.cz", "actris-ri.cz", "addinol-ce.pl", "addinol.cz", "addinol.sk",
    "aditeg.cz", "aditivni.cz", "admasys.cz", "agos.cz", "agrostroj.cz", "airthink.cz",
    "ak-rozehnal.cz", "alfa-top.cz", "alpen-invest.cz", "altumare.cz", "amadeusfin.cz", "ambishop.cz",
    "amotiq.cz", "anch.cz", "andretea.cz", "anglickyresort.cz", "anglictina-pelhrimov.cz", "anglictinajihlava.cz",
    "anglictinapelhrimov.cz", "aquaprogress.cz", "arcade-europe.eu", "arreda.cz", "arvo.cz", "asekol-solar.cz",
    "asekol.cz", "asekol.pl", "asekol.sk", "asekolsolar.cz", "aso.cz", "asociacezzs.cz",
    "asproject.eu", "atelier-radost.cz", "autex.sk", "auto-jamar.cz", "autobejda.cz", "autola.cz",
    "automatnamobily.cz", "autoneza.cz", "autoskola-budejovice.cz", "autoskola-trebic.cz", "babypartner.cz", "balnya.cz",
    "barvy-sanmarco.cz", "baspol.cz", "behprousmev.cz", "benestruhlarstvi.cz", "better-process.cz", "bezlepkovyfrgal.cz",
    "bjallra.eu", "bjallraofsweden.sk", "bjs-kompresory.cz", "bohemia-crystalglass.com", "bohemia-crystalglass.cz", "bohemiaagro.cz",
    "bohemiatreasury.cz", "bonmoto.cz", "bookinagency.cz", "brixen.cz", "brno.city.cz", "bugaboo-bazar.cz",
    "ca-laguna.cz", "carbonwave.cz", "caresse.cz", "casopis-inspirace.cz", "cattlemarket.cz", "ceeor.com",
    "cenoskok.cz", "centrum-truck.cz", "cervenarecice.cz", "cervenekontejnery.cz", "cervenekontejnery.pl", "cervenekontejnery.sk",
    "cesnek-cesky.cz", "cesnek-vysocina.cz", "cestr.cz", "chicbypig.com", "chysna.cz", "chytryhracky.cz",
    "cime-shop.cz", "cis.cz", "cisteni-kobercu-brno.eu", "cisteni-kobercu-praha.net", "ckmarketa.cz", "cokovino.cz",
    "comatex.eu", "covid19movie.cz", "covidor.cz", "danmoguls.cz", "dareknaprani.cz", "davidfoto.cz",
    "davidphotography.cz", "dcprofi.cz", "delnak.cz", "demark.cz", "detailingpro.cz", "diton.cz",
    "diton.sk", "dobrozahrada.cz", "domyaplus.com", "domyaplus.cz", "doprava-pelhrimov.cz", "dostupnepotraviny.cz",
    "doudou.cz", "dpp.cz", "dprogram.cz", "drmax.promotime.net", "duotrans.cz", "dup.cz",
    "dvaftom.cz", "dvuranezka.cz", "e-repro.cz", "e-sea.cz", "eco-building.cz", "eco-house.cz",
    "ecomole.com", "edilgru.cz", "efektivni-bydleni.cz", "effepizeta.cz", "egardens.cz", "ekogreen.cz",
    "ekoimpex.cz", "elasticr.cz", "elektro-instalacni-material.cz", "elektrochmelar.com", "elektrohned.cz", "elektrolavicka.cz",
    "elektrospotrebic.com", "elektrosrot.cz", "elite-energy.cz", "elite-facility.cz", "emitis.cz", "energy-management.cz",
    "enviropol.cz", "enviropol.sk", "envogue.sk", "epodhajska.eu", "equitum.cz", "equivet.cz",
    "equivet.sk", "erdenet.cz", "eshop-topteam.cz", "eshop.elpe.cz", "eshopsanmarco.cz", "eshub.cz",
    "esic.cz", "eturbo.cz", "euro-jackpot.org", "euro-palety.cz", "eurobyt-jihlava.cz", "europaleta.eu",
    "fahe.cz", "farmaservis.cz", "fdinterier.cz", "fdreality.cz", "fert.cz", "festivalkrbu.cz",
    "fhlhumpolec.cz", "fifty-fifty.cz", "fimo-hmota.cz", "finance-bulldog.cz", "fkmaratonpelhrimov.cz", "fkmpelhrimov.cz",
    "fkpelhrimov.cz", "florbalak.cz", "flowtech.cz", "folietuning.cz", "forsapi.cz", "fotokoutky.cz",
    "funzorbing.cz", "gadeo.cz", "galaxieknih.cz", "gasoilfashion.cz", "gasparin.ch", "gistrans.cz",
    "glenowell.cz", "goldnutrition.cz", "graneo.cz", "hala-fert.eu", "hana-veronika.cz", "hd-el.cz",
    "hezke-obrazy.cz", "hezkykryty.cz", "hiestech.com", "highhack.cz", "hlouch.cz",
    "hnspirit.cz", "hodinky-kubu.cz", "hoeko.cz", "hotelartaban.cz", "houpejse.cz", "hracky-drevene.eu",
    "hrackyuzabicek.cz", "hrackyzvysociny.cz", "hradeckypacov.cz", "hydro-balance.cz", "iaqua.cz", "imusicdata.cz",
    "institut-kavy.cz", "institutkavy.sk", "intelsol.cz", "intelstyle.cz", "iqkonstrukt.cz", "itc-real.com",
    "itkc.cz", "jenicek-vseprodite.cz", "jihlava.city.cz", "jnservis.cz", "kaap.cz", "kanalizacezplastu.cz",
    "karelfink.cz", "karta.city.cz", "kaspercz.cz", "kavarna-pelhrimov.cz", "khkvysocina.cz", "kia-jihlava.cz",
    "kia-pelhrimov.cz", "kia-vysocina.cz", "kingspas.cz", "kitl.cz", "klenoty-kamenice.cz", "knofliky.cz",
    "kokardy.cz", "kokinokrmiva.cz", "kolahory.cz", "kominy-komitech.cz", "komobjekt.cz", "koralek-obchod.cz",
    "koralky-komponenty.com", "koralky-navody.cz", "koralky-online.cz", "kos-real.cz", "kosyka.cz", "kozlovnauzachu.cz",
    "kpzas.cz", "krby.komitech.cz", "krytynaiphone.cz", "kuks-as.cz", "kukuricnymlyn.cz", "kvetinovafarma.com",
    "kvetinovykuryr.eu", "lamartina-studio.cz", "landeco.cz", "lapek.cz", "laura-crystal.cz", "laznicka.cz",
    "lean-digital-twin-training.com", "lean-fabrika.cz", "lekarna-luziny.cz", "lekarnik.promotime.net", "lekarny-pro-prahu.cz", "lekarskepraxe.cz",
    "letnanskaterasa.cz", "letnikurzyjihlava.cz", "letnikurzypelhrimov.cz", "liboli.cz", "lightz.one", "loprais.cz",
    "luvoxyhair.cz", "luxurybaby.cz", "malejfesak.cz", "malir-naterac.info", "malirhindra.cz", "mamsportovnipovrchy.cz",
    "markpjetri.cz", "martinalisova.cz", "mastratuning.cz", "maturaknaklic.cz", "maturitni-ples.eu", "mcctraining.eu",
    "medisol-as.cz", "meetedison.cz", "metal-produkt.com", "michaelatulachova.cz", "milivanili.cz", "minaru-pelhrimov.cz",
    "mindflow.cz", "minipivovarpalicak.cz", "msbusiness.cz", "msdubovice.cz", "mttech.cz", "musil.cz",
    "muzeumrekorduakuriozit.cz", "na-bytecek.cz", "nafukovadla.cz", "najem-pelhrimov.cz", "naradi-dolezal.cz", "nasmaturak.com",
    "nasmaturak.cz", "navnada.cz", "nejlevnejsiponozky.cz", "nejzabavnejsitesla.cz", "nerezblucina.cz", "neza.cz",
    "novague.com", "novazelena-usporam.cz", "nowaplus.cz", "nunynek.cz", "nutricount.cz", "nutriservis.cz",
    "nutriservisprofi.cz", "nutslife.cz", "obchod-bezlepku.cz", "objednejkino.cz", "ochrance-udaju.cz", "oculista.cz",
    "odpadykj.cz", "okna-v-praze.cz", "oknavpraze.cz", "olomouc.city.cz", "one-core.com", "online.ergo.cz",
    "optika-maderova.cz", "orthojh.cz", "osma-cz.cz", "palandy.cz", "palirnaboskovice.cz", "parkctvrtstoleti.cz",
    "parkdesetileti.cz", "parkroku.cz", "partes.cz", "paus-ordinace.cz", "pavelvanek.cz", "pdca.cz",
    "pear-control.com", "pelhrimov.milost.cz", "pevak.info", "pevex.cz", "pharma-alpha.cz", "pharma2.cz",
    "phdesign-reklama.cz", "pichlova.cz", "pickmanauto.cz", "pikhartsport.cz", "planning.promotime.net", "playforever.cz",
    "plazma.plus", "pobyty-vysocina.cz", "pohary-trofeje.eu", "pohary.com", "pokorny-podlahy.cz", "polavcefestivaly.cz",
    "potisknitriko.cz", "pracovnitextil.cz", "pradloskiny.cz", "prefarmu.sk", "prestigeselection.cz", "prezenta.cz",
    "primestsky-tabor.net", "profittrans.cz", "protopvysocinu.cz", "provobis.cz", "prsniodsavacky.cz", "psppelhrimov.cz",
    "quanda.cz", "rafaproshop.com", "razitka-colop.eu", "razitka-vysocina.cz", "reklamavysocina.cz", "reklamkape.cz",
    "reklamnipotisk.cz", "rema-pv.cz", "remaxwell.cz", "remaxwell.eu", "rempominaru.cz", "retela.cz",
    "retropelhrimov.cz", "rezidencecarpediem.cz", "roka-servis.cz", "roka-transport.cz", "rotasport.cz", "rotmak.cz",
    "rouskyslogem.cz", "rs-shop.cz", "ruleta-hra.com", "saunabar.cz", "sberne-dvory.cz", "schrnovacky.cz",
    "second-hand-velkoobchod.sk", "secondhandautex.cz", "senior-tablet.cz", "seniormobility.cz", "seniortrek.cz", "severane.cz",
    "shop.cerepa.cz", "shop.sportsscouting.cz", "skochuv-dum.cz", "skodafitness.cz", "skrobarny.com", "smarthel.cz",
    "snadnyprofit.cz", "snemovni7.com", "spacidodavka.cz", "spodne-pradlo-2bpoint.sk", "sport-starter.cz", "sportovni-pohary.com",
    "sportovnipohary.com", "sportservistocik.cz", "sportstarter.cz", "sportstarter.eu", "sportstarter.info", "sportstarter.net",
    "sportstarter.sk", "spravapelhrimov.cz", "squash-vysluni.cz", "squash-vysocina.cz", "srdcemrozumem.cz", "status-holding.cz",
    "status.cz", "stavcom.cz", "stavebni-systemy.cz", "steodesign.cz", "stepandavid.com", "strechypelhrimov.cz",
    "stredniskola.com", "strikt.cz", "stroller.cz", "subterplus.cz", "suma.cz", "svycarskapujcka.cz",
    "swoboda.de", "synpro.cz", "szuz.cz", "talpa-orlova.cz", "technocon.cz", "technoworld.cz",
    "telc-ubytovani.cz", "tenisinet.cz", "tenisovyobchod.cz", "tesladave.cz", "tesloukolemsveta.cz", "tiketman.cz",
    "tisknatrika.cz", "tlc.cz", "tmccr.cz", "tocik-klice.cz", "tocikpelhrimov.cz", "tokra-okna.cz",
    "tokra.cz", "tomaskarel.cz", "top-az.eu", "topenidvorak.cz", "topicrem.cz", "toppotisk.cz",
    "topteam.cz", "toptenis.cz", "tradicni-ceske.cz", "tradition-gifts.com", "traficon-re.eu", "traficon.eu",
    "traficonadvisors.eu", "trnavka.net", "truck-pe.cz", "truhlarstvislza.cz", "trymall.cz", "tt.ensis.cz",
    "turbo-opravna.cz", "turbocar.cz", "tvrzmladejovice.cz", "ubytovani-pelhrimov.eu", "ubytovani-vysocina.info", "uctodane.cz",
    "uludanka.cz", "umenitridit.cz", "upvest.cz", "valasskyfrgal.cz", "vamafil.com", "vandastore.cz",
    "vandastore.sk", "vanprodukt.cz", "velkychovatel.cz", "velo-team.com", "veloservis-team.com", "veloservisteam.com",
    "velosport.cz", "venujmobil.cz", "vhst.cz", "vibrator4you.cz", "vicevina.cz", "victoryauto.cz",
    "virivky-penziony.cz", "vitalpet.cz", "vlajkylevne.cz", "vlnika.cz", "vnd.cz", "vostry.biz",
    "vysocina40hub.cz", "vystroj-vyzbroj.cz", "vytvarnyobchod.cz", "weeerec.org", "wellgroup.cz", "wellprague.cz",
    "whsys.cz", "wixx.cz", "woodiy.cz", "wos.hradeckapekarna.cz", "wos.karlova-pekarna.cz", "wu-shu.ensis.cz",
    "yetty.eu", "zahradadesetileti.cz", "zahradni-sekacky.eu", "zahradnickesluzby.cz", "zahradnictvi-simkova.cz", "zahrady-kapral.com",
    "zamek-cervenarecice.cz", "zatrnkovymkerem.cz", "zdravizvysociny.cz", "zdravotni-pojisteni-cizincu.cz", "zelandia.cz", "zelenastrechaprahy.cz",
    "zelenastrecharoku.cz", "zelenestrechy.info", "zelezarstvi-jaros.cz", "zeliv.cz", "zijemejihlavou.cz", "zijemevysocinou.cz",
    "zitrabudepozde.cz", "zkontrolujvodu.cz", "zlatnictvifelix.cz", "zlatnik-hodinar.cz", "zpetnyodber.cz",
}


# ============================================================================
# RENTAL — nájemníci, správa nemovitostí, energie kolem pronajímaných bytů (v4)
# Pořadí v classify(): kontrola PO INVOICE a PŘED CLIENT.
# Důvod: pokud má bytservis-ji.cz fakturu, jde do Účetní. Ostatní mail od nájemníků
# a správce nemovitosti → _mail.Najmy.
# ============================================================================

RENTAL_FROM_EXACT = {
    "peta9870@email.cz",           # Petra Starová — nájemnice
    "vcelarova@bytservis-ji.cz",   # Bytservis — vyúčtování služeb SVJ (i invoice path)
}

RENTAL_DOMAINS = {
    "bytservis-ji.cz",   # správce SVJ Hruškové Dvory 104, Jihlava
}


# ============================================================================
# FIRMA_BUDOVA — cenové nabídky pro firmu/budovu (v4)
# Pořadí v classify(): po RENTAL, před CLIENT.
# Pozn.: kategorie pro maily typu "cenová nabídka X" od dodavatelů zařízení/úprav.
# ============================================================================

FIRMA_BUDOVA_FROM_EXACT = {
    "info@fabrego.cz",
    "jan.steiden@flexibox.cz",
    "info@zasklej.to",
}

FIRMA_BUDOVA_DOMAINS = {
    "fabrego.cz",
    "flexibox.cz",
    "zasklej.to",
}


# ============================================================================
# IMPORTANT — interní, govt, zákazníci, dodavatelé, osobní
# ============================================================================

IMPORTANT_DOMAINS = {
    # Interní
    "prvni-pozice.com", "ensis.cz",
    # Govt
    "mssf.cz", "mojedatovaschranka.cz", "mv.gov.cz",
    "czechinvest.gov.cz", "czechinvest.org", "mpo.gov.cz",
    "khkvysocina.cz", "kr-vysocina.cz",
    "nic.cz",  # doménové notifikace
    # Zákazníci / dodavatelé
    # Pozn.: vnd.cz odstraněno (je v CLIENT_DOMAINS), hkpe.cz/hkjihlava.cz přesunuto do KOMORA_DOMAINS (v4)
    "neza.cz",  # v6: ortex.cz přesunuto do CLIENT_DOMAINS
    # v5: edc-cr.cz odstraněno (jeho subdoména dis.edc-cr.cz je v ENERGIE_DOMAINS)
    "flowtech.cz", "duotrans.cz", "czechmclub.cz", "fundex.cz",
    "asekol.cz", "enviropol.sk", "szuz.cz", "mupe.cz", "tspe.cz",
    "polygonhradec.cz", "lukashron.cz",
    "michalmartinek.cz", "portasky.cz",
    "ji-hlava.cz",  # MFDF
    "collabim.cz",  # SEO trénink — szpuk
    "compic.cz",    # AI Monday — kvido
}

IMPORTANT_FROM_EXACT = {
    # Osobní seznam.cz / gmail / atlas.cz / centrum
    "otto.salbaba@seznam.cz", "wushu-pe@seznam.cz", "iva.hrabetova@seznam.cz",
    "dvorak.med@seznam.cz", "pescitm@seznam.cz",
    "tomulinek@gmail.com", "lukas@krajicek.net",
    "astoichkov@enhauto.com",
    "zdenek@chapani.cz", "zuzana.tomesova@globusada.eu",
    # Doprava / pošta — terminy-carsjet ponecháno (různě formátované upomínky)
    "terminy-carsjet@post.cz",
    "ceskaposta@cpost.cz",
    # Banka — osobní bankéř / fondy
    "jan_pecha@kb.cz", "fondy@conseq.cz",
    # Govt notifiers
    "noreply@ms21.mssf.cz",
    "aneta.pechova@apiagentura.gov.cz",
    # Google services that need attention
    "no-reply@accounts.google.com",
    "google-noreply@google.com",   # v4: bezp. notifikace
    "comments-noreply@docs.google.com",
    "drive-shares-dm-noreply@google.com",
    # v5: support@ppl.cz vráceno do DOPRAVA (uživatel přesune sem doručenky)
    # v5: discover@airbnb.com vráceno do ESHOPS (marketing, ne notifikace)
    # v5: balikovna@balikovna.cz přesunuto do DOPRAVA
    # v5: no-reply@egd.cz / info@egd.cz přesunuto do ENERGIE (jen oznámení o přerušení)
    # v5: dan@danielnytra.cz přesunuto do SW (marketing newsletter)
    # v5: mailer-daemon@mail.prvni-pozice.com přesunuto do SW (bounce notifikace)
    "futurego@cez.cz",
    # Crypto verification
    "no_reply@coinmate.io",
    # Zákazníci — konkrétní lidi
    "robot@edookit.com", "robot@edookit.net",
    "rezervace@spacetown.cz", "rezervace@motokarymodrice.cz",
    "info@jumparenatabor.cz",
    "jiri.vesely@nastejnelodi.cz",  # meeting invites
    "kvido@compic.cz",
    "szpuk@collabim.cz",
    "sarka.vankova@ortex.cz", "jan.klimes@ortex.cz", "jan.cernohorsky@ortex.cz",
    "hrdlickova.martina@karlova-pekarna.cz",
    # Energetická data — automated státní notifikace, ale ponecháno v Review:
    # ms21.mssf.cz (depeše MS2021+)
}


# ============================================================================
# UNIMPORTANT — newsletter / marketing / notifikace
# Sub-kategorie: banks | energie | eshops | develop | sw | doprava | komora
# Pořadí kontroly v rules: speciální sub-kategorie → generický UNIMPORTANT
# ============================================================================

# --- 1) BANKS ---
BANKS_FROM_EXACT = {
    "ceskasporitelna@info.csas.cz", "erstepremier@info.csas.cz",
    "noreply@info.csas.cz", "feedback@info.csas.cz",
    "kontakt@info.csas.cz", "csasezpro@info.csas.cz", "no_reply@info.csas.cz",
    "kontakt@mbank.cz",
    "erstepremier@erstepremier.cz",
    "no-reply@revolut.com",
    "novinky@info.csob.cz", "no-reply@info.csob.cz", "kontakt@info.csob.cz",
    "info@georgesso.cz",
    # Investiční
    "sales@xtb.cz", "crm-no-reply@mail.xtb.com", "david.penaz@xtb.cz",
    # v5
    "noreply@fio.cz",
}
BANKS_DOMAINS = {
    "info.csas.cz", "csas.cz", "info.csob.cz", "csob.cz",
    "mbank.cz", "revolut.com", "mail.revolut.com",
    "xtb.cz", "mail.xtb.com", "xtb.com",
    "kb.cz",  # mailingy (osobní bankéř už v IMPORTANT_FROM_EXACT)
    "georgesso.cz",
    "fio.cz",  # v5
}


# --- 2) ENERGIE ---
ENERGIE_FROM_EXACT = {
    "b2bsluzby@novinky.eon.cz",
    "noreply@customer.enelx.com",
    "noreply_at_customer_enelx_com_b2nr7jhnxb_26041088@privaterelay.appleid.com",
    "premobilita@pre.cz",
    "office@eldrive.eu",
    "reply@chargepoint.com", "drivers@reply.chargepoint.com",
    "GP@beelectric.cz", "gp@beelectric.cz",
    "no-reply@plugsurfing.com",
    "noreply@service.emob.eniplenitude.com",
    "emobilita@charge.innogy.cz",
    "info@mail.cez.cz",
    "info@notifications.ionity.eu",
    "info@lecircuitelectrique.com",
    "noreply-nepasrepondre@communication.nbpower.com",
    # v5
    "info@egd.cz", "no-reply@egd.cz",
    "noreply@dis.edc-cr.cz",
}
ENERGIE_DOMAINS = {
    "mail.cez.cz", "novinky.eon.cz", "info.eg-d.cz",
    "customer.enelx.com", "enelx.com",
    "eldrive.eu", "reply.chargepoint.com", "chargepoint.com",
    "beelectric.cz", "plugsurfing.com",
    "service.emob.eniplenitude.com", "eniplenitude.com",
    "charge.innogy.cz", "innogy.cz",
    "notifications.ionity.eu",
    "lecircuitelectrique.com",
    "communication.nbpower.com",
    "mail.zes.net", "info.email.zes.net", "b2.email.zes.net", "zes.net",
    # v5
    "egd.cz", "eg-d.cz",
    "dis.edc-cr.cz",
}


# --- 3) ESHOPS ---
ESHOPS_FROM_EXACT = {
    "info@members.netflix.com", "info@account.netflix.com",
    "newsletter@info.mountfield.cz",
    "ikea@news.email.ikea.cz", "information@loyalty.email.ikea.com",
    "news@letter.alza.cz", "sluzebnicek@alza.cz", "naminutku@alza.cz",
    "alza@recenze-zbozi.cz", "pneuprodejna@recenze-zbozi.cz",
    "decathlon.cz@email.decathlon.com", "noreply-cz@email.decathlon.com",
    "noreply-cz@service.decathlon.com", "decathlon@overenozakazniky.cz",
    "lidl@overenozakazniky.cz", "no_reply@lidl-shop.cz",
    "noreply@tracking.lidl-shop.cz", "no-reply@e.lidl.cz", "news@e.lidl.cz",
    "newsletter@info.albert.cz", "newsletter@info.kaufland.cz",
    "newsletter@datart.cz", "newsletter@info.datart.cz",
    "newsletter@notino.cz",
    "novinky@info.iwant.cz", "info@novinky.argos.cz", "newsletter@info.argos.cz",
    # v5: discover@airbnb.com vráceno do ESHOPS (marketing newsletter)
    "discover@airbnb.com",
    "noreply@booking.com", "noreply-payments@booking.com",
    # v5: nové eshop newslettery
    "info@mailing.rcobchod.cz", "info@rcobchod.cz",
    "pegasus@e-flypgs.com",
    "noreplyrewards@dennys.com",
    "postmaster@edm.cdkeysales.com",
    "novinky@amoreloviny.cz",
    "noreply@email.volvocars.ch",
    "info@megaubytko.cz",
    "promotion@aliexpress.com", "no-reply@aliexpress.com",
    "transaction@notice.aliexpress.com",
    "trip.com@newsletter.trip.com",
    "gb@e.opodo.com", "travel@info.mytrip.com", "travel@kiwi.com",
    "no-reply@sender.skyscanner.com",
    "noreply@ozon.cz", "noreply@notify.ozon.cz",
    "info@notifikace.allegro.cz",
    "novinky@shop.hecht.cz",
    "eshop.cz@robel.shoes",
    "no-reply@e.amazon.com", "amazon-marketplace@amazon.com",
    "noreply@e.crm.lego.com", "noreply@lego.com",
    "noreply@bambulab.com",  # marketing variant — invoice už řeší INVOICE_FROM_EXACT pod podmínkou subject
    "hello@news.bambulab.com", "no-reply@news.bambulab.com",
    "info@mobileon.cz",
    "ceska.posta@centrum-zakaznickych-karet.cz",
    "msv@news.bvv.cz", "mfo@news.bvv.cz", "mfostk@news.bvv.cz", "mfodc@news.bvv.cz",
    "info@firmy.cz", "info@tipy.firmy.cz", "seznam.partner@firma.seznam.cz",
    "partneri@slevomat.cz", "diskuse@slevomat.cz", "radost@slevomat.cz",
    "dovolena@ck.nev-dama.cz", "odbaveni@nev-dama.cz",
    "info@aodaci.com", "info@viansa.com", "wineclub@viansa.com",
    "info@nev-dama.cz",  # marketing variant (invoice už řeší rules)
    "kontakt@mbank.cz",  # marketing variant
}
ESHOPS_DOMAINS = {
    # v5 přírůstky
    "mailing.rcobchod.cz", "rcobchod.cz",
    "e-flypgs.com",
    "dennys.com",
    "edm.cdkeysales.com", "cdkeysales.com",
    "amoreloviny.cz",
    "email.volvocars.ch", "volvocars.ch",
    # v4 přírůstky
    "e.dji.com", "dji.com",
    "newsletter.olaola.cz", "olaola.cz",
    "news-bambusovky.cz",
    "news.elpe.cz",   # ELPE Pelhřimov (eshop.elpe.cz je klient — ne kolize, suffix-match)
    "vyrobkydilna.cz",
    "nextbase.eu",
    "klubpevnehozdravi.cz",
    "mail.ceskyhokej.cz", "ceskyhokej.cz",
    # původní
    "members.netflix.com", "account.netflix.com",
    "info.mountfield.cz", "newsletter.info.mountfield.cz", "mountfield.cz",
    "news.email.ikea.cz", "mail.ikea.cz", "loyalty.email.ikea.com",
    "letter.alza.cz", "newsletter.alza.cz", "alza.cz",
    "email.decathlon.com", "service.decathlon.com", "decathlon.com",
    "overenozakazniky.cz",
    "lidl-shop.cz", "e.lidl.cz", "lidl.cz",
    "info.albert.cz", "albert.cz",
    "info.kaufland.cz", "kaufland.cz",
    "datart.cz", "info.datart.cz",
    "notino.cz",
    "info.iwant.cz", "iwant.cz", "novinky.iwant.cz",
    "info.argos.cz", "novinky.argos.cz", "argos.cz", "mail.argos.cz",
    "airbnb.com",
    "booking.com", "property.booking.com",
    "megaubytko.cz",
    "aliexpress.com", "notice.aliexpress.com",
    "newsletter.trip.com", "trip.com",
    "e.opodo.com", "opodo.com",
    "info.mytrip.com", "mytrip.com",
    "kiwi.com",
    "sender.skyscanner.com", "skyscanner.com",
    "ozon.cz", "notify.ozon.cz",
    "notifikace.allegro.cz", "allegro.cz",
    "shop.hecht.cz", "hecht.cz",
    "robel.shoes",
    "e.amazon.com", "amazon.com",
    "e.crm.lego.com", "crm.lego.com", "lego.com",
    "news.bambulab.com", "bambulab.com",
    "centrum-zakaznickych-karet.cz",
    "news.bvv.cz",
    "firmy.cz", "tipy.firmy.cz", "firma.seznam.cz",
    "slevomat.cz",
    "ck.nev-dama.cz", "nev-dama.cz",
    "aodaci.com", "viansa.com",
    "mailing.alenoroleobchody.cz",
    "shoptet.eu",  # survey
    "recenze-zbozi.cz",
    "asfinag.at",  # Rakousko dálnice
    "autovignet.eu",
    "mobileon.cz",
}


# --- 4) DEVELOP ---
DEVELOP_FROM_EXACT = {
    "no-reply@docker.com",
    "noreply@discord.com",
    "no-reply@github.com",
    "gitlab@mg.gitlab.com",
    "security@vercel.com", "noreply@vercel.com",
    "do-not-reply@trello.com",
    "do-not-reply@uipath.com",
    "noreply@getpostman.com",
    "noreply@docker.com",
    "noreply@asus.com",
    "noreply@info.csas.cz",  # ne — banka, ale ponecháno pro fallback
    "team@info.digitalocean.com",
    "no-reply@contabo.com", "support@contabo.com",
    "noreply@email.openai.com",
    "noreply@lovable.dev",
    "contact@mail.replit.com", "matt@mail.replit.com",
    "no-reply@autodesk.com",
    "noreply@news.synology.com",
    "noreply@makerworld.com",
    "no-reply@docker.com",
    "hello@cal.com",
    "no-reply@publer.com", "tea@support.publer.com",
    "newsletter@chargepoint.com",
}
DEVELOP_DOMAINS = {
    "docker.com",
    "discord.com",
    "github.com",
    "gitlab.com", "mg.gitlab.com",
    "vercel.com",
    "trello.com",
    "uipath.com",
    "getpostman.com", "postman.com",
    "asus.com",
    "info.digitalocean.com", "digitalocean.com",
    "contabo.com",
    "email.openai.com", "openai.com",
    "lovable.dev",
    "mail.replit.com", "replit.com",
    "autodesk.com",
    "news.synology.com", "synology.com",
    "makerworld.com",
    "cal.com",
    "publer.com", "support.publer.com",
    "transkriptor.com",
    "cloudflare.com",
    "aws.amazon.com",  # ne pure marketing
    "googlecloud.com",
}


# --- 5) SW (ostatní SaaS / sw) ---
SW_FROM_EXACT = {
    # v4: přesunuto z IMPORTANT_FROM_EXACT — Google business profile / families
    "businessprofile-noreply@google.com",
    "families-noreply@google.com",
    # v4: nové
    "noreply@loxone.com",
    "hello@qr-code-generator.com",
    "majestic@majestic.com",
    # v5: marketingové newslettery a notifikační maily
    "hello@8020ai.co",
    "info@mailer.mindvalley.com",
    "hello@deepstash.com",
    "photos@onedrive.com",
    "hello@review.capterra.com",
    "support@complianz.io",
    "no-reply@email.claude.com",
    "analytics-noreply@google.com",
    "newsletter@trademedia.cz",
    "dalibor@collabim.cz",
    "dan@danielnytra.cz",          # v5: přesunuto z IMPORTANT (marketing newsletter)
    "emails@marketing.nascar.com",
    "admin@promotime.net",
    "mailer-daemon@mail.prvni-pozice.com",   # v5: bounce notifikace (přesunuto z IMPORTANT)
    # původní
    "autopost@app.opus.pro", "result@app.opus.pro", "storage@app.opus.pro",
    "refund@app.opus.pro", "service@app.opus.pro", "support@app.opus.pro",
    "opus.pro@app.opus.pro", "no-reply@opus.pro",
    "admin@mail.capcut.com",
    "support@aioseo.com",
    "reply@acdsystems.net",
    "no_reply@email.heygen.com", "no_reply@learn.heygen.com",
    "noreply@clickup.com",
    "moritz-and-team@mail.sliderrevolution.com",
    "team@newsletter.artlist.io",
    "noreply@tesla.com", "mail_teslaownersclubcz@mg.eosclub.zone",
    "email@create.prezi.com", "email@content.prezi.com",
    "hello@hootsuite.com", "hello@balanceapp.com", "hello@duolingo.com",
    "no-reply@duolingo.com",
    "recommends@ted.com", "email@email.variety.com",
    "no_reply@email.heygen.com",
    "no-reply@transkriptor.com",
    "info@smartymeapp.com",
    "support@pdfhouse.com",
    "info@notifications.ionity.eu",
    "feedback@info.csas.cz",  # redundant pre-banks
    "info@engage.istockphoto.com",
    "contact@chargify.gravityforms.com",
    "noreply@hg.qualitando.email",
    "your@insights.veed.io",
    "learning@marketing.descript.com", "events@marketing.descript.com",
    "hi@marketing.xmind.ai",
    "noreply.invitations@trustpilotmail.com",
    "info@reshoper.cz",
    "info@aimonday.cz",
    "noreply@spotify.com",
    "no-reply@adobe.com", "message@adobe.com",
    "paypal-mailservice@paypal.com",
    "orders@email.linkedin.com", "jobs-listings@linkedin.com",
    "invitations@linkedin.com", "messages-noreply@linkedin.com",
    "jobalerts-noreply@linkedin.com", "jobsearch@linkedin.com",
    "noreply@parcellab.com",
    "no-reply@easypark.net",
    "newsletter@daktela.com", "daktela@daktela.com",
    "member@surveymonkeyuser.com",
    "notification@findpenguins.com",
    "do-not-reply@trello.com",
    "no-reply@youtube.com", "noreply-local-guides@google.com",
    "sc-noreply@google.com",  # Search Console
    "sklik@sklik.cz",
    "vyzkum@pokusnikralici.cz", "pruzkum25@flexmedia.cz",
    "system@bforb.cz",
    "uber@uber.com",
    "nicholas.gregory@calendly.com",
    "comms@comms.vueling.com",
    "no-reply@plugsurfing.com",  # redundant pre-energie
    "info@firmy.cz",  # already eshops
}
SW_DOMAINS = {
    # v5 přírůstky
    "8020ai.co",
    "mailer.mindvalley.com", "mindvalley.com",
    "deepstash.com",
    "onedrive.com",
    "review.capterra.com", "capterra.com",
    "complianz.io",
    "email.claude.com", "claude.com",
    "marketing.nascar.com", "nascar.com",
    "promotime.net",
    "trademedia.cz",
    "danielnytra.cz",
    "mail.prvni-pozice.com",
    # v4 přírůstky
    "loxone.com",
    "qr-code-generator.com",
    "majestic.com",
    # původní
    "app.opus.pro", "opus.pro",
    "mail.capcut.com", "capcut.com",
    "aioseo.com",
    "acdsystems.net", "reply.acdsystems.net",
    "email.heygen.com", "learn.heygen.com", "app.heygen.com",
    "clickup.com",
    "mail.sliderrevolution.com", "sliderrevolution.com",
    "newsletter.artlist.io", "artlist.io",
    "tesla.com", "mg.eosclub.zone",
    "create.prezi.com", "content.prezi.com", "prezi.com",
    "hootsuite.com", "balanceapp.com", "duolingo.com",
    "ted.com", "email.variety.com", "variety.com",
    "transkriptor.com",
    "smartymeapp.com",
    "pdfhouse.com",
    "engage.istockphoto.com", "istockphoto.com",
    "chargify.gravityforms.com", "gravityforms.com",
    "hg.qualitando.email", "qualitando.email",
    "insights.veed.io", "veed.io",
    "marketing.descript.com", "descript.com",
    "marketing.xmind.ai", "xmind.ai",
    "trustpilotmail.com",
    "reshoper.cz",
    "aimonday.cz",
    "spotify.com",
    "adobe.com",
    "paypal.com",
    "email.linkedin.com", "linkedin.com",
    "parcellab.com",
    "easypark.net",
    "daktela.com", "newsletter.daktela.com",
    "surveymonkeyuser.com", "surveymonkey.com",
    "findpenguins.com",
    "youtube.com",
    "sklik.cz",
    "pokusnikralici.cz", "flexmedia.cz",
    "bforb.cz",
    "uber.com",
    "calendly.com",
    "comms.vueling.com", "vueling.com",
    "mailerlite.com", "mlsend.com", "mailerlite-mail.com",
    "mailgun.org", "sendgrid.net", "mailchimp.com", "mailchi.mp",
    "hubspotemail.net", "hubspot.com",
    "sendinblue.com", "brevo.com",
    "klaviyomail.com", "klaviyo.com",
    "mailjet.com",
    "omnisendapp.com", "omnisend.com",
    "campaign-archive.com",
    "novinky.czechonlineexpo.cz", "czechonlineexpo.cz",
    "newsletter.czechonlineexpo.cz",
    "automationanywhere.com",
    "jaromirotava.cz", "novinky.jaromirotava.cz", "newsletter.jaromirotava.cz",
    "ivotoman.cz", "novinky.ivotoman.cz",
    "inizio.cz",
    "make.com",
    "info@ejaj.cz", "ejaj.cz",
    "iwant.cz", "info.iwant.cz",
    "donio.cz", "novinky.donio.cz", "aktuality.donio.cz",
    "novinky.cis.cz",
    "newsletter.briefcastnews.com", "briefcastnews.com",
    "snitchertracker.com",
    "edumame.cz",
    "shoptetpay.com",
    "vhstdevelopment.cz",
    "runsportnews.cz",
    "news.bmracing.cz", "bmracing.cz",
    "alpinecentrum.cz", "news.alpinecentrum.cz",
    "ultimatedakar.com",
    "ranketta.com", "mail.ranketta.com",
    "lopraisnews.socialnetworks4you.net", "socialnetworks4you.net",
    "zlatebehy.cz",
    "mailzz.cz",
    "transcri.io",
    "moonshotplatform.org",
}


# --- 6) DOPRAVA ---
DOPRAVA_FROM_EXACT = {
    # v5: support@ppl.cz a balikovna@balikovna.cz vráceno do DOPRAVA z IMPORTANT
    "support@ppl.cz",
    "balikovna@balikovna.cz",
    "cz-no-reply@dpd.com", "noreply@dpd.cz",
    "fedex@message.fedex.com",
    "noreply@dhl.de",
}
DOPRAVA_DOMAINS = {
    "ppl.cz",
    "dpd.com", "dpd.cz",
    "message.fedex.com", "fedex.com",
    "dhl.de", "dhl.com",
    # v5
    "balikovna.cz",
}


# --- 7) HOSP. KOMORA ---
KOMORA_FROM_EXACT = {
    "newsletter@hkcr.komora.cz", "kurzy@hkcr.komora.cz", "info@hkcr.komora.cz",
}
KOMORA_DOMAINS = {
    "hkcr.komora.cz", "komora.cz",
    "newsletter.hkcr.komora.cz",
    # v4: přesunuto z IMPORTANT_DOMAINS / CLIENT_DOMAINS — uživatel chce do unimportant.komora
    "hkpe.cz", "hkjihlava.cz",
}


# --- Generic UNIMPORTANT (zbytek — neidentifikovaná podsložka) ---
UNIMPORTANT_FROM_EXACT = {
    # v4: false-positive ze COLD_SPAM (behaviolabs.com je obecně cold, ale tento sender je OK)
    "hynek.spinar@behaviolabs.com",
    # původní
    "jan.nedved@inizio.cz", "standa@inizio.cz", "meet@jarekmikes.com",
    "mistrprodeje@ivotoman.cz", "zpravodaj@ivotoman.cz",
    "profesionalnikouc@jaromirotava.cz",
    "profesionalnikouc@novinky.jaromirotava.cz",
    "mezerova@jihlavske-listy.cz",
    "pr@novinky.cis.cz",
    "newsletter@info.eg-d.cz",
    "info@emails.metrobus.co.uk",
    "shop@asfinag.at", "nashop@asfinag.at",
    "noreply@dein.mobile.de",
    "decathlontabor@eu.erply.io",
    "fahrtechnik.wachauring@oeamtc.at",
    "newsletter@info.albert.cz",
    "info@enermia.org",
    "info@nextbikeczech.com",
    "info@taiwanchamber.cz",
    "ncp40@ciirc.cvut.cz",
    "info@dodavkymorava.cz",
    "info@dshopboard.pl",  # marketing, ne cold
    "info@tgwowdigital.com",
    "zeleni@zeleni.cz",
    "mfo@news.bvv.cz",
    "gdgjihlava@gug.cz",
    "monika.hamalcikova@nfpelhrimovsko.cz",
    "info@mailzz.cz",
    "info@viansa.com",
    "info@aodaci.com",
    "info@aliteo.com",
    "raf@dobryden.cz",
    "moje@rondo.cz",
    "ekofarmy@email.cz",
    "info@recordedfuture.com",
    "info@mobilux.cz",
    "info@vhstdevelopment.cz",
    "peta9870@email.cz",
    "info@runsportnews.cz",
    "neodpovidat@sbazar.cz",
    "notifications@mail.ranketta.com",
    "zprava@info.seznam.cz",
    "noreply@info.csas.cz",  # marketing variant
    "ptejtese@vlesku.cz",
    "info@ic40.cz",
    "lukas.hron@prvni-pozice.com",  # interní, ale automaty — TODO: case-by-case
    "hello@enhauto.com",
    "cesko@student-eshop.cz",
    "newsletter@info.csas.cz",
    "survey@shoptet.eu",
    "no-reply@autodesk.com",
}
UNIMPORTANT_DOMAINS = {
    "inizio.cz", "jarekmikes.com", "novinky.jarekmikes.com",
    "novinky.jaromirotava.cz", "jaromirotava.cz",
    "ivotoman.cz", "novinky.ivotoman.cz",
    "jihlavske-listy.cz",
    "info.eg-d.cz", "eg-d.cz",
    "emails.metrobus.co.uk", "metrobus.co.uk",
    "asfinag.at",
    "dein.mobile.de", "mobile.de",
    "eu.erply.io", "erply.io",
    "oeamtc.at",
    "info.albert.cz",
    "enermia.org",
    "nextbikeczech.com",
    "taiwanchamber.cz",
    "ciirc.cvut.cz",
    "dodavkymorava.cz",
    "dshopboard.pl", "tgwowdigital.com",
    "zeleni.cz",
    "gug.cz",
    "nfpelhrimovsko.cz", "pelhrimovsko.cz",
    "mailzz.cz",
    "aliteo.com",
    "dobryden.cz", "raksclub.cz",
    "rondo.cz",
    "recordedfuture.com", "spravce.recordedfuture.com",
    "mobilux.cz",
    "vhstdevelopment.cz",
    "runsportnews.cz",
    "sbazar.cz",
    "info.seznam.cz",
    "vlesku.cz",
    "ic40.cz",
    "enhauto.com",
    "student-eshop.cz",
    "info.csas.cz",  # marketing — banks už řeší
    "survey.shoptet.eu",
    "novinky.cis.cz",
    "wzv.cz",
    "kraj-vysocina.cz", "info.kraj-vysocina.cz",
    "edumame.cz",
    "ji-veda.eu",
    "snny.net",
    "sparkpostmail2.com", "sparkpostmail.com",
}


# ============================================================================
# SPAM — agresivní routing do /Junk
# ============================================================================

# Konkrétní doménové fragmenty / vzory pro cold B2B a scam
COLD_SPAM_DOMAINS_RE = re.compile(
    r"@(?:"
    r"reply\.chargepoint\.com|techflod\.com|qoreto\.pl|zentryx\.pl|commenvo\.com|"
    r"gestores\.net|stafflinkglobal\.info|stafflnk\.com|behaviolabs\.com|"
    r"bizemailsource\.com|focuspodcastfirestorm\.co|"
    r"launchpodcastguests\.co|meetupexpos\.com|createleads\.info|"
    r"accessingclickuptasklist\.co|clickupresultstoday\.co|enrichleyai\.com|"
    r"myenrichleyai\.com|thesoftaims\.website|b2bsalesroadmap\.website|"
    r"scalewithaiandbillionaire\.com|tirol\.com\.br|augenklinik-regensburg\.de|"
    r"roxheimer-hundesalon\.de|kemendagri\.go\.id|kailyshoes\.com|fangshuimifeng\.com|"
    r"phrostmade\.com|gradina\.ftn\.kg\.ac\.rs|ea4711\.de|vitra-service\.de|"
    r"dubaiprimesk\.com|111309\.com|englobalcommercialllc\.com|sba010\.com|"
    r"wngmc\.cn|cofane\.com\.tr|codaga\.com\.tr|comila\.com\.tr|conuk\.in\.rs|"
    r"inkirsones\.gb\.net|seowork\.ru|botetextile\.com|"
    r"wisdom-mold\.me|hwm01\.com|"
    r"apzt-b2dfd\.firebaseapp\.com|"
    r"theinsurancequoter\.com|nzizaviewhotel\.com|dteam\.solutions|"
    r"xmbagsss\.com|uzdubu\.cz|forgeup\.cz|"
    r"kineticworkforceadvisors\.com|smartdatalists\.com|sohamcapital\.in|"
    r"guestinfoform\.co|pear-control\.com|sdg\.lt|voitech\.academy|"
    r"belisario\.com\.co|pdvprom\.com|goldengate\.cz|mathesio\.cz|"
    r"marky-sport\.cz|artediabitare\.it|banoabdi\.de|mshopboard\.pl|"
    r"property\.booking\.com|"
    r"snapblueinkapro\.com|noborup\.tv|"
    r"aquamarinarealestate\.it|ekkate\.com|gsd\.com|makatrade\.store|"
    r"talkneedaguest\.co|"
    r"guestcallsneedaguest\.co|leads-picker-for-agencies\.com|"
    r"momentstudio\.ca|epmca\.co|trylinkrocket\.com|"
    r"theml-creative\.com|youtubetotext\.ai|s1\.inteli\.email|inteli\.email|"
    # v5: cold B2B / průmyslový spam
    r"firezink\.de|hymedimachinery\.com|nextgroup\.ge"
    r")",
    re.IGNORECASE,
)

# Czech-fake produktové domény (.eu)
CZECH_FAKE_EU_DOMAINS = {
    "winstells.eu", "polanserd.buzz", "topola.in.rs", "karellis.beauty",
    "kresloprodeti.eu", "damepizzu.eu", "cernypatek.eu", "naparu.eu",
    "puzzlepodloz.eu", "10zatez.eu", "pbohrev.eu", "skladujem.eu",
    "navakuovat.eu", "ionigsuj.eu", "momentak.eu", "susickyhub.eu",
    "karafaglobus.eu", "vrchnivyhrevna.eu", "tepleponozky.eu",
    "vrtakem.eu", "mb-koupelnyji.eu", "prkenkobamb.eu", "prezijem.eu",
    "arbentio.buzz", "collo.in.rs", "comaxe.shop",
    "cofane.com.tr", "codaga.com.tr", "comila.com.tr", "conuk.in.rs",
    "inkirsones.gb.net",
    # Doplněno z batch 2
    "nakrajeni.eu", "radiatorohen.eu", "robustniprkenko.eu", "vysavacpraci.eu",
    "autovysaju.eu", "retezbruska.eu", "ozonemsanituj.eu", "profibrouseni.eu",
    "srozprasovacem.eu", "hlidejavaz.eu", "prozahradniky.eu", "provacauto.eu",
    "budikhodiny.eu",
}

# TLDs pro mass spam — agresivní (žádný legit user-base zde)
SPAM_TLDS_HARD = (
    ".za.com", ".sa.com", ".in.rs", ".com.tr", ".com.ni", ".com.uy", ".com.do",
    ".shop", ".buzz", ".top", ".autos", ".mom", ".makeup", ".motorcycles",
    ".sbs", ".help", ".website", ".quest", ".click", ".online", ".life",
    ".beauty", ".style", ".uno", ".review", ".bond", ".cyou",
    ".yachts", ".onl", ".icu", ".world", ".pics", ".casa", ".qpon",
)
# Whitelist legit .shop / .top apod.
SHOP_TLD_WHITELIST = {"shoptet.cz", "shoptet.eu", "shop.opus.pro", "shop.hecht.cz"}

# Soft-spam TLDs — VŠECHNY MAILY procházejí, ALE platí přísnější kontrola
# Pokud match na konkrétní soft-spam vzor → spam. Jinak může projít jako unsure.
SPAM_TLDS_SOFT = (".pl", ".ru", ".ua", ".cn", ".tw", ".de")
SOFT_SPAM_WHITELIST_DOMAINS = {
    "allegro.pl", "dhl.de",
    "mailing.eu",  # placeholder, doplň podle potřeby
}

# Subject klíčová slova pro scam
SCAM_SUBJECTS = (
    "powerball", "powerbal", "nigerijsk", "jackpot",
    "půjčk", "investiční večeř", "soukromá investiční",
    "pink salt", "fix your teeth", "erection switch",
    "banned:", "bizarre indian", "shrink your prostate",
    "tinnitus is killing", "flushes out fat",
    "mens vitality", "manhood", "septic tank",
    "lose weight", "water dumping", "belly fat",
    "verify your email address", "malware detected",
    "naléhavé upozornění", "unicredit", "uпicredit", "uпicгedit",
    "your refund", "vaše refundace",
    "your document",
    "important emails awaiting delivery",
)


# ============================================================================
# Pomocné funkce
# ============================================================================

GIBBERISH_LOCAL_RE = re.compile(r"^[a-z]{6,9}$")
FIRSTNAME_LASTNAME_RE = re.compile(r"^[a-z]+\.[a-z]+$")

# Mapování (category, subcategory) → klíč v config.toml [targets]
TARGET_KEYS = {
    ("invoice", None): "invoices",
    ("client", None): "clients",
    ("domeny", None): "domeny",
    ("interni", None): "interni",
    ("rental", None): "rentals",
    ("firma_budova", None): "firma_budova",
    ("important", None): "important_review",
    ("unimportant", None): "unimportant",
    ("unimportant", "banks"): "unimportant_banks",
    ("unimportant", "energie"): "unimportant_energie",
    ("unimportant", "eshops"): "unimportant_eshops",
    ("unimportant", "develop"): "unimportant_develop",
    ("unimportant", "sw"): "unimportant_sw",
    ("unimportant", "doprava"): "unimportant_doprava",
    ("unimportant", "komora"): "unimportant_komora",
    ("spam", None): "spam",
    ("unsure", None): "unsure",
}


def _domain_match(domain: str, allowed: set[str]) -> bool:
    """Doména `foo.bar.com` matchne `bar.com` i `foo.bar.com`."""
    if domain in allowed:
        return True
    for d in allowed:
        if domain.endswith("." + d):
            return True
    return False


def classify(item: dict) -> RuleResult:
    """Klasifikuj jeden mail (dict s `folder`, `from_addr`, `subject`, `body_text`/`snippet`).

    Pořadí kontrol:
      1. Junk-source → spam
      2. Hard scam patterns (firebaseapp, czech-fake .eu, .za.com, .shop, .buzz, ...)
      3. Subject scam keywords
      4. INVOICE matchers (exact + conditional)
      5. RENTAL → category=rental (po INVOICE, před CLIENT)
      6. FIRMA_BUDOVA → category=firma_budova
      7. CLIENT_DOMAINS → category=client
      8. IMPORTANT domain / exact + conditional
      9. UNIMPORTANT sub-kategorie (banks → energie → eshops → develop → sw → doprava → komora)
     10. UNIMPORTANT (generic newsletter)
     11. COLD_SPAM_DOMAINS_RE + firstname.lastname patterns
     12. Soft-spam TLDs (.pl/.ru/.ua/.cn/.tw/.de) → přísnější kontrola
     13. Default → unsure
    """
    folder = item.get("folder") or ""
    from_addr = (item.get("from_addr") or "").strip().lower()
    subject = (item.get("subject") or "").strip()
    body = (item.get("body_text") or item.get("snippet") or "")[:500]
    s = subject.lower()
    text = (subject + " " + body).lower()
    domain = from_addr.split("@", 1)[-1] if "@" in from_addr else ""
    local = from_addr.split("@", 1)[0] if "@" in from_addr else ""

    # 1) Junk-source = spam
    if folder == "Junk":
        return RuleResult("spam", None, 0.99, "marketing", "source folder = Junk")

    # 2) Hard scam patterns
    if "firebaseapp.com" in from_addr:
        return RuleResult("spam", None, 0.98, "marketing", "firebaseapp mass scam")

    if domain in CZECH_FAKE_EU_DOMAINS:
        return RuleResult("spam", None, 0.95, "marketing", "Czech-fake product .eu spam")

    if any(domain.endswith(t) for t in SPAM_TLDS_HARD):
        # Whitelist legit shops
        if not any(w in from_addr for w in SHOP_TLD_WHITELIST):
            return RuleResult("spam", None, 0.92, "marketing", f"spam TLD: {domain.rsplit('.', 1)[-1]}")

    # 3) Subject scam keywords
    for kw in SCAM_SUBJECTS:
        if kw in text:
            return RuleResult("spam", None, 0.93, "marketing", f"scam keyword: {kw}")

    # 4) INVOICE
    if from_addr in INVOICE_FROM_EXACT:
        return RuleResult("invoice", None, 0.95, "supplier", "invoice exact match")
    if any(f in from_addr for f in INVOICE_FROM_CONTAINS):
        return RuleResult("invoice", None, 0.93, "supplier", "invoice contains pattern")

    # Conditional invoice
    if from_addr == "info@shoptet.cz":
        if "výzva k platbě" in s:
            return RuleResult("invoice", None, 0.95, "supplier", "Shoptet platba")
        return RuleResult("unimportant", "sw", 0.85, "service", "Shoptet notifikace")
    if from_addr == "kontakt@mbank.cz":
        if "výpis" in s or "email push" in s:
            return RuleResult("invoice", None, 0.93, "bank", "mBank výpis")
        return RuleResult("unimportant", "banks", 0.88, "bank", "mBank marketing")
    if from_addr == "info@nev-dama.cz":
        if "platba" in s or "platby" in s:
            return RuleResult("invoice", None, 0.92, "supplier", "Nev-dama platba")
        return RuleResult("unimportant", "eshops", 0.85, "marketing", "Nev-dama marketing")
    if from_addr == "info@growjob.com":
        if "objednávka" in s or "zaplacení" in s:
            return RuleResult("invoice", None, 0.92, "supplier", "Growjob platba")
        return RuleResult("unimportant", "sw", 0.85, "marketing", "Growjob newsletter")

    # 5) RENTAL — nájemníci a správa nemovitostí (po INVOICE, před CLIENT)
    if from_addr in RENTAL_FROM_EXACT or _domain_match(domain, RENTAL_DOMAINS):
        return RuleResult("rental", None, 0.93, "personal", f"rental sender: {from_addr}")

    # 6) FIRMA_BUDOVA — cenové nabídky pro firmu/budovu
    if from_addr in FIRMA_BUDOVA_FROM_EXACT or _domain_match(domain, FIRMA_BUDOVA_DOMAINS):
        return RuleResult("firma_budova", None, 0.9, "supplier", f"firma/budova sender: {from_addr}")

    # 7) CLIENT — známé klientské domény (po INVOICE, před IMPORTANT)
    if domain in CLIENT_DOMAINS or _domain_match(domain, CLIENT_DOMAINS):
        return RuleResult("client", None, 0.92, "customer", f"client domain: {domain}")

    # 7.5) Exact-match overrides PŘED domain-based IMPORTANT
    # Důvod: některé exact addresses (dalibor@collabim.cz, mailer-daemon@mail.prvni-pozice.com)
    # patří do unimportant podsložky, i když jejich domain je v IMPORTANT_DOMAINS.
    if from_addr in BANKS_FROM_EXACT:
        return RuleResult("unimportant", "banks", 0.9, "bank", "bank exact override")
    if from_addr in ENERGIE_FROM_EXACT:
        return RuleResult("unimportant", "energie", 0.9, "service", "energie exact override")
    if from_addr in ESHOPS_FROM_EXACT:
        return RuleResult("unimportant", "eshops", 0.9, "marketing", "eshop exact override")
    if from_addr in DEVELOP_FROM_EXACT:
        return RuleResult("unimportant", "develop", 0.9, "service", "dev tool exact override")
    if from_addr in SW_FROM_EXACT:
        return RuleResult("unimportant", "sw", 0.9, "service", "SaaS/SW exact override")
    if from_addr in DOPRAVA_FROM_EXACT:
        return RuleResult("unimportant", "doprava", 0.9, "service", "doprava exact override")
    if from_addr in KOMORA_FROM_EXACT:
        return RuleResult("unimportant", "komora", 0.9, "service", "komora exact override")

    # 6) IMPORTANT
    if domain in IMPORTANT_DOMAINS or _domain_match(domain, IMPORTANT_DOMAINS):
        if domain == "nic.cz":
            return RuleResult("domeny", None, 0.95, "service", "CZ.NIC doménová notifikace")
        if domain == "mssf.cz":
            return RuleResult("important", None, 0.9, "gov", "MS2021+ govt depeše")
        if domain == "mojedatovaschranka.cz":
            return RuleResult("important", None, 0.95, "gov", "Datová schránka")
        if domain.endswith(".gov.cz") or domain == "mv.gov.cz":
            return RuleResult("important", None, 0.92, "gov", "Govt notifikace")
        if domain in {"prvni-pozice.com", "michalmartinek.cz"}:
            return RuleResult("interni", None, 0.95, "personal", "Interní firemní pošta")
        return RuleResult("important", None, 0.88, "customer", f"important domain: {domain}")

    if from_addr in IMPORTANT_FROM_EXACT:
        return RuleResult("important", None, 0.88, "service", "important exact match")

    # Conditional ČS
    if from_addr == "ceskasporitelna@csas.cz":
        if "trvalá platba neproběhla" in s or "nedostatečný zůstatek" in s:
            return RuleResult("important", None, 0.92, "bank", "ČS trvalá platba neproběhla")
        return RuleResult("unimportant", "banks", 0.85, "bank", "ČS notifikace")

    # 6) UNIMPORTANT podsložky
    if from_addr in BANKS_FROM_EXACT or _domain_match(domain, BANKS_DOMAINS):
        return RuleResult("unimportant", "banks", 0.9, "bank", "bank newsletter/marketing")
    if from_addr in ENERGIE_FROM_EXACT or _domain_match(domain, ENERGIE_DOMAINS):
        return RuleResult("unimportant", "energie", 0.9, "service", "energie/dobíjení notifikace")
    if from_addr in ESHOPS_FROM_EXACT or _domain_match(domain, ESHOPS_DOMAINS):
        return RuleResult("unimportant", "eshops", 0.9, "marketing", "eshop newsletter")
    if from_addr in DEVELOP_FROM_EXACT or _domain_match(domain, DEVELOP_DOMAINS):
        return RuleResult("unimportant", "develop", 0.9, "service", "dev tool notifikace")
    if from_addr in SW_FROM_EXACT or _domain_match(domain, SW_DOMAINS):
        return RuleResult("unimportant", "sw", 0.9, "service", "SaaS/SW notifikace")
    if from_addr in DOPRAVA_FROM_EXACT or _domain_match(domain, DOPRAVA_DOMAINS):
        return RuleResult("unimportant", "doprava", 0.9, "service", "doprava/zásilky")
    if from_addr in KOMORA_FROM_EXACT or _domain_match(domain, KOMORA_DOMAINS):
        return RuleResult("unimportant", "komora", 0.9, "service", "hosp. komora newsletter")

    # 7) Generic UNIMPORTANT
    if from_addr in UNIMPORTANT_FROM_EXACT or _domain_match(domain, UNIMPORTANT_DOMAINS):
        return RuleResult("unimportant", None, 0.88, "marketing", "newsletter generic")

    # 8) COLD SPAM patterns
    if COLD_SPAM_DOMAINS_RE.search(from_addr):
        return RuleResult("spam", None, 0.9, "marketing", "cold B2B / spam domain pattern")

    # firstname.lastname@suspicious TLD (mass cold)
    if FIRSTNAME_LASTNAME_RE.match(local):
        if domain.endswith(".eu") and domain not in IMPORTANT_DOMAINS:
            return RuleResult("spam", None, 0.9, "marketing", "firstname.lastname@*.eu cold pattern")
        if domain.endswith((".co", ".cc", ".info", ".guru", ".one", ".com.co")):
            return RuleResult("spam", None, 0.88, "marketing", "firstname.lastname@cold TLD")

    # Gibberish local @ suspicious TLD
    if GIBBERISH_LOCAL_RE.match(local) and (
        domain.endswith(".shop") or domain.endswith(".buzz")
        or domain.endswith(".com.tr") or domain.endswith(".in.rs")
        or domain.endswith(".eu")
    ):
        return RuleResult("spam", None, 0.93, "marketing", "gibberish local + suspicious TLD")

    # 9) Soft-spam TLDs — přísnější kontrola
    # Pokud doména končí na .pl/.ru/.ua/.cn/.tw/.de a nemá legit whitelist:
    if any(domain.endswith(t) for t in SPAM_TLDS_SOFT):
        if domain not in SOFT_SPAM_WHITELIST_DOMAINS and domain not in IMPORTANT_DOMAINS:
            # Pokud sender vypadá automatizovaně (no-reply / noreply / info / sales) → spam
            if local in {"info", "sales", "contact", "hello", "marketing", "support",
                         "newsletter", "no-reply", "noreply", "admin", "office"} \
                    or local.startswith(("info", "sales", "contact", "noreply", "no-reply")):
                return RuleResult("spam", None, 0.85, "marketing", f"soft-spam TLD automated sender: {domain}")
            # Firstname.lastname pattern už řešen výš
            # Jinak unsure (může to být legit kontakt)
            return RuleResult("unsure", None, 0.5, "unknown", f"soft-spam TLD: {domain} — needs review")

    # 10) Default — unsure
    return RuleResult("unsure", None, 0.5, "unknown", "no pattern matched")
