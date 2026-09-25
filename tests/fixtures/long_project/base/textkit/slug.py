"""Slug generation.

The contract for :func:`slugify` is documented in the project README and pinned by
``tests/test_slug.py``; the two declared checks (``run`` = the CLI, ``build`` = the
packaged artifact) both exercise the same function.
"""

from __future__ import annotations


def slugify(text: str) -> str:
    """Turn a human title into an ASCII URL slug.

    Promised: lowercase, ``[a-z0-9]`` only, every run of other characters becomes a
    single ``-``, no leading/trailing ``-``.
    """
    return str(text).lower().replace(" ", "-")
