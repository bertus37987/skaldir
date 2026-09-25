"""Environment variable lookup with the legacy ``GODSV_*`` names as fallback.

The product reads ``SKALDIR_*``. Every ``GODSV_*`` name that existed before the
rename is still accepted, and it is accepted *everywhere*: no module in
:mod:`skaldir` reads the environment directly any more, so the fallback cannot be
forgotten in one place and present in another.

Two rules, and they are different on purpose:

``getenv("HOME")`` / ``getenv("SKALDIR_HOME")``
    the bare token (``"HOME"``) is expanded to ``SKALDIR_HOME``, and the legacy
    ``GODSV_HOME`` is tried when the primary one is *not present*. Presence — not
    truthiness — decides, so an explicitly empty primary value stays empty
    instead of silently falling back.

``first_env(("SKALDIR_LLM_BASE_URL", "OPENAI_BASE_URL"))``
    an ordered list of names (the LLM layer's existing contract), each primary
    name followed by its legacy twin, first **non-empty** value wins — the rule
    that list already had.

``child_env({"CHECK_ID": …})``
    the *write* side: both spellings, because the child may be older than the
    rename (a declared check script, a generated tool).

``host_getenv("HERMES_HOME")``
    the **host-owned** names (:data:`HOST_NAMES`). They are absolute: no
    ``SKALDIR_`` twin exists and prefixing one would invent a variable nobody
    sets. They are read *here* rather than at the call site for one reason —
    "no module in the package reads the environment directly" stays a rule a
    test can enforce, and a leaked ``HERMES_HOME``/``SKALDIR_HOME`` in a shell
    is then a value this module hands out instead of a second read nobody sees.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping, MutableMapping
from pathlib import Path
from typing import Any

__all__ = [
    "FRAME_ENV",
    "HOST_NAMES",
    "LEGACY_PREFIX",
    "PRIMARY_PREFIX",
    "child_env",
    "expand",
    "first_env",
    "load_env_file_into",
    "flag",
    "getenv",
    "host_getenv",
    "host_isset",
    "isset",
    "legacy_name",
    "primary_name",
    "source",
]

PRIMARY_PREFIX = "SKALDIR_"
LEGACY_PREFIX = "GODSV_"

#: Set while a frame owns the terminal. A product command that would open a *second*
#: terminal surface (``skaldir run "<goal>"`` opens the chat attached to the goal on a tty)
#: reads it and runs in the foreground instead, so the command returns to the frame that is
#: already up — one definition, read by ``chatbar`` (which sets it) and the run group.
FRAME_ENV = "SKALDIR_FRAME"

#: Variables the **host** owns. They carry no product prefix (``HERMES_HOME``, not
#: ``SKALDIR_HERMES_HOME``) and must be read literally — the set is explicit so a
#: typo (``host_getenv("SKALDIR_HOME")``) is a decision, not an accident.
HOST_NAMES: tuple[str, ...] = (
    "HERMES_HOME",
    "HERMES_DESKTOP_HERMES_ROOT",
    "XDG_DATA_HOME",
    "XDG_RUNTIME_DIR",
    "XDG_STATE_HOME",
)

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def primary_name(name: str) -> str:
    """``"HOME"`` → ``"SKALDIR_HOME"`` (an already-prefixed name is returned as-is)."""
    text = str(name)
    if text.startswith((PRIMARY_PREFIX, LEGACY_PREFIX)):
        return text
    return PRIMARY_PREFIX + text


def legacy_name(name: str) -> str | None:
    """``"SKALDIR_HOME"`` → ``"GODSV_HOME"``; ``None`` when there is no twin."""
    text = str(name)
    if text.startswith(PRIMARY_PREFIX):
        return LEGACY_PREFIX + text[len(PRIMARY_PREFIX):]
    return None


def _aliases(name: str, *, primary_first: bool = True) -> tuple[str, ...]:
    """One name → ``(primary, legacy)``, both spellings, de-duplicated."""
    text = primary_name(name)
    twin = legacy_name(text)
    if twin is None or twin == text:
        return (text,)
    return (text, twin) if primary_first else (twin, text)


def _literal_aliases(name: str) -> tuple[str, ...]:
    """A name that is *already* absolute: its legacy twin, or itself.

    ``SKALDIR_LLM_BASE_URL`` → ``(SKALDIR_LLM_BASE_URL, GODSV_LLM_BASE_URL)``;
    ``OPENAI_API_KEY`` (a third-party name with no ``SKALDIR_`` meaning) → itself,
    unchanged — prefixing it would invent a variable nobody sets.
    """
    text = str(name)
    if text.startswith(PRIMARY_PREFIX):
        return (text, LEGACY_PREFIX + text[len(PRIMARY_PREFIX):])
    return (text,)


def expand(names: Iterable[str]) -> tuple[str, ...]:
    """``("SKALDIR_A", "OPENAI_KEY")`` → ``("SKALDIR_A", "GODSV_A", "OPENAI_KEY")``.

    A name without a ``SKALDIR_`` spelling (``OPENAI_KEY``) is passed through
    with no twin, so an existing third-party fallback list keeps its order and
    its meaning.
    """
    out: list[str] = []
    for name in names:
        for candidate in _literal_aliases(name):
            if candidate not in out:
                out.append(candidate)
    return tuple(out)


def _environ(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def getenv(token: str, default: str | None = None, *, environ=None) -> str | None:
    """``SKALDIR_<token>``, else the legacy ``GODSV_<token>``, else ``default``.

    The first name that is *present* wins (an explicit empty value is a value).
    """
    env = _environ(environ)
    for name in _aliases(token):
        if name in env:
            return env[name]
    return default


def isset(token: str, *, environ=None) -> bool:
    """True when the primary or the legacy spelling of ``token`` is present."""
    env = _environ(environ)
    return any(name in env for name in _aliases(token))


def source(token: str, *, environ=None) -> str | None:
    """The name that `getenv` would read, or ``None`` when neither is present.

    For a report that has to say *which* variable was used — naming the primary
    one when the operator set the legacy one would be a lie.
    """
    env = _environ(environ)
    for name in _aliases(token):
        if name in env:
            return name
    return None


def flag(token: str, default: bool = False, *, environ=None) -> bool:
    """``getenv`` interpreted as a boolean (``1/true/yes/on``)."""
    value = getenv(token, None, environ=environ)
    if value is None:
        return default
    return str(value).strip().lower() in _TRUTHY


def host_getenv(token: str, default: str | None = None, *, environ=None) -> str | None:
    """The literal value of a **host-owned** variable (see :data:`HOST_NAMES`).

    No prefix is applied and no legacy twin is tried: ``HERMES_HOME`` is the host's
    own name, and ``SKALDIR_HERMES_HOME`` does not exist. A name that is not in
    :data:`HOST_NAMES` is a caller bug and raises, so routing a product variable
    through the host reader cannot silently skip the ``GODSV_*`` fallback.
    """
    text = str(token)
    if text not in HOST_NAMES:
        raise KeyError(
            f"{text!r} is not a host-owned variable (known: {list(HOST_NAMES)}) — read it "
            "with getenv()/first_env() so the GODSV_* fallback applies"
        )
    env = _environ(environ)
    return env[text] if text in env else default


def host_isset(token: str, *, environ=None) -> bool:
    """True when a host-owned variable is present (see :func:`host_getenv`)."""
    return host_getenv(token, None, environ=environ) is not None


def first_env(names: Iterable[str], default: str | None = None, *, environ=None) -> str | None:
    """Ordered lookup over an existing fallback list; first non-empty value wins.

    Each ``SKALDIR_*`` name is tried before its legacy ``GODSV_*`` twin, which is
    exactly what the LLM layer's ordered env lists need after a rename.
    """
    env = _environ(environ)
    for name in expand(names):
        value = env.get(name)
        if value is not None and str(value).strip() != "":
            return value
    return default


def child_env(pairs: Mapping[str, str]) -> dict[str, str]:
    """Both spellings of a variable this process *sets* for a child process.

    ``{"CHECK_ID": "pytest"}`` → ``{"SKALDIR_CHECK_ID": "pytest", "GODSV_CHECK_ID": "pytest"}``.

    A child that was generated, written or configured before the rename still reads
    the old name (a declared check script reading ``$GODSV_CHECK_ID``, a generated
    tool reading ``$GODSV_SECRET_…``), so both are exported. The primary spelling is
    the one documented; the legacy one exists only for that child.
    """
    out: dict[str, str] = {}
    for token, value in pairs.items():
        text = str(value)
        for name in _aliases(token):
            out[name] = text
    return out


def load_env_file_into(
    environ: MutableMapping[str, str] | None = None,
    *,
    path: str | Path = "",
    config_path: str | Path | None = None,
    override: bool = False,
) -> dict[str, Any]:
    """Load the credentials file ``skaldir setup`` writes into an environment.

    Setup stores the two keys in ``.env`` (0600, beside ``config.yaml``) and validates them
    from there — but a run reads the *process environment*. Without this step a user who
    ran ``skaldir setup`` is told "no credentials" by the very next command, which is the
    gap this closes.

    Rules, in order:

    * an **already-set, non-empty** variable always wins (``override=True`` is for tests);
    * empty values in the file set nothing;
    * a missing or unreadable file is *reported*, never raised — a hint must not be why a
      command fails;
    * nothing is written anywhere and the values never reach a log.

    Returns the honest report: ``{"path", "exists", "loaded", "kept", "error"}``.
    """
    target = environ if environ is not None else os.environ
    report: dict[str, Any] = {
        "path": "",
        "exists": False,
        "loaded": [],
        "kept": [],
        "loaded_count": 0,
        "error": "",
    }
    try:
        from .setup_flow import env_file_path, read_env_file

        resolved = (
            Path(str(path)).expanduser()
            if str(path or "").strip()
            else env_file_path(config_path=config_path, env=dict(target))
        )
        report["path"] = str(resolved)
        credential_file = read_env_file(resolved)
    except Exception as exc:  # noqa: BLE001 — an unreadable hint is reported, not raised
        report["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return report
    report["exists"] = bool(credential_file.exists)
    for name, value in dict(credential_file.values or {}).items():
        text = str(value)
        if not text.strip():
            continue
        if not override and str(target.get(name, "") or "").strip():
            report["kept"].append(name)
            continue
        target[name] = text
        report["loaded"].append(name)
    report["loaded_count"] = len(report["loaded"])
    return report
