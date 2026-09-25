"""``python -m skaldir.tools_core <verb> …`` — the group without the product CLI.

The same verbs ``skaldir tools-core …`` exposes, for a build that has not wired
the group into ``cli.py`` yet (and for tests): ``list``, ``show``, ``register``,
``call``, ``smoke``, ``health``, ``policy``, ``unregister``.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
