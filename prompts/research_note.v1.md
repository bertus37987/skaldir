---
id: research_note
version: 1
purpose: "Curate a raw finding into one atomic research note with provenance."
model_tier: small
inputs: ["raw finding text", "source metadata: url, type, date, version scope"]
output: json {topic, claim, source_type, source_url, retrieved_at, valid_until, trust, repo_scope, notes}
---

# Rules
- One note = ONE atomic claim. If the finding contains several claims, record the most
  central one and list the others briefly under `notes`.
- Never speculate beyond the source. If the source is weak (forum post, undated, AI
  summary), lower `trust` (<= 0.4) and say why in `notes`.
- `valid_until`: set it for version-scoped claims (e.g. docs of library version X).
- `repo_scope`: the project/repo the note applies to, or `global`.

# Output (strict JSON, nothing around it)
{"topic": "…", "claim": "…", "source_type": "docs|paper|repo|blog|forum",
 "source_url": "…", "retrieved_at": "YYYY-MM-DD", "valid_until": null,
 "trust": 0.0, "repo_scope": "global", "notes": "…"}
