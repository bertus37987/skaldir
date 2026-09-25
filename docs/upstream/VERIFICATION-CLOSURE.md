# Skaldir Agent — Verification Closure (Block 3)

**Datum:** 13.09.2026 · **Branch:** `godsv-supervisor-hardening`
**Grundlage:** `docs/IMPLEMENTATION-PLAN-live-control-plane.md` (Block 3) · `docs/IMPLEMENTATION-MATRIX.md` P0-Punkte 5, 6, 7, 8 ·
`docs/COMPLETE_AUDIT-2026-09-13.md` (VOICE-22 / VOICE-26 / VOICE-27 / STATE-3, Top-5 #1+#2)
**Dateien:** `src/godsv/completion.py`, `src/godsv/gates.py`, `tests/test_verification_closure.py`
**Regel:** Jede Zusage ist als **Requirement → Implementation → Evidence → Verification → Verdict** abbildbar.
Fertig = mindestens `wired + tested`; `live verified` wo die Umgebung real vorhanden ist — und **ehrlich als
nicht-zertifizierbar** markiert, wo sie es nicht ist.

---

## 0. Ergebnis in einem Satz

Die fünf kanonischen Check-IDs (`run`, `ui_e2e`, `api`, `build`, `persistence_restart`) haben jetzt echte Runner
mit einem einzigen Result-Contract (Exit 0 **und** explizite `PASS`-Zeile), der `gate_tests`-Deadlock ist ohne
Semantik-Aufweichung aufgelöst, der Evidenz-Zaun ist für leere Workspace/Revision/Session geschlossen, und ein
erreichtes `VERIFIED_COMPLETE` kann nicht mehr ohne neues Goal aufgelöst werden. Alles ist rot-bewiesen und grün.

| Nachweis | Kommando | Ergebnis |
|---|---|---|
| Eigene Tests | `.venv/bin/python -m pytest tests/test_verification_closure.py -q` | **49 passed** (49 gesammelt) |
| Volle Suite (ohne Block 6) | `.venv/bin/python -m pytest tests -q --ignore=tests/test_capability_learning.py` | **1076 passed** |
| Lint | `.venv/bin/ruff check src tests tools integrations app-overlay` | **All checks passed!** |
| Rot-Beweise (6 Punkte) | `.venv/bin/python /tmp/block3_red_proofs.py` | jeder Punkt: echtes FAIL (exit 1) → wiederhergestellt → grün |
| Live-Kette | `.venv/bin/python /tmp/block3_live.py` | Verdikt + Deadlock-Probe + Ehrlichkeit + Terminalzustand (siehe §6) |

---

## 1. Runner-Matrix — was jeder Runner **wirklich** tut

Alle Runner laufen über **einen** Contract (`completion.check_verdict`, `completion.py:1804`):

> **PASS** ⇔ Exit-Code 0 **und** eine Zeile, die mit `PASS` beginnt.
> Eine explizite `FAIL`/`ERROR`-Zeile gewinnt gegen Exit 0 („FAIL: check did not PASS" bei Exit 0 ist **kein** Pass).
> Alles Unklare (kein Exit-Code, kein PASS, Timeout, `-127`) fällt **fail-closed** auf `failed` bzw. `not-certifiable`.

| Check-ID | Was der Runner wirklich tut | PASS-Kriterium | Grenzen |
|---|---|---|---|
| `pytest` / `tests` / `test` | Führt `python -m pytest -q` im Workspace aus; bevorzugt `<workspace>/.venv/bin/python`, sonst den laufenden Interpreter (`_pytest_runner_evidence`, `completion.py:2149`). | Exit 0. Zusätzlich `suite=True` gestempelt (Identitätsmerkmal für `gate_tests`). | Kein Venv/keine Tests ⇒ Exit 5/Fehler ⇒ `failed`. Kein Netz nötig. Kein PASS-Zwang, weil pytest selbst das Verdikt über den Exit-Code spricht (bewusste Ausnahme, dokumentiert). |
| `run` | Führt den **deklarierten** Befehl im Workspace aus, sandboxed (`bwrap`, `--unshare-net`) über `gates.run_declared_check` (`gates.py:242`), cwd = Workspace (`_declared_check_evidence`, `completion.py:1928`). | Exit 0 **und** `PASS`-Zeile. | Braucht eine Deklaration (§2). Ohne Sandbox: `not-certifiable` (es wird **nie** unsandboxed ausgeführt). Netz ist im Sandbox-Namespace abgeschaltet ⇒ netzabhängige Checks sind per Design nicht zertifizierbar. |
| `api` | Identisch zu `run` — derselbe Runner, eigene Deklaration. Der Name ist die Identität, nicht ein anderer Mechanismus. | Exit 0 **und** `PASS`-Zeile. | Ein API-Test gegen einen laufenden Server ist im `--unshare-net`-Sandbox nicht erreichbar; er muss lokal/mockbar sein, sonst ehrlich `not-certifiable`. |
| `build` | Identisch zu `run`; führt den deklarierten Build aus (schreibt Artefakte in den Workspace, im Live-Beweis nachgewiesen: `dist/artifact.txt`). | Exit 0 **und** `PASS`-Zeile. | Erkennt **nicht** selbst ein `package.json`/`pyproject.toml`; ein Build ohne Deklaration ist `not-certifiable` (kein Raten). |
| `ui_e2e` | Identisch zu `run`: führt den deklarierten UI/E2E-Befehl aus, wenn einer deklariert ist. | Exit 0 **und** `PASS`-Zeile. | Die **echte Electron-App** wird nicht selbst gestartet (kein Build, kein CDP-Port, kein App-Restart — Nicht-Ziel dieses Blocks). Ohne deklarierten Befehl: `not-certifiable` mit Grund. Der echte App-Probe (`tools/gv_ui_e2e.mjs`) bleibt dem §40-E2E vorbehalten. |
| `persistence_restart` | Dreiphasen-Zyklus (`_persistence_cycle_evidence`, `completion.py:1996`): **1.** `GODSV_CHECK_RESTART_MARK_CMD` erzeugt den Zustand, **2.** `GODSV_CHECK_RESTART_ASSERT` benennt die Zustandsdatei, **3.** `GODSV_CHECK_RESTART_CMD` ist der „Restart". PASS nur, wenn die Restart-Stufe `PASS` meldet **und** der SHA-256 der Zustandsdatei sich messbar geändert hat. | Exit 0 **und** `PASS`-Zeile **und** beobachtete Zustandsänderung. | Ohne alle drei Deklarationen: `not-certifiable`. Ein Restart, der nichts verändert, ist `failed` („no restart evidence"). Gestartet wird kein echter Prozess/keine echte App — der Runner prüft die **deklarierte** Zyklus-Prozedur, nicht den Produkt-Restart selbst. |
| registrierte Testing-Tools (z. B. `check_readme`) | Unverändert aus Block 1/6: `register_testing_tools` registriert validierte Tools, `_testing_tool_runner` erzeugt `source="runner"`-Evidenz. | Exit 0 **und** PASS-Zeile (`parse_testing_result`, `gates.py:316`). | Bleibt fail-closed; die Registrierung verlangt `validation.status == validated`. |
| unbekannte IDs | Kein Runner. `execute_check` gibt `None` zurück. | — | `gate_dod` meldet den fehlenden Prüfplan blockierend. |

**Wichtige Unterscheidung, die vorher fehlte:** `failed` (der Check **lief** und scheiterte) vs.
`not-certifiable` (der Check **konnte nicht laufen** — kein Sandbox, keine Deklaration, fehlende Binary).
Beide zertifizieren **nie**, aber nur der erste ist ein Arbeitsauftrag an das Produkt. Der Blocker-Text in
`gate_dod` nennt den Grund im Klartext (`completion.py:1413-1421`).

---

## 2. Deklaration: wie ein Workspace einen Check definierbar macht

Ein deklarierter Check kommt aus dem **Workspace selbst** — er ist damit Fremdcode und läuft ausschließlich
sandboxed.

**Weg A — `.godsv/checks.json` im Workspace (bevorzugt, persistent):**

```json
{
  "run":   {"argv": ["/pfad/.venv/bin/python", "smoke.py"], "timeout": 120},
  "build": {"argv": ["npm", "run", "build"], "timeout": 600},
  "ui_e2e": {"argv": ["node", "tools/gv_ui_e2e.mjs", "9333"], "timeout": 300},
  "persistence_restart": {"argv": ["python", "restart_probe.py"], "assert": "tmp/state.txt",
                          "timeout": 300}
}
```

**Weg B — Umgebungsvariablen** (prozesslokal, für Läufe/Proben): `GODSV_CHECK_RUN_CMD`,
`GODSV_CHECK_UI_CMD`, `GODSV_CHECK_API_CMD`, `GODSV_CHECK_BUILD_CMD`,
`GODSV_CHECK_RESTART_CMD` (+ `_MARK_CMD`, `_RESTART_ASSERT`).

Die Deklaration wird **nur** relativ zum Workspace gelesen (`<repo>/.godsv/checks.json`). Eine unlesbare oder
kaputte Datei gilt als „nichts deklariert" ⇒ `not-certifiable`, nie still grün (`_declared_spec`, `completion.py:1836`).

---

## 3. `gate_tests`-Deadlock — vorher / nachher mit Beleg

### Der Deadlock (vorher)

`gate_tests` (`completion.py:1080`, vorher `:1042`) verlangte bei **geändertem Code** Runner-Evidenz mit der
Check-ID `pytest`:

```python
tests = [e for e in evidence if (e.check_id or e.name) in ("pytest", "tests", "test")]
runner_tests = [e for e in tests if e.source == "runner"]
if code_changed and not runner_tests:
    findings.append(Finding(gate="tests", severity=BLOCKER, ...))
```

Der Runner (`run_requirement_checks`) führte aber **nur die im DoD deklarierten** Checks aus. Ein Contract ohne
`pytest`-Kriterium konnte also nie Runner-pytest-Evidenz erzeugen; ein grüner `pytest`-Lauf **in der Session** ist
`source="events"` und zählt nicht. Ein Goal ohne pytest-Kriterium bei schmutzigem Repo war damit **unclosbar** —
für den Nutzer sah der fail-closed-Deadlock wie ein Hänger aus (P0 #7/#8, VOICE-26/27).

### Der Fix (nachher), ohne Gate-Aufweichung

Zwei komplementäre, jeweils eigenständig rot-bewiesene Änderungen:

1. **Producer-Seite:** `run_requirement_checks` (`completion.py:2248`) fügt den Suite-Lauf selbst hinzu, wenn der
   Workspace geänderten Code trägt und der Contract keinen Suite-Check deklariert:
   ```python
   if not any(str(check).lower() in ("pytest", "tests", "test") for check in wanted):
       if workspace_has_code_changes(repo_root):
           wanted.append("pytest")
   ```
   Der Gate-Satz bleibt unverändert — es **existiert jetzt nur endlich eine Quelle**, die ihn erfüllen kann.
2. **Gate-Seite:** `gate_tests` akzeptiert Evidenz nach dem **ausgeführten Befehl**, nicht nur nach dem Label.
   `RunEvidence.suite` (bzw. der Kommandotext) entscheidet, ob wirklich ein Test-Runner lief.

**Was ausdrücklich NICHT passiert ist:** Event-Evidenz zertifiziert weiterhin nicht (der Test
`test_events_only_green_pytest_run_does_not_clear_gate_tests` bleibt grün), ein Befehl ohne Test-Runner zählt
nicht (`test_a_non_pytest_command_cannot_pose_as_the_suite`), ein fehlgeschlagener Suite-Lauf blockt weiter
(`test_failed_suite_run_still_blocks`), und ohne jeden Suite-Lauf blockt das Gate
(`test_code_change_without_any_suite_run_still_blocks`).

### Beleg (live, `/tmp/block3_live.py` §3)

```
=== 3) Deadlock-Probe: Contract ohne pytest-Kriterium, dreckiges Repo ===
  runner ids: ['run', 'pytest']          ← der Produzent ergänzt den Suite-Lauf selbst
    run                    failed     …/.venv/bin/python smoke.py
    pytest                 passed     python -m pytest -q
  status=INCOMPLETE
  gate_tests-Blocker: KEINE (Deadlock geloest)
```

Die Statuszeile bleibt `INCOMPLETE` — weil das `run`-Kriterium im geänderten Workspace **echt** scheiterte. Genau
das ist der Unterschied zwischen „Gate blockt aus gutem Grund" und „Gate ist unerfüllbar".

### Rot-Beweis

| Beweis | Deaktivierter Guard | echtes Rot | grün danach |
|---|---|---|---|
| P2a | `if code_changed and not runner_tests and not suite_runs:` → `…not runner_tests:` | `FAILED …::test_executed_command_identity_clears_gate_tests` (exit 1) | exit 0 |
| P2b | Auto-Suite-Ergänzung auf `if False:` | `AssertionError: ['run'] == ['run', 'pytest']` (exit 1) | exit 0 |

---

## 4. Evidenz-Bindung — der Workspace-Bypass ist zu

`RunEvidence` (`completion.py:194-219`) trägt jetzt zusätzlich **`session`** (Session-Id der Erzeugung) und
**`suite`** (der Check hat wirklich einen Test-Runner aufgerufen). Der Zaun (`_evidence_bound`,
`completion.py:1344`) ist fail-closed:

```python
wanted_workspace = (self.workspace or "").strip()
evidence_workspace = (e.workspace or "").strip()
if self.revision and int(e.revision or 0) != self.revision:
    return False
if wanted_workspace:
    if not evidence_workspace or evidence_workspace != wanted_workspace:
        return False
wanted_session = (self.session or "").strip()
evidence_session = (e.session or "").strip()
if wanted_session:
    if not evidence_session or evidence_session != wanted_session:
        return False
```

**Was vorher der Bypass war:** `if self.workspace and e.workspace and e.workspace != self.workspace` — eine
Evidenz mit **leerem** `workspace` passierte den Zaun, obwohl der Runner einen Workspace setzte (Audit-Probe:
`A empty-workspace evidence -> NO FINDING (bypass!)`). Ebenso fehlte jede Session-Bindung am Evidenz-Objekt.

**Bindung im Betrieb:** `GateRunner.verify` (`completion.py:1441`) übernimmt die Session des session-scoped Stores
und stempelt ungestempelte Runner-Evidenz in **diese** Session — Evidenz mit einer **fremden** Session wird
abgelehnt, Evidenz ganz ohne Session wird in die laufende Session adoptiert (nicht: akzeptiert, egal woher).
`execute_check`/`run_requirement_checks` stempeln Revision, Workspace und Session selbst.

**Rot-Beweis P3** (beide Guards entfernt):

```
E       assert ([])
FAILED …::TestEvidenceBinding::test_empty_workspace_evidence_does_not_certify
FAILED …::TestEvidenceBinding::test_foreign_session_evidence_does_not_certify
```

Grün danach — plus die parametrisierten Nachbartests: leere Workspace, `None`-Workspace, fremder Workspace,
fremde Session, fehlende Session, alte Revision werden **alle** abgelehnt; ungebundene Legacy-Aufrufer
(`revision=0`, `workspace=""`, `session=""`) behalten ihr altes Verhalten (kein falscher Negativ).

---

## 5. Terminalzustand — kein stiller Freeze

`CompletionStore.transition` (`completion.py:454`) hatte ein Loch: `record.get("state") in TERMINAL_STATES and
state != WORKING` erlaubte den Übergang `VERIFIED_COMPLETE → WORKING`. Damit ließ sich ein **erreichtes und
zertifiziertes** Ergebnis ohne neues Goal wieder auf `WORKING` setzen — der Zustand wirkte wieder bearbeitbar,
die alte Revision blieb stehen (P0 #5).

Die Invariante (`completion.py:471-491`): `VERIFIED_COMPLETE → WORKING` ist nur mit **neuer Goal-Identität**
erlaubt — also mit `goal` **und** explizitem `revision` **und** anderem Goal-Text. Der reguläre Weg
`begin_goal` setzt Revision 1 und erfüllt das. Alles andere wird abgelehnt:

```python
if previous == VERIFIED_COMPLETE and state == WORKING:
    if "goal" not in extra: raise ValueError(… "begin a new goal (a new goal identity)" …)
    if revision is None:    raise ValueError(… "reopening needs a new goal with an explicit revision" …)
    if int(revision) <= 0 or goal == previous_goal:
        raise ValueError(… "a new goal needs a new identity … not a rename" …)
```

Andere terminale Zustände behalten ihre eigenen Regeln (`BLOCKED_USER → WORKING` bleibt möglich — ein freigegebener
Blocker ist kein abgeschlossenes Ergebnis).

**Rot-Beweis P4** (Guard auf `if False`): `Failed: DID NOT RAISE <class 'ValueError'>` ×2 (exit 1), grün danach.
**Live-Beleg** (`/tmp/block3_live.py` §5):

```
  abgelehnt: state VERIFIED_COMPLETE is terminal — begin a new goal (a new goal identity) to continue
  state danach: VERIFIED_COMPLETE / goal=prove verification closure
```

---

## 6. Ehrlichkeit: deklariert-aber-nicht-verfügbar = `not-certifiable`

Der gefährlichste Ausgang wäre ein Exit 0 auf einer Umgebung, die gar nicht prüft. Deshalb ist **jede**
Nicht-Ausführbarkeit explizit:

| Fall | Verdikt | Detailtext (Auszug) |
|---|---|---|
| Keine Deklaration | `not-certifiable` | „`ui_e2e` is declared but not available here — no declaration — set GODSV_CHECK_UI_CMD or add it to .godsv/checks.json" |
| Binary fehlt | `not-certifiable` | „declared executable missing: /nonexistent/binary" |
| Kein `bwrap`/Sandbox | `not-certifiable` | „sandbox unavailable — declared check not executed (fail-closed)" |
| Restart-Zyklus unvollständig | `not-certifiable` | „GODSV_CHECK_RESTART_MARK_CMD, …_ASSERT und …_CMD must all be set" |
| Restart lief, Zustand unverändert | `failed` | „restart reported PASS but the state file did not change — no restart evidence" |

**Rot-Beweis P5:** `_declared_check_evidence` gab bei fehlender Deklaration `None` zurück (stille Lücke) →
`AssertionError: a canonical check id must never be missing entirely` ×3 (exit 1), grün danach.

---

## 7. Rot-Beweis-Protokoll (wirklich ausgeführt, nicht behauptet)

Ausgeführt mit `/tmp/block3_red_proofs.py`: pro Punkt wird **genau ein** Guard im Quellcode temporär deaktiviert,
der echte Fehlschlag gezeigt, die Datei **in-place wiederhergestellt** (kein `git stash`), dann grün. Ein
pytest-Aufruffehler (Exit 4) zählt ausdrücklich **nicht** als Rot-Beweis und wird als PROBLEM gewertet.

```
OK (real red, then green)  red=1 green=0  P1 Runner-Luecke run/ui_e2e/api/build (kein Runner)
OK (real red, then green)  red=1 green=0  P2a gate_tests zaehlt nur die Check-ID pytest
OK (real red, then green)  red=1 green=0  P2b gate_tests: kein Producer fuer den Suite-Lauf
OK (real red, then green)  red=1 green=0  P3 Evidenz-Bindung: leerer Workspace / fremde Session
OK (real red, then green)  red=1 green=0  P4 Terminalzustand: VERIFIED_COMPLETE wieder auf WORKING
OK (real red, then green)  red=1 green=0  P5 not-certifiable Ehrlichkeit (statt None-Luecke)

all points: real red failure then green: True
```

Zusätzlicher Rot-Beweis auf Testebene: `tests/test_verification_closure.py` wurde **vor** der Implementierung
geschrieben und lief rot — **37 FAILED** gegen den Block-1-Stand (u. a. `check_verdict` unbekannt,
`RunEvidence.session` fehlt, `not-certifiable` nicht existent, `['run'] == ['run', 'pytest']`); erst danach
entstand der Fix. Messung: `git show HEAD:src/godsv/{completion,gates}.py` temporär eingespielt → 37 FAILED,
Fix zurückgespielt → 49 passed.

---

## 8. Status je Achse

| # | Zusage | exists | wired | tested | live-verified | Restgrenze |
|---|---|---|---|---|---|---|
| 1 | Runner für `run`/`ui_e2e`/`api`/`build` | ✅ | ✅ `execute_check`/`run_requirement_checks` | ✅ 12 Tests (`TestDeclaredRunners` 8, `TestPassContract` 4) | ✅ Deklaration ausgeführt (`.godsv/checks.json` + Env), Build schrieb echtes Artefakt | Braucht Deklaration; kein Selbst-Erkennen von package.json/pyproject.toml |
| 2 | Runner für `persistence_restart` | ✅ | ✅ Dreiphasen-Zyklus | ✅ 3 Tests (`TestPersistenceRestartRunner`) | ⚠️ Zyklus-Mechanik live, **Produkt-Restart nicht** | Startet keine echte App; Zustandsänderung per SHA-256, kein Prozess-Restart |
| 3 | Einheitlicher PASS-Contract | ✅ `check_verdict` | ✅ alle Runner | ✅ 4 Tests (`TestPassContract`) + `test_silent_success_is_failed_closed`, `test_fail_verdict_at_exit_zero_is_failed` | ✅ (FAIL bei Exit 0 in Live-Probe) | pytest selbst nutzt Exit-Code statt PASS-Zeile (bewusste, dokumentierte Ausnahme) |
| 4 | `not-certifiable` statt falschem Bestehen | ✅ | ✅ alle Runner | ✅ 8 Tests (`TestHonestNotCertifiable`, inkl. 4× parametrisiert) | ✅ `ui_e2e` ohne Env → `not-certifiable` | Grund ist ein Freitext-Detail, kein Fehlercode |
| 5 | `gate_tests`-Deadlock gelöst | ✅ | ✅ Producer + Gate | ✅ 9 Tests (`TestGateTestsDeadlock`) | ✅ Live: `['run','pytest']`, keine `gate_tests`-Blocker | Auto-Suite-Lauf nur bei geändertem Code |
| 6+7 | Evidenz-Bindung (Workspace + Session) | ✅ `RunEvidence.session` | ✅ `_evidence_bound` + Session-Adoption in `verify` | ✅ 10 Tests (`TestEvidenceBinding`, ein Klasse für beide Achsen) | ✅ Bypass geschlossen, fremde Session abgelehnt | Keine kryptografische Signatur; Session-Adoption für Legacy-Aufrufer |
| 8 | Terminalzustand-Invariante | ✅ | ✅ `transition` | ✅ 5 Tests (`TestTerminalInvariant`) | ✅ abgelehnt, Goal+Revision bleiben | Nur `VERIFIED_COMPLETE`; andere Terminalzustände unverändert |
| 9 | Harter Timeout, keine Netzabhängigkeit | ✅ `run_declared_check` | ✅ `subprocess timeout` + `--unshare-net` | ✅ `test_hard_timeout_fails_instead_of_hanging` + Sandbox-Tests | ✅ Timeout → `failed` (nicht hängend) | Netz-Checks sind damit per Design nicht zertifizierbar |
| 10 | Doku | ✅ diese Datei | — | — | — | — |

**Gesamt:** 49 Tests in `tests/test_verification_closure.py` (4+8+8+3+2+9+10+5 = 49; `TestRequirementChecksUseTheRunners` 2
deckt die Kette Deklaration→Runner→Gate ab und ist in Zeile 1 mitgezählt).

### Offene Restgrenzen (bewusst, nicht versteckt)

1. **Kein Produkt-Restart im `persistence_restart`-Runner.** Er verifiziert die *deklarierte* Write→Restart→Verify-Prozedur
   inklusive beobachteter Zustandsänderung, startet aber weder die GV-App noch einen zweiten Prozess. Der echte
   App-Restart-Beweis bleibt §40 vorbehalten (Instanz-Schutz: nicht neu starten/neu bauen).
2. **`ui_e2e` startet die echte Electron-App nicht** (kein Build, kein CDP-Port). Mit Deklaration läuft der reale
   `tools/gv_ui_e2e.mjs`-Lauf; ohne Deklaration ist das Ergebnis ehrlich `not-certifiable`.
3. **Der Suite-Erkennungs-Heuristik (`_SUITE_HINT`) ist textbasiert.** Ein Befehl, der `pytest` nur *nennt*
   (z. B. in einem Argument), könnte als Suite-Lauf gelesen werden. Die Lookahead-Grenze (`(?<![\w-])`) verhindert
   den praktisch häufigen Fall (`/tmp/pytest-of-...`); ein absichtlich irreführender Befehl bleibt möglich —
   deshalb stempeln die eigenen Runner `suite` explizit und der Fall ist hier dokumentiert.
4. **Session-Bindung ist Adoption, keine Signatur.** Evidenz ohne Session-Stempel wird in die laufende Session
   adoptiert (statt einen Alt-Wert zu erfinden) — eine bewusste Kompatibilitätsentscheidung für Legacy-Aufrufer.
5. **Die Panel-/Plugin-Aufrufer übergeben `session=` noch nicht explizit.** Die Bindung greift trotzdem, weil
   `verify()` die Session des session-scoped Stores übernimmt. `integrations/hermes/godsv_plugin/**` gehört
   Block 4/5 und wurde in diesem Block nicht angefasst (Datei-Eigentum); die explizite Übergabe ist dort ein
   Ein-Zeilen-Follow-up.
6. **`gate_tests` läuft weiterhin nur bei geändertem Code.** Ein sauberer Baum braucht keinen Suite-Lauf; die
   Bindung an die Revision verhindert, dass alte Evidenz eine neue Revision zertifiziert.

---

## 9. Nicht-Ziele dieses Blocks (unverändert)

Keine zweite Agent-/Tool-Runtime · keine Änderung der Gate-Semantik zugunsten eigener Zertifizierung · kein
Start/Neustart/Build der GV-App · keine Änderung an Dateien anderer Blöcke
(`capability_mining.py`, `capability_health.py`, `liveinsert.py`, `toolbuilder.py`, `monitor.py`, `pause.py`,
`events.py`, `untrusted.py`, `patterns.py`, `toolcall.py`, `voice.py`, `integrations/**`).
