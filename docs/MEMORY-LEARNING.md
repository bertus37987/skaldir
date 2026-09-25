# Memory Learning (Block 7) — Schemas, Retrieval-Regel, Staleness/Provenance

**Auftrag:** Master-Plan `docs/IMPLEMENTATION-PLAN-live-control-plane.md` §Block 7
**Ground Truth:** `docs/COMPLETE_AUDIT-2026-09-13.md` (§3 MEM-01…31, audit-3)
**Regel:** Nicht neu bauen — erweitern, verdrahten, testen. Status je Abschnitt: `exists` → `wired` → `tested` → `live verified`.
**Sprache:** Code/UI englisch, Doku deutsch.

Der Satz, um den sich dieses Dokument dreht:

> **Ein schwacher Incident-Match darf niemals direkt eine starke Intervention auslösen.**
> (§38 Golden Set, Fall GS-04 „falscher Incident Match" → erwartet: *Memory ablehnen*.)

---

## 1. Incident-Memory — Schema

**Mandatiertes Schema (11 Felder):**

| Feld | Typ | Spalte/Quelle | Anmerkung |
|---|---|---|---|
| `failure_class` | str | `incidents.failure_class` (neu) | Fallback beim Lesen von `""`; beim Schreiben `or kind` |
| `root_cause` | str | `incidents.root_cause` (alt) | bereits vorhanden |
| `component` | str | `incidents.component` (neu) | Grundlage für den Anwendbarkeits-Check |
| `evidence` | list[str] | **Alias** auf `evidence_refs` (alt) | kein zweites Feld; Alias dokumentiert |
| `failed_strategies` | list[str] | `incidents.failed_strategies` (neu) | JSON-Text |
| `successful_recovery` | str | `incidents.successful_recovery` (neu) | Fallback beim Rendern: `resolution_steps` |
| `confidence` | float | `incidents.confidence` (alt) | 0..1, bleibt float (kein kategorialer Umbau) |
| `scope` | str | `incidents.scope` (neu) | `effective_scope` fällt auf `repo_scope` (alt) zurück |
| `first_seen` / `last_seen` | float | alt | unverändert |
| `provenance` | dict | `incidents.provenance` (neu) | JSON-Text |

`Incident.to_schema_dict()` liefert exakt diese 11 Schlüssel; `incidents.SCHEMA_FIELDS`
ist die maßgebliche Reihenfolge.

**Rückwärtskompatibilität (belegt):** Die Migration ist rein additiv.
`SCHEMA_V2_COLUMNS` wird per `ALTER TABLE ... ADD COLUMN` nur für **fehlende** Spalten
angewandt; vorhandene Zeilen werden nie umgeschrieben. Bestehende Datensätze lesen mit
dokumentierten Defaults (`scope` → `repo_scope`, `failure_class` → `""`), und ein
Repeat-Sighting überschreibt die alten Achsen nicht mit leeren Werten.
Bei dieser Gelegenheit ein echter Altfehler behoben: eine Datenbank aus der Zeit **vor**
der FTS-Tabelle hatte keine Index-Einträge — `search_text` fand dort still nichts.
`IncidentStore._backfill_fts_locked()` indiziert fehlende Zeilen beim Öffnen (idempotent).

**Verdrahtung (Schreibpfad):** `nightshift.consolidate()` — der einzige produktive
Incident-Writer (CLI `godsvoice nightshift`) — füllt jetzt `failure_class`
(`monitor.classify_tool_error`), `evidence_refs`, `scope`-fähiges `component`-Umfeld und
`provenance` (`make_provenance`, inkl. Session/Step/Event-Referenz).

**Embedding-Spalte:** `IncidentStore.set_embedding()` ist der fehlende Schreibpfad
(audit MEM-10: „Live `incidents with embedding: 0`"). Ohne Aufrufer bleibt die Spalte
NULL — die Doku behauptet weiterhin **keine** Semantik: `HashingEmbeddings` ist
Trigram-Overlap auf Zeichenebene, ausdrücklich lexikalisch.

Status: `exists` ✅ · `wired` ✅ · `tested` ✅ (`test_memory_learning.py::TestIncidentSchema`,
`::TestNightshiftProvenance`) · `live verified` ⚠️ **eingeschränkt** — Live-`memory.db`
hat weiterhin nur 2 Incidents und die Spalten sind dort noch nicht migriert (die DB wird
beim nächsten echten Öffnen additiv erweitert); ein echter Nachtlauf über neue Sessions
steht aus.

---

## 2. Retrieval-Regel (erzwungen)

Die Regel ist in `src/godsv/memory.py` implementiert und hat genau drei Schritte —
in dieser Reihenfolge:

1. **retrieve** — `retrieve_incidents()`: exakte Signatur → FTS5 → optional Vektor.
2. **applicability check** — `classify_incident()` / `classify_research()`:
   deterministisch, **ohne LLM** (ein Urteil darf nie davon abhängen, ob ein Modell
   gerade erreichbar ist).
3. **use/reject** — nur `strong` darf eine starke Intervention auslösen.

**Urteile:**

| Urteil | Bedeutung |
|---|---|
| `strong` | darf eine starke Intervention auslösen (`can_trigger_strong_intervention()`) |
| `informational` | darf als Kontext gezeigt werden, **niemals** als starke Grundlage |
| `reject` | darf gar nicht verwendet werden — nicht einmal gerendert |

**Reject-Gründe (harte Ausschlüsse):** `expired`, `archived`, `contradicted`,
`component_mismatch`, `failure_class_mismatch`, `goal_scope_mismatch`.

**Strong-Bedingungen:** exakte Signatur **oder** `similarity ≥ 0.6` **und** mindestens
ein positives Achsen-Match (component/failure_class/goal_scope) **und**
`confidence ≥ 0.5` **und** Status `active`. Sonst `informational`.

**Score-Herkunft (ehrlich):** `similarity = max(Term-Overlap, 0.75 × Vektor-Cosine)`.
Der Vektor-Anteil ist trigram-lexikalisch und wird deshalb niedriger gewichtet; der
Term-Overlap ist der belastbare Teil.

**§38/GS-04-Regressionstest — der Pflichtfall:**
`tests/test_memory_learning.py::TestRetrievalRule::test_false_incident_match_never_triggers_a_strong_intervention`
seedet einen textuell plausiblen Incident (gemeinsames Vokabular, wird von `search_text`
also *gefunden*) und prüft mit abweichendem `component`/`failure_class`, dass
`strong_intervention_allowed is False`, alle Urteile ≠ `strong` sind und die Gründe
`component_mismatch` + `failure_class_mismatch` tragen.

**Enforcement an den Live-Grenzen (verdrahtet, opt-in):**

* `retrieval.find_evidence(..., context=RetrievalContext(...))` — filtert das Ergebnis
  durch das Gate; abgelehnte Einträge werden **verworfen**, behaltene tragen einen
  `applicability`-Verdict auf dem `Evidence`-Objekt. Ohne `context` bleibt der Aufruf
  unverändert (kein Bruch bestehender Aufrufer).
* `memory.gate_evidence()` / `memory.strong_basis()` — Ein-Zeilen-Hooks für weitere
  Aufrufer. Untrusted-Recherche-Notizen bleiben als Kontext sichtbar, zählen aber
  **nie** als starke Grundlage (`untrusted_source`).
* `IncidentStore.intervention_signatures()` — die **anwendbarkeitsgeprüfte**
  Signatur-Menge für die Monitor-Eskalation (`known_incident_repeat`), im Gegensatz zu
  `signatures()`, das weiterhin *alles* listet (inkl. archiviert/abgelaufen/unreviewed).

Status: `exists` ✅ · `wired` ✅ (`find_evidence`-Gate, `intervention_signatures`) ·
`tested` ✅ (12 Tests in `TestRetrievalRule` + 4 in `TestEvidenceBoundaryGate` + 2 in
`TestInterventionEligibility`) · `live verified` ⚠️ **nicht** — der Live-Monitor wird
weiterhin mit `signatures()` gespeist (`integrations/hermes/godsv_plugin/__init__.py:242`).
Der Ein-Zeilen-Umstieg auf `intervention_signatures()` wurde bewusst **nicht** in die
fremde Block-4/5-Datei geschrieben; er ist offener Punkt (s. §7).

---

## 3. Research-Database — Schema

**Mandatiertes Schema (11 Felder):** `question`, `finding`, `evidence`, `sources`,
`created_at`, `valid_until`, `confidence`, `component`, `goal_scope`, `contradictions`,
`reused_from`. `RESEARCH_SCHEMA_FIELDS` in `research.py` ist die maßgebliche Reihenfolge;
`archive_to_schema()` rendert **jedes** Archiv (auch Altbestand) auf genau diese Schlüssel.

* `question` = das Problem; `finding` = der kondensierte Redirect.
* `valid_until` wird beim Schreiben auf `created_at + 14 Tage` gesetzt (`_STALE_AFTER_SECONDS`);
  `stale_after` bleibt als Alias erhalten (keine Namens-Drift nach außen).
* `confidence` bleibt der String `"unverified"` (Provenienz-Wahrheit: Voice-Findings sind
  unbelegt); `confidence_score` ist die normalisierte Zahl — **Alt-/unbelegte** Einträge
  mappen auf `0.0`, nie auf einen Wert, der verifiziert aussähe.
* Zusätzlich immer mitgeschrieben: `provenance` (`origin`, `recorded_at`, `session_id`,
  `step`, `event_refs`, `source`) und `search_plan`.

**Search-before-research (erzwungen im Lauf):** `ResearchRunner.run()` ruft **vor** jeder
Untersuchung `plan_research(state_dir, problem, component, goal_scope)` auf:

1. vorhandene Research durchsuchen,
2. Freshness (`valid_until`, Alter) **und** Anwendbarkeit (component/goal_scope) prüfen,
3. nur die fehlenden **oder veralteten** Teile neu recherchieren.

`plan_research()` liefert `{reusable, inapplicable, questions, needed, checked}`:
wiederverwendbare Treffer stehen in `reused_from`, abgelaufene werden **nicht** benutzt,
sondern zu offenen Fragen (`questions`). Ein frischer, stark ähnlicher Treffer für
dasselbe Problem beendet den Lauf ohne zweite Voll-Recherche und **ohne LLM-Aufruf**
(`ResearchResult.used_prior_finding`, `research_needed=False`, `reused_research`).

**Legacy-Archive:** 37 bestehende `research-*.json` bleiben unverändert auf der Platte;
sie werden beim Lesen auf das neue Schema gemappt (`question` ← `problem`,
`finding` ← `llm.finding`, `sources`/`contradictions` ← leer). Kein Rewrite nötig.

Status: `exists` ✅ · `wired` ✅ (`plan_research` im Lauf) · `tested` ✅
(`TestResearchSchema`, 5 Tests; die bestehenden Tests in `test_research_reuse.py` bleiben grün) ·
`live verified` ⚠️ **nur Reuse-Teil**: Live 37 Archive, davon 29 mit nicht-leerem
`reused_from` (audit MEM-04). Die *neuen* Felder erscheinen erst in Archiven, die nach
diesem Commit geschrieben wurden — die 37 Altarchive haben `valid_until: 0`,
`provenance: {}`, `question: ""` (gemessen, s. §8).

---

## 4. Staleness und Provenance

**Staleness ist eine Sperre, keine Anzeige** (audit MEM-12: „`stale`-Flag nur Anzeige,
keine Sperre").

* `valid_until` überschritten → `reject` mit Grund `expired`; der Eintrag ist keine
  gültige Grundlage — weder für eine Intervention noch für eine Antwort.
* Alter > 14 Tage → `stale` (Anzeige **und** Ausschluss aus `reusable`).
* Research: ein abgelaufener Treffer wird zu einer offenen Frage
  (`"re-verify prior finding <id> (expired): …"`) — die Lücke wird also nicht still
  übersprungen, sondern als Arbeit sichtbar.
* `valid_until = 0` bedeutet ausdrücklich **kein Ablaufdatum** (kein „abgelaufen").

**Provenance muss nachvollziehbar sein** (woher, wann, in welchem Kontext):

*Incident:* `make_provenance(origin, session_id, step, event_refs, source, recorded_at)`.
`upsert` füllt die unvermeidbaren Teile selbst (`origin="incident_proposal"`,
`recorded_at`), damit ein vergesslicher Writer kein unverfolgbares Record erzeugt.
Repeat-Sightings **mergen** (nicht überschreiben): `_merge_provenance` behält die
Session, übernimmt neue Origins, vereinigt `event_refs`.

*Research:* `provenance` im Archiv mit `origin="voice_research"`, Session, Step,
`event_refs = ["<session>:<step>"]`, `source = "<trigger_kind>:<fingerprint>"`.

*Prüfseite:* `memory.provenance_of(entry)` → `{origin, recorded_at, session_id, step,
event_refs, source, complete}`. `complete` ist nur `True`, wenn `recorded_at` gesetzt
**und** ein konkreter `origin` vorhanden ist — fehlende Provenienz wird als
unvollständig **gemeldet**, nicht geglättet. Korrupte JSON-Werte degradieren zu `{}`
statt zu crashen.

Status: `exists` ✅ · `wired` ✅ (nightshift schreibt, Research schreibt) · `tested` ✅
(11 Tests in `TestStalenessAndProvenance`) · `live verified` ⚠️ **Teil**: Live-`memory.db`
hat 2 Incidents **ohne** `provenance` (Altbestand → korrekt als unvollständig
gemeldet); Live-Research hat `valid_until: 0` in allen 37 Altarchiven.

---

## 5. Cold/Warm-Verhalten

| | **Cold** (kein passendes Memory) | **Warm** (anwendbares Memory) |
|---|---|---|
| Incident-Retrieval | keine Kandidaten → `strong_intervention_allowed is False` | `strong` → Intervention erlaubt |
| Research | `needed=True`, volle Bounded-Recherche (LLM-Pfad) | `research_needed=False`, **kein** LLM-Aufruf, Redirect wird übernommen |
| Provenienz | neues Record mit eigenem `origin` | `reused_from`/`reused_research` verweisen auf die Quelle |
| Unsicherheit | „Deterministische Einschätzung (kein LLM)" | nennt Quelle **und** Alter und verlangt Prüfung vor Anwendung |

`memory.RetrievalLog` zählt Retrievals/use/informational/reject und setzt `warm=True`,
sobald ein `strong`-Urteil vorlag — Grundlage für die spätere Kennzahl
„Memory verbessert spätere Arbeit" (Benchmark `+Memory`, s. `BENCHMARK-RUN-SCHEMA.json`).

Beide Wege sind getestet: `TestCrossSessionLearning::test_cross_session_log_shows_warm_use`
(cold → `False`, warm → `True` auf demselben Store) und
`test_second_session_reuses_the_recovery_without_researching_again`.

---

## 6. Cross-Session-Nachweis (messbar)

**Test:**
`tests/test_memory_learning.py::TestCrossSessionLearning::test_second_session_reuses_the_recovery_without_researching_again`

Aufbau: **ein** Store (`memory.db` + `events.db`), zwei Sessions, `ScriptedLLM` ohne
geskriptete Antworten (ein LLM-Aufruf wäre ein Fehler, kein Zufall).

1. **Session 1 (cold):** `ImportError: cannot import name split_cv from trainer` →
   `used_prior_finding is False`, `research_needed is True` (volle Recherche). Danach wird
   der Fix als `active`-Incident gelernt: `component="trainer"`,
   `failure_class="import_error"`, `successful_recovery="Import auf split_cv umgestellt"`,
   `confidence=0.9`, Provenienz `session-1/step 1`.
2. **Session 2 (warm):** dasselbe Problem → `used_prior_finding is True`,
   `research_needed is False`, `reused_research == [<id aus Session 1>]`.
   Zusätzlich: `evaluate_retrieval` liefert `strong_intervention_allowed is True`,
   `ref == "incident#1"`, und der Text enthält `split_cv` — **genau der gelernte
   Recovery-Pfad**.
3. **Kontrolle:** derselbe Schritt gegen einen *leeren* Store ergibt
   `research_needed is True` — der Unterschied ist also das Memory, nicht der Code-Pfad.

**Gemessenes Signal:** kein zweiter Voll-Research-Lauf und kein LLM-Aufruf
(`client.calls == []` nach Session 2) — bei identischem Problem.

**Restgrenze (ehrlich):** Das ist ein **Komponenten-Beweis mit deterministischem Client**,
kein echter Modelllauf. Damit ist belegt, dass die Maschinerie den wiederkehrenden Fall
messbar günstiger behandelt (weniger Recherche, direkte Anwendung des Recovery-Pfads) —
**nicht** belegt ist eine Erfolgsquote am echten Modell (das wäre Benchmark-Sache,
`schema.configurations[full_godsv_memory]`). Kein echter Modelllauf wurde durchgeführt;
die Aussage „Memory verbessert Task-Erfolg messbar" ist damit **nicht** behauptet.

---

## 7. Dedupe/Retention-Status (Audit-Nebenbefund)

**Befund (audit, Top-11):** Event-Duplikate ohne Dedupe, keine Retention, 19,6 MB.
**Live nachgemessen** (read-only Kopie des echten Stores, `godsvoice memory-audit`):

```
events: 15539 · sessions: 110
bytes_on_disk: 22900736 · over_age(30.0d): 0
duplicate groups: 20 (7275 extra rows)
  20260911_155709_98c547 step 1 tool_call: 149×
  20260911_155709_98c547 step 2 tool_result: 126×
  …
retention policy: report-only (read-only, no pruning)
```

Der Befund ist also **größer** als im Audit beschrieben (20 Gruppen / 7275 Extra-Zeilen
statt 5 Gruppen).

**Was implementiert wurde — Prüfung, nicht Eingriff** (`memory.py`):

* `duplicate_event_groups(store)` → `(groups, extra_rows, scanned)` über
  `(session_id, step, kind)`, **read-only**, in Seiten (`_page_events`).
* `retention_report(store, max_age_days=…)` → Alter/Anzahl/Sessions/Bytes +
  Duplikate + `policy` (`mode="report-only"`).
* CLI: `godsvoice memory-audit --events <db> [--max-age-days N]`.
* Zugehörige Tests: `TestDedupeAndRetention` (4 Tests), inkl. Nachweis, dass die Prüfung
  den Store **nicht** verändert.

**Warum kein Eingriff:** Ein `CREATE UNIQUE INDEX` auf `(session_id, step, kind)` würde
auf dem Live-Store **fehlschlagen** (7275 Verletzungen), und beides — Index/Prune — liegt
in `src/godsv/events.py`, einer Datei eines anderen Arbeitsblocks (`git status`: von
einem parallelen Agenten in Arbeit). Der Auftrag verlangt ausdrücklich: Dedupe/Retention
nur umsetzen, wenn es **ohne Eingriff in fremde Dateien** geht, sonst präzise
dokumentieren. Die Prüfung ist implementiert und getestet, die Durchsetzung ist offener
Punkt.

**Offene Punkte (symbol-verankert, in `memory.ENFORCEMENT_GAPS` und in der CLI-Ausgabe):**

| Ort | Lücke |
|---|---|
| `src/godsv/events.py::EventStore._append` | `INSERT INTO events` ohne Uniqueness-Key; append wird nicht dedupliziert |
| `src/godsv/events.py::EventStore.iter_session` / `::EventStore.stream` | laden unbegrenzt; keine Retention/Kompaktierung existiert |
| `src/godsv/events.py::KINDS` | jeder neue Event-Kind muss dort registriert werden → kein Memory-Event aus `memory.py` möglich |
| `tests/test_memory_learning.py::TestDedupeAndRetention` | nur Verifikation; die Durchsetzung (Unique-Key/Pruning) braucht `events.py` |
| `integrations/hermes/godsv_plugin/__init__.py:242` (Block 1/4/5) | Live-Monitor nutzt weiterhin `signatures()` statt `intervention_signatures()` |

Die Referenzen sind absichtlich **symbol**-statt zeilenverankert: `events.py` wird von
einem anderen Block bearbeitet, Zeilennummern verschieben sich.

---

## 8. Status je Abschnitt

| Abschnitt | exists | wired | tested | live verified | Restgrenze |
|---|---|---|---|---|---|
| 1 Incident-Schema (11 Felder, additiv) | ✅ | ✅ nightshift | ✅ 6 Tests | ⚠️ eingeschränkt | Live-DB noch nicht geöffnet/migriert; nur 2 Incidents |
| 1a Rückwärtskompatibilität | ✅ | ✅ | ✅ 3 Tests (Legacy-DB-Fixture) | ⚠️ | Live-DB erst beim nächsten Öffnen migriert |
| 1b FTS-Backfill für Alt-DBs | ✅ | ✅ | ✅ | ⚠️ | Live-DB hatte FTS-Einträge, Backfill war dort ein No-Op |
| 1c Embedding-Schreibpfad | ✅ | ⚠️ kein Produktionsaufrufer | ✅ 1 Test | ❌ `with embedding: 0` | Embedding-Provider bleibt offene Produktfrage (MEM-10) |
| 2 Retrieval-Regel | ✅ | ✅ opt-in Gate | ✅ 18 Tests | ⚠️ | Live-Pfad nutzt noch `signatures()` |
| 2a §38/GS-04 falscher Match | ✅ | ✅ | ✅ Pflicht-Regressionstest | ⚠️ | — |
| 3 Research-Schema | ✅ | ✅ im Lauf | ✅ 5 Tests | ⚠️ neue Felder erst ab jetzt | 37 Altarchive bleiben auf Platz unverändert |
| 3a Search-before-Research | ✅ | ✅ | ✅ 2 Tests | ✅ Reuse live (29/37) | Semantik der Ähnlichkeit ist lexikalisch |
| 4 Staleness | ✅ | ✅ | ✅ 5 Tests | ❌ | Live hat 0 Einträge mit `valid_until` |
| 4a Provenance | ✅ | ✅ | ✅ 11 Tests (gemeinsam mit 4) | ⚠️ | Live-Incidents sind Altbestand → `complete=False` |
| 5 Cold/Warm | ✅ | ✅ `RetrievalLog` | ✅ 2 Tests | ❌ | Kennzahl-Erhebung im echten Lauf offen |
| 6 Cross-Session-Nachweis | ✅ | ✅ | ✅ 2 Tests | ❌ **Restpunkt** | deterministischer Client, kein echter Modelllauf |
| 7 Dedupe-Prüfung | ✅ | ✅ CLI | ✅ 4 Tests | ✅ live gemessen (20 Gruppen/7275) | Durchsetzung braucht `events.py` |
| 7a Retention-Regel | ⚠️ nur Report | ✅ CLI | ✅ 1 Test | ✅ live (23 MB, 0 über Alter) | Pruning braucht `events.py` |

**Testzahlen (gemessen):** `tests/test_memory_learning.py` = **48 Tests**
(TestIncidentSchema 6 · TestRetrievalRule 12 · TestResearchSchema 5 ·
TestStalenessAndProvenance 11 · TestCrossSessionLearning 2 · TestEvidenceBoundaryGate 4 ·
TestInterventionEligibility 2 · TestNightshiftProvenance 2 · TestDedupeAndRetention 4).
Betroffene Nachbardateien (`test_incidents` 10, `test_memory_retrieval` 10,
`test_research` 6, `test_research_reuse` 4, `test_notes` 7, `test_nightshift` 7,
`test_pause_hard` 12, `test_events` 2, `test_events_security` 14) = **72 Tests** →
zusammen **120**, alle grün. Volle Suite grün, `ruff check src tests tools integrations
app-overlay` grün.

**Geänderte/neue Dateien:**

* neu: `src/godsv/memory.py`, `tests/test_memory_learning.py`
* erweitert: `src/godsv/incidents.py`, `src/godsv/research.py`, `src/godsv/retrieval.py`,
  `src/godsv/nightshift.py`, `src/godsv/cli.py`
* Lint-Fix außerhalb des Auftrags: `tests/test_monitor_joint_reading.py` (2× B007,
  vorbestehend, blockierte das „ruff grün"-Kriterium)

**Bewusst nicht angefasst** (Eigentümerschaft anderer Arbeitsblöcke):
`completion.py`, `gates.py`, `mcpserver.py`, `dashboard/plugin_api.py`, `monitor.py`,
`pause.py`, `events.py`, `run_supervisor.py`, `tools/gv_crash_recovery.py`, Plugin-Hooks,
`src/godsv/untrusted.py` (parallel entstanden).

---

## 9. Restpunkte (offen, präzise)

1. **Cross-Session am echten Modell** — der Nachweis in §6 nutzt einen deterministischen
   Client. Ein realer Zwei-Session-Lauf mit Modell (Aufgabe → Fehler → Fix → neuer Lauf
   mit warmem Store) und Messung von Schritten/Tokens steht aus; das ist
   Benchmark-Arbeit (`+Memory`-Konfiguration), nicht Memory-Mechanik.
2. **Live-Umstieg des Monitors** auf `IncidentStore.intervention_signatures()`
   (`integrations/hermes/godsv_plugin/__init__.py:242`) — Ein-Zeilen-Änderung in einer
   fremden Datei.
3. **Event-Dedupe/Retention-Durchsetzung** in `src/godsv/events.py` (Unique-Key +
   Pruning/Archivierung); Vorarbeit (Prüfung, Messung, CLI, Tests) liegt vor.
4. **Live-Migration der echten `memory.db`** (2 Incidents) — passiert beim nächsten
   Öffnen; danach ist `provenance` für den Altbestand weiterhin leer (korrekt, aber
   sichtbar als `complete=False`).
5. **Embedding-Provider** (MEM-10) — weiterhin offene Produktfrage; der Schreibpfad
   (`set_embedding`) existiert jetzt, die Qualität bleibt lexikalisch/trigram, und die
   Doku behauptet weiterhin keine Semantik.
6. **Retention-Zielwerte** (Alter/Bytes) sind Produktentscheidung; der Report nimmt
   `max_age_days` als Parameter (Default 30) und behauptet nichts über die richtige Zahl.
