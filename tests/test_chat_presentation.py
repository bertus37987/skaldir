"""Human chat presentation is separate from the diagnostic agent CLI."""
from __future__ import annotations

import json

from test_agent_loop import StubEndpoint, answer, tool_step

from skaldir import agent_loop_cli as agent

NOISE = ('usage=', 'steps=', 'transcript:', 'answer: streamed above', 'step(s)',
         'first token at', 'deltas', 'updates (', 'run run-')


def run_chat(tmp_path, monkeypatch, capsys, steps, *, json_mode=False):
    monkeypatch.setenv('SKALDIR_LLM_API_KEY', 'local-fixture-only')
    with StubEndpoint(steps) as stub:
        argv = ['run', 'hi', '--workspace', str(tmp_path), '--runtime', str(tmp_path / 'rt'),
                '--base-url', stub.base_url, '--model', 'stub-model', '--no-stream',
                '--no-jev-turns', '--max-steps', '1']
        if json_mode:
            argv.append('--json')
        args = agent.build_parser().parse_args(argv)
        args.chat_output = True
        args.progress = True  # chat owns the live row even if standalone progress was requested
        code = agent.cmd_run(args)
    output = capsys.readouterr()
    return code, output.out, output.err


def test_chat_answer_has_no_diagnostic_summary(tmp_path, monkeypatch, capsys):
    code, out, err = run_chat(tmp_path, monkeypatch, capsys, [answer('Hello there.')])
    assert code == 0
    assert out.strip() == 'Hello there.'
    assert not any(word in out + err for word in NOISE), out + err
    paths = list((tmp_path / 'rt' / 'agent' / 'runs').glob('*.jsonl'))
    assert len(paths) == 1
    events = [json.loads(line) for line in paths[0].read_text().splitlines()]
    final = next(e for e in events if e['kind'] == 'run.finished')
    assert final['answer'] == 'Hello there.' and final['status'] == 'answered'


def test_chat_blocked_run_still_reports_failure(tmp_path, monkeypatch, capsys):
    code, out, err = run_chat(tmp_path, monkeypatch, capsys,
                             [tool_step(('c1', 'list_dir', {}), text='Working')])
    assert code == 3
    assert 'blocked' in (out + err).lower()
    assert 'max_steps' in out + err
    assert 'usage=' not in out + err


def test_streamed_chat_answer_is_not_reprinted(monkeypatch):
    shown = []
    monkeypatch.setattr(agent, '_human', shown.append)
    data = {'run_id': 'r1', 'status': 'answered', 'steps': 1, 'tool_calls': 0,
            'executed_calls': 0, 'duration_ms': 100, 'usage': {},
            'answer': 'Hello there.', 'streaming': {'streamed': True, 'deltas': 3},
            'transcript_path': '/tmp/test-transcript'}
    agent._render_result(data, streamed_text='Hello there.', chat_output=True)
    assert shown == []


def test_chat_renderer_has_no_second_status_footer(monkeypatch):
    import argparse
    import io

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    seen = {}
    monkeypatch.setattr(agent.sys, 'stdout', Terminal())
    monkeypatch.setattr(agent, '_MarkdownStream', lambda stream, **kw: seen.update(kw) or seen)
    agent._answer_renderer(argparse.Namespace(chat_output=True, json=False, stream=True))
    assert seen['footer'] is False
    assert seen['incremental'] is True


def test_chat_failure_exposes_provider_reason_without_diagnostic_dump(monkeypatch):
    shown = []
    monkeypatch.setattr(agent, '_human', shown.append)
    agent._render_result({'status': 'failed', 'reason': 'provider_error',
                          'message': 'provider call failed (HTTP 402: Insufficient Balance)'},
                         chat_output=True)
    assert len(shown) == 1
    assert 'HTTP 402' in shown[0] and 'Insufficient Balance' in shown[0]
