# Browser-State-Liveness-Reconcile — State-Ehrlichkeit der Browser-Sitzung

Feature: `browser-state-liveness-reconcile` (m4-web). Fix des gemessenen
State-Ehrlichkeits-Defekts aus browser-research-acceptance (2026-10-06,
`logs/browser-research/step15+step16`): nach dem Out-of-Band-Tod des Session-Browsers
blieb die State-Datei bei `state=ready / starts=1 / last_launch_attempt` gefroren
stehen, und der nächste Transport-Aufruf startete den Browser per Call neu
(`lifecycle.launched: true`, neue targetId), OHNE dass das Produkt ihn beobachtete —
der Start-Zähler verpasste einen echten Launch.

Status: **ABGESCHLOSSEN (implementiert und live-verifiziert über die echte CLI)**.
Repo-Stand bei Reproduktion: `9b4e77b`; Fix-Commit: `5f25ab5`.

## 1. Failing-first-Reproduktion (PRE-FIX, gegen 9b4e77b)

Rohdaten: `logs/browser-liveness/step01-prefix-repro.txt`. Ausgangslage (gemessen,
19:02): KEIN Chromium-Prozess auf dem Host, `agent-browser session list` →
`{"sessions":[]}` — und dennoch meldete die echte CLI:

```
uv run skaldir browser discover   → exit 0
  "state": "ready", "session_id": "skaldir", "starts": 1,
  "last_launch_attempt": 1791305147.3242345   ← gefroren (Stand der Acceptance 18:45)
```

Ein nacktes `ready`, das logisch falsch war — exakt der gemeldete Defekt.

## 2. Gemessene Liveness-Signale (0.38.1, live, `step02/step03`)

Experimente mit dem projektlokalen Binary (`node_modules/.bin/agent-browser` 0.38.1):

| Situation | `session info` | `session list` | Verdict-Quelle |
|---|---|---|---|
| Browser lebt | `active:true, pid=X, runtime.browserLaunched:true, pageCount:1` | Session gelistet | `browserLaunched` |
| Graceful `close`, t+0s | **`active:true` (stale!), `browserLaunched:false`, pageCount:0** | Session gelistet (stale) | `browserLaunched` |
| Out-of-Band SIGKILL des Browsers, t+1s | `active:true` (stale), **`browserLaunched:false`, pageCount:0** | Session gelistet (stale) | `browserLaunched` |
| Session existierte nie | `active:false, pid:null, runtime:null` | — | `active` |

**Befund:** `data.runtime.browserLaunched` ist der ehrliche, NICHT-mutierende
Liveness-Signal — unmittelbar nach Close UND nach Out-of-Band-Kill korrekt, während
`active`/`session list` kurz stale bleiben können. Und: jeder Transport-Aufruf
(`tab list`, `open`, `read`) trägt `data.lifecycle.launched:true` GENAU dann, wenn er
den Browser selbst neu gestartet hat; bei laufendem Browser `launched:false,
reused:true`. Roh-Envelopes in `step02/step03`.

## 3. Der Fix (Commit 5f25ab5)

- `AgentBrowserBackend.session_info()`: `session info --session <s> --json` — die
  billige, nicht-mutierende Liveness-Lesung.
- `session_alive()`: Verdict liest JETZT `session info` (startet nichts). Alt:
  `tab list` — was agent-browser dazu verleitete, DEN Browser neu zu starten, nach
  dem es gefragt wurde (stumme Blind-Launches, auch im unconfirmed-Recovery-Pfad).
  Neu: stale-`active` + `browserLaunched:false` / nie-existiert / toter pid → `False`;
  unusabler Transport → `None` (kann nicht sagen — nie ein Guess).
- `tabs()/read()/act()` liefern `relaunched` aus dem gemessenen Lifecycle-Envelope.
- `SessionManager.discover()/ensure()`: ein `ready`-Zustand wird gegen den Verdict
  reconciliert — tot → `state=unknown` + `live:false` + Diagnosis (NIE nacktes ready),
  State-Datei bekommt `reconciled_dead_session/reconciled_diagnosis/reconciled_at`;
  `ensure` startet danach SELBST und ZÄHLT den Start. Live → `reused, live:true`;
  Verdict `None` → konservativer Reuse mit benanntem Caveat. Reconciliation-Writes
  nutzen nicht-blockierendes flock (kein Clobber eines laufenden Starts, kein Deadlock).
- `SessionManager.record_observed_relaunch()`: ein vom Produkt beobachteter Per-Call-
  Relaunch wird gezählt (`starts+1`, `last_launch_attempt` bewegt sich).
- `core_capabilities` Browser-Handler: beobachtete Relaunches in Transport-Ergebnissen
  (tabs/read/act) werden über die State-Datei verbucht.
- **Unverändert (bewusst):** Ownership/Cleanup — fremde Browser werden nie geschlossen,
  kein `close --all`, nur aufgezeichnete Agent-Tabs sind closable (BR-13).

## 4. Live-Nachweis über die echte CLI-Oberfläche

Alle Envelopes roh in `logs/browser-liveness/step03…step07` (echte Exitcodes,
`uv run skaldir …`, kein `| tail`-Maskieren; bewiesener Eintritt: Repo-venv-CLI).

| Schritt | Befehl | Exit | Kernaussage |
|---|---|---|---|
| step03 | `browser discover` (Defekt-State, kein Browser) | 0 | `state=unknown, live=false` — reconciliert, NICHT nacktes ready; starts blieb 1 |
| step03 | `browser ensure` | 0 | `action=launched, starts 1→2`, `last_launch_attempt` bewegt (1791306790.4) |
| step03 | `browser discover` | 0 | `state=ready, live=true, starts=2` |
| step04 | Out-of-Band `agent-browser --session skaldir close`, dann `browser discover` | 0 | **Kernfix live:** `state=unknown, live=false`, Diagnosis nennt Session + Messung (`browserLaunched=false`); State-Datei: `reconciled_dead_session=skaldir` |
| step04 | `browser ensure` | 0 | `action=launched, starts 2→3` — eigener gezählter Start statt Blind-Relaunch |
| step05 | 10× `browser ensure` GLEICHZEITIG (10 Prozesse) | 10×0 | **VAL-WEB-003-Merge live:** 10× `reused`, `starts` bleibt 3, `live=true` — kein einziger Spurious-Start |
| step07 | `browser tabs` / `browser detach` | 0/0 | Inventar + `closed_agent_tabs=[]`, `foreign_browsers_closed=false` |
| step06 | `tools/browser_state_liveness_proof.py` (isoliertes `SKALDIR_HOME`, echter Transport) | 0 | checks: ensure zählt (0→1), dead-ready reconciliert, ensure zählt eigenen Launch (1→2), Per-Call-Relaunch surfaced + gezählt (2→3) — 7/7 true |

## 5. Gates

- `tests/test_browser_state_liveness.py`: 17 Fälle, failing-first geschrieben (15 F
  gegen den unfixed Stand gemessen), danach grün. Envelopes = die GEMESSENEN
  (Prozessgrenze gestubbt, Verdict-/Reconciliation-/Counter-Logik echt).
- Browser-Bereich: `test_browser_layer + test_browser_session_reuse +
  test_browser_tool_handler + test_browser_backend` → 50 grün (Vorher) / 27 grün
  (Backend+Liveness Nachher, inkl. 2 Echt-Transport-Tests mit neuem Session-Cleanup).
- `uv run ruff check src tests` → clean.
- Full suite: `uv run python -m pytest tests/ -q -o addopts=''` →
  **8412 passed, 33 skipped, 0 failed** in 32:00 (Exit 0; Rohlog
  `logs/browser-liveness/full-suite.log`; die 2 Warnungen sind Altlasten:
  starlette-Deprecation + getpass in test_model_picker — beide ohne Bezug zu dieser
  Änderung).

## 6. Nebeneffekt behoben + Aufräumen

- Die Echt-Transport-Tests in `tests/test_browser_backend.py` hinterließen pro
  Suite-Lauf zwei Browser-Sessions (`pytest-launch`/`pytest-read`, gemessen 19:14) —
  Teardown-Fixture `close_real_sessions` schließt nur diese eigenen Namen.
- Aufräumen (gemessen): Produkt-Session `skaldir` geschlossen (über den Proof-Script-
  finally und wieder via CLI-Beweis), eigene Test-Sessions geschlossen;
  `agent-browser session list` → `{"sessions":[]}` (19:20).
- Real-State-Datei am Ende: `state=unknown, live=false` — ehrlich (kein Browser lebt),
  kein nacktes ready mehr.
