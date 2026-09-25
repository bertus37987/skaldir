# TOOLS-V2 — Gemeinsame Ausführungs- und Berechtigungsgrenze (§1.1)

**Block B1** · Branch `godsv-supervisor-hardening` · Auftrag: §1.1 des Masterprompts
„Implementierungsauftrag für Hermes" (14.09.2026).

Grundlage: `docs/IMPLEMENTATION-PLAN-docker-tool-builder.md` §2 (Datei-Eigentum B1) und §3
(gemeinsame Regeln), `docs/SECURITY-BOUNDARIES.md` §6/§7, `docs/FEATURE-MAP.md` Kapitel 3.

Status: **umgesetzt und belegt** (Code, Tests, echte Läufe). Restgrenzen in §6.

---

## 1. Ergebnis in einem Satz

Alle lokalen Tool-Ausführungspfade laufen jetzt über **eine** Grenze
(`src/godsv/execution.py`): sie entscheidet über Sandbox-Pflicht, Netz und Output-Deckel,
führt den Prozess argv-fixiert (nie über eine Shell) aus und liefert ein strukturiertes
Ergebnis (`ok` / `failed` / `not-certifiable`). Ohne Sandbox läuft **nichts** — der bisher
ungeschützte Panel-Pfad `action=test` ist gefixt.

## 2. Pfade vorher → nachher

| Lokaler Ausführungspfad | vorher | nachher |
|---|---|---|
| **Panel `POST /tools/{name}/action`, `action=test`** (Route `plugin_api.py:1850-1857`) | direkter `subprocess.run` `plugin_api.py:1097-1114`, Wrapper-Lauf **ohne bwrap**, Kommentar `# nosec B603` | `execution.run_local(..., policy=RunPolicy(require_sandbox=True))` `plugin_api.py:1100-1128`; ohne Sandbox `not_certifiable` (`:1117-1125`), der Wrapper läuft dann **nicht** |
| **Testing-Tool-Runner am Completion-Gate** (`register_testing_tools` → `_testing_tool_runner`, `capability.py:289-350`) | `gates._run_checked` (`capability.py:303` alt) | `execution.run_local` (`capability.py:307-318`); `RunEvidence(source="runner")` unverändert (`capability.py:337-345`) |
| **Gate-/Builder-Prüfungen** (`run_gates`, `_run_testing_case`) | `gates._run` / `gates._run_checked` | `gates._run_checked` ist jetzt ein Tuple-Adapter auf `execution.run_local` (`gates.py:242-271`) — damit laufen `toolbuilder.py:57-60`, `toolbuilder.py:471` und CLI `godsvoice tools approve` (`cli.py:828`) über die Grenze |
| **Deklarierte Workspace-Checks** (`run`-Kriterium) | `gates.run_declared_check` → `gates._run` (`completion.py:1911-1920`) | unverändert — derselbe bwrap-Primitiv, weiter fail-closed |
| **MCP-Wrapper generierter Tools** | `gates._run` direkt (`mcpserver.py:88-130`, Block-1-Fix) | noch direkt am Primitiv; die Umstellung auf `execution.run_local` ist **B5** (Eigentum `mcpserver.py`) |
| **bwrap-argv (die eine Implementierung)** | `gates._run` (`gates.py:101-213`) | `gates._run` (`gates.py:112-235`), additiv `allow_network` (`gates.py:119`, `:170`) und `stdin` (`gates.py:120`, `:220-221`); Defaults unverändert |

Kein Pfad baut mehr eine eigene bwrap-Kommandozeile: `execution.run_local` ruft das
Primitiv auf (`execution.py:135-146`), statt es zu duplizieren.
## 3. Interface (eingefroren — andere Blöcke konsumieren es)

```python
# src/godsv/execution.py
@dataclass class RunPolicy:   # :50  — Defaults sind die strengen
    require_sandbox: bool = True
    allow_network: bool = False
    max_output_bytes: int = 200_000

@dataclass class ToolRun:     # :59  — argv only, nie eine Shell
    argv: list[str]; workdir: str; timeout: float = 60.0
    extra_binds: list[str] = field(default_factory=list)
    stdin: bytes | None = None; env: dict[str, str] = field(default_factory=dict)

@dataclass class RunResult:   # :71
    ok: bool; status: str      # "ok" | "failed" | "not-certifiable"
    exit_code: int | None = None; stdout: str = ""; stderr: str = ""
    detail: str = ""; sandboxed: bool = False

def sandbox_available() -> bool                                   # :93
def run_local(run: ToolRun, *, policy: RunPolicy = RunPolicy())   # :106
class ContainerExecutor(Protocol): available() / run_job()        # :231
def register_container_executor(executor)                          # :264  (additiv, für B6)
def get_container_executor() -> ContainerExecutor                  # :270
```

Signaturen stabil, Erweiterungen additiv. Semantik:

* `status="ok"` ⇔ der Prozess **lief** und endete mit 0. `"failed"` ⇔ er lief und scheiterte
  (Exit-Code oder Timeout). `"not-certifiable"` ⇔ er lief **nicht** (keine Sandbox) —
  ausdrücklich *kein* `ok` und kein gewöhnliches `failed`; `exit_code` bleibt `None`.
* `stdout` führt den **zusammengeführten** Ausgabe-Stream des bwrap-Laufs (das Primitiv liefert
  einen kombinierten Stream, es gibt dort keine getrennten Kanäle); `stderr` bleibt für
  spätere getrennte Kanäle leer. `detail` ist der menschenlesbare Grund.
* `max_output_bytes` deckelt die Ausgabe mit Markierung (`execution.py:100-104`).

## 4. Policy und Fail-closed (kein stiller Rückfall)

```text
policy.require_sandbox=True (Default):
    sandbox_available() == False        ergibt status="not-certifiable", nichts startet
    bwrap scheitert zur Laufzeit        ergibt status="not-certifiable", nichts wird wiederholt
    allow_network=False                 ergibt --unshare-net im bwrap-argv
policy.require_sandbox=False (expliziter Opt-out):
    läuft ohne bwrap NUR, wenn keine Sandbox existiert; das Ergebnis trägt
    sandboxed=False und detail="unsandboxed run — explicit policy require_sandbox=false"
    (kein Produktionsaufrufer setzt das heute: Panel, Testing-Tools und Gates nutzen den Default)
```

Zwei Ebenen sichern das ab: die Vorprüfung `sandbox_available()` (`execution.py:127-136`) und
die Laufzeit-Fail-closed des Primitivs (`gates.py:235-239`, `NO_SANDBOX_EXIT = -127`).
Ein einmal *verfügbarer* und dann *scheiternder* Sandbox-Lauf wird nie zu einem
ungeschützten Zweitversuch — genau das wäre der stille Downgrade.

Die Container-Naht ist ehrlich leer: `NullExecutor.available()` liefert
`(False, "docker runtime not installed yet")`, `run_job` gibt einen strukturierten
`not-certifiable`-Fehler zurück (`execution.py:239-259`) — nie einen lokalen Ersatzlauf.
## 5. Tests und echte Nachweise

### 5.1 Neue Suite `tests/test_execution_boundary.py` (17 Tests)

```bash
$ .venv/bin/python -m pytest tests/test_execution_boundary.py -p no:randomly
17 passed, 1 warning in 1.73s
```

| Auftrags-Punkt | Test | Was er belegt |
|---|---|---|
| (a) argv statt Shell | `TestArgvIsNeverAShell::test_metacharacters_are_passed_as_argv` | `; touch pwned.txt`, `` `id` ``, `$(id)`, `> redirected.txt` kommen **wörtlich** als argv an; keine der Dateien entsteht |
| (b) Pfad-/Host-Sichtbarkeit | `TestHostPathsAreInvisible::test_escape_and_hidden_host_paths_are_not_visible` | `../outside.txt`, echtes Home (+Dotfiles), `/var/run/docker.sock` unsichtbar; Sandbox-Home == Workdir |
| (c) Netz-Policy | `TestNetworkPolicy::test_default_policy_has_no_network` | echter bwrap-Lauf: `socket.create_connection` scheitert, nur `lo` |
| (c) Policy-Schalter wirkt | `TestNetworkPolicy::test_allow_network_drops_the_namespace_isolation` | mit `allow_network=True` sind Host-Interfaces sichtbar (ohne Internet-Zwang) |
| (d) fail-closed | `TestFailClosed::test_missing_sandbox_is_not_certifiable_and_nothing_runs` | ohne bwrap: `status="not-certifiable"`, `ok=False`, `exit_code=None`, Probe-Datei entsteht **nicht** |
| (d) Laufzeit-Scheitern | `TestFailClosed::test_runtime_sandbox_failure_is_not_certifiable` | bwrap-Fehler zur Laufzeit ⇒ `not-certifiable`, nie `ok` |
| (d) expliziter Opt-out | `TestFailClosed::test_explicit_opt_out_is_marked_never_silent` | `require_sandbox=False` ist der einzige unsandboxed-Pfad und trägt `sandboxed=False` + Marker |
| (d) Container-Naht | `TestFailClosed::test_container_executor_fails_closed_without_docker` | NullExecutor: `available()==(False, …docker…)`, `run_job` ⇒ `not-certifiable` |
| (e) Panel über die Grenze | `TestPanelTestRunsThroughTheBoundary::{test_panel_test_uses_the_shared_boundary, test_panel_test_without_sandbox_is_not_certifiable}` | `action=test` ruft `execution.run_local` (Spion), Policy `require_sandbox=True`; ohne Sandbox `ok=False` + `not_certifiable` und **kein** Ledger-Eintrag (Wrapper lief nicht) |
| (e) Panel echt | `TestPanelTestRunsThroughTheBoundary::test_panel_test_really_runs_inside_the_sandbox` | echter Builder + echter Lauf: `cwd == home == tool_dir`, `ifaces == ["lo"]` |
| (e) Route-Ebene | `TestPanelTestRunsThroughTheBoundary::test_route_level_action_test_goes_through_the_boundary` | `POST /tools/doubler/action {"action":"test"}` über FastAPI-TestClient (`app.include_router(panel.router)`, Origin+Token wie in `test_panel_trust_boundary.py`) ⇒ 200, `ok=False`, `not_certifiable=True`, Grenze erreicht |
| (f) Vertrag unverändert | `TestTestingToolResultContract::*` (5 Fälle) | `RunEvidence(source="runner", check_id=name)`; PASS-Zeile nötig, FAIL/ERROR gewinnt, kein Verdikt ⇒ fail-closed; Sandbox fehlt ⇒ `failed`, niemals `passed` |

**Rot-first-Beleg für den Panel-Fix** (neue Suite gegen den **alten** `plugin_api.py`, vor dem Umbau):

```
2 failed, 13 passed
FAILED TestPanelTestRunsThroughTheBoundary::test_panel_test_uses_the_shared_boundary
FAILED TestPanelTestRunsThroughTheBoundary::test_panel_test_without_sandbox_is_not_certifiable
E  AssertionError: panel action=test must call godsv.execution.run_local
E  AssertionError: {'name': 'doubler', 'ok': True, 'output': '{"doubled": 42}'}   # ungeschützt gelaufen
```

**Vorher/Nachher am echten Tool** (identische Probe `sandbox_probe` via `ToolBuilder` gebaut,
identischer Panel-Aufruf `action=test`; der alte Modulstand wurde read-only aus `git show HEAD~1` geladen):

```text
ALt (HEAD~1, subprocess.run) : {"cwd": ".../generated_tools/sandbox_probe", "home": "/home/willy",
                                "ifaces": ["enp0s31f6", "lo", "wlp0s20f3", "wwan0"]}
NEU (execution.run_local)    : {"cwd": ".../generated_tools/sandbox_probe",
                                "home": ".../generated_tools/sandbox_probe", "ifaces": ["lo"]}
```

### 5.2 Regression der berührten Suiten (nach dem Umbau)

```bash
$ .venv/bin/python -m pytest tests/test_panel_actions.py tests/test_panel_trust_boundary.py \
    tests/test_testing_result_contract.py tests/test_generated_tool_isolation.py \
    tests/test_toolgates.py tests/test_tool_module_resolution.py -p no:randomly
65 passed
```

Lint: `.venv/bin/ruff check` über alle fünf geänderten Dateien ⇒ `All checks passed!`

`action=recheck` (bestehender Pfad, seit diesem Umbau über die Grenze) — echter Panel-Lauf:

```text
recheck ok: True
  scan: True — keine Befunde
  import: True — Import ok (bwrap-Sandbox)     # Gates laufen im Sandbox-Lauf
  determinism: True — {"doubled": 42}
```

### 5.3 Vollsuite

```bash
$ .venv/bin/python -m pytest -p no:randomly
1283 passed, 1 warning in 105.08s
```

(Voller Repo-Lauf inkl. der 17 Tests dieser Suite und der WIP-Testdateien der Schwester-Blöcke;
zwei Läufe während des Blocks ergaben 1232 bzw. 1268 — die Differenz sind Fremd-Tests, die
zwischen den Läufen dazukamen.)
## 6. Grenzen und Restpunkte (bewusst, nicht still)

1. **Container-Ausführung folgt mit DockerRuntimeManager (Welle 2/B6).** Die Naht steht
   (`ContainerExecutor`, `register_container_executor`, `get_container_executor`), der Default ist
   der `NullExecutor`: `available() == (False, "docker runtime not installed yet")`, `run_job()`
   gibt einen strukturierten `not-certifiable`-Fehler. Ein Container-Job läuft **nie** ersatzweise
   lokal über `run_local` — B6 implementiert `run_job()` gegen den echten Runtime-Manager.
2. **Domänen-Allowlist bleibt deklarativ.** `allow_network` ist ein grober Namespace-Schalter
   (an/aus), kein per-Domain-Egress-Proxy; `allowed_domains` im Manifest beschreibt weiter nur die
   Absicht. Vgl. `docs/SECURITY-BOUNDARIES.md` §6.3.
3. **Der Sandbox-Lauf sieht den Interpreter-Präfix des Hosts (read-only).** Die exe-Binds
   (bwrap-Kette um `sys.executable`) legen die venv/uv-Python-Verzeichnisse read-only in den
   Sandbox-Namespace. Gemessen (echter Lauf):
   ```text
   host_root_exists True
   host_root_listing ['.local', 'projects']     # nur die Bind-Kette, keine echten Dotfiles
   venv_prefix_visible True
   ```
   `~/.ssh`, `~/.bash_history`, `/var/run/docker.sock` bleiben unsichtbar (Test (b)); die
   Restgrenze ist genau der Präfix-Pfad des Interpreters — ihn auszuweiten wäre ein Fehler,
   ihn zu entfernen macht python unbrauchbar.
4. **`require_sandbox=False` existiert als expliziter Opt-out** (Definition `execution.py:53`,
   Pfad `execution.py:177-228`), wird aber von **keinem** Produktionsaufrufer gesetzt:
   Panel (`plugin_api.py:1112`) und Testing-Tools (`capability.py:314`) übergeben explizit
   `require_sandbox=True`; die Gates nutzen den Default (`gates.py:267`).
5. **MCP-Wrapper (B5) hängen noch direkt am Primitiv.** `mcpserver.py:88-130` ruft `gates._run`
   (Block-1-Fix, bereits sandboxed) — die Umstellung auf `execution.run_local` gehört Block B5
   (Eigentum `mcpserver.py`), damit auch dort Policy/`RunResult` gelten.
6. **`RunResult.stdout` führt die kombinierte Ausgabe** (stdout+stderr), weil das Primitiv einen
   Stream liefert; `stderr` ist für später getrennte Kanäle reserviert. Kein Informationsverlust,
   aber keine getrennten Kanäle.
7. **Exit-Code-Heuristik des Primitivs unverändert:** `gates.py:229` wertet `rc ∈ {0,1}` oder
   vorhandene Ausgabe als „Sandbox-Lauf"; ein bwrap-Setup-Fehler mit rc 1 und ohne Ausgabe würde
   also als *Lauf* mit rc 1 (⇒ `failed`) gelesen, nicht als `not-certifiable`. Sicherheitsrichtung
   unverändert (nie `ok`), Nuance offen für einen späteren bwrap-Fehlercode-Abgleich.
8. **Timeout ist `failed` mit `exit_code=TIMEOUT_EXIT (-9)`**, nicht `not-certifiable` — der Lauf
   fand statt und wurde abgebrochen; der Vertrag der Aufrufer (`-9, "Timeout nach Xs"`) ist exakt
   erhalten (`gates.py:242-271`).
