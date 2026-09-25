# RELEASE_NOTES.md — Skaldir Agent (Kapitel 06 §8)

Stand: 16.09.2026 · Branch `godsv-supervisor-hardening`. Erstellt für den integrierten
Kandidat-Stand; Versionen bumped erst mit dem nächsten Release-Commit (aktuell v1.5.0).

## Hier ist das Projekt im Stand dieser Runde

**Produkt:** Skaldir Agent — Supervisor-Harness über einen Hermes-Main-Agent, ausgeliefert
als native Fedora-App (Hermes-Desktop mit eingebackenem Panel + Branding) plus CLI (`godsvoice`).
Chat ist die einzige Arbeitsfläche (V2-IA); Einstellungen bleiben motiviert über die
System-Ebene erreichbar; Watcher-Voice arbeitet über Ereignisse/Hinweise/Redirect im Chat.

## Neu in dieser Runde (verifiziert, Commit-Kette im Commit-Log)

- **Rebranding Unity:** Wortmarke `SKALDIR AGENT` (Empty-State), Desktop-Launcher „Skaldir",
  WM_CLASS `SkaldirAgent`, UA `SkaldirAgent/<ver>`; Migration des GV-Datenpfades.
- **Rebranding-Nachzieher:** zwei Stale-Tests auf identität gepinnt (`1005aff`, test-only).
- **Auditpaket-Grundlage:** Kriterienregister (77) + CODE_MAP, verifizierbar reproduzierbar
  (`python3 tools/build_audit_register.py --check`); PLATFORM-MATRIX, UPDATE_COMPATIBILITY,
  SKILLS_USED, BENCHMARK_MANIFEST (schema-frozen), TEST_REPORT (lebend) ergänzt.
- **RF-02/RF-03 (from precursor, verified this round):** requestweite Deadline (HERMES_REQUEST_DEADLINE_SECS,
  Pilot 180 s, Grenzschließung mit 0.00 s Overshoot im lokalen Pilot) + monotone Stream-Clock
  (Wall-Clock-Sprung inert, echter 1-h-Stale noch fire). Overlays + Proben + 12 hermetic Pins.

## Ausstehend (ehrlich)

- RF-04–RF-12 Umsetzung (Engine-Worker aktiv); RF-01/RF-11/RF-12 brauchen Modell- und
  Budgetfreigabe für einen echten 152k/80-Iterationen-Lauf — `not_run`, keine Zahlen erfunden.
- Chat-IA-Vollzugriff (Cockpit-/Activity-Nav-Rückbau durch Worker C, Tools/Treasury-Chat-Einfolge später).
- Benchmark-Resultaten (BENCHMARK_MANIFEST.json trägt bewusst Nummern-frei).
- Plattform-Matrix: nur Linux-x64 teilweise belegt; Windows/macOS blockiert (Build-Hosts, Signierung).
- Keine öffentliche Publish-Aktion (Regel: Freigabe nötig).

## Rollout / Startkommandos (bestehender Pfad)

```bash
cd ~/projects/gods-voice
source .venv/bin/activate
pytest -q && node tools/check_desktop_plugin.mjs   # beide Gates
godsvoice desktop build-app && godsvoice desktop --help      # Problemeigener Pfand
```
