# IMPLEMENTATION-MATRIX — Skaldir Agent Live-Control-Plane

**Zweck:** Je Feature/Fähigkeit: Audit-Status vorher → tatsächlicher Codepfad → Änderungen →
Tests → Live-Proof → Restgrenzen → finaler Status. Fortschreibung während der Umsetzung.
**Labels (strikt, nie hochstufen ohne Beleg):** `exists` · `wired` · `tested` · `live-verified` ·
`offen`.

**Stand:** 13.09.2026 · HEAD `bedcd01` (Branch `godsv-supervisor-hardening`) · Baseline `85ee575`
**Ground Truth:** `docs/COMPLETE_AUDIT-2026-09-13.md` + `/tmp/gv_audit/*`
**Plan:** `docs/IMPLEMENTATION-PLAN-live-control-plane.md`

---

## 1. Ausgangslage (verifizierte Zahlen, selbst gezählt)

**Anforderungskatalog** (`/tmp/gv_audit/00-anforderungen.md`), 250 Einträge — Zählung über unique IDs:

| Familie | Einträge | | Status | Anzahl |
|---|---|---|---|---|
| VOICE | 36 | | FIXED | 145 |
| TOOLS | 38 | | TEILWEISE | 57 |
| MEM | 31 | | OFFEN | 55 |
| AUTO | 27 | | | |
| UI | 32 | | | |
| SUI | 32 | | | |
| CLI | 30 | | | |
| SEC | 24 | | | |
| **Σ** | **250** | | | **257** |

**Domain-Audits** (5 Dateien, `- **VERDICT:`-Zeilen; `audit-3` nutzt `**VERDICT:`):

| Audit | GUT | TEILWEISE | SCHWACH | FEHLT | Σ |
|---|---|---|---|---|---|
| audit-1 voice-gates | 27 | 12 | 1 | 3 | 43 |
| audit-2 tools-capability | 17 | 13 | 2 | 7 | 39 |
| audit-3 memory-autonomy | 26 | 17 | 8 | 9 | 60 |
| audit-4 desktop-release | 40 | 35 | 5 | 15 | 95 |
| audit-5 security | 13 | 8 | 1 | 2 | 24 |
| **Σ** | **123** | **85** | **17** | **36** | **261** |

> Hinweis: 261 Verdikt-Worte auf 257 Verdikt-Zeilen — 4 Zeilen tragen Doppelverdikte
> (z. B. `GUT (…) / nicht code-prüfbar`). Die früher berichteten „232 Einträge" stammen
> aus der Zeit **vor** dem Security-Audit und sind überholt.

**Eigene, unabhängig verifizierte Befunde** (`docs/VERIFIED-FINDINGS-live-2026-09-13.md`):
- `gate_security` fail-open **bestätigt** (`src/godsv/completion.py:1078-1096`).
- **NEU (MINOR):** `secretscan.scan()` meldet dasselbe Secret doppelt (`['generic_key','openai']`);
  `redact()` dedupliziert, das Gate nicht ⇒ zwei BLOCKER für ein Secret. Kein Überblocken.
- Gegenprobe: Handle-Notation, Prosa, Diff-Header ⇒ 0 Treffer (kein Fehlalarm).

---

## 2. §39 Audit-driven P0 — Abarbeitungsstand

| # | P0-Punkt | Audit-Ort | Status | Beleg / Restgrenze |
|---|---|---|---|---|
| 1 | persistent Run Ownership | audit-3 AUTO | 🔄 Block 2 | `run_supervisor.py` in Arbeit |
| 2 | orphaned-run watchdog | audit-3 (2 abgelaufene Leases live) | 🔄 Block 2 | — |
| 3 | real crash recovery Redirect↔Continue | audit-3 | 🔄 Block 2 | echter Crash-Lauf offen (bewusst, Instanz geschützt) |
| 4 | exactly-once continuation | audit-3 | 🔄 Block 2 | Store-Ebenen-Test angesetzt |
| 5 | terminal-state invariant | SKILL-Pitfall (VERIFIED_COMPLETE einfrierbar) | ⬜ offen | noch kein Block zugewiesen |
| 6 | evidence workspace/session binding | audit-1 (workspace-Bypass leer) | ⬜ offen | Block 3 |
| 7 | missing Verification Runners (`run`/`ui_e2e`/`api`/`build`/`persistence_restart`) | SKILL-Pitfall + audit-1 | ⬜ offen | **Deadlock für gewöhnliche Goals** |
| 8 | `gate_tests` deadlock | SKILL-Pitfall (replay-verifiziert) | ⬜ offen | braucht pytest-Kriterium oder clean tree |
| 9 | `gate_security` fail-open | audit-5 SEC | 🔄 Block 1 | Befund von mir reproduziert, Steer mit Regressionsauftrag |
| 10 | Tool Builder / Testing Tool registration integrity | audit-2 | 🔄 Block 6 | — |
| 11 | generated-tool runtime isolation | audit-5 SEC | 🔄 Block 1 | bwrap-Pfad gefordert |
| 12 | live tool insertion | audit-2 | 🔄 Block 6 | ohne Restart, same-session |
| 13 | semantic no-intervention calibration | audit-3 (`no_action=0` bei 38 Pausen) | 🔄 Block 4+5 | NO_INTERVENTION_NEEDED als Pflichttest |
| 14 | event deduplication | audit-3 (5 Duplikat-Gruppen) | ⚠️ Prüfung fertig, Durchsetzung offen | eigener Live-Lauf: **20 Gruppen / 7320 Extra-Zeilen** (`godsvoice memory-audit`, 23,7 MB); Enforcing braucht `events.py` (fremdes Block-Eigentum) — präzise dokumentiert statt behauptet |
| 15 | budget producer / explicit wait | audit-3 (`budget_tick` ohne Producer) | ⬜ offen | keinem Block zugewiesen |
| 16 | atomic/locked contract persistence | audit-5 SEC | 🔄 Block 1 | temp+rename gefordert |
| 17 | plugin-side trust boundary/auth | audit-5 SEC (kein `HERMES_DASHBOARD_SESSION_TOKEN`) | 🔄 Block 1 | fail-closed ohne App-Bruch |

Legende: 🔄 = in Umsetzung · ⬜ = offen, noch nicht dispatcht · ✅ = verifiziert erledigt.

---

## 3. Deliverables — Status

| Deliverable | Status | Ort |
|---|---|---|
| `COMPLETE_AUDIT-2026-09-13.md` | ✅ committet | 548,8 KB · 5510 Z. · 250/250 |
| `IMPLEMENTATION-PLAN-live-control-plane.md` | ✅ committet | 8 Blöcke + DoD |
| `BENCHMARK-RUN-SCHEMA.json` | ✅ committet | 7 Konfigurationen, Metrikset |
| `VERIFIED-FINDINGS-live-2026-09-13.md` | ✅ committet | eigene Verifikation |
| `IMPLEMENTATION-MATRIX.md` (diese Datei) | 🔄 lebend | wird je Block fortgeschrieben |
| `SECURITY-BOUNDARIES.md` | 🔄 Block 1 | — |
| `RUN-LIFECYCLE.md` | 🔄 Block 2 | — |
| `VOICE-CALIBRATION.md` | 🔄 Block 4+5 | — |
| `CAPABILITY-LEARNING-E2E.md` | 🔄 Block 6 | — |
| `MEMORY-LEARNING.md` | ✅ **selbst verifiziert** | 365 Z. · Commit `776c4ef` · 48 Tests grün (nachgelaufen) |

---

## 4. Arbeitsblöcke

| Block | Inhalt | Status |
|---|---|---|
| 1 | Security/State Integrity (5 Defekte) | 🔄 läuft |
| 2 | Persistent Run Supervisor (P0) | 🔄 läuft |
| 3 | Verification Closure (Runner, `gate_tests`-Deadlock, Evidenz-Bindung, Terminal-Freeze) | ⬜ wartet auf Block 1 (`gates.py`) |
| 4+5 | Live Control Layer + Calibration (15 Golden-Set-Fälle) | 🔄 läuft |
| 6 | Capability Learning (kausale Generalisierung, Holdout, Live-Insertion) | 🔄 läuft |
| 7 | Memory Learning (Incident/Research, Staleness, Cross-Session) | ✅ geliefert, selbst verifiziert (`776c4ef`) |
| 8 | Benchmarks (Vergleichsmatrix) | ⬜ Schema fertig |
| §40 | Voller E2E-Beweis (alle Stufen der Kette) | ⬜ nach 1–4 |

---

## 5. Pflicht-Golden-Set (§38) — Beweisstand

Noch **kein** Fall live-verifiziert; Block 4+5 legt die Regressionstests an.
Kritischster Fall laut Audit-Lage: **Fall 1** (gesundes Debugging → *kein* Eingriff) — live wurde
`no_action = 0` bei 38 echten Pausen beobachtet, d. h. das Gegenteil ist derzeit wahrscheinlicher.
Dieser Fall ist der härteste Kalibrier-Test und muss explizit bewiesen werden, bevor
Precision/Recall als erreicht gilt.

---

## 6. Verifizierter Endstand 14.09. 01:59 — ALLE BLÖCKE COMMITTET (eigene Messung)

**Voller Suite-Lauf OHNE jede Ausnahme (der entscheidende Beweis):**
```
01:59:14  .venv/bin/python -m pytest -q -p no:randomly
          → RC=0 · 1110 Pytest-Punkte · 0 FAILED · 0 ERROR
01:58:31  .venv/bin/ruff check src tests tools integrations app-overlay
          → All checks passed!
HEAD = origin = 1f58760   ·   Arbeitsbaum: 0 uncommittete Pfade
(erste Messung 01:59 auf f47689b; nach den beiden Matrix-Doku-Commits 02:07:44 erneut gemessen:
 SUITE_RC=0 · 0 FAILED/ERROR · 1110 Punkte — identisch, suite bleibt grün)
```
Vorher (Block-1-Beweislauf, 23:39): `SUITE_RC=1` mit **21 FAILED — alle ausschließlich in
`tests/test_capability_learning.py`** (damals Block-6-WIP). Mit dem Block-6-Commit `f47689b`
ist diese Datei getrackt und die Suite **vollständig grün**. Die rote Phase war also echt
rot-first und ist sauber abgeschlossen, nicht wegignoriert.

**Alle sechs Blöcke committet (Commits dieser Etappe):**

| Block | Commit | Umfang |
|---|---|---|
| 1 · Security | `5ba7fd4` `6c31af6` `40a30b9` `61691ee` `fc77e00` | `completion.py`, `secretscan.py`, `plugin_api.py`, `mcpserver.py` + eigene Tests + `SECURITY-BOUNDARIES.md` + 7 Rot-Belegdateien |
| 2 · Run Supervisor | `aa3e655` | `run_supervisor.py` (1769 Z.), `desktop.py`, `gv_crash_recovery.py`, `test_run_supervisor.py` (973 Z.), `RUN-LIFECYCLE.md` |
| 3 · Verification Closure | `7766af3` | `completion.py` (+640), `gates.py` (+48), `test_verification_closure.py`, `VERIFICATION-CLOSURE.md` |
| 4+5 · Live Control | `e169255` `dcc5dcb` | `patterns.py`, `toolcall.py`, `untrusted.py`, `monitor.py`, `pause.py`, `events.py`, `voice.py`, `test_golden_set.py`, `VOICE-CALIBRATION.md` |
| 6 · Capability Learning | `f47689b` | `capability_mining.py` (+886), `capability_health.py`, `liveinsert.py`, `toolbuilder.py`, `test_capability_learning.py`, `CAPABILITY-LEARNING-E2E.md` |
| 7 · Memory | `776c4ef` | `memory.py` (852 Z.), `incidents.py`, `research.py`, `MEMORY-LEARNING.md` |

**Pfad-Audit je Commit (kein Fremdeigentum) — selbst geprüft:**
- `7766af3`: ausschließlich `completion.py`, `gates.py`, `test_verification_closure.py`, Doku ✅
- `f47689b`: ausschließlich `capability_*`, `liveinsert.py`, `toolbuilder.py`, eigener Test, Doku ✅
- `e169255`: ausschließlich Block-4+5-Dateien ✅
- **Cross-Block-Risiko geprüft und ausgeblieben:** Block 3 patchte `completion.py`, während Block 1 dort Tests hatte — Nachprüfung `test_contract_store_atomic + test_security_gate_failclosed + test_panel_trust_boundary → RC=0`. Kein Bruch.
- `tool_ran_here.txt` (Sandbox-Rest) ist **nicht** getrackt und nicht mehr vorhanden ✅

**Dokumentierte Restgrenzen (bewusst, nicht behauptet):**
- Dedupe-/Retention-**Durchsetzung** braucht `events.py` — nur Prüfung + Report implementiert (20 Gruppen / 7320 Extra-Zeilen gemessen).
- `ui_e2e`/`persistence_restart`-Runner führen den **deklarierten** Befehl aus, starten die echte
  Electron-App/keinen echten Prozess-Neustart — ehrlich als `not-certifiable` markiert wo nicht verfügbar.
- Live-Insertion ist **auf Testebene** belegt; echter Hermes-Roundtrip ohne Restart bleibt Restpunkt.
- Der §40-E2E-Beweis (alle Stufen in einem realen Run) ist der nächste offene Meilenstein.

**Eigene Integrationsprüfung 02:03 (nicht von Agenten behauptet):**
- Semantik-Eval `tools/gv_eval_longhorizon.py --semantic-only` → **EXIT 0** (kein App-Zugriff nötig).
- Importprobe: `capability_health` 33 Symbole, `liveinsert` 10 Symbole, `capability_mining.TraceStep`
  + `GeneralizedCapability` vorhanden.
- Event-Registrierung geprüft: `prompt_injection_signal` + `untrusted_instruction_attempt` sind in
  **KINDS und CHAT_KINDS** ✅; `pause_no_action` in KINDS ✅.
- **Namensraum geklärt:** die neun Tool-/Command-Muster (`tool_loop`, `command_loop`, `oscillation`,
  `dependency_churn`, `no_new_evidence`, `retry_without_strategy_change`, `test_without_change`,
  `change_without_verification`, `search_without_progress`) sind **keine** eigenen Event-Kinds,
  sondern `sequence_pattern`-Signale mit `refs["pattern"]` (`src/godsv/patterns.py:13,41-43`) —
  korrektes Design, **keine** Registrierungslücke. Verdrahtung live:
  `plugin/__init__.py:292` (`SequenceAnalyzer`), `:528-544` (Ingest), `:1476` (Signal-Emission).

---

## 6b. AKTUELLER Endstand 14.09. 13:37 — Blöcke 1–8a committet, Suite komplett grün

**Echter Suite-RC im Vordergrund gemessen (keine Ausnahme, kein `--ignore`):**
```
13:37:14  .venv/bin/python -m pytest -q -p no:randomly
          → SUITE_RC=0
HEAD = origin = beb33a9   ·   Arbeitsbaum: 0 uncommittete Pfade
```
Der frühere `SUITE_RC=1` (02:47) kam aus `tests/test_gv_benchmark.py` — Block-8a-WIP, inzwischen
committet (`beb33a9`) und **grün**. Die rote Phase war echt rot-first, nicht wegignoriert.

**Abgeschlossene Blöcke (3 parallele Langläufe, 153/114/192 API-Calls):**

| Block | Status | Eigener Nachweis |
|---|---|---|
| 1 · Security | ✅ gepusht | 4 Rot-Belege (6/6/11/8 failed) je Fix temporär deaktiviert; `fc77e00` auf origin bestätigt |
| 2 · Run Supervisor | ✅ gepusht (`aa3e655`) | 65 Tests; fand **zwei echte Bugs** in sich selbst (Recovery-Race, „neue Generation“ statt exactly-once) |
| 3 · Verification Closure | ✅ (`7766af3`) | Datei:Zeile-Referenzen gespot-checkt: `gates.py:242`, `completion.py:1928/1996/2149` — alle exakt |
| 4+5 · Live Control | ✅ (`e169255`, `dcc5dcb`) | **39 Golden-Set-Tests grün**, 15 Pflichtfälle: 11 testbar, 4 als `OPEN_LIVE_ONLY` benannt |
| 6 · Capability Learning | ✅ (`f47689b`) | 33 Tests; Zombie ×2 behandelt, Code war fertig |
| 7 · Memory | ✅ (`776c4ef`) | 48 Tests selbst nachgelaufen |
| 8a · Benchmark-Harness | ✅ (`beb33a9`) | `tools/gv_benchmark.py` 1437 Z. + 742 Z. Tests |

**Offen (bewusst, nicht behauptet):**
- `docs/BENCHMARK-MATRIX.md` (Block 8b): echte Vergleichszahlen fehlen. Der Harness steht, ist aber
  noch **nicht mit zwei realen Konfigurationen gefahren** — der frühere 8b-Versuch starb im Zombie.
- `docs/E2E-LIVE-PROOF-40.md` (§40): der Agent arbeitete an Stufe 1 (echter Run, Repo-Setup,
  `app.py`-Fehler) und wurde gestoppt; sein Scratch-Verzeichnis ist inzwischen aufgeräumt.
  **Kein Stufen-Ergebnis ist dokumentiert** — die Kette ist damit **nicht** end-to-end bewiesen.
- Dedupe-/Retention-Durchsetzung (braucht `events.py`), echter MCP-Same-Session-Aufruf,
  Live-`no_action > 0`.

**Voice-Selbstbeleg während dieser Etappe:** der Supervisor hat meine eigenen Tool-Calls **live
blockiert** (`pre_tool_call`: „Ziel liegt außerhalb des Projekts (/tmp/…)") und eine Pause wegen
`pattern_no_new_evidence` ausgelöst („152 tool events with no contract verdict — Aktivität ist
kein Fortschritt"). Das ist der Beweis, dass Block 4+5 im laufenden Betrieb greift — an mir selbst.

---

## 6g. Tool-Builder-Welle 1 — §1.1–§1.9 committet (14.09. abends) · eigene Messung

**Auftrag:** Owner-Masterprompt 14.09.2026 (Tool-Builder/Docker-Etappe); Plan `docs/IMPLEMENTATION-PLAN-docker-tool-builder.md`.

**Suite (Block-Lauf B5, Log `outputs/b5_fullsuite.log`): 1316 passed, RC=0.**
**Zentrale-Nachläufe (eigene, unabhängige Läufe je Dateigruppe):**
- 96 passed — execution_boundary, toolbuilder, tools_store, tool_scopes, generated_tool_isolation
- 93 passed — trace_mining, health_events, capability_mining, registry_context, tool_scopes
- 87 passed — mcp_link, capability_learning, execution_boundary, plugin_release, plugin_robustness
- 20 passed — live_surface_wiring, host_refresh, mcp_stdio_e2e

**Commits (alle auf origin; 23 seit Wellen-Start):**

| Block | §1-Lücke | Kerncommits | Eigener Nachweis (Zentrale) |
|---|---|---|---|
| B1 | §1.1 gemeinsame Ausführungs-/Berechtigungsgrenze, Panel-test sandboxed | `951655e` `365750c` `d2e8440` | `execution.py` 262 Z.; Panel `plugin_api.py:1100` und Testing-Runner `capability.py:297` über die Grenze; 17. Test auf Route-Ebene; Sandbox-Probe in `TOOLS-V2-EXECUTION-BOUNDARY.md` |
| B2 | §1.5 Live-Trace-Mining; §1.6 Health-Events + selektionswirksam | `a9705b1` `7b61bae` `9f62155` `c173137` `ffe8bda` | Cursor/Dedupe/Replay/Redaction; `selectable()`; Retirement terminal (Fold-Fix); 93-Test-Lauf grün |
| B3 | §1.7 Compose-before-create; §1.8 Draft am vorhandenen Modellclient | `97b37f7` `f3f51a5` | 678 Z. Diff; Tests grün; CLI-Surface `tools draft` → Welle 2 |
| B4 | §1.2 eine Registry-Wahrheit + Migration; §1.9 CLI Scope/Version + Promotion | `f6b41cd` `f908722` `1c1fda4` `c031d60` `d51e586` | `registry.py` 513 Z.; Migration mit Lazy-Backup (No-op schreibt nichts); `registry status/migrate`, `tools list` mit Scope/Version; live gefahren |
| B5 | §1.3 Live-Insertion an echter Modellgrenze; §1.4 MCP-Automatik | `7bec0c4` `80a5fdf` `8a9fa01` `1d4cb1d` `e6b8e12` `c1688a6` | `pre_llm_call`-Verdrahtung; Host-Overlay `patch_tool_refresh` (+180 Z. Test); Hot-Set ≤3; Health-Gate in MCP-Surface; 20-Test-Lauf grün |

**Ehrliche Rot-Phasen:** B3s Suite-Lauf (18:26) zeigte 3 FAILED ausschließlich in `tests/test_trace_mining.py` (B2-WIP, damals untracked) — kein Regressionssignal; mit B2s Commit grün. Kein Kind hat fremde Dateien verändert (Pfad-Audit per `git show --stat` je Commit).

**Zentrale-Vorarbeit:** Discovery-Testset v1.0.0 eingefroren (`14b66c4`, 36 Fälle) — §8-Anforderung, vor jeder Discovery-Optimierung.

**Nachtrag (Zentrale, 19:29):** Live-Defekt aus B5s Doc geschlossen — `voice_intervention_assessment` und `run_sweep` wurden live emittiert, fehlten aber in `events.KINDS` (`ValueError: unknown event kind`); registriert + Regressionstest + Rot-Beweis (RC=1 → RC=0); voller Suite-Lauf danach **1317 passed, RC=0** (eigene Messung).

**Restgrenzen (bewusst offen):** Docker-Kern (§2/§3 DockerRuntimeManager + Manifest v2 + Executor-Registry) und damit alle Container-Referenztools — Docker ist auf der Maschine nicht installiert; §1.10 CU/Runner, Builder-Pipeline §4a/§4b/§4d, Discovery §4c → Welle 2; B5-Doc `TOOLS-V2-HOST-WIRING.md` fertig (`ef6d615`); **§1.3 live bewiesen** (echter Host-Roundtrip `20260914_190421_ec3a6d`).

---

## 6c. Früherer Endstand 13.09. 23:57 (überholt)

**Echte Suite-RC (nicht abgeleitet — explizit im Vordergrund gemessen):**
```
01:34:28  .venv/bin/python -m pytest -q --ignore=test_capability_learning.py --ignore=test_verification_closure.py
          → SUITE_RC_OHNE_WIP=0
01:37:06  Block-1-Tests NACH Blocks-3-Patch an completion.py:
          test_contract_store_atomic + test_security_gate_failclosed + test_panel_trust_boundary → RC=0
```
⇒ Blocks 3 Patch an `completion.py` hat Block 1 **nicht** gebrochen (der befürchtete
Cross-Block-Bruch ist geprüft und ausgeblieben).

**Weitere Commits auf origin:** Block 4+5 (`e169255` Live Control Layer + Golden Set,
`dcc5dcb` Kalibrierungs-Doku). Lokaler HEAD == origin (`dcc5dcb`).

**Pfad-Audit der neuen Commits (kein Fremdeigentum):**
- `e169255`: nur `docs/VOICE-CALIBRATION.md`, `patterns.py`, `toolcall.py`, `untrusted.py`,
  `monitor.py`, `pause.py`, `events.py`, `voice.py`, `plugin/__init__.py`, `test_golden_set.py`
  — ausschließlich Block-4+5-Eigentum. ✅
- `aa3e655` (Block 2): `run_supervisor.py` + eigene Tests + `desktop.py` + `gv_crash_recovery.py` + Doku. ✅
- `5ba7fd4`/`6c31af6`/`40a30b9`/`61691ee` (Block 1): `completion.py`, `secretscan.py`, `plugin_api.py`,
  `mcpserver.py` + eigene Tests. ✅
- `fc77e00`: `SECURITY-BOUNDARIES.md` **+ `outputs/block1-evidence/*`** — die Rot-Belege sind bewusst
  als Repo-Evidenz committet (7 Dateien, referenziert von der Doku). `tool_ran_here.txt` ist **nicht**
  getrackt und existiert nicht mehr. ✅

**Zwei Zombies behandelt:** Block 6 verlor zwei Agenten am selben Schritt (Doku-Generierung am Ende
der Beweiskette). Beide gestoppt — nicht weitergefüttert. Ihr Code ist fertig und grün
(`tests/test_capability_learning.py` **33 Tests, RC=0**, `ruff` clean); ein dritter, eng
begrenzter Agent schreibt nur noch die Doku + committet (kein Neucode).

---

## 6d. Früheste Messung 13.09. 23:57

**Commits auf origin (HEAD `61691ee`):**

| Block | Commit(s) | Eigener Nachweis |
|---|---|---|
| **1 · Security** | `5ba7fd4` `6c31af6` `40a30b9` `61691ee` | 4 Rot-Beweise + Suite-Cleanup; **isolierter Lauf RC=0**; `ruff` auf Commit-Umfang **„All checks passed"** |
| **2 · Run Supervisor** | `aa3e655` | `run_supervisor.py` 1769 Z. · **69 Tests selbst grün** · `RUN-LIFECYCLE.md` 243 Z. |
| **7 · Memory** | `776c4ef` | **48 Tests selbst nachgelaufen RC=0** |
| Doku | `a3f2447` (Matrix) | diese Datei |

**Rot-Beweise selbst gesichtet** (`outputs/block1-evidence/red-*.txt`) — echte Fehlschläge, keine Behauptungen:
```
red-gate-security:   assert findings, "scanner crash must not yield an empty finding list" → assert []
red-panel-trust:     assert panel.origin_allowed("") is False → AssertionError: assert True is False
red-contract-store:  with pytest.raises(OSError): → Failed: DID NOT RAISE
red-generated-tool-isolation: (Isolationsasserts der Quelle)
```

**Suite-Lage präzise getrennt (wichtig):**
- Volle Suite **ohne** `tests/test_capability_learning.py`: **RC=0** (eigener Lauf, 79 s).
- Die **21 FAILED** im Block-1-Beweislauf gehören **ausschließlich** `tests/test_capability_learning.py` —
  Blocks 6 rot-first-WIP-Datei (`untracked ??`, ImportError `TraceStep` → inzwischen implementiert).
  **Keine** Regression, **kein** Block-1- oder Block-2-Schaden.

**Isolation real geprüft:** `tool_ran_here.txt` (Root, mtime 22:50) war ein Überrest **vor** dem
Fix-Commit 23:21. Nach dem Löschen erzeugt der Isolationstest die Datei **nicht** neu
(`assert not (runtime / "generated_tools" / ...)` greift) → b03-Sandbox wirkt.

**Offen (bewusst, nicht behauptet):** `docs/VERIFICATION-CLOSURE.md` ✅ geliefert (Block 3 · `7766af3` · 308 Z.) ·
`docs/CAPABILITY-LEARNING-E2E.md` (Block 6, läuft) · Dedupe-/Retention-**Durchsetzung** braucht
`events.py` · `outputs/` ist ungetrackt im Root (Evidenz, von `SECURITY-BOUNDARIES.md` referenziert —
vor dem Abschluss committen oder nach `docs/evidence/` verschieben).

---

## 6e. Block 6 + 7 — verifizierte Restgrenzen (14.09., eigene Prüfung)

**Block 6 (`f47689b`, 2476 Z. über 5 Unterziele, 33 Tests grün) — vier Restgrenzen, alle in der Doku belegt:**
- `traces_from_events` (`capability_mining.py:769`) ist **nicht** verdrahtet: Mining aus echten
  Event-Traces ist offen. Belegt ist nur das Mining über explizit übergebene Trace-Listen.
- Zielname-Generalisierung liefert den parametrisierten Namen im Beispiel, aber die
  Namensbildung ist lexikalisch, nicht semantisch.
- Search-before-Build sucht per Term-Überlappung (`_match_score`) — **lexikalisch**, kein Embedding.
- „Holdout" = Arbeits-/Kaputt-Paar ohne wiederholtes Ziel; **kein** unabhängiger Datensatz.
- Live-Insertion: kein Aufrufer außerhalb `liveinsert.py` + Tests; echter Hermes-Roundtrip offen.

**Block 7 (`776c4ef`, 48 Tests) — Dedupe/Retention bleibt OFFEN, mit jetzt größerer Messung:**
- Nur **Prüfung** implementiert (read-only, `godsvoice memory-audit`); Durchsetzung bräuchte
  `src/godsv/events.py` (fremder Eigentümer) → bewusst nicht angefasst.
- Live an einer Kopie des echten Stores gemessen: **15539 Events, 20 Duplikat-Gruppen,
  7275 Extra-Zeilen, 22,9 MB** — größer als der Audit-Befund (5 Gruppen).
- Restpunkt 2: der Live-Monitor wurde mit `signatures()` gespeist (zu breit) — Verdrahtung
  auf `intervention_signatures()` läuft in eigener Delegation (`deleg_623af1a4`).

**Suite-Status zum Zeitpunkt dieser Messung:** `RC=0`, keine Ausnahme, ruff clean, Arbeitsbaum
bis auf `outputs/benchmark/` und `outputs/e2e40/` (laufende Blöcke 8b und §40) sauber.

## 6f. Block 3 — gemessenes Verdikt (14.09., eigene Vordergrund-Messung)

- **Replay-Check:** `git merge-base --is-ancestor 7766af3 HEAD` → **JA**. `HEAD = origin = 419aa3f`;
  `git log --oneline 7766af3..HEAD` zeigt ausschließlich meine eigenen `docs(matrix)`-Commits.
  Die Kinder-Meldung „geliefert und gepusht“ war ein **Replay** eines bereits bestehenden Commits.
- **Umfang:** genau 4 Pfade — `completion.py` (+640), `gates.py` (+48),
  `tests/test_verification_closure.py` (711 Z. neu), `docs/VERIFICATION-CLOSURE.md` (308 Z. neu).
  **`integrations/**` NICHT enthalten** — Auftragsgrenze gegen Block 4/5 eingehalten.
- **Tests** (ohne doppeltes `-q`, das verschluckt die pytest-Zählzeile):
  `tests/test_verification_closure.py` → **49 passed**, RC=0. Volle Suite im Vordergrund
  (ohne die untracked WIP-Datei des laufenden Wiring-Leafs) → **1178 passed, 0 FAILED/ERROR**, RC=0.
  `ruff check src tests tools integrations app-overlay` → **All checks passed!**
- **Doku-Anker:** Runner-Matrix, `gate_tests`-Deadlock vorher/nachher, Evidenz-Bindung,
  Terminalzustand, 6 Marker, 6 Restgrenzen.
- **Ehrliche Grenzen (übernommen, nicht geglättet):** `persistence_restart` startet keine echte App,
  `ui_e2e` startet die Electron-App nicht (bleibt §40), Session-Bindung ist **Adoption** (keine
  Signatur), `gate_tests` feuert weiterhin nur bei geändertem Code. Kein „live-verified“ für die
  App-Proben — nur wired/tested.

## 7. Verdrahtungs-Rot-Beweis (14.09., von der Zentrale selbst gefahren)

`docs/IMPLEMENTATION-MATRIX.md` · Beleglog: `outputs/px_wiring_redproof.log`

- **Fix:** `integrations/hermes/godsv_plugin/__init__.py:275` — Live-Monitor wird mit
  `self.incidents.intervention_signatures()` gespeist statt mit dem zu breiten
  `signatures()` (enthält laut eigenem Docstring auch abgelaufene/archivierte/nie geprüfte
  Einträge ⇒ ein veralteter Fingerprint konnte stark eskalieren, §38/GS-04-Verstoß).
- **Commit:** `2804788 fix(plugin): feed the monitor applicability-checked signatures` (+8/−1).
- **Testdatei:** `tests/test_plugin_incident_wiring.py` (5 Tests, getrackt).
- **Rot-Beweis (echt gefahren, NICHT per `git stash`):** Zeile 275 temporär auf
  `signatures()` zurückgenommen → `RC=1`, **2 Tests ROT**
  (`test_the_session_monitor_holds_only_intervention_eligible_fps`,
  `test_the_live_session_does_not_escalate_a_stale_fingerprint`); wiederhergestellt
  (sha256 vor == nach, byte-identisch) → `RC=0`, 5 Tests grün.
- **Herkunft:** Die Verdrahtung stammt aus der `tested → wired`-Lücke, die diese Runde selbst
  gefunden hat; sie war bereits verifiziert und committet. **Den Rot-Beweis hat der beauftragte
  Leaf nie geliefert** (Unterbrechung ohne Commit); die Zentrale hat ihn nachgefahren.
- **Restgrenze:** Die Bindung der Panel-/Plugin-Aufrufer an `session=` ist Adoption, keine
  Signatur — in `docs/VERIFICATION-CLOSURE.md` als Block-3-Restgrenze geführt.

## 8. Regel für Status-Upgrades

Ein Feature wird **erst** hochgestuft, wenn der Beleg existiert:
`exists` (Codepfad) → `wired` (echte Runtime-Verbindung) → `tested` (Test läuft grün, Rot-Beweis
vorhanden) → `live-verified` (echter Lauf/Produktbeweis). Agenten-Behauptungen sind keine Belege —
jeder Block wird nach Rückmeldung selbst nachgemessen (pytest + ruff + `git show --stat`).
