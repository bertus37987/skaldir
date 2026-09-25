"""textkit CLI: ``python3 cli.py slugify <text>``."""

from __future__ import annotations

import sys

from textkit import slugify

USAGE = "usage: cli.py slugify <text>"


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "slugify":
        print(USAGE, file=sys.stderr)
        return 2
    print(slugify(argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
