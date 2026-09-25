# TOOLS-V2-REGISTRY — eine Registry-Wahrheit + nachvollziehbare Migration (§1.2/§1.9, Block B4)

**Status:** gefüllt (14.09.2026, Code + Tests + Live-CLI; Testzahlen in §5).
**Quelle:** Auftrag §1.2/§1.9 (IMPLEMENTATION-PLAN-docker-tool-builder.md §2, Block B4).
**Dateien:** NEU `src/godsv/registry.py` · `src/godsv/cli.py` · NEU
`tests/test_registry_context.py` · `tests/test_tool_scopes.py` (Ergänzung).
**Nicht angefasst:** `capability.py` (B1), `tools.py`/`toolbuilder.py` (B3),
`mcpserver.py`/Panel (B5) — nur gelesen bzw. über die CLI aufgerufen.

## 0. Zweck

CLI, Panel, MCP, Builder und Hermes sehen **dieselben autorisierten Tools**:
Runtime-Verzeichnis, Tools-Datenbank, `generated_tools/` und Projektkontext
werden an **einer** Stelle aufgelöst (`src/godsv/registry.py`).

Der behobene Zwiespalt: Der CLI-Default war `.godsv/tools.db` (cwd-relativ,
Parser `cli.py`), Builder/Panel/MCP schrieben aber unter
`$HERMES_HOME/godsv/tools.db` — „out of the box leere Oberfläche"
(`docs/FEATURE-MAP.md` §3 Lücke 7, §4 Lücke 12). Ab jetzt gilt überall der
Runtime-Pfad; ein **explizites** `--db` gewinnt weiterhin.

## 1. Auflösungsregeln

`resolve_registry(runtime=None, project=None, *, db=None) -> RegistryContext`
(`src/godsv/registry.py:123`); `RegistryContext` trägt `runtime_dir`, `tools_db`,
`generated_tools_dir`, `project_id`, `selection_reason`, `legacy_candidates`
(`:88`). Auflösung, erste Übereinstimmung gewinnt:

| Stufe | Regel | `selection_reason` |
|---|---|---|
| 1 | expliziter `runtime`-Parameter | `explicit --runtime` |
| 2 | expliziter `db`-Parameter → Runtime = dessen Verzeichnis (historische CLI-Regel: ein explizites DB benennt seine Umgebung) | `explicit --db` |
| 3 | `$GODSV_HOME` (etablierter Override, s. `tools-hub`) | `runtime default ($GODSV_HOME)` |
| 4 | `$HERMES_HOME/godsv` (Fallback `~/.hermes/godsv`) | `runtime default ($HERMES_HOME/godsv)` |

- `tools_db` = explizites `--db`, sonst `<runtime_dir>/tools.db`; `generated_tools_dir` = `<runtime_dir>/generated_tools` (`default_runtime_dir()` `:109`).
- **Projekt-ID** über `capability.project_id_for` (`src/godsv/capability.py:474-507`, nur lesend importiert): remote > shared git-common-dir > kanonischer Pfad (als schwächste Stufe mit `path:` markiert). Default-Pfad = cwd, überschreibbar per `project`-Parameter (`--project`).
- **Legacy-Funde** (`_legacy_candidates` `:206`): `<cwd>/.godsv/tools.db` und — bei explizitem Projektpfad — `<projekt>/.godsv/tools.db`; gemeldet mit `exists`, `readable`, `tool_count`, `names`. Sie werden **nie** ausgewählt, nur gemeldet. Ein nicht existierender Kandidat bleibt als `exists: false` sichtbar (Status-Ausgabe zeigt „not present").
- **Keine Verhaltensänderung**, wenn die Runtime-DB existiert und Legacy fehlt/leer ist: Auswahl, Counts und Sicht bleiben identisch (Test `test_existing_runtime_db_changes_nothing_and_no_legacy`).
- `registry_snapshot(context)` (`:172`) liefert den read-only Status (Spec-Zahl aus der DB, gelesen mit `mode=ro` `:250`, generierte Tools aus `capability.load_tool_entries`). Auflösen allein **erzeugt und verschiebt nichts**.


## 2. Migrationsprotokoll

`migrate_legacy(context, *, apply=False) -> dict` (`src/godsv/registry.py:299`).

**Dry-Run (Default):** Bericht ohne jeden Schreibzugriff — die Ziel-DB wird
nicht einmal angelegt. Pro Quelle und Tool: `name`, `status`, `version`,
`updated`, `action` (`new` | `identical` | `imported-newer` | Konflikt). Rückgabe
enthält zusätzlich `migrated` (Anzahl zu importierender Records, im Dry-Run
„would migrate"), `identical`, `conflicts`, `errors`.

**`apply=True` — Reihenfolge:**

1. **Backup der QUELLE** direkt vor dem ersten echten Write:
   `shutil.copy2` nach `<db>.pre-migration-<utc-ts>.bak` (`_backup` `:512`;
   existiert der Name, kommt `-2`, `-3`, … dazu — ein Backup wird nie
   überschrieben). Ein Lauf **ohne** importierbaren Record schreibt nichts und
   legt kein Backup an.
2. **Backup der Ziel-DB** (falls vorhanden) unmittelbar vor dem ersten Write
   in dasselbe Namensschema.
3. **Merge** (`_migrate_source` `:364`): neuer Name → `ToolSpec`-Import; die
   Record-eigene `updated`-Zeit bleibt erhalten (`upsert(..., ts=...)`,
   `_write_spec` `:483`).
4. **Journal** `<runtime_dir>/registry_migration.jsonl` (`_journal` `:500`,
   append-only, eine JSON-Zeile pro Entscheidung): `imported`,
   `imported-newer`, `target-kept` — mit `tool`, `source`, `target`,
   `source_updated`, `target_updated`, `resolution`, `ts`.
5. **Die Quell-DB bleibt erhalten** (nie modifiziert, nie gelöscht). Lesen
   passiert ausschließlich mit `sqlite3` `mode=ro` (`_read_specs` `:250`).

**Idempotent:** zweiter `apply`-Lauf → `migrated: 0`, alle Records
`identical`, Journal unverändert (Test `test_second_apply_is_idempotent`).

**Konfliktbehandlung bei Namensgleichheit** (Inhalt = alle fachlichen Felder
ohne `created`/`updated`/id, `_fingerprint` `:287`):

| Fall | Aktion | `resolution` | Garantie |
|---|---|---|---|
| Inhalt identisch | überspringen | — (`action: identical`) | Idempotenz |
| Inhalt abweichend, Legacy-**neuer** (`updated`) | übernehmen | `legacy-newer-imported+journaled` (Dry-Run: `would-import-newer`) | Übernahme ist ausschließlich im Journal dokumentiert |
| Inhalt abweichend, Ziel neuer oder gleich alt | **nichts schreiben** | `target-kept` | kein stilles Überschreiben |
| Quelle/Ziel unlesbar (kein `tool_specs`, defektes SQLite) | abbrechen/Report | Fehlereintrag in `errors`, CLI-Exit 1 | keine Teilanwendung ohne Meldung |

## 3. Konfliktfälle (Belege)

Verifiziert in `tests/test_registry_context.py::TestConflicts` (3 Fälle) und
per Live-Smoke (14.09.):
Ziel-Record `Alpha TARGET` (updated=9e9) bleibt nach `apply` unverändert, wenn
der Legacy-Record `Alpha LEGACY` (updated=100) abweicht → `target-kept`;
Legacy-neuer-Fall importiert `Gamma new` (updated 2000 > 1000) und schreibt
`{'action': 'imported-newer', 'tool': 'gamma', …}` ins Journal — die
Quell-DB behielt dabei `Gamma LEGACY`.

## 4. CLI-Syntax

Aus `--help` kopiert (14.09.2026):

```
usage: godsvoice registry [-h] {status,migrate} ...

usage: godsvoice registry status [-h] [--runtime RUNTIME] [--db DB]
                                 [--project PROJECT] [--json]

usage: godsvoice registry migrate [-h] [--dry-run | --apply]
                                  [--runtime RUNTIME] [--db DB]
                                  [--project PROJECT] [--json]

usage: godsvoice tools list [-h] [--db DB] [--runtime RUNTIME]
usage: godsvoice tools show [-h] name [--db DB] [--runtime RUNTIME]
  --db DB            explicit tools DB (default: the runtime DB; see
                     `godsvoice registry status`)
  --runtime RUNTIME  runtime dir (default: $HERMES_HOME/godsv)

usage: godsvoice tools promote-global [-h] [--evidence-project ...] [--db DB]
                                      [--runtime RUNTIME] [--json]
```

`tools list` zeigt je Spec Scope + Version aus
`generated_tools/*/manifest.yaml` (Loader `capability.load_tool_entries`,
nur gelesen) und `project_id`, wenn gesetzt:

```
[approved ] money_convert (python) — Convert money amounts between currencies via provider rates · scope=legacy · version=0.1.0
```

`tools show <name>` liefert das Spec-JSON plus Block
`"registry": {scope, version, project_id, path}`.

Alle `tools`-Handler (`list|show|propose|approve|revoke|compose|build|builds|
request|fulfill|promote-global`) sowie `mcp serve` laufen über
`_registry_context(args)` (`src/godsv/cli.py:500`); `build` und `mcp serve`
benutzen denselben Kontext für Builder-Runtime, Store und Projekt-ID (Default
Projekt = cwd — Build und Serve teilen den Kontext, sonst leere MCP-Surface).
Read-Pfade legen nichts an (`_tools_store` `:516`), Schreib-Pfade erzeugen
Parent + DB (`_tools_store_rw` `:523`).

Live-Beleg (read-only, 14.09.):
```
$ godsvoice registry status
runtime:    /home/willy/.hermes/godsv
tools db:   /home/willy/.hermes/godsv/tools.db (exists, 3 spec(s))
generated:  /home/willy/.hermes/godsv/generated_tools (4 tool(s))
project:    remote:github.com/bertus37987/gods-voice
legacy:     /home/willy/projects/gods-voice/.godsv/tools.db — not present
```
(Nebenbei sichtbar: DB 3 Specs vs. 4 generierte Tools — genau die Divergenz,
die §1.2 sichtbar machen wollte.)


## 5. Tests

**Neu (`tests/test_registry_context.py`, 27 Testfunktionen → 33 Fälle inkl.
7er-Parametrisierung):**

| Klasse | Was belegt wird |
|---|---|
| `TestResolverDefaults` (8) | Runtime-Default aus tmp-`HERMES_HOME`, `GODSV_HOME`-Vorrang, explizites `--db`/`--runtime` gewinnt, bestehende Runtime-DB + kein Legacy ⇒ keine Verhaltensänderung, Legacy-Fund in cwd und über expliziten Projektpfad |
| `TestDryRun` (2) | Dry-Run berichtet Specs (status/version/updated) und schreibt **nichts** (kein Ziel, kein Backup, kein Journal); ohne Legacy ist auch `apply` ein No-op |
| `TestApply` (3) | Backup von Quelle und Ziel (`<db>.pre-migration-<ts>.bak`), Quelle überlebt, Merge-Ergebnis, Journal-Zeilen, **Idempotenz** (zweiter Lauf: 0 neu, Journal unverändert) |
| `TestConflicts` (3) | Namenskonflikt wird gemeldet statt überschrieben (`target-kept`), Legacy-neuer-Record wird importiert **und** journaliert, Dry-Run kündigt die Übernahme an |
| `TestCliSurface` (11, davon 7 parametrisiert) | `registry status --json`/Text, `registry migrate --apply` (mit Backup) über `main()`, `tools list` liest default die **Runtime-DB, nicht cwd**, Scope/Version/projekt-Annotation, fehlendes Manifest ⇒ `scope=-`, `tools show`-JSON `registry`-Block, `--db` gewinnt, alle Subcommands antworten `--help` (Parser-Layer!), Parser-Defaults (`db=None`) |

**Ergänzt (`tests/test_tool_scopes.py`, jetzt 31):** §1.9-CLI-Seite —
`TestCliPromotionEvidence` (6): ≥2 fremde Kontexte via **tmp-Git-Repos**
(Pfade werden zu `remote:…`-IDs normalisiert), variable Belege (Pfad + bereits
aufgelöste ID), <2 Kontexte ⇒ Blocker + Exit 1 + Manifest unverändert, fehlende
Negativtests ⇒ Blocker trotz Belegen, Klartext-Secret ⇒ Blocker,
Help-Aussage „user-wide in this installation, never published".

**Ausgeführte Läufe (14.09.2026, `.venv/bin/python -m pytest <dateien> -p no:randomly`):**

- `tests/test_tool_scopes.py tests/test_registry_context.py` → **64 passed in 1.14s**
- `tests/test_tool_scopes.py tests/test_registry_context.py tests/test_capability_flow.py tests/test_mcp_scope.py tests/test_mcp_server.py tests/test_demo_cli.py` → **92 passed in 6.89s**
- Bereich erweitert (inkl. `test_mcp_stdio_e2e.py`, `test_tools_store.py`) → **114 passed in 10.58s**
- Gesamt-Suite (alle Tests, `.venv/bin/python -m pytest tests/ -p no:randomly`) → **1283 passed, 1 warning in 100.34s, RC 0** (14.09.2026; ein voriger Lauf zeigte 2 rote Tests in `test_capability_learning.py` durch fremdes WIP an `capability_health.py` — nach dessen Commit grün, kein Befund dieses Blocks)
- Lint: `.venv/bin/ruff check src/godsv/cli.py src/godsv/registry.py tests/test_registry_context.py tests/test_tool_scopes.py` → alle Prüfungen bestanden.

## 6. Restgrenzen / Welle 2

- **Panel und MCP-Modul übernehmen den Resolver erst in Welle 2.** Die
  Panel-Seite (`integrations/hermes/godsv_plugin/dashboard/plugin_api.py:
  runtime_dir()`) und die mcpserver-Interna bestimmen ihre Runtime noch
  selbst; der **Hook-Punkt** ist `godsv.registry.resolve_registry` /
  `default_runtime_dir` (beide read-only, keine Imports in deren Richtung).
  Diese Dateien sind B1/B5-Eigentum und wurden hier nicht angefasst.
- `mcp serve` (CLI, dieses Block) defaultet jetzt auf **Runtime-DB + cwd-
  Projektkontext** — damit teilen Build und Serve den Kontext. Die
  mcpserver-Signaturen selbst sind unverändert.
- **Migration umfasst `tool_specs`** (die Registry-Tabelle). Manifests in
  `generated_tools/` werden nicht verschoben — sie liegen bereits im Runtime-
  Verzeichnis; Legacy-Runtimes mit eigenen `generated_tools` außerhalb von cwd/
  Projektpfad werden nicht automatisch gefunden (bewusst: keine Glob-Suche über
  das Dateisystem).
- **Kein Auto-Migrieren**: Migration ist ausschließlich explizit
  (`registry migrate --apply`); Panel-Trigger/First-Run-Migration gibt es
  nicht (bewusst — Owner-Entscheidung).
- **Kein Publish**: „global" = nutzerweit lokal in dieser Installation;
  `promote-global` schreibt nur das Manifest (Version-Bump via
  `capability._bump_version`, Provenance `promoted_from`), nie in einen Hub.
- `--evidence-project` normalisiert **Pfade** über
  `project_id_for`; bereits aufgelöste IDs werden unverändert übernommen.
  Die Promotion-Logik selbst (`capability.promote_tool`) blieb unangetastet
  (B1) — gemeldet an die Zentrale, falls dort Semantik geändert werden soll.
- Nicht abgedeckt: gleichzeitige Migration zweier Prozesse (kein Lock) —
  die Ziel-DB nutzt WAL wie der Store; Konfliktfenster ist dokumentiert,
  aber kein verteiltes Locking.

