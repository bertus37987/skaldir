---
name: write-test
description: "Write one focused failing test for a specific behavior, then make it pass without weakening existing tests. Use when adding or fixing behavior."
version: 0.1.0
---

# Write test

## Steps

1. State the behavior in one sentence including expected input and output.
2. Reproduce the current behavior with a quick REPL/script run or an existing test.
3. Write ONE new test asserting the expected behavior; run it and confirm it fails for the right reason (assert, not import error).
4. Make the smallest change that turns it green; rerun the new test and the nearest existing test file.
5. Diff review: confirm no existing test was edited to weaker expectations.

## Acceptance criteria

- New test name states the behavior (test_<behavior>).
- Red→green observed, command line and both outputs quoted.
- `git diff` shows no weakened assertions in pre-existing tests.
