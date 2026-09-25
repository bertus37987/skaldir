# TOOLS-V2 — Host-Wiring der Live-Insertion (Block B5: §1.3 + §1.4 + §1.6-Konsum)

**Status:** belegt am 14.09.2026. Alle Zahlen/Zeilen sind am Code bzw. an echten Testläufen
gemessen und im Text als `Datei:Zeile` belegt. Beweise werden strikt getrennt:
**Harness** (Tests) ≠ **Live** (echter Host-Roundtrip).

## 0. Auftrag und Abgrenzung

- (§1.3) `apply_pending`/`boundary_safe` an einer **tatsächlichen Hermes-Modellaufrufgrenze**:
  neue geprüfte Tools ohne Session-Neustart auffindbar + aufrufbar, höchstens `HOT_LIMIT` (3) neue
  relevante Fähigkeiten gleichzeitig sichtbar, laufende Aufrufe behalten Version/Rechte/Workspace.
- (§1.4) MCP-Konfiguration bei Bedarf automatisch über den **vorhandenen**
  Hermes-Konfigurationsmechanismus, idempotent, ohne andere Server zu überschreiben.
  **Konfigänderung allein ist kein Live-Beweis.**
- (§1.6-Konsum) Die Auswahl (MCP-Surface/Hot-Set) berücksichtigt Health — konsumiert, nicht gebaut
  (B2 besitzt `capability_health.py`).

## 1. Gefundener Host-Seam

Der Host leitet das Tool-Array (`tools=`) jedes Agenten **pro Turn** aus der lebenden Registry ab:

- `agent/turn_context.py:811` — `_refresh_mcp_tools_between_turns(agent)` im Turn-Start, VOR dem
  Zusammenbau von `tools=`;
- `_refresh_mcp_tools_between_turns` (`agent/turn_context.py:392-405`) rief den Refresh nur, wenn
  `"tools.mcp_tool" in sys.modules` **und** `has_registered_mcp_tools()` — also nur bei
  MCP-Präsenz; ein Tool, das ein **Plugin später registriert**, blieb bis zum Session-Neustart
  unsichtbar;
- `tools/mcp_tool_agent.py:82-124` — `refresh_agent_mcp_tools(...)`: leitet das Snapshot aus
  `model_tools.get_tool_definitions` neu ab (Generationen-Check `agent._tool_snapshot_generation`,
  `preserve_prefix=True` = nur ans Ende anhängen, Prompt-Cache-Prefix bleibt);
- `model_tools.py:212-251` — `get_tool_definitions` memoisiert gegen `registry._generation`
  (`tools/registry.py:385`, Bump in `register()` `:661`): **jede Registrierung invalidiert das Memo**;
- `agent/turn_context.py:618` — `invoke_hook("pre_llm_call", …)`: der Modell-Grenzpunkt, an dem das
  Plugin arbeitet (läuft im selben Turn-Start; die Grenze ist „vor dem ersten API-Call des Turns“);
- Plugin-Tools erreichen das Modell über die **Tool-Search-Brücke** (`model_tools.py:471-486`,
  `tools/tool_search.py:31-46` `ToolSearchConfig`): plugin-/MCP-Tools werden — sobald die
  deferrbare Fläche die Schwelle überschreitet — durch `tool_search`/`tool_describe`/`tool_call`
  ersetzt. Das ist Host-Verhalten, kein Defekt; die Tools bleiben so auffindbar + aufrufbar.
- Plugin-Surface: `ctx.register_tool(...)` (`hermes_cli/plugins.py:449-491`) schreibt in dieselbe
  Registry (Scope = Profil); `PluginRegistration.dispose()` (`hermes_cli/plugins_ledger.py:50-59`)
  nimmt eine Registrierung wieder zurück. Plugin-Toolsets sind per Default aktiv, solange ihr Key
  zur Toolset-Auflösung **bekannt** ist (`hermes_cli/tools_config.py:534-541`,
  `_get_platform_tools`).

## 2. Gewählter Weg + Begründung

Gewählt: **(b) native Registrierung aus dem `pre_llm_call`-Boundary + ein enges Host-Overlay**,
das den Between-Turns-Refresh auch für spät registrierte Plugin-Tools öffnet.

- Der Refresh-Mechanismus des Hosts ist bereits der richtige (append-only, generationengenau,
  vor dem API-Call) — er war nur auf MCP eingeschränkt. Das Overlay weitet den **Trigger**, nicht
  die Semantik.
- Die Insertion selbst bleibt Aufgabe des Produkts: `CapabilityIndex.sync_registry()` +
  `apply_pending()` + `ctx.register_tool()`. Kein zweites Register, kein zweiter Ausführungspfad:
  der native Handler ruft über denselben Wrapper wie die MCP-Surface
  (`mcpserver._wrapper_for` → generierter Code über `gates._run`/bwrap, fail-closed).
- Kein Session-Neustart, kein Konfigurationswechsel: die Registrierung erhöht die
  Registry-Generation, und der nächste Turn-Snapshot liest sie.
- `pre_llm_call` ist der **einzige** vom Produkt bespielbare Boundary vor dem Zusammenbau von
  `tools=`; `boundary_safe()` verhindert jede Umformung, während ein Aufruf in flight ist.

## 3. Plugin-Änderungen (`integrations/hermes/godsv_plugin/__init__.py`)

Alles additiv, bestehende Guard-Pfade unangetastet (shadow/whisper-Frühausgänge bleiben):

| Element | Zeile | Zweck |
|---|---|---|
| `LIVE_REPORT_TOOL`/`LIVE_REPORT_SCHEMA` | `:1870` | stabiles Tool `godsv_capabilities` |
| `_LIVE_SURFACES` | `:1882` | Surface pro Session-ID (überlebt Session-Objekt-Churn) |
| `godsv_capabilities()` | `:1885` | Report der lebenden Fläche (live/hot/inserted/blocked) |
| `class LiveSurface` | `:1907` | Index + View + Registrierungen einer Session |
| `_live_surface()` | `:1936` | holt/erzeugt die Surface (modulweites Map) |
| `_apply_live_surface()` | `:1953` | registriert die heiße Menge, `dispose()`t Verdrängtes |
| `_live_boundary()` | `:1997` | Sync + `apply_pending` + Registrierung, nie werfend |
| `_maybe_autolink_mcp()` | `:2044` | §1.4-Automatik, einmal pro Prozess, non-blocking |
| Aufruf in `on_session_start` | `:2102` | Autolink andocken |
| Aufruf in `pre_llm_call` | `:2217` | die Modellgrenze (§1.3) |
| Toolset-Pin in `register(ctx)` | `:2321` | Toolset-Key existiert zum Agent-Build |

Details:

- `pre_llm_call` adoptiert eine Session ohne `on_session_start` (CLI/`--oneshot`-Pfad) — dieselbe
  Lücke, die das Completion-Gate in `pre_verify` adoptiert. Ohne Adoption gäbe es in diesen Läufen
  keine lebende Fläche.
- Hot-Set-Deckel: `surface_plan()` gibt genau die neuesten `HOT_LIMIT=3` zurück; der älteste
  verliert seinen Slot und wird per `dispose()` deregistriert. Ein Aufruf in flight wird **nie**
  entfernt (`liveinsert.surface_plan`, `:443`), und nichts wird mitten im Aufruf umgeformt
  (`CapabilityIndex.boundary_safe`, `:123`; `apply_pending`, `:131`).
- Fehlerisolation: jede neue Funktion fängt alles ab und schreibt nur `_trace` — eine kaputte
  Fläche kann keinen Turn brechen (Test: `tests/test_live_surface_wiring.py`).
- Autolink ist per `GODSV_MCP_AUTOLINK=0` abschaltbar und läuft in einem Daemon-Thread (der
  Host-CLI-Probe des MCP-Servers dauert Sekunden — `on_session_start` darf nicht blockieren).

## 4. Host-Overlay-Patch

- Neu: `host-overlay/patch_tool_refresh.py` — verankert, idempotent (MARKER
  „gv-host-overlay: refresh when the registry MOVED“), `--check`, schreibt `.gv-backup`, bricht laut
  ab, wenn der Anker fehlt.
- Wirkung: `_refresh_mcp_tools_between_turns` refresht, sobald sich die Registry-Generation seit
  dem Snapshot verschoben hat (plus der bisherige MCP-Pfad). Zusätzlich eine neutrale
  `HERMES_TURN_TRACE`-Zeile `gv_tool_refresh session=… added=[…] tools=N` für den Live-Beweis.
- Angewandt im echten Checkout (`~/.hermes/hermes-agent/agent/turn_context.py`); `--check` danach:
  „already patched“; Datei parst (`ast.parse`).
- **Nach jedem Hermes-Update erneut anwenden** (`python host-overlay/patch_tool_refresh.py --check`).
- Rot-Beweis (Harness): der ungepatchte Code refresht in genau der Situation **nicht**
  (Registry bewegt, kein MCP im Prozess): `RED-PROOF (unguepatchter Host): refresh_calls = 0`.

## 5. MCP-Link (`src/godsv/mcp_link.py`)

- Mechanismus: der **vorhandene** Host-Weg `hermes mcp add/remove` (feste argv, kein Shell),
  exakt wie `src/godsv/modelroles.py:201-224` es für die Capability-Flags vormacht. `~/.hermes/config.yaml`
  wird nie direkt editiert.
- `server_entry()` beschreibt den Server (`command=godsvoice`, `args=[mcp, serve, …]`; Scope über
  `--project/--runtime/--all` des Servers, **nie** über einen `tools.include`-Filter — ein
  eingefrorener Namensfilter würde jede künftig gebaute Fähigkeit verbergen; getestet).
- `link_state()` meldet `linked/enabled/matches/tool_filter/other_servers`; `ensure_linked()`
  schreibt **nur** bei Bedarf (idempotent: bei passendem Eintrag kein einziger CLI-Aufruf),
  legt vor dem ersten Write ein Backup neben die Config und vergleicht die übrigen
  `mcp_servers`-Einträge vorher/nachher (`others_unchanged`). `--check` ist read-only
  (Exit 0 nur bei `linked` + `enabled` + `matching`).
- Live-Rezept-Messung (echter Mechanismus, am 14.09. beobachtet): `hermes mcp add godsv-tools
  --command ~/.hermes/hermes-agent/venv/bin/godsvoice --args mcp serve` schrieb den Eintrag nach
  `~/.hermes/config.yaml`; die Probe fand ohne Scope **keine** Tools und der CLI speicherte
  `enabled: false`. Genau deshalb zählt ein `enabled: false`-Eintrag in `mcp_link` **nicht** als
  Link (Test `test_an_entry_saved_disabled_is_not_a_working_link`); Aufräumen erfolgte über den
  sanktionierten Weg `hermes mcp remove godsv-tools` (die `mcp_servers`-Sektion verschwand wieder
  vollständig).
- **Konfiguration ≠ Live-Beweis.** §1.4 ist hier „Automatik + Idempotenz + Merge-Sicherheit“
  belegt; der Live-Beweis der *Nutzbarkeit* kommt aus §8 (native Surface).
- Restgrenze MCP-Server-Seite: ein laufender `godsvoice mcp serve`-Child erfährt von einer neuen
  Fähigkeit heute nichts (kein Re-Scan/`list_changed` in `mcpserver.run_serve`; fastmcp 4.0.3 zeigt
  kein Emit beim `add_tool` nach Start). Follow-up: Live-Serve mit Watch + Notification.

## 6. Health in der Auswahl

- Neu: `mcpserver.health_verdict(runtime, name) -> (selectable, reason)` (`src/godsv/mcpserver.py:36`)
  — konsumiert B2s `capability_health.selectable()`, sonst `routable()`; harte Ausschlusszustände
  (`QUARANTINED`, `RETIRED`, `BROKEN`) sind fail-closed; ein Tool **ohne** Ledger-Eintrag bleibt
  sichtbar (konservativ — eine fehlende API darf nie die ganze Fläche leeren).
- `approved_python_specs(..., apply_health=True)` (`src/godsv/mcpserver.py:144`) filtert die
  exportierte MCP-Surface; `apply_health=False` ist die explizite Audit-Sicht. Ohne `runtime`
  (Fixture-Aufrufe) bleibt das Verhalten unverändert.
- Hot-Set/Live-Sync: `sync_registry()` prüft jedes Kandidaten-Tool über `health_verdict` und
  **benennt** Ausgeschlossenes in `blocked` (`{"QUARANTINED: …"}`) statt es stillschweigend zu
  übergehen (`src/godsv/liveinsert.py:258-292`). `still_available()` ist fail-closed bei fehlendem
  Health-Urteil (`:213-224`).
- Rot-Beweis (Gate aus ⇒ Test rot): `assert ['inspect_blender_project'] == []` schlägt fehl, sobald
  der Health-Filter in `approved_python_specs` deaktiviert wird.

## 7. Beweise — Harness (Testebene)

| Datei | Tests | Beleg |
|---|---|---|
| `tests/test_capability_learning.py` | 42 | Sync/Health-Gate/Native-Surface/Recency + Bestand |
| `tests/test_live_surface_wiring.py` (neu) | 10 | Plugin-Hooks mit FakeCtx + echter Registry/Builder |
| `tests/test_mcp_link.py` (neu) | 12 | Idempotenz, Merge, Backup, `--check`, `enabled:false` |
| `tests/test_host_refresh.py` (neu) | 9 | Anker-Sicherheit + Verhalten der gepatchten Funktion |

Gemeinsamer Lauf (8 Dateien, inkl. Plugin-Bestandstests und MCP-Scope):
`101 passed in 8.24s`, RC=0. Lint: `ruff check` über alle geänderten Dateien → `All checks passed!`
Rot-Beweise: Health-Gate (s. §6) und Host-Refresh (s. §4) — beide real gefahren, nicht behauptet.

Was der Harness beweist: Discovery + Insertion + Registrierung + Health-Auswahl **in einem Prozess**
gegen echte Registry/Builder (`_build_real_tool`), inkl. der Randfälle (HOT_LIMIT-Deckel, Eviction,
in-flight-Schutz, kaputte Registry, verweigerte Registrierung).
Was er **nicht** beweist: den echten Host-Roundtrip → §8.

## 8. Beweise — Live (echter Host-Roundtrip)

**Status: bewiesen** (Session `20260914_190421_ec3a6d`, interaktive `hermes chat`-Session,
Plugin live aus `~/.hermes/plugins/godsv` → Symlink auf dieses Repo, `GODSV_MODE=whisper`):

1. Turn 1 (leerer Bestand): Plugin-Boundary feuert —
   `liveinsert boundary session=20260914_190421_ec3a6d inserted=['money_convert', 'money_convert_panel',
   'money_convert_v2'] add=[…] live=[…] blocked={}` (Plugin-Trace, 19:05:01) → die geprüften
   Tools werden nativ registriert (je ein `PluginRegistration`-Handle).
2. **Mitten in der Session** (kurz vor Turn 2, 19:06) wurde außerhalb des Hosts `gv_live_probe3`
   gebaut (`ToolBuilder` → `tools.db` + `generated_tools/`, Projekt `path:/tmp/gv_live`).
3. Nächster Boundary (Turn 2, Antwort „ok“, 19:06:10): der Sync sieht die neue Fähigkeit und
   registriert sie — `inserted=['gv_live_probe3'] add=['gv_live_probe3'] remove=['money_convert']
   live=['gv_live_probe3','money_convert_panel','money_convert_v2']` (Plugin-Trace) —
   der älteste Slot wird verdrängt, der Deckel hält.
4. Host-Seite: `gv_tool_refresh session=20260914_190421_ec3a6d added=[] tools=20`
   (HERMES_TURN_TRACE) — der Refresh **läuft** im echten Turn (das ist der Patch-Beleg);
   `added=[]`, weil Plugin-Tools als **deferred** in die Tool-Search-Brücke gehen, nicht als
   direkte Namen.
5. Das echte Modell in genau dieser Session — ohne Neustart — hat die neue Fähigkeit gefunden und
   aufgerufen (`~/.hermes/state.db`, session `20260914_190421_ec3a6d`):
   - `tool_search ["gv_live_probe3", …]` → `"gv_live_probe3": {"source": "plugin",
     "source_name": "godsv-tools", …}` (Katalog enthielt auch `godsv_capabilities`, `money_convert_v2`)
   - `tool_describe` → `{"gv_live_probe3": {"description": "Summarize the delivery status of an
     invoice document", "parameters": {"properties": {"project_path": {"type": "string"}},
     "additionalProperties": true}}}` (Schema = `native_schema` der Spec)
   - `tool_call` → Tool-Antwort `{"built": "mid-session", "probe3": "live", "project":
     "/tmp/live.blend"}`; die Antwort des Modells zitiert genau diesen Rohtext.

Negativ-Probe davor (dieselbe Mechanik, Session `20260914_184415_b9686a`): die Registrierung lief,
aber das Modell bekam `tool_describe(["gv_live_probe"])` → `{"tools": {}, "not_found":
["gv_live_probe"]}` und antwortete „There is no tool named `gv_live_probe`… I won't invent output."
— dieser Fehlschlag führte zu den zwei echten Fixes in §3 (Toolset-Pin, Recency) und ist der Grund,
warum der Live-Nachweis oben als Kette (Plugin-Trace **und** Host-Trace **und** Modell-Tool-Call)
geführt wird, nicht als Einzelmessung.

**Restpunkt Live:** Der Beweis gilt für die deferrbare Surface (Standard des Hosts).
Ein direkter Eintrag im `tools=`-Array wäre nur ohne Tool-Search-Deferral zu sehen; das Array blieb
bei 20 Tools.

## 9. Restgrenzen

- **Tool-Search-Deferral:** Plugin-/MCP-Tools erreichen das Modell über
  `tool_search`/`tool_describe`/`tool_call`, nicht als direkte Tool-Definitionen. Der Host
  entscheidet das anhand der Fläche; das Produkt kann es nicht erzwingen (und sollte es nicht).
- **Prozess-Scope:** eine Registrierung gilt prozess-/profilweit; die Verdrängung wirkt nur auf die
  Registrierungen derselben Session. Zwei Sessions desselben Prozesses sehen die Vereinigung.
- **`include_legacy=True`** in der Live-Surface: legacy (scope-lose) Tools sind live sichtbar,
  sofern der Health-Ledger sie nicht ausschließt — bewusst, weil der Pause-Builder projekt-
  gescopte und ältere Läufe scope-lose Tools hinterlassen.
- **MCP-Server-Child:** live gebaute Fähigkeiten erreichen einen *laufenden* `godsvoice mcp serve`
  noch nicht (kein Re-Scan/`list_changed`); Follow-up (fastmcp 4.0.3: kein Emit beim `add_tool`
  nach Start nachgewiesen).
- **Ausführungsgrenze:** `src/godsv/execution.py` (B1) existierte in dieser Runde nicht; der native
  Handler läuft über `mcpserver._wrapper_for` → `gates._run`/bwrap (dieselbe Grenze wie MCP).
  Sobald B1 landet, hier umhängen (ein Aufrufpfad, nicht zwei).
- **Health-Selektion:** konsumiert `selectable()`/`routable()` defensiv; B2s Ausbau (Events,
  `selectable`-Semantik) kann die Fallback-Regel verfeinern. Tools ohne Ledger-Eintrag bleiben
  sichtbar (konservativ).
- **Eventing:** die Insertion ist heute nur als `_trace`-Zeile sichtbar; ein eigener Event-Kind
  gehört in `godsv/events.KINDS` (B2). Live im Plugin-Trace gesehen: `emit
  voice_intervention_assessment failed: ValueError: unknown event kind` — der Kind fehlt in der
  Whitelist (B2-Backlog → Nachtrag Zentrale: behoben — beide Kinds in `KINDS` registriert, Regressionstest in `test_events.py`).
- **Host-Patch-Pflege:** nach jedem Hermes-Update erneut anwenden (`--check`).
- **Residuen dieser Runde:** `~/.hermes/godsv` enthält die Live-Probe-Tools
  (`gv_live_probe`, `gv_live_probe2`, `gv_live_probe3`) sowie die Bestands-Tools; die Probes sind
  per `ToolStore.set_status(..., "candidate")` deaktiviert (nicht gelöscht), damit der Nachweis
  reproduzierbar bleibt.
- **Testdatei-Namen** (dokumentationspflichtig): `tests/test_live_surface_wiring.py`,
  `tests/test_mcp_link.py`, `tests/test_host_refresh.py`.
- **Cache-Hinweis:** der Refresh schreibt ausschließlich ans Ende des Tool-Arrays
  (`preserve_prefix=True`) — dieselbe Mechanik, die der Host für spät verbundene MCP-Server nutzt;
  die bestehende Präfix-Cache-Regel bleibt damit gewahrt (kein Umsortieren, kein Entfernen aus der
  Mitte).

## 10. Dateien, Commits, Testzahlen

| Datei | Art |
|---|---|
| `src/godsv/liveinsert.py` | Sync/Health-Gate/Native-Surface/Surface-Plan |
| `src/godsv/mcpserver.py` | `health_verdict` + `apply_health`-Filter |
| `integrations/hermes/godsv_plugin/__init__.py` | Boundary-Wiring, Toolset-Pin, Autolink |
| `host-overlay/patch_tool_refresh.py` | neues Host-Overlay |
| `src/godsv/mcp_link.py` | neue MCP-Link-Automatik (§1.4) |
| `tests/test_capability_learning.py` | +9 Tests (42 gesamt) |
| `tests/test_live_surface_wiring.py`, `tests/test_mcp_link.py`, `tests/test_host_refresh.py` | neu |
| `docs/TOOLS-V2-HOST-WIRING.md` | dieses Dokument |

Commits (Branch `godsv-supervisor-hardening`): `1d4cb1d` (Health-Gate + Sync + native Surface),
`7bec0c4` (MCP-Link), `8a9fa01` (Plugin-Wiring), `80a5fdf` (mcp_link-Test), `e6b8e12`
(Host-Overlay), `c1688a6` (Live-Fixes: Toolset-Pin, Recency, Session-Churn).
