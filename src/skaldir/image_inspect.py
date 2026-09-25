"""Shell-free container image inspection — the ``sh -c`` probe is optional, not required.

Why this module exists
----------------------
``toolbuilder.inspect_container_surface`` probes an image's surface by *executing a
shell inside it*::

    sh -c "cat /etc/os-release"
    sh -c "(busybox --list || true); ls -1 /bin /usr/bin /usr/local/bin"

That is a fine route for a full distribution image (alpine, debian), and it is the
right route when it works. It cannot work at all for an image that carries **no
shell** — ``hello-world``, ``distroless``, a scratch-built binary. There the probe
fails with docker's own ``executable file not found`` and the whole inspection is
refused (``inspect_container_surface`` → ``inspected: False`` → the build stops with
exit 3). The image is then "un-inspectable" for a reason that has nothing to do with
the image's usefulness: our *probe* needed a shell.

This module answers the same question without ever needing one:

============================  ==========================================================
``docker image inspect``      config, entrypoint/cmd, env, labels, exposed ports,
                             volumes, user, architecture, healthcheck (read-only, no
                             container, no shell)
``docker save`` + tar stream  the real filesystem: layers, executables (``/bin/*``,
                             ``/usr/bin/*``, ``/usr/local/bin/*``, ``/sbin/*``,
                             root-level binaries), shared libraries, ``/etc/os-release``
                             — read out of the layer tars, not out of a shell
``docker run --rm <args>``    an **exec** probe of one binary (``<bin> --help``, or the
                             image's declared entrypoint) — no ``sh -c``, no wrapper:
                             the binary is the container's command
============================  ==========================================================

Every finding is classified as **data**, and the classification is honest about what
each route can and cannot answer:

* ``inspectable``   — the shell-free evidence describes the candidate (it executed
  directly, or the image metadata declares it); a surface can be built from it.
* ``needs-shell``   — the shell-free route is *insufficient* for this candidate, and the
  image carries a shell, so the existing ``sh -c`` route can still answer. The reason
  names the exact probe that would answer it.
* ``impossible``    — neither route can answer: the direct exec did not run **and** the
  image has no shell. Reported as such instead of being dressed up as a refusal of the
  image itself.

The module never fabricates a surface: an unprobed binary is ``needs-shell`` /
``impossible``, not ``inspectable``, and a capability that the inspected surface does
not offer is handed to the existing generator (:func:`skaldir.toolbuilder.
generate_container_adapter`) so that *its* refusal — not a second copy of the matching
logic here — decides. No adapter matching logic is duplicated in this file.

Safety (identical policy to :mod:`skaldir.docker_runtime`, see MVP/05 §3)
----------------------------------------------------------------------
* Nothing is pruned, no image is removed, no container is removed by name.
* The only container this module ever starts is a one-shot ``docker run --rm`` exec
  probe through :class:`skaldir.docker_runtime.RunConfig`, which stamps
  ``godsv.owner=skaldir`` on it (``RunConfig.__post_init__`` enforces the label) and
  applies ``--network none``, ``--read-only``, ``no-new-privileges`` and resource caps.
* Every inspect run first counts Skaldir's own containers by label, then sweeps
  leftovers by that same label (``cleanup_owned``) and reports the counts — a probe
  killed by its timeout can leave one behind, so the sweep is measured, not assumed.
* ``probe_exec=False`` guarantees **no container at all** (metadata + layers only).
* The 7 foreign ``penpot-local`` containers are untouched: a label filter that is not
  ours is refused by the runtime (``label-scope-required``), not trusted.

CLI (the CLI owner wires it — this module never edits ``cli.py``)::

    from .image_inspect_cli import register_cli as _register_image_cli
    _register_image_cli(sub)

``src/skaldir/image_inspect_cli.py`` carries ``register_cli(subparsers)`` and the two
dotted paths (``image.inspect``, ``image.layers``) that must appear in
``cli_contract.COMMANDS`` — without those entries ``tests/test_cli_contract_dump.py``
fails ("a new command cannot ship without a contract").
"""

from __future__ import annotations

import gzip
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

#: the three honest outcomes of a candidate classification
CLASS_INSPECTABLE = "inspectable"
CLASS_NEEDS_SHELL = "needs-shell"
CLASS_IMPOSSIBLE = "impossible"

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_BLOCKED = 3
EXIT_NOT_FOUND = 5

#: `docker save` is spooled to a file so the layer tars can be read twice. Small
#: images only (hello-world 31 KB, alpine 4 MB) — the cap is a guard, not a target.
DEFAULT_MAX_SAVE_BYTES = 256 * 1024 * 1024
DEFAULT_TIMEOUT = 60.0
DEFAULT_EXEC_TIMEOUT = 15.0
DEFAULT_EXEC_PROBES = 3
DEFAULT_CANDIDATE_LIMIT = 10
DEFAULT_VERB_LIMIT = 500
MAX_ENTRIES_PER_LAYER = 200_000
OS_RELEASE_MAX_BYTES = 8 * 1024

#: one directory level below these holds the executables
BIN_DIRS = ("bin", "sbin", "usr/bin", "usr/sbin", "usr/local/bin", "usr/local/sbin")
#: shared-library locations
LIB_DIRS = ("lib", "lib64", "usr/lib", "usr/lib64", "usr/local/lib", "usr/local/lib64")
#: paths that carry a shell (`busybox` counts: it ships the `sh` applet)
SHELL_NAMES = ("sh", "bash", "ash", "dash", "ksh", "zsh", "busybox", "tcsh", "csh")
#: binaries that usually mean "a CLI a user can call" (samples for the exec probe)
KNOWN_CLI_TOOLS = (
    "busybox", "sha256sum", "md5sum", "sha1sum", "base64", "openssl", "curl", "wget",
    "jq", "python3", "python", "node", "npm", "git", "sqlite3", "ffmpeg", "nginx",
    "httpd", "caddy", "traefik", "uvicorn", "gunicorn", "pandoc", "convert", "magick",
    "7z", "unzip", "zip", "gzip", "xz", "zstd", "tar", "redis-server", "postgres",
)
#: label substrings that announce an HTTP surface
HTTP_LABEL_HINTS = ("http", "port", "url", "endpoint", "expose", "listen")
_SHELL_TOKEN = re.compile(r"(?:^|/)(?:sh|bash|ash|dash|ksh|zsh)$")
_GZIP_MAGIC = b"\x1f\x8b"
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
_TAR_MAGIC_AT = 257  # ustar magic offset inside a tar header


# --------------------------------------------------------------------------- #
# docker plumbing (injectable — no docker needed to unit-test this module)      #
# --------------------------------------------------------------------------- #


def _binary_of(manager: Any) -> str:
    """The docker binary: the manager's own, else PATH, else the plain name."""
    binary = str(getattr(manager, "docker_bin", "") or "").strip()
    return binary or shutil.which("docker") or "docker"


def _subprocess_runner(argv: list[str], timeout: float) -> dict:
    """One docker call: exit code / stdout / stderr, hard timeout, never raising."""
    try:
        proc = subprocess.run(  # nosec B603 — fixed argv, no shell
            list(argv), capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return {"exit_code": -9, "stdout": "", "stderr": "", "timeout": True}
    except FileNotFoundError as exc:
        return {"exit_code": 127, "stdout": "", "stderr": f"docker binary not found: {exc}"}
    except OSError as exc:  # noqa: BLE001 — a broken host must come back as data
        return {"exit_code": 125, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}
    return {
        "exit_code": int(proc.returncode),
        "stdout": str(proc.stdout or "").strip(),
        "stderr": str(proc.stderr or "").strip(),
        "timeout": False,
    }


def _call(runner: Callable[[list[str], float], dict], argv: list[str], timeout: float) -> dict:
    """A runner call normalized to ``{exit_code, stdout, stderr, timeout}``."""
    try:
        result = runner(list(argv), float(timeout)) or {}
    except Exception as exc:  # noqa: BLE001 — an injected runner must not break a report
        return {"exit_code": 125, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}",
                "timeout": False}
    return {
        "exit_code": int(result.get("exit_code", 125)),
        "stdout": str(result.get("stdout", "") or ""),
        "stderr": str(result.get("stderr", "") or ""),
        "timeout": bool(result.get("timeout", False)),
    }


def _default_runtime():
    """The real lifecycle half (labels, one-shot run, label-scoped teardown)."""
    try:
        from . import docker_runtime as dr
    except Exception:  # noqa: BLE001 — a build without the runtime module
        return None
    return dr


def _default_manager() -> Any:
    dr = _default_runtime()
    if dr is None:
        return None
    try:
        return dr.DockerRuntimeManager()
    except Exception:  # noqa: BLE001
        return None


def _owned_labels(**extra: str) -> dict:
    """``godsv.owner=skaldir`` plus extras — the runtime's own label helper."""
    dr = _default_runtime()
    if dr is not None and hasattr(dr, "owned_labels"):
        return dict(dr.owned_labels(**extra))
    labels = {"godsv.owner": "skaldir"}
    labels.update({str(k): str(v) for k, v in extra.items() if str(v)})
    return labels


# --------------------------------------------------------------------------- #
# 1. image metadata — `docker image inspect` (no container, no shell)           #
# --------------------------------------------------------------------------- #


def image_metadata(runner: Callable[[list[str], float], dict], binary: str, image: str, *,
                   timeout: float = DEFAULT_TIMEOUT) -> dict:
    """The image's own declaration of itself, straight from the daemon.

    Returns ``{"ok": bool, "blocked": str, "reason": str, "config": {...}}``. The
    digest is *not* required here — a tag is enough to read metadata; pinning is a
    separate (and stricter) question answered by :func:`resolve_digest`.
    """
    result = _call(runner, [binary, "image", "inspect", "--format", "{{json .}}", image],
                   timeout)
    if result["exit_code"] != 0:
        text = (result["stderr"] or result["stdout"]).lower()
        if "no such image" in text or "not found" in text or "no such object" in text:
            return {"ok": False, "blocked": "image_not_found",
                    "reason": f"the image {image!r} is not local: "
                              f"{(result['stderr'] or result['stdout'])[:200]}",
                    "config": {}}
        return {"ok": False, "blocked": "docker_unreachable",
                "reason": f"`docker image inspect` did not answer for {image!r}: "
                          f"{(result['stderr'] or result['stdout'])[:200]}",
                "config": {}}
    try:
        raw = json.loads(result["stdout"])
    except json.JSONDecodeError as exc:
        return {"ok": False, "blocked": "metadata_unreadable",
                "reason": f"`docker image inspect` returned unparsable JSON: {exc}",
                "config": {}}
    if isinstance(raw, list):
        raw = raw[0] if raw else {}
    if not isinstance(raw, dict) or not raw:
        return {"ok": False, "blocked": "metadata_unreadable",
                "reason": "`docker image inspect` returned no image record", "config": {}}
    config = raw.get("Config") or {}
    return {"ok": True, "blocked": "", "reason": "", "config": _normalize_metadata(raw, config)}


def _normalize_metadata(raw: dict, config: dict) -> dict:
    """Only the fields this module is allowed to reason about — never GraphDriver."""
    labels = {str(k): str(v) for k, v in (config.get("Labels") or {}).items()}
    exposed = sorted(str(port) for port in (config.get("ExposedPorts") or {}))
    entrypoint = [str(part) for part in (config.get("Entrypoint") or [])]
    cmd = [str(part) for part in (config.get("Cmd") or [])]
    healthcheck = config.get("Healthcheck") or {}
    return {
        "id": str(raw.get("Id", "")),
        "architecture": str(raw.get("Architecture", "")),
        "os": str(raw.get("Os", "")),
        "size_bytes": int(raw.get("Size", 0) or 0),
        "created": str(raw.get("Created", "")),
        "repo_tags": [str(tag) for tag in (raw.get("RepoTags") or [])],
        "repo_digests": [str(digest) for digest in (raw.get("RepoDigests") or [])],
        "rootfs_layers": [str(item) for item in ((raw.get("RootFS") or {}).get("Layers") or [])],
        "user": str(config.get("User", "") or ""),
        "working_dir": str(config.get("WorkingDir", "") or ""),
        "entrypoint": entrypoint,
        "cmd": cmd,
        "env": [str(item) for item in (config.get("Env") or [])],
        "labels": labels,
        "exposed_ports": exposed,
        "volumes": sorted(str(path) for path in (config.get("Volumes") or {})),
        "healthcheck": {
            "test": [str(item) for item in (healthcheck.get("Test") or [])],
            "interval": int(healthcheck.get("Interval", 0) or 0),
        } if healthcheck else {},
        "entrypoint_uses_shell": _uses_shell(entrypoint + cmd),
        "declared_argv": [*(entrypoint or cmd)],
    }


def _uses_shell(argv: Iterable[str]) -> bool:
    """True when an argv element is a shell invocation (``sh -c …`` and friends)."""
    for part in argv or []:
        if _SHELL_TOKEN.search(str(part)):
            return True
    return False


def resolve_digest(runner: Callable[[list[str], float], dict], binary: str, image: str, *,
                   timeout: float = DEFAULT_TIMEOUT) -> str:
    """The local image id (``sha256:…``) — ``""`` when the image is not local."""
    result = _call(runner, [binary, "image", "inspect", "--format", "{{.Id}}", image], timeout)
    return result["stdout"].strip() if result["exit_code"] == 0 else ""


# --------------------------------------------------------------------------- #
# 2. filesystem — `docker save` streamed through tar (no container, no shell)   #
# --------------------------------------------------------------------------- #


def _spool_save(
    runner: Callable[[list[str], float], dict],
    binary: str,
    image: str,
    *,
    timeout: float,
    max_bytes: int,
    spool_dir: str | Path | None,
) -> dict:
    """Stream ``docker save <image>`` into a temporary tar file (bounded).

    Returns ``{"ok", "blocked", "reason", "path", "bytes", "truncated"}``. The
    spool file is the caller's to delete; a spooled image tar is the only way to
    read the layer tars twice (the layer index is written *after* the layers).
    """
    argv = [binary, "save", image]
    try:
        proc = subprocess.Popen(  # nosec B603 — fixed argv, no shell
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except FileNotFoundError as exc:
        return {"ok": False, "blocked": "docker_unreachable",
                "reason": f"docker binary not found: {exc}", "path": "", "bytes": 0,
                "truncated": False}
    except OSError as exc:  # noqa: BLE001
        return {"ok": False, "blocked": "docker_unreachable",
                "reason": f"{type(exc).__name__}: {exc}", "path": "", "bytes": 0,
                "truncated": False}

    directory = str(spool_dir) if spool_dir else None
    handle = None
    written = 0
    truncated = False
    deadline = time.time() + max(1.0, float(timeout))
    try:
        handle = tempfile.NamedTemporaryFile(
            prefix="godsv-save-", suffix=".tar", dir=directory, delete=False
        )
        stream = proc.stdout
        assert stream is not None  # noqa: S101 — Popen was told to pipe
        while True:
            if time.time() > deadline:
                truncated = True
                break
            chunk = stream.read(1 << 20)
            if not chunk:
                break
            if written + len(chunk) > max_bytes:
                chunk = chunk[: max(0, max_bytes - written)]
                truncated = True
            handle.write(chunk)
            written += len(chunk)
            if truncated:
                break
        handle.flush()
    except OSError as exc:  # noqa: BLE001
        if handle is not None:
            try:
                handle.close()
                Path(handle.name).unlink(missing_ok=True)
            except OSError:
                pass
        return {"ok": False, "blocked": "spool_failed",
                "reason": f"the image stream could not be spooled: {exc}",
                "path": "", "bytes": 0, "truncated": False}
    finally:
        if truncated:
            try:
                proc.kill()
            except OSError:
                pass
        stderr_text = ""
        try:
            if proc.stderr is not None:
                stderr_text = (proc.stderr.read() or b"").decode("utf-8", "replace").strip()
            proc.wait(timeout=30)
        except Exception:  # noqa: BLE001 — a hung CLI must not hang the report
            stderr_text = stderr_text or "docker save did not exit in time"
    path = handle.name if handle is not None else ""
    if handle is not None:
        handle.close()
    if written == 0:
        try:
            if path:
                Path(path).unlink(missing_ok=True)
        except OSError:
            pass
        return {"ok": False, "blocked": "save_failed",
                "reason": f"`docker save {image}` produced no bytes: {stderr_text[:200]}",
                "path": "", "bytes": 0, "truncated": False}
    return {"ok": True, "blocked": "", "reason": stderr_text[:200], "path": path,
            "bytes": written, "truncated": truncated}


def _member_name(name: str) -> str:
    """Normalized member path: no ``./``, no leading ``/``."""
    text = str(name or "")
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def _is_regular(member: tarfile.TarInfo) -> bool:
    return bool(member.isfile() or member.issym() or member.islnk())


def _layer_paths(outer: tarfile.TarFile, names: set[str]) -> tuple[list[str], str, list[str]]:
    """The layer members of a saved image, with the source that named them.

    Order of trust: ``manifest.json`` (docker's own index of the tar) → the OCI
    ``index.json`` chain → a magic scan of the compressed blobs. The source is
    returned so a heuristic read is never passed off as an authoritative one.
    """
    warnings: list[str] = []
    if "manifest.json" in names:
        try:
            fileobj = outer.extractfile("manifest.json")
            payload = json.loads(fileobj.read().decode("utf-8", "replace")) if fileobj else []
            if isinstance(payload, list) and payload:
                layers = [_member_name(item) for item in (payload[0].get("Layers") or [])]
                if layers:
                    return layers, "manifest.json", warnings
        except (OSError, ValueError) as exc:
            warnings.append(f"manifest.json unreadable: {exc}")

    if "index.json" in names:
        try:
            fileobj = outer.extractfile("index.json")
            index = json.loads(fileobj.read().decode("utf-8", "replace")) if fileobj else {}
            node = index
            for _hop in range(3):  # index → manifest (→ index → manifest)
                manifests = node.get("manifests") or []
                if not manifests:
                    break
                digest = str(manifests[0].get("digest", "")).split(":", 1)[-1]
                member = f"blobs/sha256/{digest}"
                if member not in names:
                    break
                blob = outer.extractfile(member)
                node = json.loads(blob.read().decode("utf-8", "replace")) if blob else {}
                layers = [f"blobs/sha256/{str(item.get('digest', '')).split(':', 1)[-1]}"
                          for item in (node.get("layers") or [])]
                if layers:
                    return layers, "index.json", warnings
        except (OSError, ValueError) as exc:
            warnings.append(f"index.json unreadable: {exc}")

    magic = [name for name in sorted(names)
             if name.startswith("blobs/") and _blob_magic(outer, name) == "gzip"]
    if magic:
        warnings.append(
            "the layer index was unreadable — layers were taken from the compressed "
            "blobs themselves (magic scan)"
        )
        return magic, "magic-scan", warnings
    return [], "none", warnings


def _blob_magic(outer: tarfile.TarFile, name: str) -> str:
    """``gzip`` / ``zstd`` / ``json`` / ``tar`` / ``""`` for one spooled member."""
    try:
        fileobj = outer.extractfile(name)
        if fileobj is None:
            return ""
        head = fileobj.read(512)
    except (OSError, ValueError):
        return ""
    if head[:2] == _GZIP_MAGIC:
        return "gzip"
    if head[:4] == _ZSTD_MAGIC:
        return "zstd"
    if head[:1] == b"{":
        return "json"
    if len(head) > _TAR_MAGIC_AT and head[_TAR_MAGIC_AT:_TAR_MAGIC_AT + 5] in (b"ustar", b"ustar"):
        return "tar"
    return ""


def filesystem_listing(path: str | Path, *, max_entries: int = MAX_ENTRIES_PER_LAYER) -> dict:
    """Read a spooled ``docker save`` tar and describe the image's filesystem.

    No shell is involved anywhere: the layer tars are decompressed in-process
    (``tarfile`` + ``gzip``) and the members are read directly.
    """
    filesystem: dict[str, Any] = {
        "layers": [], "layer_count": 0, "layer_source": "none", "entries_scanned": 0,
        "executables": [], "libraries": [], "directories": [], "shells": [],
        "os_release": {}, "truncated": False, "warnings": [],
    }
    try:
        outer = tarfile.open(str(path), "r")
    except (tarfile.TarError, OSError) as exc:
        filesystem["warnings"].append(f"the saved image is not a readable tar: {exc}")
        return filesystem

    executables: set[str] = set()
    libraries: set[str] = set()
    top_level: set[str] = set()
    shells: set[str] = set()
    os_release: dict[str, str] = {}
    scanned = 0
    try:
        names = set(outer.getnames())
        layer_paths, source, warnings = _layer_paths(outer, names)
        filesystem["layer_source"] = source
        filesystem["warnings"] += warnings
        scanned = 0
        for layer_index, layer_path in enumerate(layer_paths):
            record = {"path": layer_path, "digest": "", "media_type": "",
                      "status": "unreadable", "entries": 0, "executables": 0,
                      "libraries": 0}
            try:
                member = outer.getmember(layer_path)
                raw = outer.extractfile(member)
            except (KeyError, OSError, tarfile.TarError) as exc:
                record["status"] = f"missing: {exc}"
                filesystem["layers"].append(record)
                continue
            if raw is None:
                record["status"] = "missing (no payload)"
                filesystem["layers"].append(record)
                continue
            head = raw.read(4)
            raw.seek(0)
            mode = "r:"
            if head[:2] == _GZIP_MAGIC:
                mode = "r:gz"
            elif head[:4] == _ZSTD_MAGIC:
                record["status"] = "unsupported-compression: zstd"
                filesystem["warnings"].append(
                    f"layer {layer_index} uses zstd, which this Python cannot stream "
                    "through tarfile — its files are not listed"
                )
                filesystem["layers"].append(record)
                continue
            try:
                inner = tarfile.open(fileobj=raw, mode=mode)
            except (tarfile.TarError, OSError, gzip.BadGzipFile) as exc:
                record["status"] = f"not a layer tar: {exc}"
                filesystem["layers"].append(record)
                continue
            try:
                for item in inner:
                    scanned += 1
                    if scanned > max_entries:
                        filesystem["truncated"] = True
                        record["status"] = "truncated (entry cap reached)"
                        break
                    record["entries"] += 1
                    name = _member_name(item.name)
                    if name and "/" not in name:
                        top_level.add(name + "/" if item.isdir() else name)
                    if item.isdir() or not _is_regular(item):
                        continue
                    if name == "etc/os-release":
                        content = _read_text(inner, item, OS_RELEASE_MAX_BYTES)
                        os_release = _parse_os_release(content) or os_release
                    base = name.rsplit("/", 1)[-1]
                    if _is_executable_member(name, item):
                        executables.add("/" + name)
                        record["executables"] += 1
                        if base in SHELL_NAMES:
                            shells.add("/" + name)
                    if _is_library_member(name):
                        libraries.add("/" + name)
                        record["libraries"] += 1
            finally:
                inner.close()
            if record["status"] == "unreadable":
                record["status"] = "ok"
            filesystem["layers"].append(record)
    except (tarfile.TarError, OSError, KeyError) as exc:  # noqa: BLE001
        filesystem["warnings"].append(f"the layer walk stopped: {type(exc).__name__}: {exc}")
    finally:
        outer.close()

    filesystem["layer_count"] = len(filesystem["layers"])
    filesystem["entries_scanned"] = int(scanned)
    filesystem["executables"] = sorted(executables)
    filesystem["libraries"] = sorted(libraries)
    filesystem["shells"] = sorted(shells)
    filesystem["os_release"] = os_release
    filesystem["directories"] = sorted(name for name in top_level if name)
    return filesystem


def _read_text(inner: tarfile.TarFile, member: tarfile.TarInfo, limit: int) -> str:
    try:
        fileobj = inner.extractfile(member)
        if fileobj is None:
            return ""
        return fileobj.read(limit).decode("utf-8", "replace")
    except (OSError, tarfile.TarError):
        return ""


def _parse_os_release(text: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in str(text or "").splitlines():
        key, _, value = line.partition("=")
        key = key.strip()
        if key and value and not key.startswith("#"):
            parsed[key] = value.strip().strip('"').strip("'")
    return parsed


def _is_executable_member(name: str, member: tarfile.TarInfo) -> bool:
    """A binary/link a user could call: in a bin dir, or a root-level file."""
    if not name or name.endswith("/"):
        return False
    if "/" not in name:
        return True  # hello-world's /hello lives at the root
    directory, base = name.rsplit("/", 1)
    if directory in BIN_DIRS and base:
        return True
    return False


def _is_library_member(name: str) -> bool:
    if ".so" not in name:
        return False
    directory = name.rsplit("/", 1)[0] if "/" in name else ""
    return directory in LIB_DIRS or directory.endswith("/lib") or directory.endswith("/lib64")


def _shell_available(metadata: dict, filesystem: dict) -> dict:
    """Does anything in this image give us ``sh -c`` — the old probe's precondition?"""
    shells = sorted(filesystem.get("shells") or [])
    declared = [part for part in (metadata.get("entrypoint") or []) + (metadata.get("cmd") or [])
                if _SHELL_TOKEN.search(str(part))]
    available = bool(shells or declared)
    note = (
        "a shell is present (" + ", ".join(shells or declared) + ") — the sh -c route works"
        if available
        else "no shell in the image filesystem and none declared in entrypoint/cmd — "
             "the sh -c route cannot run here at all"
    )
    return {"available": available, "paths": shells, "declared": declared, "note": note}


# --------------------------------------------------------------------------- #
# 3. candidates + the three-way classification (pure functions — unit-testable) #
# --------------------------------------------------------------------------- #


def build_candidates(*, metadata: dict, filesystem: dict,
                     limit: int = DEFAULT_CANDIDATE_LIMIT) -> list[dict]:
    """Every surface candidate the shell-free evidence produced — as data.

    A candidate is only a *claim to be checked*: ``probe`` stays empty until an
    exec probe (or the metadata itself) answers it.
    """
    executables = list(filesystem.get("executables") or [])
    present = set(executables)
    candidates: list[dict] = []

    def add_cli(path: str, source: str, evidence: list[str]) -> None:
        if any(item["kind"] == "cli" and item["path"] == path for item in candidates):
            return
        candidates.append({
            "id": f"cli:{path}", "kind": "cli", "path": path,
            "basename": path.rsplit("/", 1)[-1], "source": source,
            "evidence": list(evidence), "probe": {},
        })

    for part in metadata.get("entrypoint") or []:
        if str(part).startswith("/") and str(part) in present:
            add_cli(str(part), "entrypoint", [f"Config.Entrypoint declares {part}",
                                              "the path exists in the image filesystem"])
    for part in metadata.get("cmd") or []:
        if str(part).startswith("/") and str(part) in present:
            add_cli(str(part), "cmd", [f"Config.Cmd declares {part}",
                                       "the path exists in the image filesystem"])
    for path in executables:
        base = path.rsplit("/", 1)[-1]
        if "mcp" in base.lower():
            candidates.append({
                "id": f"mcp:{path}", "kind": "mcp", "path": path, "basename": base,
                "source": "mcp-name",
                "evidence": [f"the executable name announces an MCP surface: {path}"],
                "probe": {},
            })
    for path in executables:
        base = path.rsplit("/", 1)[-1]
        if base in KNOWN_CLI_TOOLS:
            add_cli(path, "known-tool", [f"{base} is a well-known command-line tool",
                                         "the path exists in the image filesystem"])
    sampled = [path for path in executables
               if not any(item["path"] == path for item in candidates)]
    for path in sampled[: max(0, limit)]:
        add_cli(path, "filesystem-sample",
                ["found in the image filesystem (executables are sampled, not guessed)"])

    for port in metadata.get("exposed_ports") or []:
        candidates.append({
            "id": f"http:{port}", "kind": "http", "path": "", "basename": port,
            "source": "exposed-port",
            "evidence": [f"Config.ExposedPorts declares {port}",
                         "the surface is declared by the image metadata itself"],
            "probe": {},
        })
    http_labels = {key: value for key, value in (metadata.get("labels") or {}).items()
                   if any(hint in key.lower() for hint in HTTP_LABEL_HINTS)}
    if http_labels and not (metadata.get("exposed_ports") or []):
        candidates.append({
            "id": "http:label", "kind": "http", "path": "", "basename": "",
            "source": "labels",
            "evidence": ["metadata label(s) announce an HTTP surface: "
                         + ", ".join(sorted(http_labels))],
            "probe": {},
        })
    return candidates


def _api_server_names() -> tuple[str, ...]:
    """The API-server binary names, borrowed from the builder (never re-listed here).

    The builder owns that list; this module only reads it, so the two can never
    drift into two different definitions of "looks like an API server".
    """
    try:
        from . import toolbuilder as tb

        return tuple(getattr(tb, "_API_SERVER_HINTS", ()) or ())
    except Exception:  # noqa: BLE001 — a build without the builder
        return ()


def probe_targets(candidates: list[dict], *, limit: int) -> list[dict]:
    """Which candidates get a real ``docker run`` exec probe, and why.

    Deterministic priority: an MCP executable → the binary the image *declares*
    (``source`` ``entrypoint``/``cmd``) → a known server binary → other executables
    in path order. The choice is data on the report, so "we did not probe
    everything" is visible instead of implied.
    """
    rank = {"entrypoint": 0, "cmd": 1, "mcp-name": 2, "known-tool": 3,
            "filesystem-sample": 4}
    http_hint = set(_api_server_names())
    cli = [item for item in candidates if item["kind"] in ("cli", "mcp")]

    def score(item: dict) -> tuple[int, int, str]:
        base = item.get("basename", "")
        return (
            0 if item["kind"] == "mcp" else rank.get(str(item.get("source", "")), 9),
            0 if base in http_hint else 1,
            str(item.get("path", "")),
        )

    chosen = sorted(cli, key=score)[: max(0, int(limit))]
    for item in chosen:
        item["probe_selected"] = True
    return chosen


def classify_candidate(candidate: dict, *, shell_available: bool) -> tuple[str, str]:
    """The honest three-way answer for ONE candidate — ``(class, reason)``.

    * a candidate whose direct exec ran → ``inspectable`` (we have the evidence);
    * a candidate the shell-free route could not settle, in an image that *has* a
      shell → ``needs-shell`` (the existing ``sh -c`` route still answers it);
    * the same candidate in an image *without* a shell → ``impossible``.
    """
    kind = str(candidate.get("kind", ""))
    probe = candidate.get("probe") or {}
    if kind == "http":
        return (CLASS_INSPECTABLE,
                "declared by the image metadata (exposed port / label): nothing has to "
                "be executed, and no shell is involved")
    if probe.get("ran"):
        exit_code = probe.get("exit_code")
        excerpt = str(probe.get("stdout_excerpt") or probe.get("stderr_excerpt") or "")
        detail = f", {len(excerpt)} bytes of output" if excerpt else ", no output"
        return (CLASS_INSPECTABLE,
                f"the binary executed directly through `docker run` (exec, no shell), "
                f"exit {exit_code}{detail}")
    if probe.get("attempted"):
        failure = str(probe.get("failure_class") or f"exit {probe.get('exit_code')}")
        if shell_available:
            return (CLASS_NEEDS_SHELL,
                    f"the direct exec did not run ({failure}); this image carries a shell, "
                    f"so `sh -c \"{candidate.get('path', '')} --help\"` can still describe it")
        return (CLASS_IMPOSSIBLE,
                f"the direct exec did not run ({failure}) and the image carries no shell — "
                "neither the exec route nor a `sh -c` route can describe this candidate")
    if shell_available:
        return (CLASS_NEEDS_SHELL,
                "not probed directly (probe budget); this image carries a shell, so the "
                f"existing `sh -c` route could still describe {candidate.get('path', '')!r}")
    return (CLASS_IMPOSSIBLE,
            "not probed directly (probe budget) and the image carries no shell — no route "
            "is left to describe this candidate")


def classify_candidates(candidates: list[dict], *, shell_available: bool) -> dict:
    """Classify every candidate and summarize — the data the CLI reports."""
    records: dict[str, dict] = {}
    counts = {CLASS_INSPECTABLE: 0, CLASS_NEEDS_SHELL: 0, CLASS_IMPOSSIBLE: 0}
    for candidate in candidates:
        verdict, reason = classify_candidate(candidate, shell_available=shell_available)
        candidate["classification"] = verdict
        candidate["reason"] = reason
        counts[verdict] += 1
        records[str(candidate["id"])] = {"classification": verdict, "reason": reason,
                                         "kind": candidate["kind"]}
    if counts[CLASS_INSPECTABLE]:
        verdict = "shell-free"
    elif counts[CLASS_NEEDS_SHELL]:
        verdict = "needs-shell"
    elif counts[CLASS_IMPOSSIBLE]:
        verdict = "impossible"
    else:
        verdict = "no-candidates"
    return {
        "verdict": verdict,
        "counts": counts,
        "shell_available": bool(shell_available),
        "candidates": records,
        "note": (
            "the shell-free route produced usable evidence for "
            f"{counts[CLASS_INSPECTABLE]} candidate(s); "
            f"{counts[CLASS_NEEDS_SHELL]} need the in-image shell; "
            f"{counts[CLASS_IMPOSSIBLE]} cannot be described by any route here"
        ),
    }


# --------------------------------------------------------------------------- #
# 4. exec probes — one-shot, labelled, `--rm`, no shell                        #
# --------------------------------------------------------------------------- #


def exec_probe(manager: Any, *, image: str, image_digest: str, path: str,
               timeout: float = DEFAULT_EXEC_TIMEOUT, extra_args: Iterable[str] = ("--help",),
               runner: Callable[[list[str], float], dict] | None = None,
               binary: str = "") -> dict:
    """``docker run --rm <image> <path> [--help]`` — a direct exec, never ``sh -c``.

    The run goes through :class:`skaldir.docker_runtime.RunConfig`, so the container
    carries ``godsv.owner=skaldir`` (the label a teardown may use), ``--network
    none``, ``--read-only``, ``no-new-privileges`` and resource caps, and ``--rm``
    removes it when it exits. When no manager is available the probe is reported as
    *not attempted* rather than skipped silently.
    """
    argv = [str(path), *[str(part) for part in extra_args]]
    record: dict[str, Any] = {
        "attempted": True, "ran": False, "argv": argv, "exit_code": None,
        "stdout_excerpt": "", "stderr_excerpt": "", "failure_class": "",
        "detail": "", "labels": _owned_labels(**{"godsv.purpose": "inspect"}),
    }
    if runner is not None:
        result = _call(runner, [binary or "docker", "run", "--rm", "--label",
                                "godsv.owner=skaldir", image, *argv], timeout)
        record.update({
            "exit_code": result["exit_code"],
            "stdout_excerpt": result["stdout"][:400],
            "stderr_excerpt": result["stderr"][:400],
            "ran": not result["timeout"],
        })
        if not result["timeout"] and result["exit_code"] != 0:
            record["failure_class"] = "exec_nonzero"
            record["detail"] = "the binary ran but refused the arguments"
        return record

    if manager is None:
        record.update({"attempted": False, "detail":
                       "no container runtime available — the exec probe was not run"})
        return record
    dr = _default_runtime()
    if dr is None or not hasattr(dr, "RunConfig"):
        record.update({"attempted": False, "detail":
                       "skaldir.docker_runtime is not importable — the exec probe was not run"})
        return record
    try:
        config = dr.RunConfig(
            image=image,
            image_digest=image_digest,
            entrypoint_argv=argv,
            network="none",
            readonly_rootfs=True,
            resource_limits={"cpu": "1", "ram": "128m"},
            labels=record["labels"],
        )
        started = manager.run(config, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — a probe failure is data, not a crash
        record.update({"exit_code": None, "failure_class": type(exc).__name__,
                       "detail": f"the exec probe could not be started: {exc}"})
        return record
    exit_code = getattr(started, "exit_code", None)
    ok = bool(getattr(started, "ok", False))
    record.update({
        "exit_code": exit_code,
        "stdout_excerpt": str(getattr(started, "stdout", "") or "")[:400],
        "stderr_excerpt": str(getattr(started, "stderr", "") or "")[:400],
        "failure_class": str(getattr(started, "failure_class", "") or ""),
        "ran": exit_code is not None,
    })
    if not record["ran"]:
        record["detail"] = str(getattr(started, "detail", "") or "the container did not report "
                              "an exit code (it may have been killed by the timeout)")
    elif not ok and not record["failure_class"]:
        record["failure_class"] = "exec_nonzero"
        record["detail"] = (f"the binary ran and exited {exit_code} — its output is still "
                            "evidence that the surface exists")
    return record


def _container_counts(manager: Any) -> dict:
    """How many containers carry OUR label right now (never a name-based count)."""
    if manager is None or not hasattr(manager, "list_by_labels"):
        return {"listed": False, "owned": 0, "detail": "no label-scoped listing available"}
    labels = _owned_labels()
    try:
        listing = manager.list_by_labels(labels, timeout=30.0)
    except Exception as exc:  # noqa: BLE001
        return {"listed": False, "owned": 0, "detail": f"{type(exc).__name__}: {exc}"}
    containers = list(getattr(listing, "payload", {}).get("containers", []) or [])
    return {
        "listed": bool(getattr(listing, "ok", False)),
        "owned": len(containers),
        "ids": containers,
        "scope": str(getattr(listing, "payload", {}).get("filters", "") or ""),
        "detail": str(getattr(listing, "detail", "") or ""),
    }


def _sweep_delta(manager: Any, before: dict, after: dict) -> dict:
    """Tear down ONLY the containers that appeared during this run — by id.

    A label-scoped blanket sweep would also remove a sibling block's labelled
    containers on this shared daemon, so the scope is the *delta*: ids that the
    post-run label listing has and the pre-run listing did not. Removal is always
    ``docker stop``/``docker rm -f`` on an explicit id — never a name, never a
    prune. When either listing failed, no sweep happens at all and that is stated.
    """
    if not before.get("listed") or not after.get("listed"):
        return {"swept": False, "removed": [], "failure_class": "listing_unavailable",
                "detail": "the label-scoped listing did not answer — no sweep was attempted "
                          "(a removal without a trustworthy scope is never done)",
                "before_owned": int(before.get("owned", 0)),
                "after_owned": int(after.get("owned", 0))}
    known = {str(item) for item in before.get("ids", [])}
    new_ids = [str(item) for item in after.get("ids", []) if str(item) not in known]
    removed: list[str] = []
    errors: list[str] = []
    for cid in new_ids:
        if manager is None or not hasattr(manager, "stop_and_remove"):
            errors.append(f"{cid}: no stop_and_remove available")
            continue
        try:
            result = manager.stop_and_remove(cid, timeout=30.0)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{cid}: {type(exc).__name__}: {exc}")
            continue
        if getattr(result, "ok", False):
            removed.append(cid)
        else:
            errors.append(f"{cid}: {str(getattr(result, 'detail', '') or '')[:120]}")
    # A removal that reported success can still be lying (or still be in flight) — the
    # honest close is a second listing, not the removal call's own return value.
    remaining: list[str] = []
    if new_ids and manager is not None and hasattr(manager, "list_by_labels"):
        try:
            post = manager.list_by_labels(_owned_labels(), timeout=30.0)
            if getattr(post, "ok", False):
                live = {str(item) for item in getattr(post, "payload", {}).get("containers", [])}
                remaining = [cid for cid in new_ids if cid in live]
            else:
                remaining = list(new_ids)  # cannot prove it is gone → report it as open
        except Exception as exc:  # noqa: BLE001
            remaining = list(new_ids)
            errors.append(f"post-sweep listing failed: {type(exc).__name__}: {exc}")
    return {
        "swept": not errors and not remaining,
        "removed": removed,
        "new": new_ids,
        "remaining": remaining,
        "failure_class": "cleanup_partial" if (errors or remaining) else "",
        "detail": (
            f"removed {len(removed)} container(s) that appeared during this run"
            if not errors and not remaining
            else ("; ".join(errors) or "removal was attempted but the container is still listed: "
                  + ", ".join(remaining))
        ),
        "scope": str(after.get("scope", "") or before.get("scope", "")),
        "before_owned": int(before.get("owned", 0)),
        "after_owned": int(after.get("owned", 0)),
    }


# --------------------------------------------------------------------------- #
# 5. the surface in the builder's shape + the adapter handoff                   #
# --------------------------------------------------------------------------- #


def surface_for_adapter(report: dict, *, verb_limit: int = DEFAULT_VERB_LIMIT) -> dict:
    """The inspected surface in the SAME shape ``toolbuilder`` already consumes.

    ``verbs`` are the executable basenames the filesystem really carries (what the
    old ``ls -1 /bin /usr/bin`` inside a shell used to produce), so the existing
    generator and its verb templates work unchanged. ``inspectable_paths`` names
    which of them were proven to execute.
    """
    metadata = report.get("metadata") or {}
    filesystem = report.get("filesystem") or {}
    executables = list(filesystem.get("executables") or [])
    verbs = sorted({path.rsplit("/", 1)[-1] for path in executables})
    truncated = len(verbs) > verb_limit
    verbs = verbs[:verb_limit]
    proven = sorted({str(item.get("basename", "")) for item in (report.get("candidates") or [])
                     if item.get("classification") == CLASS_INSPECTABLE and item.get("basename")})
    mcp_names = sorted({str(item.get("basename", "")) for item in (report.get("candidates") or [])
                        if item.get("kind") == "mcp"})
    api_hint = next((name for name in _api_server_names() if name in set(verbs)), "")
    probes = list(report.get("probes") or [])
    return {
        "inspected": True,
        "failure_class": "",
        "method": "shell-free",
        "image": report.get("image", ""),
        "image_digest": report.get("image_digest", ""),
        "os": dict(filesystem.get("os_release") or {}),
        "cli": {
            "verbs": verbs,
            "count": len(verbs),
            "truncated": truncated,
            "proven": proven,
            "probe": ["docker save <image> | tar (layer members: bin/, sbin/, usr/bin/, …)"],
            "source": "image layers",
        },
        "api": {
            "server_hint": api_hint,
            "paths": [],
            "ports": list(metadata.get("exposed_ports") or []),
            "note": (
                f"the metadata declares port(s) {', '.join(metadata.get('exposed_ports') or [])}"
                if metadata.get("exposed_ports")
                else "no port declared in the image metadata"
            ),
        },
        "mcp": {
            "executable": mcp_names[0] if mcp_names else "",
            "note": (f"an MCP executable is present: {mcp_names[0]}" if mcp_names
                     else "no MCP executable in the inspected surface"),
        },
        "shell": dict(report.get("shell") or {}),
        "probes": probes,
        "inspected_at": float(report.get("inspected_at") or time.time()),
    }


def handoff_to_adapter(report: dict, *, capability: str, name: str = "",
                       template: dict | None = None) -> dict:
    """Hand the inspected surface to the EXISTING generator — no logic copied here.

    ``skaldir.toolbuilder.generate_container_adapter`` owns the verb matching and its
    refusals; this function only passes the surface over and reports what came back.
    Because the generated argv (and the warm-exec service) of today's generator
    still spells out ``sh -c``, a result for a shell-less image is marked
    ``generated-with-warning`` instead of being presented as callable.
    """
    if not capability:
        return {"handoff": "not-requested", "reason": "no --capability was given"}
    try:
        from . import toolbuilder as tb

        generate = tb.generate_container_adapter
        refusals = tuple(
            item for item in (getattr(tb, "SurfaceRefusal", None),
                              getattr(tb, "AdapterRefusal", None)) if item is not None
        )
        exceptions = refusals or (Exception,)
    except Exception as exc:  # noqa: BLE001 — a build without the builder
        return {"handoff": "unavailable",
                "reason": f"the adapter generator (skaldir.toolbuilder) is not importable "
                          f"in this build: {exc}"}
    surface = surface_for_adapter(report)
    tool_name = name or _slug(f"{report.get('image', 'image')}-{capability}")
    try:
        adapter = generate(capability, name=tool_name, image=report.get("image", ""),
                           image_digest=report.get("image_digest", ""), surface=surface,
                           template=template)
    except exceptions as exc:  # the generator's own honest refusal
        return {
            "handoff": "refused",
            "reason": f"{type(exc).__name__}: {exc}",
            "surface_verbs": surface["cli"]["verbs"][:40],
            "surface_verb_count": surface["cli"]["count"],
        }
    except Exception as exc:  # noqa: BLE001
        return {"handoff": "failed", "reason": f"{type(exc).__name__}: {exc}"}
    needs_shell = _adapter_needs_shell(adapter)
    shell_available = bool((report.get("shell") or {}).get("available"))
    result = {"handoff": "generated", "adapter": adapter,
              "adapter_needs_shell": needs_shell,
              "shell_available": shell_available}
    if needs_shell and not shell_available:
        result["handoff"] = "generated-with-warning"
        result["warning"] = (
            "the generated adapter's argv (or its warm-exec service) still calls a shell, "
            "and this image has no shell: it would fail at call time. The adapter is "
            "reported, not registered — a shell-free argv has to come from "
            "skaldir.toolbuilder's generator, which owns that logic."
        )
    return result


def _adapter_needs_shell(adapter: dict) -> bool:
    """True when any generated argv still spells out a shell invocation."""
    parts: list[str] = list(adapter.get("argv_template") or [])
    parts += list(((adapter.get("service") or {}).get("argv")) or [])
    return _uses_shell(parts)


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return slug[:60] or "image-tool"


# --------------------------------------------------------------------------- #
# 6. the one entry point                                                       #
# --------------------------------------------------------------------------- #


def inspect_image(
    *,
    image: str,
    image_digest: str = "",
    manager: Any = None,
    runner: Callable[[list[str], float], dict] | None = None,
    capability: str = "",
    name: str = "",
    probe_exec: bool = True,
    exec_probe_limit: int = DEFAULT_EXEC_PROBES,
    exec_timeout: float = DEFAULT_EXEC_TIMEOUT,
    timeout: float = DEFAULT_TIMEOUT,
    max_save_bytes: int = DEFAULT_MAX_SAVE_BYTES,
    spool_dir: str | Path | None = None,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
    handoff: bool = True,
    compare_shell: bool = False,
) -> dict:
    """Inspect ``image`` without a shell and classify every candidate — as data.

    Never raises for a request: a missing image, an unreachable daemon, an
    unreadable stream and a refused capability all come back as structured
    ``ok: False`` + ``blocked`` or as a classification record. ``probe_exec=False``
    keeps the whole run container-free (metadata + layers only).
    """
    started = time.time()
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "blocked": "",
        "reason": "",
        "image": str(image or ""),
        "image_digest": str(image_digest or ""),
        "method": "shell-free",
        "container_started": False,
        "metadata": {},
        "filesystem": {},
        "shell": {},
        "http": {},
        "candidates": [],
        "classification": {},
        "probes": [],
        "adapter": {"handoff": "not-requested", "reason": "inspection did not complete"},
        "baseline_shell_path": None,
        "containers": {"before": {}, "after": {}, "sweep": {}},
        "inspected_at": started,
        "duration_ms": 0,
    }
    if not str(image or "").strip():
        report.update({"blocked": "image_required",
                       "reason": "no image was given to inspect"})
        return report

    if manager is None and runner is None:
        manager = _default_manager()
    binary = _binary_of(manager)
    call = runner or _subprocess_runner

    metadata_result = image_metadata(call, binary, image, timeout=timeout)
    if not metadata_result["ok"]:
        report.update({"blocked": metadata_result["blocked"],
                       "reason": metadata_result["reason"]})
        return report
    metadata = metadata_result["config"]
    report["metadata"] = metadata
    report["image_digest"] = str(image_digest or "") or str(metadata.get("id") or "")
    digest = report["image_digest"]
    if not (digest.startswith("sha256:") and len(digest) == 71):
        report.update({"blocked": "pin_required",
                       "reason": "no pin-shaped image digest — an untraceable mutable tag "
                                 "cannot be inspected"})
        return report

    spool = _spool_save(call, binary, digest, timeout=timeout, max_bytes=max_save_bytes,
                        spool_dir=spool_dir)
    if not spool["ok"]:
        report.update({"blocked": spool["blocked"], "reason": spool["reason"]})
        return report
    report["spool"] = {"bytes": spool["bytes"], "truncated": spool["truncated"],
                       "reason": spool["reason"]}
    try:
        filesystem = filesystem_listing(spool["path"])
    finally:
        try:
            Path(spool["path"]).unlink(missing_ok=True)
        except OSError:
            pass
    filesystem["truncated"] = bool(filesystem.get("truncated") or spool["truncated"])
    report["filesystem"] = filesystem
    report["shell"] = _shell_available(metadata, filesystem)
    report["http"] = {
        "exposed_ports": list(metadata.get("exposed_ports") or []),
        "labels": {key: value for key, value in (metadata.get("labels") or {}).items()
                   if any(hint in key.lower() for hint in HTTP_LABEL_HINTS)},
        "volumes": list(metadata.get("volumes") or []),
        "user": str(metadata.get("user", "")),
    }
    candidates = build_candidates(metadata=metadata, filesystem=filesystem,
                                 limit=candidate_limit)
    report["candidates"] = candidates
    report["candidate_summary"] = {
        "total": len(candidates),
        "executables_in_image": len(filesystem.get("executables") or []),
        "sampled": sum(1 for item in candidates if item.get("source") == "filesystem-sample"),
        "note": ("every candidate is listed with the evidence that produced it; the "
                 "filesystem sample is capped so a big image does not flood the report"),
    }

    if probe_exec:
        report["containers"]["before"] = _container_counts(manager)
        targets = probe_targets(candidates, limit=exec_probe_limit)
        for candidate in targets:
            record = exec_probe(manager, image=digest, image_digest=digest,
                               path=candidate["path"], timeout=exec_timeout)
            candidate["probe"] = record
            report["probes"].append({"probe": f"exec:{candidate['path']}", **record})
            report["container_started"] = report["container_started"] or bool(
                record.get("attempted")
            )

    report["classification"] = classify_candidates(
        candidates, shell_available=bool(report["shell"].get("available"))
    )
    report["ok"] = True
    report["blocked"] = ""

    if handoff:
        report["adapter"] = handoff_to_adapter(report, capability=capability, name=name)
    if compare_shell:
        report["baseline_shell_path"] = _baseline_shell_path(
            manager, image=image, image_digest=digest, timeout=timeout
        )
    if probe_exec:
        report["containers"]["after"] = _container_counts(manager)
        report["containers"]["sweep"] = _sweep_delta(
            manager, report["containers"]["before"], report["containers"]["after"]
        )
    report["duration_ms"] = int((time.time() - started) * 1000)
    return report


def _baseline_shell_path(manager: Any, *, image: str, image_digest: str,
                         timeout: float) -> dict | None:
    """What the existing ``sh -c`` inspector says about the same image — for contrast.

    Imported defensively: the baseline is a *comparison*, never a dependency. On an
    image without a shell this is exactly the refusal this module exists to replace.
    """
    if manager is None:
        return None
    try:
        from . import toolbuilder as tb

        surface = tb.inspect_container_surface(manager, image=image, image_digest=image_digest,
                                               timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    return {
        "available": True,
        "inspected": bool(surface.get("inspected")),
        "failure_class": str(surface.get("failure_class", "")),
        "reason": str(surface.get("reason", ""))[:400],
        "verbs_found": int(((surface.get("cli") or {}).get("count")) or 0),
    }


def render_text(report: dict) -> str:
    """The human rendering — one screen, English, no claim without its number."""
    lines: list[str] = []
    metadata = report.get("metadata") or {}
    filesystem = report.get("filesystem") or {}
    classification = report.get("classification") or {}
    if not report.get("ok"):
        lines.append(f"image {report.get('image')!r}: BLOCKED ({report.get('blocked')}) — "
                     f"{report.get('reason')}")
        return "\n".join(lines)
    lines.append(f"image {report.get('image')}  digest {report.get('image_digest')}")
    lines.append(f"  method: {report.get('method')} (no shell needed) · container started: "
                 f"{report.get('container_started')}")
    lines.append(f"  architecture {metadata.get('architecture')}/{metadata.get('os')} · "
                 f"{metadata.get('size_bytes')} bytes · user "
                 f"{metadata.get('user') or '(default)'}")
    lines.append(f"  entrypoint {metadata.get('entrypoint') or '(none)'} · "
                 f"cmd {metadata.get('cmd') or '(none)'}")
    lines.append(f"  exposed ports {metadata.get('exposed_ports') or '(none)'} · "
                 f"labels {len(metadata.get('labels') or {})}")
    os_release = filesystem.get("os_release") or {}
    lines.append(f"  os-release {os_release.get('PRETTY_NAME') or '(not in the image)'}")
    lines.append(f"  layers {filesystem.get('layer_count')} (source "
                 f"{filesystem.get('layer_source')}) · entries scanned "
                 f"{filesystem.get('entries_scanned')} · executables "
                 f"{len(filesystem.get('executables') or [])} · libraries "
                 f"{len(filesystem.get('libraries') or [])}")
    shell = report.get("shell") or {}
    lines.append(f"  shell: {'yes' if shell.get('available') else 'NO'} — {shell.get('note')}")
    lines.append(f"  classification: {classification.get('verdict')} "
                 f"{classification.get('counts')}")
    for item in report.get("candidates") or []:
        if item.get("kind") != "cli":
            continue
        lines.append(f"    [{item.get('classification')}] {item.get('kind')} "
                     f"{item.get('path')} ({item.get('source')}) — {item.get('reason')}")
    for item in report.get("candidates") or []:
        if item.get("kind") == "cli":
            continue
        lines.append(f"    [{item.get('classification')}] {item.get('kind')} "
                     f"{item.get('basename') or item.get('path')} — {item.get('reason')}")
    adapter = report.get("adapter") or {}
    lines.append(f"  adapter handoff: {adapter.get('handoff')} — "
                 f"{str(adapter.get('reason') or adapter.get('warning') or '')[:300]}")
    baseline = report.get("baseline_shell_path")
    if baseline:
        lines.append(f"  baseline sh -c path: inspected={baseline.get('inspected')} "
                     f"failure_class={baseline.get('failure_class')!r} "
                     f"— {str(baseline.get('reason') or '')[:200]}")
    containers = report.get("containers") or {}
    if containers.get("before"):
        lines.append(f"  our containers before/after: "
                     f"{(containers.get('before') or {}).get('owned')}/"
                     f"{(containers.get('after') or {}).get('owned')} · sweep removed "
                     f"{len((containers.get('sweep') or {}).get('removed') or [])}")
    return "\n".join(lines)


def layer_excerpt(filesystem: dict, *, limit: int = 12) -> dict:
    """The proof-shaped excerpt of the layer walk (used by ``image layers``)."""
    return {
        "layer_source": filesystem.get("layer_source"),
        "layer_count": filesystem.get("layer_count"),
        "entries_scanned": filesystem.get("entries_scanned"),
        "truncated": bool(filesystem.get("truncated")),
        "layers": [
            {"path": item.get("path"), "status": item.get("status"),
             "entries": item.get("entries"), "executables": item.get("executables"),
             "libraries": item.get("libraries")}
            for item in (filesystem.get("layers") or [])
        ],
        "directories": list(filesystem.get("directories") or [])[:limit],
        "executables": list(filesystem.get("executables") or [])[:limit],
        "executables_total": len(filesystem.get("executables") or []),
        "libraries": list(filesystem.get("libraries") or [])[:limit],
        "libraries_total": len(filesystem.get("libraries") or []),
        "shells": list(filesystem.get("shells") or []),
        "os_release": dict(filesystem.get("os_release") or {}),
        "warnings": list(filesystem.get("warnings") or []),
    }


def layers_only(*, image: str, image_digest: str = "", manager: Any = None,
                runner: Callable[[list[str], float], dict] | None = None,
                timeout: float = DEFAULT_TIMEOUT,
                max_save_bytes: int = DEFAULT_MAX_SAVE_BYTES,
                spool_dir: str | Path | None = None,
                excerpt_limit: int = 12) -> dict:
    """Metadata + layer walk only — no exec probe, no container, ever."""
    report = inspect_image(image=image, image_digest=image_digest, manager=manager,
                           runner=runner, probe_exec=False, timeout=timeout,
                           max_save_bytes=max_save_bytes, spool_dir=spool_dir,
                           handoff=False)
    if not report.get("ok"):
        return report
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "image": report["image"],
        "image_digest": report["image_digest"],
        "method": "shell-free",
        "container_started": False,
        "metadata": report["metadata"],
        "shell": report["shell"],
        "http": report["http"],
        "filesystem": layer_excerpt(report["filesystem"], limit=excerpt_limit),
        "candidates": report["candidates"],
        "classification": report["classification"],
        "inspected_at": report["inspected_at"],
        "duration_ms": report["duration_ms"],
    }


__all__ = [
    "CLASS_IMPOSSIBLE",
    "CLASS_INSPECTABLE",
    "CLASS_NEEDS_SHELL",
    "SCHEMA_VERSION",
    "build_candidates",
    "classify_candidate",
    "classify_candidates",
    "exec_probe",
    "filesystem_listing",
    "handoff_to_adapter",
    "image_metadata",
    "inspect_image",
    "layer_excerpt",
    "layers_only",
    "probe_targets",
    "render_text",
    "resolve_digest",
    "surface_for_adapter",
]
