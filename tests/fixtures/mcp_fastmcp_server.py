"""A real fastmcp server (interop proof) — used by the MCP adapter tests.

``add``/``shout`` return plain dicts (fastmcp publishes no ``outputSchema`` for
those), ``total`` returns a pydantic model, which fastmcp DOES publish an
``outputSchema`` for — so both output modes of the adapter are exercised
against a real MCP implementation, not only against our own fixture.
"""

from __future__ import annotations

import sys

from fastmcp import FastMCP
from pydantic import BaseModel

server = FastMCP("godsv-fastmcp-fixture")


class Total(BaseModel):
    """The structured answer of ``total``."""

    total: int
    count: int


@server.tool
def add(a: int, b: int) -> dict:
    """Add two integers."""
    return {"sum": a + b}


@server.tool
def shout(text: str) -> dict:
    """Uppercase a text."""
    return {"loud": text.upper()}


@server.tool
def total(values: list[int]) -> Total:
    """Sum a list of integers and report how many were given."""
    return Total(total=sum(values), count=len(values))


if __name__ == "__main__":
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if transport == "http":
        server.run(transport="http", host="127.0.0.1", port=int(sys.argv[2]), show_banner=False)
    else:
        server.run(transport="stdio", show_banner=False)
