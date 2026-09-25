"""Stdlib-only readers for the media artefacts a mined container capability produces.

Why this module exists
----------------------
``toolbuilder`` certifies a container capability by comparing what the container
returned with an expected value computed *locally* (the independent oracle). For
the three hash-like verbs that shipped first this is trivial: the artefact is a
line of text and the local oracle is ``hashlib``/``base64``.

An argument-mapping capability (ImageMagick ``-resize``, ffmpeg ``-ss/-to``) is a
different animal: the call takes an input file and returns an *artefact*, and the
interesting fact is a property of those bytes — the pixel geometry of the image,
the number of audio samples in the sound file, whether the file is really the
container format that was asked for. Reading those facts with a local, stdlib
parser is what keeps the oracle independent: the expected value comes from the
*input* the host owns plus the parameters it asked for, and the observed value
comes from the artefact's own header — never from the container's own report of
what it thinks it did.

What it reads
-------------
============================  ==================================================
``media_format``              magic-byte sniffing: png / webp / jpeg / gif / wav
                              / mp4-family / flac / ogg / mp3 / aac (adts)
``image_geometry``            ``"<width>x<height>"`` for PNG (IHDR), WebP
                              (VP8X canvas, VP8 frame header, VP8L bitstream),
                              JPEG (SOF marker)
``audio_ticks``               the audio duration in samples: PCM WAV — the
                              ``data`` chunk size over the block alignment; MP4
                              family — ``Σ count x delta`` of the audio track's
                              ``stts`` (the timescale being the sample rate)
``audio_frames``              MP4 family: the coded frame count (``stts``
                              counts, summed)
``mp4_audio_rate``            the first audio track's ``mdhd`` timescale (its
                              sample rate) — this is what the ffmpeg range
                              oracle multiplies by the requested window
``describe``                  everything above, as one record for an artefact

Honesty rules
-------------
* Nothing here is a decoder and nothing here guesses: an unknown magic is
  ``""``, a container whose field cannot be read raises
  :class:`ArtefactRefusal` naming the container and the field. A refusal is
  data for the caller (the derived output is reported as not derivable with the
  reason), never a fabricated number.
* A streaming WAV (the header a pipe produces: a ``data``/``RIFF`` size of
  ``0xFFFFFFFF``) is read from the number of bytes that are really present,
  which is exact for the file the caller received.
* The MP4 walker follows ``moov``/``trak``/``mdia``/``hdlr``/``minf``/``stbl``
  and takes the first track whose handler is ``soun``. A fragmented file whose
  ``stts`` is empty is refused by name rather than reported as zero samples.

CLI: none. This module is a library: ``toolbuilder`` (the oracle and the parse
step of the container pipeline) and the generated adapter tests import it.
"""

from __future__ import annotations

import hashlib
import struct

SCHEMA_VERSION = 1

#: The media types this module can name by magic bytes.
MEDIA_PNG = "png"
MEDIA_WEBP = "webp"
MEDIA_JPEG = "jpeg"
MEDIA_GIF = "gif"
MEDIA_WAV = "wav"
MEDIA_MP4 = "mp4"
MEDIA_FLAC = "flac"
MEDIA_OGG = "ogg"
MEDIA_MP3 = "mp3"
MEDIA_AAC = "aac"

#: The JPEG start-of-frame markers that carry the geometry.
_JPEG_SOF = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                       0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})

_WEBP_LOSSLESS_SIGNATURE = 0x2F
_WEBP_LOSSY_START_CODE = b"\x9d\x01\x2a"

#: MP4-family brands (``ftyp`` major brand) that mean "this is an MP4 file".
_MP4_BRANDS = (b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42",
               b"avc1", b"M4A ", b"M4V ", b"m4a ", b"mp71", b"dash", b"qt  ")


class ArtefactRefusal(RuntimeError):
    """An artefact field cannot be read from these bytes — named, never guessed."""


# --------------------------------------------------------------------------- #
# magic bytes
# --------------------------------------------------------------------------- #


def media_format(data: bytes) -> str:
    """The container format of ``data`` by magic bytes — ``""`` when unknown."""
    head = bytes(data[:64])
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return MEDIA_PNG
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return MEDIA_WEBP
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return MEDIA_WAV
    if head.startswith(b"\xff\xd8\xff"):
        return MEDIA_JPEG
    if head.startswith((b"GIF87a", b"GIF89a")):
        return MEDIA_GIF
    if head[:4] == b"fLaC":
        return MEDIA_FLAC
    if head.startswith(b"OggS"):
        return MEDIA_OGG
    if head[4:8] == b"ftyp" and head[8:12] in _MP4_BRANDS:
        return MEDIA_MP4
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return MEDIA_MP3
    if len(head) > 1 and head[0] == 0xFF and head[1] & 0xF6 == 0xF0:
        return MEDIA_AAC
    return ""


# --------------------------------------------------------------------------- #
# image geometry
# --------------------------------------------------------------------------- #


def _png_geometry(data: bytes) -> str:
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise ArtefactRefusal("png: no IHDR at offset 12 — the file is truncated")
    width, height = struct.unpack(">II", data[16:24])
    return f"{width}x{height}"


def _webp_geometry(data: bytes) -> str:
    if len(data) < 30:
        raise ArtefactRefusal("webp: fewer than 30 bytes — the file is truncated")
    fourcc = data[12:16]
    if fourcc == b"VP8X":
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return f"{width}x{height}"
    if fourcc == b"VP8L":
        if data[20] != _WEBP_LOSSLESS_SIGNATURE:
            raise ArtefactRefusal("webp: VP8L chunk without the 0x2f signature")
        bits = int.from_bytes(data[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return f"{width}x{height}"
    if fourcc == b"VP8 ":
        start = data.find(_WEBP_LOSSY_START_CODE, 20, 40)
        if start < 0:
            raise ArtefactRefusal("webp: VP8 keyframe start code not found")
        width = struct.unpack_from("<H", data, start + 3)[0] & 0x3FFF
        height = struct.unpack_from("<H", data, start + 5)[0] & 0x3FFF
        return f"{width}x{height}"
    raise ArtefactRefusal(f"webp: unknown first chunk {fourcc!r}")


def _jpeg_geometry(data: bytes) -> str:
    index = 2
    size = len(data)
    while index + 9 < size:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        if marker == 0xDA:  # start of scan: no frame header was found
            break
        segment = struct.unpack_from(">H", data, index + 2)[0]
        if marker in _JPEG_SOF:
            height, width = struct.unpack_from(">HH", data, index + 5)
            return f"{width}x{height}"
        index += 2 + max(segment, 2)
    raise ArtefactRefusal("jpeg: no SOF marker with the geometry was found")


def image_geometry(data: bytes) -> str:
    """``"<width>x<height>"`` of an image artefact — by its own header bytes."""
    kind = media_format(data)
    if kind == MEDIA_PNG:
        return _png_geometry(data)
    if kind == MEDIA_WEBP:
        return _webp_geometry(data)
    if kind == MEDIA_JPEG:
        return _jpeg_geometry(data)
    if kind == MEDIA_GIF:
        if len(data) < 10:
            raise ArtefactRefusal("gif: fewer than 10 bytes")
        width, height = struct.unpack_from("<HH", data, 6)
        return f"{width}x{height}"
    raise ArtefactRefusal(
        f"the geometry of a {kind or 'unknown'} artefact is not readable here "
        "(png, webp, jpeg and gif are)"
    )


# --------------------------------------------------------------------------- #
# audio
# --------------------------------------------------------------------------- #


def _wav_chunks(data: bytes) -> dict[str, tuple[int, int]]:
    """``{chunk id: (offset, size)}`` of a RIFF/WAVE file — sizes as declared."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ArtefactRefusal("wav: not a RIFF/WAVE file")
    chunks: dict[str, tuple[int, int]] = {}
    index = 12
    while index + 8 <= len(data):
        name = data[index:index + 4].decode("ascii", "replace")
        size = struct.unpack_from("<I", data, index + 4)[0]
        chunks.setdefault(name, (index + 8, size))
        if size == 0xFFFFFFFF:
            break  # streaming: the following chunk is the last one written
        index += 8 + size + (size & 1)
    return chunks


def wav_audio_rate(data: bytes) -> int:
    """The sample rate of a WAV file (``fmt`` at offset 4)."""
    chunks = _wav_chunks(data)
    if "fmt " not in chunks:
        raise ArtefactRefusal("wav: the fmt chunk is missing")
    offset, size = chunks["fmt "]
    if size < 8:
        raise ArtefactRefusal("wav: the fmt chunk is shorter than 8 bytes")
    rate = struct.unpack_from("<I", data, offset + 4)[0]
    if rate == 0:
        raise ArtefactRefusal("wav: the fmt chunk declares a sample rate of 0")
    return rate


def wav_audio_samples(data: bytes) -> int:
    """The PCM sample count of a WAV — ``data`` bytes over the block alignment."""
    chunks = _wav_chunks(data)
    if "fmt " not in chunks or "data" not in chunks:
        raise ArtefactRefusal("wav: the fmt or the data chunk is missing")
    fmt_offset, fmt_size = chunks["fmt "]
    if fmt_size < 16:
        raise ArtefactRefusal("wav: the fmt chunk is shorter than 16 bytes")
    bits = struct.unpack_from("<H", data, fmt_offset + 14)[0]
    block_align = struct.unpack_from("<H", data, fmt_offset + 12)[0]
    if bits == 0 or block_align == 0:
        raise ArtefactRefusal(
            "wav: fmt carries no PCM block alignment (bits_per_sample="
            f"{bits}, block_align={block_align}) — a compressed payload is not "
            "countable in samples here"
        )
    data_offset, data_size = chunks["data"]
    if data_size == 0xFFFFFFFF or data_offset + data_size > len(data):
        # a pipe-produced WAV leaves the sizes unknown; the bytes that are here
        # are the bytes the container really wrote
        data_size = len(data) - data_offset
    return data_size // block_align


def _mp4_boxes(data: bytes, start: int, end: int):
    """Yield ``(type, payload_start, payload_end)`` of the boxes in ``[start,end)``."""
    index = start
    while index + 8 <= end:
        size = struct.unpack_from(">I", data, index)[0]
        kind = data[index + 4:index + 8]
        header = 8
        if size == 1:  # 64-bit size
            if index + 16 > end:
                return
            size = struct.unpack_from(">Q", data, index + 8)[0]
            header = 16
        elif size == 0:  # "to the end of the file"
            size = end - index
        if size < header or index + size > end:
            return
        yield kind, index + header, index + size
        index += size


def _mp4_find(data: bytes, start: int, end: int, path: list[bytes]) -> tuple[int, int] | None:
    """Walk one box ``path`` (e.g. ``["moov", "trak"]``) — first match wins."""
    if not path:
        return start, end
    wanted = path[0]
    for kind, payload_start, payload_end in _mp4_boxes(data, start, end):
        if kind == wanted:
            found = _mp4_find(data, payload_start, payload_end, path[1:])
            if found is not None:
                return found
    return None


def _mp4_audio_path(data: bytes) -> tuple[int, int]:
    """The first audio track: ``trak`` whose handler is ``soun``."""
    moov = _mp4_find(data, 0, len(data), [b"moov"])
    if moov is None:
        raise ArtefactRefusal("mp4: no moov box (a live/fragmented stream without one)")
    for kind, payload_start, payload_end in _mp4_boxes(data, moov[0], moov[1]):
        if kind != b"trak":
            continue
        handler = _mp4_find(data, payload_start, payload_end, [b"mdia", b"hdlr"])
        if handler is None or handler[1] - handler[0] < 12:
            continue
        # hdlr: version/flags (4) + pre_defined (4) + handler_type (4)
        if data[handler[0] + 8:handler[0] + 12] == b"soun":
            return payload_start, payload_end
    raise ArtefactRefusal("mp4: no audio (soun) track — the file carries no sound")


def _mp4_timescale(data: bytes, trak: tuple[int, int]) -> int:
    mdhd = _mp4_find(data, trak[0], trak[1], [b"mdia", b"mdhd"])
    if mdhd is None or mdhd[1] - mdhd[0] < 20:
        raise ArtefactRefusal("mp4: the audio track carries no mdhd box")
    version = data[mdhd[0]]
    offset = mdhd[0] + (20 if version == 1 else 12)
    timescale = struct.unpack_from(">I", data, offset)[0]
    if timescale == 0:
        raise ArtefactRefusal("mp4: the audio track's timescale is 0")
    return timescale


def mp4_audio_rate(data: bytes) -> int:
    """The first audio track's sample rate (its ``mdhd`` timescale)."""
    if media_format(data) != MEDIA_MP4:
        raise ArtefactRefusal("mp4: these bytes are not an MP4-family file")
    return _mp4_timescale(data, _mp4_audio_path(data))


def mp4_audio_frames(data: bytes) -> int:
    """The number of coded audio frames (the ``stts`` entry counts, summed)."""
    return _mp4_stts(data)[0]


def mp4_audio_ticks(data: bytes) -> int:
    """The audio duration in ticks — ``Σ count x delta`` of the audio ``stts``.

    For an audio track the ``mdhd`` timescale *is* the sample rate, so the tick
    total is the number of samples the track decodes to (AAC frames of 1024
    samples, plus the encoder's priming/padding — within one frame of the
    requested window). This is the quantity the ffmpeg range oracle predicts.
    """
    return _mp4_stts(data)[1]


def _mp4_stts(data: bytes) -> tuple[int, int]:
    """``(frames, ticks)`` of the first audio track's ``stts`` box."""
    if media_format(data) != MEDIA_MP4:
        raise ArtefactRefusal("mp4: these bytes are not an MP4-family file")
    trak = _mp4_audio_path(data)
    stts = _mp4_find(data, trak[0], trak[1], [b"mdia", b"minf", b"stbl", b"stts"])
    if stts is None or stts[1] - stts[0] < 8:
        raise ArtefactRefusal(
            "mp4: the audio track carries no stts box — a fragmented file's "
            "sample counts live in its fragments, which this reader does not sum"
        )
    entries = struct.unpack_from(">I", data, stts[0] + 4)[0]
    frames = 0
    ticks = 0
    index = stts[0] + 8
    for _ in range(entries):
        if index + 8 > stts[1]:
            raise ArtefactRefusal("mp4: the stts box is truncated")
        count, delta = struct.unpack_from(">II", data, index)
        frames += int(count)
        ticks += int(count) * int(delta)
        index += 8
    if frames == 0 or ticks == 0:
        raise ArtefactRefusal("mp4: the stts box declares no audio samples")
    return frames, ticks


def audio_ticks(data: bytes) -> int:
    """The audio duration in samples/ticks of a WAV (PCM) or MP4 artefact."""
    kind = media_format(data)
    if kind == MEDIA_WAV:
        return wav_audio_samples(data)
    if kind == MEDIA_MP4:
        return mp4_audio_ticks(data)
    raise ArtefactRefusal(
        f"the sample count of a {kind or 'unknown'} artefact is not readable here "
        "(wav and mp4 are)"
    )


def audio_rate(data: bytes) -> int:
    """The audio sample rate of a WAV or MP4 artefact."""
    kind = media_format(data)
    if kind == MEDIA_WAV:
        return wav_audio_rate(data)
    if kind == MEDIA_MP4:
        return mp4_audio_rate(data)
    raise ArtefactRefusal(
        f"the sample rate of a {kind or 'unknown'} artefact is not readable here "
        "(wav and mp4 are)"
    )


# --------------------------------------------------------------------------- #
# one record per artefact
# --------------------------------------------------------------------------- #


def describe(data: bytes) -> dict:
    """Every fact this module can read from one artefact — refusals included."""
    record: dict = {
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "media": media_format(data),
        "geometry": "",
        "audio_ticks": None,
        "audio_rate": None,
        "audio_frames": None,
        "unreadable": {},
    }
    try:
        if record["media"] in (MEDIA_PNG, MEDIA_WEBP, MEDIA_JPEG, MEDIA_GIF):
            record["geometry"] = image_geometry(data)
    except ArtefactRefusal as exc:
        record["unreadable"]["geometry"] = str(exc)
    try:
        if record["media"] in (MEDIA_WAV, MEDIA_MP4):
            record["audio_ticks"] = audio_ticks(data)
            record["audio_rate"] = audio_rate(data)
        if record["media"] == MEDIA_MP4:
            record["audio_frames"] = mp4_audio_frames(data)
    except ArtefactRefusal as exc:
        record["unreadable"]["audio"] = str(exc)
    return record


def field_of(data: bytes, field: str) -> object:
    """ONE named field of an artefact, or an :class:`ArtefactRefusal`.

    The ``derive`` specs of a container adapter name their field here, so the
    adapter never grows a second parser: ``geometry``, ``audio_ticks``,
    ``audio_rate``, ``audio_frames``, ``media``, ``bytes``, ``sha256``.
    """
    record = describe(data)
    if field not in record:
        raise ArtefactRefusal(
            f"'{field}' is not a readable artefact field (readable: "
            + ", ".join(sorted(record))
            + ")"
        )
    value = record[field]
    if value in ("", None):
        reason = record["unreadable"].get(field) or record["unreadable"].get("audio")
        raise ArtefactRefusal(
            f"'{field}' is not derivable from this {record['media'] or 'unknown'} "
            f"artefact" + (f": {reason}" if reason else "")
        )
    return value
