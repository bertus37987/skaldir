# SNIPPET — SUBAGENT-ROLES + EVIDENCE-BRIEF for the surface owner (`cli.py` / `verify_cli.py` / `subagents_cli.py`)

Owner block: **SUBAGENT-ROLES + EVIDENCE-BRIEF** (`MVP/93-subagent-roles.md`).
This file is a **hook + snippet**, not a patch: the surface blocks own `cli.py`, `repl.py`, `chatbar.py`,
`picker.py` and the CLI wiring, so the three call sites below are written out here instead of edited in
place. Everything they call already exists and is tested.

The three hooks, in priority order:

## 1. `agent spawn --role <role>` — the ceiling becomes reachable from the CLI

`src/skaldir/subagents_cli.py` already has `--max-depth`; `role` is the sibling argument. Add to the
`spawn` parser (next to line 872, `spawn.add_argument("--max-depth", …)`):

```python
spawn.add_argument(
    "--role", choices=list(S.ROLES), default="",
    help="tool ceiling for every child: explore (read/research only) | worker "
         "(edit+test, may not spawn) | verifier (read-only + declared check) | main",
)
```

and pass it at the two `S.spawn(...)` call sites (lines 354 and 475 region):

```python
    record = S.spawn(
        tasks,
        provider=provider,
        state_dir=state_dir,
        run_id=args.run_id,
        max_depth=int(args.max_depth),
        role=args.role,              # ← the hook
        …
    )
```

For the brief-driven path (`briefs_from_tasks`) add `role=args.role` the same way. The `agent retry`
parser should carry `--role` too (`retry_briefs` builds fresh briefs; the role is the retry's own).

What a caller may then show, all of it already in the run record:

```python
    run = S.SubagentRun(...)          # or S.spawn(...)
    record = run.run()
    print(json.dumps(run.role_report(), indent=2))
    #   {"c1": {"role": "explore",
    #           "tools_offered": ["git_read", "glob", "grep", "read", "web_fetch", "web_search"],
    #           "may_spawn": false,
    #           "spawn_ceiling": {"depth": "allows", "role": "blocks"},
    #           "refusals": [{"tool": "spawn", "code": "role_ceiling.delegation_forbidden", …}]}}
```

## 2. `verify run --claim "<the completion claim>"` — deterministic VERIFY → Jev → promotion

`src/skaldir/verify_cli.py:334` does `run = V.run_plan(...)`. The gate goes **immediately after it**,
and it is additive: it never changes `run["verdict"]`.

```python
from .subagent_roles import RoleToolDispatcher

run = V.run_plan(plan, timeout=args.timeout, session=args.session, state_dir=state_dir)

gate = {}
if args.claim:
    gate = V.semantic_gate(
        run,
        claim=args.claim,
        dispatcher=RoleToolDispatcher("verifier"),   # read-only by construction
        # evaluator=/thresholds= come from the armed Jev binding when one exists;
        # without a provider the gate honestly reports "no_advisory"
    )
    run["semantic_gate"] = gate          # or pass a side table to the renderer
```

with

```python
run.add_argument("--claim", default="",
                 help="the completion claim to read against the evidence brief "
                      "(no claim ⇒ the gate reports 'unread', never 'supported')")
```

and one line in the text renderer:

```python
    if gate:
        print(f"semantic gate: {gate['state']} · promotion {gate['promotion']} · "
              f"{gate['promotion_reason']}")
```

How the completion path must read it (this is the contract, not a suggestion):

| `gate["state"]` | `gate["promotion"]` | what may happen next |
|---|---|---|
| `supported` | `allowed` | the deterministic `PASS` may be promoted to a completion |
| `refused` | `blocked` | nothing is completed: print `gate["reasons"]` and `gate["evidence_gaps"]` |
| `underspecified` | `blocked` | same, with the gaps — the claim or the evidence has to change |
| `no_advisory` | `unread` | the deterministic verdict stands (never assume agreement) |
| `skipped` | `skipped` | not a `PASS`: the gate was not consulted at all |

## 3. `roles` — the ceiling as a readable self-audit

`ceiling_report()` is computed from the same policy table the dispatcher enforces, so it cannot drift:

```python
from .subagent_roles import ceiling_report
print(json.dumps(ceiling_report(), indent=2))
# {"schema": "skaldir.subagent_roles.v1", "roles": [{"role": "explore", "tools": […],
#  "may_spawn": false, "forbidden_tools": ["edit", "exec", "spawn", …], "budget": {…}}, …]}
```

Suggested placement: a `skaldir agent roles --json` leaf (the `agent` group already exists), or a
section of `skaldir verify plan --json`. Either way it is one call.

## What is *not* in this snippet

* `cli_contract.py` entries — a new leaf needs its `COMMANDS` row; that file belongs to the CLI owner.
* anything in `repl.py` / `chatbar.py` — a `/spawn --role explore` line reaches the same `S.spawn`
  once hook 1 lands, because the palette runs the line as `skaldir <line>`.
* the Jev provider binding for the gate (`armed_service`) — that belongs to the ROUTE wiring owner; the
  gate works without it and says so (`no_advisory`).
