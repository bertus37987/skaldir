"""W2 — general container → tool templates: more families + the declared-fixture path.

Evidence class: unit (hermetic — no docker). The five original families stay
pinned by ``test_toolbuilder_container`` / ``test_toolbuilder_argument_templates``;
this file owns the **general CLI** catalogue:

* text families with an independent host twin (tr/wc/sort/uniq/cut/basename/jq);
* ``oracle_fixture`` — a golden expectation for ONE sample, refusing every other
  payload (never "the container agrees with itself");
* ``load_schema_template`` — a JSON declaration becomes a real template used by
  ``generate_container_adapter`` and ``mine_container``.

The honesty ladder is pinned: a fixture labels itself as NOT a host twin, and a
schema without an oracle is refused.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skaldir import container_cli_templates as C
from skaldir.container_adapter import AdapterRefusal
from skaldir.toolbuilder import (
    VERB_TEMPLATES,
    expected_outputs,
    generate_container_adapter,
    match_verb_template,
    parse_container_stdout,
    render_container_argv,
)

DIGEST = "sha256:" + "d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc"


def _surface(*, verbs: list[str], image: str = "busybox:1.36") -> dict:
    return {
        "inspected": True,
        "failure_class": "",
        "image": image,
        "image_digest": DIGEST,
        "entrypoint": [],
        "entrypoint_bypass": False,
        "os": {"PRETTY_NAME": "synthetic"},
        "cli": {"verbs": sorted(verbs), "count": len(verbs), "probe": ["sh", "-c", "…"]},
        "api": {"server_hint": "", "paths": [], "note": ""},
        "mcp": {"executable": "", "note": ""},
        "probes": [],
        "inspected_at": 1.0,
    }


# --------------------------------------------------------------------------- #
# catalogue breadth                                                             #
# --------------------------------------------------------------------------- #


def test_the_catalogue_offers_at_least_ten_verb_families() -> None:
    """W2 acceptance: N≥10 distinct families (was 3, then 5)."""
    ids = [entry["id"] for entry in VERB_TEMPLATES]
    assert len(ids) >= 10, ids
    assert len(set(ids)) == len(ids), "template ids must be unique"
    for needle in (
        "sha256-string",
        "md5-string",
        "base64-string",
        "imagemagick-resize",
        "ffmpeg-audio-range",
        "tr-upper",
        "wc-chars",
        "wc-lines",
        "sort-lines",
        "uniq-lines",
        "cut-first-field",
        "basename-path",
        "jq-field",
    ):
        assert needle in ids, f"missing family {needle}"


@pytest.mark.parametrize(
    ("capability", "verbs", "want_id", "want_verb"),
    [
        ("UPPERCASE this text", ["tr", "sh"], "tr-upper", "tr"),
        ("count characters in a string", ["wc", "sh"], "wc-chars", "wc"),
        ("count lines of a string", ["wc", "sh"], "wc-lines", "wc"),
        ("sort the lines", ["sort", "sh"], "sort-lines", "sort"),
        ("dedupe consecutive lines", ["uniq", "sh"], "uniq-lines", "uniq"),
        ("basename of a path", ["basename", "sh"], "basename-path", "basename"),
        ("extract a json field", ["jq", "sh"], "jq-field", "jq"),
    ],
)
def test_a_text_family_matches_by_terms_against_a_real_surface_verb(
    capability: str, verbs: list[str], want_id: str, want_verb: str
) -> None:
    chosen = match_verb_template(capability, verbs)
    assert chosen["id"] == want_id
    assert chosen["verb"] == want_verb


def test_wc_disambiguates_chars_from_lines_by_the_capability_text() -> None:
    assert match_verb_template("how many lines", ["wc"])["id"] == "wc-lines"
    assert match_verb_template("character count", ["wc"])["id"] == "wc-chars"


def test_refusal_names_the_schema_escape_hatch() -> None:
    with pytest.raises(AdapterRefusal) as failure:
        match_verb_template("compress a directory into a tar archive", ["tar", "gzip"])
    assert "--schema" in str(failure.value)
    assert "refused instead of inventing" in str(failure.value)


# --------------------------------------------------------------------------- #
# host-twin oracles (independent of the container)                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("template_id", "payload", "want"),
    [
        ("tr-upper", {"text": "skaldir"}, "SKALDIR"),
        ("wc-chars", {"text": "skaldir"}, "7"),
        ("wc-lines", {"text": "a\nb\nc\n"}, "3"),
        ("sort-lines", {"text": "b\na\nc"}, "a\nb\nc"),
        ("uniq-lines", {"text": "a\na\nb"}, "a\nb"),
        ("basename-path", {"text": "/tmp/gv/file.txt"}, "file.txt"),
        ("jq-field", {"text": '{"value": "skaldir"}', "field": "value"}, "skaldir"),
    ],
)
def test_every_text_family_has_an_independent_host_twin(
    template_id: str, payload: dict, want: str
) -> None:
    entry = next(item for item in VERB_TEMPLATES if item["id"] == template_id)
    expectations = entry["oracle"](dict(payload))
    assert list(expectations["values"].values()) == [want]
    assert "host twin" in expectations["note"]


def test_the_adapter_renders_and_parses_a_text_family() -> None:
    surface = _surface(verbs=["tr", "sh", "base64"])
    adapter = generate_container_adapter(
        "UPPERCASE a string",
        name="gv_upper",
        image="busybox:1.36",
        image_digest=DIGEST,
        surface=surface,
    )
    assert adapter["template_id"] == "tr-upper"
    argv = render_container_argv(adapter, {"text": "skaldir"})
    assert argv[0] == "sh" and "tr" in argv[2]
    ok, parsed, detail = parse_container_stdout(adapter, "SKALDIR\n")
    assert ok, detail
    assert parsed["upper"] == "SKALDIR"


def test_expected_outputs_carries_the_host_twin_label() -> None:
    entry = next(item for item in VERB_TEMPLATES if item["id"] == "tr-upper")
    surface = _surface(verbs=["tr", "sh"])
    adapter = generate_container_adapter(
        "UPPERCASE a string",
        name="gv_upper",
        image="busybox:1.36",
        image_digest=DIGEST,
        surface=surface,
    )
    expectations = expected_outputs(adapter, {"text": "ab"}, template=entry)
    assert expectations["values"] == {"upper": "AB"}
    assert "host twin" in expectations["note"]


# --------------------------------------------------------------------------- #
# declared-fixture path (no host twin)                                         #
# --------------------------------------------------------------------------- #


def test_fixture_oracle_certifies_the_sample_and_refuses_anything_else() -> None:
    oracle = C.oracle_fixture({"text": "# Hi"}, {"html": "<h1>Hi</h1>"})
    result = oracle({"text": "# Hi"})
    assert result["values"] == {"html": "<h1>Hi</h1>"}
    assert "NOT a host twin" in result["note"]
    with pytest.raises(AdapterRefusal) as failure:
        oracle({"text": "# Other"})
    assert "sample input only" in str(failure.value)


def test_load_schema_template_builds_a_usable_fixture_template(tmp_path: Path) -> None:
    schema = {
        "id": "pandoc-md-html",
        "terms": ["pandoc", "markdown", "html", "convert documents"],
        "verb": "pandoc",
        "argv_template": ["sh", "-c", 'printf %s "$0" | pandoc -f markdown -t html', "{text}"],
        "inputs": {"text": {"type": "string", "required": True}},
        "outputs": {"html": {"type": "string", "from": "stdout", "pattern": "^<"}},
        "sample_input": {"text": "# Hi"},
        "oracle": {
            "kind": "fixture",
            "sample_input": {"text": "# Hi"},
            "expected": {"html": "<h1 id=\"hi\">Hi</h1>"},
        },
    }
    path = tmp_path / "pandoc.json"
    path.write_text(json.dumps(schema), encoding="utf-8")
    template = C.load_schema_template(path)
    assert template["id"] == "pandoc-md-html"
    assert template["verb"] == "pandoc"

    surface = _surface(verbs=["pandoc", "sh"])
    adapter = generate_container_adapter(
        "convert documents markdown to html",
        name="gv_pandoc",
        image="pandoc/minimal:3.1.13",
        image_digest=DIGEST,
        surface=surface,
        template=template,
    )
    assert adapter["template_id"] == "pandoc-md-html"
    assert adapter["operation"]["outputs"]["html"]["type"] == "string"
    expectations = expected_outputs(adapter, {"text": "# Hi"}, template=template)
    assert expectations["values"]["html"].startswith("<h1")


def test_schema_without_an_oracle_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "bare.json"
    path.write_text(
        json.dumps(
            {
                "id": "bare",
                "verb": "foo",
                "argv_template": ["foo", "{text}"],
                "inputs": {"text": {"type": "string"}},
                "outputs": {"value": {"type": "string", "from": "stdout"}},
                "sample_input": {"text": "x"},
                "oracle": {"kind": "fixture", "expected": {}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(AdapterRefusal) as failure:
        C.load_schema_template(path)
    assert "oracle.expected" in str(failure.value)


def test_schema_with_host_twin_kind_is_refused_as_json(tmp_path: Path) -> None:
    path = tmp_path / "twin.json"
    path.write_text(
        json.dumps(
            {
                "id": "twin",
                "verb": "foo",
                "argv_template": ["foo", "{text}"],
                "inputs": {"text": {"type": "string"}},
                "outputs": {"value": {"type": "string", "from": "stdout"}},
                "sample_input": {"text": "x"},
                "oracle": {"kind": "host_twin"},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(AdapterRefusal) as failure:
        C.load_schema_template(path)
    assert "host twin is code" in str(failure.value)


def test_ambiguous_terms_refuse_instead_of_guessing() -> None:
    # "character count and line count" hits wc-chars (character count) and
    # wc-lines (line count) with one term each — refuse instead of guessing.
    with pytest.raises(AdapterRefusal) as failure:
        match_verb_template("character count and line count", ["wc", "sh"])
    assert "ambiguous" in str(failure.value)
    assert "explicitly" in str(failure.value)
