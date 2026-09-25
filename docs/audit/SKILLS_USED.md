# SKILLS_USED.md — belegter Skill-Einsatz (Kapitel 06 §8)

Stand: 16.09.2026. Ein gelesener Skill ist kein Funktionsnachweis — hier steht,
welcher Skill bei welcher konkreten Arbeit angewandt wurde und welcher Prod­ukt-Nachweis
aus ihm folgte. Keine Einträge ohne benutzten Realpfad.

## In dieser Runde (16.09.) genutzt

| Skill | Konkreter Einsatz | Beweis / Produkt-Nachweis |
|---|---|---|
| `gods-voice` (Prozedur-Skill) | Etablierte Release-Schleife, Pitfalls (Suite-Zählung, Overlay-Mechanik, Rot-First-Regeln, Logformat-Datenzeilen), Wellen-/Block-Eigentumsdisziplin beim Verteilen an Worker | Basis-Suite verifiziert 1424 passed/4 skipped RC=0; beide Rebranding-Stale-Tests gepinnt (Commit `1005aff`); Worker-Dispatch mit Block-Eigentum gegen Dateikollisionen |
| `hermes-agent` | Engine-Integration-Verträge und Overlay-Mechanics beim RF-Worker-Kontext bedient (Anker, Idempotenz, `--check`) | Overlays `patch_rf02_request_deadline.py`, `patch_rf03_monotonic_stream_clock.py` + Proben; Pins `tests/test_rf02_*.py` / `test_rf03_*.py` grün |

## In früheren Runden produktiv belegt (historisch – Snapshot, gilt nicht neu auditfrei)

- `computer-use` / `agent-reach`: Laufzeitbelege im Projektarchiv (ui-artifacts/audit),
  aktuelle Runde re-erhebt den Beleg nicht automatisch — S17 bleibt `open` bis frisch gefahren.
- `systematic-debugging` / `pr-review` als Vorgehens-Hintergrund der Supervisor-Runden.

## Noch offen (nicht behauptet)

- computer-use gegen die echte gebaute App (Kapitel-05 §4-Test, S17/PX-04):
  jeder Schritt braucht die lokale Instanz am Gerät.
- agent-reach im Endstand (R07: Exa-CLI-Pfad war nicht verfügbar; Browserpfad dokumentiert).
