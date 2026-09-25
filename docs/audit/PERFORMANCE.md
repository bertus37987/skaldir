# PERFORMANCE.md — Performance-/Langlaufbericht (ABNAHME §114, S15, RF-12, §108)

Stand: 16.09.2026 · Branch `godsv-supervisor-hardening` · HEAD `8da8b3d` (+ uncommittete
Parallel-Wellen, siehe Abschnitt 7).

**Regel dieses Berichts:** jede Zahl ist in dieser Session mit dem genannten Befehl
entstanden; wo eine Zahl fehlt, steht `not_run` **mit Grund** und dem kleinsten nächsten
Schritt. Es wird nichts geschätzt, nichts aus einem früheren Lauf fortgeschrieben und nichts
aus einem grünen Unit-Test abgeleitet (ABNAHME §0, §5.2).

## 0. Messumgebung (damit die Zahlen einordenbar sind)

| Größe | Wert | Befehl |
|---|---|---|
| CPU | AMD Ryzen 5 8600G (12 Threads) | `nproc` / `grep -m1 "model name" /proc/cpuinfo` |
| RAM | 30 GiB total, ~20 GiB verfügbar | `free -g` |
| Last beim letzten Messpunkt | `0.66 0.96 0.93` (1-min/5-min/15-min) | `cat /proc/loadavg` um 19:20:37 |
| Projekt-Python | 3.12.13 (`.venv`) | `.venv/bin/python --version` |
| Node | v26.8.1 | `node --version` |
| Engine-Python | 3.11.16 (Engine-venv) | `~/.hermes/hermes-agent/venv/bin/python --version` |
| Engine-Revision | `fb56a7e06d` (`~/.hermes/hermes-agent`) | `git -C ~/.hermes/hermes-agent rev-parse --short HEAD` |
| Dateisystem | NVMe, 7 % belegt | `df -h /home` |

Der Host ist eine geteilte Workstation (12 Threads, weitere Prozesse). Alle Zahlen sind
Wanduhr-Messungen unter dieser Bedingung; die Streuung ist unten je Messpunkt angegeben,
wo mehr als eine Wiederholung gefahren wurde (§108: **mindestens drei Wiederholungen je
Messpunkt**).

---

## 1. Gemessen — Testsuite (Wanduhr)

```bash
cd ~/projects/gods-voice && .venv/bin/python -m pytest -q -p no:randomly
```

| # | Zeitpunkt | Kommando | Ergebnis | Wanduhr |
|---|---|---|---|---|
| 1 | 19:02:24 → 19:04:58 | `pytest -q -p no:randomly` (HEAD `b7a287a`, sauberer Baum) | 1515 gesammelt · **1508 passed · 7 skipped · 0 failed** · RC=0 | **154.356 s** |
| 2 | 19:07:03 → 19:09:38 | `… --ignore=tests/test_system_one_*.py` (6 WIP-Module) | **1508 passed · 7 skipped · 0 failed** · RC=0 | **155.162 s** |
| 3 | 19:13:20 → 19:15:55 | `pytest -p no:randomly -vv --tb=no` (HEAD `8da8b3d`, WIP-Module enthalten) | **1625 passed · 7 skipped · 0 failed** · RC=0 · Summenzeile `1625 passed, 7 skipped, 1 warning in 154.82s` | **154.82 s** |
| 4 | 19:23:00 → 19:25:37 | **kanonisch**: `pytest -q -p no:randomly` (HEAD `8da8b3d`, WIP-Module enthalten) | 1632 gesammelt · **1625 passed · 7 skipped · 0 failed** · RC=0 | **156.612 s** |

**Vier Wiederholungen, Streuung 1.5 %** (154.36 / 154.82 / 155.16 / 156.61 s; Mittel = **155.24 s**;
`(154.356+155.162+154.82+156.612)/4`).
Die Anzahl der Tests unterscheidet sich zwischen den Läufen, weil parallele Wellen während
der Messreihe Testmodule hinzugefügt haben — die Laufzeit hängt an den langsamsten
Engine-Proben, nicht an der Testanzahl (siehe 3. und 4.).

Zwischenstand, ehrlich: ein weiterer Lauf (19:10:18 → 19:12:45, HEAD `e422d22`) war **rot** — die
Parallel-Welle schrieb in diesem Fenster `src/godsv/system_one*.py` und die vier Module
`computer_use/events/llm/voice.py` neu; Ergebnis 1255 passed / 49 failed / 57 errors, danach
ein pytest-`INTERNALERROR`-Abbruch bei ~83 %. Dieser Wert ist **kein** Performance-Messpunkt
(abgebrochen), sondern ein Zustandsbefund: siehe `docs/audit/TEST_REPORT.md`. Nach Abschluss
der Welle lief dieselbe Suite wieder grün (Lauf 3).

## 2. Gemessen — Plugin-Contract-Smoke und Audit-Werkzeuge

```bash
node tools/check_desktop_plugin.mjs        # 3 Wiederholungen
python3 tools/build_audit_register.py --check
```

| Messpunkt | Wiederholungen | Ergebnis | Wanduhr |
|---|---|---|---|
| `node tools/check_desktop_plugin.mjs` | 3 | `desktop panel: contract OK` · **158 ok / 0 fail** | 0.0597 / 0.0565 / 0.0608 s (Spread 4 ms) |
| `python3 tools/build_audit_register.py --check` | 1 | `audit register current — 97 criteria` | 0.031 s |
| Alle 11 Engine-Overlays `--check` gegen die Live-Engine | 1 Durchlauf über 11 Dateien | 11 × RC=0 („would patch", **nichts geschrieben**) | 0.186 s gesamt |

## 3. Gemessen — Engine-Regressionsprobe (13 Tests, 3 Wiederholungen)

```bash
~/.hermes/hermes-agent/venv/bin/python tools/host_engine_regression_probe.py \
    ~/.hermes/hermes-agent \
    ~/.hermes/hermes-agent/tests/agent/test_stream_wait_notice.py \
    ~/.hermes/hermes-agent/tests/agent/test_nonstream_wait_notice.py
```

| # | Ergebnis | Wanduhr |
|---|---|---|
| 1 | `engine regression probe: 13 passed, 0 failed` · RC=0 | 0.204 s |
| 2 | `13 passed, 0 failed` · RC=0 | 0.197 s |
| 3 | `13 passed, 0 failed` · RC=0 | 0.197 s |

Die Engine-eigenen Wait-/Notice-Regressionen laufen auf der **unveränderten** installierten
Engine unverändert grün — dieselbe Zahl (13/13) führen die RF-Statusdokumente vor/nach den
Overlays. Streuung 7 ms.

## 4. Gemessen — RF-10 Stop-Ansprechzeit: gepatcht vs. pristine (je 3 Wiederholungen)

```bash
# pristine: nur die zwei Engine-Dateien kopiert, nichts gepatcht
~/.hermes/hermes-agent/venv/bin/python tools/host_rf10_stop_probe.py /tmp/gv_perf10/pristine
# gepatcht: dieselbe Kopie + host-overlay/patch_rf10_stop_responsiveness.py
python3 host-overlay/patch_rf10_stop_responsiveness.py /tmp/gv_perf10/patched
~/.hermes/hermes-agent/venv/bin/python tools/host_rf10_stop_probe.py /tmp/gv_perf10/patched
```

| Fall | # | Gemessene Stop-Latenz | RC |
|---|---|---|---|
| **pristine** (blockiertes `time.sleep(2)` im Kompressions-Retry) | 1 / 2 / 3 | **2.000 s** (Stop wird erst nach dem vollen Sleep bemerkt) | 1 (FAIL, erwartet) |
| **gepatcht** A (Kompressions-Retry-Pause) | 1 / 2 / 3 | **0.200 s** | 0 |
| **gepatcht** B (3–8 s Recovery-Wait) | 1 / 2 / 3 | **0.200 s** | 0 |

Das ist die gesuchte Vorher-/Nachher-Zahl: **2.000 s → 0.200 s** (Faktor 10) bei einer
Anforderung von ≤ 2 s (Kapitel 09 §3B). Probe-Wanduhr gesamt: 2.12 s pristine / 0.55 s
gepatcht (der gepatchte Lauf ist auch insgesamt kürzer, weil er nicht mehr aussitzt).

**Grenze dieser Messung:** sie treibt die echten Engine-Funktionen mit einem gestubbten
Agent-Objekt; es gibt **keinen** echten Provider auf der Leitung und kein echtes `/stop`
eines Live-Turns (RF-11-Blocker). Die frühere Reproduktion aus `tools/host_rf09_crash_probe.py`
mit einem echten `SIGKILL` deckt das Crash-Fenster, nicht den Stop-Race.

## 5. Gemessen — RF-Testprovider-Stichprobe (RF-01-Datenlage, 2 frische + 1 archivierter Lauf)

```bash
~/.hermes/hermes-agent/venv/bin/python tools/rf_measure.py --out /tmp/gv_rf_rep<N>
# archiviert: outputs/rf/2026-09-16_131617/{raw.jsonl,summary.json}
```

Wanduhr je vollständigem Lauf: **53.71 s** (Rep 1) und **53.15 s** (Rep 2); der archivierte
Lauf brauchte dieselbe Größenordnung. Client-seitige Dauer je Modus (s):

| Modus | Rep 1 | Rep 2 | archivierter Lauf (13:16) | abgeschlossen |
|---|---|---|---|---|
| `healthy_fast` | 0.371 | 0.360 | 0.373 | ja / ja / ja |
| `healthy_slow` | 1.504 | 1.504 | 1.504 | ja / ja / ja |
| `reasoning_only` | 1.504 | 1.504 | 1.505 | ja / ja / ja |
| `slow_prefill` | 4.503 | 4.504 | 4.503 | ja / ja / ja |
| `stall_mid` | 6.610 | 6.625 | 6.613 | ja / ja / ja |
| `headers_no_sse` | 15.017 | 15.016 | 15.017 | nein — Client-`ReadTimeout` |
| `ping_only` | 20.027 | 20.026 | 20.018 | nein — Client-`ReadTimeout` |
| `partial_toolcall_disconnect` | 0.003 | 0.003 | 0.003 | nein — `RemoteProtocolError` |
| `rate_limit` | 1.335 | 1.178 | 1.362 | nein — HTTP 429 + `Retry-After` |
| `unavailable` | 1.450 | 1.455 | 1.189 | nein — HTTP 503 |
| `auth_error` | 0.042 | 0.041 | 0.042 | nein — HTTP 401 |
| `context_overflow` | 0.043 | 0.042 | 0.043 | nein — HTTP 400 |

Die Streuung liegt bei allen deterministischen Modi **unter 25 ms** (Ausnahme: die
Fehlerpfade `rate_limit`/`unavailable`, die von Netz-/Scheduler-Jitter des lokalen
HTTP-Fixtures abhängen, ±0.3 s). Aus der Engine **abgeleitet** (nicht hier definiert):

```text
stale-Schwellen: 16k → 180 s · 64k → 240 s · 96k → 240 s · 152k → 300 s
                 · stream-derived default → 180 s
```

Das ist die Zahl, die RF-02 als Problem benennt: für 152k Kontext erlaubt die
Kontextskalierung **300 s** Stille, während die aus dem Stream abgeleitete Grenze 180 s
bleibt — ein requestweites Gesamtbudget gab es vor dem Overlay nicht.

## 6. Gemessen — Docker-Builder-Pfad (Pull/Pin/Container)

```bash
docker version; docker images
time docker pull alpine:3.20            # bereits lokal: Digest-Verifikation
time docker pull alpine:3.18            # kalter Fetch (~3.5 MB)
time docker image inspect --format '{{.Id}}' alpine:3.20    # Digest-Pin auflösen
# Container-Runde 3× mit digest-gepinntem Image:
docker run -d --rm --name gvp$i --network none --security-opt no-new-privileges \
    --label godsv.build_id=perf-probe <sha256> sleep 30
docker exec <cid> echo hello-build ; docker stop -t 1 <cid>
```

| Messpunkt | Wiederholungen | Ergebnis | Zeit |
|---|---|---|---|
| Docker-Daemon | 1 | Client+Server 29.6.1, API 1.55, x86_64 | — |
| `docker pull alpine:3.20` (lokal vorhanden) | 1 | Digest verifiziert (`sha256:d9e853e8…`) | 1.621 s |
| `docker pull alpine:3.18` (kalt) | 1 | neu geladen | 3.161 s |
| `docker image inspect` (Pin auflösen) | 1 | `sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc` | 0.019 s |
| Container-Start (digest-gepinnt, `--network none`) | 3 | gestartet | 0.148 / 0.150 / 0.141 s |
| `docker exec` im Container | 3 | `hello-build` | 0.051 / 0.048 / 0.056 s |
| `docker stop -t 1` | 3 | Container beendet, **0 Reste** (`docker ps -a` leer) | 1.073 / 1.075 / 1.074 s |
| `docker stop` **ohne** `-t` (Standard 10 s Grace) | 3 | — | **10.13 s** (Sleep ignoriert SIGTERM) → die Wartezeit ist Grace-Periode, kein Build-Kostenanteil |

Die `-t 1`-Beobachtung ist ein echter Betriebshinweis: ein `sleep`-Keepalive im Container
reagiert nicht auf SIGTERM, also kostet jedes `docker stop` ohne kurzes Timeout die volle
Grace-Periode. Für einen Builder heißt das: **kein** `sleep N`-Keepalive ohne
`stop -t`-Disziplin (dieselbe Beobachtung steht in `docs/audit/DOCKER-BUILDER-EVIDENCE.md`
§7).

Der dort belegte End-to-End-Pfad (digest-gepinnte Pulls, Manifest v2 fail-closed, echter
`ToolBuilder.build()`, Pandoc md→html, SearXNG 200 mit 34 Treffern, Reuse nach Publish ohne
Restart) ist **live_tested**; er wurde in dieser Session nicht erneut gefahren. Was der
Builder **nicht** messbar macht: einen echten Modellaufruf auf dem gebauten Tool (bleibt
`not_run`, braucht die RF-01-Freigabe).

## 7. Was NICHT gemessen ist — mit Grund und nächstem Schritt

| Fehlt | Grund (exakt) | Kleinster nächster Schritt |
|---|---|---|
| **Der 152k-/80-Iterationen-Langlauf (RF-01, RF-11, RF-12, §108-Langlaufmessung)** | keine Budgetfreigabe für einen echten Modelllauf in diesem Fenster (`docs/RF-LONGRUN-FREIGABE.md`) | Freigabe einholen, dann `tools/rf_measure.py` **plus** den echten Agent-Turn fahren und TTFT/Gesamtdauer/Retry-Wartezeit/Tokens je Segment roh ablegen |
| Wire-Größen-Reduktion der Kompression (RF-06) | braucht einen echten Lauf mit Summary-Aufruf; die Beweise sind bislang vakuumfremde Stub-Summarizer | im Langlauf vor/nach der Kompression die Wire-Größe aus dem echten Request mitschreiben |
| Kosten/Tokens echter Modelle | kein autorisierter Provideraufruf; `outputs/benchmark/*.raw.json` sind Vorvertrags-Läufe und dürfen nicht als §97-Armbeleg dienen | Benchmark-Rohdaten nach `docs/audit/benchmark-runs/` schreiben und mit `tools/eval_benchmark.py` auswerten |
| CPU/RAM/Heap-Wachstum über einen Langlauf | ohne Langlauf keine Messreihe | `§108`: Ressourcenmessung am autorisierten Langlauf anhängen |
| App-Startzeit, Erststart-Renderzeit, Chat-Scroll-Performance | headless (kein Display); eine Messung wäre erfunden | Gerätefenster + `tools/gv_shot.mjs`/`gv_ui_e2e.mjs` mit Zeitstempeln |
| GUI-/Computer-Use-Latenz | kein realer GUI-Runner verfügbar (Container-GUI-Image fehlt), siehe `docs/audit/COMPUTER_USE_SUBTOOLS.md` | Image bereitstellen, dann §57-Messung je Plattform |
| Voice-Kalibrierung (Stufe C) Latenz/Kosten | Holdout nicht gefahren | Stufe C einfrieren und fahren |
| Jev-Latenz/-Kosten | kein Zugang (Early Access), siehe `docs/audit/PLATFORM-MATRIX.md` §2 | LLM-Adapter-Arm (B) fahren, Jev-Arme C/D erst mit Credential |

**Keine Zahl dieses Berichts stammt aus einer Schätzung.** Wo ein Messwert fehlt, steht
`not_run` — auch dort, wo ein plausibler Wert leicht zu erfinden wäre (z. B. „Kosten pro
Lauf“).

## 8. Vorher/Nachher-Zusammenfassung (RF-12-Kern)

| Größe | vorher (pristine) | nachher (Overlay) | Quelle/Befehl |
|---|---|---|---|
| Stop im Kompressions-Retry | 2.000 s | **0.200 s** | §4, `host_rf10_stop_probe.py` ×3 |
| Stop im Recovery-Wait (3–8 s) | volle Wartezeit (Probe: identisch blockiert) | **0.200 s** | §4 |
| Requestweite Deadline | keine (`FAIL A … no kill within 12s`) | 3.00 s Budget → Kill bei 3.00 s, Overshoot **+0.00 s** | `tools/host_rf02_deadline_probe.py` (aus `docs/RF-STATUS.md` §1b übernommen, in dieser Session **nicht** erneut gefahren) |
| Wire-Versuche pro logischer Anfrage | 12 (gestapelte Ebenen) | **3** (Limit 3) | `tools/host_rf04_retry_budget_probe.py` (übernommen) |
| Wall-Clock-Sprung | fälschlich als Stille → Stream-Kill | Sprung inert, echte 1 h Stille feuert weiterhin | `tools/host_rf_monotonic_probe.py` (übernommen) |

Die mit „übernommen" markierten Zeilen sind **frühere Messungen aus den RF-Statusdokumenten**
und in dieser Session nicht wiederholt worden — sie stehen hier der Vollständigkeit halber
und sind nicht als frische Messung dieses Berichts zu lesen. Die Zeilen in §4 sind frische
Wiederholungen (3×) mit exaktem Befehl.
