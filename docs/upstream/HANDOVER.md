# HANDOVER — Skaldir Agent, Stand nach dem verbindlichen Vertrag `ABNAHME.md`

**Zweck:** Übergabe an den nächsten Agenten. Diese Datei beschreibt ausschließlich den
*belegten* Zustand: was in der Sitzung vom 16.09.2026 nach Eintreffen von
`/home/willy/Documents/Design_Docs/ABNAHME.md` getan wurde, mit welchem Befehl es belegt ist,
was noch läuft, was extern blockiert ist und welcher Schritt als nächstes dran ist.
Sie ersetzt keine der bestehenden Dateien, sie verlinkt sie.

**Oberste Regel aus ABNAHME.md §0 gilt für dich genauso:** nichts gilt als bestanden, weil ein
Worker „fertig“ meldet, ein Unit-Test grün ist oder ein Container startete. Jede hier
behauptete Zahl ist mit dem genannten Befehl reproduzierbar — tue das, bevor du darauf baust.
`blocked`, `not_run` und `unknown` sind ehrliche Zustände und dürfen nie zu `PASS` werden (§4.3).

---

## 1. Repository, Stände, Umgebung

| Fakt | Wert | Nachweis |
|---|---|---|
| Produkt-Repo | `~/projects/gods-voice` | `git -C ~/projects/gods-voice rev-parse --show-toplevel` |
| Branch | `godsv-supervisor-hardening` | `git rev-parse --abbrev-ref HEAD` |
| HEAD dieser Übergabe | `afd4e12` (Arbeitsbaum sauber) | `git log --oneline -1`, `git status --short` |
| Engine-Baum | `~/.hermes/hermes-agent` @ `fb56a7e06d`, **pristine** | `grep -c "gv-host-overlay" ~/.hermes/hermes-agent/agent/turn_finalizer.py` → `0` |
| Python | `.venv` im Repo (uv), Python 3.12 | `.venv/bin/python -V` |
| Engine-venv | `~/.hermes/hermes-agent/venv/bin/python` — **enthält kein pytest** | `which pytest` im Engine-venv schlägt fehl |
| Docker | installiert, Daemon läuft: **29.6.1**, `overlayfs` | `docker info`, `docker run --rm hello-world` |
| Kriterienregister | **97 Kriterien, 33 gemappt, 64 unknown** | `python3 tools/build_audit_register.py` |

**Kritische Einordnung:** Die Engine-Overlays (RF-02…RF-10) sind **Artefakte, nicht angewandt**.
Der Live-Baum ist unverändert — `grep -c` = 0 ist der Beweis. Ein Live-Eingriff braucht ein
eigenes autorisiertes Fenster und deine Zustimmung.

---

## 2. Was in dieser Sitzung seit `ABNAHME.md` passiert ist

### 2.1 RF-09 (Engine-Crash-Fenster) — fertig, **selbst** verifiziert

Der RF-09-Worker hatte geliefert; statt dem Bericht zu glauben, habe ich gegen den Baum geprüft:

```bash
# Rot-Beweis auf unverändertem Baum
/tmp/gv_pristine/venv/bin/python tools/host_rf09_crash_probe.py /tmp/gv_pristine
# → PRISTINE_RC=1
#   "PRISTINE RF-09: no _rf09_mark_pending_external / _rf09_claim_resume on the engine —
#    crash windows have no fence (side effects may silently re-send, two owners possible)"

# Grün auf dem komponierten Baum (trägt rf04–rf08+10)
python3 host-overlay/patch_rf09_crash_windows.py /tmp/gv_secfix_int
eng=/tmp/gv_secfix_int/venv/bin/python
for p in host_rf09_crash_probe host_rf04_retry_budget_probe host_rf05_fallback_probe \
         host_rf07_interim_report_probe host_rf08_segment_probe host_rf10_stop_probe; do
  timeout 300 $eng tools/$p.py /tmp/gv_secfix_int; echo "$p RC=$?"; done
# → alle RC=0

# Engine-Regressionen (Start OHNE Modul-Argumente gibt RC=2 = Usage, kein Fehler!)
timeout 420 $eng tools/host_engine_regression_probe.py /tmp/gv_secfix_int \
  tests/agent/test_stream_wait_notice.py tests/agent/test_nonstream_wait_notice.py
# → engine regression probe: 13 passed, 0 failed

# Pins
.venv/bin/python -m pytest tests/test_rf09_crash_windows.py -p no:randomly
# → 8 passed
```

Commit `f772fb0`. Artefakte: `host-overlay/patch_rf09_crash_windows.py`,
`tools/host_rf09_crash_probe.py`, `tests/test_rf09_crash_windows.py`,
`docs/audit/RF09-CRASH-WINDOW-INVENTORY.md` (Fensterinventur W1–W4: W1 „externe Wirkung ohne
Ergebniszeile“ und W2 „Cursor geschrieben, Run nicht fortgesetzt“ waren ungesichert → gefenced;
W3 RF-04/05-Fallback-Swap und W4 RF-06-Kompression bewertet als sicher, dokumentiert statt
umgebaut).

**Engine-Recovery ist damit 10/12 implementiert.** Offen: RF-01, RF-11, RF-12 — kein
Implementierungsdefekt, sondern echter Langlauf (siehe §5).

### 2.2 ABNAHME.md als Vertrag eingearbeitet — Kriterienmatrix 77 → 97

`ABNAHME.md` Teil XV führt `JEV-01…JEV-20` ein. §2 verlangt ausdrücklich, dass diese in die
*reale* `REQUIREMENTS.json` übernommen werden. Getan, wörtlich aus dem Dokument geparst:

* Eingefrorene Inventur: `/home/willy/Documents/Design_Docs/Skaldir_Release_Paket/REQUIREMENTS.json`
  → `requirements` von 77 auf 97 Einträge, `criteria_count: 97`, plus Block `jev_extension`.
  Backup der Vorfassung: **`REQUIREMENTS.json.pre-jev.bak`** (im selben Verzeichnis).
  Quelle je Kriterium: `{"file": "ABNAHME.md", "line": <Überschriftenzeile>}`, also 1560…1667.
* Register neu gebaut: `python3 tools/build_audit_register.py` → `97 criteria · 33 mapped · 64 still unknown`;
  `--check` → `audit register current — 97 criteria`.
* `tools/build_audit_register.py`: `EVIDENCE_STATES` um **`integration_tested`** erweitert
  (ABNAHME §4.2 führt die Stufe zwischen `mock_tested` und `live_tested` ein; ohne die
  Erweiterung hätte `check_states()` eine ehrliche Klassifizierung als Schemafehler gemeldet).
* Commits: `b7a287a` (Register + Extension), `ce7b1ca` (Wellen-Log).

### 2.3 RF-09 gemappt und die Evidenzstufe ehrlich gesetzt

`audit/verification-mapping.json` → `RF-09: implemented / integration_tested / open`, mit
Begründungstext und `next_action` (Live-Crash-E2E gegen echten Provider bleibt `not_run`;
`execute_tool_calls_concurrent` hat die W1-Form und ist noch nicht gefenced). Deshalb
`integration_tested` und **nicht** `live_tested`.

### 2.4 UX-Prüfwelle (zweiter unabhängiger Reviewer, §110) — bewertet und gemappt

Die Welle hatte `outputs/review16/ux/UX_REVIEW.md` geliefert (Commit `e3f6215`). Verdikte sind
in das Register übernommen (`65854d8`, 8 Kriterien): S02, S10, S11, S14, V2-05, V2-08, V2-12,
VD-22. Kernbefunde:

* **P1** `composer.microActions` war strukturell leer (Core registriert nichts) → **von mir
  gefixt** in Commit `0ca9980`: der godsv-Plugin-Beitrag liefert die drei nie-toten
  Kontextaktionen („Plan öffnen“, „Werkzeug suchen“, „Docker-Image hinzufügen“), die zwei
  vorgangsgebundenen Aktionen degradieren ehrlich ohne Session. Smoke: Beitragszähler von
  `nine` auf `ten`, zwei neue Prüfungen, `desktop panel: contract OK`.
  * Nachtrag (Wave 9): „Docker-Image hinzufügen“ ist **entfernt** — der Badge versprach einen
    Image-Flow, den weder die Tools-View noch das Produkt hat (kein docker-Subcommand, keine
    Panel-Route; `docker_runtime.py` klassifiziert nur Docker-Fehler). Der immer-gültige Badge ist
    jetzt „Werkzeug suchen“ (die Tools-View braucht kein Goal), „Plan öffnen“ bleibt
    vorgangsgebunden. Smoke pinnt beides (2 Badges mit Session, 1 ohne, kein docker-Label).
* **P2** ausgeliefertes Plugin hinkt dem Repo-Kandidaten hinterher (Cockpit/Activity noch in der
  NAV der Deployment-Kopie) — Fix-Hinweis: Plugin neu deployen, **nicht** im Repo „reparieren“.
* **P2** Computer-Use-Weg aus dem Chat ist nicht erreichbar und es gibt auch keine ehrliche
  Lücke-Meldung (S10 = `missing`). Kleinster Fix: `cu_payload` → `capability_gap`-Event.
* **P2** Scrollposition nach Sessionwechsel wird nicht wiederhergestellt (Chat-Core).
* **P3** Middleware liest die Session-ID erst beim Absenden (Fenster für Fehlzuordnung),
  **P3** CLI ohne `--json`/`NO_COLOR`-Schalter, `eval` vs. `evals`.
* Nicht geprüft und so dokumentiert: Live-CDP-Verhalten, S12, S14-Kataloginhalt.

### 2.5 B6 (Docker-Tool-Builder) — verifiziert und integriert

Commit `0547a5c`; eigene Nachmessung: `tests/test_docker_runtime.py` + `tests/test_rf08_segment.py`
→ **30 passed, 3 skipped**; `ruff` clean; `bandit -q -r src -ll` → 0 Findings;
vollständige Suite danach **RC=0, 1507 Punkte**. Echte Live-Belege:
`alpine:3.20` pull/pin/start/exec/verify/stop, Fehlerklassen `image_missing`/`arch_mismatch`/
`port_conflict` je mit strukturierter Nutzlast, `pandoc/minimal:3.1.13` md→html, SearXNG mit
`/search?format=json` → 34 Treffer, Publish → zweiter Kontext ohne Restart. Dokument:
`docs/audit/DOCKER-BUILDER-EVIDENCE.md`.

### 2.6 RF-08 (Segmentfortsetzung) — verifiziert

Commit `6fb7501`; `tests/test_rf08_segment.py` → 8 passed; Overlay `patch_rf08_segment_continuation.py`
schreibt einen idempotenten, restart-sicheren Segment-Cursor (Gates A–D). Registermapping mit
S06, VD-13, PX-06.

### 2.7 Was ich in dieser Welle **nicht** getan habe

* Kein Live-Eingriff in `~/.hermes/hermes-agent` (bewusst).
* Keine erfundenen Messwerte: wo kein Lauf möglich war, steht `not_run`/`blocked` mit Grund.

### 2.8 JEV-Foundation (System One) — verifiziert, **gemappt**, committet

Worker-Welle `deleg_d23d9506` (2 Tasks) hat geliefert, ich habe **selbst nachgemessen** statt zu glauben:

```bash
# Der eigene Vertrag des Workers
.venv/bin/python -m pytest tests/test_system_one_contract.py tests/test_system_one_adapters.py \
  tests/test_system_one_flags.py tests/test_system_one_policy.py tests/test_system_one_questions.py \
  tests/test_system_one_sanitizer.py -p no:randomly      # → 117 passed

# Meine unabhängige Gegenprüfung (eigene Aussagen, ohne die Autoren-Tests zu lesen)
.venv/bin/python tools/jev_contract_probe.py             # → 16/16 green, RC=0
```

Nachgeprüft von außen: Noul ohne `confidence` (Zusatzfeld wird abgelehnt), Choice-Verteilung
muss 1 ergeben, Registry verweigert unbekannte/doppelte Packs statt zu erfinden, Sanitizer lässt
`api_key`/Token/`authorization` nicht durch (erlaubter Inhalt schon), `live_jev=False` in jeder
Implementierung, ohne Credential `SystemOneBlockedExternal` mit `blocked_external_access` und
**ohne** `answers`, Staleness-Bindung (ältere Goal-Revision inaktiv, gleiche nicht), CU-Gate
advisory mit unveränderter `allowed_kinds`, alle sechs Flags per Default aus.

Register danach: **97 Kriterien · 48 gemappt** (33 + 11 JEV). Neue Evidenzstufe `contract_tested`
(ABNAHME §5.1) im Register-Tool. JEV-16 ist `partial` (nur das advisory Gate, keine reaktiven
Pre-/Postcondition-Fragen), JEV-07 `blocked_external_access` — heute ist §117.1 so `not_started`,
als Stufe höchstens `JEV_READY`. **JEV-08/10/11/12/13/15/17/18/19 bleiben `unknown`** — nicht
implementiert, nicht bewertet; das steht so auch im `note`-Feld der Mapping-Datei.

### 2.9 §109–§119-Artefaktsatz — verifiziert und committet

`audit/findings.json` (36 Findings, §113-Felder), `docs/abnahme-status.md`, PERFORMANCE,
ARTIFACT-MANIFEST (106 Dateien, echte sha256), COMPUTER_USE_SUBTOOLS (sagt offen: **kein**
wiederverwendbares visuelles Subtool), SYSTEM_ONE_QUESTIONS, PLATFORM-MATRIX, TEST_REPORT,
`tools/eval_benchmark.py`. Geprüft: **101 von 106 Hashes selbst nachgerechnet**, die 5 Abweichungen
waren genau die Dateien, die ich *nach* der Manifest-Erzeugung gefixt hatte → Manifest neu erzeugt,
`--check` grün. `eval_benchmark` ohne Daten → **RC=2**, „no runs, nichts wird als Null gedruckt".
`check_findings_criteria` → RC=0 (36 Findings, 15 Kriterien-IDs, alle in den 97 vorhanden).

### 2.10 Zwei Werkzeug-Defekte, die still falsche Evidenz erzeugt hätten

1. **Eine per `cp -a` kopierte Engine liest den Live-Baum.** Das Venv ist eine PEP-660-Editable-
   Installation; die Finder-Datei trägt absolute Pfade. Folge: Probes auf der Kopie fragen
   `~/.hermes/hermes-agent` und melden „PRISTINE", obwohl die Kopie gepatcht ist. `tools/rf_stage1_compose.sh`
   schreibt jetzt **alle** Mappings um und hat ein Auflösungs-Gate, das ohne korrekte Auflösung gar
   nicht erst probt. Dasselbe Muster habe ich in `tests/test_rf09_crash_windows.py` repariert (vorher
   nur `agent`/`tools` umgebogen — teil-geheilt ist nicht geheilt).
2. **`--check`-Gates wurden durch den eigenen HEAD-Stempel dauerhaft rot.** Beide Generatoren
   stempeln `observed_project_head`; jeder spätere Commit macht sie „stale" → ein Gate, das immer
   rot ist, wird ignoriert. Jetzt wird der Stempel beim Vergleich ignoriert (Informationsfeld,
   kein Stale-Kriterium), Inhalt muss matchen. Beide Gates grün.

---

## 3. Befehle, die du zuerst laufen lässt (5-Minuten-Wahrheitscheck)

```bash
cd ~/projects/gods-voice

# 1. Register == Eingaben?
python3 tools/build_audit_register.py --check        # → "audit register current — 97 criteria"

# 2. UI-Kontrakt (schnell, hermetisch)
node tools/check_desktop_plugin.mjs                  # → "desktop panel: contract OK"

# 3. Suite (hermetisch, ~2–4 min; addopts setzt bereits -q, also kein zweites -q)
.venv/bin/python -m pytest -p no:randomly > /tmp/suite.log 2>&1; echo "RC=$?"

# 4. Lint/Security
source .venv/bin/activate
ruff check src tests tools host-overlay
bandit -q -r src -ll

# 5. Engine-Overlays sind Artefakte, nicht angewandt
grep -c "gv-host-overlay" ~/.hermes/hermes-agent/agent/turn_finalizer.py   # → 0
```

### Fallen, die dich Zeit kosten werden (aus dieser Sitzung)

* **Kopierte Engine liest stillschweigend den Live-Baum.** `~/.hermes/hermes-agent/venv` ist eine
  PEP-660-Editable-Installation; die Finder-Datei
  `venv/lib/python3.*/site-packages/__editable___hermes_agent_*_finder.py` trägt **absolute
  Pfade** in `MAPPING`. Nach `cp -a` zeigt jeder Eintrag weiter auf den Live-Baum — ein Probe auf
  der Kopie kann also die falsche Quelle lesen und trotzdem selbstbewusst PASS/FAIL melden.
  `tools/rf_stage1_compose.sh` schreibt alle Pfade auf die Kopie um **und** hat ein
  Auflösungs-Gate (`inspect.getfile(...)` muss unter der Kopie liegen), das die Probe-Phase
  verweigert, wenn das nicht gilt. Wenn du Kopien anders baust: Gate selbst nachbauen.
  Beleg: `…/python -c "import agent.turn_finalizer as tf, inspect; print(inspect.getfile(tf))"`.
* **Engine-Kopien gehören nach `$HOME`, nicht nach `/tmp`.** `/tmp` ist ein 16-G-Tmpfs mit
  ~1 M Inodes; eine Engine-Kopie braucht ~150 k davon. `cp -a` bricht dann mitten im Kopieren mit
  `Disk quota exceeded` ab und hinterlässt einen halben Baum, der auf den Live-Baum auflöst.
  Siehe `~/gv-trees/`.
* **Zählung nicht per `tail`**: die Suite-Ausgabe enthält Carriage-Returns; Punktzahl per Python
  zählen, nicht mit `tr`/`tail`.
* **`RC=2` der Engine-Regressionsprobe** heißt „usage“, nicht „kaputt“ — sie braucht die
  Testmodulpfade als Argumente (`tests/agent/test_stream_wait_notice.py tests/agent/test_nonstream_wait_notice.py`).
* **Kein pytest im Engine-venv** — deshalb die `tools/host_*_probe.py`-Skripte.
* **`tools/rf_measure.py`** bricht(e) bei relativem `--out` am Ende mit `ValueError` ab
  (`relative_to(REPO)`); gefixt über `_display()`. Relative `--out`-Pfade sind jetzt legal.
* **Overlay-Disziplin**: `MARKER` + `.gv-backup` + `--check` + idempotent + **lauter Abbruch bei
  Anker-Drift** (`anchor missing/ambiguous … Nothing written`, RC=1). Nie stiller No-op, nie
  verbreiterte Anker. Komposition der Overlays in beiden Reihenfolgen testen.
* **Overlay-Reihenfolge**: `tools/rf_stage1_compose.sh` wendet rf02→rf10 an. Bei eigenen
  Kompositionen daran denken, dass rf08 und rf09 dieselbe Datei (`agent/turn_finalizer.py`)
  anfassen — Anker bleiben eindeutig, aber die Reihenfolge muss wie im Skript sein und danach
  alle Probes laufen.
* **pytest-Aufruf**: `.venv/bin/python -m pytest tests/test_<area>.py -p no:randomly`
  (kein zusätzliches `-q`).
* **Ehrliche Klassifizierung**: Evidenz `integration_tested` nur, wenn mehrere echte
  Komponenten zusammenspielen, aber die volle Außenwirkung fehlt; sonst `mock_tested`.

---

## 4. Die abgeschlossene Worker-Welle `deleg_d23d9506` — Ergebnis

Beide Tasks sind **fertig, von mir nachgeprüft und committet** (`411a3f8`, `afd4e12`). Kein
Arbeitsbaum-Rest, `git status --short` ist leer.

| Task | Ergebnis | Transcript |
|---|---|---|
| task-0 | JEV-Foundation: `src/godsv/system_one*.py` + `SYSTEM_ONE.md`, additive Edits in `voice.py`/`computer_use.py`/`events.py`/`llm.py`, 6 Testmodule mit 117 Pins (rot-first). | `~/.hermes/cache/delegation/live/deleg_d23d9506/task-0.log` |
| task-1 | §109–§119-Artefaktsatz unter `audit/`, `docs/`, `tools/` (siehe §2.9). | `~/.hermes/cache/delegation/live/deleg_d23d9506/task-1.log` |

Beide hatten **kein Commit-Recht** — committet hat sie der Integrator, nach eigener Messung.
Das Prüfprotokoll für künftige Wellen:

1. `git status --short` gegen den zugewiesenen Pfad lesen; alles außerhalb verwerfen, nicht retten.
2. Jeden rot-first-Beleg aus dem Testdocstring selbst nachfahren (Befehl + beobachteter Fehler).
3. `.venv/bin/python -m pytest -p no:randomly` (RC + Punktzahl), `ruff check src tests tools`,
   `bandit -q -r src -ll` selbst laufen lassen.
4. Kernzusagen **von außen** nachrechnen: `tools/jev_contract_probe.py` (16/16), bei Engine-Overlays
   zusätzlich Rot- auf unverändertem Baum und Grün auf dem komponierten Baum.
5. Erst dann `audit/verification-mapping.json` fortschreiben, Register + Manifest neu erzeugen,
   beides `--check` grün, dann committen.

Was **nicht** durch eine Worker-Welle zu holen ist und weiter auf den Auftraggeber wartet: der
echte Langlauf (RF-01/11/12, siehe `docs/RF-LONGRUN-FREIGABE.md`) und Windows/macOS-Builds.

---

### 2.11 JEV-Welle 2 (JEV-10/12/13/15) — verifiziert, gemappt, committet (`4d68666`)

Shadow Mode, Voice-Triage-Pack, Completion-Precheck und GUIState-Vertrag sind implementiert und von
mir gegengeprüft. **Eigene Läufe, nicht Worker-Behauptungen:**

```bash
.venv/bin/python -m pytest tests/test_system_one_shadow.py tests/test_system_one_triage_pack.py \
  tests/test_completion_precheck.py tests/test_system_one_guistate.py -p no:randomly   # → 89 passed
.venv/bin/python -m godsv.system_one_shadow     # → cases=16, counter_examples=7, disagreements=15,
                                                #   records=16 executed_routes=0 mutations=0
                                                #   mismatches=0 outcome_mismatches=0 fully_covered=False
                                                #   + 4 COVERAGE GAPS
.venv/bin/python tools/jev_contract_probe.py    # → 31/31 green (jetzt auch JEV-10/12/13/15)
```

**Ein echter Defekt, den erst die Integration zeigte:** `system_one_policy.completion_precheck` (Welle 1,
committed) und `completion.completion_precheck` (Welle 2) waren **zwei öffentliche Funktionen gleichen
Namens mit unterschiedlicher `None`-Semantik** — ein Aufrufer konnte die falsche erwischen, ohne es zu
merken. Die Policy-Variante heißt jetzt `evaluation_precheck_signals` (Verhalten unverändert, die
Pins in `tests/test_system_one_policy.py` mitgezogen und grün), sodass der binding-bewusste Precheck
den Namen besitzt. Der §91-Einstieg ist damit eindeutig.

**Offen geblieben, ehrlich markiert:** vier echte Trigger-Arten (`hallucinated_api`,
`pattern_dependency_churn`, `dependency_change`, `outage_misdiagnosis`) komponieren `loop/completion/
recovery`, brauchen aber die Research-Dimension — Fix-Ort ist `TRIGGER_GROUPS` in
`src/godsv/system_one_questions.py`, außerhalb des Worker-Schreibbereichs. Ebenso: kein
Produktionsaufrufer shadowt einen echten Lauf (der laufende Supervisor ist die Live-Engine), keine
echte Desktop-Session (`godsvoice cu check`: Container-GUI-Image fehlt), kein Jev-Livezugang.

## 5. Blockiert — mit nächster Aktion, damit nichts davon still verschwindet

| Punkt | Kriterien | Status | Genau nächste Aktion |
|---|---|---|---|
| Echter 152k-/80-Iterations-Langlauf | RF-01, RF-11, RF-12, S15, S19 | `not_run` | **Willy muss den Lauf freigeben** (mehrere Stunden, Modellkosten). Danach: Baseline und Fix nach RF-01 reproduzieren, Vorher-/Nachher-Bericht nach RF-12 mit TTFT, Dauer, Retry-Wartezeit, Token/Kosten, Kompressionsverlust. |
| Windows-/macOS-Builds | PX-01, PX-02, PX-06, PX-08 | `external_dependency` | Build-Hosts/Runner oder CI-Matrix; Cross-Build zählt nicht als nativer Beweis (§78). |
| Signierung/Notarisierung | PX-07 | `external_dependency` | Codesign-/Developer-ID-Identität; fehlende Identität = offene Releasefreigabe, kein Fake-PASS (§77). |
| Echte Computer-Use-Geräteabnahme | S10, S02, V2-07, PX-04 | `not_run` | Lokale GUI-Session mit echter Host-App; headless wurden diese Wege bewusst als `not_run` markiert, statt sie zu simulieren. |
| TypeSafe/Jev-Livezugang | JEV-07, JEV-08 | `blocked_external_access` | Early Access, kein autorisierter Key. Ohne Zugang bleibt die Aussage `JEV_READY` — niemals `JEV_LIVE_TESTED`. |
| Redactor-Restlücken | SEC-12b, SEC-20 | P2 offen | `gsk_live`-, `vnc://user:***@`-, zeilenumbrochene und base64-Muster entkommen `_rf06/_rf07_redact`; als Grenze dokumentiert, nicht geschlossen. |

---

## 6. Nächster konkreter Schritt nach der laufenden Welle

1. Worker-Ergebnisse nach §4 prüfen, committen, JEV-Kriterien ins Register mappen.
2. **JEV-Welle 2** auf den dann stehenden Contracts: JEV-10 Shadow Mode (darf Goal/Voice/
   Completion/Computer Use nachweislich **nicht** mutieren), JEV-12 Voice-Triage-Pack mit
   produktiven Gegenbeispielen, JEV-13 Completion-Precheck (signalisiert fehlende Evidenz,
   zertifiziert aber nie allein), JEV-11 Kalibrierung/Holdout, JEV-15/16/17 GUIState +
   Reactive Checks + Subtools, JEV-19 Benchmark-Arme A–D.
3. `audit/RESUME.md` und die §117-Gate-Tabelle auf den neuen Stand ziehen.
4. Offene UX-P2 aus §2.4 abarbeiten (Plugin-Redeploy, CU-Lücke-Meldung, Scroll-Restore) —
   Reihenfolge nach Wirkung, nicht nach Aufwand.

**Nicht anfangen ohne ausdrücklichen Auftrag:** Live-Eingriff in die Engine, Löschen oder
Abschwächen von Kriterien, Formulierungen wie „alles funktioniert“/„production ready“
(§119 verbietet sie ohne Beleg) oder ein `JEV_ACTIVE_VERIFIED` ohne echten Jev-Lauf.

---

## 7. Artefakt-Landkarte (wo was steht)

* Vertrag: `/home/willy/Documents/Design_Docs/ABNAHME.md` (97 Kriterien, Severity, Gate §117).
* Design-Paket: `/home/willy/Documents/Design_Docs/Skaldir_Release_Paket/` (00–09, `REQUIREMENTS.json`
  mit JEV-Erweiterung, Backup `.pre-jev.bak`).
* Register: `audit/REQUIREMENTS.json`, `audit/verification-mapping.json`, `audit/CODE_MAP.md`,
  `audit/RESUME.md`; Werkzeug `tools/build_audit_register.py`.
* Wellen-Log: `docs/IMPLEMENTATION-PLAN-recovery-skaldir.md`.
* Engine-Recovery: `docs/RF-STATUS.md`, `docs/audit/RF09-CRASH-WINDOW-INVENTORY.md`,
  Overlays `host-overlay/patch_rf0*.py`, Probes `tools/host_rf*_probe.py`,
  Pins `tests/test_rf0*.py`.
* Prüfwellen: `outputs/review16/` (Security/Recovery), `outputs/review16/ux/` (UX/Produkt).
* Docker-Builder: `docs/audit/DOCKER-BUILDER-EVIDENCE.md`, `src/godsv/docker_runtime.py`,
  `tests/test_docker_runtime.py`.
* Abnahmepaket §114: `docs/audit/{TEST_REPORT,PERFORMANCE,PLATFORM-MATRIX,SKILLS_USED,UPDATE_COMPATIBILITY,RELEASE_NOTES,BENCHMARK_MANIFEST,COMPUTER_USE_SUBTOOLS}.md|json`
  (teilweise in Arbeit), `audit/findings.json` (in Arbeit), `docs/ABNAHME-STATUS.md` (in Arbeit).