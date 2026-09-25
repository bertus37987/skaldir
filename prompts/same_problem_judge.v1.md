---
id: same_problem_judge
version: 1
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

# Output (strict JSON, nothing around it)
{"same": false, "incident_id": null, "reason": "<= 1 sentence", "confidence": 0.0}
