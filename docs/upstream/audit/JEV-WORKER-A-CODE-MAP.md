# JEV Worker A — Code Map (Runtime-Call → Evidence → UI-Wirkung)

Nur der Worker-A-Anteil (Voice + Completion). Der Provider (TypeSafe/Treasury) und
GUIState/Computer-Use gehören zu Worker B; der Integrator führt die Karte zusammen.

## Voice-Pfad

```text
Runtime-Ereignis (Signal/Trajectory)
  → Voice.consider()                          src/godsv/voice.py:424   (deterministisch, unverändert)
  → SystemOneService.voice_triage()           src/godsv/system_one_service.py:266
      → StateSanitizer.sanitize()             src/godsv/system_one_sanitizer.py:221
          (§40 Scopefilter → Secret-Redaktion → Projekt-Isolation → Minimierung)
          (§7 Stempel: workspace_fingerprint, source_event_refs, created_at)
      → Cache-Key (7+ Felder)                 src/godsv/system_one_cache.py:CacheKey
      → SystemOneGate.advise()                src/godsv/system_one_policy.py:SystemOneGate
      → SystemOneEvaluator.evaluate()         Provider-Kante (einzige)
      → build_evaluation()                    src/godsv/system_one.py:build_evaluation
          (§4.2 provider_mode abgeleitet + geprüft)
  → propose_route()                           src/godsv/system_one_policy.py (Regelleiter)
  → advisory_from_evaluation()                src/godsv/voice.py  (route_proposal/rule/basis/via)
  → TriageOutcome (path advisory|shadow|legacy)
  → Events: system_one.requested/completed/failed/discarded_stale/escalated
  → Metrics: state_build_ms, sanitization_ms, provider_latency_ms, decision_policy_ms,
             total_system_one_ms, input_tokens/output_tokens/cost (nur wenn berichtet),
             system2_calls_saved
  → Evidence: evaluation.evidence() (provider_mode, live_jev, state_fingerprint, …)
  → UI-Wirkung: deterministischer Hint bleibt; Advisory nur als Aufzeichnung
```

## Completion-Pfad (§9.1)

```text
Completion-Claim (CompletionRequest)
  → CompletionLedger                          src/godsv/completion.py:1453
  → hard_checks()  (9 Checks, deterministisch, VOR jedem Jev-Call)
                                              src/godsv/completion.py:1532
  → detect_shortcuts()  (§9.5, 8 Codes)       src/godsv/completion.py:1648
  → SystemOneService.evaluate_snapshot()      src/godsv/system_one_service.py:233
        Fragepaket completion_preflight (8 Noul + evidence_fit)
  → _verdict_for()  (Hart schlägt Modell)     src/godsv/completion.py
  → aggregate_criteria()  (Eltern in Code)    src/godsv/completion.py:_verdicts
  → build_ledger_summary() (9 Zustände)       src/godsv/completion.py
  → Fragepaket completion_sanity (7 Fragen)   nur wenn Blätter passen
  → CompletionPreflight (certifies=False, certifies_completion() → immer False)
  → Metrics: completion_criteria_checked/flagged/escalated, total_completion_latency
  → GateRunner.verify(precheck=…)             src/godsv/completion.py:GateRunner
      → gate_precheck()  (precheck-only ⇒ BLOCKER completion-precheck-only-source)
      → gate_evidence/tests/build/integration/fake_completion/security/cleanup/dod
      → VERIFIED_COMPLETE nur bei runner-belegter, gebundener Evidenz
  → CompletionStore.transition()  (einzige Autorität, die Completion setzt)
```

## Unveränderlich (nicht von System One)

* Hard Policy, Rechte, Goal-Revision, Treasury, Runtime-Ownership.
* Completion setzt ausschließlich `GateRunner` + `CompletionStore`.
* `propose_route()` liefert einen **Vorschlag**; `ProposedRoute` trägt keinen Handgriff.
* Shadow-Records: `mutations=0`, `route=None`.
