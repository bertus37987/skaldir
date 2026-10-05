"""The registry gate keeps the schema enum in lockstep with the BACKENDS table.

Failing-first regression for web-backend-resilience: the input schema's
``backend`` enum declared the old chain (marginalia, duckduckgo,
duckduckgo-lite) but NOT bing, which the fallback table carried. Two surfaces
of the same contract disagreed — an explicit ``backend: "bing"`` call was
refused by the schema gate while the chain itself iterated bing. Locked since
the change, so neither can drift.
"""

from __future__ import annotations

from skaldir.tools_core import web


def test_the_backend_enum_and_the_backend_table_are_in_lockstep():
    enum = web.SEARCH_INPUT_SCHEMA["properties"]["backend"]["enum"]
    table = set(web.BACKENDS) | {"auto"}
    assert set(enum) == table, (
        f"drift between the schema enum ({sorted(enum)}) and the backend table "
        f"({sorted(web.BACKENDS)} + auto) — one surface disagrees with the other"
    )
