# COMPLETION_PRECHECK.md — JEV-13 Completion-Precheck (§91, ABNAHME Teil XV)

Stand: 16.09.2026, Branch `godsv-supervisor-hardening`, Arbeitsbaum (nicht committet);
Basis-HEAD `27913dc`. Betroffene Dateien: `src/godsv/completion.py` (additiv),
`tests/test_completion_precheck.py` (neu).

Vertragstext, wörtlich (ABNAHME Teil XV §91):

> **JEV-13 — Completion-Precheck**
> Jev kann fehlende/stale Completion-Evidence signalisieren, aber niemals allein
> Completion zertifizieren.

## Was jetzt existiert (real, mit Datei:Zeile)

| Artefakt | Ort (Arbeitsbaum) | Was es ist |
|---|---|---|
| `CompletionPrecheck` | `src/godsv/completion.py` (Frozen-Dataclass) | Advisory-Lesung: `signals`, `reasons` (maschinenlesbar), `missing`, `stale`, `certifies=False`, `fingerprint` |
| `completion_precheck(...)` | `src/godsv/completion.py` | bindungsbewusste Lesung einer Evidence-Liste gegen Revision/Workspace/Session; optionale System-One-Evaluation (§92-stale → inert) |
| `PRECHECK_MISSING_EVIDENCE` / `PRECHECK_STALE_EVIDENCE` / `PRECHECK_ONLY_SOURCE` / `PRECHECK_CERTIFIES` | `src/godsv/completion.py` | die maschinenlesbaren Reason-Codes |
| `Finding.code`, `VerificationResult.refusal_codes` | `src/godsv/completion.py` | Reason-Code am Finding und am Verdikt (beides additiv, Default leer) |
| `GateRunner.gate_precheck(...)` | `src/godsv/completion.py` | die Autoritäts-Regel: Precheck signalisiert, Gate zertifiziert |
| `GateRunner.verify(..., precheck=None)` | `src/godsv/completion.py` | der einzige neue Parameter des Bestandsgates (keyword-only, Default `None`) |
| `CompletionStore.record_precheck(...)` | `src/godsv/completion.py` | Audit-Trail: jede Lesung landet als `{"kind": "precheck", "certifies": false}` in der Timeline |

Der **Bestandspfad ist unverändert**: ohne `precheck=` gibt es keinen neuen
Blocker, keine neuen Codes, kein neues Verhalten (Pin-Test:
`test_existing_gate_behaviour_is_unchanged_without_a_precheck`).

## Die Regeln, die der Code erzwingt

1. **Signal, kein Zertifikat.** `CompletionPrecheck.certifies` ist `False`, und
   `certifies_completion()` gibt **immer** `False` zurück — auch bei einer
   gefälschten Instanz mit `certifies=True`.
2. **Nur das Gate zertifiziert.** Zertifizieren kann ausschließlich
   runner-ausgeführte, an Revision/Workspace/Session gebundene Evidenz
   (`source == "runner"`, `passed`, `_evidence_bound`).
3. **Precheck als einzige Quelle ⇒ Ablehnung.** Liegt nur ein Precheck vor (kein
   gebundener, bestandener Runner-Nachweis), ist das Verdikt `INCOMPLETE` mit
   `refusal_codes == ["completion-precheck-only-source"]` und einem BLOCKER-Finding.
4. **Ein Precheck, der zertifizieren will, wird abgelehnt** —
   `refusal_codes == ["completion-precheck-certifies"]`; das Gate glaubt dem
   Objekt nicht, es prüft es.
5. **Stale ≠ missing.** Evidenz aus fremder Revision/Workspace/Session wird als
   *stale* signalisiert (Code `completion-precheck-stale-evidence`), nie als
   Nachweis benutzt; eine angekündigte, aber nicht lauffähige Prüfung
   (`status == "not-certifiable"`) wird als solche signalisiert und nie grün.
6. **Kein Vertrauen in Modellmeinungen.** Signale aus einer System-One-Antwort
   sind sprachlich bedingt (`completion_evidence_may_be_missing` /
   `..._may_be_stale`) und stehen nur, wenn die Antwort nicht stale ist
   (§92/JEV-14); sie erzeugen keinen Reason-Code und sind nie allein blockend.
7. **§98-Abschaltung.** Sind die Flags übergeben und
   `system_one.completion_precheck` (oder `enabled`) aus, gibt
   `completion_precheck(...)` `None` zurück — der bestehende Pfad läuft
   unverändert.

## Beweise (gefahren, mit beobachteter Ausgabe)

Rot zuerst, vor der Implementierung:

```bash
$ .venv/bin/python -m pytest tests/test_completion_precheck.py \
      tests/test_system_one_guistate.py -p no:randomly
E   ImportError: cannot import name 'PRECHECK_CERTIFIES' from 'godsv.completion'
    (/home/willy/projects/gods-voice/src/godsv/completion.py)
E   ImportError: cannot import name 'guistate_action_candidates' from 'godsv.computer_use'
    (/home/willy/projects/gods-voice/src/godsv/computer_use.py)
!!! Interrupted: 2 errors during collection !!!
2 errors in 0.12s

$ .venv/bin/python -c "from godsv.completion import completion_precheck"
ImportError: cannot import name 'completion_precheck' from 'godsv.completion'

$ .venv/bin/python -c "GateRunner(repo_root='.').verify(r, [], precheck=object())"
TypeError: GateRunner.verify() got an unexpected keyword argument 'precheck'
```

Grün nach der Implementierung:

```bash
$ .venv/bin/python -m pytest tests/test_completion_precheck.py \
      tests/test_system_one_guistate.py -p no:randomly
34 passed in 0.10s

$ .venv/bin/python -m pytest tests/test_completion.py \
      tests/test_completion_loop_e2e.py tests/test_computer_use.py \
      tests/test_completion_precheck.py tests/test_system_one_guistate.py -p no:randomly
101 passed, 4 skipped in 0.90s

$ .venv/bin/python -m ruff check src/godsv/completion.py \
      tests/test_completion_precheck.py
All checks passed!

$ .venv/bin/python -m bandit -q -r src -ll
(Totals: Low 48, Medium 0, High 0; Exit 0)
```

16 Tests in `tests/test_completion_precheck.py` decken die Fälle 1–7 oben ab
(u. a. `test_precheck_as_the_only_source_refuses_completion_with_a_reason`,
`test_a_forged_certifying_precheck_is_refused`,
`test_precheck_does_not_block_a_genuinely_verified_completion`,
`test_precheck_flag_off_keeps_the_existing_completion_path`,
`test_a_stale_evaluation_stays_inert_while_the_structural_reading_stands`).

## Evidenzklasse

**contract** — die Aussage „signalisiert, zertifiziert nie" ist eine
Schema-/Zustandsmaschinen-Aussage und wird auf echten `GateRunner.verify`-Läufen
gegen ein echtes Git-Arbeitsbaum-Fixture und einen echten `CompletionStore`
(Session-Scope, Timeline) bewiesen. Kein Modell, kein Mock des Geprüften ist an
der Kernaussage beteiligt.

Was **nicht** reicht und deshalb hier nicht als Beweis steht (§5): Ein einzelner
grüner Unit-Test ist kein Nachweis für einen **live** Jev-Precheck. Die
Precheck-Signale aus einer echten Jev-Antwort sind in dieser Umgebung nicht
gefahren (kein autorisierter Jev-Zugang, JEV-07 bleibt
`blocked_external_access`); der LLM-/TypeSafe-Pfad ist hier nur mit
Test-Doubles belegt.

## Nicht gefahren (ehrlich, mit kleinster nächster Aktion)

| Was | Warum nicht | Kleinste nächste Aktion |
|---|---|---|
| Precheck-Signale aus einer **echten Jev-Antwort** (`live_jev=True`) | kein autorisierter Jev-Zugang auf diesem Host; JEV-07 ist `blocked_external_access` (siehe `system_one_adapters`) | autorisierten Zugang bereitstellen, dann `completion_precheck(..., evaluation=<Live-Evaluation>, live=<VersionStamp>)` im Shadow-Modus fahren und `reasons`/`signals` protokollieren |
| Verdrahtung in den Loop-Hook / das Panel | Scope dieses Auftrags (nur die genannten Dateien); kein bestehender Aufruf von `system_one_policy.completion_precheck` existiert | Aufrufstelle im Hook ergänzen: `precheck=completion_precheck(request=..., evidence=..., revision=..., flags=flags)` — der Rückgabewert `None` hält den Bestandsweg automatisch frei |
| Kalibrierung der Precheck-Signale gegen Holdout (§89) | ohne Live-Jev keine Datenbasis | Teil von JEV-11, nicht dieses Kriteriums |
