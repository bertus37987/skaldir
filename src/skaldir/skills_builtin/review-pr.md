---
name: review-pr
description: "Review a pull request or diff: correctness, tests, scope creep, and security-relevant changes. Produces a verdict with evidence."
version: 0.1.0
---

# Review PR

## Steps

1. Read the full diff (`git diff main...HEAD` or the PR view); note every file touched.
2. For each changed function: does its behavior match its name and docstring? Read the surrounding code, not just the hunks.
3. Tests: is every behavior change covered by a new/updated test? Which existing test should have failed but didn't?
4. Scope: list anything unrelated to the PR's stated purpose.
5. Security-sensitive touches (auth, subprocess, file paths, secrets, network): inspect each call site individually.
6. Verdict: approve / request changes, with numbered findings each citing file:line.

## Acceptance criteria

- No finding without a file:line reference.
- Test-coverage claim verified by naming the test.
- Verdict follows the findings; findings are reproducible statements.
