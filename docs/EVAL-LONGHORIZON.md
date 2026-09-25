# Long-Horizon Evaluation — Skaldir Agent (DoD §18)

Harness: `tools/gv_eval_longhorizon.py` — läuft gegen die **echte** Maschinerie:
den laufenden GV-Serve (Completion-API + strenger Verifier über ein Scratch-Repo
mit ausgeführtem pytest), den echten Pause-/Research-Store und das registrierte
Testing-Tool (`check_readme`) über den echten Gate-Pfad (`register_testing_tools`
→ `execute_check`). Szenario: Ziel → kaputte Trajektorie → vorzeitige
Fertig-Meldungen → Voice-Kette (Pause → Research → Redirect, inkl. P1-5
Novel-Eskalation) → echter Fix → Testing-Tool prüft Produktzustand → Re-Verify.

## Gemessene Ergebnisse (13.09.2026) — *actual results only*

| Metrik | `whispered` (Voice an) | `shadow` (nur Beobachtung) |
|---|---|---|
| Premature-Completion-Claims abgelehnt | **2 / 2** (echte Runner-Evidenz: `tests: test run failed: python -m pytest -q`) | **2 / 2** (Gate modusunabhängig) |
| Voice-Interventionen (Pause→Research→Redirect) | **3** | 0 |
| Novel-Strategy-Eskalationen (`REPEATED INTERVENTION`, durable gezählt) | **2** (ab #2 der Klasse) | 0 |
| Testing-Tool über Gate-Pfad | **PASS** (`check_readme`, `python check_readme.py /tmp/gv_lh_repo`) | PASS |
| Finales Verdict | **VERIFIED_COMPLETE**, Evidenz `pytest: ok`, 0 Blocker | VERIFIED_COMPLETE |
| End-to-End-Latenz des Skripts | ~1–16 s (deterministisch, ohne LLM) | ~1 s |

## Was der Lauf beweist

1. **Premature Completion wird mit ausgeführter Evidenz abgelehnt** — nicht mit
   Behauptungen: der Verifier ließ den echten pytest laufen und legte
   `test run failed` als Blocker vor.
2. **Die Voice-Kette feuert real**: drei Pausen mit Research (`status: delivered`),
   die dritte mit erzwungener Novel-Strategie — Schleifen werden nicht blind
   wiederholt.
3. **Testing-Tools zertifizieren Produktzustand** über denselben Pfad, den das
   Completion-Gate nutzt (BROKEN→FAIL / WORKING→PASS separat belegt).
4. **Der Modus trennt sauber**: shadow beobachtet (0 Interventionen), das Gate
   bleibt identisch streng — Voice steuert die Trajektorie, nicht die Wahrheit.
5. **Erst nach echtem Fix + Verifikation** steht `VERIFIED_COMPLETE` (0 Blocker).

## Nebenfund (gefixt, `plugin_api.py`)

Die mutierenden API-Routen (`/completion/goal|verify|criteria`, Transition)
gaben die **unscoped** Panel-Payload zurück — Aufrufer sahen den Zustand einer
fremden/letzten Session. Jetzt scopen alle vier auf die übergebene `session_id`
(der Eval-Lauf las vorher fremde Contract-Daten; nach dem Fix stimmen Response
und gespeicherter Contract überein). Regression: Plugin-Suite 31/31 grün.

## Reproduzieren

```bash
.venv/bin/python tools/gv_eval_longhorizon.py            # whispered
.venv/bin/python tools/gv_eval_longhorizon.py --shadow   # Vergleich
```
