# long_project — fixture for the long-project E2E (`tools/gv_long_project_e2e.py`)

A small but *real* Python project: a library (`textkit`), a CLI, a test suite, a declared
CLI check and a declared packaging check. `textkit.slugify` is deliberately wrong at the
start, so every one of the three criteria is genuinely red.

```
base/            the project as the agent finds it (no .godsv/ declarations)
achievable/      overlay: 3 criteria, all three reachable  (checks.json + verify.json)
impossible/      overlay: like achievable, but criterion 3 needs an upstream release
                 manifest that does not exist here  ->  can never be met
```

`base/` + one overlay is copied into a scratch dir by the runner (never edited in place).

The two declared checks obey the product's result contract: exit 0 **and** an explicit
`PASS` line, else `FAIL: <reason>` + exit 1.
