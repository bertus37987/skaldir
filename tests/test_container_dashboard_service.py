"""The dashboard warm service — a container adapter's declared service spec.

A mined research tool must be able to START its own dashboard: the adapter's
``service`` spec (argv + network + ports) is what ``ensure_container_started``
turns into the ONE warm container. Ports in 3400–3499 are the mission's
dashboard range (VAL-TOOL-001); the default kept every warm service isolated
behind ``--network none`` with no way to serve a dashboard at all.

Pinned here:

* a declared service spec travels from the template into the adapter,
* the warm container is started WITH the declared network and port bindings
  (one container, published on its localhost port),
* an undeclared service (the keepalive default) stays network-none as before,
* a service spec with an empty argv is refused (nothing invented).
"""

import json
from pathlib import Path

import pytest
from test_toolbuilder_container import FakeManager

from skaldir import toolbuilder
from skaldir.container_cli_templates import fixture_template
from skaldir.toolbuilder import (
    ContainerToolBuilder,
    ensure_container_started,
    generate_container_adapter,
    inspect_container_surface,
)

GOLDEN_NOTE = (
    "AMD builds CPUs and GPUs; the reference dataset carries research notes, "
    "not live prices"
)


def _schema(**overrides) -> dict:
    """The market-research snapshot template with a declared dashboard service."""
    entry = fixture_template(
        id="market-research-snapshot",
        terms=("market", "research", "snapshot", "equity"),
        verb="snapshot",
        argv_template=[
            "python3", "/srv/service.py", "snapshot", "--symbol", "{symbol}",
            {"flag": "--detail", "value": "{detail}", "when_given": "detail"},
        ],
        inputs={
            "symbol": {"type": "string", "required": True, "pattern": "^[A-Z]{1,10}$"},
            "detail": {"type": "enum", "values": ("brief", "full")},
        },
        outputs={
            "symbol": {"pattern": "^[A-Z]{1,10}$"},
            "sector": {},
            "sections": {},
            "research_note": {},
            "source": {},
            "dataset_version": {},
        },
        sample_input={"symbol": "AMD", "detail": "brief"},
        expected={
            "symbol": "AMD",
            "sector": "Semiconductors",
            "sections": "overview,risk",
            "research_note": GOLDEN_NOTE,
            "source": "skaldir-market-research-dataset",
            "dataset_version": "2026.10",
        },
        description="market research snapshot from the dashboard container",
        output_protocol="keyed",
    )
    entry["service"] = {
        "argv": ["python3", "/srv/service.py", "serve", "--port", "8080"],
        "network": "bridge",
        "ports": {"8080": 3400},
    }
    entry.update(overrides)
    return entry


def _golden_stdout() -> str:
    return "\n".join([
        "symbol=AMD",
        "sector=Semiconductors",
        "sections=overview,risk",
        f"research_note={GOLDEN_NOTE}",
        "source=skaldir-market-research-dataset",
        "dataset_version=2026.10",
    ]) + "\n"


@pytest.fixture(autouse=True)
def _no_real_docker(monkeypatch):
    """No test of this module may create a REAL DockerRuntimeManager."""
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
def runtime(tmp_path) -> Path:
    path = tmp_path / "runtime"
    path.mkdir()
    return path


@pytest.fixture
def manager() -> FakeManager:
    return FakeManager(verbs="sh\nsnapshot\npython3\n", adapter_stdout=_golden_stdout())


def _mined(builder, manager, **mine_overrides) -> dict:
    result = builder.mine_container(
        "market research snapshot from the dashboard container",
        image="skaldir-tool-market-research:v1",
        name="market_research_snapshot",
        verb="snapshot",
        test_input={"symbol": "AMD", "detail": "brief"},
        schema=_schema(),
        **mine_overrides,
    )
    assert result["ok"] is True, result
    return result


class TestTheDeclaredServiceSpec:
    def test_it_travels_from_the_template_into_the_adapter(self):
        """The generator keeps a declared service spec — it does not override it."""
        surface = inspect_container_surface(
            FakeManager(verbs="sh\nsnapshot\n"),
            image="skaldir-tool-market-research:v1",
            image_digest="sha256:" + "a" * 64,
        )
        adapter = generate_container_adapter(
            "market research snapshot",
            name="market_research_snapshot",
            image="skaldir-tool-market-research:v1",
            image_digest="sha256:" + "a" * 64,
            surface=surface,
            template=_schema(),
        )

        service = adapter["service"]
        assert service["argv"] == [
            "python3", "/srv/service.py", "serve", "--port", "8080",
        ]
        assert service["network"] == "bridge"
        assert service["ports"] == {"8080": 3400}
        # the call path of the adapter is unchanged by the service declaration
        assert adapter["verb"] == "snapshot"
        assert adapter["operation"]["inputs"]["symbol"]["required"] is True

    def test_a_service_spec_without_an_argv_is_refused(self):
        """A service spec that declares nothing to run is refused, not defaulted."""
        surface = inspect_container_surface(
            FakeManager(verbs="sh\nsnapshot\n"),
            image="skaldir-tool-market-research:v1",
            image_digest="sha256:" + "a" * 64,
        )
        template = _schema()
        template["service"] = {"ports": {"8080": 3400}}
        with pytest.raises(toolbuilder.AdapterRefusal) as excinfo:
            generate_container_adapter(
                "market research snapshot",
                name="market_research_snapshot",
                image="skaldir-tool-market-research:v1",
                image_digest="sha256:" + "a" * 64,
                surface=surface,
                template=template,
            )
        assert "argv" in str(excinfo.value)


class TestTheWarmDashboardContainer:
    def test_the_warm_container_publishes_the_declared_port(self, runtime, manager):
        """ONE warm container, started through the sanctioned port-publication route."""
        builder = ContainerToolBuilder(runtime, manager=manager)
        _mined(builder, manager)
        builder.register_container_tool("market_research_snapshot")

        started = ensure_container_started(
            runtime, "market_research_snapshot", manager=manager
        )

        assert started["started"] is True, started
        assert len(manager.starts) == 1
        record = manager.starts[0]
        assert record["argv"] == [
            "python3", "/srv/service.py", "serve", "--port", "8080",
        ]
        # the sanctioned route (RunConfig.validate): ports are declared and the
        # network is left at none so the config layer PROMOTES it to bridge —
        # an explicit network next to -p is refused (policy)
        assert record["network"] == "none"
        # the config layer's declared shape: keys are the CONTAINER ports (int)
        assert record["ports"] == {8080: 3400}
        assert record["image_digest"].startswith("sha256:")
        # and the same config DOES promote to bridge with published ports
        # (the real start path runs validate() before docker_argv)
        config = toolbuilder._run_config(
            image=record["image"], image_digest=record["image_digest"],
            argv=record["argv"], port_bindings=record["ports"], network="none",
        )
        assert config.validate() == []
        assert config.network == "bridge"

    def test_a_serving_service_on_a_foreign_network_is_refused(self, runtime, manager):
        """Ports under a non-bridge network (e.g. host) are refused before the run."""
        builder = ContainerToolBuilder(runtime, manager=manager)
        _mined(builder, manager)
        # forge the declaration the way an adapter would carry it
        adapter_path = runtime / "generated_tools" / "market_research_snapshot" / "adapter.json"
        adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
        adapter["service"]["network"] = "host"
        adapter_path.write_text(json.dumps(adapter), encoding="utf-8")

        started = ensure_container_started(
            runtime, "market_research_snapshot", manager=manager
        )

        assert started["started"] is False
        assert "host" in started["reason"]
        assert manager.starts == []

    def test_an_undeclared_service_stays_network_none(self, runtime):
        """The keepalive default is unchanged: warm, isolated, no ports."""
        plain = FakeManager()
        builder = ContainerToolBuilder(runtime, manager=plain)
        builder.mine_container(
            "compute the sha-256 digest of a utf-8 string",
            image="alpine:3.20", name="container_sha256",
        )

        started = ensure_container_started(
            runtime, "container_sha256", manager=plain
        )

        assert started["started"] is True, started
        record = plain.starts[0]
        assert record["network"] == "none"
        assert record["ports"] == {}
