# RESUME — Skaldir Agent release program (state + next actions)

Stand: 16.09.2026 · Repo `~/projects/gods-voice` @ `8da8b3d` (branch `godsv-supervisor-hardening`) ·
Design-Paket `/home/willy/Documents/Design_Docs/Skaldir_Release_Paket` (**97 Kriterien**: 77 geerbt + JEV-01…20,
Vertrag `/home/willy/Documents/Design_Docs/ABNAHME.md`) ·
Engine `~/.hermes/hermes-agent` @ `fb56a7e06d` (pristine; Overlays sind Artefakte, **NICHT** angewandt).

Diese Datei ist der Einstiegspunkt nach einer Unterbrechung. Sie sagt **was belegt ist**, **was offen ist**,
**was extern blockiert** und **welcher Schritt als nächstes läuft** — ohne Behauptungen, die nicht durch
einen Befehl reproduzierbar sind.

> **Wichtig zum Baum:** Zum Zeitpunkt dieses Schreibens arbeiten parallele Wellen im selben Arbeitsbaum
> (System-One/JEV: `src/godsv/system_one*.py` + Änderungen an `computer_use/events/llm/voice.py`, uncommittet).
> Ein HEAD allein beschreibt den Baum **nicht** — ABNAHME §5 verlangt einen Workspace-Fingerprint, der
> uncommittete Änderungen einschließt. Details: `docs/ABNAHME-STATUS.md` §6.

## 1. Kriterienstand (Register: `audit/REQUIREMENTS.json` + `audit/verification-mapping.json`)

**48/97 gemappt, 49 weiterhin `unknown`, 0 × `satisfied`.** Prüfregel:
`python3 tools/build_audit_register.py --check` → `audit register current — 97 criteria`.
Verteilung (aus dem Register gezählt): `implemented/integration_tested/open` 1 ·
`implemented/live_tested/open` 2 · `implemented/mock_tested/open` 9 · `missing/mock_tested/open` 1 ·
`partial/live_tested/open` 8 · `partial/mock_tested/open` 12 · `unknown/unreviewed/open` **64**.

### A. Engine-Recovery (Kapitel 09) — 10 implementiert (Overlays, Live-Engine bewusst UNVERÄNDERT)

| ID | Feature | Artefakt | Testtiefe |
|---|---|---|---|
| RF-02 | requestweite Deadline | `host-overlay/patch_rf02_request_deadline.py` | rot-first, 6 Pins |
| RF-03 | monotone Stream-Clock | `patch_rf03_monotonic_stream_clock.py` | 5 Pins incl. Real-Defekt-Nicht-Vakuität |
| RF-04 | Retry-Gesamtbudget (3 Versuche gesamt) | `patch_rf04_retry_budget.py` | 5 Probe-Pässe + Pins |
| RF-05 | Fallback-Fitness-Gate | `patch_rf05_fallback_fitness.py` | pristine RC=1 → 0 + Pause-Flag |
| RF-06 | Kompressive Deadline + Preserve-Liste + Reintegration | `patch_rf06_compression_preserve.py` | Vakuum-Summarizer-Probe PASS |
| RF-07 | Interim-Report am 80er-Limit (0 Modell-Aufrufe bei kaputtem Provider) | `patch_rf07_interim_report.py` | 7× PASS, gelenkte Redaction |
| RF-08 | Segment-Continuation-Cursor (idempotent, restart-sicher) | `patch_rf08_segment_continuation.py` | 8 Pins |
| **RF-09** | **Crash-Fenster-Recovery (gelandet)** | `patch_rf09_crash_windows.py` (4 Dateien) | **8 Pins, `integration_tested`** — echter `SIGKILL` eines Hilfsprozesses, alle Bruder-Proben auf dem komponierten Baum PASS, Engine-Regressionen 13/13; Inventar `docs/audit/RF09-CRASH-WINDOW-INVENTORY.md` |
| RF-10 | Stop ≤2 s lokal (200 ms Scheiben, monotonic) | `patch_rf10_stop_responsiveness.py` | **0.200 s vs 2.000 s**, 3× in dieser Session reproduziert |
| — | tool refresh | `patch_tool_refresh.py` | Anker `--check` grün |

**Offene RF-Grenzen:** RF-01/RF-11/RF-12 sind extern blockiert (echter Modell-Langlauf, Budgetfreigabe) →
registriert als `not_run`, **keine** erinnerten Zahlen. RF-09 fenced nur den **sequenziellen** Dispatcher; der
nebenläufige Batch-Pfad `execute_tool_calls_concurrent` hat dasselbe Fenster ohne Marker. Zahlen:
`docs/audit/PERFORMANCE.md`.

### B. Produkt-Suite godsv

- **VD-16 Subagent-Briefs** — SubagentBrief + BriefCoordinator (Epoch-Fencing, Timeout→Partial/Empty, Abort,
  `side_effect_state=unknown`, worktree-Pflicht); RunStore.enqueue_brief/briefs/record_brief_result.
  Review-16: SEC-01 (on_timeout unfenced) und SEC-12 (gefälschte Owner/Epoch) gefixt + frisch nachgefahren;
  **offen**: SEC-02 (Takeover-Race ohne Lock), SEC-03 (kein Abort-Audit), SEC-04/SEC-11 (keine Revalidierung
  im Store), SEC-10 (Erfolgs-Audit ohne Owner/Epoch), SEC-12b (Store vertraut der Caller-Dataclass).
- **VD-21 Shared Budget** (5 Rollen, Reserve/Spend, Unreserved-Cap 5 %, `budget_wait` strukturiert, JSONL-Dump,
  Thread-Safety). Review-16 P0 SEC-06 und P1 SEC-05 gefixt + nachgefahren (Over-Tap/Unknown-Id hart verweigert,
  Cap im selben Lock); offen: SEC-07 (Dead-Code `if False` im Cancel-Pfad), SEC-08 (Dump ohne Lock),
  SEC-05b (Reservation-Churn umgeht den Cap, P3).
- **VD-19** Outcome-Lie-Gate am Brief-Layer (Store akzeptiert kein `delivered` über dem echten Status).
- **V2-01/V2-11 Schritt 1** — Cockpit+Activity aus der Sidebar-Nav entfernt; Review-16 GV2-SEC-23/24 offen
  (die `activity`-Route + VIEW_ROUTES + Render-Zweig leben weiter, nur per Hand-Hash erreichbar).
- **S02 Composer** — P1 „strukturell leerer microActions-Streifen" in `0ca9980` gefixt (Smoke jetzt
  **158 Checks**, contract OK). Offen: P2 `UX16-02` — die **deployte** App-Kopie hinkt dem Repo-Kandidaten nach.
- **B6 Docker-Tool-Builder** (0547a5c) — DockerRuntimeManager, Manifest v2 fail-closed, echte E2E (alpine
  Round-trip, pandoc md→html, SearXNG 200/34 Treffer, publish → zweiter Kontext ohne Restart).
  Evidence: `docs/audit/DOCKER-BUILDER-EVIDENCE.md`. In dieser Session: Live-Tier
  `GV_B6_LIVE_DOCKER=1 pytest tests/test_docker_runtime.py` → 25 passed / 0 skipped.

### C. JEV / System One — `not_started`

Kein committeter `SystemOneEvaluator`, kein `SystemOneEvaluation`, keine Question Registry, keine Featureflags,
kein Treasury-Eintrag, kein Credential. Eine parallele Welle arbeitet an `src/godsv/system_one*.py`
(uncommittet; 6 Testmodule laufen grün, 117 Tests / 1.46 s — **kein** Nachweis). Vertrags-Fragesatz und
erwartete Codequelle: `docs/audit/SYSTEM_ONE_QUESTIONS.json`. Claim-Level nach §117.1: **`not_started`**
(`JEV_READY` erst nach Commit + unabhängiger Prüfung, `JEV_LIVE_TESTED` nur mit autorisiertem TypeSafe-Aufruf).

## 2. Stand der Prüfwellen (Kap. 06 §7 / ABNAHME §109–§111)

- **Reviewer A — Security & Recovery** (`outputs/review16/` + `retake/`): 25 Befunddateien, 5 Nachlauf-Befunde.
  Beide **P0** (SEC-06 Budget-Over-Tap, SEC-17 RF-07-Rohdatenpersistenz) sind gefixt **und** frisch
  nachgefahren; ebenso P1 SEC-01/05/12/13. Verbleibend: u. a. SEC-02, SEC-03, SEC-04, SEC-07, SEC-08, SEC-10,
  SEC-11, SEC-12b, SEC-14 (P2: `_rf06_redact` delegiert, Engine-Redactor deckt 4 Secret-Formen nicht),
  SEC-15, SEC-16, SEC-18, SEC-19, SEC-20b, SEC-05b, SEC-13b. Konsolidiert: **`audit/findings.json`** (36 Findings
  im §113-Schema, Zustände ehrlich, IDs gegen das Register geprüft).
- **Reviewer B — UX & Product** (`outputs/review16/ux/`): 6 nächste handelbare Defekte (1×P1 gefixt, 4×P2,
  2×P3) + Carry-forward-Liste. Konsolidiert in `audit/findings.json` (UX16-01…UX16-06).
- **Reviewer C — System One / Computer Use Calibration** (§111): **nicht gestartet** — „sofern JEV aktiv";
  es gibt keine Jev-Antworten zu kalibrieren, und kein visuelles Subtool (`docs/audit/COMPUTER_USE_SUBTOOLS.md`).

## 3. Externe Blocker (Kap. 06 §8 / ABNAHME §117/§118)

| Blocker | Kriterien | Benötigt |
|---|---|---|
| Kein autorisierter 152k/80-Iterationen-Langlauf | RF-01, RF-11, RF-12, S15, S19, §108 | Budgetfreigabe (`docs/RF-LONGRUN-FREIGABE.md`) |
| Keine Windows-/macOS-Build-Hosts | PX-01…PX-08 Plattformteile | Build-Hosts oder CI-Matrix |
| Keine Signierung/Notarisierung | PX-07, §77 | Windows-Codesign + Apple Developer-ID/Notarization-Profil |
| Kein Board-/GUI-Fenster (headless Welle) | S02, S03, S10, V2-07, V2-08, PX-04 | lokale Geräteabnahme |
| Kein TypeSafe/Jev-Zugang (Early Access) | JEV-01…JEV-20, §63 | Account/Waitlist + Treasury-`ConnectionRef` (`scope = system_one.evaluate`) |
| Kein Container-GUI-Image `localhost/godsv-cu-x11` | S10, §57, §95, §96 | Image bereitstellen |
| Kein eingefrorener Holdout-Split | Stufe C/D, §105/§106 | Split einfrieren, dann fahren |

## 4. Resume-Pfad

```bash
cd ~/projects/gods-voice
python3 tools/build_audit_register.py --check          # Register == Eingaben (97 Kriterien)
.venv/bin/python tools/check_findings_criteria.py      # §113-Vertrag gegen das Register
node tools/check_desktop_plugin.mjs                    # UI-Kontrakt (158 Checks)
.venv/bin/python -m pytest -q -p no:randomly           # volle Suite (Zählzeile: mit -vv)
GV_B6_LIVE_DOCKER=1 .venv/bin/python -m pytest -q -p no:randomly tests/test_docker_runtime.py
.venv/bin/python tools/eval_benchmark.py               # muss „no runs", RC=2 liefern, solange keine Rohdaten da sind
```

**Wichtig:** Overlays sind **Artefakte** — sie sind NICHT auf den Live-Engine-Baum angewandt. Der Live-Eingriff
(rf02–10 setzen) gehört in ein autorisiertes Fenster mit Willy-Abstimmung; danach GV-App-Neustart.

## 5. Unmittelbarer nächster Schritt (eine Reihenfolge, keine Liste)

1. System-One-Welle committen (paralleler Worker) → Register-Mapping für JEV-01…05/20 gegen den echten Code
   prüfen und **erst dann** Status heben; `docs/audit/SYSTEM_ONE_QUESTIONS.json` gegen
   `QuestionRegistry().manifest()` diffen.
2. Die sechs Retake-Nachfahr-Proben aus `outputs/review16/retake/` als reproduzierbare Tests pinnen
   (SEC-01/05/06/12/13/17) — sonst verfällt der Nachweis mit dem nächsten Commit.
3. `docs/RF-LONGRUN-FREIGABE.md` durch Willy freigeben lassen → RF-01/RF-11/RF-12 + §108-Langlauf.
4. GUIState-Vertrag + ein visuelles Subtool (§52/§55) bauen — Voraussetzung für jede Computer-Use-Abnahme.
