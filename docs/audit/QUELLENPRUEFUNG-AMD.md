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
