# `godsv.jevcomp` — Jev context compaction (Python port)

**Ported from** [`tamaratran/fast-jev-compaction`](https://github.com/tamaratran/fast-jev-compaction)
— Claude Code plugin 0.3.0 / npm package 0.2.0, commit
`e3f262a7f4d42bd8dd32ced30d26176f7cb545b0`, **MIT License, Copyright (c) 2025**.
The full upstream license text is in [`UPSTREAM_LICENSE`](UPSTREAM_LICENSE); the ported
modules (`state.py`, `compact.py`, `request.py`, `types.py`) carry the notice in their header.
`THIRD_PARTY_NOTICES.md` in the repository root records the derivative work.

## What it does

Jev's context compaction (decision 6 of the Jev layer). Instead of rewriting old turns into a
lossy summary, every tool call outside a pinned window is judged on its own and then kept,
shortened or removed — the words of the transcript are never rewritten:

| Jev says | Result |
|---|---|
| `keepResult >= keep_threshold` (default 0.5) | call **and** result stay, byte-identical |
| else `keepCall >= keep_threshold` | call stays, result shortened to its first `truncate_head_chars` (300) characters plus a note |
| else | call **and** its result are removed together — never an orphan result |
| pinned (first message, newest `preserve_recent_messages` = 6) | never touched, whatever Jev answers |

User and assistant text is never removed or shortened, untouched messages come back as the very
objects they went in as, and nothing in the output is a summary.

## Usage

```python
from godsv.jevcomp import Message, ToolResult, ToolUse, compact

transcript = [
    Message(role="user", text="Never edit src/generated. Fix the failing test."),
    Message(role="assistant", tool_uses=[ToolUse("toolu_1", "Read", {"file_path": "src/a.ts"})]),
    Message(role="user", tool_results=[ToolResult("toolu_1", "…file…")]),
]

result = compact(transcript, asker, {"preserve_recent_messages": 4})
result.messages      # the compacted transcript (originals untouched objects)
result.decisions     # keep | drop_result | drop_call per call, with the probabilities
result.stats.to_wire()  # upstream stats object (camelCase), for CLI JSON output
result.reduction_ratio()  # < 0.25 -> "not worth it", keep the original transcript
```

The asker is injected (`JevAsker`, one `ask(state, questions)` call per batch):

* **production** — an adapter over Skaldir's own LLM provider. `build_jev_request` and
  `parse_jev_response` build/validate the TypeSafe wire shape so the adapter does not guess it.
  No HTTP client and no API key live in this package.
* **tests / shadow runs** — a fixture evaluator: `godsv.jevcomp.fixture.noul_asker`,
  `replay_asker`, `constant_asker`, `keep_calls_drop_results`. Zero network.
* **async provider** — implement `AsyncJevAsker` and call `compact_async` (`asyncio.gather`).

Batches run **concurrently by default** (upstream `Promise.all`); `max_concurrency=1` runs them
one after the other.

## Wiring our LLM provider

The wire shape is upstream's (TypeSafe System One); the helpers below build and validate it, so a
provider adapter is ~10 lines and needs no knowledge of the algorithm:

```python
from godsv.jevcomp import build_jev_request, compact, parse_jev_response


class ProviderAsker:
    """JevAsker over Skaldir's own provider (sync)."""

    def __init__(self, call_provider, api_key, model):  # call_provider(req) -> (status, ok, text)
        self.call_provider = call_provider
        self.api_key = api_key
        self.model = model

    def ask(self, state, questions):
        request = build_jev_request(state, questions, api_key=self.api_key, model=self.model)
        status, ok, text = self.call_provider(request)  # request.body is the JSON payload
        return parse_jev_response(status, ok, text)     # raises on anything unusable


result = compact(messages, ProviderAsker(call_provider, key, model), {"preserve_recent_messages": 6})
if result.reduction_ratio() < 0.25:      # the plugin's "not worth it" guard
    ...                                  # keep the original transcript / fall back to a summary
```

`parse_jev_response` raises `JevRequestError` for a non-200 status, malformed JSON or a body
without `answers`; a missing/non-finite `noul` raises `JevAnswerError`; either one propagates out
of `compact`, so the caller's fallback path stays in charge.

## Failure model (the caller falls back)

Compaction **raises** instead of guessing, exactly as upstream:

| Error | Cause |
|---|---|
| `JevRequestError` | provider/HTTP failure or a response without usable `answers` |
| `JevAnswerError` | a missing, non-`noul` or non-finite answer |
| `StateFittingError` | the history cannot be fitted into `max_state_tokens`, or a batch leaves no room between the state and `max_request_tokens` |

All are subclasses of `JevCompactionError` (itself a `RuntimeError`) and carry the upstream
message text, so a caller can either match the class or the string.

## Pipelines

```
messages
  -> collect_tool_calls(messages, preserve_recent_messages)   # pair by tool_use_id, pin first + newest N
  -> fit_state(messages, calls, options)                      # staged shrink -> CompactionState (<= max_state_tokens)
  -> batch_calls(candidates, state.tokens, options)           # <= max_request_tokens per request
  -> asker.ask(state.to_wire(), questions)  (per batch)       # two noul questions per call
  -> decide_call(call, answer, keep_threshold)                # keep | drop_result | drop_call
  -> apply_decisions(messages, decisions, calls, truncate_head_chars)
```

Fitting stages, in this order, each applied only if the previous was not enough: tool inputs
truncated to 1000 → 200 → 60 characters; long texts abridged to head 400 + tail 150
oldest-non-pinned-first; old non-pinned messages collapsed to `[… N chars omitted …]`; old tool
calls reduced to one line each; old call-less messages left out; runs of old call-only messages
folded into one entry. If it still does not fit: raise.

## Testing

* `tests/test_jevcomp_state.py` — token calibration, truncation, pinning, pairing, the fitting
  stages and their order, the throw.
* `tests/test_jevcomp_compact.py` — options, questions, batching limit, all three decision
  branches, the rebuild, the verbatim guarantee, the failure/fallback path, concurrency, async.
* `tests/test_jevcomp_oracle.py` — **conformance against the original TypeScript** through
  `node`: the same fixture must produce the same tokens, pins, stage, state JSON, batches,
  decisions, stats, rebuilt messages and error messages. Skips when `node` or a checkout is
  missing (the product needs neither):

  ```sh
  git clone --depth 1 https://github.com/tamaratran/fast-jev-compaction /tmp/fast-jev-compaction
  pytest tests/test_jevcomp_oracle.py
  ```

  Override the location with `GODSV_JEVC_OMPACTION_UPSTREAM=<path>`.

## Deviations from upstream

The complete list lives in the package docstring (`godsv/jevcomp/__init__.py`); the short form:
async → sync plus an `asyncio` twin, no HTTP client / no `compactMessages` / no API key, typed
errors with identical messages, dataclasses instead of interfaces with a tolerant message reader,
upstream wire keys kept in `to_wire()`, lengths counted in Python code points (identical except
for astral characters), `json.dumps` configured to match `JSON.stringify`, lenient option
resolution, and the port-only extras (`max_concurrency`, the fixture askers,
`CompactResult.reduction_ratio()`).
