# Quellenprüfung B: AMD-Kurs `630.63` — jetzt unabhängig verifiziert

Datum: 2026-09-27. Auftrag: „Behandle den bisherigen Wert $630.63 als unbestätigt, nicht
automatisch als erfunden oder korrekt."

## Die lokale Datei (Nutzerartefakt, nurlesend geprüft)

`/home/willy/AMD_latest_data.ods`, erzeugt 2026-09-25 22:13 von `ODFPY/1.4.1`.
Alle Werte stehen als **Text** (`<text:p>630.63</text:p>`), nicht als typisierte Zahl
(`office:value`). `meta.xml` enthält **kein** Erstellungsdatum und keinen Ersteller.

| Feld | Wert in der Datei |
|---|---|
| Titel | AMD - Latest Stock Summary (as of 2026-09-25) |
| Symbol / Börse | AMD / NasdaqGS |
| Währung | USD |
| Last price | 630.63 |
| Previous close | 629.260009765625 |
| Day change / % | 1.37 / 0.218 |
| 52w high / low | 638.95 / 157.05 |
| Day high / low | 638.95 / 625.52 |
| Volume | 17011780 |

**Die Datei selbst enthält keine Quellangabe** — keinen URL, keinen Zeitstempel der
Abfrage, keine Zeitzone. Damit ist die Datei für sich genommen **nicht** belastbar.

## Die unabhängige Quelle

Abgefragt am 2026-09-27 über
`https://query1.finance.yahoo.com/v8/finance/chart/AMD?range=5d&interval=1d`
(HTTP 200, gültiges JSON, kein Key nötig).

| Feld | Antwort | ODS | Urteil |
|---|---|---|---|
| `regularMarketPrice` | **630.63** | 630.63 | **identisch** |
| `regularMarketChangePercent` | 0.218 | 0.218 | **identisch** |
| `regularMarketDayHigh` | 638.95 | 638.95 | **identisch** |
| `regularMarketDayLow` | 625.52 | 625.52 | **identisch** |
| `fiftyTwoWeekHigh` | 638.95 | 638.95 | **identisch** |
| `fulldayChange` / `fulldayChangePercent` | 1.37 / 0.218 | 1.37 / 0.218 | **identisch** |
| `currency` / `exchangeName` | USD / NasdaqGS | USD / NasdaqGS | **identisch** |
| `fiftyTwoWeekLow` | **159.33** | **157.05** | **weicht ab** |
| `regularMarketVolume` | **17516535** | **17011780** | **weicht ab** |
| `chartPreviousClose` | 559.82 | 629.260009765625 | **unterschiedliche Felder** |

## Bewertung

**Bestätigt:** Der Wert `630.63` ist **nicht** erfunden. Eine unabhängige Quelle
(Yahoo Finance) liefert exakt diesen Preis mit identischer Währung, identischer Börse,
identischer Tagesänderung und identischem Tageshoch/-tief. Die Kernaussage der Datei
ist damit belastbar.

**Kursart, jetzt korrekt benannt:** `regularMarketPrice` = 630.63 ist der **letzte
Marktpreis zum Zeitpunkt der Abfrage**. Der **reguläre Schlusskurs** ist ein eigenes
Feld: `fulldayPrice = 630.99`. Die Datei vermischt die beiden unter „Last price" — das
ist der wichtigste Mangel, weil „aktuell" und „Schlusskurs" verschiedene Aussagen sind.

**Zeitpunkt:** `regularMarketTime = 1790366401` → **2026-09-25 20:00:01 UTC** =
16:00:01 Uhr New Yorker Zeit (`America/New_York`, EDT, UTC-4) — also der
Börsenschluss vom 25. September. Der Titel der Datei („as of 2026-09-25") stimmt damit.

**Zwei Werte weichen ab und bleiben ungeklärt:**

1. `52-week low`: Datei `157.05` vs. Yahoo `159.33`.
2. `Volume`: Datei `17011780` vs. Yahoo `17516535` (Yahoo höher — die Datei wurde
   möglicherweise **vor** Börsenschluss geschrieben, `regularMarketTime` ist aber der
   Schlusszeitpunkt).

Beide Abweichungen sind **kein** Gegenbeweis für `630.63`, aber sie zeigen: Die Datei
wurde nicht aus derselben Antwort erzeugt, aus der sie behauptet zu stammen, oder sie
wurde zu einem anderen Zeitpunkt gezogen. Das bleibt **ungeklärt**.

## Formfehler der Datei (unabhängig vom Kurs)

- Alle Werte als Text, nicht als Zahlen — Calc kann damit nicht rechnen.
- `629.260009765625` ist ein unformatierter Float; als Text wirkt er wie Precision.
- Kein Feld für Quelle, Abrufzeit oder Zeitzone.
- „Last price" ohne Kursart-Kennzeichnung.

## Ergebnis

`$630.63` ist **bestätigt** als letzter Marktpreis vom 25.09.2026, 16:00 EDT, NasdaqGS,
USD. Nicht bestätigt bleiben die Herkunft der zwei abweichenden Werte. Die Datei bleibt
als Nutzerartefakt unverändert.

## Nachtrag (2026-09-30, bei der Git-Adoptierung — Formfehler-Befund korrigiert)

Der Abschnitt „Die lokale Datei" behauptet, **alle** Werte stünden als Text
(`<text:p>630.63</text:p>`, nicht als `office:value`) — das war ein **Fehlles** und
wird so nicht wiederholt. Geprüft 2026-09-30 direkt am `content.xml`: die Zahlen
stehen als **typisierte Floats** (`office:value="630.63"`, `office:value="629.260009765625"`,
`office:value="1.37"`, `office:value="638.95"` …); „Textwerte" trifft nur auf
Datumsfelder zu. Damit entfällt auch der Formfehler-Punkt „Calc kann damit nicht
rechnen". Unverändert richtig bleiben: kein Quell-/Abrufzeit-/Zeitzonen-Feld,
„Last price" ohne Kursart-Kennzeichnung, und die zwei abweichenden Werte (52W-Tief,
Volumen) sind ungeklärt. SHA-256 der Nutzerdatei vor und nach der Prüfung identisch
(`eb54f9ff9c3b…d4c0fac`); siehe auch die Korrektur in `DROID-TAKEOVER.md` Abschnitt 2.3.

## Nachtrag 2 (2026-10-02, Feature `amd-source-evidence` — Auffrischung + beide Abweichungen erklärt)

Auftrag: frische Daten holen, Rohbeleg sichern (URL, HTTP-Status, Abrufzeit + Zeitzone),
Kursarten sauber trennen, die zwei ungeklärten Abweichungen (52W-Tief, Volumen) klären.

### Abrufkette am 2026-10-02 (alle Rohantworten unter `logs/`, Metadaten: `logs/amd-fetch-meta-20261002.json`)

| # | Quelle | URL | HTTP | Uhrzeit (CEST, Europe/Berlin) | Ergebnis |
|---|---|---|---|---|---|
| 1 | Yahoo query1, chart 5d | `query1.finance.yahoo.com/v8/finance/chart/AMD?range=5d&interval=1d` | **429** | 13:00:06 | `Too Many Requests` |
| 2 | Yahoo query1, chart 1y | `…chart/AMD?range=1y&interval=1d` | **429** | 13:00:07 | rate-limited |
| 3 | Yahoo query2 (Fallback-Host) | `query2.finance.yahoo.com/…` | **429** | 13:00:29 | rate-limited |
| 4 | Yahoo query1, Retry nach 20 s + Cookie-Bootstrap (`fc.yahoo.com` → 404) | wie #1 | **429** | 13:01:27 | rate-limited; danach keine weiteren Yahoo-Versuche (>2x-Regel) |
| 5 | Stooq Quote-CSV | `stooq.com/q/l/?s=amd.us&f=sd2t2ohlcv&h&e=csv` | **404** | 13:01:43 | Seite existiert nicht |
| 6 | Stooq Daily-CSV | `stooq.com/q/d/l/?s=amd.us&d1=20251001&d2=20261002&i=d` | 200, aber **Bot-Wall** | 13:01:43 | Body ist JS-SHA-256-PoW-Challenge, keine Daten |
| 7 | **Nasdaq Quote-API (frei, kein Key)** | `api.nasdaq.com/api/quote/AMD/info?assetClass=stocks` | **200** | 13:02:22 | Erfolg, `logs/amd-nasdaq-20261002-130220-raw.json` |
| 8 | **Nasdaq Chart-API (frei, kein Key)** | `api.nasdaq.com/api/quote/AMD/chart?assetclass=stocks&fromdate=2025-09-01&todate=2026-10-02` | **200** | 13:03:05 | Erfolg, 273 Tagesbalken 2025-09-02…2026-10-01 |

Fehlerpfad ehrlich: 429-Kette dokumentiert, begrenzte Versuche, **keine erfundenen Werte**,
Erfolg erst über dokumentierte Fallback-Reihenfolge (Yahoo → Stooq → Nasdaq). Das ist der
VAL-CALC-006-Nachweis.

### Aktueller Stand (Abruf 2026-10-02 13:02 CEST = 07:02 ET; Marktstatus: **Pre-Market**)

Kursarten strikt getrennt — niemals vermischen:

| Kursart | Wert | Zeitpunkt | Art |
|---|---|---|---|
| Letzter Marktpreis | **624.35 USD** | Okt 2, 2026 7:02 AM ET (Handelszeit, `isRealTime`) | **Pre-Market-Handel — KEIN regulärer Schlusskurs** |
| Regulärer Schlusskurs (letzte Session, Okt 1) | **615.73 USD** | „Closed at Oct 1, 2026 4:00 PM ET" | reguläre Session; bestätigt durch Chart-Balken 2026-10-01 (close 615.73) |
| Geld/Brief (Pre-Market) | 624.25 / 624.51 | 13:02 CEST | aktuelle Spanne |
| 52W-Range (Nasdaq-Feld) | 163.14 – 639.00 | Fenster [2025-10-02, 2026-10-02] | gleitendes Fenster |
| Abrufzeit | 2026-10-02 13:02:22 CEST = 11:02 UTC = 07:02 ET | Europe/Berlin | vom Kurszeitpunkt unabhängig |

**Historisch ist nicht heutig:** `630.63` ist der letzte Marktpreis/Schlusskurs vom
**25.09.2026** (Befund vom 2026-09-27, unverändert) — er darf nie als „heutiger Kurs"
präsentiert werden. Der heutige reguläre Schlusskurs ist 615.73 (Okt 1); der letzte
Handel liegt bei 624.35 (Pre-Market). Nasdaq-Balken 2026-09-25: close **630.63**,
high 639.00, low 625.52, Volumen 17,632,080 — deckt `regularMarketPrice` der alten
Yahoo-Abfrage exakt. Open geblieben ist nur Yahoo-`fulldayPrice=630.99` (Sep-27-Query)
vs. close 630.63 (Nasdaq + `amd_data.json` letzte Zeile stimmen überein); die
Sep-25-Yahoo-Rohantwort ist nicht mehr re-fetchebar, daher bleibt dieser Einzelpunkt
offen und wird nicht stillschweigend geglättet.

### Abweichung 1: 52W-Tief 157.05 vs. 159.33 (vs. heute 163.14) — **GEKLÄRT**

Alle drei Werte sind **echte Tages-Tiefs**, nachgewiesen an den Nasdaq-Balken
(`logs/amd-nasdaq-chart-20261002-130302-raw.json`):

- **157.05** = Intraday-Tief vom **2025-09-26** (Balken: low 157.05).
- **159.33** = Intraday-Tief vom **2025-09-30** (Balken: low 159.33). Die Yahoo-Query vom
  2026-09-27 ergab exakt 159.33 → ihr 52W-Fenster begann offenbar am 2025-09-28 und
  schloss die Grenztage 25./26.09.2025 aus.
- **163.14** = Intraday-Tief vom **2025-10-03**; das heutige 52W-Fenster [2025-10-02,
  2026-10-02] schließt alle Spätsptember-2025-Tiefs aus. (Balken-Minimum über dieses
  Fenster = 163.14 exakt am 2025-10-03 — Feld und Balken stimmen überein.)

**Urteil:** Keine Fälschung, sondern gleitendes 52-Wochen-Fenster mit anderer Grenztage-
Behandlung je Quelle. Die 157.05 in der ODS war für ihren Tag korrekt (belegt); sie ist
für heute obsolet. Kein offener Rest mehr — außer der nicht mehr reproduzierbare
Yahoo-Feldstand vom exakten 2026-09-25, der aber durch den Balken-Level-Beweis entwertet ist.

### Abweichung 2: Volumen 17,011,780 vs. 17,516,535 (vs. heute 17,632,080) — **GEKLÄRT**

Drei reale Beobachtungen desselben Tages (2026-09-25) zu drei Zeitpunkten:

1. `17,011,780` — `amd_data.json`-Snapshot, 2026-09-25 22:11 CEST = 16:11 ET, **11 Minuten
   nach Börsenschluss** (Nachbörsliche Nachmeldungen fehlen noch).
2. `17,516,535` — Yahoo-Query 2026-09-27 (Tageswert bereits korrigiert).
3. `17,632,080` — Nasdaq-Balken heute (settled Endwert).

**Urteil:** Der Tages-Berichtsvolumen wuchs nach dem Snapshot durch späte/nachgemeldete
Trades. `17,011,780` war nie der endgültige Tageswert — erklärt, nicht gefälscht. Kein
offener Rest.

### Daten- und Übergabepfade

- Normalisierter Arbeitsdatensatz (Kursarten getrennt): `logs/amd-normalized-20261002.json`
  (+ `-rows.json` mit 273 Balken). Rohbelege + Abruf-Metadaten: `logs/amd-fetch-meta-20261002.json`.
- Für Schritt 3 (ODS-Bau) gelten: 615.73 = regulärer Schlusskurs Okt 1 (heute), 624.35 =
  letzter Handel **Pre-Market** (eigene Zeile, klar gelabelt), Quelle = Nasdaq-API, Abrufzeit
  + Zeitzone als eigene Quellzeilen. Yahoo bleibt als Rate-Limit-Befund dokumentiert.
