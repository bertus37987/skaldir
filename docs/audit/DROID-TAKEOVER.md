# DROID-TAKEOVER — Übernahme durch den Orchestrator (2026-09-27)

Statusdokument der Übernahme von Skaldir. Historische Berichte sind Hinweise,
keine Beweise für den heutigen Zustand. Dieser Stand wurde am 2026-09-27
gemessen (nicht aus Berichten übernommen, sofern nicht zitiert).

## 1. Ausgangszustand (gemessen)

### Git
- Branch `main`, HEAD `95b69d6` ("fix+feat(host): /dev/tty eigenstaendig, Fenstermanager auf dem eigenen Display, temporaere Wurzeln mit Eigentumspruefung"), 49 Commits.
- Remote: `origin` → `https://github.com/bertus37987/Skylia-Agent.git`. **Keine Push-Autorisierung** über den initialen Transfer hinaus; es wurde nichts veröffentlicht.
- **Uncommittete Arbeit (erhalten, nicht angetastet):**
  - geändert: `.gitignore`, `src/skaldir/cu_host_session.py`
  - neu: `src/skaldir/input_backend.py`, `tests/test_input_backend.py`,
    `tests/test_live_desktop_launch_env.py`, `tests/test_live_keyboard_backend_gate.py`
  - neu: `docs/audit/ABNAHME-A-LIVE-EINGABE-UND-CALC.md`, `ABNAHME-INDEX.md`,
    `BACKEND-ENTSCHEIDUNG-TASTATUR.md`, `CALC-ABNAHME-A-STAND.md`,
    `CALC-FENSTER-HYPOTHESEN.md`, `POPUP-STOPP-UND-CALC-ISOLIERUNG.md`,
    `QUELLENPRUEFUNG-AMD.md`
- **Lokale Sicherung** (ohne Veröffentlichung, ohne Secrets): `/home/willy/backups/skaldir-takeover/`
  (`head-*.txt`, `diff-*.patch` für `cu_host_session.py`/`.gitignore`, `untracked-*.tar.gz`,
  `untracked-list-*.txt`). Der Arbeitsbaum selbst wurde nicht verändert.

### Installierter Launcher (AUD-01 relevant)
- `~/.local/bin/skaldir` → `~/.local/share/uv/tools/skaldir-agent/bin/skaldir` (Symlink).
- Das ist eine **Snapshot-Installation** (uv tool): Code liegt als Kopie in
  `~/.local/share/uv/tools/skaldir-agent/lib/python3.12/site-packages/skaldir/`,
  installiert **heute 13:11–13:12**, `skaldir 1.5.0`, Python 3.12.13.
- Repo-eigenes `.venv`: Python 3.11.16 (`requires-python >=3.11`).
- **Konsequenz:** Repo-Änderungen sind in der installierten CLI erst nach
  Re-Installation live (z. B. `uv tool install --force <pfad>`), Launcher-Umschaltung
  erst nach Ersatzbeweis mit Backup des alten Launchers (AGENTS.md-Regel).

### Laufende Prozesse (beobachtet, nicht beendet)
- Hermes-Desktop + `hermes_cli serve` (Profile `skaldir-agent` und `default`), PID 6779/7096/7142.
- `ydotoold` (eigener Start aus Vorlauf): Socket `/tmp/skaldir-ydotoold/ydotoold.sock`,
  aktuell **verweigert Connect** (laut Probe-Bericht) — als `BACKEND_MISSING` behandeln.
- Penpot-Docker-Stack (7 Container, u. a. Ports 5001/5050/9001, Postgres 5432, Valkey 6379):
  **fremd, nicht anfassen**. Ebenso Ports 631 (CUPS), 5355, 25463 (`.cline`), 45501/42561 (hermes).
- Vorhandene Calc-Testinstanzen aus dem Vorlauf (eigene), fremde LibreOffice-Instanzen unangetastet.

### Tests & Runtime
- 7386 Testfunktionen unter `tests/`; Ausführung via `uv run pytest` (Repo-Root).
- Scheck: `uv run pytest tests/test_input_backend.py -q` → 11 grün (heute).
- `skaldir selftest --json` läuft; Runtime-Ziel `GODSV_HOME=/home/willy/.hermes/godsv` (tools.db dort).
- Relevante Env-Namen: `SKALDIR_HOME`, `SKALDIR_LLM_MODEL`, `SKALDIR_LLM_REASONING`,
  `SKALDIR_HTTP_BASE_URL`, `GODSV_HOME`, `TOOL_WORKSPACE`, `VAULT_KEY` (Werte nie loggen).

### Desktop-Host
- Fedora, GNOME/Wayland (`XDG_SESSION_TYPE=wayland`, `DISPLAY=:0` Xwayland, Mutter).
- `xdg-desktop-portal` 1.21.1 (+ `-gnome`/`-gtk`) aktiv, `org.gnome.Mutter.RemoteDesktop` am
  User-Bus, `libei`/`libeis` 1.5.0 installiert → offizieller Portal-/EIS-Weg vorhanden.
- GUI-Apps vorhanden: `blender`, `kdenlive`, `soffice`/`libreoffice`.
- **Kein SOUL.md im Repo** (MEM-02-Lücke, zu erstellen).

## 2. Konsolidierte Befunde aus der Bestandsaufnahme

Quellen: Distanz-Berichte zu Masterplan/Audits, P0-Codeanalyse, AMD/Calc-Auswertung
(alle mit Dateizeilen-Belegen). Vollständige Abgleichstabelle kommt nach
`docs/audit/DROID-FEATURE-MATRIX.md`.

1. **Masterplan-Inventar:** 134 Pflicht-IDs (AUD/ARCH/CORE/WEB/TOOL/BATCH/ROLE/APR/CU/DESK/STOP/
   BROWSER/SUB/SKILL/BUILD/DOCKER/FLOW/SCHED/GOAL/VERIFY/RUN/JEV/VOICE/MEM/CTX/UI/PERF/QA) plus
   BR-01…15, T-01…52, L-01…05, Produktprüfungen A–F, GOAL-CORE-Szenarien A–I.
   `docs/masterplan/05-ANFORDERUNGSMATRIX.md` ist eine Leermatrix; Statusansprüche stammen aus
   Audit-/Statusberichten (meist nur Komponententests N2/N3, selten Host-Live N4).
2. **P0-Eingabeintegration — alle 6 gemeldeten Defekte bestätigt (PRESENТ) in
   `src/skaldir/input_backend.py` / `cu_host_session.py`:**
   (1) kein Ereignisempfänger, nur Logdatei-Warte; (2) Probe ohne eigenes Testfenster
   (Eingabe landet im Nutzerfenster); (3) `marker` wird nie ausgewertet, jede Logänderung
   zählt als Zustellung; (4) X-Keycodes unverändert an uinput/ydotool (`38` = X-'a' wird
   evdev `KEY_L`; korrekt: `KEY_A=30`); Modifier/Großschreiben/Kombos fehlen;
   (5) negatives Probe-Ergebnis für Runner-Lebensdauer gecacht; (6) Tests spiegeln die
   Fehler (Cache-Test mockt die geprüfte Methode selbst). Dazu: Daemon-Socket-Erkennung
   zeigt auf die falsche Standard-Datei, Referenz auf nicht existierendes `xlib_zaehler.py`.
3. **AMD→Calc-Abnahme:** Quelle/Schritt 1–5 teilweise, Schritte 6–7 (Eingabe, Speichern/
   Wiederöffnen) **blockiert am selben Eingabe-Blocker** (Kernel-Event ja, Ziel-Fenster 0 Events).
   Korrekturen zum Vorbericht: Zahlen in `AMD_latest_data.ods` sind **typisierte Floats**
   („Textwerte" trifft nur auf Datumsfelder zu); `amd_hist.json` ist korrupt (2 Bytes `[[`);
   die Abweichungen 52W-Tief/Volumen stammen aus `amd_data.json` vs. späterer Yahoo-Abfrage
   (Snapshot-Zeitpunkt), nicht nachweislich „erfunden". 630.63 = letzter Marktpreis
   (`regularMarketPrice`), Schlusskurs 630.99, Kurszeit 2026-09-25 20:00:01 UTC,
   Abrufzeit bisher nicht dokumentiert. Nutzerdatei `/home/willy/AMD_latest_data.ods` bleibt
   unangetastet; Abnahme erfolgt mit neuer, eindeutig benannter Datei.
4. **Widersprüche in Altdokumenten (für die Feature-Matrix aufzulösen):** Bare-Start = Chat
   (Commit `d5ac4e3`) vs. TUI-Pflicht (UI-01); Scheduler-Status je Dokument unterschiedlich
   (`8e78591` meldet Ausfuehrungsdienst, Szenario H weiter unbelegt); BR-08-Identitätskonflikt
   (Notstopp vs. Lizenztest — Notstopp gilt); DESK/STOP-Status je Snapshot unterschiedlich —
   nur neue Messung zählt.
5. **Nicht live belegt (offene Nachweise):** Bare-Start-TUI (Zielbild H), doppeltes Escape
   Ende-zu-Ende auf beiden Backends bei echter Eingabezustellung, Eingabezustellung ins
   Ziel-Fenster überhaupt (der zentrale Blocker), Zen-/Browser-Fensternutzung, geplanter
   Workflow-Lauf (Szenario H), JEV-LIVE, Produktprüfungen A–F, Szenarien A–I.

## 3. Prioritäten (verbindlich)

1. **P0 — Eingabeintegration korrigieren:** die 6 Defekte, vollständige begrenzte Probe
   (eigenes Testfenster, aktiver Ereignisempfänger, Marker-Korrelation, Lease/Fencing/Not-Aus,
   sauberes Cleanup), Zustandsmodell (Backend fehlt / Zustimmung fehlt / Probe fehlerhaft /
   gesendet / am Ziel beobachtet / App-Wirkung verifiziert), echte Tests statt Mock-Spiegel.
   Backend-Wahl nach belegten Fähigkeiten; Wayland-Portal-/EIS-Weg prüfen, nötige
   Zustimmungen transparent anzeigen. Keine Host-/Compositor-Defekt-Behauptung vor
   gesicherter Messkette.
2. **P1 — AMD→Calc vollständige Produktabnahme:** Rohbeleg sichern, Quellen/Zuordnung
   (Symbol, Börse, Währung, Kursart, Kurs-/Abrufzeit), neue ODS mit echten Zahlen/Datumswerten/
   Formatierung/Quellen, Öffnen im echten Calc-Desktop, sichtbare Prüfung, Testzelle über den
   produktiven Computer-Use-Pfad, Speichern/Wiederöffnen mit Wertnachweis.
3. **P2 — Coding-Harness-Abnahme** über den normalen Chat (Hello-World-Standardtest),
   parallele Batches, Langzeitprozesse, Streaming/Abbruch; CLI-Erhalt.
4. **P3 — Web & Browser:** schlüsselfreie Suche, Multi-Tab-Research-Abnahme, keine
   Fensterflut, ehrliche Quellen.
5. **P4 — Subtools/Tool-Builder/Workflows:** Docker-Research-Tool mit localhost-Dashboard
   (erstellen, registrieren, vom Agenten nutzen), gespeicherte GUI-Abläufe mit Vorbedingungen,
   gemischter Workflow aus ≥2 Tool-Arten, Scheduling/Wiederaufnahme ohne Doppelausführung.
6. **P5 — Kreativ & Steuerung:** Blender-Projekt, Videoaufgabe mit echter GUI (kdenlive)
   samt Exportprüfung, Doppel-Escape/Approval-Grenzen im produktiven Pfad, UI-Zielbild mit
   überprüfbarem Rückfall auf den Chat.
7. **Durchgängig:** `docs/audit/DROID-FEATURE-MATRIX.md` (Anforderung → Einstieg → Registrierung
   → Dispatcher → Executor → Ergebnis → Verifikation → Tests → Restlücke) und
   `docs/audit/DROID-ACCEPTANCE.md` aktuell halten.

## 4. Offene Nachweise (Abnahmekriterium §8 des Auftrags)

- [ ] Produktiver Eingabenachweis: Tastatureingabe im Ziel-Fenster beobachtet (nicht nur Kernel-Event / rc=0).
- [ ] Hello-World-Standardtest über den normalen Chat inkl. tatsächlicher Ausgabe.
- [ ] Calc-Kette Schritt 6+7 (Testzelle, Speichern, Wiederöffnen, Wertnachweis).
- [ ] Browser-Research über mehrere Tabs/Quellen mit Scrollen und Ergebnisbericht.
- [ ] Docker-Research-Tool mit localhost-Dashboard, registriert und anschließend genutzt.
- [ ] Blender-Projekt mit nachvollziehbarem Modell + gespeichertes Projekt.
- [ ] Videoaufgabe mit realer GUI-Bedienung + überprüfter Export.
- [ ] Workflow aus ≥2 Tool-Arten.
- [ ] Scheduling + Wiederaufnahme ohne doppelte Ausführung.
- [ ] Parallele unabhängige Tool Calls nachweislich parallel.
- [ ] Stop/Doppel-Escape + Approval-Grenzen im produktiven Pfad.
- [ ] Vollbild-TUI-Zielbild oder dokumentierter, überprüfter Rückfall auf den Chat.

## 5. Betriebsregeln für die Mission (aus AGENTS.md übernommen)

- Kein Rewrite, kein Reset, kein neues Repo; die funktionierende CLI bleibt erhalten.
- `/home/willy/projects/gods-voice` nur lesend; `~/.hermes/godsv/app-src` nie ändern;
  fremde Sessions nie beenden; Nutzerartefakte (u. a. `/home/willy/AMD_latest_data.ods`) unangetastet.
- Keine automatische Veröffentlichung (Git-Push) — nur bei expliziter Autorisierung.
- Keine Käufe/Trades als Tests; keine Pauschalfreigaben; Guards nicht abschalten.
- Logs mit echten Exitcodes (keine Fehler verdeckenden Pipelines); Secrets nie in Logs/Gits.
- Ein Fixture-/PTY-/Container-Ergebnis ist kein Host-Live-Ergebnis.

## 6. Infrastruktur-Hinweis zur Übernahme

Die automatisierten Recherche-Subagente liefen zeitweise in Inaktivitäts-Timeouts;
die betroffenen Bestandteile (Betriebs-/Capability-Abgleich) wurden durch gezielte
Einzelprüfungen ersetzt. Die fehlende Resttiefe (Bereiche E–H auf Zeilenebene) wird die
Mission als erstes Feature über die Feature-Matrix nachziehen.

## 7. Nachtrag (2026-09-30, bei der Git-Adoptierung — Git-Stand aktualisiert)

- Die in Abschnitt 1 gelistete **uncommittete Code-Arbeit ist seitdem committet**:
  `input_backend.py` / `cu_host_session.py` und die drei Testdateien in
  `69acba2`, `715447d`, `f667366` (2026-09-28). Die hier adoptierten acht
  Abnahme-Dokumente und `tests/test_live_desktop_launch_env.py` werden mit diesem
  Nachtrag committet.
- `git status` zeigt am 2026-09-30 nur noch vier **geänderte Laufzeit-Artefakte**
  (`.gitignore`, `cu_runs.jsonl`, `events.db`, `uv.lock`) — bewusst uncommittet;
  sie gehören zur Nutzerarbeit und zu den noch ausstehenden Live-Läufen.
- `tests/test_live_desktop_launch_env.py` ist grün: 11 passed, Exit 0
  (`uv run python -m pytest tests/test_live_desktop_launch_env.py -q`, 2026-09-30).
