---
name: explain-code
description: "Explain a module or file: its public surface, control flow, dependencies and who calls it — from actual code reads, not guesses. Use before refactoring."
version: 0.1.0
---

# Explain code

## Steps

1. Read the target file fully (not the first 30 lines).
2. Enumerate the public surface: exported names, dataclasses, entry functions.
3. Trace dependencies: imports → do those modules import the target back (cycles)?
4. Find callers: `grep -rn "from <module> import\|import <module>" src tests` and list them with file:line.
5. Produce: purpose (one sentence), public surface list, control flow of the main path, callers list, and two risks if this file changed.

## Acceptance criteria

- Every claim carries file:line.
- Caller list comes from a grep, not memory.
- Explanation fits in one screen without marketing adjectives.
