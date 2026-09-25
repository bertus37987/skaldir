---
name: repo-audit
description: "Walk an unknown repository and produce an evidence-backed inventory of features, entrypoints and dead code. Use before changing an unfamiliar codebase."
version: 0.1.0
---

# Repo audit

## Steps

1. Read `README.md`, `pyproject.toml`/`package.json`: record the product name, entry points and claimed features.
2. List source packages (`ls src/`, `ls tests/`) and count modules per package; note the largest and smallest.
3. For each claimed feature, `grep -rn "<feature keyword>" src/` and record file:line proof or mark MISSING.
4. Run the test suite once (serially if shared resources) and capture pass/fail counts.
5. Candidates for dead code: modules with zero inbound imports — for each module run `grep -rn "import <module>" src tests` and only report it when nothing imports it.
6. Write the inventory table (feature / status / file:line) and a dead-code section, each row backed by evidence you actually read.

## Acceptance criteria

- Every feature row cites a file:line or says MISSING.
- No dead-code claim without a grep proving zero importers.
- Test-run output quoted verbatim (counts, not adjectives).
