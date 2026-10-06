# Browser-Research-Abnahme — Multi-Tab-Recherche mit echtem Scrollen (VAL-WEB-004)

Feature: `browser-research-acceptance` (m4-web), erfüllt `VAL-WEB-004` (Multi-Tab-
Research-Abnahme). Live-Abnahme am echten Produkt-Pfad: die Browser-Sitzung wird über
die echte CLI-Oberfläche `skaldir browser {probe,discover,ensure,tabs,detach}`
versorgt (NICHT `skaldir desk browser` — der Parser lehnt das ab, gemessen in
browser-session-reuse), die Recherche läuft in der Produkt-Session `skaldir`
(ONE state dir, Produkt-Session-Name `skaldir`, kein `close --all`, Fremde unberührt).

Status: **ABGESCHLOSSEN (live-abgenommen)** — Skeleton zuerst, Beweise laufend
angehängt (Methode gemäß mission-AGENTS.md). Run-Datum: 2026-10-06, Repo-Stand
`ed6342d` (Vortests); keine Produkt-Code-Änderung in diesem Run.

## Abschnitt 1: Session-Lifecycle (vor der Recherche)

Rohdaten: `logs/browser-research/step01-probe.txt`, `step02-discover-before.txt`,
`step03-ensure.txt`. Alle Aufrufe über die echte CLI (`uv run skaldir browser …`,
Exitcodes echt, keine Pipes).

| Schritt | Befehl | Exit | Kernaussage (Envelope `data`) |
|---|---|---|---|
| T0 probe | `browser probe` | 0 | backend=agent-browser, available=true, version 0.38.1, node v26.8.1 |
| discover VOR ensure | `browser discover` | 0 | state=unknown, session_id="", starts=0, tab_count=0 — kein Browser |
| ensure | `browser ensure` | 0 | action=launched, state=ready, session_id=skaldir, starts=1, tab_count=0 |
| tabs NACH ensure | `browser tabs` | 0 | tabs=[], agent_tabs=[], user_tabs=[], Regelzeile „only agent-created tabs may be closed; there is no close --all (§6)“ |

Der Ensure startete real (Start-Zähler 0→1). Produkt-Session-Name `skaldir`.

## Abschnitt 2: Stale-Session-Ehrlichkeitscheck (Protokoll vor/nach jedem langen Schritt)

Anchoring (2026-10-06, Residual aus browser-session-reuse): der Manager selbst
reconciliert keine „ready“-Session, deren Browser-Prozess AUSSERHALB der getrackten
Pfade starb — nur leere Beobachtungen und fehlgeschlagene Launches werden
reconciliert; die Erholung dort läuft über agent-browsers eigenen Per-Call-Relaunch.
Verfahren: vor/nach jedem Schritt wurde der State gemessen
(`skaldir browser discover`/`tabs` + `agent-browser session list`/`tab list`).
Rohdaten: `logs/browser-research/step01…step17`.

Messprotokoll (alle Zeiten 2026-10-06, Europe/Berlin +02:00):

| Zeit | Messung | Ergebnis |
|---|---|---|
| 18:45:41 | discover VOR ensure | state=unknown, starts=0, session_id="" — konsistent (kein Browser) |
| 18:45:47 | ensure | launched, state=ready, starts=1 — Start-Zähler 0→1, konsistent |
| 18:50:08 | discover + `agent-browser session list` (Mitte, nach allen Tab-Öffnungen) | state=ready, **starts=1 UNVERÄNDERT** (kein unsichtbarer Relaunch), `sessions:["skaldir"]` — konsistent |
| 18:50:32 | detach | closed_agent_tabs=[], foreign_browsers_closed=false, alle 9 Tabs danach noch da — konsistent |
| **18:51:03** | **`agent-browser --session skaldir close` (getrackter Close) → `session list` zeigt weiterhin `["skaldir"]`, discover weiterhin `state=ready, starts=1` — während der Browser eben geschlossen wurde** | **Inkonsistenz Messbefund 1** |
| **18:51:04** | nächster `tab list`-Aufruf: agent-browser startet den Browser **selbst neu** (`launched:true`, neuer targetId `3CA1D445…`, leeres t1) — **ein Relaunch, den der Produkt-Start-Zähler NICHT sieht** (state file bleibt `starts=1`, `last_launch_attempt` unverändert `1791305147.3242345`) | **Inkonsistenz Messbefund 2** |
| 18:51:31 | zweiter close → `session list` → `[]` (leer); discover erneut: **state=ready, starts=1** — obwohl REAL kein Session/Browser mehr existiert | Inkonsistenz reproduziert |
| 18:51:34 | erneut `tab list` → **Relaunch erneut** (`launched:true`, targetId `20AB81FE…`), Zähler erneut blind | Inkonsistenz reproduziert |
| 18:51:42 | dritter close → `session list` → `[]` — sauberes Ende, danach keine Session-Aufrufe mehr | aufgeräumt |

**Ehrliche Einordnung:** Beobachtet wurde die divergierende State-Datei („ready“,
starts=1) gegenüber der Realität (Browser zu) UND der zählerblinde Per-Call-Relaunch —
jeweils mit rohem Envelope in `step15-close.txt` / `step16-final-cleanup.txt`.
ABER: der Trigger war jedes Mal ein getrackter Close (mein eigener Cleanup-Aufruf),
NICHT ein Prozess-Tod außerhalb der getrackten Pfade. **Der eigentliche Residual-Pfad
(Prozess stirbt außerhalb der getrackten Pfade) bleibt UNGEÜBT — nicht als verifiziert
behandelt.** Die Messbefunde zeigen dennoch real: die State-Datei reflektiert nach dem
Session-Ende NICHT die Realität, und agent-browsers eigener Per-Call-Relaunch erhöht
keinen Produkt-Zähler. Genau wie vorgegeben: Bericht ja, Code-Fix hier nein.

## Abschnitt 3: Recherche — Frage, Tabs, Quellen, Abrufzeiten

Recherchefrage (aktuell, 2026-10-06): **Marktabsdeckung für AMD** — aktueller Kurs,
Marktkapitalisierung und aktuelle Entwicklung, aus ≥3 unabhängigen Quellen, über
mehrere Tabs mit echtem Scrollen. Preis-Semantik: letzter Marktpreis ≠ Schlusskurs ≠
Kurszeit ≠ Abrufzeit (VAL-CALC-001-Regel, hier sinngemäß angewandt).

Sitzung: Produkt-Session `skaldir` (ein State dir, `~/.hermes/godsv/browser`), 8
Recherche-Tabs + das leere t1 des Launches, jede Aktion mit vorangestelltem
`tab <label>`-Switch (Tab-Attribution) und `get url`-Gegenprobe. Alle Roh-Envelope
mit `tabId`/`targetId` liegen in den `step*.txt` unter `logs/browser-research/`.

Tab-Protokoll (Zeiten = Abrufzeitpunkte, +02:00):

| Tab | tabId | Quelle | geöffnet | gelesen | Scroll | Ergebnis |
|---|---|---|---|---|---|---|
| search | t2 | Bing `https://www.bing.com/search?q=AMD+stock+price+today` | 18:46:06 | 18:46:14, 18:46:39 | ✅ scrollY 0→900 (eval-beweisen) | 10 echte organische Treffer (amd.com, ir.amd.com, en.wikipedia.org, finanzen.net, …), „About 268,000 results“, kein CAPTCHA |
| nasdaq | t3 | `https://www.nasdaq.com/market-activity/stocks/amd` | 18:46:51 | 18:46:55; Retry 18:47:04 (1 Reload, begrenzt) | Scroll-Befehl lief, scrollY blieb 0 (Fehlerseite) | **GAP: ERR_HTTP2_PROTOCOL_ERROR** (Chrome-Fehlerseite) — siehe Abschnitt 5 |
| finanzen | t4 | `https://www.finanzen.net/aktien/amd-aktie` | 18:47:14 | 18:47:18 | Scroll-Befehl lief, scrollY blieb 0 (Fehlerseite) | **GAP: Akamai „Access Denied“** (Ref #18.51253717.1791305234.4b818ccf) |
| wikipedia | t5 | `https://en.wikipedia.org/wiki/AMD` (Redirect von …/Advanced_Micro_Devices) | 18:47:27 | 18:47:30 + Infobox 18:50:08 | ✅ scrollY 0→1500 (eval-beweisen) | Artikelseite lädt; Infobox-Daten (Abschnitt 4) |
| stockanalysis | t6 | `https://stockanalysis.com/stocks/amd/` | 18:47:37 | 18:47:41; Reread nach 8 s Wartezeit 18:47:48 (begrenzt) | Scroll-Befehl lief, scrollY blieb 0 (Interstitial) | **GAP: Cloudflare-Interstitial** („Performing security verification“, Ray ID a46626259e42a63e) |
| yahoo | t7 | `https://finance.yahoo.com/quote/AMD` (→ `/AMD/`) | 18:48:02 | 18:48:23 | ✅ scrollY 0→1000 (eval-beweisen) | Titel lädt („AMD Stock Price, News, Quote & History“); Hauptquote-Panel zeigt „Oops, something went wrong“, ABER Trending-Tickers-Widget liefert AMD 656.78 (+3.96 %) — siehe Abschnitt 4 |
| ariva | t8 | `https://www.ariva.de/advanced-micro-devices-aktie` (→ `/aktien/advanced-micro-devices-inc-cdr-aktie`) | 18:48:06 | 18:48:23 + Diagnose 18:49–18:50 | ⚠️ 2 Scroll-Befehle (`scrolled:true`), scrollY blieb 0 | Lädt mit echten Kursdaten; Layout: `docH=innerH=633` trotz `bodyH=10098` — die Seite scrollt NICHT auf window/body-Ebene in diesem Render; die Beschreibung steckt in kollabierten `cut-text`-Containern (gemessen, 12b/12d) |
| macrotrends | t9 | `https://www.macrotrends.net/stocks/charts/AMD/advanced-micro-devices/stock-price-history` | 18:48:10 | 18:48:23 | Scroll-Befehl lief, scrollY blieb 0 (Interstitial) | **GAP: Cloudflare-Interstitial** (Ray ID a46626f4d95e590f) |

### Tab-Attribution (kein falscher-Tab-Beweis)

Explizite Isolationsprüfung (step12, ~18:48–18:49): nach Scroll-Aktionen auf yahoo,
ariva und wikipedia wurden die drei bereits gescrollten Tabs einzeln angewählt und
positionsmäßig nachgemessen:

| Tab | erwartete Position | gemessen | Host-Bestätigung via `get url`/eval |
|---|---|---|---|
| search (t2) | 900 | **900** ✅ unverändert | `www.bing.com` |
| wikipedia (t5) | 1500 | **1500** ✅ unverändert | `en.wikipedia.org` |
| yahoo (t7) | 1000 | **1000** ✅ unverändert | `finance.yahoo.com` |

Keine Scroll-Aktion landete im falschen Tab; jede Aktion trug den Ziel-Tab-Envelope
(`tabId` in jedem Roh-Envelope). Die ariva-Aktionen (12b–12d) liefen nach
`tab ariva`-Switch gegen t8 (targetId `56A1262F…` in jedem Envelope).

## Abschnitt 4: Ergebnisbericht der Recherche (Inhalte, Quellen, Abrufzeiten)

Recherchefrage: aktueller AMD-Marktdatenstand (Kurs, Marktkapitalisierung,
Entwicklung) aus mehreren Quellen. **Alle Werte sind gemessen (abgelesen), nicht
erfunden; jede Zeile nennt Quelle, URL, Abrufzeit (+02:00) und — wo die Quelle sie
ausweist — die Kurszeit.**

| Datenpunkt | Wert | Quelle (URL) | Abrufzeit | Kurszeit lt. Quelle | Semantik |
|---|---|---|---|---|---|
| AMD letzter Kurs (Trending-Tickers-Widget) | **656.78 USD, +3.96 %** | Yahoo Finance, `finance.yahoo.com/quote/AMD/`, Tab t7 | 18:48:23 | nicht im Widget ausgewiesen | Widget-Snapshot; KEIN reguläres Quote-Panel (Hauptpanel = „Oops, something went wrong“) — nur als Widget-Angabe mit dieser Einschränkung verwendbar |
| AMD CDR (CAD-hedged, Gettex) | **73,50 €**, +2,50 €, **+3,52 %** | ariva.de, `ariva.de/aktien/advanced-micro-devices-inc-cdr-aktie`, Tab t8 | 18:48:23 | **18:43:07 Uhr, Gettex Realtime** | NICHT der Nasdaq-Kurs: börsengehandelter Canada Deposit Receipt (WKN A3DE5D, ISIN CA00791L1067, CAD-hedged) — eigenes Instrument, EUR-Quote |
| Marktkapitalisierung | **1.361,09 Mrd. $** | ariva.de (dasselbe Tab/Instrument) | 18:48:23 | — | vom CDR-Kurs abgeleitet (Streubesitz 100 %, KGV „-“ auf CDR-Ebene) |
| Entwicklung (CDR, ariva-Baustein) | 1W +7,30 % · 3M +18,55 % · 6M +198,78 % · 1J +208,82 % · Gesamt +340,12 % | ariva.de, Tab t8 | 18:48:23 | — | CDR-Performance-Reihe der Seite |
| Unternehmensfakten | gegr. 01.05.1969; HQ Santa Clara; Nasdaq: AMD (Nasdaq-100, S&P 100/500); Umsatz 2025 US$34,6 Mrd.; operat. Ergebnis US$3,69 Mrd.; Netto US$4,34 Mrd.; ~31.000 MA (2025); Lisa Su (Chair & CEO) | Wikipedia EN, `en.wikipedia.org/wiki/AMD`, Infobox, Tab t5 | 18:50:08 | statisch (Artikelstand) | Firmen-Stammdaten, indexunabhängig |
| Suchtreffer-Lage | „About 268,000 results“, 10 organische Treffer (u. a. ir.amd.com „3 days ago“-News) | Bing, Tab t2 | 18:46:14 | — | Suchindex, keine Kursdaten im Widget-Bereich |

**Konsistenz:** Yahoo-Widget (656.78 USD, +3.96 %) und ariva-CDR (+3.52 %,
Gettex 18:43) stimmen in Richtung/Größenordnung überein; die 1.361 Mrd.-$
Marktkapitalisierung passt zur Kursordnung. Ein direkter regulärer
Nasdaq-/XETRA-Referenzkurs war von hier aus nicht erreichbar (Abschnitt 5) —
daher bewusst NUR Werte zitiert, die tatsächlich abgelesen wurden, mit ihrem
jeweiligen Instrumentenkontext.

## Abschnitt 5: Lücken (ehrlich, nicht still übersprungen)

Vier der acht angesteuerten Quellen waren von diesem Host/Session aus nicht
nutzbar — benannt, nicht übernommen, keine ersetzten/fantasierten Werte:

1. **nasdaq.com (t3):** Chrome-Fehlerseite `ERR_HTTP2_PROTOCOL_ERROR`; 1 begrenzter
   Reload-Retry (18:47:04) → unverändert. (Curl-/API-Pfad zu api.nasdaq.com ist
   laut library/environment.md separat funktionierend — hier ging es um die
   Browser-Seite, die es nicht tut.)
2. **finanzen.net (t4):** Akamai-„Access Denied“ mit Reference-ID
   `#18.51253717.1791305234.4b818ccf` — Bot-Wall.
3. **stockanalysis.com (t6):** Cloudflare-Interstitial „Performing security
   verification“ (Ray ID a46626259e42a63e); Reread nach 8 s unverändert.
4. **macrotrends.net (t9):** Cloudflare-Interstitial „Just a moment…“
   (Ray ID a46626f4d95e590f).

Teil-Lücke 5. **Yahoo-Hauptpanel (t7):** Quote-Panel selbst zeigt „Oops, something
went wrong“; nutzbar war nur das Trending-Tickers-Widget (Abschnitt 4, Einschränkung
zitierfertig vermerkt).
Layout-Anmerkung 6. **ariva (t8):** Inhalte vollständig lesbar, aber die Seite
scrollt in diesem Render nicht (docH=innerH=633; kollabierte cut-text-Container
gemessen) — Scroll-Befehle wurden ausgeführt, ergaben dort messbar keine Position.

## Abschnitt 6: Gates und Aufräumen

- **Kein Produkt-Code geändert** — dieser Feature-Run ist reine Live-Abnahme +
  Dokumentation (wie vom Feature verlangt; der Stale-Session-Befund geht als
  Bericht zurück, Code-Fix ist ausdrücklich NICHT hier umzusetzen).
- Beweis-Rezepte für Wiederholung: alle Befehle stehen 1:1 in den
  `logs/browser-research/step*.txt` (mit echten Exitcodes, keine `| tail`-Masken).
- Aufräumen (gemessen): `agent-browser --session skaldir close` (dreimal, siehe
  Abschnitt 2 — wegen des beobachteten Per-Call-Relaunch), danach
  `agent-browser session list` → `{"sessions":[]}` (18:51:44, step17).
  Kein `close --all` benutzt; Fremde-Sessions existierten nicht (Mitte-Messung
  18:50:08: `sessions:["skaldir"]` — nur die Produkt-Session).
- Produkt-Session-Regeln beachtet: Produkt-Session-Name `skaldir`, ein State dir,
  detach ohne Tab-Closure (BR-13-Regelzeile im Envelope), keine `close --all`.
