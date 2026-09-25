"""Deterministic eval episodes: signals, hints and silence on healthy work."""

from skaldir.evalharness import render_report, run_eval


def test_all_episodes_pass():
    results = run_eval()
    report = render_report(results)
    failed = [result for result in results if not result.passed]
    assert not failed, report


def test_healthy_episode_is_silent():
    healthy = next(result for result in run_eval() if result.name == "healthy_work")
    assert healthy.hint_kinds == []
    assert healthy.signal_kinds == []


def test_demo_episode_matches_demo_numbers():
    demo = next(result for result in run_eval() if result.name == "demo_script")
    assert demo.passed
    assert len(demo.signal_kinds) == 10  # incl. strategy_repeat (same strategy retried)
    assert len(demo.hint_kinds) == 4


def test_cli_eval_exits_zero():
    from skaldir.cli import main

    assert main(["eval"]) == 0
