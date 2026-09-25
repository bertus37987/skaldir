"""§B6 argument-mapping templates — typed parameters, real argv, real artefacts.

Companion to ``test_toolbuilder_container.py``: this file owns the templates that
mine a *command line* out of a container image (ImageMagick ``-resize``, ffmpeg
``-ss/-to``) rather than a verb over a string. Nothing here needs docker: the
image is a synthetic surface, the media fixtures are written by stdlib code in
this file, and the artefact facts are read back with the same reader the live
pipeline uses (:mod:`skaldir.container_media`). The live runs against the two
real images are the proof transcript of the wave, not a test of this suite.

What is pinned:

* a template's argv is built from TYPED parameters: integer/number/boolean/enum/
  path, bounds, enums, an ordered range, file existence, media sniffing, a size
  cap — each refusal naming the parameter and the reason;
* a flag appears or disappears with its parameter (``when`` / ``when_not`` /
  ``when_given`` / ``when_not_given``), and an enum renders as the tool's own
  token (``m4a`` -> ``ipod``) without the caller knowing that word;
* a host-only parameter (the source file, the output path) NEVER reaches the
  container's argv, while the source's bytes travel as one base64 element;
* the oracle is independent: its expectation is computed from the SOURCE file's
  own header plus the parameters, and the observed value comes from the artefact
  the container returned;
* the keyed output protocol is strict — missing, duplicated or undeclared outputs
  are refused, and the derived fields come from the artefact's bytes;
* the generated module runs the SAME render/parse code the builder certified it
  with (a staged copy of the runtime), so the two cannot drift.
"""

import base64
import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from skaldir import container_adapter, toolbuilder
from skaldir.toolbuilder import (
    AdapterRefusal,
    ContainerToolBuilder,
    _compare,
    expected_outputs,
    generate_container_adapter,
    parse_container_stdout,
    render_container_argv,
)
from tests.test_toolbuilder_container import (  # noqa: F401 — the fixtures live there
    DIGEST,
    FakeManager,
    _no_real_docker,
    manager,
    runtime,
)

# --------------------------------------------------------------------------- #
# synthetic surfaces and media fixtures (stdlib only)
# --------------------------------------------------------------------------- #

MAGICK_VERBS = ["bash", "base64", "convert", "magick", "tr", "sh"]
FFMPEG_VERBS = ["ash", "base64", "ffmpeg", "ffprobe", "sh", "tr"]


def _surface(*, verbs: list[str], image: str, digest: str = DIGEST,
             entrypoint: list[str] | None = None, bypass: bool = False) -> dict:
    return {
        "inspected": True,
        "failure_class": "",
        "image": image,
        "image_digest": digest,
        "entrypoint": list(entrypoint or []),
        "entrypoint_bypass": bool(bypass),
        "os": {"PRETTY_NAME": "synthetic"},
        "cli": {"verbs": sorted(verbs), "count": len(verbs), "probe": ["sh", "-c", "…"]},
        "api": {"server_hint": "", "paths": [], "note": ""},
        "mcp": {"executable": "", "note": ""},
        "probes": [],
        "inspected_at": 1.0,
    }


def _png_bytes(width: int, height: int) -> bytes:
    """A real, minimal PNG of the given size — its header is what the oracle reads."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + b"\x00" * (3 * width) for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _mp4_bytes(*, rate: int = 44100, frames: int = 47, delta: int = 1024) -> bytes:
    """A real, minimal MP4: ftyp + moov/trak(mdia(mdhd, hdlr, minf/stbl/stts)).

    The audio track declares the sample rate (``mdhd`` timescale) and its sample
    count (``stts``) — the two numbers the range oracle and the artefact reader
    each take from a file instead of from the container.
    """

    def box(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload) + 8) + kind + payload

    mdhd = box(b"mdhd", b"\x00" * 12 + struct.pack(">II", rate, 0) + b"\x00" * 4)
    hdlr = box(b"hdlr", b"\x00" * 8 + b"soun" + b"\x00" * 12)
    stts = box(
        b"stts",
        b"\x00\x00\x00\x00" + struct.pack(">I", 1) + struct.pack(">II", frames, delta),
    )
    stbl = box(b"stbl", stts)
    minf = box(b"minf", stbl)
    mdia = box(b"mdia", mdhd + hdlr + minf)
    trak = box(b"trak", mdia)
    return (
        box(b"ftyp", b"M4A \x00\x00\x02\x00M4A mp42isom")
        + box(b"moov", trak)
    )


@pytest.fixture
def image_file(tmp_path) -> Path:
    path = tmp_path / "source.png"
    path.write_bytes(_png_bytes(1600, 1200))
    return path


@pytest.fixture
def video_file(tmp_path) -> Path:
    path = tmp_path / "source.mp4"
    path.write_bytes(_mp4_bytes())
    return path


def _adapter(capability: str, *, verbs: list[str], image: str, digest: str = DIGEST,
             entrypoint_bypass: bool = False) -> dict:
    surface = _surface(verbs=verbs, image=image, digest=digest,
                       entrypoint=["convert"] if entrypoint_bypass else [],
                       bypass=entrypoint_bypass)
    return generate_container_adapter(
        capability, name="mined_tool", image=image, image_digest=digest, surface=surface
    )


def _magick_adapter(**kwargs) -> dict:
    return _adapter(
        "resize an image and write it as webp", verbs=MAGICK_VERBS,
        image="dpokidov/imagemagick:latest", **kwargs,
    )


def _ffmpeg_adapter(**kwargs) -> dict:
    return _adapter(
        "extract the audio of a video between two times", verbs=FFMPEG_VERBS,
        image="jrottenberg/ffmpeg:6.1-alpine", **kwargs,
    )


# --------------------------------------------------------------------------- #
# (1) the templates themselves
# --------------------------------------------------------------------------- #


class TestTheTemplates:
    def test_the_three_verb_templates_are_still_there(self):
        ids = [item["id"] for item in toolbuilder.VERB_TEMPLATES]
        assert ids[:3] == ["sha256-string", "md5-string", "base64-string"]
        # W2 widened the catalogue: the two media families stay, then the general
        # CLI text families (container_cli_templates) — pinned by id, not by slice.
        assert "imagemagick-resize" in ids and "ffmpeg-audio-range" in ids
        assert "tr-upper" in ids and "jq-field" in ids

    def test_a_subcommand_template_declares_paths_enums_and_a_bool(self):
        adapter = _magick_adapter()
        assert adapter["kind"] == "subcommand"
        assert adapter["verb"] == "magick"
        assert adapter["output_protocol"] == "keyed"
        inputs = adapter["operation"]["inputs"]
        assert inputs["input"]["host_only"] is True
        assert inputs["input"]["transfer"] == "argv-base64"
        assert inputs["format"]["values"] == ("webp", "png", "jpeg")
        assert inputs["strip"]["type"] == "boolean"
        assert inputs["width"]["type"] == "integer"
        assert adapter["operation"]["outputs"]["image"]["host_path_param"] == "out"

    def test_a_range_template_declares_an_ordered_window(self):
        adapter = _ffmpeg_adapter()
        assert adapter["kind"] == "subcommand-range"
        inputs = adapter["operation"]["inputs"]
        assert inputs["start"]["type"] == "number"
        assert inputs["end"]["greater_than"] == "start"
        assert inputs["container"]["token_map"] == {"m4a": "ipod", "wav": "wav"}
        assert inputs["codec"]["values"] == ("copy", "aac", "pcm_s16le")

    def test_a_transferred_source_is_masked_in_every_record(self, image_file, tmp_path):
        """The command that ran carries the bytes; the RECORD does not."""
        adapter = _magick_adapter()
        payload = {"input": str(image_file), "out": str(tmp_path / "o.webp"),
                   "width": 64}
        rendered = render_container_argv(adapter, payload)
        assert image_file.read_bytes() and len(rendered[3]) > 100  # the real element
        recorded = container_adapter.redacted_argv(adapter, payload, rendered)
        assert recorded[3] == f"<source:input:{len(rendered[3])} base64 bytes>"
        assert recorded[4:] == rendered[4:]  # everything else is untouched
        assert base64.b64encode(image_file.read_bytes()).decode("ascii") not in recorded

    def test_the_verb_is_resolved_from_the_surface_not_assumed(self):
        """IM v6 offers `convert` and no `magick`: the adapter runs what exists."""
        adapter = _adapter("resize an image", verbs=["sh", "convert", "base64"],
                           image="imagemagick:6")
        assert adapter["verb"] == "convert"

    def test_an_image_entrypoint_bypass_travels_into_the_adapter(self):
        adapter = _magick_adapter(entrypoint_bypass=True)
        assert adapter["entrypoint"] == ""
        assert adapter["entrypoint_declared"] == ["convert"]


# --------------------------------------------------------------------------- #
# (2) typed inputs
# --------------------------------------------------------------------------- #


class TestTypedInputs:
    def test_a_full_call_renders_the_command_line(self, image_file, tmp_path):
        adapter = _magick_adapter()
        argv = render_container_argv(adapter, {
            "input": str(image_file), "out": str(tmp_path / "out.webp"), "width": 1280,
        })
        assert argv[0:2] == ["sh", "-c"]
        assert argv[3] == base64.b64encode(image_file.read_bytes()).decode("ascii")
        assert argv[4:] == ["magick", "/dev/shm/godsv_in.png", "-resize", "1280x",
                            "-quality", "85", "-strip", "webp:/dev/shm/godsv_out"]
        assert "{" not in "".join(argv)

    def test_a_given_height_switches_the_resize_form(self, image_file, tmp_path):
        adapter = _magick_adapter()
        argv = render_container_argv(adapter, {
            "input": str(image_file), "out": str(tmp_path / "o.webp"),
            "width": 1280, "height": 720,
        })
        assert "-resize" in argv and "1280x720" in argv
        assert "1280x" not in argv

    def test_a_false_boolean_removes_its_flag(self, image_file, tmp_path):
        adapter = _magick_adapter()
        base = {"input": str(image_file), "out": str(tmp_path / "o.webp"), "width": 64}
        assert "-strip" in render_container_argv(adapter, base)
        assert "-strip" not in render_container_argv(adapter, {**base, "strip": False})

    def test_the_default_boolean_keeps_its_flag(self, video_file, tmp_path):
        adapter = _ffmpeg_adapter()
        base = {"input": str(video_file), "out": str(tmp_path / "o.m4a"),
                "start": 2, "end": 8}
        assert "-vn" in render_container_argv(adapter, base)
        assert "-vn" not in render_container_argv(adapter, {**base, "audio_only": False})

    def test_an_enum_renders_as_the_tools_own_word(self, video_file, tmp_path):
        adapter = _ffmpeg_adapter()
        argv = render_container_argv(adapter, {
            "input": str(video_file), "out": str(tmp_path / "o.m4a"),
            "start": 1.5, "end": 4, "container": "m4a", "codec": "pcm_s16le",
        })
        assert argv[-5:] == ["-c:a", "pcm_s16le", "-f", "ipod", "/dev/shm/godsv_out"]
        assert argv[4:8] == ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel"]

    def test_a_wav_container_renders_feature_for_feature(self, video_file, tmp_path):
        adapter = _ffmpeg_adapter()
        argv = render_container_argv(adapter, {
            "input": str(video_file), "out": str(tmp_path / "o.wav"),
            "start": 1.5, "end": 4, "container": "wav", "codec": "pcm_s16le",
        })
        assert "-f" in argv and "wav" in argv and "ipod" not in argv

    def test_the_source_bytes_travel_but_the_source_path_does_not(
        self, image_file, tmp_path
    ):
        adapter = _magick_adapter()
        argv = render_container_argv(adapter, {
            "input": str(image_file), "out": str(tmp_path / "o.webp"), "width": 64,
        })
        joined = " ".join(argv)
        assert str(image_file) not in joined
        assert str(tmp_path) not in joined
        assert base64.b64decode(argv[3]) == image_file.read_bytes()

    # -- refusals, one per typed rule --------------------------------------- #

    def test_an_integer_parameter_is_not_a_string(self, image_file, tmp_path):
        adapter = _magick_adapter()
        with pytest.raises(AdapterRefusal) as exc:
            render_container_argv(adapter, {
                "input": str(image_file), "out": str(tmp_path / "o.webp"),
                "width": "1280",
            })
        assert "input 'width' is not an integer" in str(exc.value)

    def test_a_bound_is_enforced(self, image_file, tmp_path):
        adapter = _magick_adapter()
        with pytest.raises(AdapterRefusal) as exc:
            render_container_argv(adapter, {
                "input": str(image_file), "out": str(tmp_path / "o.webp"),
                "width": 64, "quality": 400,
            })
        assert "above its maximum 100" in str(exc.value)

    def test_an_enum_value_outside_its_list_is_named(self, image_file, tmp_path):
        adapter = _magick_adapter()
        with pytest.raises(AdapterRefusal) as exc:
            render_container_argv(adapter, {
                "input": str(image_file), "out": str(tmp_path / "o.gif"),
                "width": 64, "format": "gif",
            })
        assert "input 'format' is not one of ['webp', 'png', 'jpeg']" in str(exc.value)

    def test_a_boolean_parameter_needs_a_json_boolean(self, video_file, tmp_path):
        adapter = _ffmpeg_adapter()
        with pytest.raises(AdapterRefusal) as exc:
            render_container_argv(adapter, {
                "input": str(video_file), "out": str(tmp_path / "o.m4a"),
                "start": 1, "end": 2, "audio_only": "yes",
            })
        assert "input 'audio_only' is not a boolean" in str(exc.value)

    def test_an_inverted_range_is_refused_by_the_contract(self, video_file, tmp_path):
        adapter = _ffmpeg_adapter()
        with pytest.raises(AdapterRefusal) as exc:
            render_container_argv(adapter, {
                "input": str(video_file), "out": str(tmp_path / "o.m4a"),
                "start": 8, "end": 2,
            })
        assert "must be greater than 'start'" in str(exc.value)

    def test_a_missing_required_input_is_refused(self, tmp_path):
        adapter = _magick_adapter()
        with pytest.raises(AdapterRefusal) as exc:
            render_container_argv(adapter, {"out": str(tmp_path / "o.webp"),
                                            "width": 64})
        assert "missing required input: input" in str(exc.value)

    def test_an_unknown_input_is_refused(self, image_file, tmp_path):
        adapter = _magick_adapter()
        with pytest.raises(AdapterRefusal) as exc:
            render_container_argv(adapter, {
                "input": str(image_file), "out": str(tmp_path / "o.webp"),
                "width": 64, "rotate": 90,
            })
        assert "unknown input(s)" in str(exc.value) and "rotate" in str(exc.value)

    def test_a_file_that_does_not_exist_is_refused(self, tmp_path):
        adapter = _magick_adapter()
        with pytest.raises(AdapterRefusal) as exc:
            render_container_argv(adapter, {
                "input": str(tmp_path / "absent.png"), "out": str(tmp_path / "o.webp"),
                "width": 64,
            })
        assert "not an existing file" in str(exc.value)

    def test_an_output_path_that_is_a_directory_is_refused(self, image_file, tmp_path):
        adapter = _magick_adapter()
        target = tmp_path / "a-directory"
        target.mkdir()
        with pytest.raises(AdapterRefusal) as exc:
            render_container_argv(adapter, {
                "input": str(image_file), "out": str(target), "width": 64,
            })
        assert "is a directory" in str(exc.value)

    def test_an_existing_output_file_is_overwritten_like_the_first_call(
        self, image_file, tmp_path
    ):
        """A second call with the same payload must behave like the first."""
        adapter = _magick_adapter()
        target = tmp_path / "already.webp"
        target.write_bytes(b"old")
        argv = render_container_argv(adapter, {
            "input": str(image_file), "out": str(target), "width": 64,
        })
        assert argv[-1] == "webp:/dev/shm/godsv_out"

    def test_the_declared_media_is_sniffed_before_the_call(self, video_file, tmp_path):
        adapter = _magick_adapter()
        with pytest.raises(AdapterRefusal) as exc:
            render_container_argv(adapter, {
                "input": str(video_file), "out": str(tmp_path / "o.webp"), "width": 64,
            })
        assert "input 'input' is not png" in str(exc.value)

    def test_a_source_above_the_transfer_cap_is_refused_with_the_numbers(
        self, tmp_path
    ):
        adapter = _ffmpeg_adapter()
        big = tmp_path / "big.mp4"
        big.write_bytes(_mp4_bytes() + b"\x00" * (64 * 1024 + 10))
        with pytest.raises(AdapterRefusal) as exc:
            render_container_argv(adapter, {
                "input": str(big), "out": str(tmp_path / "o.m4a"),
                "start": 1, "end": 2,
            })
        message = str(exc.value)
        assert "carries at most 65536 bytes" in message
        assert "one element at 131072 bytes" in message

    def test_a_template_that_interpolates_a_host_only_input_is_refused(
        self, image_file, tmp_path
    ):
        adapter = _magick_adapter()
        adapter["argv_template"] = [
            {"transfer": "base64", "param": "input"}, "{input}",
            {"verb": True}, {"const": "{_out}"},
        ]
        with pytest.raises(AdapterRefusal) as exc:
            render_container_argv(adapter, {
                "input": str(image_file), "out": str(tmp_path / "o.webp"),
                "width": 64,
            })
        assert "host-only input 'input'" in str(exc.value)

    def test_a_part_naming_an_undeclared_parameter_is_refused(self, image_file, tmp_path):
        adapter = _magick_adapter()
        adapter["argv_template"] = [
            {"transfer": "base64", "param": "input"}, {"const": "{missing}"},
        ]
        with pytest.raises(AdapterRefusal) as exc:
            render_container_argv(adapter, {
                "input": str(image_file), "out": str(tmp_path / "o.webp"),
                "width": 64,
            })
        assert "names 'missing'" in str(exc.value)


# --------------------------------------------------------------------------- #
# (3) the independent oracle
# --------------------------------------------------------------------------- #


class TestTheOracles:
    def test_the_image_expectation_comes_from_the_source_header(self, image_file):
        adapter = _magick_adapter()
        expectations = expected_outputs(adapter, {
            "input": str(image_file), "width": 1280,
        })
        assert expectations["values"] == {"geometry": "1280x960"}
        assert expectations["tolerance"] == {}
        assert "source 1600x1200" in expectations["note"]

    def test_the_image_expectation_honours_a_both_sides_box(self, image_file):
        adapter = _magick_adapter()
        expectations = expected_outputs(adapter, {
            "input": str(image_file), "width": 400, "height": 200,
        })
        # fit 1600x1200 into 400x200: the height is the binding side
        assert expectations["values"] == {"geometry": "267x200"}

    def test_the_range_expectation_is_arithmetic_on_the_source_rate(self, video_file):
        adapter = _ffmpeg_adapter()
        expectations = expected_outputs(adapter, {
            "input": str(video_file), "start": 2, "end": 8,
        })
        assert expectations["values"] == {"audio_ticks": 6 * 44100}
        assert "source audio rate 44100 Hz" in expectations["note"]

    def test_a_copied_stream_gets_one_frame_of_slack_and_names_it(self, video_file):
        adapter = _ffmpeg_adapter()
        copied = expected_outputs(adapter, {
            "input": str(video_file), "start": 0, "end": 4, "codec": "copy",
        })
        pcm = expected_outputs(adapter, {
            "input": str(video_file), "start": 0, "end": 4, "codec": "pcm_s16le",
        })
        assert copied["tolerance"]["audio_ticks"][0] == 1024
        assert "AAC frame" in copied["tolerance"]["audio_ticks"][1]
        assert pcm["tolerance"] == {}  # a PCM window is exact

    def test_a_verb_oracle_still_returns_one_scalar(self):
        adapter = generate_container_adapter(
            "compute the sha-256 digest of a string", name="h", image="alpine:3.20",
            image_digest=DIGEST,
            surface=_surface(verbs=["sha256sum", "sh"], image="alpine:3.20"),
        )
        expectations = expected_outputs(adapter, {"text": "skaldir"})
        assert expectations["values"] == {
            "sha256": __import__("hashlib").sha256(b"skaldir").hexdigest()
        }

    def test_an_empty_range_is_refused_by_the_oracle_too(self, video_file):
        adapter = _ffmpeg_adapter()
        with pytest.raises(AdapterRefusal):
            expected_outputs(adapter, {"input": str(video_file), "start": 4, "end": 4})

    def test_the_comparison_is_exact_unless_a_tolerance_was_declared(self):
        assert _compare("1280x960", "1280x960")[0] is True
        assert _compare("1280x960", "1280x961")[0] is False
        assert _compare(264600, 264192, tolerance=1024)[0] is True
        assert _compare(264600, 264192)[0] is False
        assert _compare(264600, 260000, tolerance=1024)[0] is False
        assert _compare("x", None)[0] is False


# --------------------------------------------------------------------------- #
# (4) the keyed output protocol
# --------------------------------------------------------------------------- #


class TestTheKeyedProtocol:
    def test_the_artefact_is_read_back_by_the_host(self, image_file, tmp_path):
        adapter = _magick_adapter()
        png = _png_bytes(400, 300)
        stdout = "image@b64=" + base64.b64encode(png).decode("ascii") + "\n"
        ok, parsed, detail = parse_container_stdout(
            adapter, stdout, keep_artefacts=True
        )
        assert ok is True, detail
        assert parsed["geometry"] == "400x300"
        assert parsed["image"]["media"] == "png"
        assert parsed["image"]["bytes"] == len(png)

        written = container_adapter.write_artefacts(
            adapter, {"out": str(tmp_path / "kept.png")}, parsed
        )
        assert written == {"image": str(tmp_path / "kept.png")}
        assert (tmp_path / "kept.png").read_bytes() == png
        assert "data" not in parsed["image"]  # never a buffer the caller did not ask for

    def test_the_declared_media_is_checked_on_the_way_back(self):
        adapter = _magick_adapter()
        stdout = "image@b64=" + base64.b64encode(b"not an image").decode("ascii")
        ok, parsed, detail = parse_container_stdout(adapter, stdout)
        assert ok is False and "declared" in detail

    def test_a_missing_output_is_refused_by_name(self):
        adapter = _magick_adapter()
        ok, parsed, detail = parse_container_stdout(adapter, "")
        assert ok is False and "did not print the declared output(s): image" in detail

    def test_a_duplicated_output_is_refused(self):
        adapter = _magick_adapter()
        line = "image@b64=" + base64.b64encode(_png_bytes(2, 2)).decode("ascii")
        ok, parsed, detail = parse_container_stdout(adapter, line + "\n" + line)
        assert ok is False and "twice" in detail

    def test_an_undeclared_output_is_refused(self):
        adapter = _magick_adapter()
        ok, parsed, detail = parse_container_stdout(adapter, "surprise=1")
        assert ok is False and "undeclared output 'surprise'" in detail

    def test_a_non_base64_payload_is_refused(self):
        adapter = _magick_adapter()
        ok, parsed, detail = parse_container_stdout(adapter, "image@b64=not base64!")
        assert ok is False and "not valid base64" in detail

    def test_the_range_artefact_is_counted_from_the_file(self):
        adapter = _ffmpeg_adapter()
        mp4 = _mp4_bytes(rate=44100, frames=47, delta=1024)
        stdout = "audio@b64=" + base64.b64encode(mp4).decode("ascii")
        ok, parsed, detail = parse_container_stdout(adapter, stdout)
        assert ok is True, detail
        assert parsed["audio_ticks"] == 47 * 1024
        assert parsed["audio"]["media"] == "mp4"

    def test_a_verb_adapter_keeps_the_single_value_protocol(self):
        adapter = generate_container_adapter(
            "compute the md5 digest of a string", name="h", image="alpine:3.20",
            image_digest=DIGEST, surface=_surface(verbs=["md5sum", "sh"], image="alpine:3.20"),
        )
        digest = __import__("hashlib").md5(b"skaldir").hexdigest()
        ok, parsed, _ = parse_container_stdout(adapter, digest)
        assert (ok, parsed) == (True, {"md5": digest})


# --------------------------------------------------------------------------- #
# (5) the staged tool runs the same runtime the builder certified
# --------------------------------------------------------------------------- #


class TestTheStagedRuntime:
    def _staged(self, tmp_path, *, adapter_kind: str, payload: dict) -> dict:
        # what the container "returns": an image for the resize template, and for
        # the range template a real mp4 whose audio track carries 258 frames of
        # 1024 ticks (264192 samples) — 408 short of the 264600 a 6 s window
        # expects, i.e. inside the declared one-AAC-frame tolerance and nowhere
        # near an off-by-a-second error
        artefact = (
            _png_bytes(64, 48) if adapter_kind == "magick"
            else _mp4_bytes(frames=258, delta=1024)
        )
        marker = "image" if adapter_kind == "magick" else "audio"
        builder = ContainerToolBuilder(tmp_path / "runtime", manager=FakeManager(
            verbs="\n".join(MAGICK_VERBS if adapter_kind == "magick" else FFMPEG_VERBS),
            adapter_stdout=marker + "@b64=" + base64.b64encode(artefact).decode("ascii"),
        ))
        result = builder.mine_container(
            "resize an image and write it as webp" if adapter_kind == "magick"
            else "extract the audio of a video between two times",
            image="dpokidov/imagemagick:latest" if adapter_kind == "magick"
            else "jrottenberg/ffmpeg:6.1-alpine",
            name=f"mined_{adapter_kind}", test_input=payload,
        )
        assert result["ok"] is True, result
        return result

    def test_the_runtime_is_staged_next_to_the_tool(self, tmp_path, image_file,
                                                    video_file):
        result = self._staged(
            tmp_path, adapter_kind="magick",
            payload={"input": str(image_file), "out": str(tmp_path / "o.webp"),
                     "width": 64},
        )
        tool_dir = Path(result["tool_dir"])
        assert (tool_dir / "container_adapter.py").is_file()
        assert (tool_dir / "container_media.py").is_file()

    def test_the_staged_copy_is_importable_on_its_own(self, tmp_path, image_file):
        """The copy is a sibling module, not a member of this package."""
        result = self._staged(
            tmp_path, adapter_kind="magick",
            payload={"input": str(image_file), "out": str(tmp_path / "o.webp"),
                     "width": 64},
        )
        prepared = Path(result["tool_dir"]) / "prepare.py"
        prepared.write_text(
            "import json, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import container_adapter\n"
            "print(json.dumps(container_adapter.render(json.load(open(sys.argv[2])),"
            " json.loads(sys.argv[3])), ensure_ascii=False))\n",
            encoding="utf-8",
        )
        proc = subprocess.run(
            [sys.executable, str(prepared), str(result["tool_dir"]),
             result["adapter_path"],
             json.dumps({"input": str(image_file), "out": str(tmp_path / "o.webp"),
                         "width": 64})],
            capture_output=True, text=True, timeout=120,
            cwd=str(tmp_path), env={"PATH": "/usr/bin:/bin", "PYTHONPATH": ""},
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.returncode == 0, proc.stderr
        rendered = json.loads(proc.stdout)
        assert rendered[0:2] == ["sh", "-c"]
        assert "printf %s \"$0\" | base64 -d > \"$IN\"" in rendered[2]
        assert rendered[3] == base64.b64encode(image_file.read_bytes()).decode("ascii")
        assert rendered[4:] == [
            "magick", "/dev/shm/godsv_in.png", "-resize", "64x", "-quality",
            "85", "-strip", "webp:/dev/shm/godsv_out",
        ]

    def test_the_module_render_agrees_with_the_builder_for_every_payload(
        self, tmp_path, image_file
    ):
        result = self._staged(
            tmp_path, adapter_kind="magick",
            payload={"input": str(image_file), "out": str(tmp_path / "o.webp"),
                     "width": 64},
        )
        module = _load(result["tool_dir"], result["adapter_path"])
        adapter = json.loads(Path(result["adapter_path"]).read_text(encoding="utf-8"))
        for payload in (
            {"input": str(image_file), "out": str(tmp_path / "a.webp"), "width": 1280},
            {"input": str(image_file), "out": str(tmp_path / "b.webp"), "width": 1280,
             "height": 720},
            {"input": str(image_file), "out": str(tmp_path / "c.webp"), "width": 640,
             "format": "png", "strip": False, "quality": 30},
        ):
            assert module.render(payload) == render_container_argv(adapter, payload)

    def test_the_generated_test_carries_the_oracles_expectation(self, tmp_path,
                                                               image_file):
        result = self._staged(
            tmp_path, adapter_kind="magick",
            payload={"input": str(image_file), "out": str(tmp_path / "o.webp"),
                     "width": 64},
        )
        generated = (Path(result["tool_dir"]) / "tests" /
                     "test_mined_magick.py").read_text(encoding="utf-8")
        assert "'geometry': '64x48'" in generated
        assert "'output': 'image'" in generated and "'field': 'geometry'" in generated
        # the recorded call's paths are moved into a temp dir, so the test replays
        assert "_HOST_PARAMS = ['out']" in generated

    def test_the_range_template_certifies_within_its_declared_tolerance(
        self, tmp_path, video_file
    ):
        result = self._staged(
            tmp_path, adapter_kind="ffmpeg",
            payload={"input": str(video_file), "out": str(tmp_path / "o.m4a"),
                     "start": 0, "end": 6},
        )
        test = result["test"]
        assert test["verdicts"][0]["tolerance"] == 1024
        assert "within the declared tolerance" in test["verdicts"][0]["detail"]
        generated = (Path(result["tool_dir"]) / "tests" /
                     "test_mined_ffmpeg.py").read_text(encoding="utf-8")
        assert "_TOLERANCE = {'audio_ticks': 1024}" in generated

    def test_a_file_call_without_a_test_input_is_refused_by_name(self, tmp_path):
        builder = ContainerToolBuilder(tmp_path / "runtime", manager=FakeManager(
            verbs="\n".join(MAGICK_VERBS)))
        result = builder.mine_container(
            "resize an image and write it as webp", image="dpokidov/imagemagick:latest",
            name="mined_magick")
        assert result["ok"] is False
        assert result["failure_class"] == "test_input_required"
        assert "--test-input naming input" in result["reason"]


def _load(tool_dir: str, adapter_path: str):
    """Import a staged generated module by path — no package, no docker."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "staged_module", Path(tool_dir) / "container_mined_magick.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["staged_module"] = module
    spec.loader.exec_module(module)
    return module
