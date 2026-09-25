---
id: slop_check
version: 1
purpose: "Checkpoint review of recent changes: slop, scope drift, missing verification."
model_tier: mid
inputs: ["pact: goal, non_goals, acceptance items", "recent diff summary", "test status"]
output: json {findings, verification_gap}
---

# Finding kinds (use exactly these)
`placeholder`, `swallowed_error`, `test_theater`, `scope_drift`, `invented_api`,
`hardcoded_secret`, `dead_flag`, `defensive_noise`, `verification_gap`

# Rules
- Judge only the provided diff; ignore pre-existing code.
- Severity 1–3 (3 = blocks completion). Message: what + why + one-line suggested fix.
- `verification_gap`: acceptance criteria that are claimed done without observed evidence.
- No style nits beyond the listed kinds; no speculation about code outside the diff.

# Output (strict JSON, nothing around it)
{"findings": [{"kind": "placeholder", "severity": 2, "file": "src/x.py",
  "message": "…"}], "verification_gap": ["…"]}
