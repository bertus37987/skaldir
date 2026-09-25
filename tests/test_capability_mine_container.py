"""The mined container capability inside capability mining + discovery.

A capability mined from a container image must behave like every other
capability in the MINING path — that is the whole point of "never build what
exists": the next session (or the next process) has to FIND it.

Pinned here:

* the container tool shows up in ``search_existing`` (surface ``generated_tools``)
  with its real purpose and status, so a rephrased need reuses it,
* ``search_before_build`` answers ``reuse`` — except for the requested name
  itself (rebuild = repair, not a duplicate),
* a second ``tools mine`` of the same capability refuses (``reuse``) and writes
  nothing,
* the runtime scan (``capability_mining.mine``) is unchanged by a container tool,
* ``discovery.discover`` ranks it as ``ready``/executable for its project — and
  the scope gate keeps it out of a foreign project context.
"""

import json

import pytest
from test_toolbuilder_container import (  # noqa: E402 — shared fake docker runtime
    CAPABILITY,
    IMAGE,
    FakeManager,
    _sha256_of,
)

from skaldir import discovery, toolbuilder
from skaldir.capability import project_id_for
from skaldir.capability_mining import mine, search_before_build, search_existing
from skaldir.toolbuilder import ContainerToolBuilder

OTHER_NEED = "rotate gpg subkeys and re-encrypt a vault"


@pytest.fixture(autouse=True)
def _no_real_docker(monkeypatch):
    """No test of this module may create a REAL DockerRuntimeManager (see the
    container test module: a container of ours on the shared daemon breaks
    sibling processes and the foreign stacks)."""
    def guarded(manager=None):
        if manager is None:
            raise AssertionError(
                "this test tried to create a real DockerRuntimeManager — "
                "inject the fake manager instead"
            )
        return manager

    monkeypatch.setattr(toolbuilder, "_manager_of", guarded)
    yield


@pytest.fixture
def mined(tmp_path):
    """A registered container capability in its own project context."""
    work = tmp_path / "project"
    work.mkdir()
    runtime = work / "rt"
    runtime.mkdir()
    manager = FakeManager()
    builder = ContainerToolBuilder(runtime, manager=manager)
    result = builder.mine_container(CAPABILITY, image=IMAGE, name="container_sha256")
    assert result["ok"] is True, result
    registered = builder.register_container_tool(
        "container_sha256", project_id=project_id_for(work)
    )
    assert registered["ok"] is True, registered
    return {"runtime": runtime, "builder": builder, "manager": manager, "work": work}


def test_the_mined_container_capability_is_found_by_the_search(mined):
    found = search_existing(mined["runtime"], CAPABILITY)

    assert found["matches"], found
    top = found["matches"][0]
    assert top["name"] == "container_sha256"
    assert top["surface"] == "generated_tools"
    assert top["source"] == "generated"
    assert top["status"] == "ready"
    assert top["purpose"] == CAPABILITY


def test_a_rephrased_need_reuses_the_container_capability(mined):
    verdict = search_before_build(
        mined["runtime"], "compute the sha256 digest for a text", name="another_tool"
    )

    assert verdict["decision"] == "reuse"
    assert verdict["match"]["name"] == "container_sha256"
    assert "generated_tools" in verdict["searched"]


def test_the_requested_name_itself_is_the_repair_path_not_a_duplicate(mined):
    """A rebuild under the SAME name is intended (repair), not a duplicate."""
    verdict = search_before_build(
        mined["runtime"], CAPABILITY, name="container_sha256"
    )
    # the search itself only skips the FIRST same-named match (the tool is
    # listed on two surfaces) — the builder therefore decides the repair by the
    # matched NAME, and that is what the next test pins
    same_name = [entry for entry in verdict["candidates"]
                 if entry["name"] == "container_sha256"]
    assert same_name

    again = mined["builder"].mine_container(
        CAPABILITY, image=IMAGE, name="container_sha256"
    )
    assert again["ok"] is True, again
    assert again["search"]["decision"] == "repair"


def test_an_unrelated_need_is_not_matched(mined):
    verdict = search_before_build(mined["runtime"], OTHER_NEED, name="gpg_rotate")

    assert verdict["decision"] == "build"
    assert verdict["match"] is None
    assert verdict["candidates"] == []


def test_a_second_mine_of_the_same_capability_refuses_and_writes_nothing(mined):
    builder = mined["builder"]
    runs_before = len(mined["manager"].runs)

    again = builder.mine_container(CAPABILITY, image=IMAGE, name="container_sha256_v2")

    assert again["ok"] is False and again["failure_class"] == "reuse"
    assert "already provides this capability" in again["reason"]
    assert len(mined["manager"].runs) == runs_before  # nothing was inspected
    assert not (builder.tools_dir / "container_sha256_v2").exists()


def test_the_runtime_scan_is_unaffected_by_a_container_tool(mined):
    summary = mine(mined["runtime"])

    assert summary["scanned_requests"] == 0
    assert summary["scanned_commands"] == 0
    assert summary["candidates_recorded"] == 0


def test_discovery_ranks_the_container_capability_as_ready(mined):
    project_id = project_id_for(mined["work"])

    found = discovery.discover("compute the sha-256 digest of a string",
                               runtime=mined["runtime"], project_id=project_id)

    assert found["executable_count"] == 1, found
    row = found["results"][0]
    assert row["name"] == "container_sha256"
    assert row["state"] == discovery.STATE_READY
    assert row["executable"] is True and row["selectable"] is True
    assert row["validation"] == "validated"
    assert row["version"] == "0.1.0"
    assert "health observed: HEALTHY" in row["reasons"]
    assert found["error"] is None


def test_the_scope_gate_keeps_it_out_of_a_foreign_project(mined):
    """A project-scoped tool is never an automatic pick in another context."""
    found = discovery.discover("compute the sha-256 digest of a string",
                               runtime=mined["runtime"], project_id="path:/somewhere/else")

    assert found["results"] == []
    assert found["blocked"]["scope"] == 1
    assert "container_sha256" not in json.dumps(found, default=str)


def test_the_registered_adapter_and_its_evidence_agree(mined):
    """The call path and the recorded expectation are the same bytes of truth."""
    call = mined["builder"].call_container_tool("container_sha256", {"text": "skaldir"})
    evidence = json.loads(
        (mined["runtime"] / "generated_tools" / "container_sha256"
         / "container_evidence.json").read_text(encoding="utf-8")
    )

    assert call["output"]["sha256"] == evidence["expected"] == _sha256_of("skaldir")
    assert evidence["registered"] is True
    assert evidence["adapter_digest"] and evidence["oracle"]
    assert evidence["isolation"]["network"] == "none"
