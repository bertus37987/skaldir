---
id: incident_extract
version: 1
purpose: "Extract a durable incident record from a finished episode (only if solved & verified)."
model_tier: mid
inputs: ["episode summary", "resolution and verification evidence"]
output: json {title, kind, root_cause, resolution_steps, verification, confidence}
---

# Rules
- Produce a record only when the fix was verified (a green test or an observed run).
  If unverified: set `confidence` <= 0.3 and prefix the title with `[unverified]`.
- `root_cause`: the mechanism, not the symptom. `resolution_steps`: reproducible actions.
- Never include secrets, tokens, credentials, or personal data.

# Output (strict JSON, nothing around it)
{"title": "…", "kind": "error|failtest|loop|other", "root_cause": "…",
 "resolution_steps": ["…"], "verification": "…", "confidence": 0.0}
