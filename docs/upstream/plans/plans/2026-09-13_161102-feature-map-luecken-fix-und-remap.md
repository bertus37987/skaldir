# Plan: God's-Voice-Lücken schließen (Feature-Map-Register) + Re-Mapping & Doku-Update

**Datum:** 2026-09-13 · **Autor:** Hermes (für Willy) · **Repo:** `/home/willy/projects/gods-voice`, Branch `godsv-supervisor-hardening`
**Grundlage:** `docs/FEATURE-MAP.md` (code-verifiziertes Lücken-Register, Kapitel 8, 24 Einträge)

## Goal

Die im Feature-Map-Register belegten echten Bugs und unverdrahteten Pfade beheben (TDD, je Task ein Commit) und danach Mapping + Dokumentation mit derselben Methodik erneut durchführen (Feature-Map v2 mit Fix-Status pro Lücke).

## Current context / assumptions

- **Umgebung:** Python 3.12-Projekt mit `.venv` via `uv` — **niemals `.venv/bin/pip`**, immer `.venv/bin/python -m pytest …`. Lint: `.venv/bin/ruff check src tests tools`.
- **Alle Pfade relativ zum Repo-Root** `/home/willy/projects/gods-voice`. Tests liegen in `tests/`, je Modul eine Datei nach Schema `tests/test_<modul>.py`.
- **Konvention „rot-first":** Jeder Task beginnt mit dem Test, führt ihn aus und zeigt den Fehlschlag (RED), dann minimal implementieren (GREEN), dann folgende Kette als Abschluss eines Tasks:
  ```
  .venv/bin/python -m pytest -q tests/<datei>::<testname>          # erwartet: 1 failed (RED)
  # … implementieren …
  .venv/bin/python -m pytest -q tests/<datei>::<testname>          # erwartet: 1 passed (GREEN)
  .venv/bin/ruff check <geänderte dateien>                          # erwartet: "All checks passed!"
  git add -A && git commit -m "<message aus dem Task>"
  ```
- **Bestehende Tests dürfen nicht brechen.** Nach jedem Task zusätzlich:
  `.venv/bin/python -m pytest -q tests/<datei> tests/<nachbardatei>` (erwartet: alle passed).
- **Sprache:** Code-Kommentare/Fehlertexte im Projekt sind gemischt DE/EN; übernimm den Stil der Nachbarzeilen. Commit-Messages: Conventional-Commits-Stil wie im Repo (`feat(...)`, `fix(...)`, `docs(...)`).
- **Keine Doku als Quelle** für Verhalten — immer den Code lesen. Die FEATURE-MAP nennt für jede Stelle `Datei:Zeile`; Zeilennummern können um ±5 abweichen, in dem Fall die Anker (Funktionsnamen) im Text suchen.
- **Kein Push am Anfang** — am Ende von Phase 1+2 einmal pushen (Autorisierung für diesen Branch gegeben), Phase 3 committet separat.

## Architecture / proposed approach

Drei Phasen: **(1)** kleine, präzise Bugfixes an wohldefinierten Stellen (Event-Register, DoD-Status-Lifecycle, `project_id`-Plumbing, Workflow-Detailfixes) — jeder mit eigenem rot-first Test; **(2)** Verdrahtung dreier klar umrissener CONNECTs (Testing-Tool-Validierung per CLI, Scope-Promotion-Eintritt, Workflow-/Draft-Kommandos); **(3)** erneutes Mapping mit der gleichen 6-Agenten-Methodik plus eigener Verifikation, Ergebnis als `FEATURE-MAP.md` v2 mit einem Status je Lücke (`FIXED`/`OPEN`/`ACCEPTED-RISK`).

---

# Phase 1 — Bugs (jeder Task: Test → RED → Fix → GREEN → Commit)

## T1 — `pause_no_action` ins Event-Register

**Warum:** `pause.py` emittiert `pause_no_action` (No-Intervention-Ausgang), aber `events.KINDS` kennt das Kind nicht → `EventStore.append` wirft `unknown event kind`, der Emit geht live verloren (nur Trace).

**Datei:** `src/godsv/events.py` (die `KINDS = frozenset({ … })`-Menge ab Zeile ~25) und `tests/test_events.py` (existiert; sonst anlegen — nach Muster von `tests/test_incidents.py`).

**Schritt 1 — Test (RED):** In `tests/test_events.py` ergänzen:

```python
def test_pause_no_action_is_a_registered_event_kind(tmp_path):
    from godsv.events import KINDS, EventStore

    assert "pause_no_action" in KINDS
    store = EventStore(tmp_path / "events.db")
    store.append(kind="pause_no_action", session_id="s1", payload={"pause": "p1", "reason": "x"})
    assert any(row["kind"] == "pause_no_action" for row in store.iter_session("s1"))
    store.close()
```

Ausführen: `.venv/bin/python -m pytest -q tests/test_events.py::test_pause_no_action_is_a_registered_event_kind`
Erwartet: **FAIL** (AssertionError `"pause_no_action" in KINDS`).

> Hinweis: Wenn `EventStore.append` eine andere Signatur hat, die Nachbarzeilen im Test-File übernehmen — Ziel bleibt: das Kind ist registriert und ein Append mit diesem Kind wirft nicht.

**Schritt 2 — Fix:** In `KINDS` eine Zeile ergänzen (bei den anderen `pause_*`-Tags, Stil übernehmen):

```python
        "pause_no_action",  # v1: no-intervention outcome — lease abandoned, agent keeps working
```

**Schritt 3 — GREEN + Commit:**
```
.venv/bin/python -m pytest -q tests/test_events.py
.venv/bin/ruff check src/godsv/events.py tests/test_events.py
git add -A && git commit -m "fix(events): register pause_no_action kind (no-intervention outcome was dropped)"
```

## T2 — `treasury_selftest` ins Event-Register

**Warum:** `treasury.py:362` emittiert `treasury_selftest`; fehlt ebenfalls in `KINDS` → latenter `ValueError`.

**Schritt 1 — Test (RED):** In `tests/test_events.py`:

```python
def test_treasury_selftest_is_a_registered_event_kind():
    from godsv.events import KINDS

    assert "treasury_selftest" in KINDS
```

**Schritt 2 — Fix:** In `KINDS` ergänzen:

```python
        "treasury_selftest",  # v2.5: owner credential self-test (decrypt + isolated injection)
```

**Schritt 3 — GREEN + Commit:**
```
.venv/bin/python -m pytest -q tests/test_events.py
.venv/bin/ruff check src/godsv/events.py tests/test_events.py
git add -A && git commit -m "fix(events): register treasury_selftest kind"
```

## T3 — DoD-Status-Lifecycle: `verified`/`failed` wirklich schreiben

**Warum:** `DodItem.status` wird nur als `'open'` geschrieben; `gate_dod` (completion.py:1074) liest `'verified'` → Skip-Zweig unerreichbar. „x/y verified" im Cockpit ist Schätzung. Fix: Nach jedem Verify-Lauf die Outcomes zurückschreiben.

**Datei:** `src/godsv/completion.py`, `tests/test_completion.py` (existiert; Muster übernehmen).

**Schritt 1 — Test (RED):**

```python
def test_verify_records_dod_outcomes(tmp_path):
    from godsv.completion import (
        CompletionRequest, GateRunner, VERIFIED_COMPLETE, WORKING,
    )
    # Setup analog zu bestehenden Tests in dieser Datei:
    # - CompletionStore(tmp_path, session_id="t3")
    # - begin_goal(...) mit zwei Kriterien: c1 mit verification="pytest", c2 mit verification=""
    # - RunEvidence(name="pytest", check_id="pytest", source="runner", status="passed", cmd="pytest -q", ok=True)
    # - result = GateRunner(dod=[...]).verify(request, evidence=[...], store=store)
    # Erwartung:
    #   result.status == INCOMPLETE          (c2 hat keinen Plan → BLOCKER)
    #   store.state()["dod"][0]["status"] == "verified"   (c1 bestanden)
    #   store.state()["dod"][1]["status"] == "failed"      (c2 blockierte)
    ...
```

Konkret (vollständig, an bestehende Fixtures anpassen):

```python
def test_verify_records_dod_outcomes(tmp_path):
    from godsv.completion import (
        CompletionStore, CompletionRequest, GateRunner, INCOMPLETE,
    )

    store = CompletionStore(tmp_path, session_id="t3")
    store.begin_goal(
        session_id="t3",
        goal="outcomes",
        dod=[
            {"id": "c1", "text": "tests pass", "blocking": True, "verification": "pytest"},
            {"id": "c2", "text": "no plan", "blocking": True, "verification": ""},
        ],
    )
    evidence = []  # keine Runner-Evidenz → c1 auch rot
    result = GateRunner(dod=store.dod_items()).verify(
        CompletionRequest(user_goal="outcomes", claimed_completed=True), evidence, store=store
    )
    assert result.status == INCOMPLETE
    statuses = {item["id"]: item["status"] for item in store.state()["dod"]}
    assert statuses["c1"] == "failed" and statuses["c2"] == "failed"
```

Zweiter Test für den Grün-Fall (c1 bekommt runner-Evidence → `verified`).

> **Wichtig:** Vor dem Schreiben die exakten Fixture-/API-Namen in `tests/test_completion.py` nachsehen und übernehmen (Konstruktor-Parameter von `CompletionStore`, `GateRunner`, `RunEvidence`, `store.state()["dod"]`-Struktur). 

Ausführen: `.venv/bin/python -m pytest -q tests/test_completion.py -k "dod_outcomes"` → erwartet **FAIL** (`statuses["c1"] == "failed"` scheitert, weil noch `"open"`).

**Schritt 2 — Implementierung:** In `src/godsv/completion.py`:

a) In `CompletionStore` eine Methode ergänzen (bei `_criterion_edit`, ~Zeile 533):

```python
    def record_dod_outcomes(self, result) -> None:
        """Write per-criterion outcomes back after a verification run (§13).

        A criterion without a blocking/major finding that had runner evidence
        is ``verified``; a criterion named by a blocker/major finding is
        ``failed``. Never called for terminal states (frozen contract).
        """
        record = self.state()
        if record.get("state") in TERMINAL_STATES:
            return
        bad_ids = {
            f.requirement
            for f in list(result.blocking_failures) + list(result.major_failures)
            if getattr(f, "requirement", "")
        }
        changed = False
        for item in record.get("dod") or []:
            new = "failed" if item.get("id") in bad_ids else "verified"
            if item.get("status") != new:
                item["status"] = new
                changed = True
        if changed:
            path = self.state_path  # exakten Attributnamen der Klasse übernehmen
            path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
```

b) In `GateRunner.verify(...)` im `if store is not None:`-Block **nach** `store.record_result(result)` einfügen:

```python
            if result.status != INCOMPLETE:
                store.record_dod_outcomes(result)
```

> Begründung der Bedingung: Bei `INCOMPLETE` markiert der Lauf möglicherweise nur *momentan* fehlende Evidenz; `failed` soll stehen bleiben, `verified` nur bei Bestätigung (Gates grün). Alternative (wenn du strenger sein willst): immer aufrufen — dann dokumentiere die Wahl im Commit.

c) **Wichtig — Wechselwirkung prüfen:** `gate_dod` überspringt jetzt Items mit `status=="verified"`. Da `verify()` die Gates **vor** dem Zurückschreiben auswertet, entsteht keine Selbstbestätigung. Aber: `set_goal` setzt alle auf `open` (bleibt korrekt), `update_criterion` ändert nichts am Status (bleibt korrekt).

**Schritt 3 — GREEN + Nachbartests + Commit:**
```
.venv/bin/python -m pytest -q tests/test_completion.py tests/test_contract_sessions.py
.venv/bin/ruff check src/godsv/completion.py tests/test_completion.py
git add -A && git commit -m "fix(completion): persist DoD item outcomes (verified/failed) after verify runs"
```

## T4 — `project_id` beim Capability-Request befüllen (CLI + Plugin)

**Warum:** Builder schreibt `scope: project` + leere `project_id` → Tool in keiner Sicht sichtbar (FEATURE-MAP 8.9). Minimalinvasiver Fix: den Kontext erfassen, wo er entsteht.

**Datei:** `src/godsv/cli.py` (`_cmd_tools_request` ~Zeile 417-455, Parser ~1272-1291), `integrations/hermes/godsv_plugin/__init__.py` (`_capability_gap_signal` 475-517), `tests/test_capability_flow.py`.

**Schritt 1 — Test (RED):** In `tests/test_capability_flow.py`:

```python
def test_tools_request_records_project_id_from_cwd(tmp_path, monkeypatch):
    # Muster: bestehende Tests rufen _cmd_tools_request über argparse-Namespace
    # ODER über die öffentliche API. Wenn CLI-Tests im Repo unüblich sind,
    # stattdessen auf CapabilityRequest-Ebene testen:
    from godsv.capability import CapabilityRequest, project_id_for

    pid = project_id_for(tmp_path)     # tmp_path hat kein git → "path:<resolved>"
    req = CapabilityRequest(capability="x", project_id=pid)
    assert req.project_id == pid
```

**Aussagekräftiger Integrationstest** (bevorzugt) — in `tests/test_capability_flow.py`:

```python
def test_request_stores_stable_project_id(tmp_path):
    import argparse
    from godsv import cli
    from godsv.capability import CapabilityStore, project_id_for

    ns = argparse.Namespace(
        capability="probe", reason="", requested_by="test", session="",
        kind="auto", name="", source="", tests="", module="", entrypoint="",
        sample="", research="", secret=[], domain=[], dependency=[], json=False,
        project=str(tmp_path),
    )
    # Runtime auf tmp umbiegen:
    assert cli._cmd_tools_request(ns) == 0
    store = CapabilityStore(tmp_path)
    # Hinweis: CapabilityStore-Pfad ggf. anpassen (state_dir/runtime) —
    # siehe plugin-Aufruf capability.py:92-93: <runtime>/capability_requests.jsonl
    rows = store.all()
    assert rows and rows[-1].project_id == project_id_for(tmp_path)
```

> Vor dem Fix die exakten Handler-/Store-Pfade im Code lesen (`_cmd_tools_request` in `cli.py:417-455`; `CapabilityStore.__init__` in `capability.py:89-93`). Wenn der CLI-Handler fest auf `HERMES_HOME/godsv` zeigt, erweitere den Test so, dass er `HERMES_HOME` per `monkeypatch.setenv` auf `tmp_path` setzt — dann prüft er den echten Pfad.

**Schritt 2 — Implementierung:**

a) Parser (`cli.py`, beim `p_tools_request`-Block ~1272-1291) ergänzen:

```python
    p_tools_request.add_argument(
        "--project",
        default="",
        help="project path — records the stable project id (default: cwd)",
    )
```

b) Handler (`_cmd_tools_request`): vor `store.add(...)`:

```python
    from .capability import project_id_for

    project_id = project_id_for(args.project or Path.cwd())
```

und in `CapabilityRequest(…)` das Feld `project_id=project_id` ergänzen.

c) Plugin (`integrations/hermes/godsv_plugin/__init__.py`, in `_capability_gap_signal`): vor `store.newest_open(...)` den Kontext aus dem Session-Task-Base ableiten:

```python
        project_id = ""
        try:
            from godsv.capability import project_id_for

            base = self._repo_root_hint()
            project_id = project_id_for(base) if base else ""
        except Exception:
            project_id = ""
```

und nach `request = store.newest_open(...)` (bzw. beim Persistieren des Requests — beachte: der Request entsteht per CLI, das Plugin annotiert ihn nur): **Das Plugin kann den Request nachträglich updaten** — `store.update(...)` mit `project_id=project_id` nur, wenn `request.project_id` leer ist:

```python
        if request is not None and project_id and not request.project_id:
            request.project_id = project_id
            store.update(request)
```

**Schritt 3 — GREEN + Commit:**
```
.venv/bin/python -m pytest -q tests/test_capability_flow.py tests/test_capability_mining.py tests/test_tool_scopes.py
.venv/bin/ruff check src/godsv/cli.py integrations/hermes/godsv_plugin/__init__.py
git add -A && git commit -m "fix(capability): record stable project_id on requests (CLI --project + plugin context)"
```

## T5 — `run_model_test`: Ausgangsmodell wirklich wiederherstellen

**Warum:** Der Restore-Block (`workflows.py` ~768-776) setzt nur `side_effect_state="model-selection-left"` — der Vertragstext verspricht Wiederherstellung.

**Datei:** `src/godsv/workflows.py`, `tests/test_workflows.py`.

**Schritt 1 — Test (RED):** In `tests/test_workflows.py`:

```python
def test_run_model_test_restores_original_model(tmp_path, monkeypatch):
    calls = {"evals": [], "shots": []}
    model_state = {"current": "Deepseek V4 Flash"}

    def fake_eval(port, expression, timeout=10.0):
        calls["evals"].append(expression)
        if "model-select restore" in expression:   # Marker den der Fix nutzt
            model_state["current"] = "Old Model"
            return "restored"
        return model_state["current"]

    monkeypatch.setattr(wf, "cdp_targets", lambda p, timeout=5.0: [{"type": "page"}])
    monkeypatch.setattr(wf, "_cdp_eval", fake_eval)
    monkeypatch.setattr(wf, "wait_for", lambda pred, timeout, interval=2.0: pred())
    out = wf.run_model_test("app", "New Model", "ping", runtime=tmp_path)
    assert any("model-select restore" in e for e in calls["evals"])
    assert out["side_effect_state"] == "restored"
```

Erwartet: **FAIL** (kein Restore-Eval, side_effect_state ist `model-selection-left`).

**Schritt 2 — Implementierung:** Den *ganz* aktuellen Zustand zuerst lesen (`sed -n '740,780p' src/godsv/workflows.py`) und die dortige Platzhalter-Anweisung ersetzen durch:

```python
    if restore_model and before and before != after:
        original_name = before.strip()
        restore_js = (
            "(() => { /* model-select restore */ "
            "const els = [...document.querySelectorAll('button,[aria-haspopup]')]"
            ".filter(e => e.getBoundingClientRect().width > 0); "
            f"const re = new RegExp({json.dumps(original_name[:40])}, 'i'); "
            "const hit = els.find(e => re.test(e.textContent || '')); "
            "if (!hit) return 'no-option'; hit.click(); return 'restored'; })()"
        )
        restored = _cdp_eval(cdp_port, restore_js)
        verified = _cdp_eval(cdp_port, MODEL_READER_JS)
        if restored == "restored" and original_name[:20].lower() in (verified or "").lower():
            result["side_effect_state"] = "restored"
        else:
            result["side_effect_state"] = "model-selection-left"
            result["warnings"].append(
                f"Ausgangsmodell {original_name[:60]!r} nicht automatisch wiederhergestellt "
                f"(restore={restored!r}, sichtbar={verified[:60]!r})"
            )
```

> **Hinweis:** Das Dropdown kann geschlossen sein — wenn `restored == "no-option"`, zuerst den Trigger öffnen (der Selektor aus dem bestehenden Setter-Block direkt über der Stelle) und dann erneut suchen. Als Minimal-Fix reicht: Trigger öffnen → 300 ms warten → Option klicken → verifizieren; scheitert das, bleibt der sichtbare Warnzustand (nie stiller Erfolg).

**Schritt 3 — GREEN + Commit:**
```
.venv/bin/python -m pytest -q tests/test_workflows.py
.venv/bin/ruff check src/godsv/workflows.py tests/test_workflows.py
git add -A && git commit -m "fix(workflows): run_model_test actually restores the original model or reports why not"
```

## T6 — Settings-Tastenkürzel korrekt senden

**Warum:** `settings_keys.split("+")[-1]` sendet von `"ctrl+comma"` nur `"comma"`.

**Datei:** `src/godsv/workflows.py` (~Zeile 443), `tests/test_workflows.py`.

**Schritt 1 — Test (RED):**

```python
def test_settings_navigation_sends_full_key_combo(tmp_path, monkeypatch):
    seen = []
    def fake_run(cmd, timeout=10.0):
        seen.append(cmd)
        return (0, "", "")
    monkeypatch.setattr(wf, "process_running", lambda p: [42])
    monkeypatch.setattr(wf, "find_window", lambda **k: {"xid": "0x1a", "title": "App", "wm_class": "app"})
    monkeypatch.setattr(wf, "focus_window", lambda x: True)
    monkeypatch.setattr(wf, "screenshot", lambda path, xid="": (path.parent.mkdir(parents=True, exist_ok=True), path.write_bytes(b"x"), (True, "import"))[-1])
    monkeypatch.setattr(wf.shutil, "which", lambda n: "/usr/bin/xdotool")
    monkeypatch.setattr(wf, "_run", fake_run)
    wf.open_app_settings_snapshot("app", settings_keys="ctrl+comma", runtime=tmp_path)
    assert any(cmd[:2] == ["xdotool", "key"] and "ctrl+comma" in cmd for cmd in seen)
```

Erwartet: **FAIL** (letztes Element ist `"comma"`, nie `"ctrl+comma"`).

**Schritt 2 — Fix** (Zeile ~443):

```python
    if nav_supported and settings_keys:
        _run(
            [
                "xdotool", "key", "--window", window["xid"],
                settings_keys.replace("+", "+"),  # xdotool akzeptiert "ctrl+comma" als EIN Argument
            ]
        )
```

`settings_keys.replace("+","+")` ist bewusst ein No-op-Dokumentations-Äquivalent — schreibe einfach `settings_keys` als letztes Argument. (Kommentar anpassen.)

**Schritt 3 — GREEN + Commit:**
```
.venv/bin/python -m pytest -q tests/test_workflows.py
.venv/bin/ruff check src/godsv/workflows.py tests/test_workflows.py
git add -A && git commit -m "fix(workflows): send full key combo for settings navigation (ctrl+comma)"
```

## T7 — Redirect-Text für Testing-Tools korrigieren

**Warum:** `redirect_payload` beschreibt `testing_tool` fälschlich als „exposed by the godsv-tools MCP server".

**Datei:** `src/godsv/toolbuilder.py` (~463-467), `tests/test_toolbuilder.py`.

**Schritt 1 — Test (RED):**

```python
def test_redirect_payload_uses_gate_text_for_testing_tools(tmp_path):
    from godsv.toolbuilder import ToolBuilder

    builder = ToolBuilder(tmp_path)
    spec = type("S", (), {"name": "check_x", "kind": "testing_tool"})()
    payload = builder.redirect_payload(
        capability="check", spec=spec, how_to_use="check_x <target>",
        constraints=[], kind="testing_tool",
    )
    text = str(payload)
    assert "MCP" not in text
    assert "completion gate" in text or "testing" in text.lower()
```

> Exakte Signatur von `redirect_payload` **zuerst lesen** (`sed -n '453,483p' src/godsv/toolbuilder.py`) und den Test an die echten Parameter anpassen.

**Schritt 2 — Fix:** In `redirect_payload` den Kind-Verzweig um `testing_tool` erweitern:

```python
    if kind == "testing_tool":
        how = f"Testing-Tool: laeuft am Completion-Gate als wiederholbare Pruefung (register_testing_tools). Aufruf: {how_to_use}"
    elif kind == "cli":
        how = f"CLI: {how_to_use}"
    else:
        how = f"MCP: exposed by the godsv-tools MCP server — {how_to_use}"
```

**Schritt 3 — GREEN + Commit:**
```
.venv/bin/python -m pytest -q tests/test_toolbuilder.py
.venv/bin/ruff check src/godsv/toolbuilder.py tests/test_toolbuilder.py
git add -A && git commit -m "fix(toolbuilder): correct usage text for testing tools in redirect payload"
```

## T8 — Scope-Konflikte im MCP-Export sichtbar machen

**Warum:** `resolve_tool_names(...)[0]` verwirft gleichrangige Namensdubletten still — der Name verschwindet kommentarlos aus dem Export.

**Datei:** `src/godsv/mcpserver.py`, `src/godsv/cli.py` (`_cmd_mcp_serve`), `tests/test_mcp_scope.py`.

**Schritt 1 — Test (RED):** In `tests/test_mcp_scope.py` ergänzen:

```python
def test_scope_conflicts_are_reported_not_silent(rig, capsys):
    store, runtime = rig
    # zwei Manifeste mit gleichem Namen sind über Verzeichnisse nicht baubar →
    # Konflikt über dieselbe visible-Liste simulieren:
    from godsv.mcpserver import scope_conflicts

    entries = [
        {"name": "same", "scope": "project", "project_id": "proj-a", "version": 1, "path": "/a"},
        {"name": "same", "scope": "global", "project_id": "", "version": 1, "path": "/b"},
    ]
    conflicts = scope_conflicts(entries)
    assert [c["name"] for c in conflicts] == ["same"]
```

> Der Konflikt-Fall „echte Ties" braucht Entries mit gleichem Rang; lege den Test so an, dass `resolve_tool_names` sie als Konflikt sieht (Rang = (project?1:0, version)). Prüfe vorher mit `python -c "from godsv.capability import resolve_tool_names; print(resolve_tool_names([...]))"`.

**Schritt 2 — Implementierung:** In `mcpserver.py`:

```python
def scope_conflicts(entries: list[dict]) -> list[dict]:
    """Unresolvable name collisions (project/global, same version) — surfaced, not silent."""
    from .capability import resolve_tool_names

    _resolved, conflicts = resolve_tool_names(entries)
    return conflicts
```

und in `_cmd_mcp_serve` (nach der Count-Berechnung) auf stderr warnen:

```python
    from .capability import visible_tools
    from .mcpserver import scope_conflicts

    visible = visible_tools(runtime, project_id, include_legacy=include_legacy)
    for conflict in scope_conflicts(visible):
        print(f"warning: name conflict '{conflict['name']}' — not exported (resolve scope/version)", file=sys.stderr)
```

**Schritt 3 — GREEN + Commit:**
```
.venv/bin/python -m pytest -q tests/test_mcp_scope.py tests/test_mcp_server.py
.venv/bin/ruff check src/godsv/mcpserver.py src/godsv/cli.py tests/test_mcp_scope.py
git add -A && git commit -m "fix(mcp): surface scope name conflicts instead of silently dropping tools"
```

## T9 — `desktop.uninstall` entfernt auch den Plugin-Symlink

**Datei:** `src/godsv/desktop.py` (`uninstall` ~416-436), `tests/test_desktop.py`.

**Schritt 1 — Test (RED):** Muster der bestehenden Desktop-Tests übernehmen (die testen mit `monkeypatch.setenv("HERMES_HOME", str(tmp_path))`):

```python
def test_uninstall_removes_plugin_link(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from godsv import desktop

    plugins = tmp_path / "plugins"
    plugins.mkdir()
    link = plugins / "godsv"
    link.symlink_to("/somewhere/plugin")
    desktop.uninstall()
    assert not link.exists()
```

> `desktop.uninstall()`-Signatur vorher lesen und ggf. `link_cli=False` o.ä. übergeben.

**Schritt 2 — Fix:** In `uninstall()` symmetrisch zu `_link_panel`:

```python
    try:
        (Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "plugins" / "godsv").unlink()
    except FileNotFoundError:
        pass
```

**Schritt 3 — GREEN + Commit:**
```
.venv/bin/python -m pytest -q tests/test_desktop.py
.venv/bin/ruff check src/godsv/desktop.py tests/test_desktop.py
git add -A && git commit -m "fix(desktop): uninstall also removes the plugin symlink"
```

---

# Phase 2 — CONNECTs (die klar umrissenen Verdrahtungen)

## T10 — Testing-Tool-Requests per CLI möglich machen (`validation_cases` + `--kind testing_tool`)

**Warum:** Mining promotet `testing_tool`, aber weder `tools request` noch `tools build` können `validation_cases` liefern → deterministisch `needs_validation`.

**Dateien:** `src/godsv/cli.py` (beide Parser + Handler `_cmd_tools_request`/`_cmd_tools_build`), `src/godsv/capability.py` (`to_tool_request` prüft schon `("mcp","cli","testing_tool")`), `tests/test_capability_flow.py`.

**Schritt 1 — Tests (RED):** Parser erweitern um `--validation-case TARGET:pass|fail` (append) bei `tools request` UND `tools build`; `--kind`-choices um `"testing_tool"` ergänzen. Test:

```python
def test_tools_request_accepts_testing_tool_with_validation_cases(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import argparse
    from godsv import cli
    from godsv.capability import CapabilityStore

    ns = argparse.Namespace(
        capability="check_x", reason="", requested_by="test", session="", kind="testing_tool",
        name="check_x", source="", tests="", module="", entrypoint="", sample="",
        research="", secret=[], domain=[], dependency=[], json=False, project="",
        validation_case=["/tmp/broken.py:fail", "/tmp/ok.py:pass"],
    )
    assert cli._cmd_tools_request(ns) == 0
    req = CapabilityStore(tmp_path / "godsv").all()[-1]
    assert req.kind == "testing_tool"
    assert req.validation_cases == [("/tmp/broken.py", False), ("/tmp/ok.py", True)]
```

**Schritt 2 — Implementierung:** Parsing-Helper (bei den anderen kleinen Helpern in `cli.py`):

```python
def _parse_validation_case(raw: str) -> tuple[str, bool]:
    target, _, expect = raw.rpartition(":")
    if not target or expect not in ("pass", "fail"):
        raise SystemExit(f"Ungueltiger --validation-case (erwartet ZIEL:pass|fail): {raw}")
    return target, expect == "pass"
```

In beiden Handlern: `validation_cases=[_parse_validation_case(v) for v in (args.validation_case or [])]` an `CapabilityRequest(…)` bzw. `ToolBuildRequest(…)` übergeben.

**Schritt 3 — GREEN + Commit:**
```
.venv/bin/python -m pytest -q tests/test_capability_flow.py tests/test_toolbuilder.py
.venv/bin/ruff check src/godsv/cli.py
git add -A && git commit -m "feat(cli): testing-tool requests with validation cases (--kind testing_tool, --validation-case)"
```

## T11 — Scope-Promotion zugänglich machen (CLI + Panel)

**Warum:** `promote_tool` (capability.py:664-717) hat keinen Eintritt.

**Dateien:** `src/godsv/cli.py` (neues Subkommando `tools promote-global`), `integrations/hermes/godsv_plugin/dashboard/plugin_api.py` (Route `POST /tools/{name}/promote`), Tests: `tests/test_tool_scopes.py`, `tests/test_panel_api.py`.

**Schritt 1 — Test (RED):** In `tests/test_tool_scopes.py` (dort existieren Promotion-Fixtures):

```python
def test_cli_promote_global_runs_promote_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Fixture der Datei wiederverwenden: validiertes Projekt-Tool bauen
    ...  # siehe vorhandene Tests in dieser Datei (Manifest schreiben + validation.status)
    import argparse
    from godsv import cli
    ns = argparse.Namespace(name="tool_x", evidence_project=["proj-b", "proj-c"], db=str(tmp_path / "godsv" / "tools.db"), json=True)
    assert cli._cmd_tools_promote_global(ns) == 0
```

**Schritt 2 — Implementierung:** CLI-Handler:

```python
def _cmd_tools_promote_global(args: argparse.Namespace) -> int:
    from .capability import promote_tool

    runtime = Path(args.db).parent
    result = promote_tool(runtime, args.name, evidence_projects=list(args.evidence_project or []))
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result)
    return 0 if result.get("promoted") else 1
```

Parser (im `tools_sub`-Block):

```python
    p_tools_promote_global = tools_sub.add_parser(
        "promote-global", help="promote a validated project tool to global (requires evidence)"
    )
    p_tools_promote_global.add_argument("name")
    p_tools_promote_global.add_argument("--evidence-project", action="append", default=[])
    p_tools_promote_global.add_argument("--db", default=f"{DEFAULT_DIR}/tools.db")
    p_tools_promote_global.add_argument("--json", action="store_true")
    p_tools_promote_global.set_defaults(func=_cmd_tools_promote_global)
```

Panel-Route (bei den anderen `/tools`-Routen):

```python
    @router.post("/tools/{name}/promote")
    def tools_promote(name: str, payload: dict, request: Request) -> dict:
        _guard_origin(request)
        from godsv.capability import promote_tool

        result = promote_tool(runtime_dir(), name, evidence_projects=list((payload or {}).get("evidence_projects") or []))
        _audit("tools.promote", {"name": name, "promoted": result.get("promoted")})
        return result
```

**Schritt 3 — GREEN + Commit:**
```
.venv/bin/python -m pytest -q tests/test_tool_scopes.py tests/test_panel_api.py
.venv/bin/ruff check src/godsv/cli.py integrations/hermes/godsv_plugin/dashboard/plugin_api.py
git add -A && git commit -m "feat(tools): expose scope promotion via CLI promote-global and panel route"
```

## T12 — `workflow_contracts()` und `draft_tool_spec()` erreichbar machen

**Warum:** Beide sind fertig implementiert, aber ohne Aufrufer (FEATURE-MAP 8.11).

**Dateien:** `src/godsv/cli.py`, `src/godsv/llm.py` (Client-Muster), `tests/test_workflows.py`.

**Schritt 1 — Test (RED):**

```python
def test_cli_workflows_list_prints_contracts(capsys):
    import argparse
    from godsv import cli

    ns = argparse.Namespace(json=True)
    assert cli._cmd_workflows_list(ns) == 0
    out = capsys.readouterr().out
    assert "open_app_snapshot" in out and "run_model_test" in out
```

**Schritt 2 — Implementierung:** Handler + Parser (`draft` nur, wenn LLM-Client verfügbar; sonst klare Fehlermeldung):

```python
def _cmd_workflows_list(args: argparse.Namespace) -> int:
    from .workflows import workflow_contracts

    contracts = workflow_contracts()
    if getattr(args, "json", False):
        print(json.dumps(contracts, ensure_ascii=False, indent=2))
    else:
        for c in contracts:
            print(f"- {c['name']}  [{c['scope']}/{c['execution']}]  {c['success']}")
    return 0


def _cmd_tools_draft(args: argparse.Namespace) -> int:
    from .llm import make_client_from_env  # exakten Fabriknamen aus llm.py übernehmen

    client = make_client_from_env()
    if client is None:
        print("kein LLM-Client konfiguriert (GODSV_LLM ...) — siehe llm.py", file=sys.stderr)
        return 1
    from .tools import draft_tool_spec

    spec = draft_tool_spec(client, args.need)
    print(json.dumps(spec, ensure_ascii=False, indent=2, default=str))
    return 0
```

Parser: `workflows`-Subparser mit `list` (+`--json`); `tools draft NEED` mit `--db` optional.

> **Vor dem Schreiben:** In `src/godsv/llm.py` die echte Client-Fabrik und in `src/godsv/tools.py:280-317` die exakte `draft_tool_spec`-Signatur lesen und übernehmen.

**Schritt 3 — GREEN + Commit:**
```
.venv/bin/python -m pytest -q tests/test_workflows.py
.venv/bin/ruff check src/godsv/cli.py
git add -A && git commit -m "feat(cli): workflows list + tools draft (wire up dead-but-ready entry points)"
```

## T13 — Zwischenabnahme Phase 1+2 (voller Lauf + Push)

```
.venv/bin/python -m pytest -q                # erwartet: alle grün (Baseline war zuletzt grün)
.venv/bin/ruff check src tests tools integrations app-overlay
node tools/check_desktop_plugin.mjs          # erwartet: "desktop panel: contract OK"
git push origin godsv-supervisor-hardening
```

Doku-Zwischenstand: In `docs/PROJECT-STATE.md` einen Abschnitt „Lücken-Register-Abarbeitung (13.09.)" mit den erledigten T1-T12 anhängen (Muster der bestehenden Einträge übernehmen) und committen.

---

# Phase 3 — Mapping und Dokumentation erneut durchführen

## T14 — Re-Mapping mit identischer Methodik

**Vorbereitung:** `git log --oneline -20` notieren (Commit-Hash für den Berichts-Stand).

**Vorgehen (genau wie beim ersten Mal):**
1. Die 6 Audit-Bereiche erneut als Subagents anstoßen — **identische Datei-Listen wie in `docs/FEATURE-MAP.md`** (Kapitel 2-7), mit ZUSATZ-Auftrag:
   > „Prüfe zusätzlich am Code, ob folgende zuvor gemeldete Lücken jetzt behoben sind: [Liste der T1-T12-Fixes mit Datei + erwartetem Verhalten]. Markiere jede als FIXED (mit `Datei:Zeile`-Beleg) oder weiterhin OPEN."
2. Eigener Fokus-Audit „Voice ↔ Main-Agent" (Kapitel 1) — Fixes gegenprüfen: `pause_no_action`-Event live prüfbar via `sqlite3 ~/.hermes/godsv/events.db "select kind,count(*) from events where kind='pause_no_action' group by kind"`.
3. **Stichproben-Prinzip beibehalten:** mindestens 5 Fix-Claims selbst mit `grep`/`sed` nachverifizieren, bevor sie als FIXED ins Dokument wandern.

## T15 — `docs/FEATURE-MAP.md` v2 erzeugen

**Konkret:**
1. Neues Lücken-Register (Kapitel 8) mit Drei-Spalten-Status je Eintrag: `FIXED (commit)` · `OPEN (Grund)` · `ACCEPTED-RISK (Begründung)` — Reihenfolge/Nummerierung des v1-Registers beibehalten, damit die Änderungen diffbar sind.
2. Kopfzeile aktualisieren: „**Revision 2** — Stand <Datum> — fixes Commit <hash>".
3. Änderungen in Kapitel 1-7 einarbeiten, wo Fixes Verhalten ändern (z. B. T1/T2-KINDS-Hinweis, T3-DoD-Lifecycle, T4-project_id).
4. Datei hat 200+ KB — beim Einarbeiten **nicht** das ganze File neu generieren; gezielte Patches per Python (Anker suchen → ersetzen → verifizieren, wie gehabt).

## T16 — Abschluss: Tests + Doku + Commit + Bericht

```
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests tools integrations app-overlay
node tools/check_desktop_plugin.mjs
git add docs/FEATURE-MAP.md docs/PROJECT-STATE.md
git commit -m "docs: feature-map v2 — fixes verified, gap register statused"
git push origin godsv-supervisor-hardening
```

Bericht an den Nutzer: (1) welche Lücken gefixt (mit Commits), (2) welche bewusst offen blieben + warum, (3) neue Feature-Map-Zahlen (Lücken vorher/nachher), (4) verbleibende Nutzer-Entscheidungen.

---

## Tests / validation (Gesamtübersicht)

| Task | Testdatei | Kern-Assertion |
|---|---|---|
| T1 | `tests/test_events.py` | Kind registriert + Append wirft nicht |
| T2 | `tests/test_events.py` | Kind registriert |
| T3 | `tests/test_completion.py` | Nach `verify`: DoD-Status `verified`/`failed` persistiert; `gate_dod`-Skip greift |
| T4 | `tests/test_capability_flow.py` | Request trägt stabile `project_id` (CLI + Plugin-Pfad) |
| T5 | `tests/test_workflows.py` | Restore-Eval läuft; `side_effect_state == "restored"` oder sichtbare Warnung |
| T6 | `tests/test_workflows.py` | `ctrl+comma` als ein Argument |
| T7 | `tests/test_toolbuilder.py` | Testing-Tool-Text ohne „MCP" |
| T8 | `tests/test_mcp_scope.py` | Konflikt wird zurückgegeben; CLI warnt |
| T9 | `tests/test_desktop.py` | Plugin-Symlink nach `uninstall()` weg |
| T10 | `tests/test_capability_flow.py` | `testing_tool` + `validation_cases` roundtrip |
| T11 | `tests/test_tool_scopes.py` + `tests/test_panel_api.py` | CLI/Route rufen `promote_tool`, Exitcode/HTTP korrekt |
| T12 | `tests/test_workflows.py` | `workflows list` druckt Verträge |

**Regel:** Jeder Test wird ERST geschrieben, EINZELN ausgeführt (muss fehlschlagen), dann implementiert, dann grün — inklusive Nachbardatei-Lauf. Kein Task gilt als fertig ohne roten Beweis vor dem Fix.

## Risks, tradeoffs, and open questions

- **T3 (DoD-Status)** ändert Daten im Live-Store (`~/.hermes/godsv/completion/*.json`). Risiko: Alt-Verträge mit Status `verified` aus altersschwachen Läufen könnten Kriterien überspringen. Mitigation: `record_dod_outcomes` nur für Nicht-Terminal-Verträge; vor dem Rollout ein Backup eines echten Stores nach `/tmp` ziehen.
- **T4** kann nichts befüllen, wenn der Aufrufer in einem Nicht-git-Verzeichnis läuft (`path:`-Fallback). Das ist bewusst so (stabil + ehrlich) — im Manifest sichtbar machen: `project_id`-Format zeigt die Quelle (`remote:`/`gitdir:`/`path:`).
- **T5** hängt von der DOM-Struktur des jeweiligen App-Dropdowns ab; wenn die Option nicht klickbar ist, bleibt es beim sichtbaren Warnzustand — kein Fake-Restore.
- **T11 Panel-Route:** `promote_tool` mutiert das Manifest in-place — vorher ein Backup des Tool-Verzeichnisses (`/tmp/gv_tool_backup_<name>.tar.gz`), und die Route nur mit `_guard_origin` freigeben (wie alle Mutationen).
- **Bewusst NICHT in diesem Plan** (OFFEN für Nutzer-Entscheidung, im Register als `ACCEPTED-RISK` führen): echte Embedding-Verdrahtung, automatische MCP-Registrierung in der Hermes-Config, Ledger-Live-Pfad, Nightshift-Scheduling, Event-Retention, Löschung toter API-Flächen (`InjectionQueue.drain`, `to_mcp_descriptor`, `by_status`, `_wait_and_open_panel`).
- **Offene Fragen an den Nutzer:** (1) Soll `mine` künftig automatisch laufen (Cron am Sessionende)? (2) Soll der MCP-Server beim `desktop install` automatisch in `~/.hermes/config.yaml` eingetragen werden (mit Bestätigungsdialog)? (3) Embeddings: lokaler Provider (z. B. sentence-transformers) oder weiter Trigram-Hashing?
