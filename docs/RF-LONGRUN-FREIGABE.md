# Freigabe-Vorlage — RF-01 / RF-11 / RF-12 (echter Langlauf)

Diese Datei ist die Entscheidungsvorlage für den **einzigen verbleibenden großen Blocker** im
Engine-Recovery-Paket. Alles andere aus Kapitel 09 ist implementiert und lokal belegt
(`docs/RF-STATUS.md`, Register `audit/REQUIREMENTS.json`). RF-01/11/12 hängen ausschließlich an
einem echten Modell-Langlauf — es fehlt kein Code.

## 1. Warum das eine Freigabe braucht

Ein Lauf nach RF-01 reproduziert einen Kontext von ~152k Token über die 80-Iterationsgrenze
hinaus gegen einen echten Provider. Das verbraucht echtes Budget und läuft über Stunden. Beides
kann ich nicht selbst entscheiden, und ABNAHME.md §0 verbietet, so einen Pflichtfall als
„bestanden“ zu verbuchen, solange er nicht wirklich gelaufen ist. Der ehrliche Zustand ist
heute `not_run` — und bleibt es ohne deine Freigabe.

## 2. Was es bringt (welche Kriterien sich damit schließen)

| Kriterium | Was der Lauf belegt |
|---|---|
| RF-01 | Baseline und Fix reproduzieren das 152k-/80-Iterations-Szenario mit Request-, Retry- und Kontextdaten |
| RF-11 | Kernpfade funktionieren in normalem Hermes **und** integriertem Skaldir, CLI/Desktop bleiben bedienbar |
| RF-12 | Vorher/Nachher-Bericht mit TTFT, Dauer, Retry-Wartezeit, Token/Kosten, Kompressionsverlust, Blockern |
| S15 / S19 | reproduzierbarer Performance-Bericht, eingefrorene Aufgaben, alle Versuche, Rohdaten, Kosten |
| ABNAHME §18 (E2E-10) | >80-Iterations-/Segmentlauf: Segmentgrenze, Checkpoint, genau-einmaliges Folgesegment, kumulatives Budget, Nutzerpause verhindert Fortsetzung |

Der Nutzen ist nicht „noch ein Test grün“, sondern: **die drei Ziele, die dieses Paket überhaupt
begründen** (kein Kontextverlust am Limit, keine stillen Wiederholungen, kein Doppel-Side-Effect)
werden erstmalig unter echten Bedingungen und mit echten Kosten gemessen statt nur simuliert.

## 3. Zwei Stufen — Stufe 1 ist billig und sofort sinnvoll

### Stufe 1 — Trassierung (Minuten, Kosten minimal)

Ziel: beweisen, dass die Messkette selbst trägt, **bevor** Geld ausgegeben wird.

1. Komponierten Engine-Baum bauen (Artefakt, kein Live-Eingriff):
   `rf02, rf03, rf04, rf05, rf06, rf07, rf08, rf09, rf10` in genau dieser Reihenfolge auf eine
   Kopie anwenden, danach alle Probes + Engine-Regressionsprobe 13/13.
2. Fixture-/Rohdatenschicht laufen lassen:
   `~/.hermes/hermes-agent/venv/bin/python tools/rf_measure.py --out outputs/rf01/`
   → ein Rohdatensatz je logischer Anfrage (connect, response headers, erster Chunk, erster
   semantischer Delta, letzter Fortschritt, Abschluss/Abbruch), zweiseitige Attribution
   (Client-Beobachtung ↔ Provider-Eigeneinschätzung) und die vom Motor **selbst** abgeleiteten
   Stale-Stream-Schwellen für 16k/64k/96k/152k.
3. Kurzer echter Agent-Lauf **mit kleinem Budget** (nicht 152k): beweist Parent/Child-Zuordnung,
   Event-Cursor, Stop-Wirkung, Resume-Pfad und dass die Overlays im echten Loop nichts brechen.
4. Vorher/Nachher-Gerüst nach RF-12 füllen, soweit aus Stufe 1 belegbar; Rest bleibt ausdrücklich
   `not_run`.

Ergebnis Stufe 1: belastbare Messkette + ehrlicher Teilbericht. Kosten im Bereich weniger
Modellaufrufe.

### Stufe 2 — der eigentliche Langlauf (Stunden, echtes Budget)

1. Zielaufgabe mit ~152k Kontext über die 80-Iterationsgrenze; Providerseite mit Störung
   (Stall/Timeout-Fixture), damit RF-02/RF-05/RF-07 im echten Lauf greifen.
2. Nutzerpause während des Laufs, Start-Stopp-Messung (RF-10 lokal, späte Antworten inert).
3. Segmentgrenze: Checkpoint, absichtlicher Summary-Ausfall, automatisches **genau einmaliges**
   Folgesegment, kumulatives Budget.
4. Kontrollierter Crash zwischen Side Effect und lokaler Bestätigung (RF-09 im echten Lauf),
   danach Resume: Zustand `unknown` → Reconciliation, **kein** blinder Doppelaufruf.
5. Abschluss: RF-12-Bericht mit Vorher/Nachher, Token/Kosten je Weg, TTFT, Retry-Wartezeit,
   Kompressionsverlust, Blockern; `docs/audit/BENCHMARK-*.jsonl` als Rohdaten.

## 4. Kosten — ehrliche Größenordnung, keine Zusage

Ich nenne die Rechnung, nicht eine erfundene Summe. Bei *n* Iterationen mit Kontext *c* ergibt
sich die Eingabemenge grob als Σ über die Iterationen. Für den Worst Case (kein Caching,
c ≈ 152k, n = 80) sind das ~12 Mio Eingabetoken. Mit aktivem Prompt-Caching — das Hermes
standardmäßig nutzt und dessen Erhalt hier Invariante ist — liegt die **bezahlte** Menge
deutlich darunter, weil der Prefix je Iteration wiederverwendet wird. Die tatsächliche Zahl
steht erst nach dem Lauf fest, und genau deshalb steht sie im Bericht und nicht in der Planung.

Was ich brauche, damit das nicht aus dem Ruder läuft:
* **Provider und Modell**, die für den Lauf benutzt werden dürfen (bei mir aktiv:
  `opencode-go/deepseek-v4.1-flash`; für einen Kostenvergleich sinnvoll wäre derselbe Kopf wie in
  `docs/BENCHMARK-RUN-SCHEMA.json` hinterlegt).
* **Harte Obergrenze** für Token bzw. Kosten und für die Wanduhrzeit; wird sie erreicht, bricht
  der Lauf ab und der Bericht sagt genau das.
* **Erlaubt oder verboten:** Providerseitige Störungsinjektion (Stall/Timeout) und ein
  kontrollierter Prozess-Crash im Lauf. Ohne beides lässt sich RF-01 nur halb belegen; mit beidem
  ist der Lauf aussagekräftig, aber es wird ein echter Abbruch provoziert.

## 5. Sicherheitsnetz

* Läuft **ausschließlich** auf einer Engine-Kopie mit angewandten Overlays; der Live-Baum bleibt
  nachweislich unangetastet (`grep -c "gv-host-overlay" ~/.hermes/hermes-agent/agent/turn_finalizer.py` → 0).
* Keine Nutzer-Produktionssession wird für Fault Injection benutzt (ABNAHME §7).
* Abbruch jederzeit per Stop/Ctrl+C; der Lauf schreibt inkrementell Rohdaten, ein Abbruch kostet
  also maximal die letzte Iteration.
* Keine Secrets in Rohdaten oder Berichten: die Redaction-Pfade aus RF-06/RF-07 sind im Lauf
  aktiv, und der Bericht wird vor dem Commit auf Klartextmuster geprüft (die SEC-17-Lehre:
  `hunter2`-Zählung = 0).

## 6. Was ich nach deiner Freigabe konkret tue

1. Komponierten Baum bauen und alle Probes + Engine-Regression 13/13 nachweisen.
2. Stufe 1 komplett fahren und den Teilbericht vorlegen (mit Kosten-Zahl aus Stufe 1).
3. Erst dann Stufe 2 starten — und wenn die Obergrenze greift, ehrlich mit Teilbericht enden
   statt die Grenze zu überschreiten.
4. Ergebnis: RF-01/11/12 + S15/S19 + E2E-10 gemappt, §117-Gate-Ziffern 4 und 9 belegt,
   `docs/audit/PERFORMANCE.md` mit echten Zahlen.

**Ohne Freigabe passiert nichts von alldem** — die betroffenen Kriterien bleiben `not_run`, und
das Register sagt das weiterhin ausdrücklich.
