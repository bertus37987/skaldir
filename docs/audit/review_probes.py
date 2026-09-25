"""Independent, offline audit probes. Uses only temporary fixture state."""
import json
import os
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

from godsv.completion import CompletionStore, CompletionRequest, DodItem, GateRunner, RunEvidence, run_evidence_from_events

out = {}
def event(step, kind, payload):
    return SimpleNamespace(step=step, kind=kind, payload=payload)

with tempfile.TemporaryDirectory(prefix="godsv-audit-") as tmp:
    root = Path(tmp)
    repo = root / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)
    git("init", "-q")
    (repo / "README.md").write_text("audit fixture\n")
    git("add", ".")
    git("-c", "user.name=Audit", "-c", "user.email=audit@example.invalid", "commit", "-qm", "fixture")
    request = CompletionRequest(claimed_completed=["The requested login feature works."])

    events = [event(1, "tool_call", {"name":"terminal", "args":{"command":"echo pytest"}}), event(2, "tool_result", {"tool":"terminal", "exit_code":0, "stdout":"pytest"})]
    ev = run_evidence_from_events(events)
    verdict = GateRunner(repo_root=repo, dod=[DodItem("login", "Login actually authenticates", verification="pytest")]).verify(request, ev)
    out["echo_pytest_certifies_missing_feature"] = {"evidence":[vars(e) for e in ev], "verdict":verdict.status}

    events2 = [event(1,"tool_call",{"name":"terminal","args":{"command":"pytest -q"}}), event(2,"tool_result",{"tool":"terminal","exit_code":1}), event(3,"tool_call",{"name":"terminal","args":{"command":"pwd"}}), event(4,"tool_result",{"tool":"terminal","exit_code":0})]
    ev2 = run_evidence_from_events(events2)
    out["tool_name_collapses_distinct_calls"] = {"evidence":[vars(e) for e in ev2],"verdict":GateRunner(repo_root=repo).verify(request,ev2).status}

    ev3 = run_evidence_from_events([event(1,"tool_call",{"name":"terminal","args":{"command":"pytest -q"}}),event(2,"tool_result",{"tool":"terminal","error":"process never started"})])
    out["error_without_exit_code"] = [vars(e) for e in ev3]

    rt = root / "criteria"
    store = CompletionStore(rt)
    store.begin_goal("A", "Implement login", [])
    state, _ = store.add_criteria(["Login works after restart"])
    dod = [DodItem(**x) for x in state["dod"]]
    good_all = [RunEvidence(name=n,ok=True) for n in ("pytest","ui_e2e","api","run","build","persistence_restart")]
    out["chat_criterion_unverifiable"] = {"dod":state["dod"],"failures":GateRunner(repo_root=repo,dod=dod).verify(request,good_all).blocking_failures}

    os.environ["HERMES_HOME"] = str(root / "home")
    os.environ["GODSV_PAUSE"] = "0"
    os.environ["GODSV_MODE"] = "whisper"
    import godsv_plugin
    import godsv_plugin.dashboard.plugin_api as panel
    runtime = panel.runtime_dir()
    runtime.mkdir(parents=True, exist_ok=True)
    s = CompletionStore(runtime)
    s.begin_goal("A", "User goal", [DodItem("required", "Missing mandatory UI", verification="ui_e2e")])
    panel.completion_criteria_payload({"criteria":["Must survive a restart"]})
    session = godsv_plugin.GodsvSession("A",runtime)
    session._run_evidence = lambda: [RunEvidence(name="run",ok=True,cmd="pwd")]
    session._repo_root_hint = lambda: repo
    loaded = session._load_pact()
    result = session._strict_completion("The task is done.")
    out["chat_goal_disconnected_from_hook"] = {"loaded_pact":str(loaded),"verdict":getattr(result,"status",None),"stored_goal":s.state().get("goal"),"stored_dod":s.state().get("dod")}
    other = godsv_plugin.GodsvSession("B",runtime)
    out["second_session_shares_terminal_state"] = {"session_B_reads":other._completion_store().state().get("state"),"strict_result":str(other._strict_completion("The task is done."))}
    session.end()
    other.end()

    # A new untracked source file is absent from git diff HEAD.
    (repo / "src").mkdir()
    (repo / "src" / "broken.py").write_text("raise RuntimeError('not implemented')\n")
    out["untracked_python_not_test_gated"] = GateRunner(repo_root=repo).verify(request,[RunEvidence(name="run",ok=True,cmd="pwd")]).status

print(json.dumps(out, indent=2))
