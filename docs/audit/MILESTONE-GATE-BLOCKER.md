# Meilenstein-Gate-Blocker beseitigt — Feature `fix-milestone-gate-blockers`

Stand 2026-09-28. Repo `/home/willy/Agentic_Projects/Skaldir_Agent`, Ausgangsstand `f667366`
(Arbeitsbaum zusätzlich mit uncommitteter Vorarbeit der Übernahme). Alle Befehle aus dem
Repo-Wurzelverzeichnis, mit `uv run` (Python 3.11.16, `.venv`).

## 1. Gemessene Ausgangslage (Reproduktion vor der Änderung)

| Prüfung | Befehl | Exit | Beobachtung |
|---|---|---|---|
| die zwei roten Tests | `uv run pytest tests/test_cu_live_desktop.py -q -k "may_not_carry_its_own_argv or clicks_inside_the_target"` | **1** | 2 failed, beide mit `input refused at the adapter: this runner drives the real host desktop (live-desktop) but no DESK-02 fence is bound — hold the desktop lease and bind it before acting` |
| ganze Datei | `uv run pytest tests/test_cu_live_desktop.py -q` | **1** | 22 passed, genau diese 2 failed |
| Lint | `uv run ruff check src tests` | **1** | **375** findings, darunter `src/skaldir/cu_host_session.py:57 I001` (unsorted import block) |
| Artefakte | md5 von `cu_runs.jsonl` / `events.db` vor und nach dem Dateilauf | 0 | beide unverändert — dieser Lauf schreibt die Runtime-Artefakte **nicht** |

Die zwei roten Tests sind **vorbestehend**: identischer Befund am Vorgänger-Stand (die Übernahme-Doku
und `library/environment.md` belegen ihn für `HEAD~1`), verursacht von Commit `20426dd`
(„no unbound host path may drive the desktop; fence at the adapter").

## 2. Ursachen

**(a) Testseitig — fehlende DESK-02-Bindung.** Seit `20426dd` weist `HostSessionRunner.act()`
eine Eingabeaktion auf einem `live-desktop`-Runner ab, solange kein Fence gebunden ist. Die
produktive Aufruferkette bindet ihn (`act_service.run_action_list` nimmt die Lease und ruft
`_bind_desktop_fence`); die zwei Tests taten das nicht. Zwei weitere gemessene Fakten blockierten
sie zusätzlich:

* `_full_desktop()` fragt die *maschinenweite* Opt-in-Einstellung (`live_desktop_optin()`), nicht
  das Instanz-Flag. Auf diesem Host ist das Live-Desktop-Opt-in **an**
  (`~/.hermes/godsv/settings.yaml: computer_use.allow_live_desktop: true`), ein *scoped*-Lauf wäre
  also stillschweigend ein Vollbild-Lauf — F1/F8 hätten einen anderen Pfad gemessen als benannt
  (eine `cli`-Action mit eigenem argv wäre tatsächlich gelaufen).
* Eine Tastaturaktion wird nur für ein Backend gebaut, das die begrenzte Probe als *zustellend*
  gemessen hat; auf einem blanken Xvfb ist das `PROBE_MISCONFIGURED` (unbekannt, kein Defekt), weil
  ohne Fenstermanager kein Fenster aktiviert und bestätigt werden kann.

**(b) Ruff I001** in `src/skaldir/cu_host_session.py`: der Importblock der Übernahme war unsortiert
(`from dataclasses …` vor `from collections.abc …`).

**(c) Runtime-Artefakte.** Die drei zusätzlichen Zeilen in `cu_runs.jsonl` und die Änderung an
`events.db` tragen Zeitstempel von **vor** dieser Sitzung (15:38 / 16:15 / 16:28) — ein Lauf, dessen
Runtime-Verzeichnis auf das Repo-Wurzelverzeichnis zeigte. Der Mechanismus ist heute bekannt: der
`skill_act`-Tool-Handler defaultet `workspace` auf `"."`, und `skill_act` schreibt seinen Run-Record
nach `runtime=<workspace>` — aus dem Repo-Wurzelverzeichnis gestartet landet er in `./cu_runs.jsonl`
und `./events.db`. In der Gesamtsuite hat genau das ein Test getan; er ist in §3 korrigiert. Gemessen:
`tests/test_cu_live_desktop.py` und die ACT-Ebenen-Tests (`test_act_service.py`,
`test_act_service_container.py`, `test_cu_lifecycle.py`, `test_desktop_lease.py`,
`test_shared_integration_16.py`) verändern die Artefakte nicht.

## 3. Änderungen

**`tests/test_cu_live_desktop.py`** (testseitig, produktive Regeln unberührt):

* neuer Helfer `_scoped_live_runner(...)`: bindet den Fence **genau so, wie das Produkt es tut** —
  echter `DesktopLeaseStore` auf einer tmp-Zustandsdatei + `act_service._bind_desktop_fence(...)`,
  und setzt `SKALDIR_HOME` auf ein leeres tmp-Verzeichnis, damit der *scoped* Pfad gemessen wird.
  (Ein No-op-Fence hätte die Regel nicht wirklich ausgeübt; die Regel selbst bleibt erzwungen —
  `tests/test_desktop_lease.py::test_an_unbound_live_desktop_runner_refuses_to_act` und
  `::test_a_live_desktop_runner_with_a_fence_actually_checks_it` bleiben grün.)
* `_start_own_window_manager` / `_stop_own_window_manager` / `_window_manager_owns`: `openbox` auf
  dem **testeigenen** Display, Bereitschaft über `xprop -root _NET_SUPPORTING_WM_CHECK` gemessen
  („no such atom on any window" ohne, `window id # 0x…` mit einem Fenstermanager). Der Fenstermanager
  startet **erst für den Tastaturteil**: mit ihm laufend benennt `getmouselocation` den WM-Rahmen
  statt des Zielfensters und `_measure_clickable_point` müsste den Test überspringen (gemessen).
* die Tastaturzeile des Pointer-Tests prüft jetzt zusätzlich `runner.input_probe_report()`:
  `delivers is True` und `backend == "xdotool"` — ein rc-0-Versand allein gilt nicht als Zustellung.
* zwei unbenutzte Imports (`SETTINGS_KEY`, `TARGET_KEY`) entfernt; vorbestehende F401-Befunde.

**`src/skaldir/cu_host_session.py`**: nur die Import-Reihenfolge (Zeilen 69–72), `I001` beseitigt.
Keine Verhaltensänderung.

**`tests/conftest.py`**: neuer autouse-Guard `_tracked_runtime_artifacts_untouched` — erfingerprintet
`cu_runs.jsonl` und `events.db` im Repo-Wurzelverzeichnis und lässt den schreibenden Test unter
Nennung seines Namens fehlschlagen (gleiches Muster wie der vorhandene Guard für die echte
Hermes-Installation). Bewusst **kein** Wiederherstellen: ein Zurückschreiben des Snapshots würde
auch eine Zeile löschen, die ein nebenläufiger Lauf des Besitzers in derselben Sekunde angehängt
hat.

**`tests/test_agent_toolset.py`** (der Guard hat den Schreiber benannt): `skill_act` baut seinen
Run-Record unter `runtime=<workspace>`, und das Tool-Schema defaultet `workspace` auf `"."`.
Läuft die Suite aus dem Repo-Wurzelverzeichnis, landet der Record von
`test_act_runner_falls_back_to_named_sandbox_env` in `./cu_runs.jsonl` **und** `./events.db`. Der
Test nimmt jetzt `workspace=str(tmp_path)` (ebenso `test_act_policy_kind_validation`, derselbe
Default). Der produktive Default `"."` bleibt unverändert — er ist für den Agenten gemeint, nicht
für die Suite.

## 4. Ergebnis

| Prüfung | Befehl | Exit | Beobachtung |
|---|---|---|---|
| die zwei Tests | `uv run pytest tests/test_cu_live_desktop.py -q -k "may_not_carry_its_own_argv or clicks_inside_the_target"` | **0** | 2 passed |
| die Datei | `uv run pytest tests/test_cu_live_desktop.py -q` | **0** | 24 passed |
| CU/Eingabe-Umfeld | `uv run pytest tests/test_cu_live_desktop.py tests/test_desktop_lease.py tests/test_cu_lifecycle.py tests/test_input_probe_owned_display.py tests/test_live_desktop_launch_env.py tests/test_input_backend.py tests/test_evdev_keys.py tests/test_live_keyboard_backend_gate.py -q` | **0** | 167 passed |
| Lint auf berührten Dateien | `uv run ruff check src/skaldir/cu_host_session.py tests/conftest.py tests/test_cu_live_desktop.py` | **0** | „All checks passed" |
| Lint-Gate gesamt | `uv run ruff check src tests` | **1** | **372** vorbestehende findings (siehe §5) |
| betroffene Testdateien | `uv run pytest tests/test_cu_live_desktop.py tests/test_agent_toolset.py tests/test_desktop_lease.py -q` | **0** | 84 passed |
| Artefakte nach allen Läufen | `md5sum -c` gegen den Snapshot vom Sitzungsbeginn | 0 | `cu_runs.jsonl: OK`, `events.db: OK` |
| Gesamtsuite (Meilenstein-Probe) | `uv run python -m pytest tests/ -q` | **1** | 4 FAILED + 1 ERROR, alles Übrige grün (8255 Fortschrittszeichen; gesammelt 8254) |

Der eine ERROR der Gesamtsuite war der neue Artefakt-Guard an
`tests/test_agent_toolset.py::test_act_runner_falls_back_to_named_sandbox_env` — er hat den
tatsächlichen Schreiber benannt und ist mit dem `workspace=`-Fix oben behoben (Datei danach
einzeln: 18 passed, Artefakte unverändert). Die verbleibenden 4 FAILED sind **vorbestehend und
unabhängig von diesem Feature** (an `HEAD` identisch belegt):

| Test | Befund |
|---|---|
| `tests/test_env_leak_guard.py::test_no_module_reads_a_runtime_variable_directly` + `::test_the_guard_owns_the_spellings_the_other_guards_do_not` | `src/skaldir/act_service.py:1550-1551`, `src/skaldir/llm_provider.py:1023` lesen `SKALDIR_*`/`GODSV_*` direkt statt über `skaldir.env` |
| `tests/test_rename_compat.py::test_no_module_reads_the_two_prefixes_directly_anymore` | derselbe Befund über den anderen Guard |
| `tests/test_subagents.py::test_no_import_happens_while_the_children_run` | reihenfolgenabhängig: einzeln grün, in der Gesamtsuite laden die Kinder `registry`/`perf_runtime` nach |
| `tests/test_browser_backend.py::test_a_real_read_returns_the_page_content_not_the_json_envelope` | echter Lese-Pfad gegen das echte Werkzeug (umgebungsabhängig) |

Hinterlassene Prozesse: keine. Der in dieser Sitzung selbst gestartete Mess-Xvfb `:177` wurde per
PID beendet und seine Lock-/Socket-Dateien entfernt; `:0`/`:1` (Owner-Display) blieben unberührt.

## 5. Offene Punkte (bewusst nicht in diesem Feature)

1. **Ruff-Schulden außerhalb des Features.** `uv run ruff check src tests` steht bei **372**
   findings (vor dieser Arbeit 375). Verteilung: `E501` 104×, `F401` 82×, `UP017 datetime.UTC` 58×
   (davon `tests/test_scheduler*.py` 48× und `src/skaldir/scheduler.py` 10×), `UP035` 18×,
   `F841` 14×, `E702` 13×, `UP042` 13×, `E741` 8×, `F811` 5×, `B0xx` 9×, `I001` 31×, Rest kleinere
   Klassen. Die Dateien gehören zu Scheduler/TUI/CLI/Browser — **kein** Zusammenhang mit der
   Eingabe-Pipeline. Frühere Messungen („sauber außer `cu_host_session.py:57 I001`“) bezogen sich auf
   die **geänderten Dateien**, daher wirkte der Abstand wie ein einziger Befund. Empfehlung:
   eigenes Feature „Lint-Schulden“ mit `ruff check --fix` für die 202 automatisch behebbaren Befunde
   plus Handarbeit für die restlichen, gefolgt vom Gesamtsuite-Lauf (einige `F841`/`F811` sind
   seitentragend und dürfen nicht blind entfernt werden).
2. **Gate-Befehl für die Testsuite.** `uv run pytest tests/ -q` bricht die Sammlung ab (Exit **2**,
   5 Fehler: `ModuleNotFoundError: No module named 'tests'` / `'tools'`), weil
   `[tool.pytest.ini_options] pythonpath = ["src", "integrations/hermes"]` das Repo-Wurzelverzeichnis
   nicht auf `sys.path` legt und `pytest` anders als `python -m pytest` das cwd nicht hinzufügt.
   Funktionierender Befehl: `uv run python -m pytest tests/ -q` (8254 Tests gesammelt).
   Vorbestehend, unabhängig von diesem Feature.

## 6. Nachtrag 2026-09-29 — Punkt 1 aus §5 ist erledigt

Das empfohlene Feature „Lint-Schulden" wurde als `lint-debt-sweep` umgesetzt. Belege und Methode:
`docs/audit/LINT-DEBT-SWEEP.md`. Kurz:

* `uv run ruff check src tests` → **Exit 0**, „All checks passed". Ausgangslage 372 findings
  (Stand `892b4cc`), Endzustand 0. Keine Regel aus der Konfiguration ausgenommen, keine Datei
  ausgeklammert, kein `noqa` für einen Befund ergänzt.
* Die 202 automatisch behebbaren Befunde gingen über `ruff check --fix` je Regelklasse mit
  Diff-Durchsicht; die restlichen 170 wurden einzeln gelesen und von Hand geändert. Die E501-
  Zeilen wurden nur umbrochen; für jede berührte Datei belegt ein AST-Vergleich gegen `HEAD`,
  dass sich **kein einziges String-Literal** geändert hat (die beiden erlaubten Ausnahmen sind
  die eine entfernte `f`-Präfix bei `F541` und die eine gekürzte Dokuzeile in
  `src/skaldir/scheduler.py`).
* Drei der Befunde waren **echte Defekte**, keine Stilfragen: `Mapping` in `browser_layer.py`
  (`F821`, nur bei aufgelösten Annotationen sichtbar — `mcpserver` löst sie zur Laufzeit auf),
  `usage(...)` im Unbekannt-Aktion-Zweig von `surface_cli.py` (`F821`, NameError zur Laufzeit)
  und `"Backend"` in `stop_signal.__all__` (`F822`, `from … import *` wäre mit `AttributeError`
  gescheitert).
* Die eine F401-Stelle mit einem echten externen Konsumenten — `load_schema_template`, das
  `toolbuilder_cli` über `skaldir.toolbuilder` importiert — bleibt mit begründetem `noqa`
  erhalten. Die drei weiteren Namen, die die vorläufige Fassung dorthin gestellt hatte, haben
  nach frischer Messung **keinen** Konsumenten und wurden entfernt.
* Die 13 `UP042`-Klassen wurden zu `enum.StrEnum`. `str()` eines `(str, Enum)`-Members liefert auf
  diesem Interpreter `"Klasse.MEMBER"`, bei `StrEnum` den Wert; die Messung zeigt, dass im ganzen
  Repository **keine** Stringifizierung dieser 13 Klassen vorkommt (alle serialisieren über
  `.value`), die Umstellung ist also verhaltensneutral.
* Die Gesamtsuite `uv run python -m pytest tests/ -q` lief nach der Umstellung; das Ergebnis steht
  in der Abnahme des `lint-debt-sweep`-Features. Die vier dort noch rot markierten Tests sind
  dieselben vorbestehenden wie hier in §4 (env-Leak-Guards, reihenfolgenabhängiger Subagenten-Import,
  umgebungsabhängiger Browser-Lesepfad) und gehören zum Feature `suite-green-followup`.
