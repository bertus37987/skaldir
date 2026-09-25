# RENAME-1 — `godsv` / `godsvoice` → `skaldir-agent` / `skaldir`

The product is **Skaldir**. Its distribution is `skaldir-agent`, its import package is
`skaldir`, its console script is `skaldir`. Every old name still works and says so; nothing
that used to run stops running. This file is the reference for what changed, what must not
change yet, and how to move an existing installation.

## The mapping

| # | What | Old | New (primary) | The old name today |
|---|---|---|---|---|
| 1 | distribution | `godsv` | `skaldir-agent` | read as a version fallback only (`skaldir/__init__.py`, `perf_lazy.dist_candidates`) |
| 2 | import package | `godsv` | `skaldir` (`src/skaldir/`) | `src/godsv/` is an alias package: every `godsv.x` **is** `skaldir.x` (same module object), plus a `DeprecationWarning` on a TTY |
| 3 | console script | `godsvoice` | `skaldir` | installed as the *same* entry point (`skaldir.perf_lazy:main`); one notice line on stderr, TTY only |
| 4 | module entry point | `python -m godsv` | `python -m skaldir` | works (the alias hands the `__main__` override to the old name) |
| 5 | environment variables | `GODSV_*` | `SKALDIR_*` | read as a fallback everywhere, through `skaldir/env.py` |
| 6 | runtime directory | `~/.hermes/godsv` | `~/.hermes/skaldir` | used while it exists and `skaldir` does not (see below) |
| 7 | user-facing CLI text | `godsvoice …` | `skaldir …` | the strings in help/hints/next-actions now say `skaldir` |
| 8 | package doc text | "God's Voice" | "Skaldir" | prose only |

`python -m skaldir` is the module spelling; **`python -m skaldir.cli` is not an entry point**
(the module carries no `__main__` guard and prints nothing — by design, it is imported by
nearly every command).

## Environment variables

`SKALDIR_*` is read first, `GODSV_*` second — for every variable the product reads
(`SKALDIR_HOME`, `SKALDIR_PROMPTS`, `SKALDIR_FORMAT`, `SKALDIR_VAULT_KEY`, `SKALDIR_COLOR`,
`SKALDIR_WIDTH`, `SKALDIR_UNICODE`, `SKALDIR_CLI_ART`, `SKALDIR_LLM_*`, `SKALDIR_CHECK_*_CMD`,
`SKALDIR_API_TOKEN`, `SKALDIR_HOST_URL|TOKEN`, `SKALDIR_WARM_*`, …). One module owns the
fallback (`src/skaldir/env.py`), and no other module reads a prefixed variable directly —
`tests/test_rename_compat.py` fails if one starts to.

Two rules that are different on purpose:

* `getenv("HOME")` — the first name that is **present** wins, so an explicitly empty
  `SKALDIR_HOME=""` means "empty", not "fall back".
* `first_env(("SKALDIR_LLM_BASE_URL", "OPENAI_API_KEY"))` — the LLM layer's ordered list: the
  first **non-empty** value wins, each primary name followed by its legacy twin, and a
  third-party name (`OPENAI_API_KEY`) is left exactly as it was.

Variables this process *sets for a child* (`SKALDIR_CHECK_ID`, `SKALDIR_SECRET_*`, warm-session
handshake) are exported in **both** spellings: the child may have been generated, written or
configured before the rename.

## The runtime directory (migration)

`~/.hermes/skaldir` is the directory a new installation creates. An installation that already
has `~/.hermes/godsv` **keeps using it** — moving a live runtime means moving `tools.db`,
`runs.db`, `events.db`, `vault.json` and `config.yaml` at once, and the same directory is
shared with the desktop app. Nothing is copied or deleted silently.

```bash
skaldir doctor                  # prints the runtime dir it resolved and why
```

To move an existing installation (do this while nothing else runs):

```bash
systemctl --user stop hermes-agent   # or: stop the app / the CLI's warm session
mkdir -p ~/.hermes/skaldir
cp -a ~/.hermes/godsv/. ~/.hermes/skaldir/     # state + config + app build
mv ~/.hermes/godsv ~/.hermes/godsv.pre-rename  # keep it until the new one is proven
skaldir doctor                                 # expect: Runtime …/.hermes/skaldir
```

Setting `SKALDIR_HOME=/somewhere` (or the legacy `GODSV_HOME`) overrides the whole rule; the
reported reason names the variable that actually supplied the value.

## What must **not** change yet (and why)

These old names are contracts with something outside this repository. Renaming them is a
separate, coordinated change:

| Name | Held by | Why |
|---|---|---|
| plugin directory + name `godsv` (`integrations/hermes/godsv_plugin/`, `~/.hermes/plugins/godsv`) | the Hermes host | registered/enabled through the host's own config; the app resolves the panel by that name |
| docker label namespace `godsv.owner`, `godsv.capability`, `godsv.purpose`, `godsv.build_id` | live containers, sibling sweeps | teardowns are scoped by these labels |
| MCP server name `godsv-tools` | `~/.hermes/config.yaml` | renaming orphans the existing entry and adds a second one |
| config keys `godsv.llm`, `godsv.providers.entries.*` | existing runtime `config.yaml` | the provider store writes/reads exactly these |
| envelope field `godsvoice_version` | desktop app + every `--json` consumer | part of the machine-readable contract |
| OpenAPI extensions + header `x-godsv-*`, `x-godsv-token` | HTTP clients, the app | API contract |
| audit schema `godsv.security_audit/1`, pin schema `godsv.image-pin/1` | already-written artifacts | a persisted schema id |
| `~/.hermes/godsv/app`, `…/app-src`, `…/desktop-userdata` | the installed desktop app | deployed by the desktop wave; the app's own launcher reads them |
| Hermes skin id `gods-voice` | host config `display.skin` | activated through the host, not by us |
| workspace directory `.godsv/` (`checks.json`, `verify.json`, `pact.yaml`) | user workspaces | project scaffolding on disk |

## Checking it

```bash
skaldir --version                  # skaldir 1.5.0
skaldir contract dump --json       # leaf_commands 77, unregistered []
godsvoice --version                # same output; one notice line, only on a TTY
python -c "import godsv, skaldir; assert godsv is skaldir"
python -m pytest tests/test_rename_compat.py -q -o addopts=""
```

`tests/test_rename_compat.py` measures all four promises above (alias identity, script
equivalence, both env prefixes end to end, runtime-directory rule).
