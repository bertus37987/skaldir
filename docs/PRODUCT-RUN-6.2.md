# §6.2 Produktlauf — erster vollständig belegter Durchlauf

**Datum:** 12.09.2026 · **Instanz:** dedizierte Skaldir Agent-App (eigener userData,
Serve-Marker `HERMES_DESKTOP_USER_DATA_DIR=…/godsv/…`, Session-Token aus der
Prozess-Umgebung) · **Treiber:** `tools/gv_product_run.py` (spricht nur die
Panel-API der GV-Instanz an — nie den normalen Hermes-Serve) ·
**Ziel-Repo:** `/tmp/gv_prodrun_repo` (Scratch-Repo mit einem absichtlich
fehlschlagenden `pytest`).

## Ablauf (Session `prodrun-3`)

| # | Schritt | Beobachtetes Ergebnis (aus Store/API gelesen) |
|---|---|---|
| 1 | Ziel beginnen (`POST /completion/goal`) | `WORKING`, DoD `c1` „the pytest suite is green" (blocking, `verification: pytest`) |
| 2 | Abschluss behaupten (`POST /completion/verify`, Suite rot) | **0.3 s** → `INCOMPLETE`, Evidence `pytest: failed (python -m pytest -q)`, Blocker: `evidence: only failing checks were provided as evidence` · `tests: test run failed: python -m pytest -q` · `dod: definition-of-done not verified: the pytest suite is green` |
| 3 | Korrektur (Test gefixt, committet) | — |
| 4 | Erneut `POST /completion/verify` | **0.4 s** → `VERIFIED_COMPLETE`, Evidence `pytest: ok (python -m pytest -q)` |
| 5 | App-Neustart (nur GV-Instanz) | `prodrun-3` → `VERIFIED_COMPLETE` + Evidence **erhalten** |
| 6 | Zweite Session `prodrun-2` | `WORKING`, unabhängiges Ziel — **keine** Zustandsvermischung |

**Laufzeit-Beleg:** 0.3–0.4 s pro Verify — die Ausführung lief nachweislich im
Scratch-Repo (die volle godsv-Suite braucht ~60 s). Der Verdikt-Pfad nutzt die
echte Runner-Evidenz (`RunEvidence`, `source: runner`), keine Tool-Events.

## Ehrlich verworfen: die Vorgänger-Runde `prodrun-1`

Der erste Lauf überschnitt sich mit der Token-Umstellung (doppelte
Fix-Commits, verwürfelte Report-Felder durch überlagerte Aufrufe). Er ist
**nicht** Teil der Belege und wurde durch `prodrun-3` ersetzt. Lehre: der
Harness läuft nur gegen die GV-Instanz und wird nicht parallel aufgerufen.

## Grenzen

- Der Lauf belegt die Supervisor-Mechanik (Vertrag → evidenzbasierter
  Fehlschlag → Korrektur → unabhängige Abschlussprüfung → Persistenz →
  Isolation) über die echte Panel-API der laufenden App.
- Der Chat-Pfad (Voice-Pause → Redirect → Resume im Verlauf) ist separat
  live belegt.
- Die Blocker-Konvention (`BLOCKED_USER:`-Statuszeilen) wird dem Main Agent
  jetzt **situativ im Redirect** beigebracht: `VerificationResult.redirect_md()`
  trägt den Hinweis genau dann, wenn er weiterarbeiten muss („end with a status
  line `BLOCKED_USER: <reason>`; prose alone does not stop the loop"),
  abgesichert durch `test_redirect_md_teaches_the_blocked_user_convention`.
  Der Systemprompt bleibt bewusst unverändert — die Konvention kommt dann,
  wenn sie gebraucht wird.
