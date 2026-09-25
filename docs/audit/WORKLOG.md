# Arbeitsprotokoll

## 25. September 2026 — Planvorbereitung

- Neuer Zielordner war leer; vorhandene übergeordnete AGENTS.md gelesen.
- Masterplan mit 134 Anforderungen sowie historische Detailpläne und Slash-Audit kopiert.
- Letzte vier UI-Entscheidungen in aktuellem Masterplan/TUI-Dokument übernommen.
- AGENTS.md, Transfer-/Implementierungsauftrag, README und initiale Ignore-Regeln angelegt.
- Quellcode nicht kopiert, altes Projekt nicht verändert, GitHub-Repo noch nicht angelegt.
- Nächster Schritt für den Coding-Agenten: Quellinventur und selektives Transfermanifest.

---

## 25. September 2026 — Sitzung 1: Transfer, Setup, Repository, Umsetzung

Kurz und nachvollziehbar. Vollständige Befehlsausgaben liegen unter `logs/` (git-ignored);
die überprüfbaren Zusammenfassungen stehen in
[VALIDATION.md](VALIDATION.md) und [DECISIONS.md](DECISIONS.md).

### Gelesen
`AGENTS.md`, `STARTPROMPT.md`, `TRANSFER-UND-IMPLEMENTIERUNG.md`,
`docs/masterplan/00-START-HIER.md` … `05-ANFORDERUNGSMATRIX.md` (1195 Zeilen),
Quell-`AGENTS.md` (Feature-Standard) und Quell-Statusmatrix.

### M0 — Quell-Inventur
- Branch `godsv-supervisor-hardening`, HEAD `a8fcaa1b`, **dirty**: 166 modifiziert,
  192 untracked, 1296 tracked.
- Wichtig: 43 untracked **Quelldateien** (`src/skaldir/*.py` inkl. `agent_toolset.py`,
  `cu_host_session.py`, `knowledge.py`, `skills_builtin/`, `tool_archive.py`,
  `verify_flows.py`) und `uv.lock` sind relevante Arbeit — sie wurden übernommen, nicht als
  „Altlast“ verworfen.
- Einstieg verfolgt: `perf_lazy.main` → `cli.main` → `repl.launch_from_bare_call` →
  `chatbar.launch_from_bare_call`. Testrunner: `pytest tests -q -o addopts=""`.

### Migration
- 1095 Dateien übernommen, 1649 ausgeschlossen, 1 Kollision (`.gitignore`) zusammengeführt.
  Manifest: [MIGRATION-MANIFEST.md](MIGRATION-MANIFEST.md) mit SHA-256 je Datei.
- Erste Umlagerung `docs/` → `docs/upstream/` erzeugte 63 Testfehler (Pfadreferenzen aus
  Code/Tools/Tests). **Zurückgenommen** (D-01); Dokumentpfade beibehalten.
- Logo-ASCII-Kunst neu abgeleitet (D-06) — sie trug den absoluten Quellpfad.

### Setup
- Neue venv (`uv venv .venv --python 3.11`), Installation aus dem Ziel, keine
  Absolute-Referenz auf die alte venv. `skaldir --version` → `1.5.0`; `--help` zeigt
  66 Root-/278 Leaf-Commands. Importpfad = Zielcode, belegt.
- `skaldir selftest --json` → `ok true`, 8 Layer, 26 Probes, 0 nicht erreichbar, exit 0.

### Repository
- `gh auth status` → `bertus37987`, aktiv. Kollisionsprüfung: `<owner>/Skylia-Agent`
  existierte **nicht**.
- Secret-Prüfung des tatsächlichen Commitbestands (1123 Dateien, 9 Muster): 21 Treffer,
  alle dokumentierte Test-Platzhalter bzw. Fachprosa (D-08).
- `gh repo create bertus37987/Skylia-Agent --private --source . --remote origin`,
  `git push -u origin main`. **Verifiziert:** `private: true`, `visibility: private`,
  Owner `bertus37987`, Default-Branch `main`, HEAD `0b09a8a` == lokal.
- Quellprojekt unverändert; `~/.hermes/godsv/app-src` nicht angefasst.

### Implementierung (M2 + M6-Kern)
Neue Module, jeweils mit Tests:
- `src/skaldir/stop_signal.py` — Doppel-Escape-Detektor + Backend-Machbarkeitsprobe.
- `src/skaldir/desktop_lease.py` — Host-Desktop-Lease mit Fencing-Generation und Eck-Indikator.
- `src/skaldir/approval_modes.py` — drei bestätigte Approval-Modi über der vorhandenen
  Policy-Engine, Tab-Zyklus, pro Run.
- `src/skaldir/tui.py` — bestätigte Vollbild-TUI: Chat links, Tools oben rechts, Goal unten
  rechts, Vorschau + aufklappbare Details, ASCII/Plain, Fehler als Symbol + Wort.
- `src/skaldir/surface_cli.py` — `skaldir tui …` / `skaldir stop …` / `skaldir desk …`,
  inkl. `cli_contract`-Einträge; in `cli.py` registriert.

Drei echte Defekte im eigenen neuen Code gefunden und behoben (Blocker überschrieb die
Eingabezeile; ASCII-Modus ließ Unicode durch; `at=0.0` wurde als „kein Zeitstempel“
gelesen). Drei Altlasten im Quellcode behoben (D-01, D-06, `_store_for`-Auflösung).
`schemathesis>=4` in die `dev`-Extras aufgenommen (war undeklariert).

### Nachtrag: drei Fehler des Abschlusslaufs, alle von dieser Sitzung

Der Zwischenstand der Volltestsuite zeigte `3 failed` — geprüft und **nicht** der Quelle
zugeschrieben, sondern hier entstanden und behoben:

1. `tests/test_agent_loop.py` pinnt `tool_set_for(kind="registry", store=None)` als
   `AgentLoopError`. Meine ursprüngliche `_store_for`-Lösung hatte auch diese Fläche
   „bequem“ gemacht. **Zurückgenommen:** Die Loop-Fläche verwirft weiter, nur
   `build_agent_toolset` (dessen Signatur `store=None` zusagt) löst selbst auf. Der
   Kontrast ist als eigener Regressionstest gepinnt, damit ihn niemand „vereinheitlicht“.
2. `tests/test_cli_shared.py` fand meinen zweiten `register_contract_entries`-Helper als
   neuen Duplikatfund. Entfernt; `cli_shared.install_contract_entries` bleibt die eine
   Heimat.
3. `tests/test_schemathesis_cli.py`: Die Reproduktion von Vertragslücken hängt vom
   Kommandozustand ab — `completion.override` und `memory-retrieval-check` wechseln sich
   ab. Statt die Liste bei jedem Lauf umzubiegen, ist die Bedingtheit jetzt modelliert
   (`conditional: true` + `condition`) und das Gate ist zweistufig: keine unerklärte
   Widersprüche (absolut), und eine aufgezeichnete Lücke darf nur fehlen, wenn sie als
   bedingt markiert ist.

Danach: `184 passed, 1 xfailed` in 35 s für die betroffenen Dateien.

**Abschluss-Volltestsuite** (nach dem `tool_search`-Fix):
`7648 passed, 33 skipped, 2 warnings in 1523.59 s`, `PYTEST_EXIT=0` — **0 Fehler, 0 xfail**.

### Nachtrag 2: P0 `tool_search` ohne Aufrufvertrag — behoben

`tool_search` nannte Fähigkeiten ohne Argumentform. Ursache: `capability_mining` baute
`inputs: ["path:string"]` (ein Etikett) und las nie den Descriptor, den das Manifest mit
`contract: <pfad>` selbst benennt. Fix: `_typed_contract()` liest den Descriptor,
`_public_row()` reicht `input_schema`/`output_schema` durch. Ohne Descriptor bleibt der
Schlüssel weg — kein erfundenes Schema, dafür ein eigener Negativtest. Details D-10.

### Betriebslektion: `SKALDIR_HOME` in der Shell

Bei einer Zwischenregression fielen 47 Tests in `test_registry_context.py`. Nicht der Code
war schuld: in der Shell hing noch `SKALDIR_HOME=/tmp/skaldir-final-check` aus einer
früheren Prüfung exportiert. Mit `env -u SKALDIR_HOME` laufen dieselben Tests **33/33**.
Die Projektvorgabe, `HERMES_HOME`/`GODSV_HOME`/`SKALDIR_*` für Testläufe explizit zu
entfernen, ist kein Formalismus — sie ist die einzige Weise, wie ein Ergebnis über den
eigenen Shell-Zustand hinaus belastbar ist. Für Folgesitzungen: jeden Testlauf mit
`env -u SKALDIR_HOME` starten.

### Ergebnisse
- Neue/gezielte Tests: **184 grün** (plus 2 für den `tool_search`-Vertrag); kein
  `xfail` mehr offen. Details: [VALIDATION.md](VALIDATION.md).
- Baseline-Volltestsuite (vor den Fixes): `16 failed, 7462 passed, 34 skipped, 63 errors`
  in 1496 s. Herkunft gegen Initialcommit `0b09a8a` in einem Zweit-Worktree verifiziert:
  **dieselben** Fehlergruppen traten dort auf (`12 failed, 5 errors`) — Altlasten, nicht
  durch diese Sitzung entstanden.
- Abschluss-Volltestsuite → `logs/final-suite.log`.

### Nächste Schritte
1. Abschluss-Volltestsuite auswerten und in `VALIDATION.md` nachtragen.
2. `tool_search`-P0: `input_schema`/`output_schema` in `build_candidates` ergänzen; der
   `xfail(strict)`-Test schlägt dann an und wird positiv umgestellt.
3. STOP-05: Gruppe `input` beim Besitzer anfragen, dann physischen Doppel-Escape auf
   Fedora abnehmen (02 §3). Wayland-Live-Abnahme separat.
4. Produktprüfungen B–F: Testobjekte/Anwendungen mit dem Nutzer festlegen (02 §7, §8).
5. `scripts/proof-codex-ux-pty.py:47` trägt noch eine absolute Referenz auf das alte
   Checkout — durch Repo-Root-Auflösung ersetzen.

### Laufende Ressourcen
- Hintergrundprozess: Abschluss-Volltestsuite (`logs/final-suite.log`).
- Kein Server, kein Browserprofil, keine Live-Desktopsitzung gestartet.
- Live-Runtime `~/.hermes/godsv` wurde von `selftest` **gelesen**; spätere Läufe liefen mit
  isoliertem `SKALDIR_HOME`.

## 2026-09-25 — Browser-Upgrade: Recherche und vertiefter Implementierungsplan

- Ergänzt: `docs/masterplan/07-BROWSER-UPGRADE.md`, im Startdokument verlinkt; Leseindex aktualisiert.
- Originaldokumentation über Browser-Plugin gelesen: agent-browser, Playwright MCP, Playwright Browser, Browser Use und Browser Harness.
- Empfehlung: gepinnter lokaler agent-browser-Adapter plus bestehender ACT-Hostpfad; Zen-Anbindung separat belegen.
- Zielcode lesend stichprobenartig geprüft (HEAD `89b02a7209a6b1bae0b2e1fdb0eea363527925ad`): Browserlaunch, open_browser, Host-Kindprozessumgebung, Playwright-Verifikation, MCP- und Archivbausteine.
- Dokument enthält Session-/Aktionsverträge, Fehlerklassen, Pakete A–F, 15 Abnahmen und Hermes-Auftrag.
- Keine Runtime geändert, keine Browserwerkzeuge installiert, keine Host-Browserprofile verändert. Markdown-Links/Index geprüft; keine Laufzeit- oder Fedora-Live-Abnahme durchgeführt. Andere bereits laufende Arbeiten und ihre Testergebnisse bleiben davon unabhängig.

## 2026-09-25 — Browser-Empfehlung ausdrücklich bestätigt

Willy beauftragt, die Browser-Architektur als gemeinsame Empfehlung von ihm und Codex als technischem Architekturberater festzuhalten und Hermes zu empfehlen. In Browserplan, Startprompt und Paketübersicht aufgenommen. Bevorzugter Weg: lokaler gepinnter agent-browser plus bestehender ACT-/Zen-Hostpfad; technische Grenzen und erforderliche Live-Abnahmen unverändert. Nur Dokumentation geändert.
