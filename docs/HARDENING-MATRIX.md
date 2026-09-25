# Supervisor Hardening — Anforderungs- & Reparatur-Matrix

Quelle: Unabhängiges Review vom 12.09.2026 (`projektbewertung-gods-voice.md`, Gegenproben
`review_probes.py` / `review-probes.json`, Manifest `review-manifest.json`).
Geprüfter Review-Stand: `af44391` (godsv-chat-ui).

**Reproduktion gegen aktuellen Stand** (`f1526fe`, 12.09. Abend, Befehl
`PYTHONPATH=integrations/hermes .venv/bin/python review_probes.py`): **alle 7 Befunde bestehen
identisch weiter** — Beleg: `docs/audit/probes-2026-09-12.json` im Repo. Die Chat-UI-Runden v4/v5
haben keinen der Kernbefunde berührt.

Legende Status: **offen** · **reproduziert** · **repariert** (Code geändert) · **integriert**
(alle Pfade nutzen die Reparatur) · **automatisiert getestet** (Regressionstest, der vorher
scheitert) · **live verifiziert** (echte App/Produktlauf).

## A. Kritische Findings (P1)

| # | Befund (Review P1) | Quelle | Reproduziert 12.09. | Reparatur | Regressionstest | Status |
|---|---|---|---|---|---|---|
| A1 | `echo pytest` (exit 0) zertifiziert fehlende Anforderung → `VERIFIED_COMPLETE` | `completion.py:636,751`; `guard.py:82` | ja (`echo_pytest_certifies_missing_feature`) | `RunEvidence(source,status,check_id)` + `gate_dod` zertifiziert nur Runner-Evidence; Tool-Events sind Kontext | `test_echo_pytest_never_certifies_a_missing_feature` (rot auf Baseline) | **repariert, integriert, getestet** |
| A2 | Ergebnisse werden nach Toolname, nicht `tool_call_id` zugeordnet; `pwd` trass über `pytest`-Fail hinweg → `VERIFIED_COMPLETE` | `completion.py` (`run_evidence_from_events`) | ja (`tool_name_collapses_distinct_calls`) | Pairing über `tool_call_id` (Fallback: nächster ungematchter Call, jeder genau einmal); Fail bleibt blockierend bis späterer passender grüner Lauf | `test_failed_pytest_stays_blocking_despite_later_pwd`, `test_parallel_same_name_calls_pair_by_tool_call_id`, `test_newer_green_run_supersedes_older_failure` | **repariert, integriert, getestet** |
| A3 | `error` ohne Exit-Code ⇒ `ok=True` | dito | ja (`error_without_exit_code`) | `_result_status()`: error ⇒ failed, fehlender Status ⇒ unknown, nie implizit passed | `test_error_without_exit_code_is_not_a_pass` | **repariert, getestet** |
| A4 | Chat/Panel schreiben `completion_state.json`, Hook liest `pact.yaml` ⇒ Ziel/DoD entkoppelt; „The task is done“ + `pwd` ⇒ `VERIFIED_COMPLETE`, Ziel wird zu `session goal`, DoD zu `[]` | Plugin `__init__.py:478,628`; API `:1235,:1333` | ja (`chat_goal_disconnected_from_hook`) | `_contract()`: Hook liest den **session-eigenen Contract** (Store-first); `pact.yaml` nur Import-Quelle; „session goal“-Ersatz entfernt (Claim ohne Nachweis → INCOMPLETE); `set_goal` bumpt Revision + DoD-Items → `open` | `test_chat_goal_reaches_the_hook`, `test_goal_edit_bumps_revision_and_reopens_items`, Probe: Goal+DoD bleiben erhalten, Verdikt INCOMPLETE | **repariert, integriert, getestet** |
| A5 | Alle Sessions teilen den Completion-Zustand; Session B liest `VERIFIED_COMPLETE` von A; Chat-Feed ohne Session-Filter | Plugin `:1129,:638`; `completion.py:186`; API `:1257`; `gvFeedRows()` | ja (`second_session_shares_terminal_state`) | `CompletionStore(runtime, session_id)` → `<runtime>/completion/<session>.json|.jsonl`; Legacy-Record nur von der Session adoptiert, die er nennt; `_chat_event_rows(session_id)` + Feed-Default = jüngster Contract (explizit im Payload) | `test_two_sessions_never_share_a_contract`, `test_legacy_record_is_adopted_by_its_own_session_only`, `test_second_session_cannot_read_the_firsts_verdict`, `test_feed_only_shows_the_named_session` | **repariert, integriert, getestet** |
| A6 | `add_criteria()` speichert `verification=""` ⇒ Kriterium nie erfüllbar; Hook-Pfad kann es zugleich ignorieren | `completion.py:245,636` | ja (`chat_criterion_unverifiable`) | `plan_verification()` leitet den kanonischen Check ab (Tests→`pytest`, UI→`ui_e2e`, API→`api`, restart/persist→`persistence_restart`); `add_criteria` dedupliziert, vergibt ids fortlaufend, bumpt Revision; **leerer Plan bleibt explizit** und wird von `gate_dod` als „Prüfplan fehlt“ blockierend gemeldet | `test_add_criteria_derives_verification_plans`, `TestPlanVerification`; Probe: `Login works after restart` → `persistence_restart` | **repariert, integriert, getestet** |
| A7 | `git diff HEAD` übersieht untracked + schon committete Task-Änderungen; Testpflicht nur `src/godsv/*.py`; neues `src/broken.py` + `pwd` ⇒ `VERIFIED_COMPLETE` | `completion.py:371,446` | ja (`untracked_python_not_test_gated`) | `RepoFacts.collect(base)`: untracked via `ls-files --others` (inkl. neuer Verzeichnisse), Diff gegen Task-Basis, Testpflicht nach Codesprachen | `test_untracked_new_module_is_test_gated` | **repariert, getestet** (Task-Basis-Bindung an den Contract folgt in Etappe 2) |

## B. Weitere wesentliche Risiken (Review §5)

| # | Befund | Quelle | Status |
|---|---|---|---|
| B1 | Tool-Build-Events `tool_build_<stage>` fehlen in `KINDS` ⇒ `EventStore.append` wirft, Builder schluckt; Queue-Zeile ≠ Chat-Ereignis | `toolbuilder.py:139`; `events.py:25,84,192` | **repariert, getestet** — alle 8 STAGES + `tool_build_failed` in `KINDS` **und** `CHAT_KINDS` (Konsistenz per Test erzwungen), Mirror-Fehler werden geloggt statt geschluckt, `ToolBuildRunner` reicht die Session durch (Build-Stufen erscheinen im richtigen Chat) |
| B2 | Sandbox-Fallback in `gates.py:71` (normaler Subprozess ohne bwrap) + `/` lesbar gebunden; `Broker.run_with_secret` verwirft `sandboxed`-Flag | `gates.py:71` | **repariert, getestet** — fail-closed ohne bwrap (kein unsandboxed Lauf), minimale ro-Binds (/usr,/etc,/opt + Symlinks; tmpfs /home,/root,/tmp,/var; nur Interpreter-Prefixe lesbar), Probe cached nur positiv (kein Blockade-Freeze), Broker verweigert Secret-Injektion ohne Sandbox |
| B3 | Origin-Prüfung per Substring (`localhost` irgendwo) statt exakter Allowlist | Panel-API | **repariert, getestet** — `origin_allowed()` mit exaktem Hostname-Vergleich (urlsplit); `https://evil.com/x?localhost` wird abgelehnt |
| B4 | `validate_testing_tool()` nur aus Tests aufgerufen; kein Pflicht-Gate im Promotion-Pfad | toolbuilder/gates | **repariert, getestet** — `promote()` markiert `testing_tool`; Builder-Gate: ohne Validierungsfälle keine Registrierung (`needs_validation`), mit Fällen Pflicht-`validate_testing_tool` („immer PASS" ⇒ Promotion refused) |
| B5 | BLOCKED aus Textmarkern; Ausnahmen fallen auf Legacy-Guard zurück; Prüferausfall darf strengen Modus nicht schwächen | Plugin `__init__.py` | **repariert, getestet** — Marker nur noch explizite Statuszeilen (`^BLOCKED_USER` etc., case-insensitiv); Prüferausfall wird als `completion_checker_failed` audit-loggt + Footer „checker FAILED — NOT verified" (fail-closed, kein stiller Pass) |

## C. Verpflichtende Abnahme (§6 des Auftrags)

| Fall | Erwartung | Status |
|---|---|---|
| `echo pytest` ohne Funktionstest | Kein gültiger Testnachweis | offen (A1) |
| Fehlgeschlagenes pytest, danach erfolgreiches pwd | Kein falscher Abschluss; korrekte Zuordnung | offen (A2) |
| Tool-Fehler ohne Exit-Code | failed/unknown | offen (A3) |
| Zwei gleichnamige parallele Tool-Aufrufe | Eindeutige Zuordnung | offen (A2) |
| Nachweis vor letzter relevanter Änderung | Für aktuellen Stand ungültig | repariert (Task-Basis + Revision; Live-Nachweis §6.2 in Etappe 4) |
| Goal aus Chat bearbeiten | Main Agent + Voice nutzen neue Revision | **repariert, getestet** (`set_goal` + `_contract`, Revision-Invalidierung) |
| `@goal` hinzufügen | Kriterium bleibt erhalten + erfüllbar mit passender Prüfung | **repariert, getestet** (Prüfplan-Ableitung + Revision) |
| Zwei parallele Sessions | Getrennte Ziele/Events/Abschlüsse | **repariert, getestet** (Session-Stores + Feed-Filter) |
| Untracked + committete Task-Dateien | Vollständig berücksichtigt | offen (A7) |
| Prüferausfall/Budgetende | Kein `VERIFIED_COMPLETE` | repariert, getestet (Checker-Ausfall = Footer + Event; Budgetpfad im §6.2-Lauf) |
| Tool-Build-Stufen | Bis in den richtigen Chat sichtbar | **repariert, getestet** (Kinds + Session-Durchreichung) |
| Testing-Tool meldet immer PASS | Promotion abgelehnt | **repariert, getestet** |
| Sandbox nicht verfügbar | Ausführung blockiert | **repariert, getestet** (fail-closed) |
| Credential-Grant entzogen | Weitere Nutzung verweigert | Grant-Prüfung im Broker vorhanden; Live-Nachweis in Etappe 4 |
| App-Neustart | Ziele + Modellkonfiguration bleiben | Datei-Persistenz vorhanden; Live-Nachweis in Etappe 4 |
| **Echter Produktlauf (§6.2)** | 8 Schritte inkl. Restart-Persistenz, Auto-Fortsetzung, zweite Session | offen |

## D. Arbeitsetappen

1. **Etappe 0 (dieser Stand):** Baseline `hardening-baseline` (f1526fe-Doku `85ee575`), Branch
   `godsv-supervisor-hardening`, Reproduktion dokumentiert, Matrix steht.
2. **Etappe 1 — Evidence-Kette (A1–A3, A7):** `RunEvidence(status, check_id, tool_call_id,
   revision, source)`; `run_evidence_from_events` paart über `tool_call_id`, Strenge Regel:
   ohne expliziten Exit-Code kein `passed`; DoD-Spezifika nur über Runner-/registrierte
   Check-Evidence; RepoFacts gegen Task-Basis inkl. untracked/committed.
3. **Etappe 2 — Vertrag & Sessions (A4–A6): ERLEDIGT.** Session-scoped `CompletionStore`
   (`<runtime>/completion/<session>.json`), Hook liest denselben Contract (`_contract`,
   Store-first), `pact.yaml` nur Import-Quelle, Legacy-Adoption nur durch die eigene Session,
   Revisionen invalidieren alte Nachweise, `plan_verification` + „Prüfplan fehlt“-Kennzeichnung,
   Feed filtert per Session (Default = jüngster Contract, explizit im Payload).
   Tests: `tests/test_contract_sessions.py` (10, rot vor der Reparatur), Proben 7/7.
4. **Etappe 3 — Loop & Grenzen (B1–B5): ERLEDIGT.** Build-Events bis in den Chat (Kinds +
   Session), Sandbox fail-closed + minimale Binds + Broker-Verweigerung ohne Sandbox,
   exakte Origin-Allowlist, Testing-Tool-Pflichtgate (validate statt skip), explizite
   Statusmarker + ehrlicher Prüferausfall. Tests: `tests/test_gate_hardening.py` (14).
5. **Etappe 4 — Produktlauf (§6.2)** + Settings/Treasury-Flows + UI-Rest (Leerzustand klein,
   Composer bereits v5).
6. **Etappe 5 — Abschlussbericht** mit Matrix-Update, Nachweisen, Rollback-Hinweisen.

Jede Etappe: eigener Commit + ggf. visueller Checkpoint; bestehende Tests bleiben grün,
Regressionstests scheitern nachweislich vor der Reparatur.
