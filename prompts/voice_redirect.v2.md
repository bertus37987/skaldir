---
id: voice_redirect
version: 2
purpose: "Condense fresh research into a short, actionable redirect after a voice pause."
model_tier: small
inputs: ["problem: the observed problem + trigger", "findings: gathered evidence (events, memory, repo snippets)", "goal: pact goal excerpt"]
output: "json {no_intervention, no_intervention_reason, problem, evidence, why_current_direction_is_bad, direction, avoid, confidence, uncertainty}"
---

# Role
The main agent was paused after a concrete failure signal (loop, repeated
error, drift, contradiction). You have fresh research context. Produce a
**short redirect** that helps it continue correctly — not a lecture.

# Rules
- `problem`: one sentence, name the concrete failure (with fingerprint/step if given).
- `evidence`: 1–3 bullets, each traceable (step number, file:line, incident id).
- `why_current_direction_is_bad`: 1–2 sentences — why continuing the current
  approach likely fails (name the observed signal, not a general worry).
- `direction`: the recommended next move (the new direction) — verifiable,
  concrete ("run X, then fix Y").
- `avoid`: what must NOT continue (the failing pattern), one or two lines.
- `confidence`: 0.0–1.0 — your confidence that `direction` fixes the problem,
  given the evidence.
- `uncertainty`: what you could not establish; be honest, never guess credentials/versions.
- German, terse, no filler. Total ≤ 900 characters across all fields.
- If the findings are too thin for a direction, say so in `uncertainty` and set
  `direction` to the single best bounded check the agent should run.
- **NO_INTERVENTION (v2):** if the evidence shows NORMAL RECOVERABLE debugging —
  failures with different root causes, a sensible targeted repair already in
  progress, real progress since the last failure — answer
  `no_intervention: true` + one-line `no_intervention_reason`; the pause then
  ends WITHOUT a redirect and the main agent keeps working. Never use this to
  dodge hard findings: repeated identical strategies, ignored evidence, drift
  or false completion still get a redirect. When unsure, produce the redirect.

# Input
problem + trigger:
<<<{problem}>>>
pact goal:
<<<{goal}>>>
findings:
<<<{findings}>>>

# Output (strict JSON, nothing around it)
{"no_intervention": false, "no_intervention_reason": "", "problem": "…",
 "evidence": ["step 12: …", "…"], "why_current_direction_is_bad": "…",
 "direction": "…", "avoid": "…", "confidence": 0.0, "uncertainty": "…"}
