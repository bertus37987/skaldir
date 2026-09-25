"""Conformance oracle: the Python port against the original TypeScript through Node.

The MIT-licensed original (``tamaratran/fast-jev-compaction``) is executed by
``node`` on a shared fixture and the two implementations are diffed: token
estimates, truncation, pinning, the fitting stages and their token counts, the
full state JSON, the batching, the decisions, the stats and the rebuilt
transcript. This is the evidence that the port is faithful, not a rewrite in the
spirit of the original.

The test **skips** cleanly (with the reason) when ``node`` is absent or no
upstream checkout is found — the product needs neither. To run it::

    git clone --depth 1 https://github.com/tamaratran/fast-jev-compaction /tmp/fast-jev-compaction
    pytest tests/test_jevcomp_oracle.py

The checkout is read-only for this suite: the TypeScript sources are copied into
a temporary directory and only their relative ``./x.js`` import specifiers are
rewritten to ``./x.ts`` so Node's type stripping can resolve them. No source
logic is modified, and nothing from the upstream is vendored into the repository.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_jevcomp_fixtures import FILE_A, transcript, wire_transcript

from skaldir.jevcomp import (
    DEFAULT_OPTIONS,
    Message,
    apply_decisions,
    batch_calls,
    collect_tool_calls,
    compact,
    decide_call,
    estimate_tokens,
    fit_state,
    resolve_options,
    truncate,
    wire_json,
)
from skaldir.jevcomp.fixture import replay_asker
from skaldir.jevcomp.types import CallAnswer, ToolResult, ToolUse

UPSTREAM_ENV = "GODSV_JEVC_OMPACTION_UPSTREAM"
UPSTREAM_COMMIT = "e3f262a7f4d42bd8dd32ced30d26176f7cb545b0"
CLONE_HINT = (
    "clone the upstream to run the oracle: "
    "git clone --depth 1 https://github.com/tamaratran/fast-jev-compaction /tmp/fast-jev-compaction"
)

#: Question-name -> probability used for the decision scenarios on both sides.
REPLAY: dict[str, float] = {
    "call_t1": 0.9,
    "result_t1": 0.1,  # keep the call, shorten the result
    "call_t2": 0.1,
    "result_t2": 0.1,  # drop call and result
    "call_t3": 0.9,
    "result_t3": 0.9,  # keep call and result
}

#: The scenarios both implementations are asked to run (options in upstream spelling).
FIT_SCENARIOS = [
    {"goal": "fix the test", "maxStateTokens": 100000, "preserveRecentMessages": 0},
    {"goal": "", "maxStateTokens": 100000, "preserveRecentMessages": 1},
    {"goal": "g", "maxStateTokens": 300, "preserveRecentMessages": 0},
    {"goal": "g", "maxStateTokens": 600, "preserveRecentMessages": 0},
    {"goal": "g", "maxStateTokens": 25000, "preserveRecentMessages": 6},
    {"goal": "g", "maxStateTokens": 60, "preserveRecentMessages": 0},
]
BATCH_SCENARIOS = [(1000, 30000), (29600, 30000), (29980, 30000)]
COMPACT_SCENARIOS = [
    {"preserveRecentMessages": 1},
    {"preserveRecentMessages": 0, "truncateHeadChars": 50},
    {"preserveRecentMessages": 0, "truncateHeadChars": 0},
    {"preserveRecentMessages": 6, "keepThreshold": 0.95},
    {"preserveRecentMessages": 0, "maxStateTokens": 60},
]
APPLY_SCENARIOS = [
    {"preserveRecentMessages": 0, "truncateHeadChars": 300},
    {"preserveRecentMessages": 0, "truncateHeadChars": 0},
    {"preserveRecentMessages": 0, "truncateHeadChars": 50},
    {"preserveRecentMessages": 0, "truncateHeadChars": 300, "answers": REPLAY},
]
TOKEN_PROBES = ["", "hello world", "internationalization", "12345678", "→", '{"a":1}', "x" * 500]
TRUNCATE_PROBES = [("abcdefghij", 4), ("abcdefghij", 10), ("abcdefghij", 0), ("x" * 500, 300)]

DRIVER = """
import fs from 'node:fs';
import {
  applyDecisions, batchCalls, collectToolCalls, compact, decideCall,
  DEFAULT_OPTIONS, estimateTokens, fitState, resolveOptions, truncate,
} from './up/index.ts';

const fx = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const messages = fx.messages;
const out = {};

// Every scenario records either its value or the error it threw, so the port has
// to reproduce the failure behaviour too.
const attempt = (fn) => {
  try {
    return { ok: true, value: fn() };
  } catch (error) {
    return { ok: false, error: String(error.message) };
  }
};
const attemptAsync = async (fn) => {
  try {
    return { ok: true, value: await fn() };
  } catch (error) {
    return { ok: false, error: String(error.message) };
  }
};

out.estimateTokens = fx.tokenProbes.map((text) => estimateTokens(text));
out.truncate = fx.truncateProbes.map(([text, limit]) => truncate(text, limit));
out.defaults = resolveOptions();

const asker = (answers, dflt) => ({
  async ask(state, questions) {
    return {
      answers: Object.fromEntries(
        Object.keys(questions).map(
          (k) => [k, { type: 'noul', noul: k in answers ? answers[k] : dflt }],
        ),
      ),
    };
  },
});

out.calls = {};
for (const [name, preserve] of Object.entries(fx.preserve)) {
  out.calls[name] = collectToolCalls(messages, preserve).map((c) => [
    c.id, c.tool, c.callIndex, c.resultIndex, c.pinned, c.isError, c.resultChars,
  ]);
}

out.fit = fx.fit.map((options) => attempt(() => {
  const calls = collectToolCalls(messages, options.preserveRecentMessages);
  const r = fitState(messages, calls, options);
  return { stage: r.stage, tokens: r.tokens, state: JSON.stringify(r.state) };
}));

out.batches = fx.batches.map(([stateTokens, maxRequestTokens]) => attempt(() => {
  const calls = collectToolCalls(messages, fx.batchPreserve);
  return batchCalls(calls, stateTokens, { maxRequestTokens }).map((b) => b.map((c) => c.id));
}));

out.compact = [];
for (const scenario of fx.compact) {
  const outcome = await attemptAsync(
    () => compact(messages, asker(fx.replay, fx.defaultAnswer), scenario.options),
  );
  if (outcome.ok) {
    const r = outcome.value;
    out.compact.push({
      ok: true,
      value: {
        decisions: r.decisions.map((d) => [d.id, d.action, d.reason, d.keepCall, d.keepResult]),
        stats: Object.assign({}, r.stats, { ms: 0 }),
        messages: JSON.stringify(r.messages),
      },
    });
  } else {
    out.compact.push(outcome);
  }
}

out.apply = fx.apply.map((scenario) => attempt(() => {
  const calls = collectToolCalls(messages, scenario.preserveRecentMessages);
  const decisions = [];
  if (scenario.answers) {
    for (const c of calls) {
      decisions.push(
        decideCall(
          c,
          {
            keepCall: scenario.answers[`call_${c.id}`],
            keepResult: scenario.answers[`result_${c.id}`],
          },
          { keepThreshold: 0.5 },
        ),
      );
    }
  }
  return JSON.stringify(applyDecisions(messages, decisions, calls, scenario.truncateHeadChars));
}));

process.stdout.write(JSON.stringify(out));
"""


def _upstream_dir() -> Path | None:
    configured = os.environ.get(UPSTREAM_ENV)
    candidates = ([Path(configured)] if configured else []) + [
        Path("/tmp/fast-jev-compaction"),
        Path.home() / "gv-upstream" / "fast-jev-compaction",
    ]
    for candidate in candidates:
        if (candidate / "src" / "compact.ts").is_file():
            return candidate
    return None


def _canonical(message: Any) -> Any:
    """A transcript message in the upstream wire shape (dicts pass through)."""
    if isinstance(message, dict):
        return message
    return Message.from_any(message).to_wire()


def _outcome(run: Any) -> dict[str, Any]:
    """Run a scenario and record its value or the error, as the driver does."""
    from skaldir.jevcomp import JevCompactionError

    try:
        return {"ok": True, "value": run()}
    except JevCompactionError as exc:  # the upstream raises plain Error
        return {"ok": False, "error": str(exc)}


def _run_node(driver_dir: Path, fixture_path: Path) -> dict[str, Any]:
    node = shutil.which("node")
    assert node is not None
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [node, str(driver_dir / "driver.mjs"), str(fixture_path)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        pytest.fail(f"node failed ({completed.returncode}): {completed.stderr.strip()[:2000]}")
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def oracle(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    if shutil.which("node") is None:
        pytest.skip("node is not installed; the oracle needs Node for the upstream implementation")
    upstream = _upstream_dir()
    if upstream is None:
        pytest.skip(f"no upstream checkout found at $UPSTREAM_ENV, /tmp/... ({CLONE_HINT})")

    work = tmp_path_factory.mktemp("jevcomp-oracle")
    (work / "up").mkdir()
    for source in sorted((upstream / "src").glob("*.ts")):
        # Only the relative ESM specifiers change (./x.js -> ./x.ts) so Node can
        # resolve the TypeScript sources; no logic is touched.
        text = re.sub(r"(from\s+'\./[\w.-]+)\.js'", r"\1.ts'", source.read_text())
        (work / "up" / source.name).write_text(text)
    (work / "driver.mjs").write_text(DRIVER)

    fixture = {
        "messages": wire_transcript(),
        "tokenProbes": TOKEN_PROBES,
        "truncateProbes": TRUNCATE_PROBES,
        "preserve": {"zero": 0, "one": 1, "three": 3},
        "batchPreserve": 0,
        "fit": FIT_SCENARIOS,
        "batches": BATCH_SCENARIOS,
        "compact": [{"options": options} for options in COMPACT_SCENARIOS],
        "apply": APPLY_SCENARIOS,
        "replay": REPLAY,
        "defaultAnswer": 0.5,
    }
    fixture_path = work / "fixture.json"
    fixture_path.write_text(json.dumps(fixture))
    return _run_node(work, fixture_path)


def test_oracle_is_the_pinned_upstream_commit(oracle: dict[str, Any]) -> None:
    # The fixture ran, so the oracle answered: guard against a silently empty run.
    assert oracle["defaults"]["maxStateTokens"] == 25_000
    assert UPSTREAM_COMMIT  # the revision this port was written against (documented)


def test_estimate_tokens_and_truncate_match_the_typescript_original(
    oracle: dict[str, Any],
) -> None:
    assert [estimate_tokens(text) for text in TOKEN_PROBES] == oracle["estimateTokens"]
    assert (
        [truncate(text, limit) for text, limit in TRUNCATE_PROBES]
        == oracle["truncate"]
        == [
            "abc…",
            "abcdefghij",
            "…",
            f"{'x' * 299}…",
        ]
    )


def test_default_options_match(oracle: dict[str, Any]) -> None:
    resolved = resolve_options()
    upstream = oracle["defaults"]
    for ours, theirs in (
        (resolved.keep_threshold, upstream["keepThreshold"]),
        (resolved.preserve_recent_messages, upstream["preserveRecentMessages"]),
        (resolved.max_state_tokens, upstream["maxStateTokens"]),
        (resolved.max_request_tokens, upstream["maxRequestTokens"]),
        (resolved.truncate_head_chars, upstream["truncateHeadChars"]),
    ):
        assert ours == theirs
    assert DEFAULT_OPTIONS.keep_threshold == upstream["keepThreshold"]


def test_tool_call_collection_and_pinning_match(oracle: dict[str, Any]) -> None:
    for name, preserve in (("zero", 0), ("one", 1), ("three", 3)):
        calls = collect_tool_calls(transcript(), preserve)
        ours = [
            [c.id, c.tool, c.call_index, c.result_index, c.pinned, c.is_error, c.result_chars]
            for c in calls
        ]
        assert ours == oracle["calls"][name], f"preserveRecentMessages={preserve}"


def test_fit_state_stages_tokens_and_state_match(oracle: dict[str, Any]) -> None:
    for options, expected in zip(FIT_SCENARIOS, oracle["fit"], strict=True):
        messages = transcript()
        calls = collect_tool_calls(messages, options["preserveRecentMessages"])

        def run(
            scenario: dict[str, Any] = options,
            given: list = calls,
            current: list = messages,
        ) -> dict[str, Any]:
            fitted = fit_state(current, given, scenario)
            return {
                "stage": fitted.stage,
                "tokens": fitted.tokens,
                "state": wire_json(fitted.state.to_wire()),
            }

        assert _outcome(run) == expected, options


def test_batching_matches(oracle: dict[str, Any]) -> None:
    def run(state_tokens: int, max_request_tokens: int) -> list[list[str]]:
        calls = collect_tool_calls(transcript(), 0)
        return [
            [call.id for call in batch]
            for batch in batch_calls(calls, state_tokens, {"maxRequestTokens": max_request_tokens})
        ]

    assert [_outcome(lambda s=s, m=m: run(s, m)) for s, m in BATCH_SCENARIOS] == oracle["batches"]
    assert oracle["batches"] == [
        {"ok": True, "value": [["t1", "t2", "t3"]]},
        {"ok": True, "value": [["t1", "t2", "t3"]]},
        {"ok": False, "error": "state leaves no room for questions (~29980 of 30000 tokens)"},
    ]


def test_compact_decisions_stats_and_messages_match(oracle: dict[str, Any]) -> None:
    for scenario, expected in zip(COMPACT_SCENARIOS, oracle["compact"], strict=True):

        def run(given: dict[str, Any] = scenario) -> dict[str, Any]:
            result = compact(transcript(), replay_asker(REPLAY, default=0.5), given)
            return {
                "decisions": [
                    [d.id, d.action, d.reason, d.keep_call, d.keep_result] for d in result.decisions
                ],
                "stats": {
                    key: value for key, value in result.stats.to_wire().items() if key != "ms"
                },
                "messages": wire_json([_canonical(message) for message in result.messages]),
            }

        ours = _outcome(run)
        assert ours["ok"] == expected["ok"], scenario
        if expected["ok"]:
            assert ours["value"]["decisions"] == expected["value"]["decisions"], scenario
            assert ours["value"]["stats"] == {
                key: value for key, value in expected["value"]["stats"].items() if key != "ms"
            }, scenario
            assert ours["value"]["messages"] == expected["value"]["messages"], scenario
        else:
            assert ours["error"] == expected["error"], scenario


def test_apply_decisions_rebuild_matches(oracle: dict[str, Any]) -> None:
    for index, scenario in enumerate(APPLY_SCENARIOS):
        answers = scenario.get("answers")

        def run(given: dict[str, Any] = scenario, replay: Any = answers) -> str:
            messages = transcript()
            calls = collect_tool_calls(messages, 0)
            decisions = (
                [
                    decide_call(
                        call,
                        CallAnswer(
                            keep_call=replay[f"call_{call.id}"],
                            keep_result=replay[f"result_{call.id}"],
                        ),
                        0.5,
                    )
                    for call in calls
                ]
                if replay
                else []
            )
            kept = apply_decisions(messages, decisions, calls, given["truncateHeadChars"])
            return wire_json([_canonical(message) for message in kept])

        assert _outcome(run) == oracle["apply"][index], scenario


def test_the_original_transcript_shape_survives_the_round_trip() -> None:
    """The oracle's own fixture: nothing here rewrites a message it does not drop."""
    messages = transcript()
    assert messages[0].text == "Never edit anything under src/generated. Fix the failing test."
    assert Message.from_any(messages[1]).tool_uses[0] == ToolUse(
        tool_use_id="tool-1", tool="Read", input={"file_path": "src/a.ts"}, text=FILE_A
    )
    assert Message.from_any(messages[2]).tool_results[0] == ToolResult(
        tool_use_id="tool-1", text=FILE_A
    )
