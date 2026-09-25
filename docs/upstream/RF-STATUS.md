# RF — Hermes-Kontext-, Provider- und Langlauf-Recovery (Status)

Umsetzung von `09_HERMES_CONTEXT_RECOVERY_FIX.md` (prioritäres Add-on, RF-01–RF-12).
Dieses Dokument ist der geforderte Nachweis je RF-ID: Code-Mapping, Baseline/Fix,
Befehle, Rohbelege, Rollback, offene Grenzen. **Nur belegte Schritte stehen hier.**

**Regel dieses Dokuments:** „behoben" erst nach bestandenem Kettentest. Ein grüner
Unit-Test allein belegt keines der RF-Kriterien (Kapitel 06 §3).

## 0. Bestand (Wave 1, am HEAD erfasst)

| Größe | Wert |
|---|---|
| Engine-Checkout | `~/.hermes/hermes-agent` @ `fb56a7e06d` (Branch `main`) |
| Design-Paket gelesen bei | `1ab32b21` — der Checkout ist **neuer**, Zeilenanker aus Kapitel 09 gelten nicht blind |
| Projekt-Repo | `~/projects/gods-voice` @ `eb38c5e` (`godsv-supervisor-hardening`, v1.5.0) |
| Engine-Testzugang | `venv/bin/python` hat die Laufzeit-Deps, **kein pytest**; kanonischer Runner `scripts/run_tests.sh` |
| Overlay-Mechanik | `host-overlay/*.py` — versioniert, verankert, idempotent, `--check`, lauter Abbruch bei Anker-Drift |

Die Overlays sind **Artefakte**, nicht automatisch appliziert: `--check` meldet
„would patch", wenn sie noch nicht im Engine-Baum stehen. Der Live-Baum wurde in
dieser Runde **nicht** verändert.

## 1. RF-03 — Wall-Clock-Sprünge verfälschen keine Laufzeitlimits

**Befund (rot-first reproduziert).** Der Stale-Detektor des Stream-Monitors leitet
seine verstrichene Zeit aus dem letzten *echten* Chunk ab, stempelte und maß aber
mit `time.time()`. Ein NTP-Sprung/Suspend erschien damit als Stille: der gesunde
Stream wurde abgebrochen.

```
# Live-Engine, unverändert (Beweis des Defekts)
$ ~/.hermes/hermes-agent/venv/bin/python tools/host_rf_monotonic_probe.py ~/.hermes/hermes-agent
FAIL A wall-clock jump of +3600s is inert (timeout 10.0s) — kills=[3600.3]
PASS B a real 1h silence still fires the detector — kills=[3600.3]
```

**Fix (kleinste wirksame Änderung, keine neue Deadline/Config/Runtime).**

| Datei | Änderung |
|---|---|
| `agent/chat_completion_helpers.py` | `_touch_chunk_time()` stempelt **beide** Uhren: `t` (Wall-Clock, weil Diagnose/`first_chunk_at` und Mensch sie so lesen) und `mono` (die Uhr, mit der gemessen wird). Alle fünf Stempelstellen nutzen den Helper; die Initialisierung setzt `mono` mit. |
| `agent/chat_completion_stream_monitor.py` | `_chunk_clock()` liefert `(now, last_chunk_at)` auf **einer** Uhr: mit `mono`-Stempel monotone Messung, bei reinem Wall-Clock-Stempel das bisherige Verhalten — die beiden Uhren werden nie in einer Subtraktion gemischt. `_poll_local_load_notice()` nimmt den Stempel als Argument; die Schleife liest pro Verwendung frisch, genau wie zuvor. |

Bewusst **nicht** geändert: persistierte/reportierte Zeitpunkte (`t`,
`first_chunk_at`), `_heartbeat()`-Semantik, `_resolve_stale_timeout()`-Werte,
`handle_max_iterations`. Kein zweiter Fortsetzungs- oder Deadline-Pfad.

**Nachher (patched copy, Engine-Interpreter, echte Abhängigkeiten).**

```
$ python3 host-overlay/patch_rf03_monotonic_stream_clock.py <copy>
✓ patched … (backup: *.py.gv-backup)   # zweiter Lauf: „already patched"
$ ~/.hermes/hermes-agent/venv/bin/python tools/host_rf_monotonic_probe.py <copy>
PASS A wall-clock jump of +3600s is inert (timeout 10.0s) — kills=[]
PASS B a real 1h silence still fires the detector — kills=[3600.3]
```

**Regression (Kapitel 09 §2: vorhandene Tests zuerst).** Die Engine-eigenen
Wait-/Notice-Regressionen laufen vor **und** nach dem Patch identisch, mit dem
Engine-Interpreter und dem echten Code (pytest fehlt im Engine-venv, daher
`tools/host_engine_regression_probe.py`):

```
$ ~/.hermes/hermes-agent/venv/bin/python tools/host_engine_regression_probe.py \
      <tree> tests/agent/test_stream_wait_notice.py tests/agent/test_nonstream_wait_notice.py
engine regression probe: 13 passed, 0 failed     # identisch vor/nach dem Patch
```

**Repo-Pin (CI-fähig, hermetisch).** `tests/test_rf03_monotonic_stream_clock.py`
(5 Fälle): Anker passen zur installierten Engine · Overlay ist idempotent ·
Anker-Drift bricht laut ab (kein stiller No-op) · gepatchte Kopie misst monoton ·
**unveränderte** Kopie zeigt den Defekt weiterhin (der Probe ist nicht vakuum-grün).

**Rollback.** `mv agent/chat_completion_stream_monitor.py.gv-backup
agent/chat_completion_stream_monitor.py` (analog für die Helpers) — oder
`git -C ~/.hermes/hermes-agent checkout -- agent/`. Das Overlay schreibt vor jeder
Änderung ein `.py.gv-backup`.

**Offene Grenzen (RF-03 ist damit NICHT abgeschlossen).**
- RF-03 verlangt zusätzlich, dass ein *langsamer gesunder* Text-/Reasoning-Stream
  innerhalb seines gewählten Profils nicht fälschlich abgebrochen wird und die
  absolute Deadline wirksam bleibt. Dafür fehlen RF-01 (Baseline-Reproduktion) und
  die reale Stichprobe aus RFC §5 — **not_run**, kein Ersatz durch Unit-Tests.
- Das Overlay ist noch **nicht** auf den Live-Baum appliziert (bewusste,
  reversible Entscheidung; ein Eingriff in die laufende Engine gehört in ein
  autorisiertes Fenster).
- Die absoluten Profilwerte (60s/45s/180s) aus RFC §3B sind Pilotziele, nicht
  kalibriert; RF-02 implementiert sie noch nicht.


## 1b. RF-02 — requestweite Deadline statt verschachtelter Wartezeiten

**Befund (rot-first).** Es gab **keine** Obergrenze für die logische Anfrage: jede
Ebene brachte ihr eigenes Zeitbudget mit, und der Stale-Detektor wartete allein 180s
(bei 152k Kontext 300s, gemessen). Ein stummer Provider konnte diese Zeit je Versuch
erneut ausschöpfen.

```
# Pristine Engine, unverändert
$ <engine>/venv/bin/python tools/host_rf02_deadline_probe.py <pristine-copy>
FAIL A budget 3s / stale 300s → kill at the budget — no kill within 12s
PASS B budget disabled → unchanged (no kill in window)
PASS C budget 300s / stale 2s → kill at the stale threshold — fired after 2.10s (deadline_hit=False)
```

**Fix** (`host-overlay/patch_rf02_request_deadline.py`, kleinste wirksame Änderung):

| Ort | Änderung |
|---|---|
| `chat_completion_helpers._request_deadline_secs()` | Gesamtbudget einer logischen Anfrage: `HERMES_REQUEST_DEADLINE_SECS` (Pilotprofil 180s, RFC §3B); ein expliziter `agent.request_deadline_secs` gewinnt; `0` schaltet die Grenze bewusst ab |
| `_StreamingCall.__init__` | fixiert EINE monotone Deadline (`_request_deadline_mono`) beim Aufbau der Anfrage |
| `StreamingWaitMonitor._deadline_left()` | Restbudget; `None`, wenn keine Deadline gesetzt ist |
| Stale-Block der Monitor-Schleife | die Deadline ist eine **absolute** Grenze: `_stale_elapsed > stale_timeout` **oder** Restbudget ≤ 0 beendet den Versuch |
| `_kill_stale_stream(..., deadline_hit=)` | Log benennt getrennt, welche Grenze gegriffen hat (Provider-Stille vs. Gesamtbudget) |

Ein erster Entwurf kombinierte Restbudget und Stale-Schwelle per `min()` und tötete
dadurch nach der **halben** Budgetzeit (Probe: 1.50s statt 3.00s) — „seit letztem
Chunk verstrichen" und „im Budget verbleibend" sind verschiedene Größen. Der Probe hat
das gefunden; die Fassung oben ist die korrigierte.

**Nachher** (gepatchte Kopie, Engine-Interpreter):

```
PASS A budget 3s / stale 300s → kill at the budget — fired after 3.00s (overshoot +0.00s, deadline_hit=True)
PASS B budget disabled → unchanged (no kill in window)
PASS C budget 300s / stale 2s → kill at the stale threshold — fired after 2.10s (deadline_hit=False)
```

Damit ist die Cleanup-Toleranz **0.00s** im lokalen Pilot (Anforderung ≤5s), und die
Deadline grenzt ein, ohne die Stale-Schwelle zu lockern (Fall C).

**Komposition mit RF-03 (Reihenfolge-unabhängig).** Beide Overlays teilen keinen
Anker; auf der doppelt gepatchten Kopie sind **beide** Proben grün, und die
Engine-eigenen Wait-/Notice-Regressionen bleiben 13/13. `tests/test_rf02_request_deadline.py`
pinnt Anker, Idempotenz, lauten Abbruch bei Anker-Drift, die drei Proben-Fälle,
Nicht-Vakuum auf der pristine Kopie **und** die Komposition in beiden Reihenfolgen.

**Offene Grenzen (RF-02 nicht abgeschlossen).**
- Die Deadline wird im Stale-Detektor durchgesetzt; ob SDK-/Transport-Retries,
  Backoff und Fallback darüber hinaus eine eigene frische Frist erfinden, ist noch
  **nicht** gemessen (RFC §3B verlangt genau das). Dafür fehlt der Agent-Lauf aus RF-01.
- Die Profilwerte 60s/45s/180s sind Pilotziele; kalibriert ist bisher nur die
  Grenzwirkung, nicht die Höhe.
- Non-cooperativer Abbruch und späte Antworten (RF-10) sind hier nicht abgedeckt.



**Gebaut (RF §3A, erste Hälfte).**

| Artefakt | Rolle |
|---|---|
| `tools/rf_test_provider.py` | isolierter HTTP/1.1-SSE-Testprovider, 12 deterministische Modi (RFC §5 ohne die Client-seitigen: Clock-Sprung, Restart, Stop-Rennen), **echtes Chunked-Framing**, JSONL-Attribution je Request |
| `tools/rf_measure.py` | fährt jeden Modus über echten Transport (openai-SDK gegen den Provider) und ruft die **Engine-eigenen** Threshold-Funktionen; schreibt `outputs/rf/<stamp>/raw.jsonl` + `summary.json` |
| `tests/test_rf_test_provider.py` | 6 Tests pinnen die Fixture selbst (Terminierung, Attribution, 429+Retry-After, stille Modi, Moduswechsel) |

**Gemessen (Engine `fb56a7e0`, Provider lokal, kein Netzwerk-Egress).**

| Modus | semantische Deltas | Client (s) | Ergebnis |
|---|---|---|---|
| healthy_fast | 5 | 0.37 | vollständig |
| healthy_slow | 5 | 1.50 | vollständig |
| reasoning_only | 5 | 1.51 | vollständig |
| slow_prefill | 5 | 4.50 | vollständig |
| stall_mid | 2 | 6.61 | vollständig nach Stille |
| headers_no_sse | 0 | 15.02 | ReadTimeout (Client) |
| ping_only | 0 | 20.02 | ReadTimeout (Client) |
| partial_toolcall_disconnect | 1 | 0.003 | RemoteProtocolError |
| rate_limit | 0 | 1.36 | HTTP 429 + Retry-After 120 |
| unavailable | 0 | 1.19 | HTTP 503 |
| auth_error | 0 | 0.04 | HTTP 401 |
| context_overflow | 0 | 0.04 | HTTP 400 |

Jeder Modus liegt zusätzlich als Server-Record vor (zweiseitige Attribution:
Client-Beobachtung gegen Provider-Beobachtung, z. B. `headers_no_sse`: Client
15.0s Timeout vs. Provider 6.0s bis Verbindungsende).

**Befund für RF-02 (aus der Messung, nicht behauptet):** Die Engine leitet für
152k Kontext **300s** Stale-Geduld ab (`_scale_stale_timeout_for_context`: >100k →
300s, >50k → 240s, Basis 180s), während `_derive_stream_stale_timeout` ohne
Kontextskalierung bei **180s** bleibt. Genau diese 300s erschienen im gemeldeten
Symptom (`auto-reconnect at 300s`). Welcher Wert einen laufenden Stream tatsächlich
regiert, ist damit die erste Frage von RF-02 — die Kontextskalierung ist plausibel
richtig für langsames Prefill, aber sie ist *kein* requestweites Gesamtbudget.

**Weiterhin `not_run` (RF-01 bleibt offen):** der vollständige Agent-Lauf mit
152k Kontext und 80 Iterationen (braucht Modell + Budget), die reale
GLM-Stichprobe, Kompression/Summary (RF-06/07), Crash-Fenster (RF-09) und
Stop-Rennen (RF-10).

```bash
# Fixture + Messung reproduzieren (Engine-Interpreter)
~/.hermes/hermes-agent/venv/bin/python tools/rf_measure.py
python3 tools/rf_test_provider.py --port 8731 --mode ping_only   # manuell
```


## 3. RF-02, RF-04–RF-12 — offen

| ID | Stand | Nächster konkreter Schritt |
|---|---|---|
| RF-02 | offen | Requestweite monotone Deadline + Restbudget für SDK/Retry/Backoff/Fallback/Cleanup; Cleanup-Toleranz ≤5s im lokalen Pilot |
| RF-04 | offen | Gemeinsame Retry-Policy über SDK/Stream/Summary/äußere Ebene zählen; 429/5xx/Auth/Schema; Circuit Breaker + Half-open testen |
| RF-05 | offen | Fallback-Parameter/Budget prüfen; fehlender Fallback → sichere fortsetzbare Pause |
| RF-06 | offen | Kompression: Wire-Größe messen, Goal/Korrekturen/Evidenz/Toolpaarung erhalten, selbst zeitlich begrenzt |
| RF-07 | offen | Persistierter ehrlicher Zwischenstand am 80er-Limit ohne Provider; Summary optional und nicht blockierend |
| RF-08 | offen | Persistente Segmente unter derselben Goal-Identität; kein automatisches „continue" im normalen Chat |
| RF-09 | offen | Crash-Fenster um Checkpoint/externe Wirkung; ein Besitzer pro Segment |
| RF-10 | offen | Stop in Prefill/Stream/Kompression/Backoff/Summary ≤2s lokal; späte Antworten inert |
| RF-11 | offen | Kernpfade in Vanilla-Hermes **und** integriert; redigierte Diagnose |
| RF-12 | offen | Vorher/Nachher-Bericht mit klarer Trennung Testprovider ↔ echter Provider |

## 4. Befehle dieser Runde (alle reproduzierbar)

```bash
# Anker gegen die installierte Engine prüfen (schreibt nichts)
python3 host-overlay/patch_rf03_monotonic_stream_clock.py --check

# Defekt zeigen / Fix zeigen (Engine-Interpreter, echter Code)
~/.hermes/hermes-agent/venv/bin/python tools/host_rf_monotonic_probe.py ~/.hermes/hermes-agent  # FAIL A (rot)
~/.hermes/hermes-agent/venv/bin/python tools/host_rf_monotonic_probe.py <patched-copy>          # PASS A+B (grün)

# Regression der Engine-eigenen Wait-/Notice-Tests (vor/nach identisch)
~/.hermes/hermes-agent/venv/bin/python tools/host_engine_regression_probe.py <tree> \
    tests/agent/test_stream_wait_notice.py tests/agent/test_nonstream_wait_notice.py

# Repo-Pin (läuft in der normalen Suite)
.venv/bin/python -m pytest tests/test_rf03_monotonic_stream_clock.py -q
```

Redigierte Rohtrace: beide Probe-Läufe oben sind vollständig; es werden keine
Header, Keys oder Prompt-Inhalte ausgegeben (der Probe fährt einen Fake-Endpoint
ohne Netzwerkzugriff).

## 5. RF-04 — gleiche Retry-Policy über alle Schichten (16.09., Worker A + Zentrale-Verifikation)

**Befund (rot-first, Pristine-Engine).** SDK-Transport={`HERMES_STREAM_RETRIES:2`} → bis
3 Transportversuche *pro Ebene*, äußere Recovery summierte unbegrenzt. Pristine-Probe RC=1.

**Fix.** `host-overlay/patch_rf04_retry_budget.py` — Anker in
`agent/chat_completion_helpers.py` + `agent/chat_completion_nonstream.py`:
`_request_attempt_budget()` (Config `agent.request_max_attempts`, Default 3, env
`HERMES_REQUEST_MAX_ATTEMPTS`; 0 schaltet bewusst ab), requestlokaler Zähler, geprüft
vor jedem Stream-Retry **und** im Summary-Pfad. Auth-/Schemafehler bleiben unretryierbar.

**Nachher** (gepatchte Kopie `/tmp/gv_rf04`, Engine-Interpreter):
```
PASS A shared budget caps the logical request — wire attempts=3 (limit 3)
PASS A2 exhausted budget: later outer leg opens at most 1 fallback request — added=0
PASS B auth_error (401) is not stream-retried — attempts=1, error propagated=True
PASS C rate_limit (429) Retry-After: no blind stream retry — attempts=1
PASS C2 unavailable (503) is not stream-retried — attempts=1, error propagated=True
```

**Komposition.** rf04+rf05+rf06 in beiden Reihenfolgen (/tmp/gv_rfcombine,
/tmp/gv_rfcombine2): beide Proben grün; Engine-Regressionen 13/13.

**Offene Grenzen (RF-04 nicht abgeschlossen).** Reale GLM-Stichprobe und die
RF-01-Agent-Läufe fehlen (`not_run`); die Profilwerte (3 Versuche gesamt) bleiben Pilotziele.

## 6. RF-05 — Fallback erhält passende Parameter (16.09.)

**Befund (rot-first, pristine RC=1).** `try_activate_fallback` aktiviert still einen
kleineren Kandidaten (90k Request vs. 4k Fenster) — kein Fitness-Gate.

**Fix.** `host-overlay/patch_rf05_fallback_fitness.py` — vor Aktivierung: grobe
Request-Größenschätzung gegen `context_length`-Override, Bild-/Tool-Heuristik,
`agent._rf05_no_compatible_fallback` = strukturierte Pause mit
`provider_unavailable: no compatible fallback`; kein Anruf beim kleinen Modell.

**Nachher** (gepatchte Kopie): RC=0; Pause-Flag trägt reason+Detail. Rollback: `.gv-backup`.

**Offene Grenzen.** Vision-/Reasoning-Heuristik grob (Bild/Tool-Heuristik nur Kernfall);
kalibrierte Werte stehen aus dem RF-01-Lauf noch aus.

## 7. RF-06 — Kompression: begrenzt und verlustarm (16.09.)

**Fix.** `host-overlay/patch_rf06_compression_preserve.py` — preserve-Liste
(`goal`, `user_correction` (letzte 3), `open_tool_call` (unbeantwortete IDs), letzte
Bild-Referenz) vor dem Drop; Obergrenze für EINEN Kompressionsaufruf
(`HERMES_COMPRESSION_DEADLINE_SECS`, Default 120 s, monotonic); reintegrieren verlorener
Records (`[RF-06 preserved]`-Zeilen bzw. verbatim assistant/tool_calls-Reihen).

**Verifikation (Zentrale, Engine-Interpreter).** Kollektor auf kombiniertem Baum:
`kinds: ['goal','open_tool_call']` mit offenem Toolcall; Reintegration mit vakuumfremdem
Summarizer liefert goal/correction/open_call wieder in die komprimierte Historie; beide
Reihenfolgen der drei Overlays komponieren; Engine-Wait-Regressionen 13/13 identisch.

**Offene Grenzen (RF-06 nicht abgeschlossen).** echte Wire-Größen-Messung des
Kompressionsergebnisses (RF-12-Bericht), Live- legt nur die harten Grenzeffekte fest.

## 8. RF-07 + RF-10 — Nachfolger-Runde (16.09., eigene Zentrale-Verifikation)

**RF-07** (`host-overlay/patch_rf07_interim_report.py`): am 80er-Limit klassifiziert
`_rf07_provider_failing()` den Provider aus dem In-Scope-Fehlerzustand (RF-04
`_rf04_wire_fail_streak`, letzter API-Fehler, Exit-Reason). Fehlgeschlagener Provider
⇒ **null Modell-Aufrufe**: deterministischer lokaler Markdown-Report (Goal-Text, letzte
12 Tool-Events, Dateien, offene Items) als Assistant-Row durch die bestehende
TurnFinalizer-`_persist_session`-Pipeline. Fährt eine Summary: max. 1 Retry unter
monotonem Budget (`HERMES_RF07_SUMMARY_BUDGET`, Pilot 20 s); lokaler Report trotzdem.

Red-first (Pristine RC=1): „model summary attempts fired against a failing provider —
NO local guaranteed report“. Patched (Kopie `/tmp/gv_int_f7`, Engine-Interpreter): 7×PASS
(„summary model calls = 0", elapsed 0.006 s, Report-Row mit Goal-Text/Files/Marker), RC=0.

**RF-10** (`host-overlay/patch_rf10_stop_responsiveness.py`):
`_rf10_interruptible_sleep()` scheidet blockierte Engine-Waits in 200-ms-Scheiben mit
Monotonic-Uhr und `agent._interrupt_requested`-Check; gepatcht:
`turn_overflow.py`-Kompressions-Backoff und der 3–8 s Recovery-Wait in
`agent_runtime_helpers.py`. Red-first pristine RC=1 (`elapsed=2.001s`); patched PASS A/B
elapsed 0.200/0.201 s (<2 s), RC=0.

**Zentrale-Verifikation (fresh copies frisch gebaut).** Alle fünf Overlays
(rf04→rf05→rf06→rf07→rf10) applied to `/tmp/gv_int_full`: vier Proben OK,
Engine-Regressionen **13/13**. Pins: `tests/test_rf07_interim_report.py` (2) +
`tests/test_rf10_stop_responsiveness.py` (5) — 7 passed. Volle Suite RC=0 (1461 Punkte).

**Offene Grenzen (ehrlich).** RF-07-Probe trieb `handle_max_iterations` gegen einen
gestubbten Provider — kein echter hängender HTTP-Lauf; Ende-zu-Ende-Persistenz durch
TurnFinalizer bleibt dem RF-01-Lauf überlassen. RF-10 deckt Kompressions-Backoff +
Recovery-Wait; der Stream-Monitor-Interrupt-Pfad und echtes `/stop` am Live-Provider
sind ausstehend (needs RF-01/RF-11-Läufe). Summary-Budget 20 s = Pilot-Hypothese.
