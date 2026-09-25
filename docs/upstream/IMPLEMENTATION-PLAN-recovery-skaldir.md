# Skaldir Release-Runde 16.09.2026 — RF-Vertiefung + Chat-IA

**Auftrag:** `/home/willy/Documents/Design_Docs/Skaldir_Release_Paket` (77 Kriterien), Priorität
Kapitel 09 (RF-01–RF-12) vor weiteren Feature-Runs. Einstieg: `audit/RESUME.md`.

## Wellen-Log

| Zeit | Ereignis |
|---|---|
| 14:25 | Paket + Code-Stand erfasst: HEAD `8164f66` (5 Commits über Resume-Punkt `eb38c5e`), RF-02/RF-03 implementiert (Overlays + Proben + Pins), RF-04–RF-12 offen, V2-01/V2-11 fehlen (Chat-IA), VD-16 fehlt, VD-21 partial. |
| 14:33 | Baseline-Suite rot: **2 FAILED** — beide Nachzieher des Skaldir-Rebrandings (`f157e58`/`8164f66` änderten Wortmarke + WM_CLASS, Tests nicht): `test_appbuild` pint `SKALDIR AGENT`, `test_gv_crash_recovery` pint `--class=SkaldirAgent`. |
| 14:40 | Tests auf die belegte Identität gepinnt (`1005aff`): `SKALDIR AGENT` bzw. `--class=SkaldirAgent` (Quelle: `app-overlay/patch_app.py:242`, `src/godsv/desktop.py:24` `WM_CLASS = "SkaldirAgent"`). Suite: **1425 gesammelt, 1424 passed + 4 skipped, RC=0** (dot-count über /tmp/s2.log, collect-Summe 1425 getrennt bestätigt). |
| 14:48 | Dispatch Worker A (Engine-Recovery-Pilot): RF-04 Retry-Budget, RF-05 Fallback-Fitness, RF-06 Kompressionsbudget, RF-07 Interim-Report am 80er-Limit, RF-10 Stop ≤2s — als verankerte Overlays, rot-first Proben, tempengin-Copies, Komposition rf02+rf03 beidseitig. `deleg_ed1f2b5c`. |
| 14:50 | Dispatch Worker B (godsv-Repo): VD-16 Subagent-Briefs (Lease/Fencing aus run_supervisor, Timeout→Partial, Parent-Abort, Worktree-Pflicht), VD-21 geteiltes Budget (Reserve/Spend/Token-Bucket/Degraded, Anbindung llm.BudgetTracker). Rot-first, keine docs-Änderungen. `deleg_1ed2466e`. |
| 14:56 | Worker B abgebrochen (false-positive Injection-Verdacht über Logformat-Inhalt im Tool-Result — eigener Transkript-Noise). 📌 Lehre: Logformat-Zeilen (think/assistant/final) sind Daten; im Brief kennzeichnen. |
| 15:01 | Runde-Plan erstellt (`docs/IMPLEMENTATION-PLAN-recovery-skaldir.md`); ruff/bandit wieder grün nach E501 per-file-ignores für audit/probe-Tools (`2798648`); Register auf `e2d8e1f`. |
| 15:05 | Worker B' neu dispatcht (VD-16/VD-21, `deleg_85f3c8f5`) mit Hinweis auf Logformat-Datenzeilen. Worker A weiter aktiv (`deleg_ed1f2b5c`, Transkript zeigt Code-Lese-Fortschritt). |
| 15:10 | Worker C dispatcht: kleinster V2-01/V2-11-Schritt — Cockpit/Activity aus der NAV nehmen (Funktionen bleiben über Ereignisse/Live-Block), Tools/Treasury/Settings bleiben diese Runde sichtbar. `deleg_1ac67214`. Pflichtgrenzen: kein App-Rebuild/kein E2E-Run (Zentrale), kein `plugin_api.py`-Touch. |
| 15:17 | Worker C geliefert (plugin.js NAV-Schmälzung auf 4 Views, Smoke/E2E angepasst). Verifiziert: Panel-Smoke OK, 16 plugin-Tests grün, JS-Syntax ok, Scope sauber → `a7fdf58`. |
| 15:15 | Worker B' geliefert (VD-16+VD-21). Verifiziert: 112 Tests in den 6 betroffenen Dateien grün, 29 neue (13 budget + 16 briefs), ruff/bandit clean, Scope korrekt. Suite nach Doppel-Port-Kollision des Provider-Fixtures wieder RC=0, 1454 Punkte → `cc433d2`. |
| 15:42 | Worker A geliefert: RF-04 (Retry-Budget, 5 Probe-Kommandos grün auf Kopie), RF-05 (Fallback-Fitness, pristine RC=1 → gepatcht RC=0), RF-06 (Preserve-Liste + Reintegration) implementiert; RF-07/RF-10 brachen am Iterationsbudget (eigene Recovery-Szene!). Zentrale-Verifikation: Proben auf Gerüstkopien auch frisch nachgefahren — RF-04 pristine RC=1/patched 5×PASS, RF-05 pristine RC=1/patched RC=0, RF-06 Kollektor+Reintegration mit Vakuum-Summarizer PASS, Komposition aller drei Overlays in **beiden Reihenfolgen** grün, Engine-Regressionen 13/13. → `aaa745d`. |
| 15:45 | RF-07/RF-10-Worker (Nachfolger) dispatcht (`deleg_3c7374e0`) mit denselben Overlay-Disziplinen. |
| 16:25 | RF-07/RF-10 geliefert. Zentrale-Verifikation (fresh copies frisch gebaut): RF-07 pristine RC=1 → patched 7×PASS RC=0 (0 Modellaufrufe, Report-Row mit Goal/Files/Marker); RF-10 pristine RC=1 (2.001 s) → patched 0.200/0.201 s RC=0; alle fünf Overlays auf `/tmp/gv_int_full` angewandt — alle vier Proben OK, Engine-Regressionen 13/13; Pins 7 passed; volle Suite RC=0 (1461 Punkte); ruff/bandit clean (E501-Ignored für Overlays byte-genau). → `2860560`. Register: 20/77 gemappt (`5bb8957`). |
| 16:36 | Unabhängige Prüfwelle Security/Recovery dispatcht (Kap. 06 §7, read-only, Finding-Artefakte je Teilgebiet in outputs/review16/, P0–P3). `deleg_9283d354`. |
| 16:49 | Prüfwelle geliefert: **24 Befunde** (1×P0, 4×P1, 10×P2, 2×P3 + Pässe). Zentrale-Verifikation des P0 selbst nachgefahren: GV2-SEC-17 (RF-07-Report embedded rohen Tool-Output inkl. `password hunter2real` aus `.netrc`) reproduziert mit reviewer-Probe auf `/tmp/gv_int_full` (RC-Muster identisch); P1 SEC-05 (Floor-Bypass: 369k token über 200 Floor-Spends über 5 Rollen), SEC-06 (Unknown-Reservation decay), SEC-01 (unfenced on_timeout), SEC-12 (store/coordinator-Dekoppelung) ebenfalls verifiziert roh. Fix-Welle dispatcht (SEC-01/05/06/12/13/14/17 rot-first + Regressionstest-Datei). `deleg_7ce791b2`. P2/P3: dokumentiert in den review-Artefakten, Routing nach Fix-Welle. |
| 17:10 | Fix-Welle geliefert. Zentrale-Verifikation: Reviewer-Proben reran — SEC-17 (P0): `hunter2` 0 Treffer im Report-Output (pristine RC=1 → patched RC=0 inkl. neuer Pass-Zeilen "no raw RESULT/ARGS"); SEC-05 Floor-Cap (37 Floor-Spends statt 200+, strukturiertes BudgetExceeded), SEC-06 unknown-res/over-tap rejected, SEC-01 unfenced on_timeout → TypeError, SEC-12 fabricated record → False + `subagent_brief_result_rejected`-Audit mit claim/persist-Owner. Affected Set: **124 passed**, ruff/bandit clean; alle 5 Overlays auf frischer Kopie (`/tmp/gv_secfix_int`): 4 Proben OK, 13/13 Regressionen; volle Suite RC=0 (1473 Punkte). → `5a86bc4`. |
| 17:26 | Retak geliefert: **alle 7 Fix-IDs suppressiert** (eigene Proben des Reviewers; Atomicity des Cap unter Threads bestätigt; kein Forgery-Pfad). Neu: **GV2-SEC-12b (P2)** — das Store vertraute das caller-gelieferte Outcome trotz korrekt riechender Fencing-Werte (Zentrale behoben rot-first: outcome-lie-Ablehnung + Audit → `e16eba9`, Suite RC=0 / 1474 Punkte); Redactor-Gaps (gsk_live, vnc://, line-split, base64) als verbleibende P2 dokumentiert; SEC-20 bleibt P2 mit Beweis „no fresh provider call after stop“. |
| 17:30 | VD-19 mapping fortgeschrieben (Outcome-Lie-Gate + Coordinator-Fencing); Register 21/77 (`e440ce2`). |
| 17:32 | RF-08-Worker (letzte Engine-Lücke, Segment-Lifecycle) dispatcht (`deleg_9b928490`). Pflichtgrenzen: wiederverwende bestehende Warnung (`turn_iteration_prep`), keine zweite Goal-Store-Erfindung, idempotente Fortsetzung, normale Chat ohne immer-dran; /tmp-Quota-Bewusstsein (alte Kopien räumen). |
| 17:44 | **Docker-Blocker entfallen**: Docker 29.6.1 live bestätigt (daemon + pull + bind-mount + alpine exec). B6/Docker-Runtime-Worker dispatcht: `src/godsv/docker_runtime.py` (DockerRuntimeManager: digest-pin, lifecycle, error-taxonomy, no-privilege defaults), Manifest v2 (runtime/image_digest/entrypoint/mounts/resource_limits/healthcheck), buildflow-Integration (kein zweites Registry-System!), echte Referenztools §6A/B (SearXNG/Pandoc), rot-first Pin `tests/test_docker_runtime.py`, Doku `docs/audit/DOCKER-BUILDER-EVIDENCE.md`. `deleg_f13c33e0`. Betroffene Kriterien: S06/S07/S08/S09/VD-12/VD-13/PX-06. |
| 18:02 | RF-08 geliefert. Zentrale-Verifikation: pristine RC=1 → gepatcht RC=0 (cursor count=1 nach Duplikat-Aufruf, restart-fresh-interpreter liest denselben Cursor, kein Duplikat); alle 8 Pins grün; Komposition rf04-07+10 auf /tmp/gv_secfix_int — alle Bruder-Proben OK, Engine-Regressionen 13/13. → `6fb7501` (mit RF-08/S06/VD-13/PX-06-Mappings; Register 24/77). Offen: Scheduler-Konsum von result[rf08_segment_continuation] nicht end-to-end verdrahtet. |
| 18:17 | **B6 geliefert und selbst committet (0547a5c)** — DockerRuntimeManager (618 Z., digest-pin, Lifecycle, Struktur-Taxonomie, Privileged-/Socket-Verweigerung, network-none Default), Manifest v2 (fail-closed), buildflow-Runtime-Traversal, **echte E2E**: alpine Round-trip (pull/pin/exec/verify), pandoc md→html Artefakt, SearXNG HTTP 200 mit 34 Treffern, publish→zweiter Kontext ohne Restart; Failure-Taxonomie live (image_missing, arch_mismatch). Suite RC=0 (1507 Punkte, 30 neue Docker/RF-08-Tests). Zentrale-Verifizierung: DOCKER-BUILDER-EVIDENCE.md geprüft, keine erfundenen Zahlen; live model call als not_run benannt. |
| 18:25 | RF-09-Worker (Crash-Fenster-Recovery, vorletzter offener Engine-Punkt) dispatcht: 4 Crash-Fenster inventar, Segmente-Resume-Fencing + External-Wirkung-Reconciliation (`unknown` state, nie blind re-send), crash-injection via echtem kill -9. `deleg_8edafdb2`. |
| 18:40 | UX-Prüfwelle dispatcht (Kap. 06 §7 zweiter Reviewer, headless: Doku-Tests + CLI-Pfade + statische DOM-Codes; GUI-Gerätepfade explizit not_run markiert statt gefaked). Criteria: S02/S03/S10/S11/S12/S14/V2-02/04/05/06/08/12, VD-22. `deleg_0d8b8f13`. Zwei Prüfwellen parallel: RF-09 (Engine) + UX (Produkt). |

## Block-Eigentum

| Block | Pflicht | erlaubte Pfade |
|---|---|---|
| Zentrale (Hauptagent) | Integration, docs/RF-STATUS.md, audit/, Commits | alles außer Worker-Pflicht |
| Worker A | RF-04/05/06/07/10 | host-overlay/*.py (neu), tools/host_rf0*.py (neu), tests/test_rf0*.py (neu), /tmp |
| Worker B | VD-16/VD-21 | src/godsv/subagent_briefs.py (neu), src/godsv/llm.py / context.py / voice.py (sanity layer), tests/ (neu) |

`chat_completion_helpers.py` bleibt Eigentum Worker A allein — keine konkurrierenden Patches.

## Offen nach dieser Welle (Plan)

1. **RF-01/RF-08/RF-09** — zweiter Engine-Worker sobald Worker A liefert (RF-08 Segment-Lifecycle ist Hard-Rest; RF-01/RF-11/RF-12 brauchen Modell/Budgetfreigabe → `blocked` dokumentieren, nicht faken).
2. **V2-01/V2-11 Chat-IA** — Entfernung der exploitierenden Cockpit-Navigation in `plugin.js` inkl. `gv_ui_e2e.mjs`-Anpassung; Einstellungen bleibt, Cockpit/Verification/Activity/Tools/Treasury werden Chat-Ereignisse. Über eigenen Worker oder Zentrale nach Worker-Abschluss.
3. **Auditpaket** (Kapitel 06 §8): TEST_REPORT.md, BENCHMARK_MANIFEST.json, PLATFORM-MATRIX.md, SKILLS_USED.md, UPDATE_COMPATIBILITY.md, RELEASE_NOTES.md.
4. **Unabhängige Prüfwelle** (Kapitel 06 §7): Security/Recovery-Reviewer + UX/Produkt-Reviewer.

## Verifizierter Startzustand (eigene Messung, 14:25–14:47)

- `python3 tools/build_audit_register.py --check` → stale, neu erzeugt: **77 Kriterien · 15 mapped · 62 unknown**.
- `node tools/check_desktop_plugin.mjs` → contract OK; thinking-orbs vendored current.
- Suite: 1424 passed, 4 skipped, RC=0 (nach den beiden Test-Pins).
- Engine `fb56a7e06d` unverändert; Overlays rf02/rf03 noch NICHT auf dem Live-Baum (bewusst; Live-Eingriff = autorisiertes Fenster mit Willy).

## ABNAHME.md — neuer verbindlicher Vertrag (16.09.2026 19:0x)

Willy hat `/home/willy/Documents/Design_Docs/ABNAHME.md` als **verbindlichen Abnahme-,
Challenge- und Releasevertrag** nachgeliefert (58 KB, 23 Teile). Konsequenzen, die sofort
eingearbeitet wurden:

1. **Kriterienmatrix 77 → 97.** Teil XV führt `JEV-01..JEV-20` ein (System One / Jev:
   providerneutraler Vertrag, Antwortvertrag, Question Registry, atomare Fragen, State
   Sanitizer, LLM-Adapter, TypeSafe-Liveadapter, Treasury-Bindung, Fehler-/Deadline-Policy,
   Shadow Mode, Kalibrierung, Voice-Triage-Pack, Completion-Precheck, Staleness-Bindung,
   GUIState, Computer-Use-Reactive-Checks, visuelle Subtools, System-2-Eskalation,
   Benchmark, sicherer Rollout). Die 20 Kriterien sind **wörtlich aus Teil XV** in die
   eingefrorene Inventur `Skaldir_Release_Paket/REQUIREMENTS.json` übernommen
   (`jev_extension`-Block, Backup `REQUIREMENTS.json.pre-jev.bak`); Register:
   `python3 tools/build_audit_register.py` → **97 Kriterien**.
2. **Evidence-Raster erweitert.** §4.2 fügt `integration_tested` zwischen `mock_tested` und
   `live_tested` ein — `tools/build_audit_register.py` akzeptiert die Stufe jetzt (sonst
   hätte eine ehrliche Klassifizierung am Schema gescheitert).
3. **Statuswahrheit bleibt dreidimensional** (§4): Implementierung / Evidenz / Abnahme.
   `blocked`, `not_run`, `unknown` zählen **nie** als `satisfied` (§4.3).
4. **JEV-Statussprache** (§117.1): höchstens `JEV_READY` ohne autorisierten TypeSafe-Zugang;
   `JEV_LIVE_TESTED`/`JEV_ACTIVE_VERIFIED` sind ohne echten Live-Call verboten. JEV-07 endet
   heute `blocked_external_access`.
5. **Pflichtartefakte** (§114): zusätzlich zu den bestehenden Dateien sind `findings.json`,
   `PERFORMANCE.md`, Artefakt-Hash-Manifest, `COMPUTER_USE_SUBTOOLS.md`,
   `SYSTEM_ONE_QUESTIONS.json`, `SYSTEM_ONE_REPORT.md` (sobald JEV begonnen) und rohe
   Benchmark-Daten + Auswerteskript fällig.

### Wellen ab 19:05

| Zeit | Schritt | Ergebnis |
|---|---|---|
| 18:26–18:55 | RF-09-Worker (Crash-Fenster) | geliefert; **eigene Verifikation**: pristine `RC=1` mit ehrlicher Meldung, Overlay auf komponiertem Baum → alle Probes PASS, Engine-Regression **13 passed / 0 failed**, 8/8 Pins. Inventur `docs/audit/RF09-CRASH-WINDOW-INVENTORY.md` (W1/W2 unsicher→gefenced, W3/W4 sicher). |
| 19:0x | RF-09 gemappt (`integration_tested`) | Engine-Recovery **10/12 implementiert**; nur RF-01/11/12 brauchen die Langlauf-Freigabe. Commit `f772fb0`, Register-Commit `b7a287a`. |
| 19:1x | JEV-Foundation-Worker (JEV-01/02/03/04/05/06/09/14/20, §36–§49 + §117.1) | läuft (`deleg_d23d9506` task-0): `src/godsv/system_one*.py`, Question Registry, Sanitizer, LLM-Adapter, Deadline-Policy, Staleness-Bindung, sechs Feature-Flags mit Rückfalltest. |
| 19:1x | Artefakt-Worker (§114/§119) | läuft (`deleg_d23d9506` task-1): `audit/findings.json` (§113), `PERFORMANCE.md`, Artefakt-Hash-Manifest, `COMPUTER_USE_SUBTOOLS.md`, `SYSTEM_ONE_QUESTIONS.json`, `PLATFORM-MATRIX` (JEV-Zeile), `TEST_REPORT`, `docs/ABNAHME-STATUS.md` (97 Kriterien + §117-Gate-Tabelle), Benchmark-Auswerteskript mit lautem Fehlschlag statt erfundener Zahlen. |

### Damit offen nach dieser Welle

- JEV-07/08/10/11/12/13/15/16/17/18/19: aufsetzend auf den JEV-Foundation-Contracts
  (Shadow Mode, Kalibrierung, Triage-Pack, Completion-Precheck, GUIState, Subtools).
- RF-01/11/12: echter 152k-/80-Iterations-Langlauf (Kosten + mehrere Stunden) — **Freigabe
  erforderlich**.
- Windows/macOS-Builds, Signierung/Notarisierung: externe Abhängigkeit (§117 Ziffer 4/5).
- Echte Computer-Use-Geräteabnahme (§57/§117 Ziffer 6): braucht eine lokale GUI-Session.

### Stufe 1 des Langlaufs gefahren (kostenfrei, kein Modellaufruf)

`tools/rf_stage1_compose.sh ~/gv-trees/secfull` (neu, committet in `8da8b3d`):

* Neun Overlays (rf02→rf10) auf eine **Kopie** angewandt; Live-Baum bleibt unangetastet.
* **Auflösungs-Gate**: `agent.turn_finalizer`, `tools.registry`, `hermes_cli.commands` laden
  nachweislich aus der Kopie — vorher hatte die Kopie den **Live-Baum** importiert (PEP-660-
  Finder mit absoluten Pfaden), wodurch Probemessungen auf Kopien wertlos waren. Das Skript
  schreibt die Finder-Map um und verweigert die Probe-Phase, wenn das Gate nicht hält.
* Alle Probes **RC=0** (rf02, rf04, rf05, rf07, rf08, rf09, rf10, monoton) und
  Engine-Regressionsprobe **13 passed / 0 failed**.
* RF-01-Fixture (`tools/rf_measure.py`) gegen den isolierten Testprovider (kein Netz-Egress)
  → `outputs/rf01/raw.jsonl` + `outputs/rf01/summary.json`. Reale Befunde daraus: der Motor
  skaliert sein Stale-Timeout mit dem Kontext **180 s (16k) → 240 s (64k/96k) → 300 s (152k)**;
  `headers_no_sse` und `ping_only` enden im `ReadTimeout` ohne einen einzigen semantischen
  Delta, `reasoning_only` gilt korrekt als Fortschritt.
* `tools/rf_measure.py` hatte einen echten Defekt: relatives `--out` brach am Ende mit
  `ValueError` ab (nach dem Schreiben) → gefixt über `_display()`.
* Ehrlich weiterhin `not_run`: der echte Agent-Loop mit ~152k Kontext über 80 Iterationen,
  echter Provider (Budget), Kompressionspfade im Lauf, Crash/Stop-Races im echten Loop.
  Das steht so im `not_run`-Block der `summary.json` und in `docs/RF-LONGRUN-FREIGABE.md`.
