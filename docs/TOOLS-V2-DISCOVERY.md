# TOOLS-V2 — Capability-Discovery im Hermes-Ausführungspfad (§4c)

**Block:** B9 (Welle 2, Masterprompt §4c) · **Branch** `godsv-supervisor-hardening` ·
Alle Zahlen sind eigene Messungen (Kommando steht dabei), `Datei:Zeile`-Angaben am
Code geprüft. Harness- und Live-Befunde werden strikt getrennt.

Statuslegende: **exists** · **wired** · **tested** · **live-verified**.

## 0. Kurzfassung

| Auftragspunkt (§4c) | Ergebnis | Beleg |
|---|---|---|
| (1) reiche, suchbare Metadaten | `capability_mining.DISCOVERY_FIELDS` + `contract_metadata()`/`capability_metadata()` — zwölf Felder, jedes mit `sources`-Herkunft; Manifest-v2-Block `discovery:` wird konsumiert, Fehlendes bleibt leer (nicht erfunden) | `src/godsv/capability_mining.py:1729-2010` |
| (1) lexikalische Suche, optional semantisch | DE/EN-Tokenizer + leichter Stemmer über Name/Wirkung/Beschreibung/Tags/Synonyme/DE+EN-Formulierungen + strukturierte Ein-/Ausgaben/Vorbedingungen; optionaler Kanal über die **vorhandene** `HashingEmbeddings` (als *lexikalisch* gelabelt, keine neue Abhängigkeit) | `src/godsv/discovery.py:117-215`, `:842-873` |
| (1) Indexversion + Registry-Revision | `DISCOVERY_INDEX_VERSION=1`; `registry_revision()` = Digest über `tools.db`-Stat + jedes Manifest + (falls vorhanden) `tool_published`-Events vom Typ | `discovery.py:42`, `:225-299` |
| (2) Such-API + Gates + Zustände | `discover()`: Scope- und Berechtigungs-Gate **getrennt**, Kompatibilität/Budget als Hartfilter, fünf Zustände + Build-Kandidat | `discovery.py:489-611`, `:882-1140` |
| (2) Ranking | sieben gewichtete, evidente Komponenten; fehlende Messwerte = UNBEKANNT (nicht „gut"); Projekt-Vorzug nur bei Gleichstand; deterministisch | `discovery.py:61-71`, `:727-840` |
| (2) `ensure_ready`-Hook | dokumentierte Schnittstelle, ehrliche Verweigerung ohne Docker (B6) | `discovery.py:1188` |
| (3) Auslöser + Cache im Plugin | additive Hooks: neuer/geänderter Bedarf, Gap-Marker, Registry-Revision, Projektwechsel; begrenztes Kandidatenset über den sanktionierten `pre_llm_call`-Kanal; Cache mit Invalidierung | `integrations/hermes/godsv_plugin/__init__.py:2098-2320`, `:2489` |
| (4) Host-Brücke | registrierte Schemas tragen die neuen Metadaten; bewiesen mit dem **echten** `tool_search`-Katalog/BM25 des Host-Checkouts | `plugin:1973`, `:2303`; Test `test_the_host_tool_search_finds_the_capability_by_metadata` |
| (5) Eval-Runner | `tools/gv_discovery_eval.py`: Zähler/Nenner je Metrik, Harness- und Live-Modus getrennt, blockierte Fälle ausgewiesen | Harness-Lauf unten §8 |


## 1. Suchbare Fähigkeiten — der Metadaten-Vertrag

Der Suchindex liest **Vertragsfakten**, nichts anderes. Quelle ist das Manifest
(`generated_tools/<name>/manifest.yaml`, die approved Spec des gated Builders) plus die
Registry-Spec (`tools.db`); jedes Feld trägt in `sources` seinen Ursprung.

Die zwölf Felder (`capability_mining.DISCOVERY_FIELDS`, `capability_mining.py:1732`):

| Feld | Herkunft |
|---|---|
| `effect` (gewünschte Wirkung) | `manifest.discovery.effect`, sonst Vertragswort (`purpose` / `created_for` / `description` / `title`) |
| `tasks`, `synonyms`, `phrases_de`, `phrases_en`, `outputs`, `non_use_cases`, `examples`, `platforms`, `requires_services` | Manifest-v2-Block `discovery:` (bzw. gleichnamige Top-Level-Felder); ein ungetyptes `phrases` füllt beide Sprachkanäle (`test_untyped_phrases_fill_both_language_channels`) |
| `preconditions` | **strukturell abgeleitet**: `dependency:<name>` aus `dependencies`, `requires:secret://…` aus `required_secrets` (**nur Handle, nie ein Wert**), `network:<domain>` aus `network.allowed_domains`, `filesystem:<access>` |
| `inputs` | deklariert, sonst aus `spec.sample_input` (die Eingabe, gegen die der Build validiert hat): `key:jsontyp` |

Regeln (jede mit Test):

- Fehlt ein Feld, bleibt es **leer** — nichts wird erfunden
  (`test_absent_fields_stay_empty_and_are_not_invented`).
- **Keine Selbstbehauptung als Priorität**: die Beschreibung wiegt im Suchfeld-Gewicht am
  kleinsten (0,7 gegen 3,0 für den Namen, `discovery.py:74-88`), Treffer je Feld haben
  abnehmenden Ertrag (`_task_fit`, `discovery.py:614`) und das Ranking zieht nur Evidenz
  heran (Validierung, Health-Ledger, Vertragsfakten) — nie Prosa-Volumen
  (`test_a_tool_cannot_buy_priority_with_a_universal_self_description`).
- `non_use_cases` ist **kein** Suchfeld, sondern eine Warnung: ein Treffer darauf kostet
  Punkte und erscheint als Grenze im Ergebnis
  (`test_a_declared_non_use_case_is_a_warning_not_a_match`) — der Lauf, der das zeigte,
  hatte „video or audio files" als Treffer für einen Audio-/Video-Auftrag gezählt.
- Redaction: Vorbedingungen tragen ausschließlich Secret-**Handles**, dieselbe Linie wie
  das übrige Projekt (kein Wert verlässt Vertrag oder Ledger).

## 2. Die Such-API (`discovery.discover()`)

```python
discovery.discover(
    need,                      # Freitext DE/EN (der Bedarf)
    *, runtime=None,           # Registry-Verzeichnis (generated_tools + tools.db)
    entries=None,              # oder In-Memory-Metadaten (Harness/Tests)
    project_id="", inputs=(), constraints=(),
    permissions=None,          # None = nicht deklariert (ehrlich „unknown")
    services=None,             # Dienst → running|stopped|missing
    budget=None, limit=3, include_legacy=False, include_locked=False,
    cache=None, embedder=None,
) -> dict
```

Rückgabe (JSON-fähig, `discovery.py:882`):

| Feld | Inhalt |
|---|---|
| `results[]` | `name`, `capability_id`, `version`, `scope`, `project_id`, `surface`, `state`, `selectable`, `executable`, `score`, `reasons[]` (Einsatzgrund), `limits[]` (Grenzen), `unknown[]` (fehlende Messwerte), `components{}`, `preconditions[]`, `validation`, `effect` |
| `build_candidates[]` | unvalidierte Fähigkeiten — **getrennt** von jeder ausführbaren Empfehlung |
| `blocked{}` | Zähler der gefilterten Einträge (scope/authorization/compatibility/locked) — **nur Zahlen, nie Namen** |
| `error` | `null` oder strukturiert (`no_match`, `build_candidate_only`, `capability_locked`, `capability_temporarily_down`, `capability_missing_prerequisite`, `capability_startable`) inkl. Alternativen |
| `registry_revision` + `index_version` | der Stand, gegen den geantwortet wurde (nur der Digest — siehe unten) |
| `semantic` | Kanalmodus (`hashing-trigram (lexical)` bzw. `provider`) |
| `cache` | `hit`, `key`, `stats` |
| `took_ms` | Suchlatenz |

Kanäle:

- **lexikalisch** (Pflicht): Tokenizer (`normalize`/`tokens`, `discovery.py:117-160`) + leichter
  DE/EN-Stemmer (`stem`/`_pair_match`, `:167-208`). Damit treffen „Wandle … um", „umwandeln"
  und „Umwandlung" denselben Kandidaten, und `docx-datei` zerfällt in `docx` + `datei`. Der
  Stemmer ist bewusst klein und vorhersagbar (Präfixe wie `um-/aus-/ver-`, Suffixe wie
  `-ung/-en/-ing`), kein Sprachmodell.
- **semantisch** (optional, `:842`): Cosinus über die **vorhandene** Embedding-Infrastruktur
  (`godsv.embedding.HashingEmbeddings` — im Repo dokumentiert als Zeichen-Trigramme, also
  lexikalisch). Ein echter Provider lässt sich über `embedder=` einsetzen; es kommt **keine
  neue verpflichtende (bezahlte) Abhängigkeit** hinzu. Der Modus steht im Ergebnis, die
  Ähnlichkeit ist Relevanz — **keine Erfolgswahrscheinlichkeit**.

Revisionsdisziplin: `registry_revision()` (`:252`) hasht `tools.db`-Größe/-mtime, jedes
Manifest (Name, mtime, Größe) und — sobald der Event-Kind existiert (B7-Outbox) und nur dann —
zählt er `tool_published`-Events mit (`_published_events`, `:225`, defensiv importiert). Die
**menschenlesbare Teileliste** bleibt Operator-Sicht; `discover()` trägt nur den Digest, damit
eine Suchantwort einen ausgeschlossenen Tool-Namen nicht über die Revision nennt (dieser Leak
war ein echter Befund des ersten Testlaufs und ist behoben).

## 3. Eignung vor Ranking — zwei getrennte Gates

Reihenfolge in `discover()` (`discovery.py:942-955`): **erst filtern, dann ranken**. Gefilterte
Einträge erscheinen nur als Zähler in `blocked` — kein Name, auch nicht in Fehlermeldungen.

| Stufe | Funktion | Frage | Verhalten |
|---|---|---|---|
| 1. Scope/Projektzugriff | `scope_gate` (`:489`) | darf dieser Projektkontext die Fähigkeit überhaupt sehen? | `global` immer; `project` nur im eigenen `project_id`; `legacy` (scope-los) nur im expliziten `include_legacy`-Blick |
| 2. Autorisierung | `authorization_gate` (`:504`) | darf der *Aufrufer*, was die Fähigkeit braucht? | fehlende `requires:secret://…`-Handle oder Rechte ⇒ raus; nicht deklarierte Rechte (`permissions=None`) = **UNBEKANNT**, kein stilles Verbot |
| 3. Schema-/Plattform-Kompatibilität + Budget | `compatibility_filter` (`:525`) | passt sie hierher? | deklarierte Plattform ≠ Host ⇒ raus; `offline`-Constraint vs. `network:`-Vorbedingung ⇒ raus; erschöpftes Budget ⇒ raus |

Danach:

- **Relevanz-Gate** (`:800-812`): Evidenz (Validierung/Health) darf ranken, aber niemals eine
  Fähigkeit *empfehlen*, die nicht passt. Nötig ist ein Treffer in einem kanonischen Vertragsfeld
  (Name, Aufgaben, Synonyme, DE-/EN-Formulierung, Wirkung) **oder** zwei verschiedene Treffer —
  ein einzelner Treffer in einem Strukturfeld (Eingabetyp, Beispiel) genügt nicht. Ohne dieses
  Gate empfahl der erste Lauf `web_search` für „Wandle diese DOCX-Datei …" (Score 0,999 allein
  aus „validiert").
- **Quarantäne** (`:570-600`): `QUARANTINED`/`RETIRED` ⇒ Zustand `locked`, `executable=false`,
  nie eine Empfehlung. Auffindbar bleibt sie nur, wenn der Bedarf die Fähigkeit **wörtlich
  nennt** (`include_locked` als explizite Audit-Sicht) — genau das verlangt `q01`
  („bleibt findbar MIT Zustand, aber keine ausführbare Empfehlung").
- Ein Eintrag ohne jede Relevanz verschwindet vollständig; ein Bedarf ohne Treffer endet als
  `error.code="no_match"` mit dem Bedarfstext, nie mit fremden Namen.

Beweise: `test_foreign_project_tool_leaves_no_trace_anywhere`,
`test_a_foreign_project_tool_is_absent_from_a_real_runtime_answer` (dieselbe Prüfung gegen eine
**echte** Runtime — sie fing den Revisions-Leak), `test_authorization_is_a_separate_gate_from_scope`,
`test_undeclared_permissions_are_unknown_not_denied`, `test_platform_and_budget_mismatches_are_hard_filters`,
`test_evidence_alone_does_not_recommend_an_irrelevant_tool`.

## 4. Zustände und `ensure_ready` (Docker-Schnittstelle B6)

Zustandsauflösung in `state_of()` (`discovery.py:570`), in dieser Reihenfolge:

1. Health-Ledger `QUARANTINED`/`RETIRED` → `locked`
2. Health-Ledger `DEGRADED`/`BROKEN` → `temporarily_down` (mit Ledger-Grund)
3. erforderlicher Dienst fehlt → `missing_prerequisite`; **gestoppt** → `startable`
4. keine Ausführungsevidenz (weder bestandene Validierung noch beobachtete Gesundheit) →
   `build_candidate`
5. sonst → `ready`

| Zustand | Bedeutung | Was der Aufrufer darf |
|---|---|---|
| `ready` | validiert bzw. beobachtet gesund | ausführen |
| `startable` | geprüft, Dienst gestoppt | `ensure_ready` rufen (B6), **genau ein** Start |
| `temporarily_down` | Ledger: degradiert/defekt | strukturierten Fehler berichten, Alternative/Blocker nennen |
| `missing_prerequisite` | Dienst/Voraussetzung fehlt auf dem Host | nicht starten, Voraussetzung melden |
| `locked` | Quarantäne/Ruhestand | nie automatisch auswählen; Retest nötig |
| `build_candidate` | keine Ausführungsevidenz | **nur** als getrennter Bau-Kandidat, nie als Empfehlung |

Wichtig für das Verhalten „gestopptes geprüftes Tool bleibt auffindbar": ein `CANDIDATE`-Zustand
im Ledger (nie beobachtet) ist **kein** Fehlerzustand — er führt in den Bau-Kandidaten-Zweig, nicht
nach `temporarily_down` (der erste Lauf hatte das verwechselt: jedes nie beobachtete Tool hieß
„vorübergehend ausgefallen").

`ensure_ready(name, ...)` (`discovery.py:1188`) ist die **dokumentierte Schnittstelle** für den
Docker-Block B6 (DockerRuntimeManager): sie meldet heute ehrlich
`{"started": false, "startable": true, "reason": "… (B6)", "hook": "ensure_ready"}` statt einen
Start zu behaupten. Sie ist bewusst kein zweiter Ausführungspfad — der Aufruf bleibt der Wrapper
aus `liveinsert`/`mcpserver`.

Beweise: `test_unvalidated_capability_is_only_a_build_candidate`,
`test_a_stopped_service_makes_the_capability_startable`,
`test_a_missing_service_is_a_missing_prerequisite`,
`test_degraded_tool_reports_a_structured_error_with_context`,
`test_quarantined_tool_stays_findable_with_its_state`,
`test_ensure_ready_is_the_documented_b6_interface`.

## 5. Ranking — Evidenz, UNBEKANNT, Determinismus

Gewichte (`RANK_WEIGHTS`, `discovery.py:61`): `task_fit` 1,0 · `criteria_coverage` 0,6 ·
`validation` 0,5 · `health` 0,5 · `input_readiness` 0,4 · `reliability` 0,3 · `cost` 0,2
(Einsatzaufwand inkl. `startup_cost_ms`, falls deklariert).

- **Fehlende Messwerte sind UNBEKANNT.** Eine Komponente ohne Messung fällt aus der Summe, ihr
  Gewicht wird weggenommen und `unknown[]` benennt sie. „Nicht gemessen" liest sich damit nie als
  „gemessen gut" (`test_missing_measurements_are_unknown_not_good`).
- **Nachweise statt Selbstdarstellung**: `validation` zählt nur mit Ausführungsevidenz (bestandene
  Validierung **oder** beobachtete Gesundheit im Ledger); `health` kommt aus dem Ledger,
  `reliability` aus beobachteten Trigger-/Verifikationszeilen, `cost` nur aus deklarierten
  Millisekunden (`test_observed_health_is_used_as_evidence`).
- **Projekt-Tools bei Gleichstand bevorzugt** — als Tie-Break, nicht als Bonus
  (`test_a_project_tool_wins_the_tie`).
- **Determinismus**: Sortierschlüssel `(-score, scope_rank, name)`, Score auf drei Nachkommastellen
  gerundet; zwei Läufe liefern dieselbe Reihenfolge und dieselben Scores
  (`test_ranking_is_deterministic`).
- **Semantische Ähnlichkeit ist keine Erfolgswahrscheinlichkeit**: der optionale Kanal trägt
  höchstens einen kleinen Relevanzzuschlag (+0,05·cos) und wird als Modus ausgewiesen; eine
  Erfolgswahrscheinlichkeit behauptet das System nirgends.

## 6. Auslöser und Cache im laufenden Agenten (Plugin)

Ort: `integrations/hermes/godsv_plugin/__init__.py` — **additiv**; die bestehenden
Guard-Pfade (shadow/whisper-Frühausgänge, `pre_verify`, Pause) bleiben unangetastet.

Auslöser (`DiscoveryState.triggers`):

| Auslöser | Quelle |
|---|---|
| `new_need` / `criteria_change` | Beginn eines Nutzerauftrags bzw. wesentliche Kriterienänderung (normalisierter Bedarf vs. letzter Bedarf der Session) |
| `capability_gap:<marker>` | strukturierter Fähigkeitsmangel aus `post_tool_call` (`GAP_MARKERS`: unknown tool / tool not found / not available for routing / no such tool / capability_gap) |
| `tool_result_unfit:<marker>` | das Tool lief, passte aber nicht (`schema mismatch`, `wrong output`, `unsupported format`) |
| `registry_revision` | neue/geänderte Fähigkeit (Manifeste/`tools.db`; `tool_published`-Events zählen mit, sobald B7 den Kind registriert) |
| `project_change` | Projekt-/Workspace-Wechsel |

**Kein Vollsuchlauf pro Token:** `DISCOVERY_MIN_INTERVAL_S=3.0` drosselt, `DISCOVERY_MAX_INJECTIONS=8`
pro Session begrenzt die Zahl der Einschübe, und ein identisches, bereits injiziertes Kandidatenset
wird nicht wiederholt (`repeats_suppressed`).

Cache (`discovery.DiscoveryCache`, eine Instanz je Session, `plugin:2144`): Schlüssel =
normalisierter Bedarf + Projekt + Registry-Revision + deklarierte Berechtigungen
(`GODSV_DISCOVERY_PERMISSIONS`; nicht deklariert heißt „undeclared" — ehrlich, statt Rechte zu
erfinden). Invalidierung bei Revisions-/Projektwechsel; und weil die Revision im **Schlüssel**
steht, kann ein altes negatives Ergebnis eine neue Lage nicht verdecken
(`test_a_moved_registry_revision_is_a_trigger_and_invalidates`).

Injektion ausschließlich über den sanktionierten Kanal: `pre_llm_call` gibt `{"context": …}` zurück
(`plugin:2489`), der Host hängt das an die Nutzernachricht an — keine Systemprompt-Manipulation,
kein Steering. Inhalt: höchstens drei Kandidaten mit Zustand, Einsatzgrund und einer Grenze
(Harness-Kontextkosten: Mittel 65 Tokens, Max 149 — §8).

Ungedeckter Bedarf wird **einmal je Bedarf** als `capability_requested` gemeldet (`plugin:2278`,
vorhandener Event-Kind; `events.py` bleibt B7-Eigentum) — die Übergabe an den Builder passiert
dort, nicht hier. Kill-Switch `GODSV_DISCOVERY=0`; der komplette Pfad läuft in `try/except` +
`_trace` (`test_a_broken_discovery_never_breaks_the_turn`).

## 7. Host-Brücke (§1.3): `tool_search`/`tool_describe` mit den neuen Metadaten

B5 hat live bewiesen, dass Plugin-Tools das Modell über die Tool-Search-Brücke erreichen
(`tool_search` → `tool_describe` → `tool_call`, Session `20260914_190421_ec3a6d`). §4c verlangt,
dass die **neuen Metadaten** dort auffindbar sind — ohne den Host zu patchen.

- **Register-Schemas füttern**: vor `ctx.register_tool` wird die Beschreibung aus dem
  Vertragsindex angereichert (`_enrich_schema_with_metadata`, `plugin:2303`; Aufruf in
  `_apply_live_surface`, `plugin:1973`): `does:` / `tasks:` / `synonyms:` / `de:` / `en:` /
  `inputs:` / `outputs:` / `preconditions:` / `not for:`. Fail-safe: schlägt die Anreicherung
  fehl, wird das unveränderte Schema registriert (Registrierung darf nie an Metadaten scheitern).
- **Harness-Beweis mit dem ECHTEN Matcher** (`test_the_host_tool_search_finds_the_capability_by_metadata`):
  der Test baut die Tool-Defs in genau der Form, die `model_tools` aus der Plugin-Registrierung
  ableitet (`{"type": "function", "function": schema}`), und lässt den
  `tools.tool_search_catalog` des Checkouts in **dessen** venv BM25 suchen. Beide Formulierungen
  („convert a pdf document to markdown", „Dokument umwandeln") finden `pandoc_convert`.
  Kein Projekt-Zusatzpaket nötig: der Matcher läuft im Host-venv (snowballstemmer), nicht im
  Projekt-venv.
- **Live-Probe: nicht durchgeführt.** Der Auftrag erlaubt sie nur ohne App-Neustart; die
  Registrierung wirkt ohnehin erst im nächsten regulären App-Start (Plugin-Bundling Regel), also
  blieb es beim Harness-Beweis. Der Host ist unverändert (kein Patch an `~/.hermes/hermes-agent`).

## 8. Eval-Runner auf dem eingefrorenen Abnahme-Testset

`tools/gv_discovery_eval.py` lädt `tests/fixtures/discovery_cases.json` (FROZEN, v1.0.0,
sha256 `6084cc5ed472d470c00f2d4f27b1c459482b9e9287f85ea75a2418a2866a6668`, unverändert) und
fährt jeden Fall gegen den echten Suchvertrag. Berichtet werden **Zähler/Nenner je Metrik**,
kritische Fälle (`s*`, `q*`) separat und blockierte Fälle ausgewiesen — nie eine pauschale
Prozentzahl.

Zwei Modi, strikt getrennt:

- `--harness` (Default): temporäre Runtime, deren Inventar den noch fehlenden Teil des
  Ziel-Surfaces vertritt (SearXNG-/Pandoc-/research-Tools, §6; Docker-Etappe B6/B10). Das
  Inventar ist aus den **Referenz-Tool-Verträgen** geschrieben (Namen, Formate, Dienste, beide
  Sprachen) und wird als approved Manifest + Registry-Spec angelegt — dieselbe Form, die der
  gated Builder schreibt. Szenarien (Quarantäne, Degradierung, gestoppter Dienst) mutieren je
  eine **eigene** Runtime (Ledger-Zustand darf nicht in den nächsten Fall bluten — der erste
  Lauf tat genau das).
- `--runtime <dir>` (Live-Modus): die echte Runtime, read-only. Fehlt eine Zielfähigkeit im
  Register, ist der Fall **blockiert** (mit Grund) — kein Dry-Run als Ersatz.

Metriken: Recall@3 über `allowed`-Glob (case-insensitive, symbolische `fixture:*`-Muster an die
Fixture-Projekte gebunden), Falsch-Eignung bei `no_match`-Fällen, Scope-Leaks (`excluded`-Treffer),
Suchlatenz, Kontexttokens des Kandidatensets.

Eigener Harness-Lauf (`.venv/bin/python tools/gv_discovery_eval.py`):

| Metrik | Wert |
|---|---|
| Recall@3 | **23/23** |
| Falsch-Eignung (`no_match`) | **0/5** |
| Scope-Leaks | **0/5** |
| Suchlatenz | Mittel 32–97 ms, p95 35–124 ms, Max 89–128 ms (36 Fälle; die Streuung ist Rechnerlast der parallelen Blöcke, nicht Fallabhängigkeit) |
| Kontexttokens des Kandidatensets | Mittel **65,1**, Max **149** |
| Kritische Fälle (`s*`/`q*`) | **5/6 bestanden, 1 blockiert** (`q03`) |
| Blockiert (getrennt) | **5/36** |

Blockierte Fälle mit Grund (unverändert ausgewiesen, nie als „bestanden" gezählt):

- `q03` — Widerruf zwischen Discovery und Dispatch (Dispatch-Wächter, keine Discovery-Metrik)
- `a01`, `a02` — Container-**Start** genau einmal / parallele Starts (Docker B6); das
  Discovery-Teil („bleibt auffindbar", Zustand `startable`) ist bewertet und grün
- `a03` — Readiness-Fehler aktualisiert Health (Docker B6)
- `g01` — Builder-Budgetpfad (B7)

Ein Lauf, der die fehlenden Teile **nicht** weglügt: im Live-Modus gegen eine leere Runtime
meldet der Runner das gesamte Ziel-Surface als blockiert
(`test_the_live_mode_reports_the_missing_surface_instead_of_faking_it`).

**Live-Modus** (eigene Messung, read-only: `.venv/bin/python tools/gv_discovery_eval.py
--runtime ~/.hermes/godsv`):

| Metrik | Wert |
|---|---|
| Recall@3 | **0/0** — kein bewertbarer Recall-Fall: die Zielfähigkeiten sind im echten Register nicht vorhanden |
| Falsch-Eignung (`no_match`) | **0/5** |
| Scope-Leaks | **0/1** |
| Suchlatenz | Mittel **625 ms** (echtes Register plus Wrapper-/Ledger-Last; der Harness-Wert ist die Vergleichszahl) |
| Kontexttokens des Kandidatensets | Mittel **16** |
| Kritische Fälle (`s*`/`q*`) | **0 bestanden / 6 blockiert** (keiner als Fehlschlag gezählt) |
| Blockiert (getrennt) | **28/36** |

Gründe der Blockade (live): 15× „target capability not registered in this runtime"
(`web_search`, `research_topic`, Pandoc fehlen live — B6/B10), 13× „matching capabilities are
not reachable in this runtime's project context" (die Projekt-Fixtures des eingefrorenen Sets
existieren live nicht; die Bestands-Tools sind legacy- bzw. fremdprojekt-gescoped und werden
deshalb korrekt gar nicht erst gezeigt). Der Live-Lauf behauptet damit **nichts**, was ohne die
fehlenden Teile nicht messbar ist: der Harness-Lauf vermisst den Suchvertrag, der Live-Lauf
vermisst die Registrierungsdecke der heutigen Runtime.

**Der Runner hat in dieser Runde echte Defekte gefunden** (alle vor dem Commit behoben):
Zustandsverwechslung nie-beobachteter Tools (jedes galt als „vorübergehend ausgefallen"),
Ledger-Bleed zwischen den Szenarien, Scope-Leak über die Revisions-Teile, Falschtreffer über
`non_use_cases`, Empfehlung ohne Aufgabentreffer (Evidenz allein) und die Zählung blockierter
kritischer Fälle als Fehler.

## 9. Testnachweis, Rot-Beweise, Commit-Stand

Eigene Messungen, Repo `/home/willy/projects/gods-voice`, Branch `godsv-supervisor-hardening`,
`.venv/bin/python -m pytest <dateien> -p no:randomly` (kein zweites `-q` — `addopts` setzt es;
die Zählzeile ist zitiert, nicht gezählt):

| Kommando | Ergebnis |
|---|---|
| `pytest tests/test_discovery.py -p no:randomly` | **36 passed** (RC=0) |
| `pytest tests/test_capability_mining.py -p no:randomly` | **15 passed** (RC=0) |
| `pytest tests/test_discovery.py tests/test_capability_mining.py -p no:randomly` | **51 passed** (RC=0) |
| `pytest tests/test_live_surface_wiring.py tests/test_capability_learning.py tests/test_capability_mining.py -p no:randomly` | **67 passed** (RC=0) — Bestand unverändert grün nach dem Plugin-Eingriff (Zahl wächst mit den Schwester-Blöcken) |
| `pytest tests/test_plugin_release.py tests/test_plugin_robustness.py -p no:randomly` | **16 passed** (RC=0) — Guard-Vertrag unberührt |
| `ruff check src/godsv/discovery.py src/godsv/capability_mining.py tools/gv_discovery_eval.py tests/test_discovery.py tests/test_capability_mining.py integrations/hermes/godsv_plugin/__init__.py` | `All checks passed!` |

**Rot-Beweise** (Mechanik wie im Projekt üblich: die eine load-bearing Zeile entfernen, den echten
Fehlschlag beobachten, restaurieren, grün nachweisen — **nie** via `git stash`; Backup unter
`outputs/gap4c/rotproof/`, sha256-identisch restauriert):

| deaktiviert | beobachtet | restauriert |
|---|---|---|
| Scope-Gate-Aufruf in `discover()` (`if not scope_gate(...)` → `if False:`) | `2 failed, 6 passed` (RC=1) — `test_foreign_project_tool_leaves_no_trace_anywhere`, `test_a_foreign_project_tool_is_absent_from_a_real_runtime_answer` | 36 passed |
| Registry-Revision im Cache-Schlüssel (`cache_key`) | `2 failed, 2 passed` (RC=1) — `test_negative_cache_is_invalidated_when_the_registry_moves`, `test_the_cache_key_carries_need_project_revision_and_permissions` | 36 passed |

**Commits** (Branch `godsv-supervisor-hardening`):

| SHA | Inhalt |
|---|---|
| `279273d` | `feat(gap4c)`: Discovery-Kern — Vertrags-Metadaten, Eignungs-Gates, Ranking, Cache, Eval-Runner |
| `54fc5b7` | `feat(gap4c)`: Plugin-Auslöser, Cache, Kandidatenset am Chat-Boundary + Host-Brücke gefüttert |
| `21b0da2` | `test(gap4c)`: Discovery-Vertrag (36 Tests) + Metadaten-Tests in `test_capability_mining.py` |
| `9ffef04` | `docs(gap4c)`: dieses Dokument |
| `10c11c4` | `fix(gap4c)`: Antwort trägt nur den Revisions-Digest (Namens-Leak) + blockierte kritische Fälle zählen nicht als Fehler |
| `d9d8b41` | `docs(gap4c)`: Messwerte aktualisiert |
| `c1101a0` | `fix(gap4c)`: Live-Modus blockiert Fixture-Projekte statt sie als Fehlschlag zu werten (`gv_discovery_eval`); Live-Messung in §8 |

`9ffef04` und `10c11c4` sind per `git pull --rebase --autostash` nachgezogen und nach
`origin/godsv-supervisor-hardening` gepusht (die frischen Zahlen oben wurden auf `10c11c4`
gemessen).

## 10. Restgrenzen (bewusst offen)

1. **Container-Start fehlt (B6).** `startable`, `missing_prerequisite` und `ensure_ready` sind
   Zustand + Schnittstelle; der echte Start (DockerRuntimeManager, genau einmal, zwei parallele
   Aufrufe) kommt mit der Docker-Etappe. Die Fälle `a01`/`a02`/`a03` sind deshalb als blockiert
   ausgewiesen, ihr Discovery-Teil ist bewertet.
2. **Kein Live-Lauf dieser Runde.** Alle Zahlen sind Harness (echte Runtime-Verzeichnisse, echte
   Ledger, echte Plugin-Hooks mit FakeCtx, echter Host-Matcher im Host-venv). Der Live-Roundtrip
   (Modell → `tool_search` → `tool_call` mit den neuen Metadaten) steht wie bei B5 aus: er
   braucht einen App-Start mit gebautem Plugin-Bundle und wurde hier nicht gefahren.
3. **Berechtigungsmodell ist deklarativ.** Das Plugin kennt heute keine echten Grants; der
   Cache-Schlüssel trägt die per `GODSV_DISCOVERY_PERMISSIONS` deklarierte Menge oder „undeclared".
   Sobald ein Grant-Modell (Treasury/Execution-Boundary) greift, gehört es hier eingespeist —
   die Gate-Funktion nimmt es bereits als Parameter.
4. **Semantischer Kanal ist lexikalisch.** `HashingEmbeddings` sind Zeichen-Trigramme; ein echter
   Provider kann über `embedder=` eingesetzt werden, ist aber nicht konfiguriert (keine neue
   verpflichtende Abhängigkeit).
5. **Stemmer ist bewusst klein.** Deutsche Komposita und unregelmäßige Formen bleiben Lücken
   (z. B. „Bildschirmfoto" ↔ „screenshot" finden sich nur über Synonyme des Vertrags).
6. **Suchkosten** wachsen mit der Registry: der Harness-Index liest pro Suchlauf alle Manifeste
   (Mittel ≈ 32–63 ms bei 8 Tools). Der Plugin-Pfad fängt das über Cache + Trigger ab; ein
   vorgehaltener Index (mit Revision als Schlüssel) wäre die nächste Stufe, falls die Registry
   dreistellig wächst.
7. **`tool_published`-Konsum ist vorbereitet, aber ungetestet gegen B7.** Der Kind existiert
   noch nicht in `events.KINDS`; der Runner zählt ihn „sobald vorhanden" und protokolliert das.
8. **Blockierte Fälle bleiben blockiert**, bis B6/B7 liefern — der Runner weist sie bei jedem
   Lauf neu aus, statt sie zu überspringen.
