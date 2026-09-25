# TOOLS-V2 — Trace-Mining aus echten Ausführungen & Health als Auswahl-Gate (§1.5 + §1.6)

**Block:** B2 (Welle 1, Masterprompt 14.09.2026) · **Eigentum:** `src/godsv/capability_mining.py`,
`src/godsv/capability_health.py`, `src/godsv/events.py`, `src/godsv/nightshift.py`,
`tests/test_trace_mining.py`, `tests/test_health_events.py`, `tests/test_capability_mining.py`.

Statuslegende wie im Projekt üblich: **exists** · **wired** · **tested** ·
**live-verified** — plus Restgrenzen. Zahlen sind eigene Messungen (Kommando steht dabei).

## 0. Kurzfassung

| Auftragspunkt | Ergebnis |
|---|---|
| §1.5 abgeschlossene Ausführungen ↔ Trace-Mining | `consume_completed_traces()` liest NUR gepaarte call/result-Ausführungen, redigiert, dedupliziert über stabile Run-IDs, persistenter Cursor, idempotenter Replay |
| §1.6 Health-Änderungen → Events | `capability_health_changed` bei jedem Zustandswechsel (KINDS + CHAT_KINDS), try/except, Caller stirbt nie |
| §1.6 Selektionswirksamkeit | `capability_health.selectable(runtime, name) -> (bool, Grund)`; 4-Achsen-Trennung dokumentiert |

## 1. Semantik: was ist eine „abgeschlossene Ausführung" (§1.5)

**Quelle** ist ausschließlich der Event-Store (`events.db`) über `EventStore.stream()`.
Als abgeschlossen zählt ein Tool-Aufruf nur, wenn sein Ergebnis vorliegt
(`_completed_runs`, `src/godsv/capability_mining.py:1020`).

- **Paarung** (Idee aus `completion.run_evidence_from_events`, `src/godsv/completion.py:1609`,
  dort nur gelesen — `completion.py` gehört Welle 2): beide Seiten paaren über eine
  Payload-Call-ID (`call_id`/`tool_call_id`/`id`); Fallback ist der **älteste ungepaarte
  Aufruf desselben Tools derselben Session** (FIFO, jeder Aufruf genau einmal); ein Ergebnis
  ohne Tool-Namen (Demo-Fixtures) fällt auf den ältesten ungepaarten Aufruf seiner Session
  zurück.
  **Live-Messung (eigene Zählung, read-only):** der produktive Store trägt 8041
  `tool_call` und 8041 `tool_result` — **keine** der Zeilen hat eine Call-ID. Der FIFO-Fallback
  ist damit der reale Pfad, nicht der Sonderfall.
- **Fehlschlag**: `error`-Feld gesetzt **oder** `status` in (`error`, `failed`) **oder**
  `exit_code` ≠ 0 (Top-Level oder im `result`-JSON-String der Wrapper) — `_result_failed`
  (`:968`). Ein fehlgeschlagener Schritt **beendet den Run** und ist nie Teil davon
  („der Flow nach einem Fehler ist ein anderer Flow").
- **Run** = maximale Sequenz abgeschlossener, erfolgreicher Aufrufe einer Session; Grenzen
  sind Session-Wechsel, Idle-Lücke über 1800 s oder ein Fehlschlag. Mindestlänge 2 Schritte
  (wie `traces_from_events`) — ein einzelner Aufruf ist kein wiederkehrender Flow.
- **Nicht abgeschlossene Aufrufe** (kein Ergebnis) werden nie gemined und **splitten nicht**:
  eine Logging-Lücke darf keine Run-Grenze erfinden.
- **Fremde Event-Arten** (`thought`, `capability_health_changed`, Gate-Verdikte …) werden vor
  jeder Run-Logik übersprungen. Vorher hätte ein Health-Event (Pseudo-Session
  `capability-health`) jeden laufenden Run in der Mitte zerschnitten — der Fix steckt in
  `_completed_runs` und zusätzlich in `traces_from_events` (`:834`).
- **TraceStep-Bau**: `TraceStep(tool, operation=_operation_of(...), args=…≤ _traceable)`
  (`:492`/`:1294`/`:1301`) — nur kleine skalare Argumente (≤ 120 Zeichen) prägen die Form,
  Payloads nicht.

## 2. Cursor-Format und Begründung der Wahl

**Datei:** `traces_cursor.json` **im Runtime-Verzeichnis** (`TRACES_CURSOR_FILE`,
`capability_mining.py:34`; Schreiben atomar in `save_traces_cursor`, `:941`).

Beispiel (gekürzt):

```json
{
  "version": 1,
  "processed_seq": 22918,
  "consumed_runs": {
    "20260913_214522_8abf89:22104": {"session": "20260913_214522_8abf89",
                                      "first_seq": 22104, "last_seq": 22131, "steps": 4}
  },
  "updated": 1757857000.0
}
```

Begründung der Wahl:

1. **Ort**: das Runtime-Verzeichnis ist der Ort, an dem `events.db` (Quelle) und die vom
   Konsum gespeisten Ledger (`capability_candidates.jsonl`, `capability_health.jsonl`,
   `tool_usage.jsonl`) liegen — der Cursor gehört zu beiden.
2. **Format**: Klartext-JSON — ein Operator kann den Verarbeitungsstand lesen, sichern und
   gezielt zurücksetzen (Replay), ohne SQLite-Werkzeuge.
3. **Atomarität**: Schreiben über Temporärdatei + `os.replace` (`:963-966`) — ein Crash
   hinterlässt nie einen halben High-Water-Mark; ein zerrissener Cursor könnte sonst Runs
   überspringen (verlorene Kandidaten).
4. **Konvention**: `capability_health` hält seinen Nutzungs-Cursor schon genauso
   (`capability_health_cursor.json`) — eine Konvention, nicht zwei.
5. **Kein zweiter Store**: `events.db` ist append-only; der Fortschritts-Cursor ist eine
   Ableitung, kein zweites Register — deshalb Datei statt DB.

`processed_seq` ist der High-Water-Mark (Seq des letzten verarbeiteten Runs). Ist seit dem
letzten Lauf nichts Neues da, endet der Lauf als billiger No-op (`:1199`). Der
Eintragsbestand ist bei 5000 Runs begrenzt (`_CURSOR_MAX_RUNS`, `:42`); Eviction kann **kein**
Doppelzählen verursachen, weil die Wahrheit über verbrauchte Runs am Kandidaten hängt (siehe §3).

## 3. Dedupe-Schlüssel (stabile Run-IDs) und Replay-Schichten

**Run-ID = `f"{session}:{first_seq}"`** — `first_seq` ist die Event-`seq` des ersten
abgeschlossenen Aufrufs; `seq` ist das `AUTOINCREMENT` des Event-Stores, also stabil über
Restart, Replay und Neustart des Prozesses (Testbeweis: die IDs `s1:1`, `s2:5` aus der
Fixture; mit Dangling-Call davor `s2:6`).

Zwei Dedupe-Schichten, absichtlich redundant:

1. **Cursor-Schicht** (`cursor["consumed_runs"]`): ein zweiter Lauf liest dieselben Runs,
   findet sie als verbraucht und meldet `new == 0` — ohne Mining, ohne Kandidatenschreiben.
2. **Kandidaten-Schicht** (`Candidate.evidence_runs`, `:112` + `merge`, `:156`): der Kandidat
   trägt die IDs aller Runs, die ihn belegen. `merge` zählt nur **neu belegte** Runs
   (`occurrences = existing + len(new_runs)`) und eine Wiederholung ohne neue Runs ist ein
   No-op (kein Append). Damit kann auch ein **gelöschter/zurückgesetzter Cursor** keinen
   Kandidaten aufblähen.

**Warum trotzdem das ganze Fenster erneut gemined wird**: die Generalisierung braucht die
*Wiederholung* (≥ 2 Runs). Würde nur „Neu" gemined, sähe jeder nächtliche Lauf genau einen Run
und nie ein Muster. Mining-Eingabe ist deshalb der gesamte konsumierbare Fensterinhalt;
gezählt wird ausschließlich über `evidence_runs` (Testfall `test_unsettled_tail_waits…`:
Lauf 1 `new=1, candidates=0`, Lauf 2 `new=1, candidates=1`, `occurrences=2`).

Kandidaten **ohne** `evidence_runs` (Requests/Commands) behalten die alte
Akkumulations-Semantik unverändert (`test_mine_records_and_merges`: `occurrences >= 4`).

## 4. Redaction auf dem Mining-Pfad

`_redact_step` (`:998`) schickt jede String-Argumentposition durch `secretscan.redact`, bevor
irgendetwas Dauerhaftes entsteht; die Zahl der Funde steht als `redacted` im Report und wird
von `mine()` als `redacted_arguments` weitergereicht.

Begründung: `EventStore` scannt zwar beim Anhängen, aber der Mining-Pfad darf sich nicht
darauf *verlassen* — Fremdschreiber, Altbestand aus älteren Versionen, restaurierte Backups
oder neue Muster. Eine Fähigkeit ist dauerhaft; ein Sekret darin wäre es auch.

Beweis (Test `test_secrets_from_a_foreign_writer_are_redacted`): zwei komplette Runs werden
per **direktem SQLite-INSERT** (Append-Gate umgangen) in den Store geschrieben; der Konsum
meldet `redacted >= 2`, und die Kandidaten-Datei enthält `[REDACTED:openai]`, nicht das
Sekret. Grenze: `redact` ist Best-Effort über acht Regexe — was keine Regel trifft, bleibt
stehen (dokumentierte Scanner-Grenze, nicht des Minings).

## 5. Verdrahtung (Produktionseinstieg Welle 1, Hook-Punkt Welle 2)

- **`capability_mining.mine(runtime)`** (`:311`) — der Pfad von `godsvoice tools mine`
  (`src/godsv/cli.py:985-994`) — ruft jetzt `consume_completed_traces` statt des
  alten `traces_from_events`-Zwischenschritts und meldet zusätzlich
  `scanned_traces`/`new_traces`/`trace_candidates`/`redacted_arguments` im Rückgabe-Dict.
- **`nightshift.consolidate_traces(runtime, apply=False)`** (`nightshift.py:72`) —
  Offline-Konsolidierung als Schritt: liefert `scanned`, `new`, `candidates`, `redacted`,
  `processed_seq`, `cursor`, `applied`. `consolidate()` (`:158`) hängt den Schritt ein, sobald
  ein Runtime verfügbar ist (explizit übergeben oder aus dem Incident-Store-Pfad abgeleitet —
  die CLI übergibt für `--events` und `--memory` dasselbe Verzeichnis); der Report reist in
  `ConsolidationResult.traces` mit. Der Schritt steht in einem `try/except`: ein Report-Schritt
  killt nie die Konsolidierung.
- **Dry-Run-Semantik unverändert**: ohne `apply` wird **nichts** geschrieben (kein Cursor,
  kein Kandidat) — die Zahlen beschreiben, was ein angewendeter Lauf täte. Nachtläufe mit
  `--apply` bzw. `tools mine` schreiben.
- **Ende-zu-Ende über die echte CLI** (eigene Messung, tmp-Fixture mit zwei abgeschlossenen
  Runs in einer Session, `python -m godsv nightshift --events … --memory … --session cli-1`):

  | Lauf | Beobachtet |
  |---|---|
  | ohne `--apply` | rc 0, `applied: False`, **kein** `traces_cursor.json`, **keine** Kandidaten-Datei |
  | mit `--apply` | rc 0, Cursor mit `consumed_runs = ['cli-1:1', 'cli-1:7']`, `processed_seq = 9`; Kandidat mit `occurrences = 2`, `evidence_runs = ['cli-1:1', 'cli-1:7']` |
  | zweiter `--apply`-Lauf | rc 0, weiterhin **1** Kandidatenzeile (kein Doppel, kein Aufblähen) |
- **OFFEN (Welle 2, B5/Host-Wiring)**: der Plugin-Hook `on_session_end`
  (`integrations/hermes/godsv_plugin/__init__.py:1855`) ruft den Schritt noch nicht — das ist
  der vorgesehene Live-Einstieg. Ebenso druckt die Nightshift-CLI die Trace-Zahlen noch nicht
  (die CLI-Ausgabe gehört B4); die Zahlen liegen in `result.traces`.

## 6. Health-Events (§1.6)

**Kind:** `capability_health_changed` — registriert in `events.KINDS` (`src/godsv/events.py:94`,
Append-Whitelist) **und** in `CHAT_KINDS` (`:143`, Chat-Feed): eine Degradierung/Quarantäne
eines Tools betrifft die Aufgabenfläche des Nutzers und gehört deshalb in den Feed.

**Regel:** ein Event pro Zustands*wechsel* (nicht pro Ledger-Zeile). Schreiber emitten alle
über den gemeinsamen Helfer `_emit_change` (`capability_health.py:276`):
`record_building` (`:269`), `record_verification` (`:316`), `record_trigger` (`:336`),
`recheck` (`:351`), `retest` (`:371`), `retire` (`:380`) — und damit auch `observe_usage`
(`:452`), das pro Ledger-Fehlerzeile über `record_trigger` läuft.

**Payload:** `{name, from, to, trigger, reason, consecutive_issues, auto_routable}`, Session
`capability-health`, Step 0. Geschrieben mit `append_redacted` — Health-Details zitieren echte
Fehlertexte.

**Robustheit:** der komplette Append steht in `try/except Exception` (`_emit_change`): ein
nicht registriertes Kind, ein gesperrter/zerstörter Store oder ein nicht schreibbares Runtime
dürfen den Aufrufer nicht töten — die Ledger-Zeile ist zu diesem Zeitpunkt bereits dauerhaft.
Test `test_event_write_failure_does_not_kill_the_caller`: `events.db` ist ein **Verzeichnis**
(jeder Append scheitert), `record_verification` liefert trotzdem `HEALTHY`, Ledger + State
sind korrekt.

**Ruhestand ist terminal:** ein `RETIRED`-Tool bleibt `RETIRED`, auch wenn danach Trigger
beobachtet werden — der Fold überspringt Trigger nach einem retire-Eintrag
(`capability_health.py:224-229`), die Beobachtungen bleiben im Ledger. Vorher kippte ein
späterer Trigger den Zustand auf `QUARANTINED`: die Auswahl war in beiden Fällen gesperrt,
aber die Owner-Entscheidung wurde überschrieben (live im Wiring-Check beobachtet). Test:
`test_retirement_is_terminal_even_under_later_issues`.

**Beweise:**
- `test_state_change_writes_an_event`: CANDIDATE→HEALTHY schreibt genau ein Event mit
  `from`/`to`/`auto_routable`.
- `test_unchanged_state_writes_no_second_event`: zwei Verifikationen, **ein** Event.
- `test_every_transition_of_the_lifecycle_is_an_event`: die Kette
  CANDIDATE→HEALTHY→BROKEN→DEGRADED→QUARANTINED→RETIRED ergibt exakt diese fünf Events.
- `test_observe_usage_degrades_from_the_real_ledger`: eine echte `tool_usage.jsonl`-Zeile
  (`ModuleNotFoundError`) → Trigger `dependency_incompatible` → BROKEN + Event; der zweite
  Lauf klassifiziert dieselbe Zeile nicht erneut (Cursor).
- **Rot-Beweis** (Fix deaktiviert, echter Fehlschlag, restauriert): der `_emit_change`-Aufruf
  aus `record_trigger` entfernt → `test_every_transition_of_the_lifecycle_is_an_event`
  **FAILED**; restauriert → *1 passed*.

## 7. `selectable` — Auswahl-Matrix und die vier getrennten Zustandsachsen

```python
def selectable(runtime, name) -> tuple[bool, str]:  # capability_health.py:393
```

Dokumentierte Erweiterung von `routable` (`:389`): **gleiche** Quelle der Wahrheit
(`AUTO_ROUTABLE` über den Fold des Ledgers), plus Begründungstext. Ausschluss ist explizit:

| Zustand | `selectable` | Begründungstext (Anfang) |
|---|---|---|
| HEALTHY / VERIFIED | `(True, …)` | `"HEALTHY (auto-routable): …"` |
| CANDIDATE | `(False, …)` | `"CANDIDATE: not selectable: …"` |
| BUILDING | `(False, …)` | `"BUILDING: not selectable: …"` |
| DEGRADED | `(False, …)` | `"DEGRADED: not selectable: …"` |
| BROKEN | `(False, …)` | `"BROKEN: not selectable: …"` |
| QUARANTINED | `(False, …)` | `"QUARANTINED: excluded from selection, retest required: …"` |
| RETIRED | `(False, …)` | `"RETIRED: withdrawn by the owner, never selected: …"` |

Der Text hängt die ersten beiden Ledger-Gründe an (`reasons`), damit ein Aufrufer sagen kann,
**warum** ein Tool übersprungen wurde (statt nur „nicht routbar"). Getestet als Matrix in
`test_selectable_matrix` inkl. unbekanntem Tool (CANDIDATE, nie routbar).

**Auswahlwirkung (wired):** `route_candidates` (`capability_mining.py:1691`) filtert die
Auto-Routing-Oberfläche über `health.file_state(...)["auto_routable"]`, das Hot-Set der
Live-Insertion lässt degradierte Fähigkeiten fallen (bestehender Test
`test_degraded_capability_leaves_the_hot_set` in `tests/test_capability_learning.py:525`).
`selectable()` ist zusätzlich bereits als reichere API **verdrahtet**: das Health-Gate der
MCP-Export-Oberfläche (`health_verdict`, `src/godsv/mcpserver.py:36-74`, Block B5) konsumiert
`selectable(runtime, name)` und übernimmt Verdict + Grund, wenn es als `(bool, str)` vorliegt
(Tupel-Prüfung `:63-64`).

Politik-Unterschied, bewusst und dokumentiert: `health_verdict` lässt Tools **ohne jede
Ledger-Zeile** sichtbar („never observed as broken" — konservativ für die Export-Fläche),
während `selectable()` für so ein Tool `CANDIDATE: not selectable` sagt (Auto-Routing-Politik,
`AUTO_ROUTABLE`). Beide Regeln stehen vor ihren jeweiligen Aufrufern fest; keine der beiden
weicht die andere auf.

**Vier Achsen — getrennt, nie vermischt** (Doku zusätzlich im Modul-Docstring,
`capability_health.py:24-42`):

| Achse | Quelle der Wahrheit | Frage |
|---|---|---|
| Registrierung | `tools.db` + `generated_tools/…` (approved spec) | existiert das Tool als Spec? |
| Validierung | `manifest.yaml` → `validation` | hat es seine Fälle bestanden (broken→FAIL, working→PASS)? |
| Verfügbarkeit | Ausführungsschicht (Sandbox/Deps/Dienst) | kann es hier gerade laufen? |
| Health | `capability_health.jsonl` (dieses Modul) | wurde es real als funktionierend/defekt *beobachtet*? |

Ein registriertes, validiertes Tool kann trotzdem unhealthy sein; „unhealthy" ändert **nie**
die Registry — Health bleibt ein Reader von Fakten, kein zweites Register.

## 8. Testnachweis (Zahlen)

Eigene Messungen, Repo `/home/willy/projects/gods-voice`, Branch
`godsv-supervisor-hardening`. `pyproject.toml` setzt `addopts = "-q"` — bewusst kein zweites
`-q` (es unterdrückt die Zählzeile); jede Zahl ist aus der Ausgabe kopiert.

| Kommando | Ergebnis |
|---|---|
| `.venv/bin/python -m pytest tests/test_trace_mining.py tests/test_health_events.py -p no:randomly` | `18 passed in 0.12s` (RC=0) |
| `.venv/bin/python -m pytest tests/test_capability_mining.py tests/test_trace_mining.py tests/test_health_events.py tests/test_nightshift.py tests/test_events.py tests/test_capability_learning.py -p no:randomly` | `80 passed in 8.32s` (RC=0) |
| `.venv/bin/python -m pytest tests -p no:randomly` (voller Lauf) | `1296 passed, 1 warning in 86.28s` (RC=0) — die Zahl wächst mit den Schwester-Blöcken; gemessen nach dem letzten Commit dieses Blocks |
| `.venv/bin/ruff check src/godsv/capability_mining.py src/godsv/capability_health.py src/godsv/events.py src/godsv/nightshift.py tests/test_trace_mining.py tests/test_health_events.py tests/test_capability_mining.py` | `All checks passed!` |

Neue/erweiterte Testdateien: `tests/test_trace_mining.py` (9 Testfunktionen),
`tests/test_health_events.py` (9), `tests/test_capability_mining.py` (12 = 10 bestehende + 2
neue: `test_mine_runs_the_trace_path_once`, `test_merge_ignores_already_evidenced_runs`).

**Rot-Beweise** (Mechanik wie im Projekt üblich: die eine load-bearing Zeile entfernen, den
echten Fehlschlag beobachten, restaurieren, grün nachweisen — nie via `git stash`):

| deaktiviert | Test | beobachtet |
|---|---|---|
| `_emit_change`-Aufruf in `record_trigger` | `test_every_transition_of_the_lifecycle_is_an_event` | `1 failed in 0.05s` (RC=1) → restauriert `1 passed` |
| Zählung nur neuer Runs (`+ len(new_runs)`) | `test_merge_ignores_already_evidenced_runs` | `1 failed in 0.11s` (RC=1) → restauriert `1 passed` |
| No-op-Rückgabe bei bekannten Runs | `test_wiped_cursor_cannot_inflate_a_candidate` | `1 failed in 0.09s` (RC=1) → restauriert `1 passed` |

Der dritten Probe ist es zu verdanken, dass der Wiederholungs-Test eine **Doppelzeile**
explizit ausschließt: die Zählzeile ist load-bearing, der Early-Return nur Optimierung — eine
erste Rot-Probe gegen nur den Early-Return lief deshalb noch grün und wurde verworfen.

## 9. Restgrenzen (bewusst offen)

1. **Plugin-Hook fehlt (Welle 2)**: der Live-Einstieg `on_session_end` ruft den Trace-Schritt
   nicht — der Produktionseinstieg der Welle 1 ist die Offline-Konsolidierung
   (`tools mine`, `nightshift`). Hook-Punkt dokumentiert (siehe §5).
2. **CLI-Druck der Trace-Zahlen**: die Nightshift-CLI (Eigentum B4) gibt `Scanned/New/
   Candidates` noch nicht aus; sie reisen in `ConsolidationResult.traces` bzw. im
   `tools mine`-Dict. Kein Blockieren der Zahlen, nur der Print fehlt.
3. **Lexikalische Suche bleibt**: `search_existing`/`search_before_build` und die
   Generalisierung arbeiten weiter mit Term-/Token-Überlappung — keine Embeddings, keine
   Kausalanalyse (unverändert gegenüber Block 6).
4. **Best-only-Generalisierung**: `generalize_traces` liefert die *stärkste* Gruppe
   (`capability_mining.py:635ff`); ein kleinerer wiederkehrender Flow kann pro Lauf verdrängt
   werden. Er bleibt im Fenster und gewinnt später — Verzögerung, kein Verlust.
5. **Fensterkante**: `limit=2000` Events pro Lauf. Ein Run, der genau an der Kante
   zerschnitten würde, könnte als kürzerer Run mit anderer ID zählen; das Fenster ist groß
   genug, dass das im Normalbetrieb nicht auftritt (bewusst nicht weiter abgesichert).
6. **Cursor-Eviction ab 5000 Runs** (älteste fliegen zuerst): kein Doppelzählen möglich, weil
   `Candidate.evidence_runs` die verbrauchten Runs unabhängig vom Cursor hält.
7. **`exit_code`-Heuristik**: ein absichtlich rot gefahrener Lauf beendet den Run — konservativ
   für „wiederkehrende *erfolgreiche* Ausführung".
8. **Severity der Trigger-Klassifikation** bleibt Regex (`classify_trigger`, `:434`) —
   Fehlklassifikationen sind möglich und landen im Ledger (retest-/recheck-Pfad).
9. **Live-Verifikation: nein.** Alle Zahlen stammen aus echten `EventStore`-Fixtures in
   `tmp_path`, nicht aus einem Lauf gegen die produktive Runtime; der produktive
   `events.db`-Store wurde nur **read-only** vermessen (Kinds/Pairing-Verteilung, §1). Ein
   angewendeter Nachtlauf gegen `~/.hermes/godsv` ist bewusst nicht Teil dieses Blocks
   (Nebeneffekt auf den Live-Betrieb) und bleibt der Zentrale überlassen.
