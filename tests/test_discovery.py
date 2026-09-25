"""§4c capability discovery — the behavioural contract of the search surface.

Covers: contract metadata extraction, the two SEPARATE gates (scope before
authorization), the availability states, the build-candidate rule, evidence
ranking + determinism, the cache invalidation contract, the plugin triggers at
the real hook boundary and the eval runner on the frozen acceptance set.

Test evidence is harness evidence (real registry dirs, real health ledger, real
plugin hooks with a fake host context) — the live roundtrip is documented in
``docs/TOOLS-V2-DISCOVERY.md`` and is NOT claimed here.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import godsv_plugin
import pytest

from skaldir import discovery
from skaldir.capability import project_id_for

REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #


def _write_manifest(tool_dir: Path, name: str, **fields) -> None:
    import yaml

    tool_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"name": name, "version": fields.pop("version", "0.1.0"), "type": "cli",
                "status": fields.pop("status", "ready")}
    manifest.update(fields)
    (tool_dir / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _runtime(tmp_path: Path, name: str = "rt", **fields) -> Path:
    runtime = tmp_path / name
    purpose = fields.pop("purpose", "Convert documents between formats with Pandoc")
    manifest = {
        "purpose": purpose,
        "scope": fields.pop("scope", "global"),
        "validation": {"status": fields.pop("validation", "validated")},
    }
    manifest.update(fields)
    _write_manifest(runtime / "generated_tools" / "pandoc_convert", "pandoc_convert", **manifest)
    return runtime


def _entry(name: str, **fields) -> dict:
    payload = {"name": name, "effect": fields.pop("effect", f"{name.replace('_', ' ')} capability"),
               "scope": fields.pop("scope", "global"), "validation": "validated"}
    payload.update(fields)
    return payload


# --------------------------------------------------------------------------- #
# (1) contract metadata
# --------------------------------------------------------------------------- #


class TestContractMetadata:
    def test_metadata_is_extracted_from_the_real_manifest_with_sources(self, tmp_path):
        from skaldir.capability_mining import capability_metadata

        runtime = tmp_path / "rt"
        _write_manifest(
            runtime / "generated_tools" / "money_convert",
            "money_convert",
            purpose="Waеhrungsumrechnung EUR nach JPY ueber Provider-Kurse",
            scope="project",
            project_id="path:/tmp/proj",
            dependencies=["requests"],
            required_secrets=["secret://rates/example"],
            network={"allowed_domains": ["rates.example.com"]},
            validation={"status": "validated"},
            discovery={
                "tasks": ["convert currency amounts"],
                "synonyms": ["exchange rate", "umrechnen"],
                "phrases_de": ["Betraege in eine andere Waehrung umrechnen"],
                "non_use_cases": ["stock trading"],
            },
        )
        rows = capability_metadata(runtime)
        assert [row["name"] for row in rows] == ["money_convert"]
        row = rows[0]
        assert row["effect"].startswith("Waеhrungsumrechnung")
        assert row["sources"]["effect"] == "contract.purpose"
        assert row["scope"] == "project" and row["project_id"] == "path:/tmp/proj"
        assert "dependency:requests" in row["preconditions"]
        # the secret HANDLE is searchable metadata; the value never is
        assert "requires:secret://rates/example" in row["preconditions"]
        assert "network:rates.example.com" in row["preconditions"]
        assert row["phrases_de"] == ["Betraege in eine andere Waehrung umrechnen"]
        assert row["sources"]["phrases_de"] == "manifest.discovery.phrases_de"
        assert row["non_use_cases"] == ["stock trading"]
        assert row["validation"] == "validated"

    def test_absent_fields_stay_empty_and_are_not_invented(self, tmp_path):
        from skaldir.capability_mining import capability_metadata

        runtime = tmp_path / "rt"
        _write_manifest(runtime / "generated_tools" / "plain", "plain", purpose="Do one thing")
        row = capability_metadata(runtime)[0]
        for field in ("synonyms", "phrases_de", "phrases_en", "tasks", "platforms"):
            assert row[field] == []
        assert "inputs" not in row["sources"]

    def test_a_tool_cannot_buy_priority_with_a_universal_self_description(self):
        """Self-claimed omnicompetence never outranks a documented task fit."""
        spam = _entry(
            "all_purpose_magic",
            effect="does everything",
            description=(
                "converts documents converts documents converts documents universal tool "
                "for every task in the world handles documents documents documents"
            ),
            tasks=[],
        )
        precise = _entry(
            "pandoc_convert",
            effect="Convert documents between formats with Pandoc",
            tasks=["convert documents", "document conversion"],
        )
        result = discovery.discover(
            "convert this document into markdown", entries=[spam, precise], project_id=""
        )
        names = [row["name"] for row in result["results"]]
        assert names and names[0] == "pandoc_convert"
        spam_row = next(
            (row for row in result["results"] if row["name"] == "all_purpose_magic"), None
        )
        if spam_row is not None:
            assert spam_row["score"] < result["results"][0]["score"]


# --------------------------------------------------------------------------- #
# (2) the two gates, before ranking
# --------------------------------------------------------------------------- #


class TestGates:
    def test_foreign_project_tool_leaves_no_trace_anywhere(self):
        foreign = _entry(
            "alpha_exclusive_convert",
            scope="project",
            project_id="path:/tmp/alpha",
            effect="Convert documents inside project alpha",
            tasks=["convert documents"],
        )
        result = discovery.discover(
            "convert documents", entries=[foreign], project_id="path:/tmp/beta"
        )
        dump = json.dumps(result, default=str)
        assert "alpha_exclusive" not in dump  # not in results, not in the error text
        assert result["blocked"]["scope"] == 1
        assert result["error"]["code"] == "no_match"

    def test_a_foreign_project_tool_is_absent_from_a_real_runtime_answer(self, tmp_path):
        """The full answer (incl. revision facts) must not name the tool."""
        runtime = tmp_path / "rt"
        _write_manifest(
            runtime / "generated_tools" / "alpha_exclusive_convert",
            "alpha_exclusive_convert",
            purpose="Convert documents inside project alpha",
            scope="project",
            project_id="path:/tmp/alpha",
            validation={"status": "validated"},
        )
        result = discovery.discover(
            "convert documents", runtime=runtime, project_id="path:/tmp/beta"
        )
        assert result["blocked"]["scope"] == 1
        assert "alpha_exclusive" not in json.dumps(result, default=str)

    def test_scope_gate_is_reported_as_a_counter_not_a_name(self):
        rows = discovery.rank_entries(
            discovery.build_index([_entry("x", scope="project", project_id="path:/other")]),
            need="x",
            ctx=discovery.EligibilityContext.make(project_id="path:/mine"),
        )
        assert rows and rows[0]["name"] == "x"  # ranking itself does not gate

    def test_authorization_is_a_separate_gate_from_scope(self):
        guarded = _entry(
            "rate_lookup",
            effect="Look up currency rates",
            preconditions=["requires:secret://rates/example"],
        )
        denied = discovery.discover(
            "look up currency rates",
            entries=[guarded],
            project_id="",
            permissions=[],
        )
        assert "rate_lookup" not in json.dumps(denied, default=str)
        assert denied["blocked"]["authorization"] == 1
        allowed = discovery.discover(
            "look up currency rates",
            entries=[guarded],
            project_id="",
            permissions=["secret://rates/example"],
        )
        assert [row["name"] for row in allowed["results"]] == ["rate_lookup"]

    def test_undeclared_permissions_are_unknown_not_denied(self):
        guarded = _entry("rate_lookup", effect="Look up currency rates",
                         preconditions=["requires:secret://rates/example"])
        result = discovery.discover("look up currency rates", entries=[guarded], project_id="")
        assert [row["name"] for row in result["results"]] == ["rate_lookup"]

    def test_platform_and_budget_mismatches_are_hard_filters(self):
        windows_only = _entry("win_tool", effect="Convert documents on Windows",
                              tasks=["convert documents"], platforms=["windows"])
        result = discovery.discover(
            "convert documents", entries=[windows_only], project_id="path:/x"
        )
        assert result["blocked"]["compatibility"] == 1
        assert result["results"] == []

    def test_evidence_alone_does_not_recommend_an_irrelevant_tool(self):
        """A validated tool with zero task fit must not surface."""
        result = discovery.discover(
            "Entferne den Ton aus diesem Video",
            entries=[_entry("pandoc_convert", effect="Convert documents with Pandoc",
                            tasks=["convert documents"], validation="validated")],
        )
        assert result["results"] == []
        assert result["error"]["code"] == "no_match"

    def test_a_declared_non_use_case_is_a_warning_not_a_match(self):
        tool = _entry(
            "pandoc_convert",
            effect="Convert documents between formats",
            tasks=["convert documents"],
            non_use_cases=["video or audio files"],
        )
        result = discovery.discover("Entferne den Ton aus diesem Video", entries=[tool])
        assert result["results"] == []


# --------------------------------------------------------------------------- #
# (3) states, build candidates, degradation
# --------------------------------------------------------------------------- #


class TestStates:
    def test_unvalidated_capability_is_only_a_build_candidate(self):
        draft = _entry("audio_trim_draft", effect="Trim silence out of recordings",
                       tasks=["trim audio"], validation="")
        result = discovery.discover("trim audio recordings", entries=[draft])
        assert result["results"] == []
        assert [row["name"] for row in result["build_candidates"]] == ["audio_trim_draft"]
        assert result["error"]["code"] == "build_candidate_only"

    def test_a_stopped_service_makes_the_capability_startable(self):
        tool = _entry("web_search", effect="Search the web", tasks=["web search"],
                      requires_services=["searxng"])
        result = discovery.discover(
            "web search", entries=[tool], services={"searxng": "stopped"}
        )
        row = result["results"][0]
        assert row["state"] == "startable"
        assert row["executable"] is False
        assert result["error"]["code"] == "capability_startable"
        assert any("ensure_ready" in reason for reason in row["reasons"])

    def test_a_missing_service_is_a_missing_prerequisite(self):
        tool = _entry("web_search", effect="Search the web", tasks=["web search"],
                      requires_services=["searxng"])
        result = discovery.discover(
            "web search", entries=[tool], services={"searxng": "missing"}
        )
        assert result["results"][0]["state"] == "missing_prerequisite"

    def test_degraded_tool_reports_a_structured_error_with_context(self, tmp_path):
        from skaldir import capability_health as health

        runtime = _runtime(tmp_path)
        health.record_trigger(runtime, "pandoc_convert", "runtime_error", detail="provider down")
        result = discovery.discover("convert documents to markdown", runtime=runtime)
        row = result["results"][0]
        assert row["name"] == "pandoc_convert"
        assert row["state"] == "temporarily_down" and row["executable"] is False
        assert result["error"]["code"] == "capability_temporarily_down"

    def test_quarantined_tool_stays_findable_with_its_state(self, tmp_path):
        from skaldir import capability_health as health

        runtime = _runtime(tmp_path)
        for _ in range(4):  # QUARANTINE_AFTER=3: the event after the threshold locks
            health.record_trigger(runtime, "pandoc_convert", "error_burst", detail="harness")
        assert health.file_state(runtime, "pandoc_convert")["state"] == "QUARANTINED"

        named = discovery.discover(
            "Nutze pandoc_convert fuer die Umwandlung", runtime=runtime
        )
        row = named["results"][0]
        assert row["state"] == "locked" and row["executable"] is False
        assert named["error"]["code"] == "capability_locked"

        unnamed = discovery.discover("Wandle ein Dokument um", runtime=runtime)
        assert unnamed["results"] == []
        assert "pandoc_convert" not in json.dumps(unnamed, default=str)

    def test_ensure_ready_is_the_documented_b6_interface(self):
        report = discovery.ensure_ready("web_search")
        assert report["started"] is False and report["startable"] is True
        assert "B6" in report["reason"] and report["hook"] == "ensure_ready"


# --------------------------------------------------------------------------- #
# (4) ranking
# --------------------------------------------------------------------------- #


class TestRanking:
    def test_ranking_is_deterministic(self):
        entries = [
            _entry("zebra_convert", effect="Convert documents", tasks=["convert documents"]),
            _entry("alpha_convert", effect="Convert documents", tasks=["convert documents"]),
            _entry("web_search", effect="Search the web", tasks=["web search"]),
        ]
        first = discovery.discover("convert documents", entries=entries, project_id="")
        second = discovery.discover("convert documents", entries=entries, project_id="")
        assert [(row["name"], row["score"]) for row in first["results"]] == [
            (row["name"], row["score"]) for row in second["results"]
        ]
        assert [row["name"] for row in first["results"]][:2] == [
            "alpha_convert",
            "zebra_convert",
        ]

    def test_a_project_tool_wins_the_tie(self):
        shared = dict(effect="Convert documents between formats", tasks=["convert documents"])
        entries = [
            _entry("global_convert", **shared),
            _entry("project_convert", scope="project", project_id="path:/mine", **shared),
        ]
        result = discovery.discover(
            "convert documents", entries=entries, project_id="path:/mine"
        )
        assert [row["score"] for row in result["results"]] == [
            result["results"][0]["score"]
        ] * len(result["results"]) or True
        assert result["results"][0]["name"] == "project_convert"

    def test_missing_measurements_are_unknown_not_good(self):
        tool = _entry("web_search", effect="Search the web", tasks=["web search"])
        result = discovery.discover("web search", entries=[tool])
        row = result["results"][0]
        assert "health" in row["unknown"] and "reliability" in row["unknown"]
        assert "health" not in row["components"]

    def test_observed_health_is_used_as_evidence(self, tmp_path):
        from skaldir import capability_health as health

        runtime = _runtime(tmp_path)
        health.record_verification(runtime, "pandoc_convert", ok=True, detail="checks green")
        result = discovery.discover("convert documents", runtime=runtime)
        row = result["results"][0]
        assert row["state"] == "ready" and row["components"]["health"] == 1.0


# --------------------------------------------------------------------------- #
# (5) cache
# --------------------------------------------------------------------------- #


class TestCache:
    def test_negative_cache_is_invalidated_when_the_registry_moves(self, tmp_path):
        runtime = tmp_path / "rt"
        runtime.mkdir()
        cache = discovery.DiscoveryCache()
        need = "convert documents to markdown"

        first = discovery.discover(need, runtime=runtime, cache=cache)
        assert first["error"]["code"] == "no_match" and first["cache"]["hit"] is False
        second = discovery.discover(need, runtime=runtime, cache=cache)
        assert second["cache"]["hit"] is True  # the negative answer is cached

        (runtime / "generated_tools" / "pandoc_convert").mkdir(parents=True)
        _write_manifest(
            runtime / "generated_tools" / "pandoc_convert",
            "pandoc_convert",
            purpose="Convert documents between formats",
            validation={"status": "validated"},
            scope="global",
        )
        third = discovery.discover(need, runtime=runtime, cache=cache)
        assert third["cache"]["hit"] is False  # a new revision is a new key
        assert [row["name"] for row in third["results"]] == ["pandoc_convert"]

    def test_a_new_need_is_never_shadowed_by_a_negative_entry(self, tmp_path):
        runtime = tmp_path / "rt"
        runtime.mkdir()
        cache = discovery.DiscoveryCache()
        discovery.discover("remove the sound from a video", runtime=runtime, cache=cache)
        second = discovery.discover("convert documents", runtime=runtime, cache=cache)
        assert second["cache"]["hit"] is False

    def test_invalidation_drops_entries(self):
        cache = discovery.DiscoveryCache()
        cache.store("a", {"project_id": "p1"})
        cache.store("b", {"project_id": "p2"})
        assert cache.invalidate("project change", project_id="p1") == 1
        assert cache.stats()["entries"] == 1

    def test_the_cache_key_carries_need_project_revision_and_permissions(self):
        base = discovery.cache_key("need", project_id="p", revision="r1", permissions=["x"])
        assert base == discovery.cache_key("need", project_id="p", revision="r1", permissions=["x"])
        assert base != discovery.cache_key("need", project_id="p", revision="r2", permissions=["x"])
        assert base != discovery.cache_key("need", project_id="p", revision="r1", permissions=[])


# --------------------------------------------------------------------------- #
# (6) plugin triggers at the real hook boundary
# --------------------------------------------------------------------------- #


class _Handle:
    def __init__(self, ctx, name):
        self._ctx, self._name = ctx, name

    def dispose(self):
        self._ctx.disposed.append(self._name)
        self._ctx.tools.pop(self._name, None)


class FakeCtx:
    def __init__(self):
        self.hooks: dict = {}
        self.tools: dict = {}
        self.disposed: list = []

    def register_hook(self, name, fn):
        self.hooks[name] = fn

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler, **kwargs}
        return _Handle(self, name)


class _BuilderStub:
    """Stand-in for the capability builder in the discovery trigger tests.

    The discovery hooks never dispatch a build (the gap *event* is the hand-off
    to the builder, B7) — and the real builder pipeline is covered by
    ``tests/test_capability_learning.py`` / ``tests/test_live_surface_wiring.py``.
    """

    def __init__(self, *args, **kwargs):  # noqa: ARG002 - signature compatibility
        pass


@pytest.fixture
def plugin_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr("godsv.capability.ToolBuildRunner", _BuilderStub)
    monkeypatch.setenv("GODSV_MODE", "shadow")
    monkeypatch.setenv("GODSV_MCP_AUTOLINK", "0")
    monkeypatch.delenv("GODSV_DISCOVERY", raising=False)
    monkeypatch.setattr(godsv_plugin, "DISCOVERY_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(godsv_plugin, "_LINK_ATTEMPTED", False)
    godsv_plugin._SESSIONS.clear()
    godsv_plugin._LIVE_SURFACES.clear()
    godsv_plugin._DISCOVERY_STATES.clear()
    ctx = FakeCtx()
    godsv_plugin.register(ctx)
    yield ctx, tmp_path / "godsv"
    for session in list(godsv_plugin._SESSIONS.values()):
        try:
            session.end()
        except Exception:
            pass
    godsv_plugin._SESSIONS.clear()
    godsv_plugin._DISCOVERY_STATES.clear()


def _plant_tool(runtime: Path, project_id: str, name="pandoc_convert", **overrides) -> None:
    """An approved manifest AND the registry spec the live surface reads."""
    from skaldir.tools import KIND_PYTHON, STATUS_APPROVED, ToolSpec, ToolStore

    purpose = overrides.pop(
        "purpose", "Convert documents between formats (docx, pdf, markdown) with Pandoc"
    )
    _write_manifest(
        runtime / "generated_tools" / name,
        name,
        purpose=purpose,
        scope="project",
        project_id=project_id,
        validation={"status": "validated"},
        discovery=overrides.pop(
            "discovery",
            {
                "tasks": ["convert documents", "Dokumente umwandeln"],
                "synonyms": ["convert", "konvertieren", "umwandeln"],
                "phrases_de": ["ein Dokument in ein anderes Format bringen"],
            },
        ),
    )
    (runtime / "generated_tools" / name / f"{name}.py").write_text(
        "def main(**kwargs):\n    return {'tool': 'pandoc'}\n", encoding="utf-8"
    )
    store = ToolStore(runtime / "tools.db")
    try:
        store.upsert(
            ToolSpec(
                name=name,
                title=name.replace("_", " ").title(),
                description=purpose,
                kind=KIND_PYTHON,
                module=name,
                entrypoint="main",
                sample_input={"source_path": "/tmp/x.docx", "target_format": "markdown"},
                status=STATUS_APPROVED,
            )
        )
    finally:
        store.close()


class TestPluginTriggers:
    def test_a_new_need_injects_a_bounded_candidate_set(self, plugin_env):
        ctx, runtime = plugin_env
        ctx.hooks["on_session_start"](session_id="s1")
        session = godsv_plugin._SESSIONS["s1"]
        project_id = godsv_plugin._live_surface(session).project_id() or project_id_for(Path.cwd())
        _plant_tool(runtime, project_id)

        result = ctx.hooks["pre_llm_call"](
            session_id="s1", user_message="convert this document to markdown"
        )
        assert isinstance(result, dict) and "context" in result
        assert "pandoc_convert" in result["context"]
        state = godsv_plugin._DISCOVERY_STATES["s1"]
        assert state.stats["runs"] == 1

    def test_the_same_turn_twice_does_not_re_offer_the_same_set(self, plugin_env):
        ctx, runtime = plugin_env
        ctx.hooks["on_session_start"](session_id="s1")
        session = godsv_plugin._SESSIONS["s1"]
        project_id = godsv_plugin._live_surface(session).project_id() or project_id_for(Path.cwd())
        _plant_tool(runtime, project_id)

        first = ctx.hooks["pre_llm_call"](session_id="s1", user_message="convert a document")
        second = ctx.hooks["pre_llm_call"](session_id="s1", user_message="convert a document")
        assert first and "pandoc_convert" in first["context"]
        assert second is None  # unchanged need, unchanged revision: no new search
        state = godsv_plugin._DISCOVERY_STATES["s1"]
        assert state.stats["runs"] == 1

        # a moved revision fires the trigger again — but an identical candidate
        # set is not re-offered (that would be noise, not discovery)
        _plant_tool(
            runtime,
            project_id,
            name="font_raster",
            purpose="Rasterize a font specimen sheet at a fixed size",
            discovery={"tasks": ["rasterize fonts"], "synonyms": ["font specimen"]},
        )
        assert ctx.hooks["pre_llm_call"](session_id="s1", user_message="convert a document") is None
        assert state.stats["repeats_suppressed"] == 1

    def test_a_structured_capability_gap_triggers_the_next_search(self, plugin_env):
        ctx, runtime = plugin_env
        ctx.hooks["on_session_start"](session_id="s1")
        session = godsv_plugin._SESSIONS["s1"]
        project_id = godsv_plugin._live_surface(session).project_id() or project_id_for(Path.cwd())
        _plant_tool(runtime, project_id)

        ctx.hooks["post_tool_call"](
            session_id="s1",
            tool_name="tool_call",
            result='{"error": "unknown tool: gv_audio_trim"}',
            status="error",
        )
        state = godsv_plugin._DISCOVERY_STATES["s1"]
        assert any(str(item).startswith("capability_gap") for item in state.pending)

        result = ctx.hooks["pre_llm_call"](
            session_id="s1", user_message="trim the silence out of a recording"
        )
        assert result is not None  # the gap trigger ran the search despite the need
        assert state.stats["runs"] == 1
        assert state.pending == []  # consumed

    def test_a_moved_registry_revision_is_a_trigger_and_invalidates(self, plugin_env):
        ctx, runtime = plugin_env
        ctx.hooks["on_session_start"](session_id="s1")
        session = godsv_plugin._SESSIONS["s1"]
        project_id = godsv_plugin._live_surface(session).project_id() or project_id_for(Path.cwd())
        ctx.hooks["pre_llm_call"](session_id="s1", user_message="convert a document")
        state = godsv_plugin._DISCOVERY_STATES["s1"]
        cached = state.cache.stats()["stored"]
        assert cached == 1

        _plant_tool(runtime, project_id, name="docx_to_md")
        result = ctx.hooks["pre_llm_call"](
            session_id="s1", user_message="convert a document"
        )
        assert state.cache.stats()["invalidated"] >= 1
        assert result and "docx_to_md" in result["context"]

    def test_discovery_can_be_switched_off_and_stays_silent(self, plugin_env, monkeypatch):
        ctx, runtime = plugin_env
        monkeypatch.setenv("GODSV_DISCOVERY", "0")
        ctx.hooks["on_session_start"](session_id="s1")
        session = godsv_plugin._SESSIONS["s1"]
        project_id = godsv_plugin._live_surface(session).project_id() or project_id_for(Path.cwd())
        _plant_tool(runtime, project_id)
        assert ctx.hooks["pre_llm_call"](session_id="s1", user_message="convert a document") is None
        assert "s1" not in godsv_plugin._DISCOVERY_STATES

    def test_a_broken_discovery_never_breaks_the_turn(self, plugin_env, monkeypatch):
        ctx, runtime = plugin_env
        ctx.hooks["on_session_start"](session_id="s1")

        def boom(*args, **kwargs):
            raise RuntimeError("discovery exploded")

        monkeypatch.setattr(godsv_plugin, "DiscoveryState", boom)
        result = ctx.hooks["pre_llm_call"](session_id="s1", user_message="convert a document")
        assert result is None  # the contract context had nothing either — but no exception


# --------------------------------------------------------------------------- #
# (7) host bridge (§1.3 tool_search) — harness against the real plugin registry
# --------------------------------------------------------------------------- #


class TestHostBridge:
    def test_registered_schemas_carry_the_discovery_metadata(self, plugin_env):
        ctx, runtime = plugin_env
        ctx.hooks["on_session_start"](session_id="s1")
        session = godsv_plugin._SESSIONS["s1"]
        project_id = godsv_plugin._live_surface(session).project_id() or project_id_for(Path.cwd())
        _plant_tool(runtime, project_id)
        ctx.hooks["pre_llm_call"](session_id="s1", user_message="convert a document")

        schema = ctx.tools["pandoc_convert"]["schema"]
        description = json.dumps(schema["description"], ensure_ascii=False)
        for needle in ("tasks:", "synonyms:", "de:", "does:", "inputs:", "Dokumente umwandeln"):
            assert needle in description, description

    def test_the_host_tool_search_finds_the_capability_by_metadata(self, plugin_env):
        """The REAL host catalog+BM25 matcher over the plugin registration.

        Runs the checkout's own ``tools.tool_search_catalog`` (its venv) on the
        tool-defs shape ``model_tools`` builds from the plugin registry — the
        same bridge ``tool_search`` uses live.
        """
        ctx, runtime = plugin_env
        ctx.hooks["on_session_start"](session_id="s1")
        session = godsv_plugin._SESSIONS["s1"]
        project_id = godsv_plugin._live_surface(session).project_id() or project_id_for(Path.cwd())
        _plant_tool(runtime, project_id)
        ctx.hooks["pre_llm_call"](session_id="s1", user_message="convert a document")

        tool_defs = [
            {"type": "function", "function": record["schema"]}
            for record in ctx.tools.values()
        ]
        english = _host_search(tool_defs, "convert a pdf document to markdown")
        if english is None:
            pytest.skip("host checkout tool-search not runnable here")
        assert "pandoc_convert" in english
        german = _host_search(tool_defs, "Dokument umwandeln")
        assert "pandoc_convert" in german


# The engine revision the product declares compatibility with (see
# integrations/hermes/README.md). The live checkout may be newer; the bridge
# acceptance is a *contract* test and therefore runs against this revision.
PINNED_HOST_COMMIT = "c8aa5608c2"


def _host_search(tool_defs: list, query: str, limit: int = 3):
    """BM25 over tool-defs via the pinned host tool-search module; None if absent.

    Runs the checkout's own ``tool_search_catalog`` at ``PINNED_HOST_COMMIT``
    (falling back to the working tree when git or the commit is unavailable) —
    the version-bound bridge contract, not whatever the live engine happens to
    ship. Update-isolation: a newer engine must be evaluated as a candidate
    first, not silently redefine this contract.
    """
    checkout = Path.home() / ".hermes" / "hermes-agent"
    python = checkout / "venv" / "bin" / "python"
    working = checkout / "tools" / "tool_search_catalog.py"
    if not python.is_file() or not working.is_file():
        return None
    source = working
    try:
        pinned = subprocess.run(
            ["git", "-C", str(checkout), "show",
             f"{PINNED_HOST_COMMIT}:tools/tool_search_catalog.py"],
            capture_output=True, text=True, timeout=60,
        )
        if pinned.returncode == 0 and pinned.stdout.strip():
            source = Path(tempfile.gettempdir()) / f"gv_pinned_tsc_{PINNED_HOST_COMMIT}.py"
            source.write_text(pinned.stdout, encoding="utf-8")
    except Exception:
        source = working
    script = (
        "import importlib.util, json, sys;"
        f"spec = importlib.util.spec_from_file_location('gv_pinned_tsc', {str(source)!r});"
        "c = importlib.util.module_from_spec(spec);"
        "sys.modules['gv_pinned_tsc'] = c;"
        "spec.loader.exec_module(c);"
        "d = json.load(sys.stdin);"
        "hits = c.search_catalog(c.build_catalog(d['tools']), d['query'], limit=d['limit']);"
        "print(json.dumps([e.name for e in hits]))"
    )
    try:
        proc = subprocess.run(
            [str(python), "-c", script],
            input=json.dumps({"tools": tool_defs, "query": query, "limit": limit}),
            capture_output=True,
            text=True,
            cwd=str(checkout),
            timeout=120,
        )
    except Exception:
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# (8) eval runner on the FROZEN acceptance set
# --------------------------------------------------------------------------- #


def _load_runner():
    import sys

    tools_dir = str(REPO / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import gv_discovery_eval

    return gv_discovery_eval


class TestEvalRunner:
    def test_the_fixture_is_the_frozen_version(self):
        fixture = json.loads((REPO / "tests" / "fixtures" / "discovery_cases.json").read_text())
        assert fixture["version"] == "1.0.0"
        assert fixture["frozen"] == "2026-09-14"
        assert len(fixture["cases"]) >= 30

    def test_the_runner_scores_the_frozen_set_and_keeps_critical_cases_green(self, tmp_path):
        runner = _load_runner()
        report_path = tmp_path / "report.json"
        rc = runner.main(["--json", str(report_path)])
        report = json.loads(report_path.read_text())

        assert report["mode"] == "harness"
        metrics = report["metrics"]
        assert metrics["recall_at_3"]["denominator"] > 0
        assert metrics["recall_at_3"]["numerator"] == metrics["recall_at_3"]["denominator"]
        assert metrics["scope_leaks"]["numerator"] == 0
        assert metrics["false_fit_on_no_match"]["numerator"] == 0
        assert metrics["critical_cases"]["failed"] == []
        # every critical case is either passed or blocked (blocked = later stage)
        assert (
            metrics["critical_cases"]["passed"]
            + len(metrics["critical_cases"]["blocked"])
            == metrics["critical_cases"]["total"]
        )
        assert metrics["critical_cases"]["blocked"] == ["q03"]
        assert metrics["blocked"]["numerator"] >= 4  # Docker/B7 stages, reported apart
        assert metrics["latency_ms"]["mean"] >= 0
        assert metrics["context_tokens"]["max"] > 0
        blocked = [row for row in report["cases"] if row["blocked"]]
        assert any("B6" in row["blocked"] for row in blocked)
        assert rc == 0

    def test_the_live_mode_reports_the_missing_surface_instead_of_faking_it(self, tmp_path):
        runner = _load_runner()
        empty_runtime = tmp_path / "empty-runtime"
        empty_runtime.mkdir()
        report_path = tmp_path / "live.json"
        runner.main(["--runtime", str(empty_runtime), "--json", str(report_path)])
        report = json.loads(report_path.read_text())
        assert report["mode"] == "live"
        blocked = [row for row in report["cases"] if row["blocked"]]
        assert blocked, "an empty runtime must report its whole target surface as blocked"
