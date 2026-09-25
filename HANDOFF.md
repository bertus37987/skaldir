# HANDOFF — Einstieg für die nächste Sitzung

Stand: 25. September 2026, nach Sitzung 1.
Projekt: `/home/willy/Agentic_Projects/Skaldir_Agent`
Repository: <https://github.com/bertus37987/Skylia-Agent> (**privat**, verifiziert)

## Was sich geändert hat

| Bereich | Zustand |
|---|---|
| Migration | 1095 Dateien selektiv übernommen, SHA-256-Protokoll in `docs/audit/MIGRATION-MANIFEST.md`; Quelle unverändert |
| Setup | neue `.venv` (Python 3.11.16), `skaldir 1.5.0`, Importpfad = Zielcode, `--version`/`--help`/`selftest --json` belegt |
| Repository | `bertus37987/Skylia-Agent`, privat, Branch `main`, Initialcommit `0b09a8a` |
| Neu implementiert | `tui.py`, `approval_modes.py`, `desktop_lease.py`, `stop_signal.py`, `surface_cli.py` (+ Tests) |
| Behoben | 3 Defekte im neuen Code, 3 Altlasten aus der Quelle (Dokumentpfade, Logo-Ableitung, `_store_for`) |
| Abhängigkeit | `schemathesis>=4` in die `dev`-Extras aufgenommen |

## Was wirklich getestet wurde

`docs/audit/VALIDATION.md` ist die Wahrheitsquelle. Kurzfassung:

- 109 gezielte Tests grün (`test_stop_signal` 23, `test_desktop_lease` 24,
  `test_tui_layout` 52, `test_tool_search_integration` 5, `test_schemathesis_cli` 19,
  `test_gv_benchmark` 63, `test_no_orphan_modules` 2, `test_logo_art` 13 — Gruppen
  überlappen, daher die kleinere Summe im gemeinsamen Lauf) plus **1 `xfail(strict)`**.
- Baseline-Volltestsuite vor den Fixes: `16 failed / 7462 passed / 34 skipped / 63 errors`
  in 1496 s. Herkunft gegen Initialcommit `0b09a8a` (Zweit-Worktree) verifiziert:
  `12 failed, 5 errors` — also Altlasten, nicht durch diese Sitzung.
- Zwischenstand nach den Altlasten-Fixes: `3 failed / 7642 passed / 33 skipped / 1 xfailed`.
  Diese drei waren **von dieser Sitzung** und sind behoben (`VALIDATION.md` §3.2 erklärt den
  einen, bei dem zwei Verträge bewusst auseinandergehen).
- **Abschluss-Volltestsuite: `7648 passed, 33 skipped, 2 warnings` in 1524 s,
  `PYTEST_EXIT=0` — 0 Fehler, 0 xfail** (`logs/final-suite3.log`; `VALIDATION.md` §6).

## Was unsicher bleibt

1. **STOP-05 — globaler Doppel-Escape ist NICHT nachgewiesen.** `skaldir stop report`
   meldet `double_escape_proven: false`. Konkreter Blocker: `/dev/input/event*` ist
   `root:input`, der Nutzer ist nicht in Gruppe `input` — eine Systemänderung, die der
   Besitzer vornimmt; Skaldir fordert kein `sudo`. Die Portal-Route
   (`org.freedesktop.portal.GlobalShortcuts` v2) ist vorhanden, garantiert aber nicht die
   Sequenz „Escape zweimal in 500 ms“. Der Host-Steuerungsmodus wird **nicht** als fertig
   freigegeben.
2. ~~`tool_search` ohne Aufrufvertrag~~ — **behoben** (D-10).
3. **Wayland- und X11-Live-Abnahme der Desktopsteuerung** offen (aktuelle Sitzung ist
   Wayland; XWayland ersetzt keine X11-Abnahme).
4. **Produktprüfungen B–F** offen; C–F brauchen eine Nutzerentscheidung über Anwendung
   und Testobjekt, bevor sie ehrlich lauffähig sind (02 §7, §8).
5. **SCHED-01..04**: Code vorhanden (`nightshift.py`), aber kein nachgewiesener
   Zeitplan-Lauf mit Zeitzone/Doppelzustellung/Überlappung.
6. **JEV live**: laut Quellstand ist die Live-Entscheidungsebene nicht belegt (JEV-LIVE).

## Nächste Aktion (in dieser Reihenfolge)

1. ~~`tool_search`-P0 beheben~~ — **erledigt** (D-10): der typisierte Vertrag wird aus
   dem Descriptor durchgereicht; der `xfail(strict)`-Test steht positiv und ist
   verschärft, plus ein Negativtest gegen erfundene Schemata.
2. STOP-05: beim Besitzer die Aufnahme in Gruppe `input` anfragen
   (`sudo usermod -aG input willy`, Neu-Login) und danach `skaldir stop report` erneut
   ausführen; anschließend physischen Doppel-Escape-Test (02 §3).
4. Produktprüfung B (Browserrecherche) als erste Computer-Use-Abnahme.
5. Produktprüfungen C–F: Testobjekte/Anwendungen mit dem Nutzer festlegen.

## Sichere Statusabfrage

```bash
cd /home/willy/Agentic_Projects/Skaldir_Agent
git status -sb                      # Branch und offene Änderungen
git log --oneline -5
.venv/bin/skaldir --version
SKALDIR_HOME=/tmp/skaldir-check .venv/bin/skaldir selftest --json | tail -1
.venv/bin/skaldir stop report --json | head -20    # STOP-05-Status
tail -3 logs/final-suite.log
```

PIDs sind keine dauerhafte Identität. Kein Server, kein Browserprofil und keine
Live-Desktopsitzung bleiben von dieser Sitzung offen.

## Rollback / Rückweg

- Quellprojekt `/home/willy/projects/gods-voice` ist unverändert; alle ausgeschlossenen
  Dateien liegen dort vollständig.
- `git worktree remove /tmp/skaldir-pristine` entfernt den nur zur Fehlerherkunft
  angelegten Zweit-Worktree.
- Der neue Workspace ist ein eigenes Git-Repository ohne Verbindung zur Quellhistorie;
  `docs/audit/MIGRATION-MANIFEST.md` stellt die Herkunft je Datei her.
- Globaler Launcher **nicht** umgestellt: `~/.local/bin/skaldir` und
  `~/.local/bin/godsvoice` zeigen weiter auf das alte Projekt. Umstellen erst nach
  ausdrücklicher Freigabe, mit Backup des alten Launchers.

## Nicht behaupten

Der Transfer und die Repo-Erstellung sind erfolgreich — **Skaldir ist deshalb nicht
fertig**. `docs/audit/IMPLEMENTATION-AUDIT.md` trennt `getestet` von
`implementiert-unbestätigt` von `extern-blockiert`.
