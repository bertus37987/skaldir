# Browser-Session-Reuse — zentraler Ensure-Lifecycle über CLI- und Agent-Pfad

Feature: `browser-session-reuse` (m4-web), erfüllt `VAL-WEB-003` (Sitzungen
wiederverwenden, keine Fensterflut) und schließt die gemessene Lücke aus Scrutiny
m3 Runde 2: der Browser-Handler (Fix 83115f7) baute einen `SessionManager`, benutzte
aber nur `.state_dir` — `ensure()`/`discover()`/der prozessübergreifende Start-Lock
wurden auf dem Agent-Pfad nie gefahren, und das Run-State-Dir (`<run runtime>/browser`)
divergierte vom State-Dir, das `skaldir browser` liest (`<CLI runtime>/browser`).
(Nachtrag zur Feature-Beschreibung: die CLI-Verben heißen `skaldir browser …`
(oberste Ebene), nicht `skaldir desk browser` — der Desk-Parser kennt kein
`browser`; beide gehen über denselben `_browser_sessions`-Konstruktor.)

Implementiert in Commit `60e05b8`.

## Was geändert wurde

`browser_layer.py`:
- `session_state_dir(runtime=None)` — die EINE State-Dir-Auflösung für CLI- und
  Agent-Pfad (explizites `--runtime` skopiert bewusst, Default ist der
  Installations-Runtime-Dir, den beide identisch auflösen).
- `PRODUCT_SESSION_NAME = "skaldir"` — das Produkt fährt eine NAMENSLICHE
  agent-browser-Session, nie `default` (das kann der Nutzer selbst belegt haben).
- `SessionManager.ensure()`: Session-Name geht in `launch()`; der Start-Zähler wird
  im Lock aus der State-Datei gelesen (prozessübergreifend wahr, nicht pro
  Prozess); `unconfirmed` wird durch Liveness-Diagnose aufgelöst (live →
  `reconciled` ohne Start; tot → Zustand freigegeben, bewusster Neustart erlaubt;
  nicht sagbar → bleibt `unconfirmed`, null Starts).
- `BrowserBackend.session_alive()` (Default: „kann nicht sagen“);
  `AgentBrowserBackend` fragt real `tab list`.
- Transport-Fixes (gegen die echte 0.38.1-CLI gemessen): `tabs` hieß vorher
  `agent-browser tabs` — die echte CLI verweigert das („Unknown command: tabs“);
  jetzt `tab list` mit Parsing der `tabId`s. `close_tab` fuhr `close` (ganzer
  Session-Browser) — jetzt `tab close <id>`; `--all` bleibt grundsätzlich verweigert.

`core_capabilities.py` (`_browser_tool`): der Handler fährt jetzt den zentralen
Lifecycle — `ensure()` auf dem geteilten State-Dir, read/act auf der
provisionierten Produkt-Session, agenteneigene Tabs werden recorded (nur die sind
jemals schließbar), leere Beobachtung → `mark_unconfirmed` mit Diagnose (null neue
Starts), Refusals tragen Zustand + Diagnose + `starts` + State-Dir. Das Schema
trägt keinen `session`-Parameter mehr (die würden den zentralen Lifecycle
umgehen); alle Argumente sind Strings wie die Handler-Annotationen.

`surface_cli.py`: `_browser_sessions` hängt jetzt das echte Backend an (davor
konnte `skaldir browser ensure` NIE starten — Refusal „no browser backend“) und
löst das State-Dir über dieselbe zentrale Funktion.

## Nachweise (alle mit echten Exitcodes, Rohdaten unter `logs/browser-reuse/`)

### 1. Failing-First-Reproduktion an HEAD vor dem Fix

`git worktree add --detach /tmp/skaldir-browser-repro b187d02`, das Repro-Skript
(`logs/browser-reuse/repro_head_gap.py`) gegen den Arbeitsbaum-Quellcode:

```
ensure() driven            : 0        (2 echte Handler-Aufrufe)
discover() driven          : 0
agent path state dir       : <tmp>/runtime/browser
CLI (desk browser) state   : /home/willy/.hermes/godsv/browser
state dirs diverge         : True
REPRO_EXIT=0  (Lücke vorhanden)
```

### 2. Zähler-Tests (Hermets, Transport-Stubs)

`uv run pytest tests/test_browser_session_reuse.py …` → 50 passed (Runde 2,
Exit 0):
- 10 gleichzeitige `ensure()` (ThreadPool, echte `SessionManager`-Locks, Zähler
  am Transport-Stub) → genau 1 Launch, 1× `launched`, alle gleiche Session-ID,
  Launch bekam `PRODUCT_SESSION_NAME`.
- Leere Beobachtung → Zustand `unconfirmed` in der State-Datei, Diagnose
  vorhanden; der nächste Handler-Aufruf `reconciled`, Launch-Zähler bleibt 1.
- `unconfirmed` ohne Sagbares → Refusal ohne Start; tot-diagnostiziert →
  freigegebener Zustand, bewusster zweiter Launch (Zähler 2).
- Ownership: nur aufgenommene Agent-Tabs sind schließbar, Nutzertab verweigert,
  kein `close --all`, detach meldet offen gebliebene Tabs.
- `browser` in `build_agent_toolset`, `tool_set_for` und der No-Runtime-Fallback
  je genau 1×; Schema/Handler-Agreement + nicht-triviales Typed-Argument durch
  `_argument_problem` und echten Handler.

### 3. LIVE-Reuse über echte Prozesse (agent-browser 0.38.1, isoliertes
`SKALDIR_HOME=/tmp/skaldir-browser-reuse/home`)

- `skaldir browser probe` → available, Projekt-Install 0.38.1 (Exit 0).
- 10 gleichzeitige `uv run skaldir browser ensure`-Prozesse → **exakt 1×
  `launched`** (live-ensure-4), 9× `reused`, alle Session-ID `skaldir`,
  Start-Zähler 1, alle Exit 0 — der prozessübergreifende Lock hält im echten
  CLI-Pfad.
- Agent-Pfad über das echte Toolset (`tools/browser_session_agent_path_proof.py`,
  realer Backend, echter Read, LLM-Turn gestubbt — Dispatch-Kette echt):
  - `session_action: reused`, Session `skaldir`, State-Dir = CLI-State-Dir;
  - echter Seiteninhalt gelesen (example.com);
  - leere Beobachtung über eine eigene leere Loopback-Seite (about:blank verweigert
    die echte CLI mit „Invalid read URL“ — gemessen) → `state: unconfirmed` +
    Diagnose, Starts bleiben 1;
  - nächster Aufruf `reconciled`, Starts bleiben 1, null neue Starts.
- Cross-Surface: CLI `browser ensure` danach → `reused` (Starts 1); CLI
  `browser discover` liest dieselbe Session — EIN State-Dir, EIN Lock, echter Reuse.

### 4. Ownership/Detach live

- Eigene Nutzer-Simulations-Session in einer FREMDEN agent-browser-Session
  (`live-user-sim`, eigene Ressource) mit Tab auf example.com.
- Agent-Pfad `act(kind=open)` über die echte Handler-Kette → Tab `t1` als
  agent-owned aufgenommen; CLI `browser tabs` zeigt `agent_tabs: [t1]`.
- CLI `browser detach` (ohne --tab) → schließt nichts,
  `foreign_browsers_closed: false`.
- CLI `browser detach --tab t1` → schließt genau den agenteneigenen Tab.
- Live-Nachweis danach: der Tab der Fremd-Session ist IMMER NOCH OFFEN
  (`tab list` der Simulations-Session, Exit 0).
- Es gibt nirgends ein `close --all` im Produkt (am Transport verweigert).

### 5. Gates

- `uv run ruff check src tests` → Exit 0 (Vollmenge).
- `uv run python -m compileall -q src` → Exit 0.
- Vollsuite (Einzellauf, Etappengrenze): `uv run python -m pytest tests/ -q`
  → **8395 passed, 33 skipped, 2 warnings** in 1887.70 s, `FULLSUITE_EXIT=0`.

## Aufräumen

Alle in dieser Runde gestarteten Browser-Sessions wurden geschlossen (Produkt-
Session `skaldir`, Nutzer-Sim `live-user-sim`, Worker-Probe `6a09dfcfc88e`);
`agent-browser session list` → „No active sessions“.
