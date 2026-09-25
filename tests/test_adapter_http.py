"""The HTTP/API surface half of the self-expanding tool system (EXPAND — gap A).

The container half mines a capability from a container's CLI verbs; an API-only
surface was *detected* and then refused (exit 3). These tests pin the API half:

* a service's surface is DISCOVERED from a machine-readable document (OpenAPI 3,
  Swagger 2 or a documented endpoint list) — and never invented: no document, no
  adaptation, with the probes as the evidence of the refusal;
* the capability is mapped onto a **documented** operation with typed inputs and
  outputs (no operation matches ⇒ refusal, no guessed endpoint);
* the adapter is TESTED against the REAL service and checked against an
  INDEPENDENT local oracle (hashlib/len, never the service's own answer) — no
  oracle ⇒ refusal instead of a self-certifying pass;
* registration happens only on that evidence, through the existing registration
  path (``ToolStore.publish`` + ``capability_health``) — with version, health,
  permissions and a rollback snapshot;
* a registered adapter REFUSES to call a service whose surface document changed
  after the test (the HTTP counterpart of the image-digest refusal);
* the call path is a real HTTP request, and the generated wrapper is the tool.

The hermetic half runs against a REAL local HTTP service (stdlib
``http.server``) — no docker, no network. The real-container run is the
``GODSV_DOCKER_E2E=1`` test at the bottom: it builds the labelled fixture image
from ``tests/fixtures/http_service/`` (busybox httpd + shell CGI, i.e. an
implementation independent of the Python oracle), adapts its API, registers,
calls, rolls back and then proves that nothing of ours is left running.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from skaldir import adapter_http, adapter_http_cli, cli_contract
from skaldir.adapter_http import (
    HTTP_KIND,
    HttpToolBuilder,
    inspect_http_surface,
    match_http_operation,
    parse_surface_document,
    render_http_request,
    resolve_http_oracle,
    surface_summary,
)
from skaldir.toolbuilder import AdapterRefusal, SurfaceRefusal
from skaldir.tools import STATUS_APPROVED, STATUS_REVOKED

CAPABILITY = "compute the sha-256 digest of a string"
OTHER_CAPABILITY = "rotate the gpg subkeys of a vault"
HTTP_RUNTIME = adapter_http.HTTP_RUNTIME


def _sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _openapi_document(paths: dict, *, title: str = "Test API", version: str = "1.0.0") -> str:
    return json.dumps(
        {
            "openapi": "3.0.3",
            "info": {"title": title, "version": version},
            "paths": paths,
        }
    )


SHA256_PATH = {
    "get": {
        "operationId": "sha256OfText",
        "summary": "Compute the sha-256 digest of a text",
        "description": "Returns the hex sha-256 digest of the text parameter.",
        "parameters": [
            {"name": "text", "in": "query", "required": True, "schema": {"type": "string"}}
        ],
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["sha256"],
                            "properties": {
                                "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}
                            },
                        }
                    }
                }
            }
        },
    }
}

ECHO_PATH = {
    "get": {
        "operationId": "echoText",
        "summary": "Return the text that was sent",
        "parameters": [
            {"name": "text", "in": "query", "required": True, "schema": {"type": "string"}}
        ],
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["echo"],
                            "properties": {"echo": {"type": "string"}},
                        }
                    }
                }
            }
        },
    }
}

TRANSLATE_PATH = {
    "get": {
        "operationId": "translateText",
        "summary": "Translate a text into another language",
        "parameters": [
            {"name": "text", "in": "query", "required": True, "schema": {"type": "string"}}
        ],
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["translation"],
                            "properties": {"translation": {"type": "string"}},
                        }
                    }
                }
            }
        },
    }
}

SHORTEN_PATH = {
    "post": {
        "operationId": "shortenUrl",
        "summary": "Shorten a long url",
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["url"],
                        "properties": {"url": {"type": "string"}},
                    }
                }
            },
        },
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["short"],
                            "properties": {"short": {"type": "string"}},
                        }
                    }
                }
            }
        },
    }
}

DEFAULT_DOC = _openapi_document({"/sha256": SHA256_PATH, "/echo": ECHO_PATH})
NO_ORACLE_DOC = _openapi_document({"/translate": TRANSLATE_PATH})
MUTATING_DOC = _openapi_document({"/shorten": SHORTEN_PATH})
SWAGGER_DOC = json.dumps(
    {
        "swagger": "2.0",
        "info": {"title": "Test API (2.0)", "version": "2.0.0"},
        "paths": {
            "/sha256": {
                "get": {
                    "operationId": "sha256OfText",
                    "summary": "Compute the sha-256 digest of a text",
                    "produces": ["application/json"],
                    "parameters": [
                        {"name": "text", "in": "query", "required": True, "type": "string"}
                    ],
                    "responses": {
                        "200": {
                            "schema": {
                                "type": "object",
                                "required": ["sha256"],
                                "properties": {"sha256": {"type": "string"}},
                            }
                        }
                    },
                }
            }
        },
    }
)
ENDPOINT_LIST_DOC = json.dumps(
    {
        "title": "Documented endpoint list",
        "version": "1.0.0",
        "endpoints": [
            {
                "path": "/sha256",
                "method": "get",
                "operationId": "sha256Entry",
                "summary": "Compute the sha-256 digest of a text (documented list)",
                "inputs": {"text": {"type": "string", "in": "query", "required": True}},
                "outputs": {"sha256": {"type": "string", "required": True}},
                "oracle": "sha256-text",
            }
        ],
    }
)


# --------------------------------------------------------------------------- #
# a REAL local HTTP service (stdlib, no docker, no network)
# --------------------------------------------------------------------------- #


class _Handler(BaseHTTPRequestHandler):
    """The fixture service: a documented API with real answer computation."""

    protocol_version = "HTTP/1.1"

    def _send(self, body: bytes, *, status: int = 200, content_type: str = "application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, *, status: int = 200):
        self._send(json.dumps(payload).encode("utf-8"), status=status)

    def _text_parameter(self, query: str) -> str:
        parsed = urllib.parse.parse_qs(str(query or ""))
        return str((parsed.get("text") or [""])[0])

    def do_GET(self):  # noqa: N802 — the stdlib's handler API
        split = urllib.parse.urlsplit(self.path)
        if split.path == "/openapi.json":
            if not getattr(self.server, "serve_document", True):
                self._json({"detail": "Not Found"}, status=404)
                return
            self._send(self.server.document)
            return
        text = self._text_parameter(split.query)
        if split.path == "/sha256":
            self._json({"sha256": _sha256_of(text)})
            return
        if split.path == "/echo":
            self._json({"echo": text})
            return
        if split.path == "/translate":
            self._json({"translation": text[::-1]})
            return
        self._json({"detail": f"no such endpoint: {split.path}"}, status=404)

    def do_POST(self):  # noqa: N802 — the stdlib's handler API
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            payload = {}
        if urllib.parse.urlsplit(self.path).path == "/shorten":
            self._json({"short": "https://s.example/" + str(payload.get("url", ""))[:4]})
            return
        self._json({"detail": "no such endpoint"}, status=404)

    def log_message(self, *args):  # noqa: A002 — silence the test output
        return


class LocalService:
    """A running HTTP service on a free loopback port (real requests, real bytes)."""

    def __init__(self, document: str, *, serve_document: bool = True) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._server.document = str(document).encode("utf-8")
        self._server.serve_document = bool(serve_document)
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def set_document(self, document: str) -> None:
        """Serve a different document from now on (a changed API)."""
        self._server.document = str(document).encode("utf-8")

    def hide_document(self) -> None:
        self._server.serve_document = False

    def document_sha256(self) -> str:
        return hashlib.sha256(self._server.document).hexdigest()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def service():
    running = LocalService(DEFAULT_DOC)
    try:
        yield running
    finally:
        running.close()


@pytest.fixture
def builder(tmp_path):
    runtime = tmp_path / "rt"
    runtime.mkdir()
    return HttpToolBuilder(runtime)


def _mined(builder, service, **kwargs) -> dict:
    result = builder.mine_http(
        kwargs.pop("capability", CAPABILITY), base_url=service.base_url,
        name=kwargs.pop("name", "http_sha256"), **kwargs,
    )
    assert result["ok"] is True, result
    return result


# --------------------------------------------------------------------------- #
# discovery — evidence, never invention
# --------------------------------------------------------------------------- #


def test_discovery_reads_the_real_document_and_types_every_operation(service):
    surface = inspect_http_surface(service.base_url)

    assert surface["inspected"] is True, surface
    assert surface["kind"] == "openapi3"
    assert surface["title"] == "Test API" and surface["version"] == "1.0.0"
    assert surface["operation_count"] == 2
    assert surface["document_sha256"] == service.document_sha256()
    assert surface["source"] == f"{service.base_url}/openapi.json"
    assert surface["raw_bytes"] > 0 and "Test API" in surface["raw_excerpt"]
    by_id = {operation["id"]: operation for operation in surface["operations"]}
    assert sorted(by_id) == ["echoText", "sha256OfText"]
    assert by_id["sha256OfText"]["method"] == "GET"
    assert by_id["sha256OfText"]["path"] == "/sha256"
    assert by_id["sha256OfText"]["inputs"] == {
        "text": {"type": "string", "in": "query", "required": True}
    }
    assert by_id["sha256OfText"]["outputs"] == {
        "sha256": {"type": "string", "required": True, "pattern": "^[0-9a-f]{64}$"}
    }
    assert by_id["echoText"]["outputs"]["echo"]["type"] == "string"
    # the winning probe answers 200; the probes themselves are the evidence
    probed = [(probe["path"], probe["status"]) for probe in surface["probes"]]
    assert probed[0] == ("/openapi.json", 200), probed


def test_a_swagger_2_document_is_understood_too(service):
    document = parse_surface_document(SWAGGER_DOC.encode("utf-8"), source="swagger2")

    assert document["kind"] == "swagger2"
    operation = document["operations"][0]
    assert operation["path"] == "/sha256" and operation["method"] == "GET"
    assert operation["inputs"]["text"] == {"type": "string", "in": "query", "required": True}
    assert operation["outputs"]["sha256"]["type"] == "string"


def test_a_documented_endpoint_list_is_a_machine_readable_surface(tmp_path):
    path = tmp_path / "endpoints.json"
    path.write_text(ENDPOINT_LIST_DOC, encoding="utf-8")

    surface = inspect_http_surface("", document=str(path))

    assert surface["inspected"] is True, surface
    assert surface["kind"] == "endpoint-list"
    assert surface["document_url"] == ""  # a file cannot be re-read at call time
    assert surface["operations"][0]["id"] == "sha256Entry"
    assert surface["operations"][0]["oracle"] == "sha256-text"


def test_a_service_without_a_document_is_refused_with_its_probes():
    """The probes ARE the evidence: which paths answered, with which status."""
    offline = LocalService(DEFAULT_DOC, serve_document=False)
    try:
        surface = inspect_http_surface(offline.base_url, timeout=2.0)
    finally:
        offline.close()

    assert surface["inspected"] is False
    assert surface["failure_class"] == "surface_unavailable"
    assert surface["operations"] == []
    assert len(surface["probes"]) == len(adapter_http.HTTP_DOCUMENT_PROBES)
    assert {probe["status"] for probe in surface["probes"]} == {404}
    assert "no machine-readable surface document answered" in surface["reason"]
    assert "/openapi.json → HTTP 404" in surface["reason"]


def test_an_unreachable_service_is_refused_without_inventing_endpoints():
    surface = inspect_http_surface("http://127.0.0.1:9", timeout=1.0)

    assert surface["inspected"] is False
    assert surface["failure_class"] == "surface_unavailable"
    assert surface["operations"] == []
    assert all(probe["status"] == 0 for probe in surface["probes"])


def test_a_service_without_a_document_is_never_adapted(builder):
    offline = LocalService(DEFAULT_DOC, serve_document=False)
    try:
        result = builder.mine_http(CAPABILITY, base_url=offline.base_url, name="http_offline")
    finally:
        offline.close()

    assert result["ok"] is False
    assert result["failure_class"] == "surface_unavailable"
    assert result["staged"] is False
    assert not (builder.tools_dir / "http_offline").exists()
    assert [stage["stage"] for stage in result["stages"]] == ["search", "resolve_service",
                                                             "inspect_surface"]


def test_a_capability_that_maps_onto_no_documented_operation_is_refused(builder, service):
    result = builder.mine_http(OTHER_CAPABILITY, base_url=service.base_url, name="http_gpg")

    assert result["ok"] is False
    assert result["failure_class"] == "adapter_refused"
    assert "no operation for" in result["reason"]
    assert "GET /sha256" in result["reason"]  # the documented operations are named
    assert not (builder.tools_dir / "http_gpg").exists()


def test_an_ambiguous_capability_is_refused_instead_of_guessed():
    """Two documented operations with the same score ⇒ refuse, name the ids."""
    operations = [
        {"id": "hashOne", "method": "GET", "path": "/one", "summary": "hash a text",
         "description": "", "tags": [], "inputs": {}, "outputs": {}},
        {"id": "hashTwo", "method": "GET", "path": "/two", "summary": "hash a text",
         "description": "", "tags": [], "inputs": {}, "outputs": {}},
    ]

    with pytest.raises(AdapterRefusal) as caught:
        match_http_operation("hash a text", operations)

    assert "ambiguous" in str(caught.value)
    assert "hashOne" in str(caught.value) and "hashTwo" in str(caught.value)


def test_an_unknown_operation_id_is_a_refusal_not_a_fallback(service):
    with pytest.raises(AdapterRefusal) as caught:
        match_http_operation(
            CAPABILITY,
            list(inspect_http_surface(service.base_url)["operations"]),
            operation_id="doesNotExist",
        )

    assert "no operation 'doesNotExist'" in str(caught.value)


# --------------------------------------------------------------------------- #
# the typed adapter + the real test against an INDEPENDENT oracle
# --------------------------------------------------------------------------- #


def test_mine_stages_a_typed_adapter_tested_against_the_real_service(builder, service):
    result = _mined(builder, service)

    assert [stage["stage"] for stage in result["stages"]] == [
        "search", "resolve_service", "inspect_surface", "match_operation",
        "generate_adapter", "test_adapter", "stage",
    ]
    assert all(stage["ok"] for stage in result["stages"]), result["stages"]
    adapter = result["adapter"]
    assert adapter["kind"] == HTTP_KIND and adapter["runtime"] == "http"
    assert adapter["operation"]["id"] == "sha256OfText"
    assert adapter["operation"]["method"] == "GET" and adapter["operation"]["path"] == "/sha256"
    assert adapter["operation"]["inputs"] == {
        "text": {"type": "string", "in": "query", "required": True}
    }
    assert set(adapter["operation"]["outputs"]) == {"sha256"}
    # the pin is the document that was really served, and the host is recorded
    assert adapter["pin"]["document_sha256"] == service.document_sha256()
    assert adapter["pin"]["document_url"] == f"{service.base_url}/openapi.json"
    assert adapter["permissions"]["allowed_hosts"] == [
        urllib.parse.urlsplit(service.base_url).netloc
    ]
    assert adapter["permissions"]["methods"] == ["GET"]
    assert adapter["permissions"]["mounts"] == [] and adapter["permissions"]["secrets"] == []
    # the test ran against the real service and an INDEPENDENT oracle
    test = result["test"]
    assert test["ok"] is True
    assert test["oracle"] == "sha256-text"
    assert test["observed"] == test["expected"] == _sha256_of("skaldir")
    assert test["status"] == 200
    assert "hashlib" in test["oracle_detail"]
    # the staged evidence is bound to the adapter BYTES
    evidence = json.loads(Path(result["evidence_path"]).read_text(encoding="utf-8"))
    assert evidence["adapter_digest"] == hashlib.sha256(
        Path(result["adapter_path"]).read_bytes()
    ).hexdigest()
    assert evidence["registered"] is False
    assert json.loads(Path(result["adapter_path"]).read_text(encoding="utf-8")) == adapter
    surface_record = json.loads(
        (Path(result["tool_dir"]) / "surface.json").read_text(encoding="utf-8")
    )
    assert surface_record["document_sha256"] == service.document_sha256()


def test_the_declared_expectation_must_agree_with_the_local_oracle(builder, service):
    result = builder.mine_http(
        CAPABILITY, base_url=service.base_url, name="http_lie", expect="deadbeef"
    )

    assert result["ok"] is False and result["failure_class"] == "test_failed"
    assert "disagrees with the independent local oracle" in result["reason"]
    assert not (builder.tools_dir / "http_lie").exists()


def test_an_operation_without_an_independent_oracle_is_refused(builder):
    """No local oracle ⇒ no test ⇒ no capability: never a self-certifying pass."""
    service = LocalService(NO_ORACLE_DOC)
    try:
        result = builder.mine_http(
            "translate a text into another language", base_url=service.base_url,
            name="http_translate",
        )
    finally:
        service.close()

    assert result["ok"] is False
    assert result["failure_class"] == "test_failed"
    assert "no independent local oracle matches operation" in result["reason"]
    assert "sha256-text" in result["reason"]  # the known oracles are named
    assert not (builder.tools_dir / "http_translate").exists()


def test_a_documented_mutating_operation_needs_an_explicit_acknowledgement(builder):
    service = LocalService(MUTATING_DOC)
    try:
        refused = builder.mine_http(
            "shorten a long url", base_url=service.base_url, name="http_shorten"
        )
        assert refused["ok"] is False
        assert refused["failure_class"] == "mutating_operation"
        assert "POST" in refused["reason"] and "--allow-mutating" in refused["reason"]
        assert not (builder.tools_dir / "http_shorten").exists()
    finally:
        service.close()


def test_the_oracle_can_be_named_explicitly(builder):
    service = LocalService(DEFAULT_DOC)
    try:
        result = builder.mine_http(
            "return the text that was sent", base_url=service.base_url,
            name="http_echo", oracle="echo-text",
        )
    finally:
        service.close()

    assert result["ok"] is True, result
    assert result["adapter"]["operation"]["id"] == "echoText"
    assert result["test"]["oracle"] == "echo-text"
    assert result["test"]["observed"] == "skaldir"


def test_the_oracle_is_resolved_from_the_document_extension():
    adapter = {
        "operation": {
            "id": "whatever", "path": "/x", "summary": "", "description": "",
            "outputs": {"value": {"type": "string"}}, "oracle": "echo-text",
        }
    }

    chosen = resolve_http_oracle(adapter)

    assert chosen["id"] == "echo-text"


def test_an_unknown_oracle_name_is_refused():
    adapter = {"operation": {"id": "x", "outputs": {"v": {"type": "string"}}}}

    with pytest.raises(AdapterRefusal) as caught:
        resolve_http_oracle(adapter, oracle="nope")

    assert "no local oracle named 'nope'" in str(caught.value)


def test_rendering_validates_inputs_and_refuses_unknown_ones(builder, service):
    adapter = _mined(builder, service)["adapter"]

    request = render_http_request(adapter, {"text": "skaldir"})
    assert request["method"] == "GET"
    assert request["url"] == f"{service.base_url}/sha256?text=skaldir"
    assert request["query"] == {"text": "skaldir"}
    with pytest.raises(AdapterRefusal) as missing:
        render_http_request(adapter, {})
    assert "missing required input: text" in str(missing.value)
    with pytest.raises(AdapterRefusal) as unknown:
        render_http_request(adapter, {"text": "a", "nope": 1})
    assert "unknown input(s)" in str(unknown.value)


def test_the_surface_refusal_is_structured_when_there_is_nothing_to_adapt():
    surface = {"inspected": False, "reason": "no document"}

    with pytest.raises(SurfaceRefusal) as caught:
        adapter_http.generate_http_adapter(
            CAPABILITY, name="x", service={"base_url": "http://127.0.0.1:1"},
            surface=surface, operation={},
        )

    assert "no inspected surface" in str(caught.value)


# --------------------------------------------------------------------------- #
# registration — the existing path, on evidence only
# --------------------------------------------------------------------------- #


def test_registration_refuses_an_adapter_without_test_evidence(builder, service):
    result = _mined(builder, service)
    Path(result["evidence_path"]).unlink()

    registered = builder.register_http_tool("http_sha256")

    assert registered["ok"] is False
    assert registered["failure_class"] == "unverified"
    assert "no test evidence" in registered["reason"]
    assert builder.store.get("http_sha256") is None


def test_registration_refuses_an_adapter_that_changed_after_the_test(builder, service):
    result = _mined(builder, service)
    adapter_path = Path(result["adapter_path"])
    adapter_path.write_text(
        adapter_path.read_text(encoding="utf-8").replace('"timeout_seconds": 30',
                                                          '"timeout_seconds": 60'),
        encoding="utf-8",
    )

    registered = builder.register_http_tool("http_sha256")

    assert registered["ok"] is False
    assert registered["failure_class"] == "adapter_changed"
    assert "changed after the test" in registered["reason"]
    assert builder.store.get("http_sha256") is None


def test_registration_publishes_version_health_permissions_and_a_rollback(builder, service):
    _mined(builder, service)

    registered = builder.register_http_tool("http_sha256", project_id="path:/tmp/project")

    assert registered["ok"] is True and registered["registered"] is True
    assert registered["version"] == "0.1.0" and registered["status"] == STATUS_APPROVED
    assert registered["kind"] == HTTP_KIND and registered["runtime"] == "http"
    assert registered["health_state"] == "HEALTHY"
    assert registered["rollback_id"]
    assert registered["publish"]["name"] == "http_sha256"
    assert registered["permissions"]["allowed_hosts"] == [
        urllib.parse.urlsplit(service.base_url).netloc
    ]
    assert registered["test"]["observed"] == registered["test"]["expected"] == _sha256_of("skaldir")
    # the registry row is the existing store, with the http kind
    spec = builder.store.get("http_sha256")
    assert spec is not None and spec.kind == HTTP_KIND and spec.version == "0.1.0"
    assert spec.status == STATUS_APPROVED
    # …and the manifest is the contract the discovery index reads
    manifest = json.loads(json.dumps(builder.http_adapter("http_sha256")["manifest"]))
    assert manifest["runtime"] == "http" and manifest["status"] == "ready"
    assert manifest["validation"]["status"] == "validated"
    assert manifest["type"] == "http"
    assert manifest["network"]["allowed_domains"] == [
        urllib.parse.urlsplit(service.base_url).netloc
    ]
    assert manifest["healthcheck"]["path"] == "/sha256"
    evidence = json.loads(Path(registered["wrapper"]).parent.parent.joinpath(
        adapter_http.HTTP_EVIDENCE_FILE
    ).read_text(encoding="utf-8"))
    assert evidence["registered"] is True and evidence["version"] == "0.1.0"


def test_re_registration_bumps_the_patch_version(builder, service):
    _mined(builder, service)
    first = builder.register_http_tool("http_sha256")
    second = builder.register_http_tool("http_sha256")

    assert first["version"] == "0.1.0"
    assert second["ok"] is True and second["version"] == "0.1.1"
    assert json.loads(Path(second["wrapper"]).parent.parent.joinpath(
        adapter_http.HTTP_EVIDENCE_FILE
    ).read_text(encoding="utf-8"))["rollback_id"]


def test_an_unknown_capability_is_a_not_found(builder):
    report = builder.http_adapter("nothing_here")

    assert report["ok"] is False and report["failure_class"] == "not_found"
    assert builder.register_http_tool("nothing_here")["failure_class"] == "not_found"


# --------------------------------------------------------------------------- #
# calling — real request, pinned surface, declared outputs
# --------------------------------------------------------------------------- #


def test_call_returns_the_services_answer_and_the_oracle_agrees(builder, service):
    _mined(builder, service)
    builder.register_http_tool("http_sha256")

    call = builder.call_http_tool("http_sha256", {"text": "skaldir"})

    assert call["ok"] is True, call
    assert call["output"] == {"sha256": _sha256_of("skaldir")}
    assert call["status"] == 200
    assert call["request"]["url"] == f"{service.base_url}/sha256?text=skaldir"
    assert call["pin"]["checked"] is True and call["pin"]["ok"] is True


def test_call_refuses_a_service_whose_surface_changed_after_the_test(builder, service):
    _mined(builder, service)
    builder.register_http_tool("http_sha256")
    service.set_document(_openapi_document({"/sha256": SHA256_PATH, "/new": ECHO_PATH}))

    call = builder.call_http_tool("http_sha256", {"text": "skaldir"})

    assert call["ok"] is False
    assert call["failure_class"] == "surface_changed"
    assert call["pin"]["pinned"] != call["pin"]["live"]
    assert "re-discover" in call["reason"]


def test_call_refuses_a_service_that_stopped_answering(builder, service):
    _mined(builder, service)
    builder.register_http_tool("http_sha256")
    service.close()

    call = builder.call_http_tool("http_sha256", {"text": "skaldir"})

    assert call["ok"] is False
    assert call["failure_class"] in ("surface_changed", "service_unavailable")


def test_call_refuses_inputs_that_the_documented_operation_does_not_have(builder, service):
    _mined(builder, service)

    call = builder.call_http_tool("http_sha256", {"text": "a", "extra": 1})

    assert call["ok"] is False and call["failure_class"] == "adapter_refused"
    assert "unknown input(s)" in call["reason"]


def test_call_parses_the_declared_output_schema(builder, service):
    """A body that does not satisfy the documented schema is not a success."""
    _mined(builder, service)
    builder.register_http_tool("http_sha256")
    # the service now answers with the wrong shape (document unchanged)
    original = _Handler.do_GET

    def broken(self):  # noqa: ANN001 — a monkeypatched handler
        if urllib.parse.urlsplit(self.path).path == "/sha256":
            self._json({"sha256": 12345})
            return
        return original(self)

    _Handler.do_GET = broken
    try:
        call = builder.call_http_tool("http_sha256", {"text": "skaldir"})
    finally:
        _Handler.do_GET = original

    assert call["ok"] is False
    assert "sha256" in call["reason"] and "string" in call["reason"]


def test_the_generated_wrapper_is_the_call_path(builder, service):
    _mined(builder, service)
    builder.register_http_tool("http_sha256")
    wrapper = Path(builder.http_adapter("http_sha256")["wrapper"])

    proc = subprocess.run(
        [str(wrapper), json.dumps({"text": "skaldir"})],
        capture_output=True, text=True, timeout=60,
    )

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"sha256": _sha256_of("skaldir")}
    # the wrapper's usage ledger lives in the runtime dir (the container half's layout)
    usage = (Path(wrapper).parents[3] / "tool_usage.jsonl").read_text(encoding="utf-8")
    assert '"name": "http_sha256"' in usage and '"ok": true' in usage


def test_health_reports_the_ledger_the_service_and_the_pin(builder, service):
    _mined(builder, service)
    builder.register_http_tool("http_sha256")

    report = builder.http_health("http_sha256", probe=True)

    assert report["ok"] is True
    assert report["health"]["state"] == "HEALTHY" and report["selectable"] is True
    assert report["registered"] is True and report["version"] == "0.1.0"
    assert report["service"]["mode"] == "external"
    assert report["service"]["reachable"] is True
    assert report["pin"]["ok"] is True
    assert report["probe"]["ok"] is True
    assert report["permissions"]["allowed_hosts"] == [
        urllib.parse.urlsplit(service.base_url).netloc
    ]


def test_rollback_quarantines_the_tool_and_retires_the_health(builder, service):
    result = _mined(builder, service)
    builder.register_http_tool("http_sha256")

    rolled = builder.rollback_http_tool("http_sha256", reason="test rollback")

    assert rolled["ok"] is True and rolled["rolled_back"] is True
    assert rolled["runtime"] == HTTP_RUNTIME
    assert rolled["removed_registration"] is True
    assert rolled["containers_removed"] == []  # an external service: nothing of ours runs
    assert not Path(result["tool_dir"]).exists()
    assert Path(rolled["quarantine"]).is_dir()
    assert rolled["health"]["state"] == "RETIRED"
    spec = builder.store.get("http_sha256")
    assert spec is not None and spec.status == STATUS_REVOKED


def test_the_builder_holds_no_second_registry():
    """The registration path is the existing one — a grep guard pins it."""
    source = Path(adapter_http.__file__).read_text(encoding="utf-8")
    assert "self.store.publish(" in source, "the registry commit goes through ToolStore"
    assert "_health.record_verification(" in source, "the health verdict is the ledger's"
    assert "_health.retire(" not in source or "rollback_container_tool" in source
    for forbidden in ("sqlite3", "INSERT INTO", "ToolStore(", "capability_health.jsonl"):
        assert forbidden not in source, f"a second registry appeared: {forbidden}"


def test_the_registered_http_capability_is_found_by_the_mining_path(builder, service):
    """A registered API capability behaves like every other capability: findable."""
    from skaldir import discovery
    from skaldir.capability import project_id_for
    from skaldir.capability_mining import search_before_build, search_existing

    work = builder.runtime.parent / "project"
    work.mkdir(exist_ok=True)
    _mined(builder, service)
    builder.register_http_tool("http_sha256", project_id=project_id_for(work))

    found = search_existing(builder.runtime, CAPABILITY)

    assert found["matches"], found
    top = found["matches"][0]
    assert top["name"] == "http_sha256"
    assert top["surface"] == "generated_tools"
    assert top["purpose"] == CAPABILITY
    verdict = search_before_build(builder.runtime, "compute the sha256 digest for a text",
                                  name="another_tool")
    assert verdict["decision"] == "reuse"
    assert verdict["match"]["name"] == "http_sha256"
    ranked = discovery.discover(CAPABILITY, runtime=builder.runtime,
                                project_id=project_id_for(work))
    assert ranked["executable_count"] == 1, ranked
    row = ranked["results"][0]
    assert row["name"] == "http_sha256"
    assert row["state"] == discovery.STATE_READY and row["executable"] is True
    assert row["version"] == "0.1.0" and row["validation"] == "validated"


def test_the_surface_summary_carries_the_probes_for_the_envelope(service):
    summary = surface_summary(inspect_http_surface(service.base_url),
                              operation_id="sha256OfText")

    assert summary["inspected"] is True and summary["operation_count"] == 2
    assert summary["matched"]["operation"] == {
        "id": "sha256OfText", "method": "GET", "path": "/sha256"
    }
    assert summary["probes"] and summary["probes"][0]["status"] == 200
    assert "Test API" in summary["excerpt"]


# --------------------------------------------------------------------------- #
# the CLI surface (parser + handlers through the real envelope)
# --------------------------------------------------------------------------- #


def test_register_cli_adds_the_api_commands_to_the_tools_group():
    parser = argparse.ArgumentParser(prog="skaldir")
    sub = parser.add_subparsers(dest="command")
    tools = sub.add_parser("tools", help="tool-suite utilities")
    tools_sub = tools.add_subparsers(dest="subcommand")

    adapter_http_cli.register_cli(tools_sub)

    api = tools_sub.choices["api"]
    subparser_actions = [
        action for action in api._actions if isinstance(action, argparse._SubParsersAction)
    ]
    api_sub = subparser_actions[0]
    assert sorted(api_sub.choices) == [
        "call", "discover", "health", "inspect", "register", "rollback", "show"
    ]
    parsed = parser.parse_args(
        ["tools", "api", "call", "http_sha256", '{"text": "skaldir"}']
    )
    assert parsed.name == "http_sha256" and parsed.payload == '{"text": "skaldir"}'
    assert parsed.func is adapter_http_cli._cmd_api_call


def test_the_cli_discover_exits_3_when_no_surface_exists(monkeypatch, capsys):
    class _Refusing:
        def mine_http(self, capability, **kwargs):
            return {
                "ok": False, "name": "http_x", "capability": capability,
                "failure_class": "surface_unavailable",
                "reason": "no machine-readable surface document answered on http://x",
                "surface": {"inspected": False, "probes": [
                    {"url": "http://x/openapi.json", "path": "/openapi.json", "status": 404,
                     "bytes": 0, "ok": False, "note": "HTTP 404"}
                ]},
                "stages": [{"stage": "inspect_surface", "ok": False, "seconds": 0.1}],
            }

    monkeypatch.setattr(adapter_http_cli, "_builder", lambda _args: _Refusing())
    namespace = argparse.Namespace(
        command_path="tools.api.discover", capability=CAPABILITY, base_url="http://x",
        json=True, quiet=True,
    )

    code = cli_contract.run_command(namespace, adapter_http_cli._cmd_api_discover, json_mode=True)
    envelope = json.loads(capsys.readouterr().out)

    assert code == cli_contract.EXIT_BLOCKED
    assert envelope["ok"] is False and envelope["exit_code"] == 3
    assert envelope["error"]["code"] == "blocked"
    assert "no machine-readable surface" in envelope["error"]["message"]
    assert any(item["ref"] == "http://x/openapi.json" for item in envelope["evidence"])


def test_the_cli_call_reports_the_real_request_as_evidence(monkeypatch, capsys):
    class _Calling:
        def call_http_tool(self, name, payload):
            return {
                "ok": True, "name": name, "mode": "external",
                "base_url": "http://127.0.0.1:8080", "input": payload,
                "output": {"sha256": _sha256_of("skaldir")},
                "request": {"method": "GET", "url": "http://127.0.0.1:8080/sha256?text=skaldir",
                            "query": {"text": "skaldir"}},
                "status": 200, "body_excerpt": "{}", "pin": {"checked": True, "ok": True,
                                                             "live": "a" * 64, "reason": "matches"},
                "service": {}, "failure_class": "", "reason": "", "duration_ms": 7,
            }

    monkeypatch.setattr(adapter_http_cli, "_builder", lambda _args: _Calling())
    namespace = argparse.Namespace(
        command_path="tools.api.call", name="http_sha256",
        payload=json.dumps({"text": "skaldir"}), input="", json=True, quiet=True,
    )

    code = cli_contract.run_command(namespace, adapter_http_cli._cmd_api_call, json_mode=True)
    envelope = json.loads(capsys.readouterr().out)

    assert code == cli_contract.EXIT_OK, envelope
    assert envelope["ok"] is True
    assert envelope["data"]["output"] == {"sha256": _sha256_of("skaldir")}
    runs = [item for item in envelope["evidence"] if item["kind"] == "run"]
    assert runs and runs[0]["ref"] == "GET http://127.0.0.1:8080/sha256?text=skaldir"


def test_every_new_command_is_declared_for_the_contract_dump():
    listed = " ".join(adapter_http_cli.EV_NEW_COMMANDS)
    for command in ("tools.api.inspect", "tools.api.discover", "tools.api.show",
                    "tools.api.register", "tools.api.call", "tools.api.health",
                    "tools.api.rollback"):
        assert command in listed, command


def test_the_cli_registration_is_the_documented_one_liner():
    """The snippet in the module docstring is the snippet cli.py needs."""
    source = Path(adapter_http_cli.__file__).read_text(encoding="utf-8")
    assert "from .adapter_http_cli import register_cli as _register_http_api" in source
    assert "_register_http_api(tools_sub)" in source


# --------------------------------------------------------------------------- #
# the real container (opt-in: GODSV_DOCKER_E2E=1)
# --------------------------------------------------------------------------- #

E2E = os.environ.get("GODSV_DOCKER_E2E", "").strip() not in ("", "0", "false")
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "http_service"


def _docker(*argv: str, timeout: float = 120.0) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *argv], capture_output=True, text=True, timeout=timeout)


def _containers_by_label(labels: dict[str, str]) -> list[str]:
    argv = ["ps", "-a"]
    for key, value in labels.items():
        argv += ["--filter", f"label={key}={value}"]
    argv += ["--format", "{{.ID}}"]
    listed = _docker(*argv)
    return [line for line in listed.stdout.split() if line]


def _containers_of_image(image: str) -> list[str]:
    """Every container created from an image — ours by construction, this test's."""
    listed = _docker("ps", "-a", "--filter", f"ancestor={image}", "--format", "{{.ID}}")
    return [line for line in listed.stdout.split() if line]


@pytest.mark.skipif(not E2E, reason="set GODSV_DOCKER_E2E=1 to run against the real daemon")
@pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not installed")
def test_e2e_http_capability_from_a_real_container(tmp_path):
    """The whole chain on a REAL service container — and nothing left behind."""
    from skaldir.docker_runtime import DockerRuntimeManager

    manager = DockerRuntimeManager()
    available, detail = manager.available()
    if not available:
        pytest.skip(f"docker is not reachable: {detail}")
    base_image = os.environ.get("GODSV_E2E_HTTP_BASE_IMAGE", "busybox:1.36")
    if not manager.resolve_digest(base_image):
        pytest.skip(f"base image {base_image} is not local (no pull in a test)")

    foreign_before = _containers_by_label({"com.docker.compose.project": "penpot-local"})
    image = "godsv-http-fixture:e2e"
    built = _docker("build", "-q", "-t", image, str(FIXTURE_DIR), timeout=300.0)
    assert built.returncode == 0, built.stderr
    runtime = tmp_path / "rt"
    runtime.mkdir()
    builder = HttpToolBuilder(runtime, manager=manager)
    name = "e2e_http_sha256"
    try:
        mined = builder.mine_http(
            CAPABILITY, image=image, container_port=8080,
            service_argv=["httpd", "-f", "-p", "8080", "-h", "/www"], name=name,
        )
        assert mined["ok"] is True, mined
        assert mined["surface"]["kind"] == "openapi3"
        assert mined["test"]["observed"] == mined["test"]["expected"] == _sha256_of("skaldir")
        assert mined["service"]["mode"] == "container"
        # the service container exists for the test only
        assert _containers_by_label({"godsv.capability": name}) == []

        registered = builder.register_http_tool(name)
        assert registered["ok"] is True and registered["health_state"] == "HEALTHY"

        call = builder.call_http_tool(name, {"text": "skaldir"})
        assert call["ok"] is True, call
        assert call["output"] == {"sha256": _sha256_of("skaldir")}
        assert call["mode"] == "container" and call["service"]["started"] is True
        running = _containers_by_label({"godsv.capability": name})
        assert len(running) == 1, running

        rolled = builder.rollback_http_tool(name, reason="e2e")
        assert rolled["ok"] is True and rolled["removed_registration"] is True
        # nothing of ours is left behind: no container of this capability, none of
        # our image (the container half's teardown is label-scoped — never by name)
        assert _containers_by_label({"godsv.capability": name}) == []
        assert _containers_of_image(image) == []
    finally:
        builder._teardown_service(name)
        _docker("rmi", "-f", image)

    # the foreign penpot-local stack (7 containers) is untouched by every step
    assert _containers_by_label({"com.docker.compose.project": "penpot-local"}) == foreign_before
