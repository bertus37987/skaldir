# SYSTEM_ONE_GUISTATE.md — JEV-15 GUIState-Vertrag (§52/§53/§93, ABNAHME Teil XV)

Stand: 16.09.2026, Branch `godsv-supervisor-hardening`, Arbeitsbaum (nicht committet);
Basis-HEAD `27913dc`. Betroffene Dateien: `src/godsv/system_one_guistate.py` (neu),
`src/godsv/computer_use.py` (additiv), `tests/test_system_one_guistate.py` (neu).

Vertragstext, wörtlich (ABNAHME Teil XV §93):

> **JEV-15 — GUIState-Vertrag**
> Computer Use erzeugt strukturierte, versionierte GUIStates aus aktueller
> Wahrnehmung. Ground Truth bleibt echte Appwirkung.

Vorher war die Lage eindeutig leer: `grep -rn "GUIState\|gui_state"
src/godsv/*.py` → **0 Treffer** (belegt in `docs/audit/COMPUTER_USE_SUBTOOLS.md`
und `docs/audit/PLATFORM-MATRIX.md`). Jetzt existiert der Vertrag.

## Was jetzt existiert (real, mit Datei:Zeile)

| Artefakt | Ort | Was es ist |
|---|---|---|
| `GUISTATE_SCHEMA_VERSION = "guistate/v1"` | `system_one_guistate.py` | die Version jeder GUIState; eine andere Version wird abgelehnt |
| `GROUND_TRUTH_APP_EFFECT = "app_effect"` | `system_one_guistate.py` | der einzige erlaubte Ground-Truth-Wert |
| `PERCEPTION_CHANNELS` / `LIVE_PERCEPTION_CHANNELS` | `system_one_guistate.py` | §51-Kanäle; nur echte GUI-Kanäle dürfen `live_display` beanspruchen |
| `GUI_STATE_FIELDS` | `system_one_guistate.py` | die §52-Feldliste, die **vollständig** berichtet werden muss |
| `PerceivedField` | `system_one_guistate.py` | ein Feld + `perceived` + `channel`; ein unperceivtes Feld mit Wert ist ein Contract-Error |
| `parse_perception_text(text, channel=...)` | `system_one_guistate.py` | liest **nur**, was der aktuelle Wahrnehmungstext sagt; Unlesbares bleibt unperceived |
| `GUIState` | `system_one_guistate.py` | der strukturierte, fingerprintete, versionierte Datensatz (+ `unperceived`, `certifies_effect`) |
| `build_gui_state(...)` | `system_one_guistate.py` | baut den State aus aktueller Wahrnehmung, verkettet `revision`/`previous`, verbietet Verbreiterung |
| `effect_check(...)` | `system_one_guistate.py` | §54.2-Postcondition auf der **Post-Action**-Wahrnehmung, `certifies: False` |
| `guistate_action_candidates(...)` | `computer_use.py` | Schnittmenge Kandidaten ∩ vorab erlaubte Kandidaten (nur filtern, nie ergänzen) |
| `system_one_guistate_gate(...)` | `computer_use.py` | advisory Bindung an den CU-Schritt; `allowed_kinds` **unverändert**, `mutates=False`, `certifies_effect=False` |

## Der Vertrag in Regeln (jede mit Testnamen)

1. **Strukturiert + versioniert + fingerprint.** `test_gui_state_is_structured_versioned_and_content_addressed`:
   alle §52-Felder, `schema_version == "guistate/v1"`, 64-stelliger Fingerprint,
   inhaltsadressierte `gui_state_id`; identische Wahrnehmung ⇒ identischer Fingerprint.
   Unbekannte Version wird abgelehnt (`test_an_unknown_schema_version_is_refused`).
2. **Aus aktueller Wahrnehmung, mit Versionskette.**
   `test_revision_chain_links_to_the_previous_state`: `revision 2`, `previous == <id von 1>`,
   geänderter Fingerprint; der Wahrnehmungstext wird verbatim (limitiert) mitgeführt
   (`test_gui_state_keeps_the_verbatim_perception_it_was_built_from`).
3. **Ehrlich über Nicht-Wahrgenommenes.**
   `test_unperceived_fields_are_explicit_and_never_defaulted`: jedes nicht
   wahrgenommene Feld steht mit `{"perceived": false, "value": null, "channel": ""}`
   **und** in `unperceived`; kein Plausibel-Default (ein fehlender Dialogblick ist
   nicht „kein Dialog"). `PerceivedField(perceived=False, value=…)` und ein
   GUIState mit fehlendem Feld sind Contract-Fehler; ein unbekannter Kanal ebenso.
4. **Die vorab erlaubte Aktionsmenge wird nie verbreitert.**
   `test_a_candidate_outside_the_pre_approved_set_is_refused`,
   `test_allowed_kinds_never_widen_along_the_revision_chain`,
   `test_guistate_binding_keeps_the_pre_approved_action_set`,
   `test_candidate_intersection_helper_never_adds_candidates`: Kandidat außerhalb
   der vorab erlaubten Menge ⇒ Contract-Error; `allowed_kinds` darf in der Kette
   nur schrumpfen; die Bindung gibt `allowed_kinds` unverändert zurück (§50).
5. **Ground Truth bleibt echte Appwirkung.**
   `test_ground_truth_is_pinned_to_the_real_app_effect` (`ground_truth != "app_effect"`
   ⇒ Contract-Error, `certifies_effect is False`),
   `test_effect_check_reads_the_post_action_perception_and_never_certifies`
   (beobachtete Änderung ⇒ `effect_observed: true`, trotzdem `certifies: false`).
6. **Kein Live-Anspruch ohne Live-Kanal.**
   `test_live_display_flag_requires_a_live_perception_channel`: auf diesem Host ist
   `live_display` `False`; ein Nicht-GUI-Kanal darf es nicht setzen.
7. **§98-Abschaltung.** `test_guistate_binding_is_off_with_the_flags_off`: Flags aus,
   fremder/abgeschalteter Provider, keine Evaluation oder kein GUIState ⇒ `None`,
   der bestehende CU-Pfad läuft unverändert.

## Beweise (gefahren, mit beobachteter Ausgabe)

Rot zuerst, vor der Implementierung:

```bash
$ .venv/bin/python -c "import godsv.system_one_guistate"
ModuleNotFoundError: No module named 'godsv.system_one_guistate'

$ .venv/bin/python -m pytest tests/test_completion_precheck.py \
      tests/test_system_one_guistate.py -p no:randomly
E   ImportError: cannot import name 'guistate_action_candidates' from 'godsv.computer_use'
    (/home/willy/projects/gods-voice/src/godsv/computer_use.py)
!!! Interrupted: 2 errors during collection !!!
2 errors in 0.12s
```

Grün nach der Implementierung:

```bash
$ .venv/bin/python -m pytest tests/test_system_one_guistate.py -p no:randomly
18 passed in 0.04s

$ .venv/bin/python -m pytest tests/test_completion_precheck.py \
      tests/test_system_one_guistate.py -p no:randomly
34 passed in 0.10s

$ .venv/bin/python -m pytest tests/test_system_one_policy.py tests/test_system_one_flags.py \
      tests/test_system_one_contract.py tests/test_system_one_questions.py \
      tests/test_system_one_sanitizer.py tests/test_system_one_adapters.py \
      tests/test_evidence_binding.py tests/test_gate_hardening.py \
      tests/test_evidence_strictness.py -p no:randomly
151 passed in 1.37s

$ .venv/bin/python -m ruff check src/godsv/system_one_guistate.py src/godsv/computer_use.py \
      tests/test_system_one_guistate.py
All checks passed!

$ .venv/bin/python -m bandit -q -r src -ll
(Totals: Low 48, Medium 0, High 0; Exit 0)

$ .venv/bin/python -c "import godsv.computer_use, godsv.completion, godsv.system_one_guistate as g; print('import ok', g.GUISTATE_SCHEMA_VERSION)"
import ok guistate/v1
```

Evidenzklassen dieser Läufe:

* **contract** — Schema/Parser/Versionierung/Bindung (Regeln 1–4, 6, 7);
* **integration** — `test_binding_uses_a_real_dry_run_observation_and_a_real_policy`
  fährt die **echten** CU-Bausteine (`DryRunRunner.observe()` → `Observation` →
  `build_gui_state` → `system_one_guistate_gate` mit `CUPolicy.allowed_kinds`),
  aber ohne echten Desktop: das ist **kein** Nachweis echter Appwirkung;
* **mock** — nur die Evaluation im Bindungs-Test ist ein Test-Double
  (`build_evaluation` über den `Computer-Use`-Pack, Provider `disabled`,
  `live_jev=False`).

## Ehrlich: was der GUIState NICHT ist

* **kein Bildverständnis.** Der Parser liest strukturierten Wahrnehmungstext eines
  Kanals; ein Kanal ohne Aussage über ein Feld lässt es *unperceived* (§3: Jev
  ersetzt keine Screenshotwahrnehmung).
* **kein Zertifikat.** `certifies_effect` ist immer `False`; `effect_check`
  beobachtet nur und urteilt nicht („Ableitung", §52).
* **keine Aktion, keine Session.** Das Modul führt nichts aus und öffnet nichts;
  der Runner-Pfad (`CUExecutor`) bleibt der einzige Ort, der handelt.
* **kein Ersatz für den echten App-Zustand.** Fenstertitel/Anchors aus einem
  Textkanal sind eine Behauptung des Kanals, nicht der Desktop selbst.

## Nicht gefahren (not_run — mit exaktem Kommando)

| Was | Warum nicht | Exaktes Kommando / nächste Aktion |
|---|---|---|
| Ein GUIState aus einer **echten Host-App** (Xvfb-Container) | Auf diesem Host gibt es keine GUI-Session, und das Container-GUI-Image fehlt. Belegt: `godsvoice cu check` → `dry-run: verfügbar`, `sandbox (bwrap): verfügbar`, `container (podman): nicht verfügbar (Image fehlt: docker.io/library/alpine:latest)`, `container GUI (godsv-cu-x11): nicht verfügbar (Image fehlt: localhost/godsv-cu-x11:latest)` | `podman build -t localhost/godsv-cu-x11:latest <recipe>` (ein Recipe/`Dockerfile` liegt **nicht** im Repo — zuerst anlegen, Xvfb + xdotool + xmessage + Ziel-App), danach `godsvoice cu check` erneut, dann `godsvoice cu demo` und ein echter Schritt über `ContainerRunner(observe())` → `build_gui_state(..., channel="window_metadata", live_display=True)` |
| Ein echter **App-Effekt**-Nachweis (Post-Action) | setzt den GUI-Lauf oben voraus | nach dem GUI-Lauf: Aktion → `observe()` → `effect_check(state, expect_change="<erwartet>", post_text=observation.text)` und das Ergebnis in eine §55/§95-Abnahme hängen |
| GUIState-Fragen an **Jev** (§54 Pre/Post/Recovery) | kein autorisierter Jev-Zugang (JEV-07 `blocked_external_access`) | autorisierten Zugang bereitstellen, dann `compose_questions("computer_use")` gegen den State fahren; die Bindung dafür ist vorbereitet |
| Verdrahtung im `CUExecutor.run` | Scope (nur die genannten Dateien) und bewusst additiv: `system_one_action_gate` hat ebenfalls noch keine Aufrufstelle im Executor | `system_one_guistate_gate(...)` im Executor-Aufrufer ergänzen; der Rückgabewert `None`/`allowed_kinds`-unverändert lässt den Bestandsweg frei |
