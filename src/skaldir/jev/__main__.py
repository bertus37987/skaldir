"""``python -m skaldir.jev <verb>`` — the standalone spelling of ``skaldir jev``.

Both spellings run the same handlers; once the CLI owner adds the three-line
wiring snippet (see :mod:`skaldir.jev.cli`), ``skaldir jev selftest --json`` is
the product path and this module stays the dependency-free way to run the
self-audit without the product CLI.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":  # pragma: no cover — exercised by the CLI tests
    sys.exit(main())
