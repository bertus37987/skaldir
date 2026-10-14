# Abnahme VAL-TOOL-002 — GUI-Subtool mit Vorbedingungen (live)

Stand: 2026-10-14, Basis-HEAD `d2dad75` (Implementierung) + `tools/gui_subtool_live_proof.py` (Beleg).
Status: **live-verifiziert** (echter Host-Desktop `:0`, eigenes Probe-Fenster).

## Was geprüft wurde
| Schritt | Ergebnis |
|---|---|
| Eigenes Fenster `SKALDIR-PROBE-subtool-…` (Bindung bestätigt: PID, WM_STATE, Fokus) | ok |
| `skaldir act subtool save` – parametrisiertes Subtool (Parameter `value`, Vorbedingung/Anker = Fenstertitel, Endzustand = App-State-Probe auf den Empfänger-Log) | rc 0 |
| `skaldir act subtool run … --runner live-desktop --params '{"value":"hello42"}'` | rc 0, `verified`; Empfänger loggte genau `hello42`; Desktop-Lease erworben und freigegeben (fencing gen 61) |
| Zustand geändert (Fenster zerstört) → Re-Run mit `{"value":"second"}` | rc 2, `reobserve`: „blind replay refused … nothing was executed" |
| `discover()` / tool_search | Treffer `surface=act_subtools`, `executable=true`, typisiertes `input_schema` (`value: string`, required) + Aufrufform |

Mining (Fehlversuche entfernt, Wiederholungen entfernt, Waits behalten, Parameter abgeleitet) ist durch
`tests/test_gui_subtool_lifecycle.py` (19 Tests, grün) abgedeckt; nicht separat live gemined.

## Reproduktion
```bash
# ydotoold (Mission-Service) muss laufen — siehe services.yaml
uv run python tools/gui_subtool_live_proof.py   # -> logs/gui-subtool-live/<ts>.json
```
Rohbeleg: `logs/gui-subtool-live/20261014-212812.json` (gitignored).

## Befunde
- Der `host-session`-Runner läuft auf einem eigenen verschachtelten Display (Openbox) und sieht
  Fenster auf `:0` korrekt **nicht** — für den echten Desktop ist `--runner live-desktop` nötig.
- Ohne laufenden Mission-`ydotoold` meldet die Eingabe ehrlich „no keyboard backend … delivered"
  (kein Falsch-Positiv). Nach Start des Dienstes Zustellung am Ziel beobachtet.
- Der Probe-Empfänger protokolliert Press+Release; Auswertungen müssen `press=true` filtern.
