"""The deprecation surface of the rename — one place, one policy.

Two old names are still accepted and both must announce themselves without ever
polluting the product's streams:

``godsv`` (the package) emits a :class:`DeprecationWarning`; ``godsvoice`` (the
console script) writes one notice line to **stderr**.

Both are gated on ``stderr`` being a TTY. A pipe, a redirect or a log file stays
byte-clean, because the machine-readable output of this product is parsed by
other programs (``--json`` envelopes, ``x-godsv-*`` OpenAPI extensions, the
desktop app's stream reader) and a stray notice line in a log is a lie about the
program's output.
"""

from __future__ import annotations

import sys
import warnings

__all__ = [
    "LEGACY_COMMAND",
    "LEGACY_PACKAGE",
    "NOTICE",
    "WARNING",
    "notice_deprecated_alias",
    "stderr_is_a_tty",
    "warn_deprecated_package",
]

#: The pre-rename console script.
LEGACY_COMMAND = "godsvoice"
#: The pre-rename import package.
LEGACY_PACKAGE = "godsv"

NOTICE = (
    f"note: `{LEGACY_COMMAND}` is deprecated and will be removed in a future release — "
    "use `skaldir` (same commands, same output, same exit codes)."
)

WARNING = (
    f"'{LEGACY_PACKAGE}' is deprecated and will be removed in a future release: "
    "import 'skaldir' instead (the package was renamed, the code is the same)."
)


def stderr_is_a_tty() -> bool:
    """True only for a real terminal (a captured/redirected stderr is not)."""
    stream = sys.stderr
    if stream is None:
        return False
    try:
        return bool(stream.isatty())
    except Exception:  # noqa: BLE001 — a closed or exotic stream is not a TTY
        return False


def script_stem(argv0: str = "") -> str:
    """``/path/to/godsvoice`` → ``godsvoice`` (``.exe`` stripped, case kept)."""
    name = str(argv0 or "").replace("\\", "/").rsplit("/", 1)[-1]
    return name[:-4] if name.lower().endswith(".exe") else name


def invoked_as_legacy_command(argv0: str = "") -> bool:
    """Was this process started through the deprecated ``godsvoice`` script?"""
    return script_stem(argv0) == LEGACY_COMMAND


def notice_deprecated_alias(argv0: str = "") -> bool:
    """Print the one-line notice when the legacy script was used (TTY only).

    Returns True when the line was printed — the tests assert both the line and
    its absence on a pipe, so the decision is a returned fact, not a side effect
    someone has to guess at.
    """
    if not invoked_as_legacy_command(argv0):
        return False
    if not stderr_is_a_tty():
        return False
    print(NOTICE, file=sys.stderr)
    return True


def warn_deprecated_package() -> bool:
    """Emit the :class:`DeprecationWarning` for ``import godsv`` (TTY only).

    The filter is forced for this one call: Python's default filters *ignore*
    DeprecationWarning outside ``__main__``, so an import-triggered warning would
    never be displayed even on a terminal — and the TTY check above is already the
    policy that keeps pipes and logs byte-clean. ``catch_warnings`` mutates the
    global filter list for the duration of the call (no lock), so the window is
    kept to a single ``warn``.
    """
    if not stderr_is_a_tty():
        return False
    with warnings.catch_warnings():
        warnings.simplefilter("always", DeprecationWarning)
        warnings.warn(WARNING, DeprecationWarning, stacklevel=2)
    return True
