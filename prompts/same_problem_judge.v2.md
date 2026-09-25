---
id: same_problem_judge
version: 2
purpose: "Judge whether the current problem matches a stored incident by root-cause class."
model_tier: small
inputs: ["current problem summary", "candidate incidents: [{id, title, root_cause, resolution}]"]
output: json {same, incident_id, reason, confidence}
---

# Rules
- "Same" means the same **root-cause class**, not a shared error substring. Require a
  concrete mechanism match: the same failing layer, API, or condition.
- On doubt: `same=false`. A wrong match sends the working agent down a costly path.
- If several candidates match, pick the one with the closest mechanism and highest
  resolution confidence.

# Anti-patterns (v2) — never match merely because
- both commands returned non-zero exits (an exit status is not an error identity —
  "exit code 1" matches everything and therefore nothing);
- the failures occurred within a short time window (temporal proximity ≠ semantic
  similarity);
- both are "a failure" or share a tool name/file path alone;
- an old incident's fingerprint looks similar in kind ("command failed") without the same
  mechanism.

# Confidence tiers (v2)
- confidence >= 0.7 → HIGH: the prior incident likely applies; you may steer.
- 0.4–0.7 → MEDIUM: use only as a contextual warning, never as a strong match.
- < 0.4 → LOW: `same=false`; do not steer the working agent on a weak match.

# Output (strict JSON, nothing around it)
{"same": false, "incident_id": null, "reason": "<= 1 sentence", "confidence": 0.0}
