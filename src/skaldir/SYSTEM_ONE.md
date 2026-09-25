# System One (Jev) — foundation package

Implementation notes for the provider-neutral System One abstraction of
`ABNAHME.md` Teil VIII (§36–§49) and the JEV criteria (Teil XV §79–§98).

**Honest status: `JEV_READY` — never `JEV_LIVE_TESTED` / `JEV_ACTIVE_VERIFIED`.**
Jev is Early Access and there is no authorized TypeSafe credential in this
environment. The live adapter therefore reports `blocked_external_access`
(JEV-07 §85) instead of producing an answer. Every adapter result is labelled
`live_jev=False`; the one `live_jev=True` path in code is the TypeSafe adapter,
and it can only run with a resolved credential handle plus an injected
transport — which is exactly what is missing.

## Modules

| Module | Contract |
|---|---|
| `system_one.py` | §37/§38 — `SystemOneEvaluator` protocol, `SystemOneEvaluation` (+§49 evidence), `NoulAnswer`/`ChoiceAnswer`/`ScoreAnswer`, strict provider-payload validation, `DisabledSystemOneEvaluator`, `build_evaluation` |
| `system_one_questions.py` | §39/§47 — versioned registry (`voice_triage_v1`, `completion_v1`, `recovery_v1`, `computer_use_v1`, `capability_v1`), atomic questions, code composition (`compose_voice_triage`), `SYSTEM_ONE_QUESTIONS.json` manifest writer |
| `system_one_sanitizer.py` | §40 — Runtime Snapshot → Scope Filter → Secret Redaction → Project Isolation → State Minimizer |
| `system_one_adapters.py` | §41/§42 — `LLMSystemOneAdapter` (usable today), `TypeSafeJevEvaluator` (blocked without a credential) |
| `system_one_policy.py` | §43/§44/§45/§91/§92 — retry/deadline policy, risk classes R0–R4 + thresholds, staleness binding, `SystemOneGate`, `ShadowHarness`, completion precheck |
| `system_one_flags.py` | §98 — the six flags (config-file driven, safe defaults) and `build_evaluator` |

Hooks into the existing runtime (no second contract, §27):

* `voice.triage_with_system_one` — the only place Voice consults System One;
  System One never replaces the deterministic hint.
* `computer_use.system_one_action_gate` — advisory only; `allowed_kinds` and
  the real app effect keep the authority.
* `completion.completion_precheck` — advisory signals; `certifies=False`
  always, and the existing completion gate stays authoritative.
* `events.KINDS` — `system_one_evaluation|shadow|error|blocked|stale|decision`.
* `llm.LLMProviderError` — provider errors carry `status`/`sdk_retries` so the
  shared deadline policy can classify them instead of parsing message text.

## Flags (§98)

`<state dir>/settings.yaml`:

```yaml
system_one:
  enabled: false            # master switch
  provider: disabled        # disabled | llm_system_one_adapter | typesafe_jev
  shadow_mode: false
  voice_triage: false
  completion_precheck: false
  computer_use: false
```

Behavioural settings are never environment variables. With the flags off (the
default) every hook returns the existing path unchanged; an unknown provider or
a missing LLM client degrades to `DisabledSystemOneEvaluator`.

## Verification

```bash
.venv/bin/python -m pytest tests/test_system_one_contract.py tests/test_system_one_questions.py \
    tests/test_system_one_sanitizer.py tests/test_system_one_adapters.py \
    tests/test_system_one_policy.py tests/test_system_one_flags.py -p no:randomly
```

Rot-first: each test module's docstring records the failing run that was
captured before the module existed.

## Open / not implemented

* JEV-07 — real `jev-latest` call: `blocked_external_access` (no credential).
* JEV-08 — Treasury integration is handle-shaped only (`credential_resolver`);
  no real `Broker.run_with_secret` wiring yet.
* JEV-10 shadow rollout against real traffic, JEV-11 calibrated numbers from
  real dev data, JEV-12/13 productive question packs in the live path,
  JEV-15/16/17 GUIState + reactive checks, JEV-18 System-2 round trip,
  JEV-19 benchmark A/B/C/D: structure only or untouched.
