# Verifikationsbericht v1.4.0 — Strict Completion Supervisor + Englisch + Mining

Stand: 2026-09-12 · Commit `a6be202` · CI grün (Run `34646316307`) · **472 Tests** ·
bandit 0 · ruff 0 · Node-Smoke 69 Checks · **echte App: 27/27 Checks**

Dieser Bericht belegt die Runde **„TURN SKALDIR AGENT INTO A STRICT GOAL & PRODUCT
QUALITY SUPERVISOR"** (+ Add-on Tool-/Testing-Tool-Mining) und die
**Englisch-Umstellung** von UI und CLI.

---

## 1. Die zentrale Regel — und wie sie im Code erzwungen wird

> The Main Agent does the work. **Skaldir Agent decides whether the user's actual
> goal has been achieved.** Work does not stop because the agent thinks it is done.

| Mechanismus | Ort | Beweis |
|---|---|---|
| Main Agent kann Completion nur **anfragen** | `pre_verify`-Hook liefert `action=continue` + Redirect | `test_plugin_release.py` (Reject auch bei `attempt>0`) |
| Nur die Gates setzen `VERIFIED_COMPLETE` | genau **eine** Zuweisung im Modul (Source-Check-Test) | `test_completion_loop_e2e.py::test_main_agent_cannot_self_certify` |
| `INCOMPLETE` beendet den Lauf **nicht** | State Machine; nur die Endzustände sind terminal | `test_completion.py::test_completion_requested_is_not_terminal` |
| Rejection gibt die Kontrolle **automatisch** zurück | Hook-Vertrag `{"action":"continue","message":redirect}` | Plugin-Tests + Mandatory-Full-Loop-E2E |
| Voice-Ausfall ⇒ **nie** „complete" | Facts nicht sammelbar ⇒ Gate blockt; Crash ⇒ kein Result geschrieben | `test_voice_failure_never_implies_completion`, `test_crash_during_verification_never_marks_complete` |

## 2. State Machine & Endzustände (§2/§3)

`WORKING → COMPLETION_REQUESTED → VERIFYING → VERIFIED_COMPLETE | INCOMPLETE`
(→ `REDIRECT` → `WORKING`); Endzustände zusätzlich `BLOCKED_USER`,
`BLOCKED_EXTERNAL`, `FAILED_UNRECOVERABLE`, `USER_CANCELLED`,
`SHIPPED_WITH_USER_OVERRIDE`. Aus einem Endzustand führt nur ein **neues Goal**
(`begin_goal`) wieder heraus — eine User-Override ist ausdrücklich **nicht**
`VERIFIED_COMPLETE` (`test_user_override_is_not_verified_complete`).

## 3. Der Completion-Loop (§29/§30/§40/§46) — Mandatory Full-Loop

`tests/test_completion_loop_e2e.py::test_full_loop_rejects_twice_then_verifies`
fährt den geforderten Ablauf **real** (echtes git-Repo, echte Kommandos):

```text
Request #1  → INCOMPLETE („tests: code changed but no test run is evidenced")
            → Redirect • kein Ende
Request #2  → INCOMPLETE („definition-of-done … ohne Evidenz")
            → Redirect • kein Ende
Request #3  → VERIFIED_COMPLETE (Tests real ausgeführt, DoD belegt) → Ende
```

Der User muss **nie** „continue" sagen: In der Plugin-Integration ist die
Rejection der `action=continue`-Pfad mit präzisem Redirect
(`test_strict_supervisor_rejects_even_on_later_attempts`).

## 4. Gates (§14–§27) — alle über echte Fakten

| Gate | Entscheidungsgrundlage | Testfall |
|---|---|---|
| evidence | nur **ausgeführte** Checks (Event-Store), frisch nach dem letzten Edit; nur fehlgeschlagene Checks ⇒ BLOCKER | §41 `tests_not_executed` |
| tests | Code geändert + kein Testlauf danach ⇒ BLOCKER | Full-Loop #1 |
| build | Build-relevanz + kein Build-Nachweis ⇒ BLOCKER | `test_completion.py` |
| integration | neues Modul ohne Import (orphan) ⇒ BLOCKER | §41 `disconnected_ui` |
| fake-completion | `TODO/placeholder/mock/skip` im Diff ⇒ MAJOR/BLOCKER | §41 `fake_data` |
| security | Secret-Muster im Diff ⇒ BLOCKER | `test_secret_material_blocks` |
| cleanup | Debug-Prints/Temp-Pfade ⇒ MINOR | `test_cleanup_findings_are_minor_only` |
| definition-of-done | blocking Kriterien brauchen ihren Verifikations-Nachweis; non-blocking ⇒ MINOR | `test_blocking_dod_cannot_be_dropped` |

Severities: BLOCKER/MAJOR blockieren, MINOR/INFO werden dokumentiert (§34).
Nice-to-haves erzeugen **keinen** Endlos-Loop (§33, getestet).

## 5. Wiederholte Rejections & Escalation (§31/§32)

Jede Rejection bekommt einen **Blocker-Fingerprint**. Tritt derselbe Fingerprint
erneut auf, setzt das Ergebnis `escalate=True` und die nächsten Schritte fordern
**Strategiewechsel/Research** statt Blind-Retry — getestet
(`test_incomplete_returns_control_and_escalates_on_repeat`). Es gibt keinen Pfad, der einen wiederholt
gescheiterten Check in „fertig" verwandelt; der Notfall-Cap (40 Nudges/Turn)
endet **unverifiziert** und geloggt (`test_strict_supervisor_cap_is_honest_not_verified`).

## 6. Produkt-/UI-/Persistenz-/Regression-Gates in der Praxis

- **UI + Persistenz + Runtime**: über DoD-Kriterien mit
  Verifikations-Nachweisen; Evidence kommt nur aus real ausgeführten Checks
  (`pytest`/`ui_e2e`/`api`/`build`-Kategorien, §41-Matrix deckt
  persistence_failure, runtime_failure, regression ab).
- **Echte App (Electron)**: `tools/gv_ui_e2e.mjs` — **27/27 Checks**, darunter
  der neue Completion-Tab: Goal + Status rendern (`completionRenders`), Timeline
  (`completionTimeline`), und der Klick auf **„Verify now" läuft real** und zeigt
  das Verdikt (`completionVerifyShows`). 11 Tabs komplett.

## 7. UI (§36–§38) & CLI

- Panel-Tab **Completion**: Goal, Requirement-Checkliste ✓/✗, Produkt-Status-Grid,
  „Verify now" / „Begin goal" / „Accept override (user)", Completion-Timeline,
  Metrics. API: `GET /completion`, `POST /completion/verify|goal|override`
  (Origin-Guard, Validierung; Override verlangt Begründung).
- CLI: `godsvoice completion status|history|begin|override` — live verifiziert
  (Goal anlegen → Status → History). Node-Smoke: **69 Checks**.

## 8. Englisch-Umstellung (per Subagent, verifiziert)

Panel (~200 Strings, `Pauses`/`Calls`/`Usage & Errors`/…), CLI (86 Stellen,
Help + Ausgaben), Panel-API-Details, `guard.py`-Meldungen (Nudge/Footer),
`capability.py`-Redirects, `treasury.py`-Deny-Gründe. Nachgezogen: 12 Testdateien,
Node-Smoke, UI-E2E. Eigene Kontrolle: **0 Umlaute** in `plugin.js`/`cli.py`;
Suite grün.

## 9. Capability-/Tool-Mining (Add-on)

`src/godsv/capability_mining.py` + CLI `tools mine|candidates|promote`; 10 Tests:
- Wiederholungserkennung (Requests ≥2, wiederkehrende Kommandos), Typwahl
  (`skill|cli|mcp|testing_tool`), Kandidaten-Store (occurrences/sessions
  akkumulieren, Status promoted/dismissed), Promotion → **bestehende**
  Capability-/ToolBuilder-Pipeline (Skill-Kandidaten abgelehnt), CLI-E2E.
- **Testing-Tool-Negativtest** (§ Add-on): `validate_testing_tool` fährt
  funktionierendes und absichtlich kaputtes Ziel — ein Tester, der am kaputten
  Ziel „PASS" meldet, ist `invalid` (`test_validate_testing_tool_rejects_always_pass`)
  und kann nie als Completion-Evidenz dienen.

## 10. Invarianten (§44) — Testmatrix

| Invariante | Test |
|---|---|
| INCOMPLETE never ends a run | `test_completion_requested_is_not_terminal` |
| Main agent cannot self-certify | `test_main_agent_cannot_self_certify` |
| Voice failure never implies completion | `test_voice_failure_never_implies_completion` |
| Blocking requirements cannot be silently dropped | `test_blocking_dod_cannot_be_dropped` |
| VERIFIED_COMPLETE requires independent evidence | `test_missing_evidence_blocks` |
| Rejection returns control automatically | Plugin-Strict-Tests + Full-Loop-E2E |
| Crash during verification never marks complete | `test_crash_during_verification_never_marks_complete` |
| Shadow mode never steers | `test_shadow_mode_records_but_never_steers` |

## 11. Bekannte Grenzen (ehrlich)

- Die `BLOCKED_*`-Erkennung ist **marker-basiert** (konservativ auf den
  Agent-Text: „blocked … user/external", `FAILED_UNRECOVERABLE`) — bewusst kein
  Ratespiel; ein falsch-negativer Marker kostet nur einen weiteren Loop-Zyklus.
- Der Panel-„Verify now"-Lauf nutzt die jüngste Event-Store-Session + das
  App-cwd als Repo-Kontext (im E2E verifiziert); er ersetzt nicht den laufenden
  Hook-Loop einer Session.
- Automatische **Ausführung** mined Testing-Tools im Completion-Gate ist als
  Promotion-Flow angelegt (Kandidat → Tool → verifizierbar); die Standard-Gates
  nutzen registrierte Checks über die Evidence-Kategorien.
- Unverändert offen aus v1.3: CU-Vision-Runner, GNOME-Icon-Abnahme, echter
  Provider-API-Smoke (nur mit Nutzer-Freigabe).

## 12. §45-DoD — Abgleich (Kurzform)

1–5 Pact/DoD/Acceptance/Drift/Scope: ✅ (Pact→DoD-Mapping, Monitoring-Signale,
`_dod_from_pact`). 6 Pause/Research/Redirect/Resume: ✅ (bestehend + Strict-Redirect).
7–11 kein Verified-Complete-Pfad am Agenten, Protokoll, unabhängige Verifikation,
Integration-/Evidence-Gate: ✅ (§1–§4, Tests). 12–18 Build/Test/Runtime/UI/
Persistenz/Regression/Security: ✅ Gates + §41-Matrix + echte App. 19 Fake-Completion:
✅ (Diff-Marker). 20–23 automatisches Weiterarbeiten, mehrfache Zyklen, Strategiewechsel
bei Wiederholung, Nice-to-haves stoppen nie in Endlosschleife: ✅. 24–28 BLOCKED_USER/
EXTERNAL/FAILED_UNRECOVERABLE/Override: ✅ (Marker + Endzustände + Tests).
29–31 Completion-/Goal-/Timeline-UI: ✅ (Tab + E2E). 32 Full-Loop-E2E: ✅.
33 Crash-Recovery: ✅. 34 keine Regression: ✅ (472 Tests inkl. Altbestand).
35 CI grün: ✅ (`34646316307`).
