"""Keep this fixture OUT of the repository's own test collection.

``tests/fixtures/long_project/`` is a *project under test*, not part of our suite: its
``tests/test_slug.py`` imports the scratch package (``textkit``), which only exists inside
the fixture.  Without this file the repo's ``pytest tests`` would try to collect it and die
with ``ModuleNotFoundError: No module named 'textkit'`` during collection.

Nothing here is copied into a scenario run — ``tools/gv_long_project_e2e.py`` copies
``base/`` plus one overlay, so the scratch project's own suite stays collectable.
"""

collect_ignore_glob = ["*", "**/*"]
