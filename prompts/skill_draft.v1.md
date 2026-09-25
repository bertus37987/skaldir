---
id: skill_draft
version: 1
purpose: "Draft a SKILL.md from repeatedly observed practice (>= 3 occurrences, >= 2 sessions)."
model_tier: mid
inputs: ["pattern evidence: occurrences with session refs", "observed pitfalls"]
output: markdown (complete SKILL.md draft)
---

# Rules
- Derive every step from the provided evidence; invent nothing (no tools, paths, or
  commands that were not actually used).
- Include the pitfalls that really occurred, with the failure each one caused.
- Format (agentskills.io compatible):

```
---
name: lowercase-with-dashes
description: "<= 60 chars, self-contained trigger"
version: 1.0.0
---

# <Title>
## When to use
## Procedure
## Pitfalls
## Verification
```

- Maximum 80 lines. English. Do not duplicate an existing skill name (a list is provided).

# Output: the complete SKILL.md text only — no commentary, no code fences around it.
